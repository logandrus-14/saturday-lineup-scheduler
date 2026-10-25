"""Fumbling's Live Activity for live slips, pushed while the app is closed.

Logan, Oct 24 2026: "We should also get a cool live activity for live
slips" — option 2: the scoreboard for the game closest to ending, the other
live slips as small tiles. The phone starts the activity and saves its push
token at fumbling/{uid}/live/activity; every live tick this builds the same
content the app builds (lib/features/fumbling/domain/live_activity.dart —
**keys and wording must match**, test/fixtures/fumble_live_cases.json is run
by both) and sends it straight to Apple with live_activity.py's credentials,
addressed to the Fumbling app. When nothing is live any more it ends the
activity.

Every Firestore and APNs call is passed in, so test_fumble_live.py runs it
against a dict.
"""
from __future__ import annotations

import hashlib
import json

from fumble_alerts import cache_doc_for, parse_slip, slip_result

TOPIC = "com.loganandrus.fumbling.push-type.liveactivity"

# What was last pushed to each token, so an unchanged scoreboard isn't sent
# again every minute (Apple limits how often an activity may be updated).
_last: dict[str, str] = {}


# ── Wording — mirrors live_activity.dart ─────────────────────────────────

def _num(v):
    v = float(v)
    return str(int(v)) if v == int(v) else str(v)


def format_line(v):
    """fumbling.dart's formatLine: PK, −3.5, +7."""
    v = float(v)
    if v == 0:
        return "PK"
    return f"−{_num(-v)}" if v < 0 else f"+{_num(v)}"


def leg_label(leg):
    """FumbleLeg.label."""
    team = leg["homeTeam"] if leg["home"] else leg["awayTeam"]
    m = leg["market"]
    if m == "spread":
        return f"{team} {format_line(leg['line'])}"
    if m == "moneyline":
        return f"{team} to win"
    if m == "total":
        return f"{'Over' if leg['home'] else 'Under'} {_num(leg['line'])}"
    return leg["homeTeam"]


def money(v):
    return f"${v:.2f}"


def _daily_league(gid):
    for key in ("nhl", "cbb", "soc", "mlb"):
        if gid.startswith(key):
            return key
    return None


def live_clock(g):
    """liveClock: "Q3 8:14", "P2 12:34", "67'", "▲5 · 1 OUT", or "LIVE"."""
    p = g.get("period")
    try:
        p = int(p) if p is not None else None
    except (TypeError, ValueError):
        p = None
    c = g.get("clock")
    league = _daily_league(g["id"])
    if league is None:
        if p is None and c is None:
            return "LIVE"
        q = None if p is None else ("OT" if p > 4 else f"Q{p}")
        if g["id"].startswith("nba") and p is not None and p > 4:
            return " ".join([f"{p - 4}OT" if p > 5 else "OT"] + ([c] if c else []))
        return " ".join(([q] if q else []) + ([c] if c and q != "OT" else []))
    if p is None and c is None:
        return "LIVE"
    if league == "nhl":
        name = None if p is None else (f"P{p}" if p <= 3 else "OT" if p == 4 else "SO")
        return " ".join(([name] if name else []) + ([c] if c and name != "SO" else []))
    if league == "cbb":
        name = None if p is None else (f"H{p}" if p <= 2 else "OT" if p == 3 else f"{p - 2}OT")
        return " ".join(([name] if name else []) + ([c] if c else []))
    if league == "soc":
        return "HALF" if c == "HT" else (c or "LIVE")
    parts = (c or "").split(":")
    arrow = {"top": "▲", "bot": "▼", "mid": "MID ", "end": "END "}.get(parts[0], "")
    outs = ""
    if len(parts) == 2 and parts[0] in ("top", "bot"):
        outs = f" · {parts[1]} OUT{'' if parts[1] == '1' else 'S'}"
    return "LIVE" if p is None else f"{arrow}{p}{outs}"


