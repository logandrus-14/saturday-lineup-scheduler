#!/usr/bin/env python3
"""The scheduler backs nudge_function up without ever racing it.

WHY THIS EXISTS. Since Sep 11 2026 a nudge is delivered by a Cloud Function
the moment it is written, and deliver_nudges only picks up what that
missed. Get the timing wrong one way and everybody in a group is nudged
twice; get it wrong the other way and a nudge the function dropped is never
delivered at all.

    /usr/bin/python3 test_nudge_backup.py
"""
import datetime as dt
import sys

from update_cache import nudge_waiting_on_function

failures = 0


def check(label, ok):
    global failures
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures += 1


NOW = dt.datetime(2026, 9, 11, 18, 0, tzinfo=dt.timezone.utc)


def ago(minutes):
    t = NOW - dt.timedelta(minutes=minutes)
    return {"timestampValue": t.isoformat().replace("+00:00", "Z")}


print(__doc__.strip().splitlines()[0])
print()

check("a request seconds old is left to the function",
      nudge_waiting_on_function({"requestedAt": ago(0.5)}, NOW))
check("an unclaimed request past the grace is the scheduler's to send",
      not nudge_waiting_on_function({"requestedAt": ago(6)}, NOW))
check("a claim in progress is left alone",
      nudge_waiting_on_function(
          {"requestedAt": ago(7), "claimedAt": ago(6)}, NOW))
check("a claim that never finished is taken over — a crash, not a delay",
      not nudge_waiting_on_function(
          {"requestedAt": ago(30), "claimedAt": ago(20)}, NOW))
check("a request from before the function existed is still delivered",
      not nudge_waiting_on_function({}, NOW))
check("the client's explicit sentAt: null does not count as sent — "
      "that is checked elsewhere, and this only reads the clocks",
      not nudge_waiting_on_function(
          {"requestedAt": ago(10), "sentAt": {"nullValue": None}}, NOW))

print()
if failures:
    print(f"{failures} check(s) FAILED")
    sys.exit(1)
print("all 6 checks OK — the backup never races the function")
