import pytest

pytest.importorskip("gymnasium")

from rogue_bot.env import Rewards, condition, effect_value, item_key, kind, reward, strength

BASE = {"depth": 2, "max_depth": 2, "gold": 10, "exp": 5, "hp": 12, "maxhp": 12, "hunger": 0, "gear": 10}
R = Rewards()


def after(**changes):
    return {**{k: v for k, v in BASE.items() if k != "max_depth"}, **changes}


def shaped(state):
    """The condition-shaping part for a move that ends in `state`."""
    return R.gamma * condition(state, R) - condition(BASE, R)


def test_progress_rewards():
    assert reward(BASE, after(depth=3), {}) == pytest.approx(R.step + 3 + shaped(after()))
    assert reward(BASE, after(), {"stairs_found": True}) == pytest.approx(R.step + 1 + shaped(after()))
    assert reward(BASE, after(), {"new_rooms": 1}) == pytest.approx(R.step + 1 + shaped(after()))
    assert reward({**BASE, "max_depth": 5}, after(depth=4), {}) == pytest.approx(R.step + shaped(after()))


def test_revisiting_costs_more_the_longer_it_goes_on():
    base = reward(BASE, after(), {})
    assert reward(BASE, after(), {"revisits": 1}) == pytest.approx(base)          # second visit: free
    third = reward(BASE, after(), {"revisits": 2})
    tenth = reward(BASE, after(), {"revisits": 9})
    assert third < base and tenth < third
    assert reward(BASE, after(), {"revisits": 500}) == pytest.approx(base + R.revisit_cap)


def test_condition_shaping_rewards_healing_and_cannot_be_farmed():
    hurt = {**BASE, "hp": 6}
    heal = reward(hurt, after(hp=12), {}) - R.step
    harm = reward(BASE, after(hp=6), {}) - R.step
    assert heal > 0 > harm
    # Getting hurt and healing back, in a loop, never comes out ahead.
    assert heal + harm < 1e-9
    better_gear = reward(BASE, after(gear=12), {}) - R.step
    assert better_gear > 0


def test_eating_pays_only_when_hungry():
    hungry = {**BASE, "hunger": 1}
    assert reward(hungry, after(hunger=0), {"ate": True}) > 0
    assert reward(BASE, after(), {"ate": True}) < reward(BASE, after(), {})


def test_dying_costs_the_penalty_and_the_condition():
    assert reward(BASE, after(hp=0), {"died": True}) == pytest.approx(R.step + R.death - condition(BASE, R))


def test_fighting_and_item_rewards():
    assert reward(BASE, after(exp=8), {"kills": 1}) - reward(BASE, after(), {}) == pytest.approx(0.9 + 1.0)
    assert reward(BASE, after(), {"hurt_attack": True}) - reward(BASE, after(), {}) == pytest.approx(-0.5)
    assert reward(BASE, after(), {"pickups": 2}) - reward(BASE, after(), {}) == pytest.approx(0.6)
    assert reward(BASE, after(), {"safe_use": True, "effect": -1.0}) - reward(BASE, after(), {}) == pytest.approx(-0.8)


def test_reading_the_pack():
    assert kind("2 plaid potions") == "potion" and kind("+1 ring mail [4] being worn") == "armor"
    assert kind("a ruby ring") == "ring" and kind("a +1,+1 mace in hand") == "weapon"
    assert kind("some food") == "food" and kind("a scroll entitled: 'xyz'") == "scroll"
    assert strength("+1 ring mail [4] being worn") == 4 and strength("scale mail") == 4
    assert strength("a +1,+1 mace in hand") == pytest.approx(6.5)
    assert item_key("2 plaid potions") == item_key("a plaid potion") == "plaid potion"


def test_effect_values_follow_what_happened():
    calm = {"hp": 10, "maxhp": 20, "str": 16, "xlevel": 3, "arm": 4}
    assert effect_value(calm, {**calm, "hp": 20}, "you begin to feel better") > 0
    assert effect_value(calm, {**calm, "str": 17}, "you feel stronger") > 0
    assert effect_value(calm, calm, "oh wow, everything seems so cosmic") < 0
    assert effect_value(calm, calm, "a cloak of darkness falls around you") < 0


def test_network_handles_the_observation():
    torch = pytest.importorskip("torch")
    from rogue_bot.env import RogueEnv
    from rogue_bot.rl import ScreenNet
    space = RogueEnv().observation_space
    net = ScreenNet(space)
    batch = {k: torch.as_tensor(v.sample()[None]).float() for k, v in space.spaces.items()}
    assert net(batch).shape == (1, 256)


def test_pickups_are_counted_from_rogues_messages():
    from rogue_bot.env import RogueEnv
    env = RogueEnv()
    env.messages, env.turn_msgs, env.pickups, env.inv_dirty = [], [], 0, False
    env._note("a plaid potion (g)")
    env._note("15 pieces of gold")
    assert env.pickups == 1 and env.inv_dirty


def test_masks_allow_only_matching_items():
    import numpy as np
    from rogue_bot.env import ACTION_NAMES, RogueEnv
    env = RogueEnv()
    env.inventory = {"a": "some food", "c": "a +1,+1 mace in hand", "f": "a plaid potion"}
    allowed = {ACTION_NAMES[i] for i in np.flatnonzero(env.action_masks())}
    assert {"eat a", "quaff f", "take stairs", "search"} <= allowed
    assert "wield c" not in allowed and "quaff a" not in allowed and "read f" not in allowed