def leg_winning_now(leg, g):
    """legWinningNow: True / False, or None with no score or dead level."""
    h, a = g.get("homeScore"), g.get("awayScore")
    if h is None or a is None:
        return None
    m = leg["market"]
    if m == "spread":
        edge = (h - a if leg["home"] else a - h) + leg["line"]
    elif m == "total":
        edge = h + a - leg["line"] if leg["home"] else leg["line"] - (h + a)
    else:
        edge = h - a if leg["home"] else a - h
    if edge == 0:
        return None
    return edge > 0


def _single_status(leg, w):
    m = leg["market"]
    if m == "spread":
        return "PUSH NOW" if w is None else "COVERING" if w else "NOT COVERING"
    if m == "total":
        return "ON THE NUMBER" if w is None else "HITTING" if w else "NOT HITTING"
    return "TIED" if w is None else "WINNING" if w else "LOSING"


def _accent(good):
    return "neutral" if good is None else "good" if good else "bad"


def _decimal(american):
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def potential_payout(slip):
    """boostedPayout at the slip's own odds: what it pays if it hits."""
    dec = 1.0
    for l in slip["legs"]:
        dec *= _decimal(l["price"])
    mul = slip.get("payoutMul") or 1
    stake = slip["stake"]
    base = stake + (stake * dec - stake) * mul if mul < 1 else stake * dec * mul
    profit = base - stake
    return base + profit * (slip.get("boost") or 0) if profit > 0 else base


def _pick(slip):
    legs = slip["legs"]
    return leg_label(legs[0]) if len(legs) == 1 else f"Parlay · {len(legs)} legs"


def _live(slip, game_of):
    """(slip, leg, game, status, accent) for a live slip, else None.
    [game_of](gameId) → the full game record (see full_game)."""
    if slip.get("cashOut") is not None:
        return None
    by_id = {l["gameId"]: l for l in slip["legs"]}
    status, _ = slip_result(slip, lambda gid: game_of(gid))
    if status != "open":
        return None
    focus = None
    for l in slip["legs"]:
        g = game_of(l["gameId"])
        if not g or g.get("status") != "live":
            continue
        if focus is None or (g.get("start") or "") < (focus[1].get("start") or ""):
            focus = (l, g)
    if focus is None:
        return None
    leg, game = focus
    if len(slip["legs"]) == 1:
        w = leg_winning_now(leg, game)
        return (slip, leg, game, _single_status(leg, w), _accent(w))
    hit = 0
    good = True
    for l in slip["legs"]:
        g = game_of(l["gameId"])
        if not g:
            continue
        if g.get("final"):
            from fumble_alerts import leg_result
            if leg_result(l, g) == "won":
                hit += 1
            continue
        if g.get("status") != "live":
            continue
        w = leg_winning_now(l, g)
        if w is False:
            good = False
        elif w is None and good is True:
            good = None
    del by_id
    return (slip, leg, game, f"{hit} OF {len(slip['legs'])} HIT", _accent(good))


def activity_state(slips, game_of, now_epoch):
    """fumblingActivityState — the content map, or None with nothing live."""
    live = [x for x in (_live(s, game_of) for s in slips) if x]
    live.sort(key=lambda x: ((x[2].get("start") or ""), x[0].get("createdAt") or ""))
    if not live:
        return None
    big, rest = live[0], live[1:]
    slip, _leg, g, status, accent = big
    return {
        "away": g.get("awayTeam") or "",
        "home": g.get("homeTeam") or "",
        "awayScore": str(g.get("awayScore") if g.get("awayScore") is not None else 0),
        "homeScore": str(g.get("homeScore") if g.get("homeScore") is not None else 0),
        "clock": live_clock(g),
        "pick": _pick(slip),
        "status": status,
        "accent": accent,
        # What it wins, the bet taken out (Oct 25 2026).
        "pay": f"to win {money(potential_payout(slip) - slip['stake'])}",
        "tiles": [{"label": _pick(t[0]), "value": t[3], "accent": t[4]}
                  for t in rest[:3]],
        "more": len(rest) - 3 if len(rest) > 3 else 0,
        "live": len(live),
        "asOf": now_epoch,
    }


