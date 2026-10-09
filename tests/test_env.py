import pytest

pytest.importorskip("gymnasium")

from rogue_bot.env import Rewards, reward


def test_reward_counts_new_depth_gold_curiosity_and_death():
    before = {"depth": 2, "max_depth": 2, "gold": 10}
    assert reward(before, {"depth": 3, "gold": 10}, 0, False) == 10.0
    assert reward(before, {"depth": 2, "gold": 60}, 0, False) == pytest.approx(1.0)
    assert reward(before, {"depth": 2, "gold": 10}, 50, False) == pytest.approx(0.5)
    assert reward(before, {"depth": 2, "gold": 10}, 0, True) == -10.0


def test_revisiting_a_shallower_depth_earns_nothing():
    before = {"depth": 3, "max_depth": 5, "gold": 0}
    assert reward(before, {"depth": 4, "gold": 0}, 0, False, Rewards()) == 0.0
