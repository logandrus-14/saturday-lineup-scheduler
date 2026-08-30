#!/usr/bin/env python3
"""Stays awake and refreshes scores every minute while games are being
played, instead of asking GitHub to wake us up every five minutes.

WHY THIS EXISTS
---------------
update_cache.py is scheduled with `cron: */5`. GitHub does not honour that
on free runners — measured over 30 consecutive runs in July 2026, the real
gap between runs was a median of 93 minutes, worst case 210. Scores an hour
and a half old make a live board pointless.

A scheduled run is a request; a running job is not. So this is one job that
starts and then loops internally, refreshing every 60 seconds for up to
about five and a half hours (GitHub's hard cap on a job is six). The
scheduling delay then only decides when the shift *starts*, not how fresh
the scores are once it is running.

It exits early the moment every game is final, so it never burns runner
minutes or CFBD calls overnight.

QUOTA
-----
Only /games is fetched in the loop. Spreads do not move once a game kicks
off, so /lines is fetched once at the start and reused — that halves the
cost of a Saturday. A full six-hour shift is ~330 CFBD calls, so a season
of Saturdays sits comfortably inside the plan.

Usage (normally via .github/workflows/live-refresh.yml):
  FIREBASE_SERVICE_ACCOUNT=... CFBD_API_KEY=... python3 live_refresh.py
"""

import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

from update_cache import (  # noqa: E402
    FS, PARENT, QuotaExhausted, access_token, cfbd_get, current_season,
    current_week, fs_get, opening_week_split_on, record_run,
    send_notifications, write_boards, write_global_standings,
)
from scoring import (apply_scoreboard, cfbd_week_for,  # noqa: E402
                     fbs_only, games_in_app_week, build_slate)

# Stop before GitHub's 6h job limit so the shift ends cleanly rather than
# being killed mid-write.
MAX_SHIFT = dt.timedelta(hours=5, minutes=30)
LIVE_INTERVAL = 60          # something is being played

# Consecutive failed ticks before a shift gives up and lets the next
# scheduled run start clean. Five minutes of nothing is long enough to
# ride out a bad gateway or a dropped connection, and short enough that a
# genuinely broken shift is not still pretending to watch at midnight.
MAX_TICK_FAILURES = 5
IDLE_INTERVAL = 5 * 60      # nothing live yet, kickoff still ahead
# How long before the first kickoff to bother staying awake at all.
WARMUP = dt.timedelta(hours=1)

# How often, inside a shift, to republish the GLOBAL board.
#
# It is not in the per-tick work and must not be: it reads a document per
# person, so at 60s it would cost thousands of reads an afternoon to
# produce a number that barely moves. But leaving it to update_cache means
# it is only as fresh as THAT job, and GitHub delivered gaps of 199, 313,
# 544 and 695 minutes over Aug 26-28. Somebody who joined a group during a
# Saturday simply was not on the board — reported twice by Logan on Aug 28
# and 29.
#
# Half an hour is the compromise: eleven writes across a full shift,
# roughly thirty reads each.
GLOBAL_EVERY = dt.timedelta(minutes=30)

# How often to mint a FRESH Firestore token.
#
# **A Google service-account token lasts one hour, and a shift lasts five
# and a half.** The token was fetched once at startup and never renewed,
# so every shift died at the 45-to-60 minute mark with a wall of
# `HTTP Error 401: Unauthorized` — boards, notifications, live activities,
# then the slate write itself, and the process exited. Nothing looked
# wrong until somebody noticed the scores had stopped.
#
# It killed the local shift at tick 43 on Aug 29 2026, mid-way through the
# first game of the season, and it would have killed every scheduled shift
# the same way — the 15:00 GitHub run included.
#
# Forty-five minutes leaves a quarter of an hour of headroom on a
# one-hour token.
TOKEN_EVERY = dt.timedelta(minutes=45)


def write_slate(token, season, week, games, lines):
    """Write one app week's slate.

    See `write_current_slates` below — during CFBD's week 1 there are two
    of these, not one.
    """
    body = {"fields": {
        "gamesJson": {"stringValue": json.dumps(games)},
        "linesJson": {"stringValue": json.dumps(lines)},
        "season": {"integerValue": str(season)},
        "week": {"integerValue": str(week)},
        "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                      .isoformat().replace("+00:00", "Z")},
    }}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/slate_{season}_{week}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=20).read()


