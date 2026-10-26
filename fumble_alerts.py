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
import datetime as dt
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


def flipped(leg):
    """Mirrors FumbleLeg.flipped: the other side, same line and price."""
    if leg["market"] == "future":
        return leg
    out = dict(leg, home=not leg["home"])
    if leg["market"] == "spread":
        out["line"] = -leg["line"]
    return out


def slip_result(slip, game_of):
    """(status, payout). Mirrors slipResult: lost the moment any leg loses,
    open while any is undecided, a push if every leg pushed, else won. A
    Miracle Comeback turns a loss into a push; a chaos card's payoutMul
    scales a win."""
    legs = [leg_result(l, game_of(l["gameId"])) for l in slip["legs"]]
    if "lost" in legs:
        if slip.get("miracle"):
            return "push", slip["stake"]
        return "lost", 0.0
    if "open" in legs:
        return "open", None
    if all(r == "push" for r in legs):
        return "push", slip["stake"]
    dec = 1.0
    for l, r in zip(slip["legs"], legs):
        if r == "won":
            dec *= decimal_odds(l["price"])
    mul = slip.get("payoutMul") or 1
    # Under 1 (Replacement Refs, Chalk Tax) it cuts only the winnings —
    # fumbling.dart's boostedPayout.
    base = (slip["stake"] + (slip["stake"] * dec - slip["stake"]) * mul
            if mul < 1 else slip["stake"] * dec * mul)
    # Bonuses (lib/features/fumbling/domain/bonuses.dart): extra profit.
    profit = base - slip["stake"]
    return "won", base + profit * (slip.get("boost") or 0) if profit > 0 else base


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


# Roasts for a slip that missed — Logan, Oct 10 2026: "Add the roast
# pushes thats super funny." Picked by what happened, else by the slip's id
# (stable, so a re-run would say the same thing).
ROASTS = [
    "Thank goodness this is fake.",
    "Your financial advisor just felt a chill.",
    "The house sends its regards.",
    "Bold strategy. Didn't work, but bold.",
    "Somewhere a sportsbook is disappointed this wasn't real.",
]


def _stable(s):
    return sum(ord(c) for c in s)


def _miss_by(leg, game):
    """How far a spread or total missed by, or None."""
    if not game or not game.get("final"):
        return None
    h, a = game.get("home"), game.get("away")
    if h is None or a is None:
        return None
    if leg["market"] == "spread":
        m = (h - a if leg["home"] else a - h) + leg["line"]
    elif leg["market"] == "total":
        m = (h + a - leg["line"]) * (1 if leg["home"] else -1)
    else:
        return None
    return -m if m < 0 else None


def roast(slip, game_of=None):
    """One line of abuse for a slip that missed."""
    legs = slip["legs"]
    if len(legs) >= 4 and game_of:
        hit = sum(1 for l in legs if leg_result(l, game_of(l["gameId"])) == "won")
        return (f"{hit} of {len(legs)} legs hit. That's a participation "
                f"trophy, not a payout.")
    if len(legs) == 1 and game_of:
        by = _miss_by(legs[0], game_of(legs[0]["gameId"]))
        if by is not None and by <= 1:
            return (f"Missed by {_num(by)}. You'll be thinking about that "
                    f"one in the shower.")
    if slip["stake"] >= 1000:
        return "Imagine if that was real. Actually, don't."
    return ROASTS[_stable(slip.get("id", "")) % len(ROASTS)]


# Earning a card (lib/features/fumbling/domain/chaos.dart, earnedCardSlip):
# a parlay of 3+ legs, $50 or more on it, placed on or after Oct 14 2026,
# every leg hit — one a day. Logan, Oct 15 2026, picking "You earned a card!"
# notifications for build 72.
EARN_LEGS = 3
EARN_STAKE = 50.0
EARN_FROM = "2026-10-14"


def earns_card(slip, earned_day, today):
    """Whether this won slip earns a card, given the day the player last
    earned one and today's date (both 'YYYY-MM-DD')."""
    return (len(slip["legs"]) >= EARN_LEGS
            and slip["stake"] >= EARN_STAKE
            and (slip.get("createdAt") or "")[:10] >= EARN_FROM
            and earned_day != today)


