#!/usr/bin/env python3
"""matchups.py against the answer key the app is also tested against.

    python3 test_matchups_parity.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matchups  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = json.load(open(os.path.join(HERE, "matchup_cases.json")))

failures = 0
for c in CASES["pairings"]:
    got = matchups.pairings_for(c["members"], c["groupId"], c["week"])
    if got != c["pairings"]:
        failures += 1
        print(f"FAIL pairings {c['groupId']} week {c['week']}: "
              f"{got} != {c['pairings']}")

r = CASES["records"]
scores = {int(k): v for k, v in r["scores"].items()}
rec = matchups.records_from(r["members"], r["groupId"], scores)
for uid, want in r["expected"].items():
    if rec[uid] != want:
        failures += 1
        print(f"FAIL record {uid}: {rec[uid]} != {want}")
order = matchups.standings_order(rec)
if order != r["order"]:
    failures += 1
    print(f"FAIL order {order} != {r['order']}")
semis = matchups.schedule_for(r["members"], "rec", 14, order)
if semis != r["semis"]:
    failures += 1
    print(f"FAIL semis {semis} != {r['semis']}")
final = matchups.schedule_for(r["members"], "rec", 15, order,
                              r["semifinalScores"])
if final != r["final"]:
    failures += 1
    print(f"FAIL final {final} != {r['final']}")

checks = len(CASES["pairings"]) + len(r["expected"]) + 3
print(f"{checks - failures}/{checks} matchup parity checks pass")
sys.exit(1 if failures else 0)
