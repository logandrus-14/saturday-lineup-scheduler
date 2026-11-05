#!/usr/bin/env python3
"""The alerts must not read everything every five minutes.

WHY THIS EXISTS. On Nov 2 2026 the free day's 50,000 reads ran out by 3 PM on
a Monday with no game until night, and Bears-Seahawks dropped off the Board.
The alerts pass was reading every slip and card play ever made, every five
minutes, so each new bet made every later day shorter.

    /usr/bin/python3 test_read_budget.py
"""

import datetime as dt
import json
import sys
from unittest import mock

import chaos_alerts as ca
import fumble_alerts as fa
import update_cache as uc
from nfl_live import CARDS_EVERY, finals, slips_due

failures = 0


def check(name, got, want):
    global failures
    if got != want:
        failures += 1
        print(f"FAIL {name}: got {got!r}, want {want!r}")


def minutes(n):
    return dt.timedelta(minutes=n)


# ── When the slip pass runs ───────────────────────────────────────────────

MONDAY = dt.datetime(2026, 11, 2, 22, 0, tzinfo=dt.timezone.utc)
SATURDAY = dt.datetime(2026, 11, 7, 22, 0, tzinfo=dt.timezone.utc)
BET = {"open": {"nfl1": 2}, "pro": set()}
FINAL = [{"id": "nfl1", "status": "final"}]
LIVE = [{"id": "nfl1", "status": "live", "period": 2, "clock": "9:00"}]

check("nothing happening: waits", slips_due(LIVE, BET, set(), MONDAY,
                                            MONDAY - minutes(20)), False)
check("the slow clock", slips_due(LIVE, BET, set(), MONDAY,
                                  MONDAY - minutes(60)), True)
check("a Saturday's clock is quicker", slips_due(LIVE, BET, set(), SATURDAY,
                                                 SATURDAY - minutes(30)), True)
check("a game with a slip on it just ended",
      slips_due(FINAL, BET, set(), MONDAY, MONDAY - minutes(6)), True)
check("…but not twice", slips_due(FINAL, BET, finals(FINAL), MONDAY,
                                  MONDAY - minutes(6)), False)
check("…and not within five minutes",
      slips_due(FINAL, BET, set(), MONDAY, MONDAY - minutes(4)), False)
check("a game nobody bet ending costs nothing",
      slips_due([{"id": "nfl2", "status": "final"}], BET, set(), MONDAY,
                MONDAY - minutes(6)), False)

LATE = [{"id": "nfl1", "status": "live", "period": 4, "clock": "3:00"}]
PRO = {"open": {"nfl1": 1}, "pro": {"nfl1"}}
check("a Pro sweat game late on", slips_due(LATE, PRO, set(), MONDAY,
                                            MONDAY - minutes(10)), True)
check("…but not before ten minutes", slips_due(LATE, PRO, set(), MONDAY,
                                               MONDAY - minutes(9)), False)
check("…and not for players without it", slips_due(LATE, BET, set(), MONDAY,
                                                   MONDAY - minutes(10)), False)
check("no quicker than the old pass", CARDS_EVERY >= minutes(5), True)

# ── What a pass reads ─────────────────────────────────────────────────────

listed = []
db = {f"notifications/{fa.LAUNCH_KEY}": {"fields": {}}}


def fs_get(path):
    return db.get(path)


def fs_list(path):
    listed.append(path)
    if path == "fumbling":
        return [{"name": "fumbling/u1"}]
    return []


slip = {"name": "fumbling/u1/slips/s1", "fields": {
    "stake": {"integerValue": "10"},
    "legs": {"arrayValue": {"values": [{"mapValue": {"fields": {
        "gameId": {"stringValue": "nfl1"},
        "season": {"integerValue": "2026"},
        "week": {"integerValue": "208"},
        "market": {"stringValue": "moneyline"},
        "home": {"booleanValue": True},
        "homeTeam": {"stringValue": "A"},
        "awayTeam": {"stringValue": "B"},
    }}}]}},
}}
db["fumbling/u1/prefs/alerts"] = {"fields": {"sweat": {"booleanValue": True}}}