def alert_text(slip, status, payout, game_of=None, earned=False):
    """(title, body) for a slip that hit or missed."""
    legs = slip["legs"]
    what = (" + ".join(leg_label(l) for l in legs) if len(legs) <= 3
            else f"Your {len(legs)}-leg parlay")
    # Logan, Oct 10 2026: a win is FUMBLED (it would have been real money),
    # a loss is DODGED (lucky it wasn't).
    if status == "won":
        return ("Fumbled 💸",
                f"{what} hit. +{money(payout - slip['stake'])} — would have "
                f"been real money."
                + (" You earned a card too: open Chaos to pick it."
                   if earned else ""))
    if slip.get("free"):
        return ("Bullet dodged 😮‍💨",
                f"{what} missed. {roast(slip, game_of)} It was your free "
                f"bet, so it cost nothing.")
    return ("Bullet dodged 😮‍💨",
            f"{what} missed. {roast(slip, game_of)} That would have cost you "
            f"{money(slip['stake'])}.")


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
    stolen = f.get("stolen")
    if stolen is not None:
        legs = [flipped(l) if i == int(stolen) else l
                for i, l in enumerate(legs)]
    return {"id": doc["name"].rsplit("/", 1)[-1], "legs": legs,
            "stake": float(f.get("stake", 0)),
            "cashOut": f.get("cashOut"),
            "payoutMul": f.get("payoutMul"),
            "miracle": bool(f.get("miracle")),
            "boost": f.get("boost"),
            "free": bool(f.get("free")),
            "createdAt": f.get("createdAt") or ""}


def cache_doc_for(leg):
    """Which cache document holds a leg's game — the app's own lookup."""
    gid, season, week = leg["gameId"], leg["season"], leg["week"]
    if gid.startswith("fut_"):
        return "futures_results"
    if gid.startswith("nba"):
        return f"nba_{week}"
    # The NHL, college basketball, soccer and MLB (daily.py), by day too.
    for key in ("nhl", "cbb", "soc", "mlb"):
        if gid.startswith(key):
            return f"{key}_{week}"
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
        else:  # nfl.py / nba.py / daily.py's shape
            out[g["id"]] = {
                "final": g.get("status") == "final",
                "home": g.get("homeScore"), "away": g.get("awayScore"),
                # For Pro's sweat alerts.
                "live": g.get("status") == "live",
                "period": g.get("period"), "clock": g.get("clock")}
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


