#!/usr/bin/env python3
"""Scoring, mirrored from the Dart app so the scheduler can total a season.

THIS IS A SECOND COPY OF THE RULES THAT DECIDE WHO WINS. Treat it that way.

The app scores in Dart (`buildSlate`, `Game.didCover`, `computeWeeklyPoints`,
`LineupSlot.points`). Aggregating season standings server-side means the
same arithmetic has to exist here, in Python, and two copies of a rule drift
— usually silently, and usually in the direction nobody notices until
someone is told they lost.

The guard against that is `test/scoring_parity_test.dart` plus
`test/fixtures/scoring_cases.json`: one file of cases that BOTH languages
run and must agree on. **If you change scoring anywhere, add a case there
first and make both sides pass it.**

Mirrors, function for function:
  pick_spread   <- parseGame's consensus-or-first-provider choice
  build_slate   <- buildSlate (spread required, sort by kickoff, dedupe)
  did_cover     <- Game.didCover
  weekly_points <- computeWeeklyPoints
"""

import datetime as dt

SLOT_POINTS = {
    "qb": 7,
    "rb": 6,
    "wr": 5,
    "te": 4,
    "def": 3,
    "flex": 2,
    "kicker": 1,
}

# 7+6+5+4+3+2+1. Derived rather than written down, so it cannot drift from
# the table above the way a second copy of a rule always eventually does.
WEEKLY_MAX_POINTS = sum(SLOT_POINTS.values())


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_spread(line_data):
    """The consensus provider's spread, else the first provider listed.

    Mirrors parseGame. The order matters: CFBD returns several books and
    picking a different one changes who covered.
    """
    if not line_data:
        return None
    lines = line_data.get("lines") or []
    best = None
    for line in lines:
        if str(line.get("provider", "")).lower() == "consensus":
            best = line
            break
        if best is None:
            best = line
    return _as_float(best.get("spread")) if best else None


def parse_status(game):
    """Mirrors _parseStatus: completed wins, then any score means live."""
    if game.get("completed") is True:
        return "final"
    if game.get("homePoints") is not None:
        return "live"
    return "scheduled"


# ── Week 0 ───────────────────────────────────────────────────────────────
#
# College football opens with a handful of games the weekend BEFORE the
# real first Saturday, and everyone calls that Week 0. CFBD does not: its
# 2026 week 1 runs Aug 29 -> Sep 8 and holds 389 games across BOTH
# weekends. Left alone that makes the app's first week ten days long, and
# — worse — build_slate de-dupes by team, so every team playing on both
# weekends would have its second game silently dropped.
#
# So the app numbers its own weeks. Only CFBD's week 1 is ever split:
#
#   app week 0  -> CFBD week 1, kickoff before the split
#   app week 1  -> CFBD week 1, kickoff on or after the split
#   app week N  -> CFBD week N, all of it
#
# Every app week from 1 up keeps CFBD's number, which is what stops the
# app drifting a week behind every scoreboard in the country.
#
# MIRRORS lib/core/utils/season_weeks.dart. Change these together.


def week_zero_ends_at(season):
    """Midnight Mountain on the Tuesday after the season's first Saturday.

    Tuesday because that is when this app already rolls a week over, and
    because football does the same — nothing is scheduled between a Monday
    night game and the following Wednesday. For 2026 that is Sep 1, a day
    with no games on either side of it.

    UTC-6, not -7: Mountain is on daylight time through September.
    """
    d = dt.datetime(season, 8, 25, tzinfo=dt.timezone.utc)
    while d.weekday() != 5:          # 5 == Saturday
        d += dt.timedelta(days=1)
    tuesday = d + dt.timedelta(days=3)
    return tuesday.replace(hour=6, minute=0, second=0, microsecond=0)


def cfbd_week_for(app_week):
    """Which CFBD week to ASK for, given an app week."""
    return 1 if app_week == 0 else app_week


