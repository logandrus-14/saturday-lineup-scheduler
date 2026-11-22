"""The referee: flags Fumbling slips and cards that can't be real.

Security check, Nov 21 2026 (Logan: "Fix everything"). firestore.rules keep
a slip's shape sane, but they can't know when a game started, what a slip
could pay, or what a card hand should hold — so someone writing to the
database directly (skipping the app) could bet on a game that's already
over, cash out for more than a slip could ever pay, or hand themselves a
deck of cards. This pass looks at what the alerts pass already read and
flags what can't have come from the app.

It never changes a bet. A flag is a `refereeFlags/{id}` document and one
push to Logan; on the Commissioner page he voids the slip (it then counts
as a push, stake back) or marks it fine. Voiding is his call because a
wrong automatic void would take a real win off an honest player.

What it checks, all on purpose loose — only the impossible:
  late_bet     a leg on a game that started more than LATE_AFTER before the
               slip was created (Firestore's own createTime, which a phone
               can't set). Live betting happens inside that window.
  wild_price   a price no game in the app can show (the app's prices come
               from chances clamped to 1–99%).
  big_cashout  a cash-out bigger than the slip could ever have paid.
  stacked_hand a card hand or Challenge Flags far beyond what packs give.
  free_money   ledger money in for a reason the app never pays, an entry
               id it never makes, more than one entry can pay, or more of
               one kind in a day than play allows (looked at weekly).

Every Firestore call is passed in, so test_referee.py runs it on dicts.
"""
from __future__ import annotations

import datetime as dt

# Live betting is real; a game can run long. Past this after kickoff, a
# new bet on it can only be a bet on a result already known.
LATE_AFTER = dt.timedelta(hours=8)

# The app's prices: pregame lines from the books, live prices from a
# chance clamped to 1–99% (fumble_chance.dart) — inside ±10000 either way.
MAX_PLUS = 15000
MAX_MINUS = -50000

# Hands and Challenge Flags beyond anything packs hand out.
MAX_HAND = 40
MAX_MULLIGANS = 10

COMMISSIONER = "H97FRYb57EaOsvhyVXLu6bdKIv33"

# Pushes to Logan a pass at most; every flag is still on the Commissioner
# page. Keeps a first run over old slips from buzzing his phone.
MAX_PUSHES = 3


