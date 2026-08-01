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
import urllib.error
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
    """Mark that we are about to spend CFBD calls.

    Called BEFORE the calls, not after they succeed. It used to be the last
    line of a successful run, which deadlocked: a 429 raised before it, so
    the marker was never written, so minutes_since_last_run stayed None, so
    the throttle never engaged, so every 5-minute wake-up spent three more
    CFBD calls — roughly 26,000 a month, which is what exhausted the quota
    that caused the 429. The failure kept causing itself, silently, and the
    cache sat 9 days stale in July 2026.

    Recording the attempt instead means a bad run costs one throttle
    interval rather than an endless retry loop.
    """
    body = {"fields": {"lastRun": {"timestampValue": dt.datetime.now(
        dt.timezone.utc).isoformat().replace("+00:00", "Z")}}}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{STATE_DOC}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


class QuotaExhausted(Exception):
    """CFBD's monthly call allowance is spent."""


def write_json_cache(token, doc_id, payload):
    """One CFBD response cached as a JSON blob under cache/<doc_id>."""
    body = {"fields": {
        "json": {"stringValue": json.dumps(payload)},
        "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                      .isoformat().replace("+00:00", "Z")},
    }}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{doc_id}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


# ─── Per-game reveal ─────────────────────────────────────────────────────
#
# Picks lock and reveal one game at a time, like a fantasy lineup. Firestore
# rules are per-DOCUMENT, so there is no way to hand a groupmate half of
# someone's lineup — "show my Thursday pick but not my Saturday picks"
# cannot be expressed there.
#
# So the reveal is published here instead. With admin rights, this reads
# every member's lineup, keeps only the picks whose games have already
# kicked off, and writes one board per group per week that the group may
# read. Lineups stay private to their owner. Clients can never write a
# board, so nobody can reveal or fake a pick that has not started.
#
# It needs no CFBD data at all — every pick carries its own kickoff — which
# is why main() writes boards BEFORE touching CFBD. A quota outage must
# never stop picks revealing.


def fs_get(token, path):
    req = urllib.request.Request(f"{FS}/{PARENT}/{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fs_list(token, collection):
    docs, page = [], None
    while True:
        url = f"{FS}/{PARENT}/{collection}?pageSize=300"
        if page:
            url += f"&pageToken={page}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"})
        body = json.load(urllib.request.urlopen(req, timeout=20))
        docs.extend(body.get("documents", []))
        page = body.get("nextPageToken")
        if not page:
            return docs


def started_picks(lineup_doc, now):
    """Only the picks whose games have kicked off, as plain values."""
    slots = (lineup_doc or {}).get("fields", {}).get(
        "slots", {}).get("mapValue", {}).get("fields", {})
    out = {}
    for name, value in slots.items():
        fields = value.get("mapValue", {}).get("fields", {})
        stamped = fields.get("kickoffAt", {}).get("timestampValue")
        if not stamped:
            continue  # no kickoff recorded — stays private, the safe way
        kickoff = dt.datetime.fromisoformat(stamped.replace("Z", "+00:00"))
        if now < kickoff:
            continue
        out[name] = {
            "gameId": fields.get("gameId", {}).get("stringValue", ""),
            "team": fields.get("team", {}).get("stringValue", ""),
            "kickoffAt": kickoff.isoformat().replace("+00:00", "Z"),
        }
    return out


