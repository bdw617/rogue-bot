"""The numbers behind the bot's judgement, with the range the tuner may try for each."""

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .term import state_dir


def knob(default, low=None, high=None):
    """A tunable value; bools need no range."""
    return field(default=default, metadata={"range": (low, high)})


@dataclass
class Params:
    # Danger
    critical_mult: float = knob(1.0, 0.5, 2.5)     # use emergency items at hp <= worst hit * this
    flee_frac: float = knob(0.4, 0.15, 0.7)        # run for the stairs below this share of max HP
    flee_stairs_dist: int = knob(20, 5, 40)        # ...if the stairs are this close
    # Kiting (running from a fight we'd lose)
    kite_margin: float = knob(1.0, -3.0, 8.0)      # kite when cost + worst hit > hp + this
    kite_hp_frac: float = knob(0.9, 0.5, 1.0)      # only kite below this share of max HP
    kite_max_turns: int = knob(200, 20, 400)
    # Waiting for a monster to come to us
    wait_turns: int = knob(6, 0, 12)
    wait_range: int = knob(5, 2, 8)
    # Resting
    rest_frac: float = knob(0.7, 0.3, 1.0)         # rest up to this share of max HP
    rest_frac_no_food: float = knob(0.5, 0.2, 0.9)
    full_rest_xl: int = knob(3, 0, 8)              # rest to full up to this experience level
    # Ranged attacks
    bow_range: int = knob(4, 2, 7)                 # draw the bow for monsters this far away
    bow_range_danger: int = knob(3, 2, 6)          # ...or this far, if the fight looks costly
    bow_danger_frac: float = knob(0.4, 0.1, 1.0)   # "costly": expected damage > hp * this
    wand_cost_frac: float = knob(0.6, 0.2, 1.5)    # zap unknown wands at fights costlier than this
    # Items
    taste_potions: bool = knob(True)               # drink unknown potions when safe
    taste_hp_frac: float = knob(0.8, 0.4, 1.0)
    read_hp_frac: float = knob(0.6, 0.2, 1.0)      # read unknown scrolls above this share of HP
    # Exploration
    sleeper_cost_frac: float = knob(0.5, 0.1, 2.0) # leave sleepers alone if a fight costs > hp * this
    level_budget: int = knob(600, 150, 2000)       # steps on a level before heading for the stairs
    dead_end_tier: int = knob(40, 5, 80)           # searches at a dead end before moving on
    wall_tier: int = knob(15, 3, 40)               # searches along a wall before moving on

    @classmethod
    def ranges(cls) -> dict[str, tuple]:
        return {f.name: f.metadata["range"] for f in fields(cls)}

    @classmethod
    def load(cls, path: Path) -> "Params":
        known = {f.name for f in fields(cls)}
        data = json.loads(path.read_text())
        return cls(**{k: v for k, v in data.get("params", data).items() if k in known})

    def save(self, path: Path, **info) -> None:
        path.write_text(json.dumps({"params": asdict(self), **info}, indent=1))


def default_path() -> Path:
    return state_dir() / "params.json"
