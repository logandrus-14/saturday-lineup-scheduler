#!/usr/bin/env python3
"""When the NFL shift ticks, sleeps and stops.

    python3 test_nfl_live.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nfl_live import (  # noqa: E402
    IDLE_INTERVAL, IDLE_RECHECK, LIVE_INTERVAL, chain_on, idle_delay,
    next_delay)

checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


now = dt.datetime(2026, 10, 11, 17, 30, tzinfo=dt.timezone.utc)
end = now + dt.timedelta(hours=5)


def g(status, minutes_from_now):
    t = now + dt.timedelta(minutes=minutes_from_now)
    return {"status": status, "startDate": t.isoformat().replace("+00:00", "Z")}


check("a live game ticks every minute",
      next_delay([g("live", -30), g("scheduled", 180)], now, end),
      LIVE_INTERVAL)
check("kicked off but ESPN not caught up still ticks",
      next_delay([g("scheduled", -2)], now, end), LIVE_INTERVAL)
check("kickoff within the hour ticks slowly",
      next_delay([g("scheduled", 40)], now, end), IDLE_INTERVAL)
check("a 4pm kickoff the shift will see: sleep to its warm-up",
      next_delay([g("final", -200), g("scheduled", 150)], now, end),
      90 * 60)
check("a kickoff after the shift ends: stand down",
      next_delay([g("scheduled", 290)], now, end), None)
check("everything final: done",
      next_delay([g("final", -200), g("final", -10)], now, end), None)
check("no games: done", next_delay([], now, end), None)

# ── Handing over (Oct 8 2026) ─────────────────────────────────────────────
check("in season, a shift starts the next one", chain_on(now), True)
check("in July, between seasons, it lets the chain go",
      chain_on(dt.datetime(2027, 7, 10, tzinfo=dt.timezone.utc)), False)
check("idle: look again in half an hour", idle_delay(now, end), IDLE_RECHECK)
check("idle near the end: only until the deadline",
      idle_delay(end - dt.timedelta(minutes=5), end), 300)
check("idle at the deadline: a second, never zero or negative",
      idle_delay(end, end), 1)

print(f"{checks - failures}/{checks} NFL shift checks pass")
sys.exit(1 if failures else 0)
