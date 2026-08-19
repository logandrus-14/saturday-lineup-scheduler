#!/usr/bin/env python3
"""Self-contained score-cache updater for a GitHub Actions runner.

Fetches the current week's games + lines from CFBD once and writes them to
Firestore `cache/slate_{season}_{week}`, so every app user reads from
Firestore instead of hitting CFBD directly. Runs on GitHub's servers (see
update-cache.yml), which sidesteps the local machine's network limits.

Reads two secrets from the environment (set as GitHub Actions secrets):
  FIREBASE_SERVICE_ACCOUNT  — the full service-account JSON (as a string)
  CFBD_API_KEY              — your CollegeFootballData API key

No third-party packages needed — pure standard library.
"""

import base64
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

# Module level, NOT a local import inside main(). It was missing entirely
# from Aug 2 to Aug 16, and because the notification pass is wrapped in a
# try/except the only symptom was one line — "notifications skipped: name
# 'build_slate' is not defined" — in a log nobody reads. Every standalone
# run of this script silently sent nothing for two weeks.
#
# Up here it fails at import instead of at the moment of sending, and
# test_notification_flags.py asserts it is present.
from scoring import (build_slate, cfbd_week_for,  # noqa: E402
                     games_in_app_week, week_zero_ends_at)

PROJECT = "saturday-lineup"
CFBD = "https://api.collegefootballdata.com"
FS = "https://firestore.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/databases/(default)/documents"

# ─── DRY_RUN: do everything except change the world ──────────────────────
#
# Built Aug 16 2026 so the emergency plan for opening weekend could be
# REHEARSED. If GitHub Actions dies mid-Saturday the fallback is to run this
# script on Logan's Mac — but that fallback was unproven, because the only
# way to try it was to write real documents and push real notifications to
# nineteen people. A plan you cannot rehearse is a hope.
#
# With DRY_RUN=1 the script still authenticates, still calls CFBD, still
# builds the slate, still decides every notification — and then writes
# nothing and sends nothing. So it exercises the parts that actually break
# (credentials, network, quota, parsing) and stops at the door.
#
#     DRY_RUN=1 FORCE_REFRESH=1 python3 update_cache.py
#
# FORCE_REFRESH matters: without it the throttle may skip the whole run and
# the rehearsal proves nothing.
#
# **Reads are deliberately NOT faked.** It reads the real Firestore and the
# real CFBD, because a dry run against invented data tests the invention.
# The ~4 CFBD calls are the price, against a 30,000/month allowance.
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# ─── SPLIT_OPENING_WEEK: the Week 0 split, off until everyone has updated ─
#
# CFBD folds two weekends into its week 1. The app now splits them — see
# week_zero_ends_at in scoring.py — but the split cannot simply be deployed,
# because THE SLATE IS SHARED WITH EVERY BUILD ANYONE HAS INSTALLED.
#
# Turn it on and `slate_2026_1` becomes Sep-only. Anybody still on a build
# that predates Opening Week watches the Aug 29 games vanish from their
# slate, with no way to reach week 0 at all, and their picks on those games
# stop scoring. That is not a rollback-able mistake on a Saturday.
#
# So the code ships dark. With the switch OFF this file behaves exactly as
# it did before the split existed: one CFBD week, one slate doc, weeks
# numbered the way CFBD numbers them.
#
# TO TURN IT ON: uncomment the line in BOTH update-cache.yml and
# live-refresh.yml — both jobs write slates, and a flag set in one place
# only works on the days that job runs. Do it once most testers are on the
# Opening Week build, and not before.
#
# Anything vague — `true`, `0`, empty, `'1 '` — reads as OFF, the same rule
# the notification flags use. A half-set flag should fail towards the old
# behaviour, which is the one that is known to work.
SPLIT_OPENING_WEEK = os.environ.get("SPLIT_OPENING_WEEK") == "1"

# ─── What happens while the switch is OFF ─────────────────────────────────
#
# Not "nothing". Off means TRANSITIONAL: the scheduler keeps `slate_{s}_1`
# exactly as it was — both weekends, which is what every installed build
# expects — and ALSO writes `slate_{s}_0` with the preseason games.
#
# That combination is safe in both directions, and it is worth writing down
# why, because it looks like it should conflict:
#
#   • Builds without the split read `slate_{s}_1` and are untouched.
#   • Builds WITH the split are on week 0 right now, so they read
#     `slate_{s}_0` — from cache instead of calling CFBD themselves. That
#     fallback was burning roughly twenty CFBD calls an hour off Logan's
#     phone alone, and it scales with every tester who auto-updates.
#   • A split build cannot even LOOK at week 1 yet: the week stepper only
#     goes backwards from the current week, and the current week is 0 until
#     Sep 1. So `slate_{s}_1` being the old combined slate is invisible to
#     them until the day the switch gets flipped anyway.
#
# The boards follow the same rule — both weeks get written, because a
# board costs Firestore writes and no CFBD calls at all, and a preseason
# pick that never reveals at kickoff would be a real bug on Aug 29.

# Logged ONCE per process, not once per call: a switch nobody can see in
# the log is indistinguishable from a bug, which is the shape of the July
# outage.
_split_logged = False


def opening_week_split_on():
    global _split_logged
    if not _split_logged:
        _split_logged = True
        print("  Opening Week split "
              + ("ON" if SPLIT_OPENING_WEEK
                 else "OFF (SPLIT_OPENING_WEEK is not '1')"))
    return SPLIT_OPENING_WEEK

# What a dry run would have done, printed as a summary at the end.
_dry_writes = []
_dry_sends = []


def _send_write(req):
    """The ONE door every Firestore write goes through.

    A single choke point rather than a DRY_RUN check at each call site, so
    a write added later is covered by default instead of silently escaping
    the guard — which is the shape of bug this project keeps re-finding.
    """
    if DRY_RUN:
        path = req.full_url.split("/documents/", 1)[-1].split("?")[0]
        _dry_writes.append(path)
        print(f"    DRY RUN — would write {path}")
        return b""
    return urllib.request.urlopen(req, timeout=20).read()


def dry_run_summary():
    """Printed at the end of a dry run so the rehearsal has a verdict."""
    if not DRY_RUN:
        return
    import collections
    print()
    print("─" * 60)
    print("DRY RUN SUMMARY — nothing was written and nothing was sent")
    print(f"  Firestore writes suppressed: {len(_dry_writes)}")
    for path, n in collections.Counter(
            p.split("/")[0] + "/…" if "/" in p else p
            for p in _dry_writes).most_common():
        print(f"    {n:>4}  {path}")
    print(f"  Notifications suppressed: {len(_dry_sends)}")
    for kind, n in collections.Counter(_dry_sends).most_common():
        print(f"    {n:>4}  {kind}")
    print()
    print("  If the numbers above look right, the same command without")
    print("  DRY_RUN=1 will do exactly this for real.")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# Firestore to read and write, plus messaging to send a notification.
# Both on one token because the alternative is minting two and keeping
# them in step; nothing here needs the narrower one on its own.
SCOPES = " ".join([
    "https://www.googleapis.com/auth/datastore",
    "https://www.googleapis.com/auth/firebase.messaging",
])


