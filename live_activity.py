#!/usr/bin/env python3
"""Pushing Live Activity updates straight to Apple.

WHY THIS IS NOT notify.py. The app's ordinary notifications go through
Firebase Cloud Messaging. **FCM cannot carry a Live Activity update.** These
have to go directly to APNs, with a different topic, a different push type,
and a different credential — the .p8 auth key rather than a Google service
account. Two pipelines, and conflating them would mean neither works.

WHY curl. APNs is HTTP/2 only and Python's stdlib is HTTP/1.1 only. Adding
httpx would mean this scheduler stops being dependency-free, which is the
reason the container is tiny and there is no requirements.txt to drift. It
already shells out to openssl to sign JWTs, so curl is the same trade.

WHAT IT SENDS. Pre-formatted strings, never numbers to be rendered. The
content state mirrors `lib/features/live_activity/domain/live_activity_payload.dart`
field for field, and `test/fixtures/live_activity_cases.json` is run by BOTH
sides — same guard as the scoring parity fixture, and for the same reason:
two implementations of one rule drift silently, in the direction nobody
notices, until somebody is shown a wrong score.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import subprocess
import tempfile
import time

TEAM_ID = "56JP8NMN24"
KEY_ID = "UY5T25HVB2"
BUNDLE_ID = "com.loganandrus.saturdaylineup"

# A Live Activity is addressed to the app's bundle id with this suffix, NOT
# to the bundle id itself. Sending to the plain topic returns a 400 that
# does not obviously explain why.
TOPIC = f"{BUNDLE_ID}.push-type.liveactivity"

# Sandbox for builds signed with a development profile, production for
# TestFlight and the App Store. Exactly the same trap as aps-environment:
# a token minted against one server is rejected by the other, and the
# rejection is easy to misread as a bad token.
HOST_PROD = "https://api.push.apple.com"
HOST_SANDBOX = "https://api.sandbox.push.apple.com"

WEEKLY_MAX_POINTS = 28


# ── The payload, kept in step with Dart ───────────────────────────────────

def build_content_state(
    won_points: int,
    lost_points: int,
    picks_playing: int,
    picks_to_come: int,
    style: str,
    rank: int | None,
    group_size: int | None,
    now: dt.datetime,
) -> dict:
    """Mirrors `buildLiveActivityState` in Dart. Pinned by a shared fixture.

    Settled points only — never a projection. "On pace" has been wrong twice
    in this app's history and a lock screen is the worst place to repeat it.
    """
    score = won_points if style == "classic" else won_points - lost_points

    if picks_playing > 0:
        caption = "1 pick playing" if picks_playing == 1 else f"{picks_playing} picks playing"
    elif picks_to_come > 0:
        caption = "1 still to come" if picks_to_come == 1 else f"{picks_to_come} still to come"
    else:
        caption = "all done"

    placing = ""
    if rank is not None and group_size is not None:
        placing = f"{_ordinal(rank)} of {group_size}"

    if style == "classic":
        accent = "neutral"
    elif score > 0:
        accent = "good"
    elif score < 0:
        accent = "bad"
    else:
        accent = "neutral"

    settled = won_points + lost_points
    progress = 0.0 if WEEKLY_MAX_POINTS <= 0 else settled / WEEKLY_MAX_POINTS
    progress = max(0.0, min(1.0, progress))

    return {
        "score": _format_score(score, style),
        "caption": caption,
        "placing": placing,
        "accent": accent,
        "progress": progress,
        "asOf": now.timestamp(),
    }


def _format_score(value: int, style: str) -> str:
    if style == "classic":
        return str(value)
    # U+2212 MINUS SIGN, not a hyphen — the same character Dart's
    # formatScore uses. A hyphen renders visibly narrower next to digits.
    return f"−{abs(value)}" if value < 0 else f"+{value}"


def _ordinal(n: int) -> str:
    if 11 <= n <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


# ── APNs authentication ───────────────────────────────────────────────────

def _b64url(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _der_to_raw(der: bytes) -> bytes:
    """ECDSA signatures from openssl are DER; JWS wants raw r||s.

    Getting this wrong produces a token Apple rejects with 403
    InvalidProviderToken, which reads like a bad key rather than a bad
    encoding — so it is worth doing explicitly rather than hoping.
    """
    # SEQUENCE { INTEGER r, INTEGER s }
    if der[0] != 0x30:
        raise ValueError("not a DER sequence")
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)

    def read_int(pos: int) -> tuple[int, int]:
        if der[pos] != 0x02:
            raise ValueError("expected DER integer")
        length = der[pos + 1]
        val = int.from_bytes(der[pos + 2: pos + 2 + length], "big")
        return val, pos + 2 + length

    r, i = read_int(i)
    s, _ = read_int(i)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


_token_cache: dict[str, tuple[str, float]] = {}


def provider_token(p8_pem: str) -> str:
    """A JWT proving we are allowed to push for this team.

    Apple rejects tokens older than an hour AND refuses more than one new
    token per twenty minutes per key, so this is cached rather than minted
    per send. A live shift sends hundreds of pushes; minting each time would
    get the key rate-limited within a minute.
    """
    cached = _token_cache.get(KEY_ID)
    if cached and time.time() - cached[1] < 45 * 60:
        return cached[0]

    header = _b64url(json.dumps({"alg": "ES256", "kid": KEY_ID}).encode())
    claims = _b64url(json.dumps({"iss": TEAM_ID, "iat": int(time.time())}).encode())
    signing_input = header + b"." + claims

    with tempfile.NamedTemporaryFile("w", suffix=".p8", delete=False) as pem:
        pem.write(p8_pem)
        pem_path = pem.name
    try:
        der = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", pem_path],
            input=signing_input, capture_output=True, check=True,
        ).stdout
    finally:
        os.unlink(pem_path)

    token = (signing_input + b"." + _b64url(_der_to_raw(der))).decode()
    _token_cache[KEY_ID] = (token, time.time())
    return token


# ── Sending ───────────────────────────────────────────────────────────────

def send_update(
    device_token: str,
    content_state: dict,
    p8_pem: str,
    *,
    event: str = "update",
    sandbox: bool = False,
    stale_after: int = 30 * 60,
    dismiss_after: int | None = None,
    timeout: int = 15,
) -> tuple[int, str]:
    """Push one content state to one Live Activity. Returns (status, body).

    `stale-date` is the quiet hero here: it tells iOS when to stop presenting
    this reading as current, so a card whose scheduler has died greys itself
    out instead of showing a frozen score all evening. The app's own
    staleness warning covers the same ground, but iOS enforces this one even
    if the app never runs again.
    """
    # DRY_RUN stops before APNs. It returns 200 rather than an error so the
    # caller's dead-token pruning stays on its normal path — a rehearsal
    # must not delete a real device token. See update_cache.py's DRY_RUN.
    if os.environ.get("DRY_RUN") == "1":
        print(f"    DRY RUN — would push Live Activity ({event})")
        try:
            import update_cache
            update_cache._dry_sends.append(f"live activity ({event})")
        except Exception:  # noqa: BLE001 — the summary is a nicety
            pass
        return 200, '{"dryRun": true}'

    now = int(time.time())
    payload = {
        "aps": {
            "timestamp": now,
            "event": event,
            "content-state": content_state,
            "stale-date": now + stale_after,
        }
    }
    if event == "end" and dismiss_after is not None:
        payload["aps"]["dismissal-date"] = now + dismiss_after

    host = HOST_SANDBOX if sandbox else HOST_PROD
    out = subprocess.run(
        [
            "curl", "--http2", "--silent", "--show-error",
            "--max-time", str(timeout),
            "--write-out", "\n%{http_code}",
            "--header", f"authorization: bearer {provider_token(p8_pem)}",
            "--header", f"apns-topic: {TOPIC}",
            "--header", "apns-push-type: liveactivity",
            # 10 = deliver now. Live scores are the case this exists for; at
            # priority 5 iOS is free to sit on them, which for a lock-screen
            # scoreboard is the same as not sending.
            "--header", "apns-priority: 10",
            "--header", f"apns-expiration: {now + 300}",
            "--data", json.dumps(payload),
            f"{host}/3/device/{device_token}",
        ],
        capture_output=True, text=True,
    )
    body, _, status = out.stdout.rpartition("\n")
    if not status.strip().isdigit():
        return 0, (out.stderr or out.stdout).strip()[:300]
    return int(status), body.strip()


def is_dead_token(status: int, body: str) -> bool:
    """Whether to stop sending to this token and forget it.

    An activity's token dies when the card ends — which happens on its own
    when the user swipes it away. Retrying forever against a dead address is
    how a scheduler wastes an afternoon of pushes on nobody.
    """
    if status == 410:
        return True
    return status == 400 and any(
        reason in body
        for reason in ("BadDeviceToken", "DeviceTokenNotForTopic", "Unregistered")
    )
