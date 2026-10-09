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

# ── words ────────────────────────────────────────────────────────────────
check("spread label", fa.leg_label(leg("spread", line=-3.5)), "BYU −3.5")
check("pick'em label", fa.leg_label(leg("spread", line=0)), "BYU PK")
check("total label", fa.leg_label(leg("total", home=False, line=44)),
      "Under 44")
check("future label", fa.leg_label(leg("future", ht="Ohio State")),
      "Ohio State")
check("cashed text",
      fa.alert_text(slip([leg("moneyline", price=140)]), "won", 24.0),
      ("Cashed 💰", "BYU to win hit. +$14.00 to your pretend bank."))
check("fumbled text",
      fa.alert_text(slip([leg("spread", line=-3.5), leg("total", line=50.5)],
                         stake=1500), "lost", 0.0),
      ("Fumbled 🏈",
       "BYU −3.5 + Over 50.5 missed. −$1,500.00 from your pretend bank."))
check("a long parlay is named by its size",
      fa.alert_text(slip([leg("moneyline")] * 5), "lost", 0)[1]
      .startswith("Your 5-leg parlay missed."), True)

# ── where a leg's game lives ─────────────────────────────────────────────
check("college", fa.cache_doc_for(leg("spread", gid="401")), "slate_2026_6")
check("nfl", fa.cache_doc_for(leg("spread", gid="nfl401", week=205)),
      "nfl_2026_2_5")
check("nba", fa.cache_doc_for(leg("spread", gid="nba401", week=20261008)),
      "nba_20261008")
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

# The live game finishes: the home side wins.
db["cache/nfl_2026_2_5"]["fields"]["gamesJson"]["stringValue"] = json.dumps([
    {"id": "nfl1", "status": "final", "homeScore": 24, "awayScore": 10},
    {"id": "nfl2", "status": "final", "homeScore": 21, "awayScore": 3},
])
sent = fa.run(fs_get, fs_list, fs_patch, lambda u, t, b: told.append((u, t, b)))
check("a slip that settles afterwards is told, once", sent, 1)
check("…as cashed", told[-1][1], "Cashed 💰")
sent = fa.run(fs_get, fs_list, fs_patch, lambda u, t, b: told.append((u, t, b)))
check("…and never again", sent, 0)

print(f"{checks - failures}/{checks} fumble alert checks pass")
sys.exit(1 if failures else 0)
