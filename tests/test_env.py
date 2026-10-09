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


def test_fighting_and_exploring_signals():
    before = {"depth": 2, "max_depth": 2, "gold": 0, "exp": 5, "hp": 12}
    assert reward(before, {"depth": 2, "gold": 0, "exp": 8, "hp": 12}, 0, False) == pytest.approx(1.5)
    assert reward(before, {"depth": 2, "gold": 0, "exp": 5, "hp": 8}, 0, False) == pytest.approx(-0.2)
    assert reward(before, {"depth": 2, "gold": 0, "exp": 5, "hp": 12}, 0, False,
                  new_visit=True) == pytest.approx(0.05)


def test_network_handles_the_observation():
    torch = pytest.importorskip("torch")
    from rogue_bot.env import RogueEnv
    from rogue_bot.rl import ScreenNet
    space = RogueEnv().observation_space
    net = ScreenNet(space)
    batch = {k: torch.as_tensor(v.sample()[None]).float() for k, v in space.spaces.items()}
    assert net(batch).shape == (1, 256)
