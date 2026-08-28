#!/usr/bin/env python3
"""The slate is FBS only, no matter what CFBD calls the filter this year.

WHY THIS EXISTS. On Aug 28 2026, twenty hours before the first game, the
preseason slate had 52 games on it instead of 8, half of them already
final. CFBD had renamed the games filter from `division` to
`classification`; an unknown query parameter is ignored rather than
rejected, so `division=fbs` quietly started returning every division in
the country. D2 games that kicked off the day before showed as live and
final, which woke the Live Activity and the widgets a day early.

Nothing failed. Nothing logged. The only symptom was the app being wrong.
So the filter is no longer only a request parameter — fbs_only judges the
games that came back — and this pins that behaviour down.

    /usr/bin/python3 test_fbs_only.py
"""

import sys

from scoring import fbs_only


def game(home, away):
    return {"homeTeam": "H", "awayTeam": "A",
            "homeClassification": home, "awayClassification": away}


CASES = [
    ("two FBS teams", game("fbs", "fbs"), True),
    ("FBS hosting an FCS team", game("fbs", "fcs"), True),
    ("FBS visiting an FCS team", game("fcs", "fbs"), True),
    ("two FCS teams", game("fcs", "fcs"), False),
    ("a Division II game", game("ii", "ii"), False),
    ("a Division III game", game("iii", "iii"), False),
    # Being unable to place a game is our problem, not the player's.
    ("a game CFBD did not classify", game(None, None), True),
    ("classified on one side only", game("fbs", None), True),
    ("classified on one side only, and not FBS", game("ii", None), False),
]

failures = 0
for label, raw, expected in CASES:
    got = bool(fbs_only([raw]))
    ok = got == expected
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")

# The whole point: the eight-game preseason cannot come back as fifty-two.
mixed = [game("fbs", "fbs")] * 8 + [game("ii", "ii")] * 44
kept = len(fbs_only(mixed))
if kept != 8:
    print(f"  FAIL a mixed-division week keeps only FBS (kept {kept} of 52)")
    failures += 1
else:
    print("  ok   a mixed-division week keeps only FBS")

if fbs_only(None) != [] or fbs_only([]) != []:
    print("  FAIL nothing at all does not crash")
    failures += 1
else:
    print("  ok   nothing at all does not crash")

print()
if failures:
    print(f"{failures} case(s) FAILED")
    sys.exit(1)
print(f"all {len(CASES) + 2} cases OK — the slate stays FBS")
