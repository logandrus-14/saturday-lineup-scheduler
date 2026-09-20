#!/usr/bin/env python3
"""The server's trophy pass, against a fake Firestore.

Pins the parts the parity fixture cannot: who gets WRITTEN to (only people
whose case changed, only people with a profile), and who gets TOLD about a
perfect week (groupmates, once each, never the person, never an old week,
never twice).

    /usr/bin/python3 test_award_trophies.py
"""

import sys

import notify
import update_cache as uc

SLOTS = ["qb", "rb", "wr", "te", "def", "flex", "kicker"]


def game(gid, hour, covered, status="final"):
    # Home -6.5; a home pick covers on a 10-point win, misses on a 3-point one.
    return {"id": gid, "homeTeam": f"H{gid}", "awayTeam": f"A{gid}",
            "startDate": f"2026-09-19T{hour:02d}:00:00Z", "spread": -6.5,
            "homeScore": 40 if covered else 33, "awayScore": 30,
            "status": status}


def week_of(week, outcomes, status="final"):
    games, picks = [], {}
    for i, cov in enumerate(outcomes):
        gid = f"w{week}g{i}"
        games.append(game(gid, 10 + i, cov, status))
        picks[SLOTS[i]] = {"gameId": gid, "team": f"H{gid}"}
    return games, picks


class Fake:
    def __init__(self, groups, sent=()):
        self.groups = groups
        self.sent_keys = set(sent)
        self.patches = []
        self.pushes = []

    def install(self):
        uc.fs_list = lambda token, path: (
            self.groups if path == "groups" else
            [{"name": f"{path}/d", "fields": {"token": {"stringValue":
                                                        f"tok-{path.split('/')[1]}"}}}]
            if path.endswith("/devices") else [])
        uc.fs_get = lambda token, path: (
            {} if path.startswith("notifications/")
            and path.split("/", 1)[1] in self.sent_keys else None)
        def patch(token, path, fields, mask=None):
            self.patches.append((path, fields, mask))
            if path.startswith("notifications/"):
                self.sent_keys.add(path.split("/", 1)[1])
        uc._fs_patch = patch
        notify.send_to_token = lambda token, project, dev, title, body, route=None: \
            self.pushes.append((dev, title, body)) or True


def group(gid, members):
    return {"name": f"groups/{gid}", "fields": {"memberUids": {"arrayValue": {
        "values": [{"stringValue": m} for m in members]}}}}


def profile(name, trophies=()):
    f = {"username": {"stringValue": name}}
    if trophies:
        f["trophies"] = {"arrayValue": {"values": [
            {"stringValue": t} for t in trophies]}}
    return f


failures = 0


def check(label, got, want):
    global failures
    if got == want:
        print(f"  ok   {label}")
    else:
        failures += 1
        print(f"  FAIL {label}: got {got!r}, expected {want!r}")


def run(fake, slates, lineups, profiles, players, week=3):
    fake.install()
    uc.award_trophies("tok", 2026, week, slates, lineups, profiles, players)


# Week 3, every game final. Logan 7/7; Sam 6/7; Jo has no profile document.
g3, logan3 = week_of(3, [True] * 7)
sam3 = dict(logan3)
sam3["kicker"] = {"gameId": "w3g6", "team": "Aw3g6"}  # took the other side
slates = {3: g3}
lineups = {("logan", 3): logan3, ("sam", 3): sam3, ("jo", 3): logan3}
profiles = {"logan": profile("Logan"), "sam": profile("Sam")}
groups = [group("a", ["logan", "sam", "jo"]), group("b", ["logan", "sam", "kim"])]

fake = Fake(groups)
run(fake, slates, lineups, profiles, ["logan", "sam", "jo"])
written = {p[0]: p[1]["trophies"]["arrayValue"]["values"]
           for p in fake.patches if p[0].startswith("users/")}
check("Logan is awarded a perfect week",
      [v["stringValue"] for v in written["users/logan"]],
      ["perfect_week", "big_week", "streak_5"])
check("only the trophies field is written",
      [p[2] for p in fake.patches if p[0] == "users/logan"], [["trophies"]])
check("nobody without a profile document gets one conjured",
      "users/jo" in written, False)
check("the push goes to each groupmate once, never to Logan",
      sorted(p[0] for p in fake.pushes if "Logan" in p[2]),
      ["tok-jo", "tok-kim", "tok-sam"])
check("it says what happened",
      {p[1:] for p in fake.pushes if "Logan" in p[2]},
      {("Perfect week 🏆", "Logan covered all 7 picks in Week 3.")})

# Second run, same data: already told, already stored — nothing happens.
profiles2 = {"logan": profile("Logan", ["perfect_week", "big_week", "streak_5"]),
             "sam": profile("Sam", ["big_week", "streak_5"])}
fake2 = Fake(groups, sent=fake.sent_keys)
run(fake2, slates, lineups, profiles2, ["logan", "sam", "jo"])
check("a second run writes nothing and tells nobody",
      (fake2.patches, fake2.pushes), ([], []))

# A perfect week still in progress: four covered, three still playing.
g_live, logan_live = week_of(3, [True] * 4 + [True] * 3, status="final")
for g in g_live[4:]:
    g["status"] = "live"
fake3 = Fake(groups)
run(fake3, {3: g_live}, {("logan", 3): logan_live}, {"logan": profile("Logan")},
    ["logan"])
check("four of seven finished is not announced",
      fake3.pushes, [])

# Last week's perfect week, seen for the first time this week: not news.
g2, logan2 = week_of(2, [True] * 7)
g3b, logan3b = week_of(3, [False] * 7)
fake4 = Fake(groups)
run(fake4, {2: g2, 3: g3b}, {("logan", 2): logan2, ("logan", 3): logan3b},
    {"logan": profile("Logan")}, ["logan"])
check("an old perfect week is a trophy but not an announcement",
      (fake4.pushes, "perfect_week" in [
          v["stringValue"] for p in fake4.patches if p[0] == "users/logan"
          for v in p[1]["trophies"]["arrayValue"]["values"]]),
      ([], True))

check("Opening Week is named the way the app names it",
      uc.perfect_week_body("Sam", 7, 1), "Sam covered all 7 picks in Opening Week.")

print()
if failures:
    print(f"{failures} check(s) failed")
    sys.exit(1)
print("all checks passed")
