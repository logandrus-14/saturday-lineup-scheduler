"""Erase a deleted account's Fumbling data.

Apple requires that deleting an account deletes its data. Most of
Fumbling can't be deleted from the phone: firestore.rules refuses deleting
slips and ledger entries (that is what stops a bad bet being wiped), and a
crewmate's doc isn't yours to touch. So Delete account writes a marker,
`deletions/{uid}`, just before the sign-in is deleted, and this sweep does
the rest with the admin key:

  fumbling/{uid} with its slips and ledger, chaos/{uid} with its plays,
  users/{uid}'s devices and live activities, every crew they were in
  (a crew of one is deleted with its code; otherwise they leave it and
  the next member owns it), crew and Fumbling invites to or from them,
  their duels, their uid on anyone's viewers list, and their name on
  crew plays ("Deleted player").

It only erases once the sign-in is really gone. Someone who backed out of
the re-sign-in still has an account: their marker waits two days, then is
dropped and nothing is touched. Any error leaves the marker for the next
run — never a guess.

Every Firestore call is passed in, so test_account_cleanup.py runs it
against a dict.
"""
from __future__ import annotations

import datetime as dt

DELETED_NAME = "Deleted player"

# A marker whose account still exists after this long was a backed-out
# delete, not one waiting on Firebase.
GIVE_UP_AFTER = dt.timedelta(days=2)

# At most this many accounts a run, so one bad run can't do much.
MAX_PER_RUN = 5


def _id(doc):
    return doc["name"].rsplit("/", 1)[-1]


def _rel(doc):
    """"projects/p/databases/(default)/documents/crews/x" → "crews/x"."""
    return doc["name"].split("/documents/", 1)[1]


def _str(doc, field):
    return doc.get("fields", {}).get(field, {}).get("stringValue")


def _strs(doc, field):
    vals = doc.get("fields", {}).get(field, {}).get(
        "arrayValue", {}).get("values", [])
    return [v["stringValue"] for v in vals if "stringValue" in v]


def _when(doc):
    v = doc.get("fields", {}).get("at", {}).get("timestampValue")
    if not v:
        return None
    return dt.datetime.fromisoformat(v.replace("Z", "+00:00"))


def _update(path, fields, mask, transforms=None):
    w = {"update": {"name": path, "fields": fields},
         "updateMask": {"fieldPaths": mask},
         "currentDocument": {"exists": True}}
    if transforms:
        w["updateTransforms"] = transforms
    return w


def _remove(field, uid):
    return {"fieldPath": field,
            "removeAllFromArray": {"values": [{"stringValue": uid}]}}


def erase(uid, list_docs, query, delete, commit):
    """Everything Fumbling holds on [uid]. Safe to run twice."""
    for sub in ("slips", "ledger"):
        for d in list_docs(f"fumbling/{uid}/{sub}"):
            delete(_rel(d))
    delete(f"fumbling/{uid}")
    for d in list_docs(f"chaos/{uid}/plays"):
        delete(_rel(d))
    delete(f"chaos/{uid}")
    for sub in ("devices", "liveActivities"):
        for d in list_docs(f"users/{uid}/{sub}"):
            delete(_rel(d))

    for crew in query("crews", "members", "ARRAY_CONTAINS", uid):
        path = _rel(crew)
        others = [m for m in _strs(crew, "members") if m != uid]
        if not others:
            for sub in ("plays", "survivor", "parlays"):
                for d in list_docs(f"{path}/{sub}"):
                    delete(_rel(d))
            if code := _str(crew, "code"):
                delete(f"crewCodes/{code}")
            delete(path)
            continue
        delete(f"{path}/survivor/{uid}")
        for play in list_docs(f"{path}/plays"):
            mask, fields = [], {}
            for who, name in (("from", "fromName"), ("to", "toName")):
                if _str(play, who) == uid:
                    mask.append(name)
                    fields[name] = {"stringValue": DELETED_NAME}
            if mask:
                commit([_update(play["name"], fields, mask)])
        fields, mask = {}, [f"names.`{uid}`"]
        if _str(crew, "owner") == uid:
            fields["owner"] = {"stringValue": others[0]}
            mask.append("owner")
        commit([_update(crew["name"], fields, mask,
                        [_remove("members", uid)])])

    for coll, fields in (("crewInvites", ("from", "to")),
                         ("fumblingInvites", ("from", "to")),
                         ("duels", ("a", "b"))):
        for f in fields:
            for d in query(coll, f, "EQUAL", uid):
                delete(_rel(d))

    for share in query("fumbling", "viewers", "ARRAY_CONTAINS", uid):
        commit([_update(share["name"], {}, [], [_remove("viewers", uid)])])


def run(list_docs, query, delete, commit, account_gone, now=None):
    """Sweep the markers. Returns how many accounts were erased.

    account_gone(uid) → True once the sign-in no longer exists, False while
    it does; it raises when it can't tell, and that marker waits.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    erased = 0
    for marker in list_docs("deletions")[:MAX_PER_RUN]:
        uid = _id(marker)
        try:
            if not account_gone(uid):
                at = _when(marker)
                if at is None or now - at > GIVE_UP_AFTER:
                    delete(f"deletions/{uid}")
                continue
            erase(uid, list_docs, query, delete, commit)
            delete(f"deletions/{uid}")
            erased += 1
        except Exception as e:  # the next run tries again
            print(f"account cleanup: {uid} waits: {e}")
    return erased
