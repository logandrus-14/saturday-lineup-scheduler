#!/usr/bin/env python3
"""Pushes for Fumbling's friend games, and the Commissioner's weekly report.

Logan, Oct 12 2026: "I also want to make sure, people who are not in your
crew will not be notified correct??" — right: a card played on you tells
you; a Stadium Blackout tells the crew it hit; a duel tells the two people
in it; a bet on your slip tells you. Nobody else.

Each push is told once (notify.dedupe_key). THE FIRST RUN SENDS NOTHING —
it marks what already exists as told — so turning this on never floods
anyone with old news.

Weekly, on Monday, the Commissioner gets Chaos Mode's balance in a line or
two — counted as lib/features/fumbling/domain/chaos_balance.dart counts it
(test_chaos_alerts.py holds the two together).
"""
import datetime as dt

import notify

LAUNCH_KEY = "chaos__launch"
COMMISSIONER = "H97FRYb57EaOsvhyVXLu6bdKIv33"

# Card names → their nicknames and what they do to you, as the app says it
# (lib/features/fumbling/domain/chaos_deck.dart).
CARDS = {
    "Freeze": ("Rain Delay", "You can't place a bet for an hour."),
    "Double Down": ("Going for It on 4th", "Your next slip's stake is doubled."),
    "Jinx": ("Cover Curse", "If that slip loses, they take 10% of it."),
    "Jumbotron": ("Kiss Cam", "Your next slip is pinned for the whole crew."),
    "Rabbit's Foot": ("Rally Cap", ""),
    "Curse": ("Cursed Franchise", "A $100 bet on the day's biggest underdog "
              "just went in your slips."),
    "Handicap Hammer": ("Replacement Refs",
                        "Your payouts are 20% worse for three days."),
    "Lockdown": ("Shutout", ""),
    "Bad Beat Insurance": ("Moral Victory", ""),
    "Mulligan": ("Challenge Flag", ""),
    "Steal the Slip": ("Midnight Trade",
                       "One of your picks just got flipped."),
    "Mirror": ("Own Goal", ""),
    "Copycat": ("Stealing Signs", ""),
    "Heist": ("Half-Court Heave", ""),
    "Golden Ticket": ("Hole-in-One", ""),
    "Stadium Blackout": ("Pulled the Plug", ""),
    "Miracle Comeback": ("Down 28–3", ""),
}