def _kickoff(raw):
    value = raw.get("startDate") or raw.get("start_date")
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def in_app_week(season, app_week, raw):
    """Whether one raw CFBD game belongs to [app_week].

    A game with no kickoff time is KEPT, never dropped: being unable to
    place a game is our problem, and hiding it would take it off somebody's
    slate for a reason they could never see.
    """
    if app_week > 1:
        return True
    kickoff = _kickoff(raw)
    if kickoff is None:
        return True
    split = week_zero_ends_at(season)
    return kickoff < split if app_week == 0 else kickoff >= split


def fbs_only(games_raw):
    """Games with at least one FBS team, judged from the DATA not the query.

    CFBD renamed the games filter from `division` to `classification`, and
    an unrecognised query parameter is ignored rather than rejected — so
    `division=fbs` silently started returning every division. On Aug 28
    2026 that turned the eight-game preseason slate into fifty-two, with
    D2 games that had already kicked off showing as live and final, which
    in turn woke the Live Activity and the widgets a day early.

    Both parameter names are still sent, but a filter the SERVER applies
    can be renamed out from under us again. This one reads the
    classification already present on every game, so a third rename costs
    nothing. A game with no classification at all is kept — dropping a
    real game is worse than carrying a stray one.
    """
    out = []
    for g in games_raw or []:
        home = g.get("homeClassification")
        away = g.get("awayClassification")
        if home is None and away is None:
            out.append(g)
        elif "fbs" in (home, away):
            out.append(g)
    return out


def apply_scoreboard(games_raw, scoreboard):
    """Overlay CFBD's LIVE scoreboard onto the schedule rows.

    **`/games` DOES NOT CARRY LIVE SCORES.** This project assumed it did —
    "the regular games endpoint already reflects live scores, no special
    live endpoint needed" — and that assumption went untested until the
    first kickoff of the season. Eighteen minutes into North Carolina at
    TCU on Aug 29 2026, `/games` still returned `homePoints: null`,
    `period: null`, `clock: null`, while `/scoreboard` had the game
    `in_progress`, 1st quarter, 9:22 left, UNC 3 TCU 0.

    Without this the app has no live scores at all — not on Game Day, not
    on the Locked In board, not on a card — and nothing goes final until
    CFBD backfills `/games` some time after the whistle.

    `/games` stays the source of truth for WHICH games exist and their
    spreads; the scoreboard supplies what is happening in them right now.
    Only fields the scoreboard actually knows are copied, and a game it
    does not mention is left exactly as it was.
    """
    by_id = {}
    for entry in (scoreboard or []):
        if entry.get("id") is not None:
            by_id[entry["id"]] = entry

    out = []
    for g in (games_raw or []):
        live = by_id.get(g.get("id"))
        if not live:
            out.append(g)
            continue
        merged = dict(g)
        home = (live.get("homeTeam") or {}).get("points")
        away = (live.get("awayTeam") or {}).get("points")
        if home is not None:
            merged["homePoints"] = home
        if away is not None:
            merged["awayPoints"] = away
        for key in ("period", "clock"):
            if live.get(key) is not None:
                merged[key] = live[key]
        # WHO HAS THE BALL, for the possession marker on Game Day. Only the
        # scoreboard knows this and only while a TV feed is attached — 13 of
        # 17 live games carried it when this was added, so the app has to
        # read a missing value as "not shown" and never as "away team".
        # `situation` ("2nd & 2 at AUB 41") rides along for free.
        for key in ("possession", "situation"):
            if live.get(key) is not None:
                merged[key] = live[key]
        # WHERE TO WATCH IT — "ESPN2", "BTN", "ESPN+". Logan, Sep 12 2026:
        # "a small note ... on the game card pop ups ... that shows what
        # channel or service is showing that game." `/games` does not carry
        # it and `/games/media` would be another call on every run; the
        # scoreboard already has it for every game in its window, free.
        if live.get("tv"):
            merged["tv"] = live["tv"]
        # NEVER UN-FINISH A GAME. `/games` backfills `completed` eventually
        # and the scoreboard drops finished games from its window, so the
        # merge has to be one-way: either source saying final makes it
        # final, and neither can take that back.
        if live.get("status") == "completed" or g.get("completed"):
            merged["completed"] = True
        out.append(merged)
    return out


