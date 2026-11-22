"""The referee (referee.py): flags only the impossible, never an honest
slip. Run: python3 test_referee.py"""
import datetime as dt

import fumble_alerts
import referee

T0 = dt.datetime(2026, 11, 21, 18, 0, tzinfo=dt.timezone.utc)


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def leg(price=-110, gid="g1"):
    return {"gameId": gid, "season": 2026, "week": 13, "market": "spread",
            "home": True, "homeTeam": "BYU", "awayTeam": "Utah",
            "line": -3.5, "price": price}


def slip(legs, stake=100.0, **kw):
    return {"id": "s1", "legs": legs, "stake": stake, **kw}


start = lambda l: T0  # every game kicks off at T0


def kinds(problems):
    return sorted(p[0] for p in problems)


def test_pregame_and_live_bets_are_fine():
    assert referee.check_slip(slip([leg()]), iso(T0 - dt.timedelta(hours=2)),
                              start) == []
    # Live: two hours in.
    assert referee.check_slip(slip([leg(+450)]),
                              iso(T0 + dt.timedelta(hours=2)), start) == []


def test_late_bet():
    p = referee.check_slip(slip([leg()]), iso(T0 + dt.timedelta(hours=30)),
                           start)
    assert kinds(p) == ["late_bet"]
    assert "30h" in p[0][1]


def test_futures_and_unknown_games_are_left_alone():
    fut = dict(leg(), gameId="fut_nfl_title_kc")
    assert referee.check_slip(slip([fut]), iso(T0 + dt.timedelta(days=9)),
                              start) == []
    assert referee.check_slip(slip([leg()]), iso(T0 + dt.timedelta(days=9)),
                              lambda l: None) == []


def test_wild_price():
    assert kinds(referee.check_slip(slip([leg(+90000)]), iso(T0), start)) \
        == ["wild_price"]
    assert referee.check_slip(slip([leg(+9900)]), iso(T0), start) == []


def test_cash_out_bigger_than_the_slip_could_pay():
    s = slip([leg(+100), leg(+100)], cashOut=399.0)  # could pay 400
    assert referee.check_slip(s, iso(T0), start) == []
    s = slip([leg(+100), leg(+100)], cashOut=5000.0)
    assert kinds(referee.check_slip(s, iso(T0), start)) == ["big_cashout"]
    # A card's multiplier and a bonus count toward what it could pay.
    s = slip([leg(+100)], cashOut=430.0, payoutMul=2, boost=0.1)
    assert referee.check_slip(s, iso(T0), start) == []


def test_stacked_hands():
    hand = {"arrayValue": {"values": [{"stringValue": "jinx"}] * 41}}
    assert kinds(referee.check_chaos({"hand": hand})) == ["stacked_hand"]
    assert kinds(referee.check_chaos(
        {"mulligans": {"integerValue": "50"}})) == ["stacked_hand"]
    assert referee.check_chaos(
        {"hand": {"arrayValue": {"values": []}},
         "mulligans": {"integerValue": "2"}}) == []


def entry(amount, reason, at="2026-11-21T10:00:00Z"):
    return {"amount": {"doubleValue": amount},
            "reason": {"stringValue": reason}, "at": {"stringValue": at}}


def test_ledger():
    ok = [("rankup_walkOn", entry(100, "rankup")),
          ("challenge_2026-47_hat", entry(20, "challenge")),
          ("duel_d1", entry(500, "duel")),
          ("heist_p1", entry(-300, "heist")),
          ("made_up", entry(-50, "anything"))]  # money out is never a cheat
    assert referee.check_ledger(ok) == []
    bad = referee.check_ledger([
        ("x1", entry(1000000, "gift")),
        ("whatever", entry(5000, "rankup")),
        ("rankup_goat", entry(900000, "rankup")),
    ])
    assert [b[0] for b in bad] == ["free_money"] * 3
    many = [(f"duel_{i}", entry(10, "duel")) for i in range(13)]
    assert [(b[0], b[1]) for b in referee.check_ledger(many)] == [
        ("free_money_day", "duel_2026-11-21")]


def doc(fields, created, updated=None, sid="s1"):
    return {"name": f"projects/p/databases/(default)/documents/fumbling/u/"
                    f"slips/{sid}",
            "fields": fields, "createTime": created,
            "updateTime": updated or created}


def fs_leg(price=-110):
    return {"mapValue": {"fields": {
        "gameId": {"stringValue": "g1"}, "season": {"integerValue": "2026"},
        "week": {"integerValue": "13"}, "market": {"stringValue": "spread"},
        "home": {"booleanValue": True}, "homeTeam": {"stringValue": "BYU"},
        "awayTeam": {"stringValue": "Utah"}, "line": {"doubleValue": -3.5},
        "price": {"integerValue": str(price)}}}}


def test_run_flags_once_and_never_voided_slips():
    late = doc({"legs": {"arrayValue": {"values": [fs_leg()]}},
                "stake": {"doubleValue": 100}},
               iso(T0 + dt.timedelta(days=2)))
    voided = doc({"legs": {"arrayValue": {"values": [fs_leg()]}},
                  "stake": {"doubleValue": 100},
                  "void": {"booleanValue": True}},
                 iso(T0 + dt.timedelta(days=2)), sid="s2")
    flags, pushes = {}, []

    def create(fid, fields):
        if fid in flags:
            return False
        flags[fid] = fields
        return True

    for _ in range(2):  # the same slip seen twice → one flag, one push
        referee.run(["u"], lambda uid: [late, voided], start, None, create,
                    lambda *a: pushes.append(a), name_of=lambda u: "Zane")
    assert list(flags) == ["late_bet__u__s1"]
    assert len(pushes) == 1 and pushes[0][0] == referee.COMMISSIONER
    assert "Zane" in pushes[0][2]


def test_since_skips_slips_not_written_since():
    old = doc({"legs": {"arrayValue": {"values": [fs_leg()]}},
               "stake": {"doubleValue": 100}},
              iso(T0 + dt.timedelta(days=2)))
    n = referee.run(["u"], lambda uid: [old], start, None,
                    lambda *a: True, lambda *a: None,
                    since=T0 + dt.timedelta(days=3))
    assert n == 0


def test_pushes_capped_but_every_flag_kept():
    docs = [doc({"legs": {"arrayValue": {"values": [fs_leg()]}},
                 "stake": {"doubleValue": 100}},
                iso(T0 + dt.timedelta(days=2)), sid=f"s{i}")
            for i in range(6)]
    pushes = []
    n = referee.run(["u"], lambda uid: docs, start, None, lambda *a: True,
                    lambda *a: pushes.append(a))
    assert n == 6 and len(pushes) == referee.MAX_PUSHES


def test_a_voided_slip_settles_as_a_push():
    s = fumble_alerts.parse_slip(doc(
        {"legs": {"arrayValue": {"values": [fs_leg()]}},
         "stake": {"doubleValue": 100}, "void": {"booleanValue": True}},
        iso(T0)))
    lost = {"final": True, "home": 0, "away": 30}
    assert fumble_alerts.slip_result(s, lambda gid: lost) == ("push", 100.0)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"{n}/{n} passed")