def _v(v):
    """A Firestore REST value → plain Python."""
    if v is None:
        return None
    for k in ("stringValue", "booleanValue"):
        if k in v:
            return v[k]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "arrayValue" in v:
        return [_v(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: _v(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "timestampValue" in v:
        return v["timestampValue"]
    return None


def _doc(d):
    return {k: _v(v) for k, v in d.get("fields", {}).items()}


def _id(d):
    return d["name"].rsplit("/", 1)[-1]


def money(v):
    return f"${v:,.0f}"


def play_push(p):
    """(title, body) for the target of a play, or None."""
    nick, does = CARDS.get(p.get("card", ""), (p.get("card", "A card"), ""))
    who = p.get("fromName") or "Someone"
    outcome = p.get("outcome")
    if outcome == "landed":
        return f"🃏 {who} played {nick} on you", does or "It landed."
    if outcome == "blocked":
        return (f"🛡️ {who} played {nick} on you",
                "Your shield stopped it. It's recharging now.")
    if outcome == "lockdown":
        return f"🛡️ {who} tried {nick} on you", "Your Lockdown held."
    if outcome == "mirrored":
        return (f"🪞 {who} played {nick} on you",
                "Your Own Goal sent it straight back.")
    if outcome == "heist-won":
        return (f"🏀 {who} pulled off a Half-Court Heave",
                f"They cracked your vault and took "
                f"{money(p.get('amount') or 0)}.")
    if outcome == "heist-lost":
        return (f"🏀 {who} tried a Half-Court Heave on you",
                "They picked the wrong vault. Your money's safe.")
    return None


def duel_push(d, stage):
    a, b = d.get("aName") or "Someone", d.get("bName") or "Someone"
    leg = d.get("leg") or {}
    stake = money(d.get("stake") or 0)
    if stage == "challenge":
        return (f"⚔️ {a} challenged you to a duel",
                f"{a.split(' ')[0]} takes {_leg_label(leg)}. "
                f"{stake} each, winner takes both.")
    return (f"⚔️ {b} accepted your duel",
            f"{_leg_label(leg)}, {stake} each. Game on.")


def _leg_label(leg):
    team = leg.get("homeTeam") if leg.get("home") else leg.get("awayTeam")
    m = leg.get("market")
    line = leg.get("line") or 0
    if m == "spread":
        sign = "PK" if line == 0 else (f"+{line:g}" if line > 0
                                       else f"−{-line:g}")
        return f"{team} {sign}"
    if m == "total":
        return f"{'Over' if leg.get('home') else 'Under'} {line:g}"
    return f"{team} to win"


# ── The Commissioner's weekly balance, as chaos_balance.dart counts it ──

def balance(plays, states, players, now, hits_per_week=3):
    """[(name, value, flag, advice)] — flag is good / watch / bad."""
    since = now - dt.timedelta(days=7)

    def at(p):
        try:
            return dt.datetime.fromisoformat(
                (p.get("at") or "").replace("Z", "+00:00"))
        except ValueError:
            return None

    week = [p for p in plays if (at(p) or since) >= since]
    attacks = [p for p in week if p.get("to")]
    landed = [p for p in attacks
              if p.get("outcome") in ("landed", "heist-won", "heist-lost")]
    n = max(1, len(players))
    out = []
    if attacks:
        rate = len(landed) / len(attacks)
        flag = ("good" if 0.40 <= rate <= 0.65 else
                "watch" if 0.30 <= rate <= 0.75 else "bad")
        advice = ("defense too strong — try a longer shield recharge"
                  if rate < 0.40 else
                  "offense too strong — try a shorter shield recharge"
                  if rate > 0.65 else None)
        out.append(("Land rate", f"{round(rate * 100)}%", flag, advice))
    else:
        out.append(("Land rate", "—", "watch", "no cards played on anyone"))
    per_day = len(week) / n / 7
    out.append(("Plays per player per day", f"{per_day:.1f}",
                "good" if per_day >= 1 else
                "watch" if per_day >= 0.5 else "bad",
                "people aren't playing cards" if per_day < 0.5 else None))
    hits = {}
    for p in landed:
        hits[p["to"]] = hits.get(p["to"], 0) + 1
    at_cap = sum(1 for h in hits.values() if h >= hits_per_week) / n
    out.append(("At the weekly hit cap", f"{round(at_cap * 100)}%",
                "good" if at_cap <= 0.15 else
                "watch" if at_cap <= 0.3 else "bad",
                "people are being piled on" if at_cap > 0.15 else None))
    return out


def report_text(lines):
    dots = {"good": "🟢", "watch": "🟡", "bad": "🔴"}
    parts = []
    for name, value, flag, advice in lines:
        parts.append(f"{dots[flag]} {name} {value}"
                     + (f" ({advice})" if advice and flag != "good" else ""))
    return ". ".join(parts) + "."


# ── The pass ─────────────────────────────────────────────────────────────

def run(fs_get, fs_list, fs_patch, send, now=None):
    """Tell everyone what happened since last time. Returns pushes sent."""
    now = now or dt.datetime.now(dt.timezone.utc)
    launched = notify.already_sent(fs_get, LAUNCH_KEY)
    sent = 0

    def tell(key, uid, push):
        nonlocal sent
        if notify.already_sent(fs_get, key):
            return
        if launched and push:
            send(uid, *push)
            sent += 1
        notify.record_sent(fs_patch, key, "chaos", uid)

    crews = [(_id(d), _doc(d)) for d in fs_list("crews")]
    all_plays = []
    for cid, crew in crews:
        members = crew.get("members") or []
        for d in fs_list(f"crews/{cid}/plays"):
            p = _doc(d)
            pid = _id(d)
            all_plays.append(p)
            if p.get("to"):
                tell(f"chaos__play__{pid}", p["to"], play_push(p))
            elif p.get("card") == "Stadium Blackout":
                for m in members:
                    if m != p.get("from"):
                        tell(f"chaos__play__{pid}__{m}", m,
                             (f"🔌 {p.get('fromName')} pulled the plug",
                              "Stadium Blackout: no bets for anyone in "
                              f"{crew.get('name', 'the crew')} for an hour."))

    for d in fs_list("duels"):
        duel, did = _doc(d), _id(d)
        status = duel.get("status")
        if status in ("pending", "accepted", "declined", "cancelled"):
            tell(f"chaos__duel__{did}__challenge", duel.get("b"),
                 duel_push(duel, "challenge") if status == "pending" else None)
        if status == "accepted":
            tell(f"chaos__duel__{did}__accepted", duel.get("a"),
                 duel_push(duel, "accepted"))

    # Bets on your slip: a leg whose game id is prop_{owner}_{slip}_{side}.
    for owner in fs_list("fumbling"):
        bettor = _id(owner)
        name = (_doc(owner).get("name") or "Someone")
        for d in fs_list(f"fumbling/{bettor}/slips"):
            for leg in _doc(d).get("legs") or []:
                gid = (leg or {}).get("gameId") or ""
                if not gid.startswith("prop_"):
                    continue
                parts = gid[len("prop_"):].split("_")
                if len(parts) < 3:
                    continue
                target, side = parts[0], parts[-1]
                what = (leg.get("homeTeam") or "your slip")
                tell(f"chaos__prop__{_id(d)}", target,
                     (f"👀 {name} bet on your slip",
                      f"{name.split(' ')[0]} says it "
                      f"{'hits' if side == 'hit' else 'misses'}. "
                      f"({what})"))

    # Monday: the Commissioner's balance report, once a week.
    if now.weekday() == 0 and now.hour >= 15:
        monday = now.date().isoformat()
        players = {m for _, c in crews if c.get("chaos")
                   for m in (c.get("members") or [])}
        lines = balance(all_plays, {}, players, now)
        tell(f"chaos__report__{monday}", COMMISSIONER,
             ("📊 Chaos balance this week", report_text(lines)))

    if not launched:
        notify.record_sent(fs_patch, LAUNCH_KEY, "chaos", "launch")
        print("  chaos alerts: launched — everything so far marked as told")
    return sent
