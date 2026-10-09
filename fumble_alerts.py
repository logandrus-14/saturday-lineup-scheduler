#!/usr/bin/env python3
"""A push when a Fumbling slip cashes or fumbles.

Logan, Oct 8 2026: "can we also update the notifications? First, lets add
if your bet cashed or fumbled."

Every slip is settled here exactly as the app settles it — legResult and
slipResult in lib/features/fumbling/domain/fumbling.dart, mirrored line for
line and held to it by test_fumble_alerts.py. A slip is told about once:
the de-dupe key names the slip, so an hourly job and a live shift noticing
the same final whistle still send one push.

THE FIRST RUN SENDS NOTHING. Everyone already has settled slips; announcing
all of them at launch would be a wall of stale pushes. The first run marks
them all as told and leaves a marker; only slips that settle after that are
announced.

Pushes are skipped for a slip that only PUSHED (stake back): nothing
happened worth a notification.
"""
import json

import notify

LAUNCH_KEY = "fumble__launch"


# ── Settling, mirrored from fumbling.dart ─────────────────────────────────

def decimal_odds(american):
    """−110 → 1.909; +140 → 2.4. Mirrors decimalOdds."""
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def leg_result(leg, game):
    """'open' | 'won' | 'lost' | 'push'. Mirrors legResult."""
    if not game or not game.get("final"):
        return "open"
    h, a = game.get("home"), game.get("away")
    if h is None or a is None:
        return "open"
    market = leg["market"]
    if market in ("future", "moneyline"):
        if h == a:
            return "push"
        return "won" if (h > a) == leg["home"] else "lost"
    if market == "spread":
        margin = (h - a if leg["home"] else a - h) + leg["line"]
        if margin == 0:
            return "push"
        return "won" if margin > 0 else "lost"
    total = h + a
    if total == leg["line"]:
        return "push"
    return "won" if (total > leg["line"]) == leg["home"] else "lost"


def slip_result(slip, game_of):
    """(status, payout). Mirrors slipResult: lost the moment any leg loses,
    open while any is undecided, a push if every leg pushed, else won."""
    legs = [leg_result(l, game_of(l["gameId"])) for l in slip["legs"]]
    if "lost" in legs:
        return "lost", 0.0
    if "open" in legs:
        return "open", None
    if all(r == "push" for r in legs):
        return "push", slip["stake"]
    dec = 1.0
    for l, r in zip(slip["legs"], legs):
        if r == "won":
            dec *= decimal_odds(l["price"])
    return "won", slip["stake"] * dec


# ── Words ─────────────────────────────────────────────────────────────────

def _num(v):
    return str(int(v)) if v == int(v) else str(v)


def _line(v):
    if v == 0:
        return "PK"
    return f"+{_num(v)}" if v > 0 else f"−{_num(-v)}"


def leg_label(leg):
    """Mirrors FumbleLeg.label."""
    team = leg["homeTeam"] if leg["home"] else leg["awayTeam"]
    return {
        "spread": f"{team} {_line(leg['line'])}",
        "moneyline": f"{team} to win",
        "total": f"{'Over' if leg['home'] else 'Under'} {_num(leg['line'])}",
        "future": leg["homeTeam"],
    }[leg["market"]]


def money(v):
    return f"${v:,.2f}"


def alert_text(slip, status, payout):
    """(title, body) for a cashed or fumbled slip."""
    legs = slip["legs"]
    what = (" + ".join(leg_label(l) for l in legs) if len(legs) <= 3
            else f"Your {len(legs)}-leg parlay")
    if status == "won":
        return ("Cashed 💰",
                f"{what} hit. +{money(payout - slip['stake'])} to your "
                f"pretend bank.")
    return ("Fumbled 🏈",
            f"{what} missed. −{money(slip['stake'])} from your pretend bank.")


# ── Reading ───────────────────────────────────────────────────────────────

