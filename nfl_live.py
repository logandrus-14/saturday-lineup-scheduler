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

**THE NBA RIDES ALONG (Oct 5 2026).** Logan: "can we add the NBA?
Preseason just started". Its games are pretend bets too, from the same ESPN
family, so each tick also refreshes today's (and yesterday's, for games past
midnight) NBA games, and the shift stays awake while either league has
something on. The workflow gained nightly starts for the NBA.

**SHIFTS HAND OVER TO EACH OTHER (Oct 8 2026).** GitHub's timer could not
be trusted: the evening start was run four hours late on Oct 5, and on Oct 8
all ten evening starts were skipped — Logan: "bets arent working". So a
shift no longer ends when nothing is on. It idles (re-checking every half
hour) until its time is up, then tells the workflow to start the next shift
(`chain=yes` in $GITHUB_OUTPUT → `gh workflow run`), which GitHub runs at
once. In season that is one unbroken watch; the timed starts remain only to
restart the chain if it ever breaks. July, between the seasons, it lets
the chain go.

    FIREBASE_SERVICE_ACCOUNT=... python3 nfl_live.py
"""

import datetime as dt
import json
import os
import time

from update_cache import (  # noqa: E402
    access_token, write_nba_cache, write_nfl_cache)

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


# How often an idle shift looks again when nothing is within reach.
IDLE_RECHECK = 30 * 60


def chain_on(now):
    """Whether this shift should start the next one. Always, except in July
    — the one month with neither an NFL nor an NBA game."""
    return now.month != 7


def idle_delay(now, deadline):
    """Nothing within reach, but the chain will carry on: wait, at most
    IDLE_RECHECK, and no later than the deadline — a game added meanwhile
    (tomorrow's night of NBA, once it is tomorrow) is then caught."""
    left = int((deadline - now).total_seconds())
    return max(1, min(IDLE_RECHECK, left))


def _hand_over(chain):
    """Tell the workflow whether to start the next shift."""
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"chain={'yes' if chain else 'no'}\n")
    print(f"  next shift: {'starting' if chain else 'not started'}")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    try:
        _shift()
    finally:
        _hand_over(chain_on(now))


def _shift():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    token = access_token(key)
    started = dt.datetime.now(dt.timezone.utc)
    deadline = started + MAX_SHIFT
    minted = started
    next_cached = dt.datetime.fromtimestamp(0, dt.timezone.utc)
    misses = 0
    print(f"NFL/NBA shift start {started:%Y-%m-%d %H:%M}Z")

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
        nfl_games = write_nfl_cache(token, include_next=with_next)  # never raises
        if with_next and nfl_games is not None:
            next_cached = now
        nba_games = write_nba_cache(token, live=True)  # never raises
        games = None if nfl_games is None and nba_games is None \
            else (nfl_games or []) + (nba_games or [])
        if games is None:
            misses += 1
            print(f"  tick wrote nothing ({misses}/{MAX_MISSES})")
            if misses >= MAX_MISSES:
                print("  ending the shift; the next one starts clean")
                return
            time.sleep(LIVE_INTERVAL)
            continue
        misses = 0

        delay = next_delay(games, now, deadline)
        if delay is None:
            if not chain_on(now):
                print("  nothing live or within reach — shift over")
                return
            delay = idle_delay(now, deadline)
            print(f"  nothing within reach — looking again in {delay}s",
                  flush=True)
            time.sleep(delay)
            continue
        live = sum(1 for g in games if g.get("status") == "live")
        print(f"  {live} live — next in {delay}s", flush=True)
        time.sleep(delay)


if __name__ == "__main__":
    main()
