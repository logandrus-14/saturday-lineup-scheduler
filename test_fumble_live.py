#!/usr/bin/env python3
"""Fumbling's Live Activity content (fumble_live.py) against the shared
fixture the app runs too, and the pass against a pretend database.

    python3 test_fumble_live.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fumble_live  # noqa: E402

checks = failures = 0


def check(what, ok):
    global checks, failures
    checks += 1
    if not ok:
        failures += 1
        print(f"FAIL: {what}")


def game_of_for(gs):
    m = {x["id"]: {**x, "final": x["status"] == "final", "home": x["homeScore"],
                   "away": x["awayScore"]} for x in gs}
    return lambda gid: m.get(gid)


# The app repo's fixture (test/fixtures), shared with the Dart test; the
# scheduler repo carries a copy next to this file.
_here = os.path.join(HERE, "fumble_live_cases.json")
fixture = json.load(open(_here if os.path.exists(_here) else os.path.join(
    HERE, "..", "test", "fixtures", "fumble_live_cases.json")))
for c in fixture["cases"]:
    st = fumble_live.activity_state(c["slips"], game_of_for(c["games"]), 1.0)
    if st:
        st.pop("asOf")
    check(c["name"], st == c["expect"])

# ── The pass: pushes changes only, ends when nothing is live ──
ROOT = "projects/p/databases/(default)/documents/"
case = fixture["cases"][0]
raw = [{"id": g["id"], "homeTeam": g["homeTeam"], "awayTeam": g["awayTeam"],
        "homeScore": g["homeScore"], "awayScore": g["awayScore"],
        "status": g["status"], "period": g["period"], "clock": g["clock"],
        "startDate": g["start"]} for g in case["games"]]


def fs_get(path):
    if path.startswith("cache/"):
        return {"fields": {"gamesJson": {"stringValue": json.dumps(raw)}}}
    return None


def slip_doc(s):
    legs = [{"mapValue": {"fields": {
        "gameId": {"stringValue": l["gameId"]},
        "season": {"integerValue": str(l["season"])},
        "week": {"integerValue": str(l["week"])},
        "market": {"stringValue": l["market"]},
        "home": {"booleanValue": l["home"]},
        "homeTeam": {"stringValue": l["homeTeam"]},
        "awayTeam": {"stringValue": l["awayTeam"]},
        "line": {"doubleValue": l["line"]},
        "price": {"integerValue": str(l["price"])}}}} for l in s["legs"]]
    return {"name": ROOT + f"fumbling/u1/slips/{s['id']}", "fields": {
        "legs": {"arrayValue": {"values": legs}},
        "stake": {"doubleValue": s["stake"]},
        "createdAt": {"stringValue": s["createdAt"]}}}


slips = [slip_doc(s) for s in case["slips"]]
pushed, deleted = [], []
rows = [{"name": ROOT + "fumbling/u1/live/activity",
         "fields": {"token": {"stringValue": "tok"}}}]


def push(tok, state):
    pushed.append(state)
    return True


fumble_live._last.clear()
n = fumble_live.run(rows, fs_get, lambda p: slips if p.endswith("/slips") else [],
                    deleted.append, push, 5.0)
check("one push", n == 1 and pushed[0]["status"] == "COVERING")
n = fumble_live.run(rows, fs_get, lambda p: slips if p.endswith("/slips") else [],
                    deleted.append, push, 65.0)
check("unchanged: no second push", n == 0)
n = fumble_live.run(rows, fs_get, lambda p: [], deleted.append, push, 125.0)
check("nothing live: it ends", n == 1 and pushed[-1] is None
      and deleted == ["fumbling/u1/live/activity"])

print(f"{checks - failures}/{checks} checks pass")
sys.exit(1 if failures else 0)