def _ts(s):
    """An ISO time (Firestore's or the app's) → aware datetime, or None."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def decimal_odds(american):
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def max_payout(slip):
    """The most [slip] could ever pay: every leg won, its card multiplier
    and bonus on top."""
    dec = 1.0
    for leg in slip["legs"]:
        if leg["price"]:
            dec *= decimal_odds(leg["price"])
    mul = max(float(slip.get("payoutMul") or 1), 1.0)
    boost = float(slip.get("boost") or 0)
    stake = float(slip["stake"])
    return stake + (stake * dec * mul - stake) * (1 + boost)


def check_slip(slip, created, start_of):
    """The problems with one slip, as (kind, detail) pairs. [created] is
    the slip document's Firestore createTime; [start_of](leg) a leg's game
    start (aware datetime) or None when the game isn't known."""
    out = []
    born = _ts(created)
    for leg in slip["legs"]:
        price = int(leg.get("price") or 0)
        label = f"{leg.get('awayTeam', '?')} @ {leg.get('homeTeam', '?')}"
        if price > MAX_PLUS or price < MAX_MINUS:
            out.append(("wild_price", f"{label} at {price:+d}"))
        if leg["gameId"].startswith("fut_") or born is None:
            continue
        start = start_of(leg)
        if start is not None and born - start > LATE_AFTER:
            hours = int((born - start).total_seconds() // 3600)
            out.append(("late_bet",
                        f"{label}: bet placed {hours}h after it started"))
    cash = slip.get("cashOut")
    if cash is not None:
        top = max_payout(slip)
        if float(cash) > top + 0.01:
            out.append(("big_cashout",
                        f"cashed out ${float(cash):,.2f}; the most it could "
                        f"pay was ${top:,.2f}"))
    return out


def check_chaos(fields):
    """The problems with one player's chaos state (raw Firestore fields)."""
    out = []
    hand = fields.get("hand", {}).get("arrayValue", {}).get("values", [])
    if len(hand) > MAX_HAND:
        out.append(("stacked_hand", f"{len(hand)} cards in hand"))
    m = fields.get("mulligans", {})
    n = int(m.get("integerValue") or m.get("doubleValue") or 0)
    if n > MAX_MULLIGANS:
        out.append(("stacked_hand", f"{n} Challenge Flags"))
    return out


# What the app writes to a ledger, and the most one entry of each can be
# (pretend dollars). Rank-ups pay 10% of a rank's floor (GOAT $1,000,000);
# a challenge 20% of the max bet; the rest move at most a max bet.
LEDGER_CAPS = {
    "rankup": 100_000, "challenge": 200_000, "duel": 1_000_000,
    "heist": 1_000_000, "wheel": 1_000_000, "parlay": 1_000_000,
    "jinx": 1_000_000, "bandwagon": 1_000_000, "insurance": 1_000_000,
}
# Each reason's entry ids, as the app makes them.
LEDGER_IDS = {
    "rankup": ("rankup_",), "challenge": ("challenge_",), "duel": ("duel_",),
    "heist": ("heist_",), "wheel": ("wheel",), "parlay": ("parlay",),
    "jinx": ("jinx_",), "bandwagon": ("bandwagon_",),
    "insurance": ("insurance_",),
}
# More money-making entries of one reason in a day than play allows.
MAX_A_DAY = 12


def check_ledger(entries):
    """The problems in one player's ledger, as (kind, subject, detail):
    [entries] are (id, fields) pairs. Only money IN is looked at — taking
    from yourself isn't a cheat."""
    out, per_day = [], {}
    for eid, f in entries:
        amt = f.get("amount", {})
        amount = float(amt.get("doubleValue") or amt.get("integerValue") or 0)
        if amount <= 0:
            continue
        reason = f.get("reason", {}).get("stringValue", "")
        if reason not in LEDGER_CAPS:
            out.append(("free_money", eid, f"${amount:,.0f} for "
                        f"'{reason or 'nothing'}'"))
            continue
        if not eid.startswith(LEDGER_IDS[reason]):
            out.append(("free_money", eid, f"a {reason} entry the app "
                        f"never makes (${amount:,.0f})"))
        elif amount > LEDGER_CAPS[reason]:
            out.append(("free_money", eid, f"${amount:,.0f} {reason}, "
                        f"more than one can pay"))
        day = (f.get("at", {}).get("stringValue")
               or f.get("at", {}).get("timestampValue") or "")[:10]
        per_day[(reason, day)] = per_day.get((reason, day), 0) + 1
    for (reason, day), n in sorted(per_day.items()):
        if n > MAX_A_DAY:
            out.append(("free_money_day", f"{reason}_{day}",
                        f"{n} {reason} payouts on {day}"))
    return out


def flag_id(kind, uid, subject):
    return f"{kind}__{uid}__{subject}".replace("/", "_")[:400]


def push_text(name, kind, detail):
    what = {
        "late_bet": "a bet on a game already over",
        "wild_price": "a price the app can't show",
        "big_cashout": "a cash-out too big",
        "stacked_hand": "a stacked card hand",
        "free_money": "bank money from nowhere",
        "free_money_day": "bank money from nowhere",
    }.get(kind, kind)
    return ("🚨 Referee flag",
            f"{name}: {what} ({detail}). Open the Commissioner page to void "
            f"it or keep it.")


def run(owners, slips_of, start_of, chaos_of, create_flag, send,
        since=None, name_of=lambda uid: "Someone", ledger_of=None):
    """Flag what can't be real. [owners] are player uids; [slips_of](uid)
    their slip docs (the alerts pass's own reads); [start_of](leg) a game
    start; [chaos_of](uid) their chaos fields, or None to skip hands;
    [create_flag](id, fields) makes a flag and returns False if it was
    already there; [send](uid, title, body) pushes. [since]: only slips
    written at or after it (Firestore updateTime) — None looks at all.
    Returns how many new flags. Never raises for one bad slip."""
    import fumble_alerts

    new = 0

    def flag(uid, kind, subject, detail, extra):
        nonlocal new
        fields = {"uid": uid, "kind": kind, "subject": subject,
                  "detail": detail, "name": name_of(uid), **extra}
        if create_flag(flag_id(kind, uid, subject), fields):
            new += 1
            if new <= MAX_PUSHES:
                title, body = push_text(name_of(uid), kind, detail)
                send(COMMISSIONER, title, body)

    for uid in owners:
        for doc in slips_of(uid):
            try:
                if since is not None and (_ts(doc.get("updateTime"))
                                          or since) < since:
                    continue
                raw = doc.get("fields", {})
                if raw.get("void", {}).get("booleanValue"):
                    continue
                slip = fumble_alerts.parse_slip(doc)
                for kind, detail in check_slip(slip, doc.get("createTime"),
                                               start_of):
                    flag(uid, kind, slip["id"], detail,
                         {"slipId": slip["id"]})
            except Exception as e:  # one odd slip never stops the pass
                print(f"  referee skipped a slip: {e}")
        if chaos_of is not None:
            fields = chaos_of(uid)
            if fields:
                for kind, detail in check_chaos(fields):
                    flag(uid, kind, "chaos", detail, {})
        if ledger_of is not None:
            for kind, subject, detail in check_ledger(ledger_of(uid)):
                flag(uid, kind, subject, detail,
                     {"entryId": subject} if kind == "free_money" else {})
    return new