def cached_lines(token, season, week):
    """Last known spreads, so the loop never has to re-fetch /lines."""
    doc = fs_get(token, f"cache/slate_{season}_{week}")
    raw = (doc or {}).get("fields", {}).get("linesJson", {}).get("stringValue")
    return json.loads(raw) if raw else []


def status_of(game):
    if game.get("completed"):
        return "final"
    if game.get("homePoints") is not None:
        return "live"
    return "scheduled"


def next_delay(games, now):
    """Seconds to wait, or None to end the shift.

    Mirrors the app's own rule so the two never disagree: watch closely
    while anything is being played, slowly while kickoff is still ahead,
    and stop once the week is done.
    """
    if not games:
        return None
    states = [status_of(g) for g in games]
    if all(s == "final" for s in states):
        return None
    if any(s == "live" for s in states):
        return LIVE_INTERVAL

    # A GAME THAT HAS KICKED OFF BUT HAS NO SCORE YET IS NEITHER.
    #
    # `status_of` calls a game "live" only once the feed publishes points,
    # and CFBD does not do that at kickoff — there is a gap of minutes.
    # In that gap the game is still "scheduled", and it is not in
    # `upcoming` either, because its start time has passed. It fell
    # through both tests and counted for nothing, so the delay was decided
    # by the NEXT kickoff three hours away and the shift ended.
    #
    # **That happened at 16:00Z on Aug 29 2026 — the first kickoff of the
    # season.** The watch stopped at the exact moment the season started,
    # and the scores sat eight minutes stale while North Carolina played
    # at TCU. Nothing errored; the shift exited 0 saying "nothing is
    # close".
    #
    # A kickoff that has passed on a game that is not final means football
    # is being played, whatever the feed has caught up with.
    for g in games:
        if status_of(g) == "final" or not g.get("startDate"):
            continue
        start = dt.datetime.fromisoformat(g["startDate"].replace("Z", "+00:00"))
        if start <= now:
            return LIVE_INTERVAL

    upcoming = []
    for g in games:
        if status_of(g) != "scheduled" or not g.get("startDate"):
            continue
        start = dt.datetime.fromisoformat(g["startDate"].replace("Z", "+00:00"))
        if start > now:
            upcoming.append(start)
    if not upcoming:
        return LIVE_INTERVAL  # kickoff passed, the feed hasn't caught up
    if min(upcoming) - now <= WARMUP:
        return IDLE_INTERVAL
    return None  # nothing for over an hour — let the shift end


