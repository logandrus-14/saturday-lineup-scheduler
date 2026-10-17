#!/usr/bin/env python3
"""Each app's pushes reach that app — scheduler/notify.py.

WHY THIS EXISTS. Saturday Lineup and the standalone Fumbling app (Oct 17
2026) share one Firebase project and one users/{uid}/devices list. Without
the `app` tag every lineup reminder would buzz the Fumbling app too, and
someone with both apps would hear every slip twice.

  python3 scheduler/test_device_apps.py
"""

import sys

import notify

failures = 0


def check(label, ok):
    global failures
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures += 1


def device(tok, app=None):
    fields = {"token": {"stringValue": tok}}
    if app:
        fields["app"] = {"stringValue": app}
    return {"name": f"users/u/devices/{tok}", "fields": fields}


def lister(*docs):
    return lambda path: list(docs)


def toks(pairs):
    return [t for t, _ in pairs]


old_phone = lister(device("sl-old"))
both = lister(device("sl-old"), device("sl-new", "lineup"), device("fum", "fumbling"))
fumbling_only = lister(device("fum", "fumbling"))
nothing = lister()

check("a device saved before the tag is Saturday Lineup's",
      toks(notify.devices_for(old_phone, "u")) == ["sl-old"])
check("Saturday Lineup pushes skip the Fumbling app",
      toks(notify.devices_for(both, "u")) == ["sl-old", "sl-new"])
check("a Fumbling-only person gets no Saturday Lineup pushes",
      notify.devices_for(fumbling_only, "u") == [])
check("Fumbling pushes go to the Fumbling app when they have it",
      toks(notify.fumbling_devices_for(both, "u")) == ["fum"])
check("…and to Saturday Lineup's Fumbling tab when they don't",
      toks(notify.fumbling_devices_for(old_phone, "u")) == ["sl-old"])
check("nobody registered, nothing sent",
      notify.fumbling_devices_for(nothing, "u") == [])

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 6 checks OK — every push lands in the right app")