def _today():
    """Today's date where the players are — the app's days are the phone's."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Denver")).date().isoformat()
    except Exception:  # no time-zone data on the server: Mountain, roughly
        return (now - dt.timedelta(hours=6)).date().isoformat()


def _now():
    return (dt.datetime.now(dt.timezone.utc).isoformat()
            .replace("+00:00", "Z"))


# ── Pro's smarter alerts (Oct 26 2026) ─────────────────────────────────────
#
# Logan's Pro list: "Sweat alerts when a slip is one score away, only for the
# games you pick." Two, each switched on in the app's Settings (Pro only) and
# kept at fumbling/{uid}/prefs/alerts {sweat, oneLeg}:
#   sweat  — late in a game, a leg is within one score of flipping;
#   oneLeg — a parlay has every leg in but one.
# Each is sent once per slip (and per leg, for sweat).

def sport_of(game_id):
    for p, s in (("fut_", "future"), ("nfl", "football"), ("nba", "nba"),
                 ("cbb", "cbb"), ("nhl", "hockey"), ("soc", "soccer"),
                 ("mlb", "baseball")):
        if game_id.startswith(p):
            return s
    return "football"


# One score, by sport.
ONE_SCORE = {"football": 8, "nba": 6, "cbb": 6, "hockey": 1, "soccer": 1,
             "baseball": 1}


def is_late(game_id, game):
    """In the last part of a live game: football's and the NBA's 4th,
    college hoops' 2nd half, hockey's 3rd, soccer from the 75th minute,
    baseball from the 8th."""
    if not game or not game.get("live"):
        return False
    period = game.get("period") or 0
    sport = sport_of(game_id)
    if sport == "soccer":
        clock = str(game.get("clock") or "")
        mins = "".join(ch for ch in clock.split("'")[0] if ch.isdigit())
        return period >= 2 and mins.isdigit() and int(mins) >= 75
    return period >= {"football": 4, "nba": 4, "cbb": 2, "hockey": 3,
                      "baseball": 8}.get(sport, 99)


def leg_margin(leg, game):
    """How far a leg is winning by right now (negative: losing by), or
    None. The spread counts; a total is how far past (or short of) the
    number."""
    h, a = game.get("home"), game.get("away")
    if h is None or a is None:
        return None
    market = leg["market"]
    if market == "moneyline":
        return h - a if leg["home"] else a - h
    if market == "spread":
        return (h - a if leg["home"] else a - h) + leg["line"]
    if market == "total":
        return (h + a - leg["line"]) * (1 if leg["home"] else -1)
    return None


def sweat_text(slip, leg, margin):
    """(title, body) for a leg one score from flipping."""
    what = leg_label(leg)
    total = leg["market"] == "total"
    if margin > 0:
        body = (f"{what} is {'past it' if total else 'covering'} by "
                f"{_num(margin)}, but one score flips it.")
    elif margin < 0:
        body = (f"{what} needs {_num(-margin)} more. One score and you're "
                f"back in it.")
    else:
        body = f"{what} is dead even. Somebody do something."
    title = "Sweat alert" if len(slip["legs"]) == 1 else (
        f"Sweat alert · {len(slip['legs'])}-leg parlay")
    return title, body


def one_leg_text(slip, last_leg):
    dec = 1.0
    for l in slip["legs"]:
        dec *= decimal_odds(l["price"])
    win = slip["stake"] * dec - slip["stake"]
    return (f"One leg away",
            f"Your {len(slip['legs'])}-leg parlay just needs {leg_label(last_leg)}. "
            f"About {money(win)} on the line.")


def pro_alerts(slip, results, game_of, prefs):
    """[(dedupe id, title, body)] for an open slip, by [prefs]."""
    out = []
    if prefs.get("sweat"):
        for leg, r in zip(slip["legs"], results):
            if r != "open":
                continue
            g = game_of(leg["gameId"])
            if not is_late(leg["gameId"], g):
                continue
            m = leg_margin(leg, g)
            if m is None or abs(m) > ONE_SCORE[sport_of(leg["gameId"])]:
                continue
            out.append((f"sweat_{slip['id']}_{leg['gameId']}",)
                       + sweat_text(slip, leg, m))
    if prefs.get("oneLeg") and len(slip["legs"]) > 1:
        open_legs = [l for l, r in zip(slip["legs"], results) if r == "open"]
        if len(open_legs) == 1 and "won" in results:
            out.append((f"oneleg_{slip['id']}",)
                       + one_leg_text(slip, open_legs[0]))
    return out


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
    # Open slips per game, for the Board's Popular tab — Logan, Oct 12 2026:
    # "Can we add a popular tab to the board". Phones can only read their
    # own and their friends' slips; this pass reads everyone's anyway.
    popular = {}
    # Each player's Pro alert switches, read once a pass.
    prefs_of = {}
    for owner in fs_list("fumbling"):
        uid = owner["name"].rsplit("/", 1)[-1]
        for doc in fs_list(f"fumbling/{uid}/slips"):
            slip = parse_slip(doc)
            # Cashed out early (Oct 10 2026): the owner settled it by hand,
            # so there is nothing to tell them when its games finish.
            if not slip["legs"] or slip["cashOut"] is not None:
                continue
            by_id = {l["gameId"]: l for l in slip["legs"]}
            status, payout = slip_result(slip, lambda gid: game_of(by_id[gid]))
            if status == "open":
                for gid in by_id:
                    popular[gid] = popular.get(gid, 0) + 1
                if uid not in prefs_of:
                    doc = fs_get(f"fumbling/{uid}/prefs/alerts") or {}
                    prefs_of[uid] = {k: _value(v) for k, v in
                                     doc.get("fields", {}).items()}
                if launched and prefs_of[uid]:
                    results = [leg_result(l, game_of(l))
                               for l in slip["legs"]]
                    for key_id, title, body in pro_alerts(
                            slip, results, lambda gid: game_of(by_id[gid]),
                            prefs_of[uid]):
                        key = notify.dedupe_key("fumble", uid, key_id)
                        if notify.already_sent(fs_get, key):
                            continue
                        send(uid, title, body)
                        sent += 1
                        notify.record_sent(fs_patch, key, "fumble", uid)
            if status not in ("won", "lost"):
                continue
            key = notify.dedupe_key("fumble", uid, slip["id"])
            if notify.already_sent(fs_get, key):
                continue
            if launched:
                earned = False
                if status == "won":
                    chaos = (fs_get(f"chaos/{uid}") or {}).get("fields", {})
                    earned = earns_card(
                        slip,
                        _value(chaos["earnedDay"]) if "earnedDay" in chaos
                        else None,
                        _today())
                title, body = alert_text(
                    slip, status, payout, lambda gid: game_of(by_id[gid]),
                    earned=earned)
                send(uid, title, body)
                sent += 1
            notify.record_sent(fs_patch, key, "fumble", uid)
    fs_patch("cache/fumble_popular", {
        "countsJson": {"stringValue": json.dumps(popular, sort_keys=True)},
        "updatedAt": {"timestampValue": _now()},
    })
    if not launched:
        notify.record_sent(fs_patch, LAUNCH_KEY, "fumble", "launch")
        print("  fumble alerts: launched — every slip already settled marked "
              "as told, nothing sent")
    return sent
