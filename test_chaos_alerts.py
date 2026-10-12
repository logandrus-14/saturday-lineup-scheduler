#!/usr/bin/env python3
"""Pushes for the friend games, and the Commissioner's weekly report.

    python3 test_chaos_alerts.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chaos_alerts as ca  # noqa: E402

checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


def s(v):
    return {"stringValue": v}


def doc(path, fields):
    return {"name": f"projects/p/databases/(default)/documents/{path}",
            "fields": fields}


db = {}
lists = {
    "crews": [doc("crews/c1", {
        "name": s("Saturday Crew"), "chaos": {"booleanValue": True},
        "members": {"arrayValue": {"values": [s("a"), s("b"), s("c")]}}})],
    "crews/c1/plays": [doc("crews/c1/plays/p1", {
        "from": s("a"), "fromName": s("Zane"), "to": s("b"),
        "toName": s("Eric"), "card": s("Freeze"), "outcome": s("blocked"),
        "at": s("2026-10-13T12:00:00Z")})],
    "duels": [],
    "fumbling": [],
}


def fs_get(path):
    return db.get(path)


def fs_list(path):
    return lists.get(path, [])


def fs_patch(path, fields):
    db[path] = {"fields": fields}


told = []


def send(uid, title, body):
    told.append((uid, title, body))


now = dt.datetime(2026, 10, 14, 18, tzinfo=dt.timezone.utc)  # a Wednesday
check("the first run sends nothing", ca.run(fs_get, fs_list, fs_patch, send,
                                            now=now), 0)
check("…but marks what was there as told",
      "notifications/chaos__play__p1" in db, True)

lists["crews/c1/plays"].append(doc("crews/c1/plays/p2", {
    "from": s("a"), "fromName": s("Zane"), "to": s("b"), "toName": s("Eric"),
    "card": s("Freeze"), "outcome": s("landed"),
    "at": s("2026-10-14T12:00:00Z")}))
ca.run(fs_get, fs_list, fs_patch, send, now=now)
check("a card played on you tells you, and only you", told[-1][0], "b")
check("…with the card's nickname", told[-1][1], "🃏 Zane played Rain Delay "
      "on you")
check("…and what it does", told[-1][2], "You can't place a bet for an hour.")
check("…once", ca.run(fs_get, fs_list, fs_patch, send, now=now), 0)

lists["crews/c1/plays"].append(doc("crews/c1/plays/p3", {
    "from": s("a"), "fromName": s("Zane"), "to": s(""), "toName": s(""),
    "card": s("Stadium Blackout"), "outcome": s("used"),
    "at": s("2026-10-14T13:00:00Z")}))
before = len(told)
ca.run(fs_get, fs_list, fs_patch, send, now=now)
check("a Stadium Blackout tells the rest of the crew",
      sorted(t[0] for t in told[before:]), ["b", "c"])

lists["duels"].append(doc("duels/d1", {
    "a": s("a"), "aName": s("Zane"), "b": s("b"), "bName": s("Eric"),
    "status": s("pending"), "stake": {"doubleValue": 200.0},
    "leg": {"mapValue": {"fields": {
        "homeTeam": s("BYU"), "awayTeam": s("Utah"),
        "home": {"booleanValue": True}, "market": s("spread"),
        "line": {"doubleValue": -3.5}}}}}))
ca.run(fs_get, fs_list, fs_patch, send, now=now)
check("a duel challenge tells the challenged", told[-1][0], "b")
check("…in words", told[-1][2], "Zane takes BYU −3.5. $200 each, winner "
      "takes both.")
lists["duels"][0]["fields"]["status"] = s("accepted")
ca.run(fs_get, fs_list, fs_patch, send, now=now)
check("…and its acceptance tells the challenger", told[-1][0], "a")

lists["fumbling"] = [doc("fumbling/c", {"name": s("Cooper Hall")})]
lists["fumbling/c/slips"] = [doc("fumbling/c/slips/x1", {
    "legs": {"arrayValue": {"values": [{"mapValue": {"fields": {
        "gameId": s("prop_b_s9_miss"),
        "homeTeam": s("Eric's 4-leg parlay misses")}}}]}}})]
ca.run(fs_get, fs_list, fs_patch, send, now=now)
check("a bet on your slip tells you", told[-1][0], "b")
check("…who and which way", told[-1][2], "Cooper says it misses. (Eric's "
      "4-leg parlay misses)")

# The weekly report: the same counting as chaos_balance.dart.
plays = [{"to": "b", "outcome": "blocked", "at": "2026-10-13T12:00:00Z"},
         {"to": "c", "outcome": "blocked", "at": "2026-10-13T12:00:00Z"},
         {"to": "d", "outcome": "blocked", "at": "2026-10-13T12:00:00Z"},
         {"to": "b", "outcome": "landed", "at": "2026-10-13T12:00:00Z"}]
lines = ca.balance(plays, {}, {"a", "b", "c", "d"}, now)
check("land rate as the app counts it", lines[0][1:3], ("25%", "bad"))
check("…with the advice", "defense too strong" in lines[0][3], True)
monday = dt.datetime(2026, 10, 19, 16, tzinfo=dt.timezone.utc)
before = len(told)
ca.run(fs_get, fs_list, fs_patch, send, now=monday)
check("Monday: the report goes to the Commissioner only",
      [t[0] for t in told[before:]], [ca.COMMISSIONER])

print(f"{checks - failures}/{checks} chaos alert checks pass")
sys.exit(1 if failures else 0)
