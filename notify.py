#!/usr/bin/env python3
"""Sending push notifications from the scheduler.

Why here and not a Cloud Function: this job already runs every 60 seconds
during games, already holds admin credentials, and already knows which
games have kicked off and which have gone final. A Cloud Function would
need gcloud, which will not install on the machine this project is
developed on. See HANDOFF.md.

Three things get sent, and nothing else. Every one of them is something a
person would be annoyed to have missed, which is the whole test for whether
a notification deserves to exist:

  lineup reminder  -- your lineup is not full and the first game is close
  pick went final  -- a game you picked has finished
  rank change      -- you moved in your group's standings

DE-DUPLICATION IS THE HARD PART. This loop runs every minute for five and a
half hours. Anything that says "send when X is true" sends 300 times.
Every send here is therefore recorded, and the record is checked first --
see `already_sent`. A notification sent twice is worse than one sent late.
"""
import datetime as dt
import json
import os
import urllib.error
import urllib.request

FCM = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"


def _post(url, token, body):
    # DRY_RUN stops here, at the last possible moment. The message has
    # already been built, addressed and de-duplicated by this point, so a
    # rehearsal exercises everything except the delivery itself.
    # See the DRY_RUN note in update_cache.py.
    if os.environ.get("DRY_RUN") == "1":
        note = {}
        if isinstance(body, dict):
            note = body.get("message", {}).get("notification", {}) or {}
        title = note.get("title", "?")
        print(f"    DRY RUN — would send push: {title!r}")
        try:
            import update_cache
            update_cache._dry_sends.append(title)
        except Exception:  # noqa: BLE001 — the summary is a nicety, not the guard
            pass
        return {"name": "dry-run"}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=20))


def send_to_token(token, project, device_token, title, body, route=None):
    """One notification to one device.

    Returns True if it went, False if the address is dead. A dead token is
    the normal end of a device's life -- the app was deleted, or restored
    onto a new phone -- so it is a value to act on, not an error to raise.
    """
    message = {
        "message": {
            "token": device_token,
            "notification": {"title": title, "body": body},
            "apns": {
                "payload": {"aps": {"sound": "default"}},
            },
        }
    }
    if route:
        # Read by routeForNotification() in the app, which only follows
        # routes on its own allow-list -- never whatever arrives.
        message["message"]["data"] = {"route": route}

    try:
        _post(FCM.format(project=project), token, message)
        return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        # 404 UNREGISTERED / 400 INVALID_ARGUMENT on the token both mean
        # "this address is gone". Anything else is our problem, not the
        # device's, and should be visible in the run log.
        if e.code in (400, 404) and (
                "UNREGISTERED" in detail or "INVALID_ARGUMENT" in detail):
            return False
        print(f"  fcm error {e.code}: {detail}")
        return True  # not the device's fault; keep the token


def devices_for(fs_list, uid):
    """Every device token registered to one person."""
    out = []
    for doc in fs_list(f"users/{uid}/devices"):
        tok = doc.get("fields", {}).get("token", {}).get("stringValue")
        if tok:
            out.append((tok, doc["name"]))
    return out


def already_sent(fs_get, key):
    """Has this exact notification already gone out?

    The de-dupe key names the EVENT, not the moment -- "logan, game 401,
    final" -- so a loop that notices the same finished game sixty times in
    an hour still sends once.
    """
    return fs_get(f"notifications/{key}") is not None


def record_sent(fs_patch, key, kind, uid):
    fs_patch(f"notifications/{key}", {
        "kind": {"stringValue": kind},
        "uid": {"stringValue": uid},
        "sentAt": {"timestampValue": dt.datetime.now(dt.timezone.utc)
                   .isoformat().replace("+00:00", "Z")},
    })


def dedupe_key(kind, uid, subject):
    """A stable id for one notification about one thing for one person."""
    safe = str(subject).replace("/", "_")
    return f"{kind}__{uid}__{safe}"
