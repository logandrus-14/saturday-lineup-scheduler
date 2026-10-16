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
check("…with the card's nickname", told[-1][1], "🃏 Zane played Weather Delay "
      "on you")
check("…and what it does", told[-1][2], "You can't place a bet for an hour.")
check("…once", ca.run(fs_get, fs_list, fs_patch, send, now=now), 0)

# Oct 13 2026: a blocked card says the shield is gone, and a curse says when
# its bet goes in — only the cursed player's own phone can put it there.
check("a blocked card says the shield is down",
      ca.play_push({"card": "Jumbotron", "fromName": "Zane",
                    "outcome": "blocked"}),
      ("🛡️ Your shield blocked Zane's Kiss Cam",
       "Your shield is down now. Until it recharges, the next card played "
       "on you lands."))
# Oct 14 2026's cards.
check("a Flop tells the target it sold",
      ca.play_push({"card": "Freeze", "fromName": "Zane",
                    "outcome": "landed", "fake": True})[1],
      "Your Flop sold it. They think it landed. Nothing happened.")
check("a Trap tells who set it",
      ca.play_push({"card": "Jinx", "fromName": "Zane",
                    "outcome": "trapped"}),
      ("🚩 Zane walked into your Trap",
       "Their Jinx is gone and they can't bet for 2 hours."))
check("a Pass tells the passer and the one it landed on",
      (ca.play_push({"card": "Freeze", "fromName": "Zane",
                     "outcome": "passed", "passedToName": "Eric"})[1],
       ca.passed_push({"card": "Freeze", "fromName": "Zane",
                       "toName": "Logan"})),
      ("It landed on Eric instead.",
       ("🏀 Zane's Weather Delay came your way",
        "Logan had a Pass ready. You can't place a bet for an hour.")))
check("a Steal names the card",
      ca.play_push({"card": "Steal", "fromName": "Zane",
                    "outcome": "landed", "detail": "Mirror"})[1],
      "They took your Mirror.")
check("a bandwagon is good news",
      ca.play_push({"card": "Bandwagon", "fromName": "Zane",
                    "outcome": "used"})[0],
      "🚌 Zane jumped on your bandwagon")
check("a curse says the bet goes in when you open Fumbling",
      ca.play_push({"card": "Curse", "fromName": "Eric Ward",
                    "outcome": "landed"})[1],
      "A $100 bet on the day's biggest underdog goes in your slips the next "
      "time you open Fumbling.")

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

# Build 72 (Oct 15 2026): "Your shield is back up", and the morning nudge
# for the free card — 10am in Denver here.
ten_am = dt.datetime(2026, 10, 20, 16, tzinfo=dt.timezone.utc)
lists["chaos"] = [
    doc("chaos/a", {"remindCards": {"booleanValue": False},
                    "pickDay": s("2026-10-19")}),
    doc("chaos/b", {"shieldReadyAt": s("2026-10-20T15:00:00Z"),
                    "pickDay": s("2026-10-20")}),
    doc("chaos/c", {"pickDay": s("2026-10-19")}),
    doc("chaos/z", {"pickDay": s("2026-10-19")}),  # not in a chaos crew
]
before = len(told)
ca.run(fs_get, fs_list, fs_patch, send, now=ten_am)
new = told[before:]
check("a shield back up tells its owner",
      [(u, t) for u, t, _ in new if "shield" in t],
      [("b", "🛡️ Your shield is back up")])
check("the free-card nudge: not picked, not switched off, in a chaos crew",
      sorted(u for u, t, _ in new if "free card" in t), ["c"])
before = len(told)
ca.run(fs_get, fs_list, fs_patch, send, now=ten_am)
check("…each once", len(told) - before, 0)
afternoon = dt.datetime(2026, 10, 21, 21, tzinfo=dt.timezone.utc)  # 3pm
before = len(told)
ca.run(fs_get, fs_list, fs_patch, send, now=afternoon)
check("no nudge outside the morning", [t for _, t, _ in told[before:]
                                        if "free card" in t], [])

print(f"{checks - failures}/{checks} chaos alert checks pass")
sys.exit(1 if failures else 0)
