#!/usr/bin/env python3
"""The two matchup alerts. Logan, Oct 4 2026: "Add the matchup alerts".

  * WEDNESDAY — who you play this week, with both records. Midweek is when
    the trash talk happens; this is what starts it.
  * WHEN THE WEEK ENDS — the result, and your record now.

Only for groups whose commissioner turned matchups on. Pure functions here
(timing and wording, tested in test_matchup_alerts.py); the sending lives
in update_cache.notify_matchups, beside every other notification, with the
same de-duplication.
"""
import datetime as dt

# Saturdays are judged on a fixed UTC−5 everywhere in this scheduler (see
# blank_slot_deadline); the Wednesday window uses the same clock.
_ET = dt.timedelta(hours=-5)
WEDNESDAY = 2
OPPONENT_HOUR = 12  # noon Wednesday, Eastern


def opponent_window(now, first_kickoff):
    """From noon Wednesday until the week's first kickoff.

    A missed Wednesday run still sends on Thursday or Friday; it never sends
    once games have started, when it would be news nobody needs.
    """
    if first_kickoff is None or now >= first_kickoff:
        return False
    local = now + _ET
    day = local.weekday()
    if day == WEDNESDAY:
        return local.hour >= OPPONENT_HOUR
    return WEDNESDAY < day <= 5  # Thursday, Friday, Saturday morning


def week_over(games):
    """Every game on the slate final — the week's result is settled."""
    return bool(games) and all(g.get("status") == "final" for g in games)


def _fmt(v):
    v = round(v)
    return f"−{-v}" if v < 0 else f"+{v}"


def _round_word(week):
    return {14: "Semifinal", 15: "Final"}.get(week)


def opponent_message(entries, week):
    """[entries] is one per matchup group you are in:
    (group_name, opponent_name or None for the average, my_record,
    their_record or None). Returns (title, body).
    """
    rnd = _round_word(week)
    if len(entries) == 1:
        group, opp, mine, theirs = entries[0]
        if opp is None:
            return ("This week: you vs the group average",
                    f"In {group}. You're {mine}. Beat the average to win it.")
        first = opp.split(" ")[0]
        title = (f"{rnd}: you vs {opp}" if rnd
                 else f"This week: you vs {opp}")
        rec = f"You're {mine}, {first} is {theirs}." if theirs else \
            f"You're {mine}."
        return (title, f"{rec} In {group}.")
    parts = [
        f"{'the group average' if opp is None else opp} in {group}"
        for group, opp, _, _ in entries
    ]
    return ("This week's matchups", " · ".join(parts))


def result_message(group, opp, mine, theirs, record):
    """The week's result for one matchup. [opp] None is the group average.
    [mine]/[theirs] are week scores; [record] is the label after it."""
    them = "the group average" if opp is None else opp
    score = f"{_fmt(mine)} to {_fmt(theirs)}"
    if mine > theirs:
        title = f"You beat {them}"
    elif mine < theirs:
        title = ("The group average beat you" if opp is None
                 else f"{opp} beat you")
    else:
        title = f"You and {them} tied"
    return (title, f"{score} in {group} · now {record}")