def carry_live_forward(games_raw, prior_games):
    """Never publish a live field emptier than the one already cached.

    **THE FAILURE THIS EXISTS TO STOP, seen live on Sep 5 2026.** The
    `/scoreboard` call in `update_cache` is wrapped in a try/except that
    prints "scoreboard skipped" and carries on. Carrying on means writing
    the raw `/games` rows — and `/games` reports `homePoints: null`,
    `period: null`, `clock: null` for anything still being played. So one
    flaky scoreboard call did not merely fail to add live scores, it
    ERASED the ones already published: seventeen games in progress went to
    a blank score across every phone at once, mid-afternoon, while the
    finished games kept theirs and made it look like the app had broken
    rather than the feed.

    A run that learns nothing new must publish what it already had. Only
    empty fields are filled, so a real update always wins, and `completed`
    is one-way for the same reason it is in [apply_scoreboard]: a game that
    has finished cannot un-finish because a later feed forgot about it.
    """
    prior = {}
    for g in (prior_games or []):
        if g.get("id") is not None:
            prior[g["id"]] = g

    out = []
    for g in (games_raw or []):
        old = prior.get(g.get("id"))
        if not old:
            out.append(g)
            continue
        merged = dict(g)
        # `tv` too: the scoreboard drops a game from its window once it is
        # over, and the channel should stay on the game sheet afterwards.
        for key in ("homePoints", "awayPoints", "period", "clock",
                    "possession", "situation",
                    "homeLineScores", "awayLineScores", "tv"):
            if merged.get(key) is None and old.get(key) is not None:
                merged[key] = old[key]
        if old.get("completed"):
            merged["completed"] = True
        out.append(merged)
    return out


def games_in_app_week(season, app_week, games_raw):
    """The half of a CFBD week that belongs to one app week."""
    return [g for g in (games_raw or []) if in_app_week(season, app_week, g)]


def build_slate(games_raw, lines_raw):
    """Mirrors buildSlate: spread required, earliest first, teams de-duped.

    The de-dupe matters for scoring, not just display: preseason data can
    list a team in two games, and keeping both would let one pick be scored
    against the wrong game.
    """
    lines_by_id = {}
    for line in lines_raw or []:
        if line.get("id") is not None:
            lines_by_id[line["id"]] = line

    games = []
    for raw in games_raw or []:
        spread = pick_spread(lines_by_id.get(raw.get("id")))
        if spread is None:
            continue  # only slate games with a spread, same as the app
        games.append({
            "id": str(raw.get("id")),
            "homeTeam": raw.get("homeTeam"),
            "awayTeam": raw.get("awayTeam"),
            "startDate": raw.get("startDate"),
            "spread": spread,
            "homeScore": raw.get("homePoints"),
            "awayScore": raw.get("awayPoints"),
            "status": parse_status(raw),
        })

    games.sort(key=lambda g: g["startDate"] or "")

    seen, deduped = set(), []
    for game in games:
        if game["homeTeam"] in seen or game["awayTeam"] in seen:
            continue
        seen.add(game["homeTeam"])
        seen.add(game["awayTeam"])
        deduped.append(game)
    return deduped


def did_cover(game, picked_team):
    """Mirrors Game.didCover. None until the game is final.

    Home covers when homeMargin > -spread; away when -homeMargin > spread.
    A push (exactly on the number) is NOT a cover, matching the app.
    """
    if game.get("status") != "final":
        return None
    home, away, spread = (
        game.get("homeScore"), game.get("awayScore"), game.get("spread"))
    if home is None or away is None or spread is None:
        return None

    home_margin = home - away
    if picked_team == game.get("homeTeam"):
        return home_margin > -spread
    return -home_margin > spread


def _kickoffs(games):
    out = []
    for g in games or []:
        start = g.get("startDate")
        if start:
            out.append(dt.datetime.fromisoformat(start.replace("Z", "+00:00")))
    return out


