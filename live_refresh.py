#!/usr/bin/env python3
"""Stays awake and refreshes scores every minute while games are being
played, instead of asking GitHub to wake us up every five minutes.

WHY THIS EXISTS
---------------
update_cache.py is scheduled with `cron: */5`. GitHub does not honour that
on free runners — measured over 30 consecutive runs in July 2026, the real
gap between runs was a median of 93 minutes, worst case 210. Scores an hour
and a half old make a live board pointless.

A scheduled run is a request; a running job is not. So this is one job that
starts and then loops internally, refreshing every 60 seconds for up to
about five and a half hours (GitHub's hard cap on a job is six). The
scheduling delay then only decides when the shift *starts*, not how fresh
the scores are once it is running.

It exits early the moment every game is final, so it never burns runner
minutes or CFBD calls overnight.

QUOTA
-----
Only /games is fetched in the loop. Spreads do not move once a game kicks
off, so /lines is fetched once at the start and reused — that halves the
cost of a Saturday. A full six-hour shift is ~330 CFBD calls, so a season
of Saturdays sits comfortably inside the plan.

Usage (normally via .github/workflows/live-refresh.yml):
  FIREBASE_SERVICE_ACCOUNT=... CFBD_API_KEY=... python3 live_refresh.py
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

from update_cache import (  # noqa: E402
    FS, PARENT, QuotaExhausted, access_token, cfbd_get, current_season,
    current_week, fs_get, record_run, write_boards,
)

# Stop before GitHub's 6h job limit so the shift ends cleanly rather than
# being killed mid-write.
MAX_SHIFT = dt.timedelta(hours=5, minutes=30)
LIVE_INTERVAL = 60          # something is being played
IDLE_INTERVAL = 5 * 60      # nothing live yet, kickoff still ahead
# How long before the first kickoff to bother staying awake at all.
WARMUP = dt.timedelta(hours=1)


def write_slate(token, season, week, games, lines):
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


def cached_lines(token, season, week):
    """Last known spreads, so the loop never has to re-fetch /lines."""
    doc = fs_get(token, f"cache/slate_{season}_{week}")
    raw = (doc or {}).get("fields", {}).get("linesJson", {}).get("stringValue")
    return json.loads(raw) if raw else []


def status_of(game):
    if game.get("completed"):
        return "final"
    if game.get("homePoints") is not None:
        return "live"
    return "scheduled"


def next_delay(games, now):
    """Seconds to wait, or None to end the shift.

    Mirrors the app's own rule so the two never disagree: watch closely
    while anything is being played, slowly while kickoff is still ahead,
    and stop once the week is done.
    """
    if not games:
        return None
    states = [status_of(g) for g in games]
    if all(s == "final" for s in states):
        return None
    if any(s == "live" for s in states):
        return LIVE_INTERVAL

    upcoming = []
    for g in games:
        if status_of(g) != "scheduled" or not g.get("startDate"):
            continue
        start = dt.datetime.fromisoformat(g["startDate"].replace("Z", "+00:00"))
        if start > now:
            upcoming.append(start)
    if not upcoming:
        return LIVE_INTERVAL  # kickoff passed, the feed hasn't caught up
    if min(upcoming) - now <= WARMUP:
        return IDLE_INTERVAL
    return None  # nothing for over an hour — let the shift end


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    cfbd = os.environ["CFBD_API_KEY"]
    token = access_token(key)

    season = current_season()
    week = current_week(cfbd, season)
    lines = cached_lines(token, season, week)
    if not lines:
        try:
            lines = cfbd_get("/lines", cfbd, year=season, week=week,
                             seasonType="regular")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise QuotaExhausted() from e
            raise

    started = dt.datetime.now(dt.timezone.utc)
    ticks = 0
    print(f"shift start {started:%Y-%m-%d %H:%M}Z — {season} week {week}, "
          f"{len(lines)} line records reused")

    while dt.datetime.now(dt.timezone.utc) - started < MAX_SHIFT:
        now = dt.datetime.now(dt.timezone.utc)
        try:
            games = cfbd_get("/games", cfbd, year=season, week=week,
                             seasonType="regular", division="fbs")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise QuotaExhausted() from e
            raise

        write_slate(token, season, week, games, lines)
        # Tell update_cache.py to stand down: while a shift is running it
        # has nothing to add, and its own runs would just spend CFBD calls
        # re-fetching what we already refreshed a moment ago.
        record_run(token)
        ticks += 1

        # Boards republish every tick, so a pick reveals within a minute of
        # its kickoff rather than whenever the next scheduled run lands.
        try:
            write_boards(token, season, week)
        except Exception as e:
            print(f"  boards skipped: {e}")

        delay = next_delay(games, now)
        live = sum(1 for g in games if status_of(g) == "live")
        final = sum(1 for g in games if status_of(g) == "final")
        print(f"  tick {ticks}: {live} live, {final} final"
              f" — {'stopping' if delay is None else f'next in {delay}s'}",
              flush=True)

        if delay is None:
            print("every game is final (or nothing is close) — ending shift")
            return
        time.sleep(delay)

    print(f"shift limit reached after {ticks} refresh(es); "
          f"the next scheduled run picks up from here")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhausted:
        # Same reasoning as update_cache.py: a spent monthly allowance is
        # expected and self-correcting, and a red X every time would bury a
        # real failure. The app serves the cached slate meanwhile.
        print("CFBD monthly quota exhausted — leaving the cache in place.")
        sys.exit(0)