def write_boards(token, season, week):
    now = dt.datetime.now(dt.timezone.utc)
    lineups = {}  # uid -> doc, so someone in two groups is read once
    written = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        members = [
            v.get("stringValue")
            for v in group.get("fields", {}).get("memberUids", {})
            .get("arrayValue", {}).get("values", [])
        ]

        board = {}
        for uid in members:
            if not uid:
                continue
            if uid not in lineups:
                lineups[uid] = fs_get(
                    token, f"users/{uid}/lineups/{season}_{week}")
            picks = started_picks(lineups[uid], now)
            if picks:
                board[uid] = picks

        body = {"fields": {
            "json": {"stringValue": json.dumps(board)},
            "season": {"integerValue": str(season)},
            "week": {"integerValue": str(week)},
            "updatedAt": {"timestampValue":
                          now.isoformat().replace("+00:00", "Z")},
        }}
        req = urllib.request.Request(
            f"{FS}/{PARENT}/groups/{gid}/board/{season}_{week}",
            data=json.dumps(body).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        written += 1

    return written, len(lineups)


# ─── Season standings ────────────────────────────────────────────────────
#
# Clients used to total the season themselves: for every week played, for
# every member, fetch that lineup and score it. weeks × members reads per
# view, and the Home screen did it for every group you belong to. Cheap in
# week 1, ~180 reads per Home open by November, and it got worse every week.
#
# This does the arithmetic once and writes the answer, so a client reads one
# document. Same shape as the reveal board.
#
# Scoring lives in scoring.py, which is a SECOND COPY of rules that also
# exist in Dart — see the warning at the top of that file, and the parity
# fixture that keeps the two honest.
#
# Deliberately NOT called from live_refresh.py's per-tick loop: totals move
# only when a game goes final, so recomputing every 60 seconds would cost
# members × weeks reads a minute to produce the same answer.


def write_season_standings(token, season, through_week):
    from scoring import build_slate, weekly_points

    # One slate per week, shared across every group.
    slates = {}
    for week in range(1, through_week + 1):
        doc = fs_get(token, f"cache/slate_{season}_{week}")
        if not doc:
            continue
        fields = doc.get("fields", {})
        games_raw = fields.get("gamesJson", {}).get("stringValue")
        lines_raw = fields.get("linesJson", {}).get("stringValue")
        if not games_raw:
            continue
        slates[week] = build_slate(
            json.loads(games_raw), json.loads(lines_raw or "[]"))

    if not slates:
        return 0, 0

    lineups = {}  # (uid, week) -> picks, so shared members are read once
    written = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        members = [
            v.get("stringValue")
            for v in group.get("fields", {}).get("memberUids", {})
            .get("arrayValue", {}).get("values", [])
        ]

        totals = {}
        for uid in members:
            if not uid:
                continue
            points = picks_made = 0
            for week, games in slates.items():
                if (uid, week) not in lineups:
                    doc = fs_get(token, f"users/{uid}/lineups/{season}_{week}")
                    slots = (doc or {}).get("fields", {}).get(
                        "slots", {}).get("mapValue", {}).get("fields", {})
                    lineups[(uid, week)] = {
                        name: {
                            "gameId": v.get("mapValue", {}).get("fields", {})
                                       .get("gameId", {}).get("stringValue"),
                            "team": v.get("mapValue", {}).get("fields", {})
                                     .get("team", {}).get("stringValue"),
                        }
                        for name, v in slots.items()
                    }
                picks = lineups[(uid, week)]
                points += weekly_points(picks, games)
                picks_made += len(picks)
            totals[uid] = {"points": points, "picksMade": picks_made}

        body = {"fields": {
            "json": {"stringValue": json.dumps(totals)},
            "season": {"integerValue": str(season)},
            "throughWeek": {"integerValue": str(through_week)},
            "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                          .isoformat().replace("+00:00", "Z")},
        }}
        req = urllib.request.Request(
            f"{FS}/{PARENT}/groups/{gid}/standings/{season}",
            data=json.dumps(body).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
        written += 1

    return written, len(slates)


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

    # Claim the slot before spending ANYTHING. current_week hits /calendar,
    # so this has to come first or that one call escapes the throttle. If
    # anything below fails — quota, network — the next wake-up still waits a
    # full interval instead of hammering CFBD every five minutes.
    record_run(token)

    season = current_season()
    week = current_week(cfbd, season)

    # Boards before the remaining CFBD calls: revealing a pick needs no
    # CFBD data, so running out of quota must never stop picks revealing
    # at kickoff.
    try:
        boards, read = write_boards(token, season, week)
        print(f"wrote {boards} group board(s) from {read} lineup(s)")
    except Exception as e:
        print(f"group boards skipped: {e}")

    try:
        games = cfbd_get("/games", cfbd, year=season, week=week,
                         seasonType="regular", division="fbs")
        lines = cfbd_get("/lines", cfbd, year=season, week=week,
                         seasonType="regular")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise QuotaExhausted() from e
        raise

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

    # The calendar (which week is it) and the AP poll used to be called
    # straight from every phone, on every session, which quietly made CFBD
    # usage scale with the number of players — the thing this cache exists
    # to prevent. Neither is worth failing the run over.
    try:
        write_json_cache(token, f"calendar_{season}",
                         cfbd_get("/calendar", cfbd, year=season))
        print(f"cached calendar_{season}")
    except Exception as e:
        print(f"calendar cache skipped: {e}")

    try:
        write_json_cache(
            token, f"rankings_{season}_{week}",
            cfbd_get("/rankings", cfbd, year=season, week=week,
                     seasonType="regular"))
        print(f"cached rankings_{season}_{week}")
    except Exception as e:
        print(f"rankings cache skipped: {e}")

    # Season totals last: they read every member's lineup for every week
    # played, so they are the most expensive thing here and the least
    # urgent. Never let them sink a run that already refreshed the scores.
    try:
        groups, weeks = write_season_standings(token, season, week)
        print(f"wrote {groups} season standings doc(s) over {weeks} week(s)")
    except Exception as e:
        print(f"season standings skipped: {e}")

    print(f"cached {season} week {week}: {len(games)} games, {len(lines)} lines")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhausted:
        # Exit 0 on purpose. Running out of monthly calls is expected and
        # self-correcting, and a red X every five minutes until the plan
        # resets would bury a real failure in noise. The app keeps serving
        # the cached slate meanwhile.
        print("CFBD monthly quota exhausted — leaving the existing cache in "
              "place. This clears when the plan resets; the app serves the "
              "cached slate until then.")
        sys.exit(0)
