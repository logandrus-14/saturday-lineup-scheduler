#!/usr/bin/env python3
"""Keeps the NFL games fresh every minute while they are being played.

Logan, Oct 4 2026, choosing Fumbling's live win chance: yes to "NFL scores
during games". Until this, the NFL cache was only written by
update_cache.py, whose cron GitHub honours every hour or two — so on a
Sunday an NFL slip's percentage would have sat still for an hour at a time.

**A SEPARATE SHIFT, NOT A PART OF live_refresh.py, AND THAT IS DELIBERATE.**
The college loop is what Saturdays depend on and has been hardened by a
season of failures (see its comments). NFL games exist in the app only as
pretend bets, and ESPN's scoreboard is unofficial. Nothing that goes wrong
here may cost the college scores anything, and the surest way to promise
that is to not share a process with them.

Same shape as the college shift: one GitHub job that stays awake and ticks
from the inside, because a running job is not subject to cron delays. It
ticks every minute while a game is on, every five in the hour before a
kickoff, sleeps through gaps it will outlive (1pm → 4pm → 8:20pm ET on a
Sunday), and ends when nothing is left within reach. ESPN is free and
keyless, so a tick costs one request and two Firestore writes.

    FIREBASE_SERVICE_ACCOUNT=... python3 nfl_live.py
"""

import datetime as dt
import json
import os
import time

from update_cache import access_token, write_nfl_cache  # noqa: E402

MAX_SHIFT = dt.timedelta(hours=5, minutes=30)
LIVE_INTERVAL = 60
IDLE_INTERVAL = 5 * 60
WARMUP = dt.timedelta(hours=1)
KICKOFF_COVER = dt.timedelta(minutes=30)
TOKEN_EVERY = dt.timedelta(minutes=45)  # tokens last an hour
# How often the shift also re-caches NEXT week. Oct 4 2026: a shift running
# older code rewrote nfl_current without the next week a minute after the
# scheduled job added it, and the board lost every upcoming game. Doing it
# from here too means one stale writer can't hide them for long.
NEXT_EVERY = dt.timedelta(minutes=30)
MAX_MISSES = 5  # consecutive ticks that wrote nothing


def _start(g):
    s = g.get("startDate")
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def next_delay(games, now, deadline):
    """Seconds until the next tick, or None to end the shift.

    Live (or kicked off and not yet showing as live) → a minute. A kickoff
    within the hour → five minutes. A later kickoff this shift will still
    be awake for → sleep until its warm-up. Otherwise → done; a later
    scheduled run covers it.
    """
    if not games:
        return None
    pending = [g for g in games if g.get("status") != "final"]
    if not pending:
        return None
    for g in pending:
        start = _start(g)
        if g.get("status") == "live" or (start and start <= now):
            return LIVE_INTERVAL
    upcoming = sorted(s for s in (_start(g) for g in pending) if s and s > now)
    if not upcoming:
        return None
    first = upcoming[0]
    if first - now <= WARMUP:
        return IDLE_INTERVAL
    if first + KICKOFF_COVER > deadline:
        return None
    return int((first - WARMUP - now).total_seconds())


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    token = access_token(key)
    started = dt.datetime.now(dt.timezone.utc)
    deadline = started + MAX_SHIFT
    minted = started
    next_cached = dt.datetime.fromtimestamp(0, dt.timezone.utc)
    misses = 0
    print(f"NFL shift start {started:%Y-%m-%d %H:%M}Z")

    while dt.datetime.now(dt.timezone.utc) < deadline:
        now = dt.datetime.now(dt.timezone.utc)
        if now - minted >= TOKEN_EVERY:
            try:
                token = access_token(key)
                minted = now
            except Exception as e:  # network blip — retry next tick
                print(f"  token renewal failed: {e}")
                misses += 1
                if misses >= MAX_MISSES:
                    return
                time.sleep(LIVE_INTERVAL)
                continue

        with_next = now - next_cached >= NEXT_EVERY
        games = write_nfl_cache(token, include_next=with_next)  # never raises
        if with_next and games is not None:
            next_cached = now
        if games is None:
            misses += 1
            print(f"  tick wrote nothing ({misses}/{MAX_MISSES})")
            if misses >= MAX_MISSES:
                print("  ending the shift; the next scheduled run starts clean")
                return
            time.sleep(LIVE_INTERVAL)
            continue
        misses = 0

        delay = next_delay(games, now, deadline)
        if delay is None:
            print("  nothing live or within reach — shift over")
            return
        live = sum(1 for g in games if g.get("status") == "live")
        print(f"  {live} live — next in {delay}s", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    main()