# Saturday is judged on a FIXED UTC-5, not on the viewer's clock and not on
# UTC. UTC is wrong in the obvious place: a Friday 8pm Eastern kickoff is
# 00:00Z SATURDAY, and week 1 of 2026 had two of them. A fixed -5 is right
# all season, across the November clock change too: it is an hour off
# during daylight time, and the only games that hour could misfile would
# start between midnight and 1am Eastern on a Saturday, which do not exist.
# Mirrored exactly in seasonLostForWeek, which is why it is arithmetic
# rather than a timezone database Dart does not have.
_SATURDAY_OFFSET = dt.timedelta(hours=-5)
_SATURDAY = 5  # datetime.weekday(): Monday is 0


def blank_slot_deadline(games):
    """When an EMPTY slot starts costing points: the week's first SATURDAY
    kickoff.

    **LOGAN'S CALL, Sep 11 2026**, over two alternatives he was shown:
    charging at the week's LAST kickoff (when a slot genuinely can no longer
    be filled) left every no-show on +0 from Thursday to Monday night and
    then dropped them to -28 all at once. Saturday's first kickoff is when
    the week actually starts for most people, so it is when a missing
    lineup starts to show.

    **THE CONSEQUENCE, accepted knowingly:** somebody with five picks and
    two empty slots at noon Saturday is charged for those two even though
    a 5pm game could still fill them. The charge disappears the moment
    they fill the slot. It reads as "you are losing these", which is true.

    A week with no Saturday game at all (bowl season) falls back to its last
    kickoff, when nothing can be filled any more. Null for an empty slate.
    """
    kicks = _kickoffs(games)
    if not kicks:
        return None
    saturdays = [k for k in kicks
                 if (k + _SATURDAY_OFFSET).weekday() == _SATURDAY]
    return min(saturdays) if saturdays else max(kicks)


def weekly_lost(picks, games, now=None):
    """The points a lineup has DEFINITIVELY dropped in one week.

    **WHY THIS IS NOT SIMPLY "28 MINUS WHAT YOU WON".** A week in progress
    has points that are neither won nor lost yet, and charging them as
    losses is the bug this exists to end: on Sep 5 2026 the global board
    showed Oakley +16 while Game Day showed him +21, and Game Day was
    right. He had 22 banked, one point missed, and five still being played
    — the season board charged those five against him.

    **AND WHY A BLANK SLOT IS STILL CHARGED.** A first pass counted only
    settled misses, and a test caught what that would have done: somebody
    who never picked has no misses, so they would have been charged NOTHING
    and finished above everybody who played and lost. Logan settled that
    rule the same morning — *"people that are genuinely trying should not
    be penalized more than someone that never shows up"* — so a no-show is
    charged the full 28.

    Two clocks, then, and they are different on purpose:

      * **a PICK** costs you only once its game is FINAL and did not cover.
        Until then it is still winnable.
      * **an EMPTY SLOT** costs you from the week's first SATURDAY kickoff
        — see blank_slot_deadline for why that moment and not another.

    Once every game is final both clocks have run and `won + lost == 28`,
    so `won - lost` equals `2 x won - 28` exactly.
    """
    games_by_id = {g["id"]: g for g in games}
    total = 0
    filled = set()

    for slot, pick in (picks or {}).items():
        points = SLOT_POINTS.get(slot)
        if points is None:
            continue
        filled.add(slot)
        game = games_by_id.get(str(pick.get("gameId")))
        if game is None:
            continue
        if did_cover(game, pick.get("team")) is False:
            total += points

    deadline = blank_slot_deadline(games)
    when = now or dt.datetime.now(dt.timezone.utc)
    if deadline is not None and when >= deadline:
        for slot, points in SLOT_POINTS.items():
            if slot not in filled:
                total += points

    return total


def weekly_points(picks, games):
    """Mirrors computeWeeklyPoints.

    [picks] is {slot_name: {"gameId": str, "team": str}}. A pick whose game
    isn't on the slate scores nothing rather than raising — the app does the
    same, and a missing game is a data problem, not a reason to lose a
    whole season total.
    """
    games_by_id = {g["id"]: g for g in games}
    total = 0
    for slot, pick in (picks or {}).items():
        points = SLOT_POINTS.get(slot)
        if points is None:
            continue
        game = games_by_id.get(str(pick.get("gameId")))
        if game is None:
            continue
        if did_cover(game, pick.get("team")) is True:
            total += points
    return total