# ── Games from the cache ──────────────────────────────────────────────────

def full_game(doc_id, raw):
    """One cached game as this module needs it: the scores fumble_alerts
    settles by (final/home/away) plus the live fields and team names."""
    if doc_id.startswith("slate_"):  # CFBD's shape
        status = "final" if raw.get("completed") else (
            "live" if raw.get("homePoints") is not None else "scheduled")
        return {
            "id": str(raw.get("id")), "status": status,
            "final": bool(raw.get("completed")),
            "home": raw.get("homePoints"), "away": raw.get("awayPoints"),
            "homeScore": raw.get("homePoints"), "awayScore": raw.get("awayPoints"),
            "homeTeam": raw.get("homeTeam"), "awayTeam": raw.get("awayTeam"),
            "period": raw.get("period"), "clock": raw.get("clock"),
            "start": raw.get("startDate") or "",
        }
    status = raw.get("status")
    return {
        "id": raw["id"], "status": status, "final": status == "final",
        "home": raw.get("homeScore"), "away": raw.get("awayScore"),
        "homeScore": raw.get("homeScore"), "awayScore": raw.get("awayScore"),
        "homeTeam": raw.get("homeTeam"), "awayTeam": raw.get("awayTeam"),
        "period": raw.get("period"), "clock": raw.get("clock"),
        "start": raw.get("startDate") or "",
    }


# ── The pass ──────────────────────────────────────────────────────────────

def run(rows, fs_get, fs_list, fs_delete, push, now_epoch):
    """Update every Fumbling Live Activity. [rows]: the token documents
    (fumbling/{uid}/live/activity), found with ONE collection-group query so
    a tick costs a read per running activity, not per player. [push](token,
    state_or_None) sends an update, or ends the activity when state is None,
    and returns False if Apple says the token is gone. Returns how many
    were sent."""
    cache = {}

    def games(doc_id):
        if doc_id not in cache:
            fields = (fs_get(f"cache/{doc_id}") or {}).get("fields", {})
            raw = fields.get("gamesJson", {}).get("stringValue")
            cache[doc_id] = {
                g["id"]: g for g in
                (full_game(doc_id, x) for x in (json.loads(raw) if raw else []))
            }
        return cache[doc_id]

    sent = 0
    for doc in rows:
        path = doc["name"].split("/documents/", 1)[-1]
        parts = path.split("/")
        if len(parts) != 4 or parts[0] != "fumbling" or parts[2] != "live":
            continue
        uid = parts[1]
        token = (doc.get("fields", {}).get("token") or {}).get("stringValue")
        if not token:
            continue
        slips = [parse_slip(d) for d in fs_list(f"fumbling/{uid}/slips")]
        legs = {l["gameId"]: l for s in slips for l in s["legs"]}

        def game_of(gid):
            leg = legs.get(gid)
            if not leg or gid.startswith("fut_"):
                return None
            return games(cache_doc_for(leg)).get(gid)

        state = activity_state(slips, game_of, now_epoch)
        # Unchanged since the last push: nothing to say. asOf is left out of
        # the comparison — it changes every time.
        sig = hashlib.sha1(json.dumps(
            {k: v for k, v in (state or {}).items() if k != "asOf"},
            sort_keys=True).encode()).hexdigest()
        if _last.get(token) == sig:
            continue
        ok = push(token, state)
        _last[token] = sig
        if not ok:
            fs_delete(f"fumbling/{uid}/live/activity")
        elif state is None:
            # Ended: the phone starts a new one (and a new token) next time.
            fs_delete(f"fumbling/{uid}/live/activity")
        sent += 1
    return sent
