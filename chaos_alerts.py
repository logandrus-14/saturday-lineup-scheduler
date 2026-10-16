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

# A recharged shield is news for a few hours, not days — so a shield that
# came back while pushes were off isn't announced late.
SHIELD_FRESH = dt.timedelta(hours=6)

# The morning nudge for the free card, in the players' time.
REMIND_FROM, REMIND_UNTIL = 9, 12


def _players_time(now):
    """[now] in the players' time (Mountain). If the server has no time-zone
    data, a fixed UTC−6 — off by an hour in winter, never broken."""
    try:
        from zoneinfo import ZoneInfo
        return now.astimezone(ZoneInfo("America/Denver"))
    except Exception:
        return now.astimezone(dt.timezone(dt.timedelta(hours=-6)))


def _when(v):
    """An ISO time (or Firestore timestamp string) → aware datetime."""
    if not v:
        return None
    try:
        t = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)

LAUNCH_KEY = "chaos__launch"
COMMISSIONER = "H97FRYb57EaOsvhyVXLu6bdKIv33"

# Card names → their nicknames and what they do to you, as the app says it
# (lib/features/fumbling/domain/chaos_deck.dart).
CARDS = {
    "Freeze": ("Weather Delay", "You can't place a bet for an hour."),
    "Double Down": ("4th Down Conversion", "Your next slip's stake is doubled."),
    "Jinx": ("Jinx", "If that slip loses, they take 10% of it."),
    "Jumbotron": ("Kiss Cam", "Your next slip is pinned for the whole crew."),
    "Rabbit's Foot": ("Rabbit's Foot", ""),
    # Only your own phone can put it in your slips, so it goes in when you
    # next open Fumbling (Oct 13 2026: Eric cursed Zane and nothing showed).
    "Curse": ("Cursed Franchise", "A $100 bet on the day's biggest underdog "
              "goes in your slips the next time you open Fumbling."),
    "Handicap Hammer": ("Replacement Refs",
                        "Your payouts are 20% worse for three days."),
    "Lockdown": ("Shutout", ""),
    "Bad Beat Insurance": ("Bad Beat Insurance", ""),
    "Mulligan": ("Challenge Flag", ""),
    "Steal the Slip": ("Midnight Trade",
                       "One of your picks just got flipped."),
    "Mirror": ("Mirror", ""),
    "Copycat": ("Copycat", ""),
    "Heist": ("Half-Court Heave", ""),
    "Golden Ticket": ("Hole-in-One", ""),
    "Stadium Blackout": ("Stadium Blackout", ""),
    "Miracle Comeback": ("Miracle Comeback", ""),
    # Oct 14 2026's eleven (lib/features/fumbling/domain/chaos_deck.dart).
    "Hide": ("Hide", ""),
    "Disguise": ("Pump Fake", ""),
    "Flop": ("Flop", ""),
    "Trap": ("Trap", ""),
    "Clean Sheet": ("Clean Sheet", ""),
    "Pass": ("Pass", ""),
    "Steal": ("Steal", "They took a card from your hand."),
    "Scout": ("Scouting Report",
              "For 24 hours they can see your hand and any trick you've set."),
    "Bench": ("Benched", "Your next slip can't be more than $25."),
    "Hat Trick": ("Hat Trick",
                  "Your next slip has to be a parlay of 3 or more legs."),
    "Bandwagon": ("Bandwagon", ""),
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
    card = p.get("card")
    # A Bandwagon Fan is a favour, not a hit.
    if card == "Bandwagon":
        return (f"🚌 {who} jumped on your bandwagon",
                "If your slip hits, they get 10% of what it pays. You lose "
                "nothing.")
    # The Flop: they were told it landed. You know better.
    if outcome == "landed" and p.get("fake"):
        return (f"🤕 {who} played {nick} on you",
                "Your Flop sold it. They think it landed. Nothing happened.")
    if outcome == "landed" and card == "Steal" and p.get("detail"):
        took = CARDS.get(p["detail"], (p["detail"], ""))[0]
        return f"🃏 {who} played Steal on you", f"They took your {took}."
    if outcome == "landed":
        return f"🃏 {who} played {nick} on you", does or "It landed."
    if outcome == "trapped":
        return (f"🚩 {who} walked into your Trap",
                f"Their {nick} is gone and they can't bet for 2 hours.")
    if outcome == "passed":
        return (f"🏀 Your Pass sent {who}'s {nick} on",
                f"It landed on {p.get('passedToName') or 'someone else'} "
                "instead.")
    # Logan, Oct 13 2026: "I don't think I got a notification when I lost my
    # shield I opened the app and was surprised" — the old "Your shield
    # stopped it" read as good news. Say it's gone.
    if outcome == "blocked":
        return (f"🛡️ Your shield blocked {who}'s {nick}",
                "Your shield is down now. Until it recharges, the next card "
                "played on you lands.")
    if outcome == "lockdown":
        return f"🛡️ {who} tried {nick} on you", "Your Shutout held."
    if outcome == "mirrored":
        return (f"🪞 {who} played {nick} on you",
                "Your Mirror sent it straight back.")
    if outcome == "heist-won":
        return (f"🏀 {who} pulled off a Half-Court Heave",
                f"They cracked your vault and took "
                f"{money(p.get('amount') or 0)}.")
    if outcome == "heist-lost":
        return (f"🏀 {who} tried a Half-Court Heave on you",
                "They picked the wrong vault. Your money's safe.")
    return None


def passed_push(p):
    """(title, body) for whoever a Pass sent a card to."""
    nick, does = CARDS.get(p.get("card", ""), (p.get("card", "A card"), ""))
    who = p.get("fromName") or "Someone"
    via = p.get("toName") or "Someone"
    return (f"🏀 {who}'s {nick} came your way",
            f"{via} had a Pass ready. " + (does or "It landed on you."))


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
            # A No-Look Pass: whoever it landed on hears too.
            if p.get("passedTo"):
                tell(f"chaos__play__{pid}__passed", p["passedTo"],
                     passed_push(p))
            elif p.get("card") == "Stadium Blackout":
                for m in members:
                    if m != p.get("from"):
                        tell(f"chaos__play__{pid}__{m}", m,
                             (f"🔌 {p.get('fromName')} pulled the plug",
                              "Stadium Blackout: no bets for anyone in "
                              f"{crew.get('name', 'the crew')} for an hour."))

    # Logan, Oct 15 2026, for build 72: "Your shield is back up" and a
    # morning nudge for the free card (which he can switch off: the chaos
    # doc's remindCards). Only for people in a crew with Chaos Mode on.
    in_chaos = {m for _, crew in crews if crew.get("chaos")
                for m in (crew.get("members") or [])}
    local = _players_time(now)
    today = local.date().isoformat()
    for d in fs_list("chaos"):
        uid, st = _id(d), _doc(d)
        if uid not in in_chaos:
            continue
        ready = _when(st.get("shieldReadyAt"))
        if ready and ready <= now and now - ready < SHIELD_FRESH:
            tell(f"chaos__shield__{uid}__{ready.isoformat()}", uid,
                 ("🛡️ Your shield is back up",
                  "The next card played on you gets blocked."))
        if (REMIND_FROM <= local.hour < REMIND_UNTIL
                and st.get("pickDay") != today
                and st.get("remindCards") is not False):
            tell(f"chaos__daily__{uid}__{today}", uid,
                 ("🃏 Your free card is ready",
                  "Pick one of four face down in Fumbling. Turn this off in "
                  "the Chaos hub."))

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
