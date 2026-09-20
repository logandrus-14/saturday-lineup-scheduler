#!/usr/bin/env python3
"""Trophies, mirrored from the Dart app so the scheduler can award them.

A SECOND COPY OF A RULE, like scoring.py — and guarded the same way.

**Why the server does this at all (Sep 18 2026).** Until build 55 the phone
worked out your trophies from your own picks and wrote the list onto your
profile, where your groupmates read it. That meant a trophy only appeared
for other people after its owner happened to open their trophy case, and
it meant anybody with a modified app could write whatever they liked onto
their own profile. The scheduler already reads every lineup and every
slate to total the standings, and it has admin rights; it writes
`users/{uid}.trophies` and the security rules now stop a phone doing so.

The phone still works out its OWN case locally, from the same rules — it
has to, to show the grey "not won yet" medals and what each one takes. So
the two must agree, and `test/fixtures/trophy_cases.json` is run by both
`test/trophy_parity_test.dart` and `scheduler/test_trophy_parity.py`.
**Change a trophy rule in lib/features/profile/domain/trophies.dart and
here together, with a fixture case first.**

Mirrors, function for function:
  scored_picks_for <- scoredPicksFor (scorecard.dart)
  unfinished_weeks <- the `unfinished` half of seasonPickLedger
  earned_trophies  <- trophiesFrom + earnedIds (trophies.dart)
"""

import datetime as dt

from scoring import SLOT_POINTS, did_cover

# Mirrors perfectWeekFloor / bigWeekPoints in trophies.dart.
PERFECT_WEEK_FLOOR = 4
BIG_WEEK_POINTS = 20

# LineupSlot.values order, which is the tie-break when two picks kick off
# at the same moment.
SLOT_ORDER = list(SLOT_POINTS)

# The order the case lists them in — earned_trophies returns ids in it.
TROPHY_IDS = [
    "perfect_week", "streak_10", "big_week", "streak_5",
    "dog_lover", "chalk", "qb_room", "iron_man",
]


def _when(start):
    return dt.datetime.fromisoformat(str(start).replace("Z", "+00:00"))


def scored_picks_for(week, picks, games):
    """Mirrors scoredPicksFor: finished picks only, with the line taken.

    [picks] is {slot: {"gameId", "team"}}, [games] build_slate output.
    """
    by_id = {g["id"]: g for g in games or []}
    out = []
    for slot, pick in (picks or {}).items():
        if slot not in SLOT_POINTS:
            continue
        game = by_id.get(str(pick.get("gameId")))
        if game is None or game.get("status") != "final":
            continue
        team = pick.get("team")
        covered = did_cover(game, team)
        spread = game.get("spread")
        if covered is None or spread is None:
            continue
        out.append({
            "week": week,
            "slot": slot,
            "team": team,
            "gameId": game["id"],
            "startDate": game.get("startDate"),
            "covered": covered,
            "line": spread if team == game.get("homeTeam") else -spread,
        })
    return out


def unfinished_weeks(lineups, slates):
    """Weeks in which somebody has a pick still to finish.

    A perfect week has to be over FOR YOU before it counts — the same rule
    as the celebration (isPerfectWeek). Four covers by Saturday tea-time
    with three still to play is a good afternoon, and awarding a trophy for
    it would mean taking one away two hours later.

    [lineups] is {week: picks}, [slates] {week: build_slate output}.
    """
    out = set()
    for week, picks in lineups.items():
        by_id = {g["id"]: g for g in slates.get(week) or []}
        for slot, pick in (picks or {}).items():
            if slot not in SLOT_POINTS:
                continue
            game = by_id.get(str(pick.get("gameId")))
            if game is not None and game.get("status") != "final":
                out.add(week)
    return out


def _record(picks):
    won = sum(1 for p in picks if p["covered"])
    return won, len(picks) - won


def _rate(won, lost):
    return None if won + lost == 0 else won / (won + lost)


def perfect_weeks(picks, unfinished=()):
    """The weeks that were clean sheets, oldest first. Never the preseason."""
    by_week = {}
    for p in picks:
        by_week.setdefault(p["week"], []).append(p)
    return sorted(
        w for w, ps in by_week.items()
        if w not in unfinished and w != 0
        and len(ps) >= PERFECT_WEEK_FLOOR
        and all(p["covered"] for p in ps))


def earned_trophies(picks, weeks_in_season, unfinished=()):
    """The ids of every trophy earned, in case order.

    The preseason (week 0) is dropped, as it is from the standings — see
    countsTowardSeason. Mirrored in trophiesFrom.
    """
    picks = [p for p in picks if p["week"] != 0]
    by_week = {}
    for p in picks:
        by_week.setdefault(p["week"], []).append(p)

    perfect = perfect_weeks(picks, unfinished)
    big = [
        w for w, ps in by_week.items()
        if sum(SLOT_POINTS[p["slot"]] for p in ps if p["covered"])
        >= BIG_WEEK_POINTS
    ]

    # Kickoff order, and a fixed order for picks that kick off together —
    # the Dart side sorts the same way, or a run could be 5 on one and 4
    # on the other depending on which of two 3:30 games came first.
    ordered = sorted(picks, key=lambda p: (
        _when(p["startDate"]), p["gameId"], SLOT_ORDER.index(p["slot"])))
    run = best = 0
    for p in ordered:
        run = run + 1 if p["covered"] else 0
        best = max(best, run)

    # A pick'em (line 0) belongs to neither side, as in buildScorecard.
    dogs = _record([p for p in picks if p["line"] > 0])
    favs = _record([p for p in picks if p["line"] < 0])
    qb = _record([p for p in picks if p["slot"] == "qb"])

    def run_of(rec, won, rate):
        r = _rate(*rec)
        return rec[0] >= won and (r or 0) >= rate

    earned = {
        "perfect_week": bool(perfect),
        "streak_10": best >= 10,
        "big_week": bool(big),
        "streak_5": best >= 5,
        "dog_lover": run_of(dogs, 10, 0.6),
        "chalk": run_of(favs, 10, 0.6),
        "qb_room": run_of(qb, 5, 0.7),
        "iron_man": weeks_in_season >= 2 and len(by_week) >= weeks_in_season,
    }
    return [t for t in TROPHY_IDS if earned[t]]


def weeks_in_season(current_week, current_slate):
    """Mirrors weeksPlayed: finished weeks that count, never negative.

    Every week before the current one, plus the current one once every
    game in it is final. Week 0 never counts (countsTowardSeason).
    """
    settled = bool(current_slate) and all(
        g.get("status") == "final" for g in current_slate)
    completed = current_week - 1 + (1 if settled and current_week >= 1 else 0)
    return max(completed, 0)