def access_token(key: dict) -> str:
    now = int(dt.datetime.now().timestamp())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({
        "iss": key["client_email"],
        "scope": SCOPES,
        "aud": key["token_uri"],
        "iat": now,
        "exp": now + 3600,
    }).encode())
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as pem:
        pem.write(key["private_key"])
        pem.flush()
        sig = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", pem.name],
            input=f"{header}.{payload}".encode(),
            capture_output=True, check=True).stdout
    jwt = f"{header}.{payload}.{_b64url(sig)}"
    body = (
        "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer"
        f"&assertion={jwt}"
    ).encode()
    req = urllib.request.Request(
        key["token_uri"], data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["access_token"]


def cfbd_get(path, key, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(f"{CFBD}{path}?{qs}")
    req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def current_season():
    now = dt.datetime.now()
    return now.year - 1 if now.month <= 2 else now.year


def current_week(key, season):
    try:
        cal = cfbd_get("/calendar", key, year=season)
        now = dt.datetime.now(dt.timezone.utc)
        regular = [w for w in cal if w.get("seasonType") == "regular"]
        for w in regular:
            end = w.get("endDate") or w.get("lastGameStart")
            if end and now < dt.datetime.fromisoformat(
                    end.replace("Z", "+00:00")):
                cfbd_wk = int(w["week"])
                # CFBD's week 1 is TWO of this app's weeks — the openers
                # and Labor Day weekend, ten days in one bucket. See
                # week_zero_ends_at in scoring.py. Until the split is
                # switched on, CFBD's numbering is ours.
                if cfbd_wk != 1 or not opening_week_split_on():
                    return cfbd_wk
                return 0 if now < week_zero_ends_at(season) else 1
        if regular:
            return int(regular[-1]["week"])
    except Exception:
        pass
    if (SPLIT_OPENING_WEEK
            and dt.datetime.now(dt.timezone.utc) < week_zero_ends_at(season)):
        return 0
    return min(max(1, (dt.datetime.now() - dt.datetime(season, 8, 24)).days
                   // 7 + (0 if SPLIT_OPENING_WEEK else 1)), 15)


# ── How often to actually hit CFBD ───────────────────────────────────────
#
# The workflow wakes every 5 minutes (GitHub's minimum), but each real run
# costs THREE CFBD calls (/calendar, /games, /lines). Refreshing on every
# wake-up would burn ~26,000 calls a month — enough to exhaust the plan on
# its own, which is exactly how the quota ran out in July.
#
# So the run rate follows the football calendar: minutes apart on a
# Saturday when scores actually move, an hour apart on a Tuesday when
# nothing changes. Skipping is free — it costs one Firestore read and no
# CFBD calls at all.
MAX_AGE_BY_WEEKDAY = {
    5: 5,    # Saturday — scores moving, refresh hard
    3: 10,   # Thursday — weeknight games
    4: 10,   # Friday
    6: 10,   # Sunday — late finals settling
    0: 60,   # Monday
    1: 60,   # Tuesday
    2: 60,   # Wednesday
}

# ...but the weekday only matters once there is football. Between seasons,
# and in the weeks before kickoff, a Saturday is just a Saturday: nothing
# is being played, so nothing can change, and refreshing every five minutes
# spends the monthly allowance on data that is already correct.
#
# This bit us for real. In the two days after the August 2026 rollover the
# app burned ~29,500 of 30,000 calls with the season 26 days away, and the
# quota does not reset until September — after opening weekend.
QUIET_MAX_AGE = 12 * 60          # refresh twice a day when nothing is close
QUIET_IF_KICKOFF_BEYOND = 24     # hours

STATE_DOC = "scheduler_state"


def read_state(token: str):
    """(minutes since last run, hours until the next kickoff).

    Both may be None. Costs ONE Firestore read and no CFBD calls, which is
    the point — the throttle has to be cheaper than the thing it guards, or
    it would be spending calls to decide whether to spend calls.

    `nextKickoffAt` is written by the run that last fetched a slate, so the
    throttle knows how close football is without asking CFBD.
    """
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{STATE_DOC}",
        headers={"Authorization": f"Bearer {token}"})
    try:
        doc = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception:
        return None, None

    fields = doc.get("fields", {})
    now = dt.datetime.now(dt.timezone.utc)

    mins = None
    stamp = fields.get("lastRun", {}).get("timestampValue")
    if stamp:
        last = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        mins = (now - last).total_seconds() / 60

    hours = None
    kick = fields.get("nextKickoffAt", {}).get("timestampValue")
    if kick:
        k = dt.datetime.fromisoformat(kick.replace("Z", "+00:00"))
        hours = (k - now).total_seconds() / 3600

    return mins, hours


def minutes_since_last_run(token: str):
    """Back-compat shim — live_refresh imports this."""
    return read_state(token)[0]


def record_next_kickoff(token: str, games) -> None:
    """Remember when football next happens, so the throttle can be lazy.

    Cheap, and it is what lets a run in the off-season skip CFBD entirely
    rather than spending three calls to rediscover that the season has not
    started.
    """
    now = dt.datetime.now(dt.timezone.utc)
    upcoming = []
    for g in games or []:
        raw = g.get("startDate")
        if not raw:
            continue
        try:
            start = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if start > now:
            upcoming.append(start)
    if not upcoming:
        return
    _fs_patch(token, f"cache/{STATE_DOC}", {
        "nextKickoffAt": {"timestampValue":
                          min(upcoming).isoformat().replace("+00:00", "Z")},
    })


def record_run(token: str) -> None:
    """Mark that we are about to spend CFBD calls.

    Called BEFORE the calls, not after they succeed. It used to be the last
    line of a successful run, which deadlocked: a 429 raised before it, so
    the marker was never written, so minutes_since_last_run stayed None, so
    the throttle never engaged, so every 5-minute wake-up spent three more
    CFBD calls — roughly 26,000 a month, which is what exhausted the quota
    that caused the 429. The failure kept causing itself, silently, and the
    cache sat 9 days stale in July 2026.

    Recording the attempt instead means a bad run costs one throttle
    interval rather than an endless retry loop.
    """
    body = {"fields": {"lastRun": {"timestampValue": dt.datetime.now(
        dt.timezone.utc).isoformat().replace("+00:00", "Z")}}}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{STATE_DOC}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    _send_write(req)


class QuotaExhausted(Exception):
    """CFBD's monthly call allowance is spent."""


def write_json_cache(token, doc_id, payload):
    """One CFBD response cached as a JSON blob under cache/<doc_id>."""
    body = {"fields": {
        "json": {"stringValue": json.dumps(payload)},
        "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                      .isoformat().replace("+00:00", "Z")},
    }}
    req = urllib.request.Request(
        f"{FS}/{PARENT}/cache/{doc_id}",
        data=json.dumps(body).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    _send_write(req)


# ─── Per-game reveal ─────────────────────────────────────────────────────
#
# Picks lock and reveal one game at a time, like a fantasy lineup. Firestore
# rules are per-DOCUMENT, so there is no way to hand a groupmate half of
# someone's lineup — "show my Thursday pick but not my Saturday picks"
# cannot be expressed there.
#
# So the reveal is published here instead. With admin rights, this reads
# every member's lineup, keeps only the picks whose games have already
# kicked off, and writes one board per group per week that the group may
# read. Lineups stay private to their owner. Clients can never write a
# board, so nobody can reveal or fake a pick that has not started.
#
# The board also carries a per-member COUNT of filled slots. The count is
# not reveal-gated, because a number is not a pick: it lets the group see
# who still has a lineup to finish, and lets the app show an honest "picks
# made" before kickoff — which reading the board alone cannot, since the
# board holds only picks whose games have started.
#
# It needs no CFBD data at all — every pick carries its own kickoff — which
# is why main() writes boards BEFORE touching CFBD. A quota outage must
# never stop picks revealing.


def fs_get(token, path):
    req = urllib.request.Request(f"{FS}/{PARENT}/{path}",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fs_list(token, collection):
    docs, page = [], None
    while True:
        url = f"{FS}/{PARENT}/{collection}?pageSize=300"
        if page:
            url += f"&pageToken={page}"
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}"})
        body = json.load(urllib.request.urlopen(req, timeout=20))
        docs.extend(body.get("documents", []))
        page = body.get("nextPageToken")
        if not page:
            return docs


def slots_of(lineup_doc):
    """The raw slot map off a lineup document, or {} if there isn't one."""
    return (lineup_doc or {}).get("fields", {}).get(
        "slots", {}).get("mapValue", {}).get("fields", {})


def filled_count(lineup_doc):
    """How many of the seven slots this member has filled.

    A COUNT, never content — it says someone has picked five games, not
    which five. That distinction is the whole reason it can be published:
    it tells a group who still has a lineup to finish without revealing
    anything a pick reveal would.
    """
    return len(slots_of(lineup_doc))


def started_picks(lineup_doc, now):
    """Only the picks whose games have kicked off, as plain values."""
    slots = slots_of(lineup_doc)
    out = {}
    for name, value in slots.items():
        fields = value.get("mapValue", {}).get("fields", {})
        stamped = fields.get("kickoffAt", {}).get("timestampValue")
        if not stamped:
            continue  # no kickoff recorded — stays private, the safe way
        kickoff = dt.datetime.fromisoformat(stamped.replace("Z", "+00:00"))
        if now < kickoff:
            continue
        out[name] = {
            "gameId": fields.get("gameId", {}).get("stringValue", ""),
            "team": fields.get("team", {}).get("stringValue", ""),
            "kickoffAt": kickoff.isoformat().replace("+00:00", "Z"),
        }
    return out


def write_boards(token, season, week):
    now = dt.datetime.now(dt.timezone.utc)
    lineups = {}  # uid -> doc, so someone in two groups is read once
    written = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        members = [
            v.get("stringValue")
            for v in group.get("fields", {}).get("memberUids", {})
            .get("arrayValue", {}).get("values", [])
        ]

        board = {}
        counts = {}
        for uid in members:
            if not uid:
                continue
            if uid not in lineups:
                lineups[uid] = fs_get(
                    token, f"users/{uid}/lineups/{season}_{week}")
            picks = started_picks(lineups[uid], now)
            if picks:
                board[uid] = picks
            counts[uid] = filled_count(lineups[uid])

        body = {"fields": {
            "json": {"stringValue": json.dumps(board)},
            # Written for EVERY member, including those with an empty
            # lineup, so a missing uid means "not in this group that week"
            # rather than "hasn't picked". The screens tell those apart.
            "counts": {"stringValue": json.dumps(counts)},
            "season": {"integerValue": str(season)},
            "week": {"integerValue": str(week)},
            "updatedAt": {"timestampValue":
                          now.isoformat().replace("+00:00", "Z")},
        }}
        req = urllib.request.Request(
            f"{FS}/{PARENT}/groups/{gid}/board/{season}_{week}",
            data=json.dumps(body).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        _send_write(req)
        written += 1

    return written, len(lineups)


# ─── Notifications ───────────────────────────────────────────────────────
#
# Sent from here rather than a Cloud Function: this already runs every 60s
# during games, already holds admin credentials, and already knows kickoffs
# and finals. See notify.py for the reasoning and the de-dupe scheme.
#
# The rule every send obeys: this loop runs ~300 times a shift, so anything
# phrased as "send while X is true" sends 300 times. Each notification is
# recorded under a key naming the EVENT, and the record is checked first.


def _fs_patch(token, path, fields):
    req = urllib.request.Request(
        f"{FS}/{PARENT}/{path}",
        data=json.dumps({"fields": fields}).encode(), method="PATCH",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    _send_write(req)


def notify_finals(token, project, season, week, slate):
    """"Your pick is final" — once per person per game, ever.

    [slate] is build_slate output, NOT raw CFBD games: it carries the
    spread, and without the spread there is no telling whether the pick
    covered. Saying "final" when you mean "you won" is the sort of thing
    that gets a notification muted forever.

    Fires off the board rather than off lineups, so it can only ever
    mention a pick that has already been revealed to the group. A
    notification is a side channel; it must not say anything the app
    itself would not show you yet.
    """
    import notify

    final_ids = {g["id"] for g in slate if g["status"] == "final"}
    if not final_ids:
        return 0

    by_id = {g["id"]: g for g in slate}
    sent = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/board/{season}_{week}")
        raw = (doc or {}).get("fields", {}).get("json", {}).get("stringValue")
        if not raw:
            continue

        for uid, picks in json.loads(raw).items():
            for slot, pick in picks.items():
                gid_ = str(pick.get("gameId"))
                if gid_ not in final_ids:
                    continue

                key = notify.dedupe_key("final", uid, gid_)
                if notify.already_sent(
                        lambda p: fs_get(token, p), key):
                    continue

                from scoring import did_cover
                game = by_id[gid_]
                team = pick.get("team", "your pick")
                covered = did_cover(game, team)
                title = "Final" if covered is None else (
                    f"{team} covered ✅" if covered else f"{team} missed ❌")
                body = (f"{game['awayTeam']} {game['awayScore']} — "
                        f"{game['homeTeam']} {game['homeScore']}")

                for dev, _ in notify.devices_for(
                        lambda p: fs_list(token, p), uid):
                    notify.send_to_token(token, project, dev, title, body,
                                         route="/gameday")
                notify.record_sent(
                    lambda p, f: _fs_patch(token, p, f), key, "final", uid)
                sent += 1
    return sent


def notify_lineup_reminders(token, project, season, week, slate, now):
    """"Your lineup isn't finished" — once per person per week.

    Only in the last few hours before the week's first kickoff. Earlier is
    nagging; afterwards it is pointless, because the games they still have
    to fill are the ones already under way.
    """
    import notify

    kickoffs = [g["startDate"] for g in slate if g.get("startDate")]
    if not kickoffs:
        return 0
    first = dt.datetime.fromisoformat(min(kickoffs).replace("Z", "+00:00"))
    hours_out = (first - now).total_seconds() / 3600
    if not (0 < hours_out <= REMINDER_HOURS):
        return 0

    sent = 0
    reminded = set()
    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/board/{season}_{week}")
        raw = (doc or {}).get("fields", {}).get("counts", {}) \
                         .get("stringValue")
        if not raw:
            continue

        for uid, made in json.loads(raw).items():
            if made >= 7 or uid in reminded:
                continue
            reminded.add(uid)

            key = notify.dedupe_key("reminder", uid, f"{season}_{week}")
            if notify.already_sent(lambda p: fs_get(token, p), key):
                continue

            left = 7 - int(made)
            body = (f"{left} slot{'s' if left != 1 else ''} still empty, and "
                    f"the first game kicks off soon.")
            for dev, _ in notify.devices_for(
                    lambda p: fs_list(token, p), uid):
                notify.send_to_token(token, project, dev,
                                     "Finish your lineup", body,
                                     route="/lineup")
            notify.record_sent(
                lambda p, f: _fs_patch(token, p, f), key, "reminder", uid)
            sent += 1
    return sent


def notify_last_chance(token, project, season, week, slate, now):
    """"Your first pick locks in 25 minutes" — once per person per week.

    The highest-intent notification this app can send. The existing reminder
    only fires for UNFINISHED lineups, so somebody who filled all seven days
    ago hears nothing at all — yet the last half hour before their earliest
    pick freezes is exactly when a person wants to change their mind, and
    after it they cannot.

    Their deadline is the earliest kickoff among their OWN picks, not the
    slate's first game. Since locking went per-game those are frequently
    different, and a Thursday night game somebody did not pick takes nothing
    away from them. This is `nextPickLockAt` in lib/features/home/domain/
    next_lock.dart, in Python — if you change how that moment is chosen,
    change it here too.

    DEDUPED BY EVENT KEY, per person per week. The live loop runs ~300 times
    a shift, so anything phrased as "send while X is true" sends 300 times.
    """
    import notify

    # Cheap gate first. A person's first lock is always AT some game's
    # kickoff, so if nothing on the slate starts inside the window then
    # nobody's window is open and there is no reason to read any lineups.
    window = dt.timedelta(minutes=LAST_CHANCE_MINUTES)
    starting_soon = False
    for game in slate:
        raw = game.get("startDate")
        if not raw:
            continue
        start = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if now < start <= now + window:
            starting_soon = True
            break
    if not starting_soon:
        return 0

    sent = 0
    seen = set()
    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/board/{season}_{week}")
        raw = (doc or {}).get("fields", {}).get("counts", {}) \
                         .get("stringValue")
        if not raw:
            continue

        for uid in json.loads(raw):
            if uid in seen:
                continue
            seen.add(uid)

            key = notify.dedupe_key("lastchance", uid, f"{season}_{week}")
            if notify.already_sent(lambda p: fs_get(token, p), key):
                continue

            lock_at = _first_pick_lock(
                slots_of(fs_get(token, f"users/{uid}/lineups/{season}_{week}")),
                now,
            )
            if lock_at is None or not (now < lock_at <= now + window):
                continue

            minutes = max(1, int((lock_at - now).total_seconds() // 60))
            body = (f"Your first pick locks in {minutes} "
                    f"minute{'s' if minutes != 1 else ''}. Last chance to "
                    f"change it.")
            for dev, _ in notify.devices_for(
                    lambda p: fs_list(token, p), uid):
                notify.send_to_token(token, project, dev,
                                     "Last chance", body, route="/lineup")
            notify.record_sent(
                lambda p, f: _fs_patch(token, p, f), key, "lastchance", uid)
            sent += 1
    return sent


def _first_pick_lock(slots, now):
    """Earliest kickoff among picks that have not started, or None.

    Mirrors nextPickLockAt in Dart. A slot with no recorded kickoff is
    skipped rather than guessed at — backfill_kickoffs.py exists for those,
    and inventing a deadline is worse than staying quiet about one.
    """
    soonest = None
    for value in (slots or {}).values():
        fields = value.get("mapValue", {}).get("fields", {})
        raw = fields.get("kickoffAt", {}).get("timestampValue")
        if not raw:
            continue
        kickoff = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if kickoff <= now:
            continue
        if soonest is None or kickoff < soonest:
            soonest = kickoff
    return soonest


def notify_reactions(token, project, season, week, now):
    """"Eric reacted to your picks" — batched, and deliberately vague.

    **It never says WHAT was said.** That is the point, not an oversight: the
    whole value of the reaction is being talked about, and a notification that
    quotes the joke is a notification nobody needs to act on. Withholding it
    turns the message into a reason to open the app, which is the only place
    the jokes live.

    **Batched per person, not per tap.** Seven picks times three groupmates is
    potentially twenty-one notifications about jokes on one Saturday, which is
    how an app gets muted. One message per person per REACTION_BATCH_MINUTES,
    naming who reacted and nothing else.

    **Only NEW reactors count.** Everything already announced is remembered in
    groups/{gid}/reactionNotify/{season}_{week}, so a reaction that has been
    reported once never counts again — including one that is taken back and
    put on a second time.
    """
    import notify

    sent = 0
    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/reactions/{season}_{week}")
        if not doc:
            continue

        # targetUid -> set of "reactor|gameId" currently on their picks.
        current = {}
        for key, value in (doc.get("fields") or {}).items():
            if "_" not in key:
                continue
            target_uid, _, game_id = key.partition("_")
            for reactor in (value.get("mapValue", {})
                                 .get("fields", {}) or {}):
                if reactor == target_uid:
                    continue  # cannot happen through the app; belt and braces
                current.setdefault(target_uid, set()).add(f"{reactor}|{game_id}")
        if not current:
            continue

        state_path = f"groups/{gid}/reactionNotify/{season}_{week}"
        state = (fs_get(token, state_path) or {}).get("fields", {})

        updates = {}
        for target_uid, seen_now in current.items():
            prior = state.get(target_uid, {}).get("mapValue", {}) \
                         .get("fields", {})
            announced = {
                v.get("stringValue")
                for v in (prior.get("announced", {}).get("arrayValue", {})
                               .get("values") or [])
            }
            fresh = seen_now - announced
            if not fresh:
                continue

            # Hold off until the batch window has passed, so a flurry during
            # one game becomes one message rather than five.
            last_raw = prior.get("lastSentAt", {}).get("timestampValue")
            if last_raw:
                last = dt.datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
                if (now - last).total_seconds() < REACTION_BATCH_MINUTES * 60:
                    continue

            reactors = sorted({item.split("|")[0] for item in fresh})
            body = _reaction_body(token, reactors)
            for dev, _ in notify.devices_for(
                    lambda p: fs_list(token, p), target_uid):
                notify.send_to_token(token, project, dev,
                                     "Someone's talking", body,
                                     route="/gameday")
            sent += 1

            updates[target_uid] = {"mapValue": {"fields": {
                "announced": {"arrayValue": {"values": [
                    {"stringValue": item} for item in sorted(seen_now)
                ]}},
                "lastSentAt": {"timestampValue":
                               now.isoformat().replace("+00:00", "Z")},
            }}}

        if updates:
            _fs_patch(token, state_path, updates)
    return sent


def _reaction_body(token, reactor_uids):
    """Looks up first names, then hands off to the pure wording below."""
    names = []
    for uid in reactor_uids[:2]:
        doc = fs_get(token, f"users/{uid}")
        fields = (doc or {}).get("fields", {})
        name = (fields.get("username", {}).get("stringValue")
                or fields.get("displayName", {}).get("stringValue")
                or "Someone")
        names.append(name.split(" ")[0])
    return reaction_body(names, len(reactor_uids))


def reaction_body(names, total):
    """"Eric and Kade reacted to your picks" - and never what they said.

    Pure, so the plural is covered by a test. "1 others" is exactly the sort
    of thing that survives a visual check and reads as broken.

    [names] is at most the first two; [total] is how many people actually
    reacted, so the overflow is counted without looking up every name.

    NEVER names the reaction itself. The only way to find out what was said
    is to open the app - see notify_reactions.
    """
    shown = list(names[:2])
    extra = total - len(shown)

    if not shown:
        who = "Someone"
    elif extra > 0:
        who = f"{', '.join(shown)} and {extra} other{'s' if extra > 1 else ''}"
    elif len(shown) == 2:
        who = f"{shown[0]} and {shown[1]}"
    else:
        who = shown[0]

    return f"{who} reacted to your picks. Open Game Day to see what they said."


def notify_line_moves(token, project, season, week, slate, now):
    """"The line on BYU moved to -8.5" - the midweek reason to open the app.

    This app has a dead zone between Sunday and Thursday: nothing happens, so
    nobody looks. A line moving on a pick somebody already made is the one
    genuinely useful thing that happens in that window, and it is specific to
    betting against a spread rather than picking winners.

    Reads lineSnapshots, which the app writes on every save - a deduplicated
    history of the line each pick was made against. See savePicks in
    lineup_repository.dart for why it is an arrayUnion.

    ONLY BEFORE KICKOFF. Once a game starts the pick is frozen and the line is
    academic; telling somebody their number moved when they can do nothing
    about it is noise dressed as information.

    Deduped per person per game per line value, so a line that moves twice
    sends twice but a tick that sees the same move again sends nothing.
    """
    import notify

    by_id = {g["id"]: g for g in slate}
    sent = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/board/{season}_{week}")
        raw = (doc or {}).get("fields", {}).get("counts", {}) \
                         .get("stringValue")
        if not raw:
            continue

        for uid in json.loads(raw):
            lineup = fs_get(token, f"users/{uid}/lineups/{season}_{week}")
            snaps = (lineup or {}).get("fields", {}).get("lineSnapshots", {}) \
                                  .get("arrayValue", {}).get("values", [])
            if not snaps:
                continue

            # Last line this person actually saw for each game.
            seen = {}
            for entry in snaps:
                f = entry.get("mapValue", {}).get("fields", {})
                game_id = f.get("gameId", {}).get("stringValue")
                team = f.get("team", {}).get("stringValue")
                spread = f.get("spread", {}).get("doubleValue")
                if spread is None:
                    spread = f.get("spread", {}).get("integerValue")
                if game_id is None or spread is None:
                    continue
                seen[game_id] = (team, float(spread))

            for game_id, (team, old_line) in seen.items():
                game = by_id.get(game_id)
                if not game or game.get("spread") is None:
                    continue

                # Frozen picks cannot be changed, so the move is academic.
                start = game.get("startDate")
                if start:
                    kickoff = dt.datetime.fromisoformat(
                        start.replace("Z", "+00:00"))
                    if kickoff <= now:
                        continue

                new_line = float(game["spread"])
                if abs(new_line - old_line) < LINE_MOVE_MIN:
                    continue

                key = notify.dedupe_key(
                    "linemove", uid, f"{season}_{week}_{game_id}_{new_line}")
                if notify.already_sent(lambda p: fs_get(token, p), key):
                    continue

                body = (f"The line on {team} moved from "
                        f"{_line_str(old_line)} to {_line_str(new_line)}. "
                        f"Still happy with it?")
                for dev, _ in notify.devices_for(
                        lambda p: fs_list(token, p), uid):
                    notify.send_to_token(token, project, dev,
                                         "Line moved", body, route="/lineup")
                notify.record_sent(
                    lambda p, f: _fs_patch(token, p, f), key, "linemove", uid)
                sent += 1
    return sent


def _line_str(value):
    """-7.5 rather than -7.5000001, +3 rather than 3, and PK rather than +0.

    PK is what a zero spread is actually called; "+0" reads like a bug.
    """
    if abs(value) < 0.05:
        return "PK"
    return f"{value:+.1f}".rstrip("0").rstrip(".")


def notify_kickoffs(token, project, season, week, slate, now):
    """"Kickoff - 3 of you took Colorado."

    The moment this app is built around, pushed rather than only shown. Every
    other pick'em freezes a whole week at once; here each pick reveals at its
    own kickoff, and that has always happened silently.

    ONE MESSAGE PER GROUP PER GAME, not per person per pick. A game with four
    pickers in a group is one event, not four - and the same game across two
    groups is two different splits, so those are two messages.

    Sent only to people IN that group who picked that game: a reveal is only
    interesting if you have something riding on it.

    Deduped by group and game, so the 60-second loop cannot repeat it.
    Mirrors kickoffReveals in lib/features/leaderboard/domain/kickoff_reveal.
    dart - change how the split is described and change it in both.
    """
    import notify

    window = dt.timedelta(minutes=KICKOFF_WINDOW_MINUTES)
    just_started = []
    for game in slate:
        raw = game.get("startDate")
        if not raw:
            continue
        start = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if now - window <= start <= now and game.get("status") != "final":
            just_started.append((game, start))
    if not just_started:
        return 0

    sent = 0
    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        board = fs_get(token, f"groups/{gid}/board/{season}_{week}")
        # The revealed picks live under "json", NOT "picksJson" — checked
        # against write_boards rather than assumed. A wrong field name here
        # would fail silently and send nothing, forever.
        raw = (board or {}).get("fields", {}).get("json", {}) \
                           .get("stringValue")
        if not raw:
            continue
        try:
            # uid -> {slotName: {gameId, team, kickoffAt}} — see started_picks.
            picks = json.loads(raw)
        except ValueError:
            continue

        for game, _ in just_started:
            game_id = str(game.get("id"))

            # uid -> team, for everyone in this group who took this game.
            took = {}
            for uid, slots in picks.items():
                for entry in (slots or {}).values():
                    if str(entry.get("gameId")) == game_id:
                        took[uid] = entry.get("team")
            if not took:
                continue

            key = notify.dedupe_key("kickoff", gid, f"{season}_{week}_{game_id}")
            if notify.already_sent(lambda p: fs_get(token, p), key):
                continue

            by_team = {}
            for team in took.values():
                by_team[team] = by_team.get(team, 0) + 1
            body = kickoff_body(by_team)

            title = f"{game.get('awayTeam')} @ {game.get('homeTeam')}"
            for uid in took:
                for dev, _ in notify.devices_for(
                        lambda p: fs_list(token, p), uid):
                    notify.send_to_token(token, project, dev, title, body,
                                         route="/gameday")
            notify.record_sent(
                lambda p, f: _fs_patch(token, p, f), key, "kickoff", gid)
            sent += 1
    return sent


def kickoff_body(by_team):
    """"All 3 of you took Colorado", or "3 took Colorado, 1 took Miami".

    Pure, so the plural is covered by a test - and it has to agree with
    revealSummary in Dart, because the same person can read the notification
    and then open the app to the banner saying the same thing.
    """
    entries = sorted(by_team.items(), key=lambda kv: -kv[1])
    if not entries:
        return "Kickoff."
    if len(entries) == 1:
        team, n = entries[0]
        return (f"1 of you took {team}." if n == 1
                else f"All {n} of you took {team}.")
    return ", ".join(f"{n} took {team}" for team, n in entries) + "."


# How soon after a game starts the kickoff notification is still worth
# sending. Short: this is a moment, and a push about a game that started half
# an hour ago is just noise.
KICKOFF_WINDOW_MINUTES = 6


# How far a line has to move before it is worth a notification. Half a point
# is noise; a full point can change whether a pick is worth keeping.
LINE_MOVE_MIN = 1.0


# One message per person per this many minutes. Long enough that a flurry
# during one game is a single notification; short enough that it still feels
# like it happened just now.
REACTION_BATCH_MINUTES = 15


# How close to somebody's OWN first kickoff the last-chance nudge fires.
# Short on purpose: it exists to catch a change of mind, and a warning two
# hours out is just another reminder.
LAST_CHANCE_MINUTES = 30


# How long before the week's first kickoff a "finish your lineup" nudge is
# still useful rather than nagging.
REMINDER_HOURS = 6


def _ranks(totals):
    """uid -> standings position, ties sharing a place.

    COMPETITION ranking (1, 1, 3 — not 1, 1, 2), matching `rankAt` in
    lib/core/utils/ranking.dart. This has to agree with the app exactly:
    a notification saying "now 2nd" against a screen saying "3rd" is worse
    than sending nothing at all.

    Ties matter for a second reason here — two people level on points have
    not overtaken each other, so neither should be told they moved.
    """
    return {
        uid: 1 + sum(1 for other in totals.values()
                     if other["points"] > t["points"])
        for uid, t in totals.items()
    }


def notify_rank_changes(token, project, season, week, gid, old, new,
                        group_name):
    """"You moved in the standings" — once per person per group per week.

    Only sent to people who CHANGED POSITION. Points moving is not news;
    everyone's points move every Saturday. Overtaking somebody is.
    """
    import notify

    shared = set(old) & set(new)
    if len(shared) < 2:
        return 0                      # a group of one has no standings

    before = _ranks({u: old[u] for u in shared})
    after = _ranks({u: new[u] for u in shared})

    sent = 0
    for uid in shared:
        moved = before[uid] - after[uid]      # positive = moved up
        if moved == 0:
            continue

        # Keyed on the week, not the position, so the same week's shuffling
        # can't notify somebody twice as later games land.
        key = notify.dedupe_key("rank", uid, f"{gid}_{season}_{week}")
        if notify.already_sent(lambda p: fs_get(token, p), key):
            continue

        title = ("You moved up 📈" if moved > 0 else "You slipped 📉")
        body = (f"{abs(moved)} place{'s' if abs(moved) != 1 else ''} "
                f"{'up' if moved > 0 else 'down'} in {group_name} — "
                f"now {after[uid]} of {len(shared)}.")
        for dev, _ in notify.devices_for(lambda p: fs_list(token, p), uid):
            notify.send_to_token(token, project, dev, title, body,
                                 route="/leaderboard")
        notify.record_sent(
            lambda p, f: _fs_patch(token, p, f), key, "rank", uid)
        sent += 1
    return sent


def deliver_nudges(token, project, season, week):
    """Send the reminders a COMMISSIONER asked for.

    The app cannot send a push itself -- that needs a server key, and a key
    inside an app binary is a key anybody can extract. So a commissioner's
    tap writes groups/{gid}/nudges/{season}_{week}, and this delivers it on
    the next run: inside a minute during a live shift, longer on a quiet
    weekday, which is why the button promises "shortly" and not "now".

    `sentAt` on the request is the de-dupe, in the same spirit as the rest
    of this file -- the marker names the EVENT, not the moment. The security
    rules let a client CREATE that document and never update or delete it,
    so a nudge cannot be replayed by anybody but this function.
    """
    sent = 0
    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        doc = fs_get(token, f"groups/{gid}/nudges/{season}_{week}")
        if doc is None:
            continue
        fields = doc.get("fields", {})
        # Checked every run, and this loop runs every 60 seconds during a
        # shift -- without it one nudge would be re-sent three hundred times.
        if fields.get("sentAt", {}).get("timestampValue"):
            continue

        uids = [v.get("stringValue") for v in
                fields.get("uids", {}).get("arrayValue", {}).get("values", [])]
        name = group.get("fields", {}).get("name", {}) \
                    .get("stringValue", "your group")

        for uid in uids:
            if not uid:
                continue
            for dev, _ in notify.devices_for(lambda p: fs_list(token, p), uid):
                notify.send_to_token(
                    token, project, dev,
                    "Your lineup isn't finished",
                    f"The commissioner of {name} is waiting on your picks.",
                    route="/lineup")
            sent += 1

        # Stamped even when nobody was reachable. The request HAS been
        # handled, and leaving it pending would retry the same empty send on
        # every tick for the rest of the week.
        _fs_patch(token, f"groups/{gid}/nudges/{season}_{week}", {
            "sentAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                       .isoformat().replace("+00:00", "Z")},
        })
    return sent


# ─── Which notifications are switched on ─────────────────────────────────
#
# A notification path that has never delivered to a human is not a feature,
# it is a hypothesis. Line moves and kickoffs were built on Aug 16 and both
# would otherwise debut on opening Saturday alongside two other first-time
# push paths, on a scheduler that is mid-migration.
#
# **They cannot be held by simply not pushing this file.** Every notifier
# lives in one list that live_refresh.py drives through send_notifications,
# so pushing update_cache.py at all — for reactions, or for anything else —
# used to turn these two on with it. That is what this switch is for: it
# decouples "the scheduler is current" from "these two are live".
#
# OFF unless the environment says otherwise, and named per path so they can
# be turned on one at a time, on different weekends. Set in the workflow's
# env block (or Cloud Run's) to enable:
#
#     NOTIFY_LINE_MOVES: '1'
#     NOTIFY_KICKOFFS: '1'
#
# Nothing else is gated. Finals, reminders, last chance, reactions, nudges
# and Live Activities are all either proven against real people or already
# running, and adding switches to those would only create a way to turn off
# something that works.
GATED_NOTIFICATIONS = {
    "line moves": "NOTIFY_LINE_MOVES",
    "kickoffs": "NOTIFY_KICKOFFS",
}

# Which switches this process has already reported. A live shift calls
# send_notifications ~300 times, so an unconditional line would print 600
# times a Saturday and teach everyone to skim the log — and the log is where
# you look when a notification did not arrive.
_reported_flags = set()


def notification_enabled(label, env=None):
    """Whether a gated notifier should run. Ungated ones always do.

    Split out and given an [env] seam so the rule is testable without
    setting process environment variables.
    """
    flag = GATED_NOTIFICATIONS.get(label)
    if flag is None:
        return True
    source = os.environ if env is None else env
    return source.get(flag) == "1"


def send_notifications(token, season, week, slate):
    """Every notification for this tick. Never raises.

    Wrapped whole, because notifications are the least important thing this
    job does. Scores being fresh is what people actually depend on; a
    failed send must not cost them that.
    """
    total = 0
    now = dt.datetime.now(dt.timezone.utc)
    for label, fn in (
        ("finals", lambda: notify_finals(token, PROJECT, season, week, slate)),
        ("reminders", lambda: notify_lineup_reminders(
            token, PROJECT, season, week, slate, now)),
        ("last chance", lambda: notify_last_chance(
            token, PROJECT, season, week, slate, now)),
        ("reactions", lambda: notify_reactions(
            token, PROJECT, season, week, now)),
        ("line moves", lambda: notify_line_moves(
            token, PROJECT, season, week, slate, now)),
        ("kickoffs", lambda: notify_kickoffs(
            token, PROJECT, season, week, slate, now)),
        ("nudges", lambda: deliver_nudges(token, PROJECT, season, week)),
        # Last, and inside the same wrapper: a lock-screen decoration must
        # never be able to cost the scores their refresh.
        ("live activities", lambda: push_live_activities(
            token, season, week, slate, now)),
    ):
        if not notification_enabled(label):
            # Said once per process, not once per tick. A switch nobody can
            # see is indistinguishable from a bug, which is the shape of the
            # July outage: something exited 0 and stayed quiet for nine days.
            if label not in _reported_flags:
                _reported_flags.add(label)
                print(f"  {label} notifications OFF "
                      f"({GATED_NOTIFICATIONS[label]} is not '1')")
            continue
        try:
            total += fn()
        except Exception as e:
            print(f"  {label} notifications skipped: {e}")
    return total


# ─── Season standings ────────────────────────────────────────────────────
#
# Clients used to total the season themselves: for every week played, for
# every member, fetch that lineup and score it. weeks × members reads per
# view, and the Home screen did it for every group you belong to. Cheap in
# week 1, ~180 reads per Home open by November, and it got worse every week.
#
# This does the arithmetic once and writes the answer, so a client reads one
# document. Same shape as the reveal board.
#
# Scoring lives in scoring.py, which is a SECOND COPY of rules that also
# exist in Dart — see the warning at the top of that file, and the parity
# fixture that keeps the two honest.
#
# Deliberately NOT called from live_refresh.py's per-tick loop: totals move
# only when a game goes final, so recomputing every 60 seconds would cost
# members × weeks reads a minute to produce the same answer.


def fs_query_group(token, collection_id):
    """Every document in a collection, wherever it sits under a user.

    One request instead of a document read per user per tick. Finding the
    handful of live activity tokens the naive way would be ~9,000 reads
    across a 5.5 hour Saturday at 28 users.
    """
    body = {"structuredQuery": {
        "from": [{"collectionId": collection_id, "allDescendants": True}]}}
    req = urllib.request.Request(
        f"{FS}/{PARENT}:runQuery", data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    rows = json.load(urllib.request.urlopen(req, timeout=20))
    return [r["document"] for r in rows if "document" in r]


def fs_delete(token, path):
    req = urllib.request.Request(
        f"{FS}/{PARENT}/{path}", method="DELETE",
        headers={"Authorization": f"Bearer {token}"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise


def push_live_activities(token, season, week, slate, now):
    """Drive everyone's lock-screen card. Returns how many were pushed.

    THIS IS WHAT MAKES THE FEATURE WORTH HAVING. Without it a Live Activity
    only moves while the app is open, which is exactly when nobody needs
    one — the whole value is a lock screen that keeps up while the game is
    on television and the phone is face down.

    Reads LINEUPS rather than the group board, which is the opposite of
    every other notification here and is correct: a card is only ever shown
    to its own owner, so there is nothing to reveal. The board deliberately
    withholds picks that have not kicked off, and a score built from it
    would be missing the very games still to come.

    Never raises. A lock-screen decoration must not be able to cost the
    scores their refresh — the same wrapper the other notifications get.
    """
    import live_activity
    from scoring import SLOT_POINTS, did_cover

    p8 = os.environ.get("APNS_KEY_P8", "").replace("\\n", "\n").strip()
    if not p8:
        return 0  # not configured yet — see HANDOFF.md

    try:
        rows = fs_query_group(token, "liveActivities")
    except Exception as e:
        print(f"live activities: could not list tokens ({e})")
        return 0

    by_id = {g["id"]: g for g in slate}
    pushed = 0

    for doc in rows:
        try:
            fields = doc.get("fields", {})
            if int(fields.get("season", {}).get("integerValue", 0)) != season:
                continue
            if int(fields.get("week", {}).get("integerValue", 0)) != week:
                continue
            device = fields.get("token", {}).get("stringValue")
            if not device:
                continue

            # .../documents/users/{uid}/liveActivities/{weekId}
            parts = doc["name"].split("/")
            uid = parts[parts.index("users") + 1]

            slots = slots_of(fs_get(token, f"users/{uid}/lineups/{season}_{week}"))
            if not slots:
                continue

            won = lost = playing = to_come = 0
            for slot, value in slots.items():
                points = SLOT_POINTS.get(slot)
                f = value.get("mapValue", {}).get("fields", {})
                game = by_id.get(f.get("gameId", {}).get("stringValue", ""))
                if points is None or game is None:
                    continue
                if game["status"] == "final":
                    if did_cover(game, f.get("team", {}).get("stringValue")):
                        won += points
                    else:
                        # A push is not points in hand either, and counting
                        # it as lost is how the app's own tally reads.
                        lost += points
                elif game["status"] == "in_progress":
                    playing += 1
                else:
                    to_come += 1

            # The group the app pinned onto this card. Read from the same doc
            # as the token so the two can never disagree about which card is
            # which.
            group_id = fields.get("groupId", {}).get("stringValue")
            rank, size = _season_placing(token, season, uid, group_id)
            state = live_activity.build_content_state(
                won_points=won, lost_points=lost,
                picks_playing=playing, picks_to_come=to_come,
                # The scheduler cannot know the reader's chosen style, and
                # the app redraws the card whenever it is open. Plus/minus
                # is the default and the shape the card was designed for.
                style="plusMinus",
                rank=rank, group_size=size, now=now,
            )

            status, body = live_activity.send_update(device, state, p8)
            if live_activity.is_dead_token(status, body):
                # Swiped away, or the week ended. Retrying forever against a
                # dead address wastes an afternoon of pushes on nobody.
                fs_delete(token, f"users/{uid}/liveActivities/{season}_{week}")
            elif status == 200:
                pushed += 1
            else:
                print(f"live activity {uid}: {status} {body[:120]}")
        except Exception as e:
            print(f"live activity skipped for one user: {e}")

    return pushed


def _season_placing(token, season, uid, group_id=None):
    """(rank, group size) from the published standings, or (None, None).

    ALWAYS the group the app pinned when it started the card — never
    "whichever one they lead right now". A Live Activity's name is fixed for
    the card's entire life (ActivityKit attributes cannot be updated) while
    this rank is pushed afresh every minute, so picking the best-ranked group
    here would put "Andrus Crew" beside a rank in SF Substation half way
    through an afternoon. One lineup is shared across every group, so the
    points are identical everywhere and it is OTHER people's scores that
    reorder them: that drift is ordinary, not a corner case.

    pinnedOrBest on the Dart side is the other half of this agreement. If you
    change how a group is chosen, change it in both languages — the card is
    assembled from both and neither can see the other.

    No recorded group means NO placing. A gap is honest; a rank belonging to
    a league the card is not naming is not.

    Cheap: this is one document, written by the scheduler itself.
    """
    if not group_id:
        return (None, None)

    doc = fs_get(token, f"groups/{group_id}/standings/{season}")
    raw = (doc or {}).get("fields", {}).get("json", {}).get("stringValue")
    if not raw:
        return (None, None)
    try:
        rows = json.loads(raw)
    except ValueError:
        return (None, None)

    totals = [r.get("points", 0) for r in rows]
    for i, row in enumerate(rows):
        if row.get("uid") != uid:
            continue
        # Tie-aware, like ranksFor in Dart: level on points means level in the
        # standings, not ordered by whoever loaded first.
        return (sum(1 for t in totals if t > totals[i]) + 1, len(rows))
    return (None, None)


def write_season_standings(token, season, through_week):
    from scoring import build_slate, weekly_points

    # One slate per week, shared across every group.
    slates = {}
    # FROM ONE, and that is deliberate rather than left over. Week 0 —
    # Opening Week — is a PRACTICE week and is never charged to the season:
    # eight games, seven picks, so it is decided by stacking order and most
    # teams are not even on it. It is played for real and then not counted.
    # Mirrors countsTowardSeason in lib/core/utils/season_weeks.dart.
    for week in range(1, through_week + 1):
        doc = fs_get(token, f"cache/slate_{season}_{week}")
        if not doc:
            continue
        fields = doc.get("fields", {})
        games_raw = fields.get("gamesJson", {}).get("stringValue")
        lines_raw = fields.get("linesJson", {}).get("stringValue")
        if not games_raw:
            continue
        slates[week] = build_slate(
            json.loads(games_raw), json.loads(lines_raw or "[]"))

    if not slates:
        return 0, 0

    lineups = {}  # (uid, week) -> picks, so shared members are read once
    written = 0

    for group in fs_list(token, "groups"):
        gid = group["name"].rsplit("/", 1)[-1]
        members = [
            v.get("stringValue")
            for v in group.get("fields", {}).get("memberUids", {})
            .get("arrayValue", {}).get("values", [])
        ]

        totals = {}
        for uid in members:
            if not uid:
                continue
            points = picks_made = 0
            for week, games in slates.items():
                if (uid, week) not in lineups:
                    doc = fs_get(token, f"users/{uid}/lineups/{season}_{week}")
                    slots = (doc or {}).get("fields", {}).get(
                        "slots", {}).get("mapValue", {}).get("fields", {})
                    lineups[(uid, week)] = {
                        name: {
                            "gameId": v.get("mapValue", {}).get("fields", {})
                                       .get("gameId", {}).get("stringValue"),
                            "team": v.get("mapValue", {}).get("fields", {})
                                     .get("team", {}).get("stringValue"),
                        }
                        for name, v in slots.items()
                    }
                picks = lineups[(uid, week)]
                points += weekly_points(picks, games)
                picks_made += len(picks)
            totals[uid] = {"points": points, "picksMade": picks_made}

        # Read the standings we're about to replace, so we can tell who
        # actually moved. Done before the write, obviously, and treated as
        # optional — never let a notification stop the standings updating.
        try:
            previous = fs_get(token, f"groups/{gid}/standings/{season}")
            old_raw = (previous or {}).get("fields", {}) \
                .get("json", {}).get("stringValue")
            if old_raw:
                notify_rank_changes(
                    token, PROJECT, season, through_week, gid,
                    json.loads(old_raw), totals,
                    (group.get("fields", {}).get("name", {})
                     .get("stringValue") or "your group"))
        except Exception as e:
            print(f"  rank notifications skipped: {e}")

        body = {"fields": {
            "json": {"stringValue": json.dumps(totals)},
            "season": {"integerValue": str(season)},
            "throughWeek": {"integerValue": str(through_week)},
            "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                          .isoformat().replace("+00:00", "Z")},
        }}
        req = urllib.request.Request(
            f"{FS}/{PARENT}/groups/{gid}/standings/{season}",
            data=json.dumps(body).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        _send_write(req)
        written += 1

    return written, len(slates)


def main():
    key = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    cfbd = os.environ["CFBD_API_KEY"]
    token = access_token(key)

    # Bail out before spending a single CFBD call if the cache is already
    # fresh enough for what day it is. FORCE_REFRESH=1 overrides, so a
    # manual run from the Actions tab always does something.
    age, kickoff_in = read_state(token)

    # How close is football? Between seasons the weekday means nothing —
    # a Saturday in July is not a game day, and refreshing every 5 minutes
    # for games three weeks out is how the August quota disappeared.
    if kickoff_in is not None and kickoff_in > QUIET_IF_KICKOFF_BEYOND:
        max_age = QUIET_MAX_AGE
        why = f"next kickoff is {kickoff_in / 24:.1f} days away"
    else:
        weekday = dt.datetime.now(dt.timezone.utc).weekday()
        max_age = MAX_AGE_BY_WEEKDAY[weekday]
        why = "football is close"

    if (os.environ.get("FORCE_REFRESH") != "1"
            and age is not None and age < max_age):
        print(f"cache refreshed {age:.1f} min ago; threshold is "
              f"{max_age} min ({why}) — skipping, no CFBD calls")
        return

    # Claim the slot before spending ANYTHING. current_week hits /calendar,
    # so this has to come first or that one call escapes the throttle. If
    # anything below fails — quota, network — the next wake-up still waits a
    # full interval instead of hammering CFBD every five minutes.
    record_run(token)

    season = current_season()
    week = current_week(cfbd, season)

    # Boards before the remaining CFBD calls: revealing a pick needs no
    # CFBD data, so running out of quota must never stop picks revealing
    # at kickoff.
    # Boards for every week people can currently have picks in. While the
    # split is off that is both 0 and 1: builds with the split save
    # preseason picks under week 0, and a pick that never reveals at
    # kickoff is a real bug rather than a cosmetic one. Boards cost
    # Firestore writes and no CFBD calls.
    board_weeks = [week] if opening_week_split_on() else sorted({0, week})
    for board_week in board_weeks:
        try:
            boards, read = write_boards(token, season, board_week)
            print(f"wrote {boards} group board(s) for week {board_week} "
                  f"from {read} lineup(s)")
        except Exception as e:
            print(f"group boards for week {board_week} skipped: {e}")

    # ASK CFBD FOR ITS WEEK, NOT OURS. Weeks 0 and 1 are both cut out of
    # CFBD's week 1, so both are fetched with one pair of calls and split
    # afterwards — the split costs no extra quota.
    cfbd_wk = cfbd_week_for(week) if SPLIT_OPENING_WEEK else week
    try:
        games = cfbd_get("/games", cfbd, year=season, week=cfbd_wk,
                         seasonType="regular", division="fbs")
        lines = cfbd_get("/lines", cfbd, year=season, week=cfbd_wk,
                         seasonType="regular")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise QuotaExhausted() from e
        raise

    # Tell the next run how close football is, so it can skip CFBD entirely
    # when the answer is "weeks away". Costs one Firestore write.
    try:
        record_next_kickoff(token, games)
    except Exception as e:
        print(f"  next-kickoff marker skipped: {e}")

    # BOTH SLATES GET WRITTEN WHILE CFBD IS ON WEEK 1, not just the
    # current one. Week 0 finishes on a Monday night and week 1 begins
    # hours later; if only the current week were written, week 0's final
    # scores would depend on the last run landing inside that gap. Two
    # Firestore writes are cheaper than that risk, and cost no CFBD calls.
    split = opening_week_split_on()
    if cfbd_wk == 1:
        # Both slates either way. What differs is what week 1 CONTAINS:
        # split, it is Labor Day only; transitional, it stays the combined
        # slate every installed build is already reading.
        app_weeks = [0, 1]
    else:
        app_weeks = [week]
    for app_week in app_weeks:
        slice_games = (games_in_app_week(season, app_week, games)
                       if (split or app_week == 0) else games)
        body = {"fields": {
            "gamesJson": {"stringValue": json.dumps(slice_games)},
            "linesJson": {"stringValue": json.dumps(lines)},
            "season": {"integerValue": str(season)},
            "week": {"integerValue": str(app_week)},
            "updatedAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                          .isoformat().replace("+00:00", "Z")},
        }}
        req = urllib.request.Request(
            f"{FS}/{PARENT}/cache/slate_{season}_{app_week}",
            data=json.dumps(body).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"})
        _send_write(req)
        if len(app_weeks) > 1:
            print(f"  slate_{season}_{app_week}: {len(slice_games)} games")

    # The calendar (which week is it) and the AP poll used to be called
    # straight from every phone, on every session, which quietly made CFBD
    # usage scale with the number of players — the thing this cache exists
    # to prevent. Neither is worth failing the run over.
    try:
        write_json_cache(token, f"calendar_{season}",
                         cfbd_get("/calendar", cfbd, year=season))
        print(f"cached calendar_{season}")
    except Exception as e:
        print(f"calendar cache skipped: {e}")

    try:
        write_json_cache(
            token, f"rankings_{season}_{week}",
            cfbd_get("/rankings", cfbd, year=season, week=cfbd_wk,
                     seasonType="regular"))
        print(f"cached rankings_{season}_{week}")
    except Exception as e:
        print(f"rankings cache skipped: {e}")

    # Notifications before the expensive season totals, so a slow or
    # failing standings pass can't delay a "your game is final" by an hour.
    try:
        n = send_notifications(token, season, week,
                               build_slate(games, lines))
        if n:
            print(f"sent {n} notification(s)")
    except Exception as e:
        print(f"notifications skipped: {e}")

    # Season totals last: they read every member's lineup for every week
    # played, so they are the most expensive thing here and the least
    # urgent. Never let them sink a run that already refreshed the scores.
    try:
        groups, weeks = write_season_standings(token, season, week)
        print(f"wrote {groups} season standings doc(s) over {weeks} week(s)")
    except Exception as e:
        print(f"season standings skipped: {e}")

    print(f"cached {season} week {week}: {len(games)} games, {len(lines)} lines")
    dry_run_summary()


if __name__ == "__main__":
    try:
        main()
    except QuotaExhausted:
        # Exit 0 on purpose. Running out of monthly calls is expected and
        # self-correcting, and a red X every five minutes until the plan
        # resets would bury a real failure in noise. The app keeps serving
        # the cached slate meanwhile.
        print("CFBD monthly quota exhausted — leaving the existing cache in "
              "place. This clears when the plan resets; the app serves the "
              "cached slate until then.")
        sys.exit(0)
