"""A Gymnasium environment around real rogue, for reinforcement learning.

The agent gets no game knowledge: it sees the raw characters on the map area of the
screen plus the numbers from the status line, and it can only move, search, or take the
stairs. The only rogue-specific code here is interface plumbing (dismissing "-more-",
escaping prompts, noticing death) and the reward: going deeper and collecting gold are
good, dying is bad, and seeing new squares earns a small curiosity bonus.
"""

from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from .level import MAP_BOTTOM, MAP_TOP, parse_status
from .term import COLS, Terminal

ACTIONS = ["h", "j", "k", "l", "y", "u", "b", "n", "s", ">"]
ACTION_NAMES = ["west", "south", "north", "east", "north-west", "north-east",
                "south-west", "south-east", "search", "take stairs"]
MAP_ROWS = MAP_BOTTOM - MAP_TOP + 1
DEATH_MARKS = ("killed by", "died of", "top  ten")
OPTIONS_SCREEN = 'Show position only at end of run ("jump")'
HUNGER = {"": 0, "hungry": 1, "weak": 2, "faint": 3}


@dataclass
class Rewards:
    new_level: float = 10.0     # per level deeper than ever before this game
    gold: float = 0.02          # per piece of gold
    new_square: float = 0.01    # curiosity: per map square seen for the first time this level
    death: float = -10.0


def reward(before: dict, after: dict, new_squares: int, died: bool, r: Rewards = Rewards()) -> float:
    """Score change between two status snapshots (dicts with 'depth' and 'gold')."""
    total = r.new_level * max(0, after["depth"] - before["max_depth"])
    total += r.gold * max(0, after["gold"] - before["gold"])
    total += r.new_square * new_squares
    if died:
        total += r.death
    return total


class RogueEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, rogue: str = "/usr/games/rogue", max_steps: int = 3000,
                 read_wait: float = 0.1, read_idle: float = 0.015, rewards: Rewards = Rewards()):
        self.rogue, self.max_steps, self.rewards = rogue, max_steps, rewards
        self.read_wait, self.read_idle = read_wait, read_idle
        self.action_space = gym.spaces.Discrete(len(ACTIONS))
        self.observation_space = gym.spaces.Dict({
            "screen": gym.spaces.Box(0, 127, (MAP_ROWS, COLS), np.uint8),
            "status": gym.spaces.Box(0.0, 10.0, (8,), np.float32),
        })
        self.term: Terminal | None = None
        self.messages: list[str] = []

    # ---- interface plumbing (no strategy) ------------------------------------

    def _lines(self) -> list[str]:
        return self.term.lines()

    def _dead(self) -> bool:
        return not self.term.alive or any(m in line.lower() for line in self._lines() for m in DEATH_MARKS)

    def _send(self, keys: str) -> None:
        self.term.send(keys)
        self.term.pump()
        for _ in range(30):
            lines = self._lines()
            top = lines[0].rstrip()
            if self._dead():
                return
            if "-more-" in top:
                self.messages.append(top.replace("-more-", "").strip())
                self.term.send(" ")
            elif any("--press space to continue--" in line for line in lines):
                self.term.send(" ")
            elif any(OPTIONS_SCREEN in line for line in lines[:3]) or top.endswith("?"):
                self.term.send("\x1b")
            else:
                if top:
                    self.messages.append(top)
                self.messages = self.messages[-4:]
                return
            self.term.pump()

    def _cause(self) -> str:
        for line in self._lines():
            low = line.lower()
            if "killed by" in low or "died of" in low:
                return line.replace("-more-", "").strip()
        return "died"

    # ---- observations ---------------------------------------------------------

    def _status(self) -> dict:
        st = parse_status(self._lines()[23])
        if st is None:
            return dict(self.last_status)
        return {"depth": st.depth, "gold": st.gold, "hp": st.hp, "maxhp": st.maxhp,
                "str": st.str, "arm": st.arm, "xlevel": st.xlevel, "hunger": HUNGER.get(st.hunger.lower(), 0)}

    def _obs(self, status: dict) -> dict:
        rows = self._lines()[MAP_TOP:MAP_BOTTOM + 1]
        screen = np.array([[min(ord(ch), 127) for ch in row[:COLS].ljust(COLS)] for row in rows], np.uint8)
        vec = np.array([status["hp"] / max(1, status["maxhp"]), status["maxhp"] / 100,
                        status["depth"] / 26, status["str"] / 20, status["arm"] / 10,
                        status["xlevel"] / 20, status["hunger"] / 3, min(status["gold"], 5000) / 1000],
                       np.float32)
        return {"screen": screen, "status": vec}

    def _new_squares(self) -> int:
        """Map squares showing something for the first time on this level."""
        rows = self._lines()[MAP_TOP:MAP_BOTTOM + 1]
        now = {(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row[:COLS]) if ch != " "}
        fresh = now - self.seen
        self.seen |= now
        return len(fresh)

    # ---- gym API ----------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        from .cli import wait_for_fresh_seed  # rogue seeds from the clock; keep starts apart
        self.close()
        wait_for_fresh_seed()
        self.term = Terminal([self.rogue], env={"ROGUEOPTS": "noskull,fruit=mango,name=roguebot"},
                             first_wait=self.read_wait, idle=self.read_idle)
        self.term.pump(first_wait=2.0)
        self.messages = []
        self._send("")
        self.last_status = {"depth": 1, "gold": 0, "hp": 12, "maxhp": 12, "str": 16, "arm": 4,
                            "xlevel": 1, "hunger": 0}
        self.last_status = self._status()
        self.max_depth, self.steps, self.seen = self.last_status["depth"], 0, set()
        self._new_squares()
        return self._obs(self.last_status), {}

    def step(self, action: int):
        self._send(ACTIONS[int(action)])
        self.steps += 1
        died = self._dead()
        status = self.last_status if died else self._status()
        if status["depth"] != self.last_status["depth"]:
            self.seen = set()  # a new level: everything on it is new
        before = {**self.last_status, "max_depth": self.max_depth}
        r = reward(before, status, 0 if died else self._new_squares(), died, self.rewards)
        self.max_depth = max(self.max_depth, status["depth"])
        self.last_status = status
        truncated = self.steps >= self.max_steps and not died
        info = {}
        if died or truncated:
            info = {"depth": self.max_depth, "gold": status["gold"], "xlevel": status["xlevel"],
                    "steps": self.steps, "cause": self._cause() if died else f"step limit ({self.max_steps})"}
        return self._obs(status), r, died, truncated, info

    def close(self):
        if self.term:
            self.term.close()
            self.term = None
