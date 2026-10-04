#!/usr/bin/env python3
"""Timing and wording of the matchup alerts.

    python3 test_matchup_alerts.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from matchup_alerts import (opponent_window, week_over,  # noqa: E402
                            opponent_message, result_message)

UTC = dt.timezone.utc
checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


# Week of Sat Oct 10 2026; first kickoff Sat 16:00Z (11am ET).
kick = dt.datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
check("tuesday is too early",
      opponent_window(dt.datetime(2026, 10, 6, 20, tzinfo=UTC), kick), False)
check("wednesday 11am ET is too early",
      opponent_window(dt.datetime(2026, 10, 7, 16, tzinfo=UTC), kick), False)
check("wednesday noon ET sends",
      opponent_window(dt.datetime(2026, 10, 7, 17, tzinfo=UTC), kick), True)
check("thursday catches a missed wednesday",
      opponent_window(dt.datetime(2026, 10, 8, 14, tzinfo=UTC), kick), True)
check("never after kickoff",
      opponent_window(dt.datetime(2026, 10, 10, 17, tzinfo=UTC), kick), False)
check("no kickoff known, no alert",
      opponent_window(dt.datetime(2026, 10, 7, 18, tzinfo=UTC), None), False)
# A Wednesday 11pm ET is Thursday 04:00Z — still Wednesday locally, still in.
check("late wednesday ET (thursday UTC) sends",
      opponent_window(dt.datetime(2026, 10, 8, 4, tzinfo=UTC), kick), True)

check("week over: all final",
      week_over([{"status": "final"}, {"status": "final"}]), True)
check("week not over: one live",
      week_over([{"status": "final"}, {"status": "live"}]), False)
check("empty week is not over", week_over([]), False)

check("one opponent",
      opponent_message([("OG's", "Eric Ward", "3–2", "2–3")], 6),
      ("This week: you vs Eric Ward", "You're 3–2, Eric is 2–3. In OG's."))
check("the average",
      opponent_message([("OG's", None, "3–2", None)], 6)[0],
      "This week: you vs the group average")
check("semifinal",
      opponent_message([("OG's", "Eric Ward", "8–5", "9–4")], 14)[0],
      "Semifinal: you vs Eric Ward")
check("two groups, one alert",
      opponent_message([("OG's", "Eric Ward", "3–2", "2–3"),
                        ("Andrus crew", "Ty Boatman", "1–4", "4–1")], 6),
      ("This week's matchups",
       "Eric Ward in OG's · Ty Boatman in Andrus crew"))

check("a win", result_message("OG's", "Eric Ward", 12, 4, "4–2"),
      ("You beat Eric Ward", "+12 to +4 in OG's · now 4–2"))
check("a loss", result_message("OG's", "Eric Ward", -6, 3, "3–3"),
      ("Eric Ward beat you", "−6 to +3 in OG's · now 3–3"))
check("a tie", result_message("OG's", "Eric Ward", 2, 2, "3–2–1")[0],
      "You and Eric Ward tied")
check("vs the average, rounded",
      result_message("OG's", None, 5, 3.4, "4–2"),
      ("You beat the group average", "+5 to +3 in OG's · now 4–2"))

print(f"{checks - failures}/{checks} matchup alert checks pass")
sys.exit(1 if failures else 0)
