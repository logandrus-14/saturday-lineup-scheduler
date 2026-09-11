#!/usr/bin/env python3
"""Everybody starts the week where they finished the last one.

WHY THIS EXISTS. Logan, Sep 11 2026: "everyone should show the points they
ended with last week not 0 again ... If someone had -7 they would show -7
and then update as the new games go in." Game Day computed only the live
week, so every Saturday started everybody on zero. The fix is two published
numbers the phone cannot work out for itself — where each member started
the week, and what their empty slots are costing — with the live week
still computed on the phone every minute.

    /usr/bin/python3 test_carry_in.py
"""
import sys

from update_cache import empty_points

failures = 0


def check(label, ok):
    global failures
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures += 1


def lineup(*slots):
    return {"fields": {"slots": {"mapValue": {"fields": {s: {} for s in slots}}}}}


print(__doc__.strip().splitlines()[0])
print()

check("a full lineup has nothing empty", empty_points(lineup(
    "qb", "rb", "wr", "te", "def", "flex", "kicker")) == 0)
check("a no-show's empty slots are the whole 28", empty_points(None) == 28)
check("an empty lineup document is the same as none",
      empty_points(lineup()) == 28)
check("an empty QB costs 7, not 1 — WHICH slot matters, not how many",
      empty_points(lineup("rb", "wr", "te", "def", "flex", "kicker")) == 7)
check("and an empty kicker costs 1",
      empty_points(lineup("qb", "rb", "wr", "te", "def", "flex")) == 1)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 5 checks OK — an empty slot is charged at what it was worth")
