#!/usr/bin/env python3
"""The read memory in update_cache.py: fewer reads, never stale ones.

WHY THIS EXISTS. On the free plan Firestore stops answering at 50,000
reads a day, and Saturdays were reading 75,000–87,000 (Oct 12 2026). The
memory is what brings that down. Every rule here protects against the way
a memory goes wrong:

  * a tick must still see the last tick's changes (TICK_MEMORY < 60 s)
  * a board must reveal the pick as it stood AT KICKOFF, not a lineup read
    ten minutes before it
  * "not sent yet" must never be remembered, or a push goes out twice
  * this process must never be served a document older than its own write

    /usr/bin/python3 test_read_memory.py
"""

import datetime as dt
import io
import json
import sys
import urllib.error

import update_cache as uc

network = []      # every path actually fetched
store = {}        # path -> doc, what "Firestore" holds


def fake_urlopen(req, timeout=20):
    url = req.full_url if hasattr(req, "full_url") else req
    path = url.split("/documents/", 1)[-1].split("?")[0]
    method = req.get_method() if hasattr(req, "get_method") else "GET"
    if method != "GET":
        return io.BytesIO(b"{}")
    network.append(path)
    if path in store:
        return io.BytesIO(json.dumps(store[path]).encode())
    children = [d for p, d in store.items()
                if p.rsplit("/", 1)[0] == path]
    if children or path in ("groups", "users"):
        return io.BytesIO(json.dumps({"documents": children}).encode())
    raise urllib.error.HTTPError(url, 404, "missing", {}, None)


uc.urllib.request.urlopen = fake_urlopen
uc.DRY_RUN = False

failures = 0


def check(label, ok):
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


def age(path, seconds):
    at, value = uc._memory[path]
    uc._memory[path] = (at - dt.timedelta(seconds=seconds), value)


def reset():
    uc.forget_reads()
    uc.note_kickoffs([])
    network.clear()
    store.clear()


print("one tick shares one read")
reset()
store["cache/slate_2026_7"] = {"fields": {"a": 1}}
uc.fs_get("t", "cache/slate_2026_7")
uc.fs_get("t", "cache/slate_2026_7")
check("read once", network.count("cache/slate_2026_7") == 1)
age("cache/slate_2026_7", 50)
uc.fs_get("t", "cache/slate_2026_7")
check("the next tick reads it again", network.count("cache/slate_2026_7") == 2)

print("the group list is remembered for ten minutes")
reset()
store["groups/g1"] = {"name": "x/groups/g1", "fields": {}}
uc.fs_list("t", "groups")
age("groups", 5 * 60)
uc.fs_list("t", "groups")
check("still remembered after 5 min", network.count("groups") == 1)
age("groups", 6 * 60)
uc.fs_list("t", "groups")
check("read again after 11 min", network.count("groups") == 2)

print("a lineup is read again after any kickoff")
reset()
now = dt.datetime.now(dt.timezone.utc)
lineup = "users/u1/lineups/2026_7"
store[lineup] = {"fields": {}}
uc.note_kickoffs([now + dt.timedelta(minutes=3)])
uc.fs_get("t", lineup)
uc.fs_get("t", lineup)
check("remembered between kickoffs", network.count(lineup) == 1)
uc.note_kickoffs([now - dt.timedelta(seconds=5)])
age(lineup, 30)
uc.fs_get("t", lineup)
check("read again once a game has kicked off", network.count(lineup) == 2)

print("a lineup isn't kept long when the kickoffs aren't known")
reset()
store[lineup] = {"fields": {}}
uc.fs_get("t", lineup)
age(lineup, 50)
uc.fs_get("t", lineup)
check("only a tick's worth", network.count(lineup) == 2)

print("a pick's own kickoff counts too")
reset()
store[lineup] = {"fields": {"slots": {"mapValue": {"fields": {"lock": {
    "mapValue": {"fields": {"kickoffAt": {"timestampValue": (
        now - dt.timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    }}}}}}}}}
uc.note_kickoffs([now + dt.timedelta(hours=2)])
uc.fs_get("t", lineup)
age(lineup, 30)
uc.fs_get("t", lineup)
check("read again after its own pick kicked off",
      network.count(lineup) == 2)

print("sent stays sent; unsent is never remembered")
reset()
key = "notifications/final__u1__401"
check("unsent reads as unsent", uc.fs_get("t", key) is None)
check("and asks again", uc.fs_get("t", key) is None
      and network.count(key) == 2)
store[key] = {"fields": {}}
uc.fs_get("t", key)
age(key, 24 * 3600)
check("once sent, remembered all day",
      uc.fs_get("t", key) is not None and network.count(key) == 3)

print("our own record of sending counts as sent")
reset()
key = "notifications/kick__u2__402"
uc._fs_patch("t", key, {"kind": {"stringValue": "x"}})
check("no read needed", uc.fs_get("t", key) is not None
      and network.count(key) == 0)

print("our own write is never served back stale")
reset()
store["groups/g1/board/2026_7"] = {"fields": {"v": 1}}
store["groups/g1"] = {"name": "x/groups/g1", "fields": {}}
uc.fs_get("t", "groups/g1/board/2026_7")
uc._fs_patch("t", "groups/g1/board/2026_7", {"v": {"integerValue": "2"}})
uc.fs_get("t", "groups/g1/board/2026_7")
check("read again after writing it",
      network.count("groups/g1/board/2026_7") == 2)
uc.fs_list("t", "groups")
uc._fs_patch("t", "groups/g1", {"x": {"integerValue": "1"}})
uc.fs_list("t", "groups")
check("its collection's list too", network.count("groups") == 2)

print("reads are counted")
reset()
uc.READS.update(made=0, saved=0)
store["fumbling/u1/slips/a"] = {"name": "x/a", "fields": {}}
store["fumbling/u1/slips/b"] = {"name": "x/b", "fields": {}}
uc.fs_list("t", "fumbling/u1/slips")
uc.fs_list("t", "fumbling/u1/slips")
check("a list costs one per document", uc.READS["made"] == 2)
check("and the repeat is saved", uc.READS["saved"] == 2)

print()
print("All passed." if not failures else f"{failures} FAILED")
sys.exit(1 if failures else 0)
