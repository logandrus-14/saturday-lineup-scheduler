"""scoring.is_covering — ahead of the spread RIGHT NOW, live or final.

Sep 26 2026: the lock screen card was built from did_cover, which is None
until a game is final, so every live pick went out as "short". Same cases
as test/is_covering_test.dart on the Dart side.
"""
from scoring import did_cover, is_covering


def game(status, home=None, away=None, spread=-5.5):
    return {"homeTeam": "Texas", "awayTeam": "Tennessee", "status": status,
            "homeScore": home, "awayScore": away, "spread": spread}


def test_the_bug_texas_up_10_3_at_minus_5_5_is_covering():
    g = game("in_progress", 10, 3)
    assert is_covering(g, "Texas") is True
    assert is_covering(g, "Tennessee") is False
    assert did_cover(g, "Texas") is None  # the reason it read "short"


def test_behind_the_number_mid_game():
    g = game("in_progress", 7, 3)
    assert is_covering(g, "Texas") is False
    assert is_covering(g, "Tennessee") is True


def test_a_push_is_not_covering():
    g = game("in_progress", 14, 7, spread=-7)
    assert is_covering(g, "Texas") is False
    assert is_covering(g, "Tennessee") is False


def test_final_agrees_with_did_cover():
    for home, away in [(28, 3), (10, 7), (14, 14), (0, 21)]:
        g = game("final", home, away)
        for team in ("Texas", "Tennessee"):
            assert is_covering(g, team) == did_cover(g, team), (team, home, away)


def test_before_kickoff_and_missing_data_have_no_answer():
    assert is_covering(game("scheduled"), "Texas") is None
    assert is_covering(game("in_progress", 10, 3, spread=None), "Texas") is None
    assert is_covering(game("in_progress"), "Texas") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ok")
