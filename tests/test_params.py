from dataclasses import asdict

import optuna

from rogue_bot.params import Params
from rogue_bot.tune import suggest


def test_defaults_sit_inside_their_tuning_ranges():
    defaults = Params()
    for name, (low, high) in Params.ranges().items():
        value = getattr(defaults, name)
        if not isinstance(value, bool):
            assert low <= value <= high, name


def test_suggest_round_trips_through_a_trial():
    params = suggest(optuna.trial.FixedTrial(asdict(Params())))
    assert params == Params()


def test_save_and_load(tmp_path):
    path = tmp_path / "p.json"
    Params(rest_frac=0.55, wait_turns=2).save(path, source="trial 7")
    assert Params.load(path) == Params(rest_frac=0.55, wait_turns=2)
