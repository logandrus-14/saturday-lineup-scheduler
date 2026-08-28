#!/usr/bin/env python3
"""Where somebody sits in their group, for the Live Activity card.

WHY THIS EXISTS. `_season_placing` read the published standings as a LIST
of rows with a `uid` field. They are a MAP keyed by uid. Iterating a dict
yields its keys, so the first `r.get("points")` raised

    'str' object has no attribute 'get'

which was caught by the per-user try inside push_live_activities, printed
one line, and moved on. Every Live Activity card belonging to somebody in
a group was quietly failing — a feature whose entire purpose is the hours
when the phone is face down and nobody is watching the app to notice.

    /usr/bin/python3 test_season_placing.py
"""

import json
import sys

import update_cache


def placing(standings, uid, group_id="g1"):
    """_season_placing with the two Firestore reads faked out."""
    doc = {"fields": {"json": {"stringValue": json.dumps(standings)}}}
    original = update_cache.fs_get
    update_cache.fs_get = lambda token, path: doc
    try:
        return update_cache._season_placing("tok", 2026, uid, group_id)
    finally:
        update_cache.fs_get = original


MAP = {
    "alice": {"points": 84, "picksMade": 7},
    "bob": {"points": 84, "picksMade": 7},
    "carol": {"points": 12, "picksMade": 7},
    "dave": {"points": 0, "picksMade": 0},
}

CASES = [
    ("the map shape leads", MAP, "alice", (1, 4)),
    ("a tie shares the rank, it is not broken", MAP, "bob", (1, 4)),
    ("third place is 3, not 2, when two are tied above",
     MAP, "carol", (3, 4)),
    ("last is last", MAP, "dave", (4, 4)),
    ("somebody not in the group has no placing", MAP, "erin", (None, None)),
    # The shape it was written for. Still accepted — a shape assumption is
    # what caused this bug, so neither shape is assumed away.
    ("the old list shape still works",
     [{"uid": "alice", "points": 84}, {"uid": "carol", "points": 12}],
     "carol", (2, 2)),
    ("an empty group", {}, "alice", (None, None)),
    ("a shape nobody expects does not crash", "nonsense", "alice",
     (None, None)),
]

failures = 0
for label, standings, uid, expected in CASES:
    try:
        got = placing(standings, uid)
    except Exception as e:  # the actual regression
        got = f"raised {type(e).__name__}: {e}"
    ok = got == expected
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}"
          + ("" if ok else f"  (got {got}, wanted {expected})"))

# No pinned group means no rank to show, and no read either.
if update_cache._season_placing("tok", 2026, "alice", None) != (None, None):
    print("  FAIL no pinned group means no placing")
    failures += 1
else:
    print("  ok   no pinned group means no placing")

print()
if failures:
    print(f"{failures} case(s) FAILED")
    sys.exit(1)
print(f"all {len(CASES) + 1} cases OK — the card knows where you stand")
