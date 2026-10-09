import pytest

pytest.importorskip("gymnasium")

from rogue_bot.env import Rewards, reward

BASE = {"depth": 2, "max_depth": 2, "gold": 10, "exp": 5, "hp": 12}


def after(**changes):
    return {**{k: v for k, v in BASE.items() if k != "max_depth"}, **changes}


def test_score_rewards():
    assert reward(BASE, after(depth=3), {}) == 10.0
    assert reward(BASE, after(gold=60), {}) == pytest.approx(1.0)
    assert reward(BASE, after(), {"died": True}) == -10.0
    assert reward({**BASE, "max_depth": 5}, after(depth=4), {}) == 0.0   # no credit for going back


def test_exploring_rewards():
    assert reward(BASE, after(), {"new_squares": 50}) == pytest.approx(0.5)
    assert reward(BASE, after(), {"new_visit": True}) == pytest.approx(0.1)
    assert reward(BASE, after(), {"new_rooms": 1}) == pytest.approx(2.0)


def test_fighting_rewards():
    assert reward(BASE, after(exp=8), {"kills": 1}) == pytest.approx(1.5 + 2.0)
    assert reward(BASE, after(hp=8), {}) == pytest.approx(-0.2)
    assert reward(BASE, after(), {"kited": True}) == pytest.approx(0.2)
    assert reward(BASE, after(), {"hurt_attack": True}) == pytest.approx(-1.0)
    assert Rewards().hurt_attack < 0 < Rewards().kite
def test_network_handles_the_observation():
    torch = pytest.importorskip("torch")
    from rogue_bot.env import RogueEnv
    from rogue_bot.rl import ScreenNet
    space = RogueEnv().observation_space
    net = ScreenNet(space)
    batch = {k: torch.as_tensor(v.sample()[None]).float() for k, v in space.spaces.items()}
    assert net(batch).shape == (1, 256)
