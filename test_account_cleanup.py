#!/usr/bin/env python3
"""Erasing a deleted account's Fumbling data (account_cleanup.py), against
a pretend database.

    python3 test_account_cleanup.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import account_cleanup  # noqa: E402

ROOT = "projects/p/databases/(default)/documents/"
NOW = dt.datetime(2026, 10, 19, 12, tzinfo=dt.timezone.utc)
checks = failures = 0


def check(what, ok):
    global checks, failures
    checks += 1
    if not ok:
        failures += 1
        print(f"FAIL: {what}")


def s(v):
    return {"stringValue": v}


def arr(*vs):
    return {"arrayValue": {"values": [s(v) for v in vs]}}


class Fake:
    """Documents by path → fields (REST shape)."""

    def __init__(self, docs):
        self.docs = dict(docs)

    def _doc(self, path):
        return {"name": ROOT + path, "fields": self.docs[path]}

    def list_docs(self, coll):
        depth = coll.count("/") + 1
        return [self._doc(p) for p in sorted(self.docs)
                if p.startswith(coll + "/") and p.count("/") == depth]

    def query(self, coll, field, op, value):
        out = []
        for p in sorted(self.docs):
            if not p.startswith(coll + "/") or p.count("/") != 1:
                continue
            f = self.docs[p].get(field, {})
            if op == "EQUAL" and f.get("stringValue") == value:
                out.append(self._doc(p))
            if op == "ARRAY_CONTAINS" and value in [
                    v.get("stringValue")
                    for v in f.get("arrayValue", {}).get("values", [])]:
                out.append(self._doc(p))
        return out

    def delete(self, path):
        self.docs.pop(path, None)

    def commit(self, writes):
        for w in writes:
            path = w["update"]["name"][len(ROOT):]
            assert path in self.docs, f"update of missing {path}"
            doc = self.docs[path]
            for m in w["updateMask"]["fieldPaths"]:
                if m.startswith("names.`"):
                    key = m[len("names.`"):-1]
                    doc["names"]["mapValue"]["fields"].pop(key, None)
                else:
                    doc[m] = w["update"]["fields"][m]
            for t in w.get("updateTransforms", []):
                gone = {v["stringValue"]
                        for v in t["removeAllFromArray"]["values"]}
                vals = doc[t["fieldPath"]]["arrayValue"]["values"]
                doc[t["fieldPath"]]["arrayValue"]["values"] = [
                    v for v in vals if v["stringValue"] not in gone]

    def run(self, gone):
        return account_cleanup.run(self.list_docs, self.query, self.delete,
                                   self.commit, gone, now=NOW)


def names(fake, path):
    return set(fake.docs[path]["names"]["mapValue"]["fields"])


def world():
    when = {"timestampValue": "2026-10-19T11:00:00Z"}
    return Fake({
        "deletions/gone": {"at": when},
        "fumbling/gone": {"name": s("Gone")},
        "fumbling/gone/slips/s1": {"stake": {"integerValue": "50"}},
        "fumbling/gone/ledger/l1": {"amount": {"integerValue": "20"}},
        "chaos/gone": {"hand": arr("Freeze")},
        "chaos/gone/plays/p0": {"card": s("Jinx")},
        "users/gone/devices/t1": {"app": s("fumbling")},
        # A crew they share, which they own; and one they're alone in.
        "crews/big": {"owner": s("gone"), "members": arr("gone", "kade"),
                      "names": {"mapValue": {"fields": {
                          "gone": s("Gone"), "kade": s("Kade")}}}},
        "crews/big/plays/p1": {"from": s("gone"), "fromName": s("Gone"),
                               "to": s("kade"), "toName": s("Kade")},
        "crews/big/survivor/gone": {"alive": {"booleanValue": True}},
        "crews/solo": {"owner": s("gone"), "members": arr("gone"),
                       "code": s("ABC123"),
                       "names": {"mapValue": {"fields": {"gone": s("Gone")}}}},
        "crews/solo/plays/p2": {"from": s("gone")},
        "crewCodes/ABC123": {"crewId": s("solo")},
        "crewInvites/big_zane": {"from": s("gone"), "to": s("zane")},
        "fumblingInvites/kade_gone": {"from": s("kade"), "to": s("gone")},
        "duels/d1": {"a": s("kade"), "b": s("gone")},
        "fumbling/kade": {"name": s("Kade"), "viewers": arr("gone", "zane")},
        # Someone else's, untouched.
        "fumbling/kade/slips/k1": {"stake": {"integerValue": "10"}},
        "duels/d2": {"a": s("kade"), "b": s("zane")},
    })


# ── The account is gone: everything of theirs goes ──
f = world()
check("one account erased", f.run(lambda uid: True) == 1)
left = set(f.docs)
for p in ("fumbling/gone", "fumbling/gone/slips/s1", "fumbling/gone/ledger/l1",
          "chaos/gone", "chaos/gone/plays/p0", "users/gone/devices/t1",
          "crews/solo", "crews/solo/plays/p2", "crewCodes/ABC123",
          "crews/big/survivor/gone", "crewInvites/big_zane",
          "fumblingInvites/kade_gone", "duels/d1", "deletions/gone"):
    check(f"{p} deleted", p not in left)
check("the shared crew stays", "crews/big" in left)
check("they left it", account_cleanup._strs(
    {"fields": f.docs["crews/big"]}, "members") == ["kade"])
check("their name is off it", names(f, "crews/big") == {"kade"})
check("the next member owns it",
      f.docs["crews/big"]["owner"]["stringValue"] == "kade")
check("their name is off the crew's plays",
      f.docs["crews/big/plays/p1"]["fromName"]["stringValue"]
      == "Deleted player"
      and f.docs["crews/big/plays/p1"]["toName"]["stringValue"] == "Kade")
check("off Kade's viewers", account_cleanup._strs(
    {"fields": f.docs["fumbling/kade"]}, "viewers") == ["zane"])
for p in ("fumbling/kade", "fumbling/kade/slips/k1", "duels/d2"):
    check(f"{p} untouched", p in left)
check("running again does nothing", f.run(lambda uid: True) == 0)

# ── The account still exists (backed out of the re-sign-in) ──
f = world()
before = dict(f.docs)
check("nothing erased", f.run(lambda uid: False) == 0)
check("nothing touched, marker kept", f.docs == before)
f.docs["deletions/gone"] = {"at": {"timestampValue": "2026-10-15T00:00:00Z"}}
f.run(lambda uid: False)
check("a 4-day-old marker is dropped", "deletions/gone" not in f.docs)
check("and still nothing else touched", "fumbling/gone/slips/s1" in f.docs)

# ── Can't tell (the sign-in lookup failed): wait ──
f = world()


def broken(uid):
    raise OSError("identitytoolkit 503")


check("nothing erased on an error", f.run(broken) == 0)
check("marker kept on an error", "deletions/gone" in f.docs)
check("slips kept on an error", "fumbling/gone/slips/s1" in f.docs)

print(f"{checks - failures}/{checks} checks passed")
sys.exit(1 if failures else 0)
