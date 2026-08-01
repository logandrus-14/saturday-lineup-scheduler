#!/usr/bin/env python3
"""Fails loudly when the score cache goes stale while games are on.

WHY THIS EXISTS
---------------
In July 2026 the cache sat 9 days stale and nothing said so. Every
scheduled run reported success, because the refresh script exits 0 on a
spent CFBD quota by design — a red X every five minutes would bury a real
failure. The cost of that choice was silence, and the only reason it was
noticed was someone happening to look.

This is the counterweight. It exits NON-ZERO when the cache is stale at a
moment it has no business being stale, which turns into a failed workflow
run, which is the one thing GitHub emails you about unprompted.

It deliberately makes no CFBD calls. It reads the cached slate and uses the
kickoff times already in it — those don't change — to work out whether
games should be underway right now. So a dead cache can't hide the alarm
that would report it, and watching costs nothing against the quota.

Usage (normally via .github/workflows/cache-alarm.yml):
  FIREBASE_SERVICE_ACCOUNT=... python3 cache_alarm.py
"""

import datetime as dt
import json
import os
import sys

from update_cache import (  # noqa: E402
    access_token, current_season, fs_get,
)

# How old the cache may be while games are being played. The live shift
# refreshes every 60s and the fallback job every 5 minutes on a Saturday,
# so 25 minutes means something is genuinely wrong rather than merely late.
LIVE_TOLERANCE = dt.timedelta(minutes=25)

# A catch-all for the quiet part of the week. Even on a Tuesday the
# fallback job refreshes hourly, so a full day of silence is the shape the
# July outage had — and this is the check that would have caught it.
IDLE_TOLERANCE = dt.timedelta(hours=24)

# Roughly how long a college football game runs, used to decide whether a
# kickoff that has passed is probably still in progress.
GAME_LENGTH = dt.timedelta(hours=4)


def cached_slate(token, season, week):
    doc = fs_get(token, f"cache/slate_{season}_{week}")
    if not doc:
        return None, None
    fields = doc.get("fields", {})
    stamp = fields.get("updatedAt", {}).get("timestampValue")
    updated = (dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
               if stamp else None)
    raw = fields.get("gamesJson", {}).get("stringValue")
    return updated, (json.loads(raw) if raw else [])


def games_underway(games, now):
    """Whether kickoff times say football should be happening right now.

    Uses startDate, not the live status, on purpose: if the cache is stale
    the statuses are stale too, and a board frozen at "scheduled" is
    exactly the failure being watched for.
    """
    for g in games:
        start = g.get("startDate")
        if not start:
            continue
        kickoff = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
        if kickoff <= now <= kickoff + GAME_LENGTH:
            return True
    return False


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    token = access_token(key)
    now = dt.datetime.now(dt.timezone.utc)

    season = current_season()  # no CFBD call — date arithmetic only
    problems = []

    # Check the current week and the one either side, so a week rolling
    # over doesn't make the alarm look at a document nobody writes.
    checked = 0
    for week in range(1, 16):
        updated, games = cached_slate(token, season, week)
        if updated is None:
            continue
        checked += 1
        age = now - updated
        live = games_underway(games or [], now)

        if live and age > LIVE_TOLERANCE:
            problems.append(
                f"week {week}: games are being played right now and the "
                f"cache is {age.total_seconds()/60:.0f} minutes old "
                f"(limit {LIVE_TOLERANCE.total_seconds()/60:.0f}). Scores in "
                f"the app are wrong.")
        elif age > IDLE_TOLERANCE and games:
            # Only the most recent week matters for the idle check; older
            # weeks are finished and legitimately never rewritten.
            latest = max(
                (dt.datetime.fromisoformat(g["startDate"].replace("Z", "+00:00"))
                 for g in games if g.get("startDate")), default=None)
            if latest and latest + GAME_LENGTH > now:
                problems.append(
                    f"week {week}: cache is "
                    f"{age.total_seconds()/3600:.1f} hours old and this "
                    f"week's games have not all finished. The scheduler is "
                    f"probably not running.")

    if checked == 0:
        problems.append(
            "no cached slate exists for any week this season — the "
            "scheduler has never written one.")

    if problems:
        print("SCORE CACHE ALARM")
        print()
        for p in problems:
            print(f"  - {p}")
        print()
        print("Check the Actions tab for the 'Live score refresh' and "
              "'Update score cache' workflows. The usual causes are a spent "
              "CFBD monthly quota (which exits 0 on purpose and so looks "
              "green) or no shift having started.")
        sys.exit(1)

    print(f"cache healthy — {checked} week(s) checked, nothing stale")


if __name__ == "__main__":
    main()