def _value(v):
    """A Firestore REST value → plain Python."""
    if "stringValue" in v:
        return v["stringValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "booleanValue" in v:
        return v["booleanValue"]
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: _value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    return None


def parse_slip(doc):
    f = {k: _value(v) for k, v in doc.get("fields", {}).items()}
    legs = []
    for l in f.get("legs") or []:
        legs.append({
            "gameId": l.get("gameId", ""),
            "season": int(l.get("season", 0)),
            "week": int(l.get("week", 0)),
            "market": l.get("market", "moneyline"),
            "home": bool(l.get("home")),
            "homeTeam": l.get("homeTeam", ""),
            "awayTeam": l.get("awayTeam", ""),
            "line": float(l.get("line", 0)),
            "price": int(l.get("price", -110)),
        })
    return {"id": doc["name"].rsplit("/", 1)[-1], "legs": legs,
            "stake": float(f.get("stake", 0))}


def cache_doc_for(leg):
    """Which cache document holds a leg's game — the app's own lookup."""
    gid, season, week = leg["gameId"], leg["season"], leg["week"]
    if gid.startswith("fut_"):
        return "futures_results"
    if gid.startswith("nba"):
        return f"nba_{week}"
    if gid.startswith("nfl"):
        return f"nfl_{season}_{week // 100}_{week % 100}"
    return f"slate_{season}_{week}"


def games_in(doc_id, fields):
    """gameId → {final, home, away} from one cache document."""
    out = {}
    if doc_id == "futures_results":
        raw = fields.get("json", {}).get("stringValue")
        return {"__results__": json.loads(raw) if raw else {}}
    raw = fields.get("gamesJson", {}).get("stringValue")
    for g in json.loads(raw) if raw else []:
        if doc_id.startswith("slate_"):  # CFBD's own shape
            out[str(g.get("id"))] = {
                "final": bool(g.get("completed")),
                "home": g.get("homePoints"), "away": g.get("awayPoints")}
        else:  # nfl.py / nba.py's shape
            out[g["id"]] = {
                "final": g.get("status") == "final",
                "home": g.get("homeScore"), "away": g.get("awayScore")}
    return out


def future_game(leg, results):
    """A decided future as a 1–0 / 0–1 "game", like futureGame in Dart."""
    parts = leg["gameId"].split("_")
    if len(parts) != 4:
        return None
    _, league, market, option = parts
    winner = results.get(f"{league}:{market}")
    if winner is None:
        return None
    won = str(winner) == option
    return {"final": True, "home": 1 if won else 0, "away": 0 if won else 1}


# ── The pass ──────────────────────────────────────────────────────────────

def run(fs_get, fs_list, fs_patch, send):
    """Tell everyone about slips that settled since last time. Returns how
    many pushes went out. [send](uid, title, body) pushes to one person."""
    launched = notify.already_sent(fs_get, LAUNCH_KEY)
    cache = {}

    def game_of(leg):
        doc_id = cache_doc_for(leg)
        if doc_id not in cache:
            fields = (fs_get(f"cache/{doc_id}") or {}).get("fields", {})
            cache[doc_id] = games_in(doc_id, fields)
        games = cache[doc_id]
        if doc_id == "futures_results":
            return future_game(leg, games["__results__"])
        return games.get(leg["gameId"])

    sent = 0
    for owner in fs_list("fumbling"):
        uid = owner["name"].rsplit("/", 1)[-1]
        for doc in fs_list(f"fumbling/{uid}/slips"):
            slip = parse_slip(doc)
            if not slip["legs"]:
                continue
            by_id = {l["gameId"]: l for l in slip["legs"]}
            status, payout = slip_result(slip, lambda gid: game_of(by_id[gid]))
            if status not in ("won", "lost"):
                continue
            key = notify.dedupe_key("fumble", uid, slip["id"])
            if notify.already_sent(fs_get, key):
                continue
            if launched:
                title, body = alert_text(slip, status, payout)
                send(uid, title, body)
                sent += 1
            notify.record_sent(fs_patch, key, "fumble", uid)
    if not launched:
        notify.record_sent(fs_patch, LAUNCH_KEY, "fumble", "launch")
        print("  fumble alerts: launched — every slip already settled marked "
              "as told, nothing sent")
    return sent