watch = {}
asked = []
fa.run(fs_get, fs_list, lambda p, f: None, lambda *a: None,
       slips_of=lambda uid: asked.append(uid) or [slip], watch=watch)
check("slips come from slips_of", asked, ["u1"])
check("…and never the whole collection",
      [p for p in listed if p.endswith("/slips")], [])
check("watch has the open games", watch["open"], {"nfl1": 1})
check("watch has the Pro games", watch["pro"], {"nfl1"})

check("the default still reads every slip", (
    listed.clear(),
    fa.run(fs_get, fs_list, lambda p, f: None, lambda *a: None),
    "fumbling/u1/slips" in listed)[2], True)

listed.clear()
ca_db = {f"notifications/{ca.LAUNCH_KEY}": {"fields": {}}}


def ca_list(path):
    listed.append(path)
    return {"crews": [{"name": "crews/c1", "fields": {}}]}.get(path, [])


quiet = dt.datetime(2026, 10, 14, 18, tzinfo=dt.timezone.utc)  # a Wednesday
crews_asked = []
ca.run(ca_db.get, ca_list, lambda p, f: None, lambda *a: None, now=quiet,
       plays_of=lambda cid: crews_asked.append(cid) or [],
       duels_of=lambda: [], props=False)
check("plays come from plays_of", crews_asked, ["c1"])
check("no whole list of plays, duels or slips",
      [p for p in listed if p not in ("crews", "chaos")], [])

# The Monday report still counts the week, not just the newest plays.
monday = dt.datetime(2026, 10, 19, 16, tzinfo=dt.timezone.utc)
week_asked = []
ca.run(ca_db.get, ca_list, lambda p, f: None, lambda *a: None, now=monday,
       plays_of=lambda cid: [],
       week_plays_of=lambda cid: week_asked.append(cid) or [],
       duels_of=lambda: [], props=False)
check("the report reads the week", week_asked, ["c1"])

# ── Shared memory ─────────────────────────────────────────────────────────

uc.forget_reads()
reads = []


class Reply:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opened(req, timeout=None):
    reads.append(req.full_url)
    if ":runQuery" in req.full_url:
        return Reply(json.dumps([{"readTime": "x"}]).encode())
    return Reply(json.dumps({"documents": [{"name": "fumbling/u1"}]}).encode())


with mock.patch("urllib.request.urlopen", opened):
    uc.fs_list("t", "fumbling")
    uc.fs_list("t", "fumbling")
    check("the player list is remembered", len(reads), 1)

    reads.clear()
    made = uc.READS["made"]
    check("an empty filter finds nothing",
          uc.fs_where("t", "fumbling/u1", "slips", "createdAt",
                      "GREATER_THAN_OR_EQUAL", {"stringValue": "2026"}), [])
    check("…under the right parent", "/documents/fumbling/u1:runQuery"
          in reads[0], True)
    check("…and still counts a read", uc.READS["made"] - made, 1)

    reads.clear()
    uc.fs_where("t", "", "duels", "status", "IN",
                {"arrayValue": {"values": []}})
    check("a top-level filter", reads[0].endswith("/documents:runQuery"), True)

    reads.clear()
    uc.remember_own_write("cache/nfl_x", {"a": {"stringValue": "b"}})
    uc._memory["cache/nfl_x"] = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=70),
        uc._memory["cache/nfl_x"][1])
    check("a shift's own write outlives a tick",
          uc.fs_get("t", "cache/nfl_x"), {"fields": {"a": {"stringValue": "b"}}})
    check("…with no read", reads, [])
uc.forget_reads()

if failures:
    sys.exit(f"{failures} failed")
print("read budget: all good")