def _counting_slates(token, season, through_week):
    """The built slates for every week that counts, keyed by week.

    From ONE, never zero: week 0 is the preseason and is never charged to
    the season. Mirrors write_season_standings, which is where this
    arithmetic lives for the scheduled job — kept to a handful of document
    reads so it is affordable on the live loop's slow clock.
    """
    slates = {}
    for wk in range(1, (through_week or 0) + 1):
        doc = fs_get(token, f"cache/slate_{season}_{wk}")
        if not doc:
            continue
        fields = doc.get("fields", {})
        games_raw = fields.get("gamesJson", {}).get("stringValue")
        if not games_raw:
            continue
        lines_raw = fields.get("linesJson", {}).get("stringValue")
        slates[wk] = build_slate(json.loads(games_raw),
                                 json.loads(lines_raw or "[]"))
    return slates


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    cfbd = os.environ["CFBD_API_KEY"]
    token = access_token(key)

    season = current_season()
    week = current_week(cfbd, season)
    lines = cached_lines(token, season, week)
    if not lines:
        try:
            lines = cfbd_get("/lines", cfbd, year=season, week=week,
                             seasonType="regular")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise QuotaExhausted() from e
            raise

    started = dt.datetime.now(dt.timezone.utc)
    token_minted = started
    ticks = 0
    # Consecutive ticks that could not get data — see the handlers in
    # the loop. Reset by any tick that succeeds.
    failures = 0
    # Zero so the first tick publishes: a shift that starts right after
    # somebody joins should not wait half an hour to show them.
    global_written = dt.datetime.fromtimestamp(0, dt.timezone.utc)
    print(f"shift start {started:%Y-%m-%d %H:%M}Z — {season} week {week}, "
          f"{len(lines)} line records reused")

    while dt.datetime.now(dt.timezone.utc) - started < MAX_SHIFT:
        now = dt.datetime.now(dt.timezone.utc)

        # Renew before it expires — see TOKEN_EVERY.
        #
        # **A RENEWAL THAT CANNOT REACH GOOGLE IS NOT A SHIFT THAT CANNOT
        # AUTHENTICATE.** This was deliberately left bare, on the reasoning
        # that "a shift that cannot authenticate has nothing to offer, and
        # failing here is far better than the silent 401 death this
        # replaces". That is right about CREDENTIALS and wrong about the
        # NETWORK, and the difference killed a shift at 00:14Z on Aug 30
        # 2026 — the fourth death of the opening weekend:
        #
        #   tick 62: 4 live, 3 final — next in 60s
        #   URLError: [Errno 8] nodename nor servname provided, or not known
        #
        # A DNS lookup failed for the twenty seconds this call happened to
        # land in. The key was fine, the token still had fifteen minutes on
        # it, and four games were being played. Logan noticed because the
        # app told him the scores were behind.
        #
        # That machine's connection dropped repeatedly all evening — the
        # same log carries "No route to host", handshake timeouts and three
        # more DNS failures, every one of them survived because the code
        # around them expects a flaky network. This call did not.
        #
        # So it retries, and only a renewal that keeps failing ends the
        # shift. The token is renewed at 45 minutes on a 60-minute expiry,
        # so there is a quarter of an hour of headroom to spend — far more
        # than the tick or two this takes.
        if now - token_minted >= TOKEN_EVERY:
            try:
                token = access_token(key)
                token_minted = now
                failures = 0
                print(f"  token renewed at {now:%H:%M}Z")
            except Exception as e:
                failures += 1
                print(f"  token renewal failed ({failures}/"
                      f"{MAX_TICK_FAILURES}): {e}", flush=True)
                if failures >= MAX_TICK_FAILURES:
                    print("  cannot renew the token — ending the shift so "
                          "the next scheduled run can pick it up")
                    return
                time.sleep(LIVE_INTERVAL)
                continue
        try:
            games = fbs_only(
                cfbd_get("/games", cfbd, year=season,
                         week=(cfbd_week_for(week)
                               if opening_week_split_on() else week),
                         seasonType="regular", division="fbs",
                         classification="fbs"))
            # THE SCORES COME FROM /scoreboard, NOT /games — see
            # scoring.apply_scoreboard. One extra call a tick, and without
            # it there are no live scores anywhere in the app. Never worth
            # failing the tick over: a stale score beats no refresh.
            try:
                games = apply_scoreboard(
                    games, cfbd_get("/scoreboard", cfbd,
                                    classification="fbs"))
            except Exception as e:
                print(f"  scoreboard skipped: {e}")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise QuotaExhausted() from e
            # A BAD MINUTE IS NOT A BAD AFTERNOON. This used to `raise`, and
            # on Aug 29 2026 — the first live Saturday — it ended three
            # separate shifts:
            #
            #   tick 38: 2 live, 1 final — next in 60s
            #   HTTPError: 502 Bad Gateway
            #
            # CFBD returned one bad gateway at 20:19Z and the whole 5.5-hour
            # watch exited, mid-game, with two games in play. The scoreboard
            # call directly above already had this right — "a stale score
            # beats no refresh" — and the fetch it depends on did not.
            #
            # So a failed tick is now SKIPPED, not fatal: the cache keeps the
            # last good slate, which is exactly what it is for. The counter
            # is what stops this becoming the other failure — a shift that
            # sits in a loop achieving nothing while looking alive. After
            # MAX_TICK_FAILURES consecutive misses something is actually
            # wrong, and the next scheduled run deserves a clean start.
            failures += 1
            print(f"  tick skipped ({failures}/{MAX_TICK_FAILURES}): "
                  f"HTTP {e.code} on /games", flush=True)
            if failures >= MAX_TICK_FAILURES:
                print("  too many consecutive failures — ending the shift so "
                      "the next scheduled run can pick it up")
                return
            time.sleep(LIVE_INTERVAL)
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # THE ERROR THAT WAS NOT EVEN BEING CAUGHT. A dropped connection
            # or a read timeout is not an HTTPError, so it never reached the
            # handler above and killed the shift without so much as naming
            # itself. On a laptop that sleeps, a phone tether, or a free
            # runner, this is the likeliest failure of the lot.
            failures += 1
            print(f"  tick skipped ({failures}/{MAX_TICK_FAILURES}): {e}",
                  flush=True)
            if failures >= MAX_TICK_FAILURES:
                print("  too many consecutive failures — ending the shift so "
                      "the next scheduled run can pick it up")
                return
            time.sleep(LIVE_INTERVAL)
            continue

        # A tick that got its data resets the run — MAX_TICK_FAILURES counts
        # CONSECUTIVE misses, because an afternoon with a dozen scattered
        # blips is a healthy afternoon.
        failures = 0

        # BOTH app weeks while CFBD is on its week 1: the openers and
        # Labor Day weekend come back in one payload and have to be split
        # before anybody's slate is written. See scoring.week_zero_ends_at.
        # Behind the same switch as update_cache — see SPLIT_OPENING_WEEK
        # there for why this cannot simply be deployed.
        #
        # WRAPPED FOR THE SAME REASON AS THE FETCH ABOVE. These were the
        # other two calls in this loop that could end a shift, and a
        # Firestore blip is no more worth an afternoon than a CFBD one. A
        # failed write leaves the previous slate in place, which is the same
        # outcome as a skipped tick and is what the cache is for.
        try:
            if opening_week_split_on() and cfbd_week_for(week) == 1:
                for app_week in (0, 1):
                    write_slate(token, season, app_week,
                                games_in_app_week(season, app_week, games),
                                lines)
            else:
                write_slate(token, season, week, games, lines)
            # Tell update_cache.py to stand down: while a shift is running it
            # has nothing to add, and its own runs would just spend CFBD calls
            # re-fetching what we already refreshed a moment ago.
            record_run(token)
        except Exception as e:
            print(f"  slate write skipped: {e}", flush=True)
        ticks += 1

        # Boards republish every tick, so a pick reveals within a minute of
        # its kickoff rather than whenever the next scheduled run lands.
        try:
            write_boards(token, season, week)
        except Exception as e:
            print(f"  boards skipped: {e}")

        # Notifications every tick too, so "your game is final" lands within
        # a minute of the whistle instead of whenever the next scheduled run
        # happens to fire. Every send is de-duped by event, which is what
        # makes it safe to call this ~300 times a shift.
        try:
            n = send_notifications(token, season, week,
                                   build_slate(games, lines))
            if n:
                print(f"  sent {n} notification(s)")
        except Exception as e:
            print(f"  notifications skipped: {e}")

        # The global board, on its own slow clock — see GLOBAL_EVERY.
        # Wrapped like everything else down here: a leaderboard is worth
        # less than the scores, and must never be able to cost them their
        # refresh.
        #
        # **THE SLATES ARE BUILT AND PASSED, NEVER LEFT EMPTY.** Handing
        # write_global_standings `{}` makes it write everybody on ZERO —
        # correct while nothing has been scored, and a wipe of the real
        # season totals from week 1 onward. It would have looked fine
        # tonight and quietly erased the board every thirty minutes of
        # every Saturday after Labor Day.
        #
        # Deliberately NOT write_season_standings, which does this and the
        # per-group totals: that path also sends rank-change
        # notifications, and a push loop nobody has ever seen meet a real
        # Saturday is not something to start in the live job. This
        # function sends nothing.
        if now - global_written >= GLOBAL_EVERY:
            try:
                write_global_standings(
                    token, season, week, _counting_slates(token, season, week),
                    {})
                global_written = now
            except Exception as e:
                print(f"  global standings skipped: {e}")

        delay = next_delay(games, now)
        live = sum(1 for g in games if status_of(g) == "live")
        final = sum(1 for g in games if status_of(g) == "final")
        print(f"  tick {ticks}: {live} live, {final} final"
              f" — {'stopping' if delay is None else f'next in {delay}s'}",
              flush=True)

        if delay is None:
            print("every game is final (or nothing is close) — ending shift")
            return
        time.sleep(delay)

    print(f"shift limit reached after {ticks} refresh(es); "
          f"the next scheduled run picks up from here")


if __name__ == "__main__":
    try:
        main()
    except QuotaExhausted:
        # Same reasoning as update_cache.py: a spent monthly allowance is
        # expected and self-correcting, and a red X every time would bury a
        # real failure. The app serves the cached slate meanwhile.
        print("CFBD monthly quota exhausted — leaving the cache in place.")
        sys.exit(0)
