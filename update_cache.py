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

PROJECT = "saturday-lineup"
CFBD = "https://api.collegefootballdata.com"
FS = "https://firestore.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/databases/(default)/documents"


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
                return int(w["week"])
        if regular:
            return int(regular[-1]["week"])
    except Exception:
        pass
    return min(max(1, (dt.datetime.now() - dt.datetime(season, 8, 24)).days
                   // 7 + 1), 15)


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
    urllib.request.urlopen(req, timeout=20).read()


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
    urllib.request.urlopen(req, timeout=20).read()


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
        urllib.request.urlopen(req, timeout=20).read()
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
    urllib.request.urlopen(req, timeout=20).read()


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
        ("nudges", lambda: deliver_nudges(token, PROJECT, season, week)),
    ):
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


def write_season_standings(token, season, through_week):
    from scoring import build_slate, weekly_points

    # One slate per week, shared across every group.
    slates = {}
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
        urllib.request.urlopen(req, timeout=20).read()
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
    try:
        boards, read = write_boards(token, season, week)
        print(f"wrote {boards} group board(s) from {read} lineup(s)")
    except Exception as e:
        print(f"group boards skipped: {e}")

    try:
        games = cfbd_get("/games", cfbd, year=season, week=week,
                         seasonType="regular", division="fbs")
        lines = cfbd_get("/lines", cfbd, year=season, week=week,
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
            cfbd_get("/rankings", cfbd, year=season, week=week,
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
