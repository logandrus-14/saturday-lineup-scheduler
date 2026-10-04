#!/usr/bin/env python3
"""Weekly matchups — who plays whom, and the records.

**A SECOND COPY of lib/features/matchups/domain/matchups.dart.** The app
draws the matchups; this sends the alerts about them ("This week you play
Eric", "You beat Eric"). If the two disagreed, somebody would be told about
one opponent and shown another. `matchup_cases.json` is the answer key
both are tested against — test_matchups_parity.py here,
test/matchup_parity_test.dart in the app. Change them together.

Rules (Logan, Oct 2–3 2026): one opponent a week by round robin; an odd
group gives one person a week the group AVERAGE; points stay the
champion; top 4 by record play semifinals in week 14 and the final in
week 15; groups under 4 have no playoffs. A week score is won − lost —
the same number the app's weekly tab prints, a no-pick week being −28.
"""

FROM_WEEK = 1
LAST_REGULAR_WEEK = 13
SEMIFINAL_WEEK = 14
FINAL_WEEK = 15
PLAYOFF_MIN_MEMBERS = 4


def _stable_hash(s):
    """FNV-1a, 32-bit, over the string's code units — Dart's _stableHash."""
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def pairings_for(members, group_id, week):
    """Round robin by the circle method. Each pair is [a, b]; b None means
    a plays the group average."""
    if len(members) < 2 or week < FROM_WEEK:
        return []
    order = sorted(members,
                   key=lambda m: (_stable_hash(f"{group_id}/{m}"), m))
    slots = list(order)
    if len(slots) % 2 == 1:
        slots.append(None)
    n = len(slots)
    rnd = (week - FROM_WEEK) % (n - 1)
    rest = slots[1:]
    rotated = [rest[(i - rnd) % len(rest)] for i in range(len(rest))]
    circle = [slots[0]] + rotated
    out = []
    for i in range(n // 2):
        x, y = circle[i], circle[n - 1 - i]
        out.append([y, None] if x is None else [x, y])
    return out


def average_of(scores):
    return sum(scores.values()) / len(scores) if scores else 0.0


def records_from(members, group_id, finished_scores):
    """{uid: [wins, losses, ties, points_for]} over FINISHED regular weeks."""
    rec = {m: [0, 0, 0, 0] for m in members}

    def add(uid, mine, theirs):
        r = rec.setdefault(uid, [0, 0, 0, 0])
        r[0] += 1 if mine > theirs else 0
        r[1] += 1 if mine < theirs else 0
        r[2] += 1 if mine == theirs else 0
        r[3] += mine

    for week, scores in finished_scores.items():
        if week > LAST_REGULAR_WEEK:
            continue
        avg = average_of(scores)
        for a, b in pairings_for(members, group_id, week):
            if a not in scores:
                continue
            if b is None:
                add(a, scores[a], avg)
                continue
            if b not in scores:
                continue
            add(a, scores[a], scores[b])
            add(b, scores[b], scores[a])
    return rec


def record_label(r):
    """'4–2', or '4–2–1' once there has been a tie — the app's label."""
    w, l, t = r[0], r[1], r[2]
    return f"{w}–{l}" if t == 0 else f"{w}–{l}–{t}"


def standings_order(records):
    """Best record first (ties count half), then points for, then the id."""
    return sorted(records,
                  key=lambda u: (-(records[u][0] + records[u][2] / 2),
                                 -records[u][3], u))


def schedule_for(members, group_id, week, seeds, semifinal_scores=None):
    if week <= LAST_REGULAR_WEEK:
        return pairings_for(members, group_id, week)
    if len(members) < PLAYOFF_MIN_MEMBERS or len(seeds) < 4:
        return []
    semis = [[seeds[0], seeds[3]], [seeds[1], seeds[2]]]
    if week == SEMIFINAL_WEEK:
        return semis
    if week == FINAL_WEEK:
        if semifinal_scores is None:
            return []

        def winner(p):
            a = semifinal_scores.get(p[0], 0)
            b = semifinal_scores.get(p[1], 0)
            return p[1] if b > a else p[0]  # a tie goes to the higher seed
        return [[winner(semis[0]), winner(semis[1])]]
    return []


def pairing_of(pairings, uid):
    for p in pairings:
        if uid in p:
            return p
    return None


def opponent_in(pairing, uid):
    return pairing[1] if pairing[0] == uid else pairing[0]
