#!/usr/bin/env python3
"""Futures market names and season rollover.

    python3 test_futures.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import futures  # noqa: E402
from update_cache import futures_season  # noqa: E402

checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


n = futures.market_name
check("title", n("NCAA(F) - Championship", "ncaaf"), "National Champion")
check("FCS left out", n("NCAAF - FCS Championship Winner", "ncaaf"), None)
check("semis", n("NCAA(F) - Playoff - To reach the Semifinals", "ncaaf"),
      "Reach the Playoff Semifinals")
check("SEC", n("NCAA(F) - Southeastern Conference", "ncaaf"), "SEC Champion")
check("Heisman", n("NCAA(F) - Heisman Trophy", "ncaaf"), "Heisman Trophy")
check("Super Bowl", n("NFL - Super Bowl Winner", "nfl"), "Super Bowl Champion")
check("AFC South", n("Pro Football (A) South Division - Winner", "nfl"), "AFC South")
check("NFC West without 'Winner'", n("Pro Football (N) West Division", "nfl"),
      "NFC West")
check("AFC title", n("Pro Football (A) Conference Winner", "nfl"), "AFC Champion")
check("NFC title", n("Pro Football (N) Conference - Winner", "nfl"), "NFC Champion")
check("MVP", n("Regular Season MVP", "nfl"), "NFL MVP")
check("unknown is left out", n("Something Else", "nfl"), None)

check("October is this season",
      futures_season(dt.datetime(2026, 10, 4)), 2026)
check("January is still last season",
      futures_season(dt.datetime(2027, 1, 20)), 2026)
check("March is the new one", futures_season(dt.datetime(2027, 3, 1)), 2027)

# ── NBA (Oct 5 2026) ────────────────────────────────────────────────────
for raw, want in [
    ("NBA - Winner", "NBA Champion"),
    ("NBA - Eastern Conference - Winner", "East Champion"),
    ("NBA - Western Conference - Winner", "West Champion"),
    ("NBA - Regular Season MVP", "NBA MVP"),
    ("NBA - Rookie of the Year", "Rookie of the Year"),
    ("NBA Eastern Conference - Atlantic Division", "Atlantic Division"),
    ("NBA - In-Season East Group A Winner", None),
    ("NBA - 6th Man of the Year", None),
]:
    check(f"nba market {raw!r}", futures.market_name(raw, "nba"), want)

print(f"{checks - failures}/{checks} futures checks pass")
sys.exit(1 if failures else 0)
