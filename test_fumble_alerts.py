#!/usr/bin/env python3
"""Cashed / fumbled pushes: settled exactly as the app settles a slip
(fumbling.dart's legResult / slipResult), worded like the app, told once.

    python3 test_fumble_alerts.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fumble_alerts as fa  # noqa: E402

checks = failures = 0


def check(name, got, want):
    global checks, failures
    checks += 1
    if got != want:
        failures += 1
        print(f"FAIL {name}: {got!r} != {want!r}")


def leg(market, home=True, line=0.0, price=-110, gid="g1", season=2026,
        week=6, ht="BYU", at="Utah"):
    return {"gameId": gid, "season": season, "week": week, "market": market,
            "home": home, "homeTeam": ht, "awayTeam": at, "line": line,
            "price": price}


final = {"final": True, "home": 27, "away": 17}  # BYU by 10

# ── leg_result, the same cases as fumbling_test.dart ─────────────────────
check("spread covers", fa.leg_result(leg("spread", line=-3.5), final), "won")
check("spread fails", fa.leg_result(leg("spread", line=-10.5), final), "lost")
check("spread lands on it: push",
      fa.leg_result(leg("spread", line=-10), final), "push")
check("the other side's spread",
      fa.leg_result(leg("spread", home=False, line=3.5), final), "lost")
check("moneyline", fa.leg_result(leg("moneyline"), final), "won")
check("moneyline, other side",
      fa.leg_result(leg("moneyline", home=False), final), "lost")
check("over", fa.leg_result(leg("total", line=41.5), final), "won")
check("under", fa.leg_result(leg("total", home=False, line=41.5), final),
      "lost")
check("total lands on it: push",
      fa.leg_result(leg("total", line=44), final), "push")
check("not final: open",
      fa.leg_result(leg("moneyline"), {"final": False, "home": 27, "away": 0}),
      "open")
check("no game: open", fa.leg_result(leg("moneyline"), None), "open")

# ── slip_result ──────────────────────────────────────────────────────────
games = {"g1": final, "g2": {"final": False, "home": 3, "away": 0},
         "g3": {"final": True, "home": 20, "away": 20}}


def slip(legs, stake=10.0):
    return {"id": "s", "legs": legs, "stake": stake}


s, p = fa.slip_result(slip([leg("moneyline", price=140)]), games.get)
check("a single that hits pays stake × odds", (s, round(p, 2)), ("won", 24.0))
s, _ = fa.slip_result(
    slip([leg("moneyline", home=False), leg("moneyline", gid="g2")]),
    games.get)
check("a parlay is lost the moment a leg loses, even with one still open",
      s, "lost")
s, _ = fa.slip_result(
    slip([leg("moneyline"), leg("moneyline", gid="g2")]), games.get)
check("…and open while any leg is undecided", s, "open")
s, p = fa.slip_result(
    slip([leg("moneyline", price=140), leg("moneyline", gid="g3")]),
    games.get)
check("a pushed leg drops out of a parlay", (s, round(p, 2)), ("won", 24.0))
s, p = fa.slip_result(slip([leg("moneyline", gid="g3")]), games.get)
check("every leg pushed: stake back", (s, p), ("push", 10.0))

s, p = fa.slip_result(dict(slip([leg("moneyline", price=140)]),
                           payoutMul=2), games.get)
check("a chaos card's payoutMul scales a win", (s, round(p, 2)), ("won", 48.0))
s, p = fa.slip_result(dict(slip([leg("moneyline", home=False)]),
                           miracle=True), games.get)
check("Miracle Comeback turns a loss into a push", (s, p), ("push", 10.0))
check("Midnight Trade flips a spread, line and all",
      fa.flipped(leg("spread", line=-3.5))["home"], False)
check("…to the other side's number",
      fa.flipped(leg("spread", line=-3.5))["line"], 3.5)

# ── words ────────────────────────────────────────────────────────────────
check("spread label", fa.leg_label(leg("spread", line=-3.5)), "BYU −3.5")
check("pick'em label", fa.leg_label(leg("spread", line=0)), "BYU PK")
check("total label", fa.leg_label(leg("total", home=False, line=44)),
      "Under 44")
check("future label", fa.leg_label(leg("future", ht="Ohio State")),
      "Ohio State")
check("cashed text",
      fa.alert_text(slip([leg("moneyline", price=140)]), "won", 24.0),
      ("Fumbled 💸", "BYU to win hit. +$14.00 — would have been real money."))
check("fumbled text",
      fa.alert_text(slip([leg("spread", line=-3.5), leg("total", line=50.5)],
                         stake=1500), "lost", 0.0),
      ("Bullet dodged 😮‍💨",
       "BYU −3.5 + Over 50.5 missed. Imagine if that was real. Actually, "
       "don't. That would have cost you $1,500.00."))
check("a near miss is rubbed in",
      fa.roast(slip([leg("spread", line=-10.5)]),
               lambda g: final),
      "Missed by 0.5. You'll be thinking about that one in the shower.")
check("a long parlay counts its hits",
      fa.roast(slip([leg("moneyline"), leg("moneyline", home=False),
                     leg("total", line=41.5), leg("total", line=60.5)]),
               lambda g: final),
      "2 of 4 legs hit. That's a participation trophy, not a payout.")
check("otherwise one of the stock roasts",
      fa.roast(slip([leg("moneyline", home=False)])) in fa.ROASTS, True)
check("a long parlay is named by its size",
      fa.alert_text(slip([leg("moneyline")] * 5), "lost", 0)[1]
      .startswith("Your 5-leg parlay missed."), True)

# ── where a leg's game lives ─────────────────────────────────────────────
check("college", fa.cache_doc_for(leg("spread", gid="401")), "slate_2026_6")
check("nfl", fa.cache_doc_for(leg("spread", gid="nfl401", week=205)),
      "nfl_2026_2_5")
check("nba", fa.cache_doc_for(leg("spread", gid="nba401", week=20261008)),
      "nba_20261008")
for key in ("nhl", "cbb", "soc", "mlb"):
    check(f"{key} (daily.py), by day",
          fa.cache_doc_for(leg("moneyline", gid=f"{key}401", week=20261019)),
          f"{key}_20261019")
check("a soccer draw pushes a moneyline (draw no bet)",
      fa.leg_result({"market": "moneyline", "home": True, "line": 0},
                    {"final": True, "home": 1, "away": 1}), "push")
check("future", fa.cache_doc_for(leg("future", gid="fut_ncaaf_2758_t194")),
      "futures_results")
check("a decided future is a 1–0 game for its winner",
      fa.future_game(leg("future", gid="fut_ncaaf_2758_t194"),
                     {"ncaaf:2758": "t194"}),
      {"final": True, "home": 1, "away": 0})


# ── a whole pass against a pretend database ──────────────────────────────
def fs_doc(name, fields):
    return {"name": name, "fields": fields}


def leg_value(l):
    def v(x):
        if isinstance(x, bool):
            return {"booleanValue": x}
        if isinstance(x, int):
            return {"integerValue": str(x)}
        if isinstance(x, float):
            return {"doubleValue": x}
        return {"stringValue": x}
    return {"mapValue": {"fields": {k: v(x) for k, x in l.items()}}}


db = {
    "cache/nfl_2026_2_5": {"fields": {"gamesJson": {"stringValue": json.dumps([
        {"id": "nfl1", "status": "final", "homeScore": 24, "awayScore": 10},
        {"id": "nfl2", "status": "live", "homeScore": 7, "awayScore": 3},
    ])}}},
}
slips = {
    "u1": [
        fs_doc("x/won", {"stake": {"doubleValue": 10.0}, "legs": {"arrayValue": {
            "values": [leg_value(leg("moneyline", gid="nfl1", week=205,
                                     price=150))]}}}),
        fs_doc("x/live", {"stake": {"doubleValue": 10.0}, "legs": {"arrayValue": {
            "values": [leg_value(leg("moneyline", gid="nfl2", week=205))]}}}),
        fs_doc("x/chicken", {"stake": {"doubleValue": 10.0},
                             "cashOut": {"doubleValue": 6.5},
                             "legs": {"arrayValue": {"values": [leg_value(
                                 leg("moneyline", gid="nfl2", week=205))]}}}),
    ],
}


def fs_get(path):
    return db.get(path)


def fs_list(path):
    if path == "fumbling":
        return [{"name": f"fumbling/{u}"} for u in slips]
    return slips.get(path.split("/")[1], [])


def fs_patch(path, fields):
    db[path] = {"fields": fields}


told = []
sent = fa.run(fs_get, fs_list, fs_patch, lambda u, t, b: told.append((u, t, b)))
check("the first run sends nothing…", (sent, told), (0, []))
check("…but marks what had already settled",
      "notifications/fumble__u1__won" in db, True)
check("…and leaves the launch marker", "notifications/fumble__launch" in db,
      True)
check("open slips are counted per game for Popular",
      json.loads(db["cache/fumble_popular"]["fields"]["countsJson"]
                 ["stringValue"]), {"nfl2": 1})

# The live game finishes: the home side wins.
db["cache/nfl_2026_2_5"]["fields"]["gamesJson"]["stringValue"] = json.dumps([
    {"id": "nfl1", "status": "final", "homeScore": 24, "awayScore": 10},
    {"id": "nfl2", "status": "final", "homeScore": 21, "awayScore": 3},
])
sent = fa.run(fs_get, fs_list, fs_patch, lambda u, t, b: told.append((u, t, b)))
check("a slip that settles afterwards is told, once", sent, 1)
check("…as fumbled (a win)", told[-1][1], "Fumbled 💸")
sent = fa.run(fs_get, fs_list, fs_patch, lambda u, t, b: told.append((u, t, b)))
check("…and never again", sent, 0)

# Build 72: a 3+ leg, $50+ parlay that hits earns a card — once a day.
big = {"legs": [{}, {}, {}], "stake": 50.0, "createdAt": "2026-10-16T12:00Z"}
check("a big parlay earns a card", fa.earns_card(big, None, "2026-10-16"),
      True)
check("…not if one was earned today",
      fa.earns_card(big, "2026-10-16", "2026-10-16"), False)
check("…not two legs",
      fa.earns_card({**big, "legs": [{}, {}]}, None, "2026-10-16"), False)
check("…not \$49", fa.earns_card({**big, "stake": 49.0}, None, "2026-10-16"),
      False)
check("…and the push says so",
      fa.alert_text({"legs": [{"market": "moneyline", "home": True,
                               "homeTeam": "BYU", "awayTeam": "Utah",
                               "line": 0, "price": -110}] * 4,
                     "stake": 50.0}, "won", 120.0, earned=True)[1]
      .endswith("You earned a card too: open Chaos to pick it."), True)

# ── Bonuses (Oct 18 2026, bonuses.dart): extra profit, and a free bet ────
boosted = {"legs": [leg("moneyline", price=100)], "stake": 100.0, "boost": 0.1}
check("a boost adds to the profit", fa.slip_result(boosted, lambda g: final),
      ("won", 210.0))
check("a missed free bet cost nothing",
      "cost nothing" in fa.alert_text(
          {"legs": [leg("moneyline")], "stake": 25.0, "free": True},
          "lost", 0.0)[1], True)

# ── Pro's smarter alerts (Oct 26 2026) ───────────────────────────────────
late = {"live": True, "period": 4, "clock": "3:12", "home": 20, "away": 17,
        "final": False}
spread = dict(leg("spread"), gameId="nfl1", home=True, line=-3.5)
one = {"id": "s1", "legs": [spread], "stake": 50.0}
check("sweat: covering by a hook late in the 4th",
      [a[0] for a in fa.pro_alerts(one, ["open"], lambda g: late,
                                   {"sweat": True})], ["sweat_s1_nfl1"])
check("sweat: nothing without the switch",
      fa.pro_alerts(one, ["open"], lambda g: late, {}), [])
check("sweat: nothing in the 2nd quarter",
      fa.pro_alerts(one, ["open"], lambda g: dict(late, period=2),
                    {"sweat": True}), [])
check("sweat: nothing when it's two scores",
      fa.pro_alerts(one, ["open"], lambda g: dict(late, home=40),
                    {"sweat": True}), [])
check("sweat: soccer from the 75th minute",
      fa.is_late("soc1", {"live": True, "period": 2, "clock": "78'"}), True)
check("sweat: soccer not at 60'",
      fa.is_late("soc1", {"live": True, "period": 2, "clock": "60'"}), False)
check("sweat words: needs it",
      "needs 1.5" in fa.sweat_text(one, spread, -1.5)[1], True)
two = {"id": "p1", "stake": 10.0,
       "legs": [dict(leg("moneyline"), gameId="nba1"), dict(spread)]}
check("one leg away: a parlay with one leg left",
      [a[0] for a in fa.pro_alerts(two, ["won", "open"],
                                   lambda g: {"live": False},
                                   {"oneLeg": True})], ["oneleg_p1"])
check("one leg away: not before any leg has hit",
      fa.pro_alerts(two, ["open", "open"], lambda g: {"live": False},
                    {"oneLeg": True}), [])

print(f"{checks - failures}/{checks} fumble alert checks pass")
sys.exit(1 if failures else 0)
