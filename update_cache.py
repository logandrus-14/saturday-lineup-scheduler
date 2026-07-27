#!/usr/bin/env python3
"""Self-contained score-cache updater for a GitHub Actions runner.

Fetches the current week's games + lines from CFBD once and writes them to
Firestore `cache/slate_{season}_{week}`, so every app user reads from
Firestore instead of hitting CFBD directly. Runs on GitHub's servers (see
update-cache.yml), which sidesteps the local machine's network limits.

Reads two secrets from the environment (set as GitHub Actions secrets):
  FIREBASE_SERVICE_ACCOUNT  — the full service-account JSON (as a string)
  CFBD_API_KEY              — your CollegeFootballData API key

No third-party packages needed — pure standard library.
"""

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

PROJECT = "saturday-lineup"
CFBD = "https://api.collegefootballdata.com"
FS = "https://firestore.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/databases/(default)/documents"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def access_token(key: dict) -> str:
    now = int(dt.datetime.now().timestamp())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "iss": key["client_email"],
        "scope": "https://www.googleapis.com/auth/datastore",
        "aud": key["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }).encode())
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as pem:
        pem.write(key["private_key"])
        pem.flush()
        sig = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", pem.name],
            input=f"{header}.{payload}".encode(),
            capture_output=True, check=True).stdout
    jwt = f"{header}.{payload}.{_b64url(sig)}"
    body = (
        "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
        f"&assertion={jwt}"
    ).encode()
    req = urllib.request.Request(
        key["token_uri"], data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["access_token"]


def cfbd_get(path, key, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{CFBD}{path}?{qs}")
    req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def current_season():
    now = dt.datetime.now()
    return now.year - 1 if now.month <= 2 else now.year


def current_week(key, season):
    try:
        cal = cfbd_get("/calendar", key, year=season)
        now = dt.datetime.now(dt.timezone.utc)
        regular = [w for w in cal if w.get("seasonType") == "regular"]
        for w in regular:
            end = w.get("endDate") or w.get("lastGameStart")
            if end and now < dt.datetime.fromisoformat(
                    end.replace("Z", "+00:00")):
                return int(w["week"])
        if regular:
            return int(regular[-1]["week"])
    except Exception:
        pass
    return min(max(1, (dt.datetime.now() - dt.datetime(season, 8, 24)).days
                   // 7 + 1), 15)


# ── How often to actually hit CFBD ───────────────────────────────────────
#
# The workflow wakes every 5 minutes (GitHub's minimum), but each real run
# costs THREE CFBD calls (/calendar, /games, /lines). Refreshing on every
# wake-up would burn ~26,000 calls a month — enough to exhaust the plan on
# its own, which is exactly how the quota ran out in July.
#
# So the run rate follows the football calendar: minutes apart on a
# Saturday when scores actually move, an hour apart on a Tuesday when
# nothing changes. Skipping is free — it costs one Firestore read and no
# CFBD calls at all.
MAX_AGE_BY_WEEKDAY = {
    5: 5,    # Saturday — scores moving, refresh hard
    3: 10,   # Thursday — weeknight games
    4: 10,   # Friday
    6: 10,   # Sunday — late finals settling
    0: 60,   # Monday
    1: 60,   # Tuesday
    2: 60,   # Wednesday
}

STATE_DOC = "scheduler_state"


def minutes_since_last_run(token: str):
    """None when we've never run (or the marker is unreadable)."""
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{STATE_DOC}",
        headers={"Authorization": f"Bearer {token}"})
    try:
        doc = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None
    stamp = doc.get("fields", {}).get("lastRun", {}).get("timestampValue")
    if not stamp:
        return None
    last = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 60


def record_run(token: str) -> None:
    body = {"fields": {"lastRun": {"timestampValue": dt.datetime.now(
        dt.timezone.utc).isoformat().replace("+00:00", "Z")}}}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{STATE_DOC}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    cfbd = os.environ["CFBD_API_KEY"]
    token = access_token(key)

    # Bail out before spending a single CFBD call if the cache is already
    # fresh enough for what day it is. FORCE_REFRESH=1 overrides, so a
    # manual run from the Actions tab always does something.
    max_age = MAX_AGE_BY_WEEKDAY[dt.datetime.now(dt.timezone.utc).weekday()]
    age = minutes_since_last_run(token)
    if (os.environ.get("FORCE_REFRESH") != "1"
            and age is not None and age < max_age):
        print(f"cache refreshed {age:.1f} min ago; "
              f"threshold today is {max_age} min — skipping, no CFBD calls")
        return

    season = current_season()
    week = current_week(cfbd, season)
    games = cfbd_get("/games", cfbd, year=season, week=week,
                     seasonType="regular", division="fbs")
    lines = cfbd_get("/lines", cfbd, year=season, week=week,
                     seasonType="regular")

    body = {"fields": {
        "gamesJson": {"stringValue": json.dumps(games)},
        "linesJson": {"stringValue": json.dumps(lines)},
        "season": {"integerValue": str(season)},
        "week": {"integerValue": str(week)},
        "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                      .isoformat().replace("+00:00", "Z")},
    }}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/slate_{season}_{week}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()
    record_run(token)
    print(f"cached {season} week {week}: {len(games)} games, {len(lines)} lines")


if __name__ == "__main__":
    main()
