#!/usr/bin/env python3
"""An unmasked Firestore PATCH replaces the WHOLE document.

WHY THIS EXISTS. That is the REST contract and it is not what the word
"patch" suggests: a PATCH carrying one field and no `updateMask` deletes
every other field on the document. Four writes in update_cache.py were
doing it, and each one lost something different:

  * record_next_kickoff / record_run write different fields of
    cache/scheduler_state, so each run's `lastRun` deleted the
    `nextKickoffAt` marker the previous run had just written. Measured on
    Aug 29 2026, that document held `lastRun` and nothing else — the
    "skip CFBD when football is weeks away" optimisation had never once
    been able to read its own marker.

  * notify_reactions built `updates` as a fresh dict of only the people
    notified in that pass, so the write dropped everybody else's
    `announced` list. Their next reaction then read as new again — a
    notification loop that could only ever appear on a live Saturday.

  * deliver_nudges stamped `sentAt` and thereby deleted the `uids` and
    `requestedBy` that said who had asked for the nudge and who it was
    for.

    /usr/bin/python3 test_patch_masks.py
"""

import sys
from urllib.parse import parse_qs, urlparse

import update_cache

calls = []
update_cache._send_write = lambda req: calls.append(req)


def patch(fields, mask=None):
    calls.clear()
    update_cache._fs_patch("tok", "cache/thing", fields, mask=mask)
    return urlparse(calls[0].full_url)


failures = 0


def check(label, ok):
    global failures
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")


url = patch({"a": {"stringValue": "1"}})
check("no mask sends no updateMask (a full replace, by choice)",
      "updateMask" not in (url.query or ""))

url = patch({"a": {"stringValue": "1"}}, mask=["a"])
check("a mask becomes updateMask.fieldPaths",
      parse_qs(url.query).get("updateMask.fieldPaths") == ["a"])

url = patch({"a": {}, "b": {}}, mask=["a", "b"])
check("every masked field is named",
      parse_qs(url.query).get("updateMask.fieldPaths") == ["a", "b"])

# The regressions themselves: these four call sites must pass a mask.
import inspect  # noqa: E402

src = inspect.getsource(update_cache)
for label, needle in [
    ("record_run masks lastRun", 'mask=["lastRun"]'),
    ("record_next_kickoff masks nextKickoffAt", 'mask=["nextKickoffAt"]'),
    ("the nudge stamp masks sentAt", 'mask=["sentAt"]'),
]:
    check(label, needle in src)

check("notify_reactions starts from the state already stored",
      "updates = dict(state)" in src)

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 7 checks OK — nothing silently replaces a document")
