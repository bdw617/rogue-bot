"""A Gymnasium environment around real rogue, for reinforcement learning.

The agent gets no game strategy. It sees the raw characters on the map (all of it, plus a
close-up centred on itself), which squares it has stood on this level, the status-line
numbers, and its pack: what kind of thing is in each slot, whether it's equipped, and for
potions and scrolls, how good the effect was the last time it used that kind in this game.
It can move, search, take the stairs, and quaff, read, wield, wear or eat any pack item.
Choices that are impossible (quaffing a sword, an empty slot) are masked out.

The only rogue-specific code is interface plumbing (dismissing "-more-", escaping prompts,
noticing death, reading the pack) and the reward, which spells out what we value (see
Rewards): going deeper, finding the stairs, exploring, kills, items, eating when hungry and
good potion and scroll effects pay; going round in circles, fighting while hurt, losing
condition (HP, hunger, gear), bad effects and dying cost.

Rogue shuffles what each potion and scroll looks like every game, so effect values are
learned per game and forgotten at reset.
"""

import re
from collections import Counter
from dataclasses import dataclass

import gymnasium as gym
import numpy as np

from .level import MAP_BOTTOM, MAP_TOP, LevelMap, find_player, parse_status
from .term import COLS, Terminal

MOVES = ["h", "j", "k", "l", "y", "u", "b", "n"]
MOVE_NAMES = ["west", "south", "north", "east", "north-west", "north-east", "south-west", "south-east"]
LETTERS = "abcdefghijklmnopqrstuvwxyz"
# Item commands and the kind of item each applies to.
ITEM_COMMANDS = {"q": "potion", "r": "scroll", "w": "weapon", "W": "armor", "e": "food"}
ITEM_VERBS = {"q": "quaff", "r": "read", "w": "wield", "W": "wear", "e": "eat"}
ACTIONS = MOVES + ["s", ">"] + [cmd + letter for cmd in ITEM_COMMANDS for letter in LETTERS]
ACTION_NAMES = MOVE_NAMES + ["search", "take stairs"] + [
    f"{ITEM_VERBS[cmd]} {letter}" for cmd in ITEM_COMMANDS for letter in LETTERS]
MAP_ROWS = MAP_BOTTOM - MAP_TOP + 1
LOCAL = (11, 21)  # close-up view centred on the agent
DEATH_MARKS = ("killed by", "died of", "top  ten")
OPTIONS_SCREEN = 'Show position only at end of run ("jump")'
HUNGER = {"": 0, "hungry": 1, "weak": 2, "faint": 3}

KINDS = ["food", "potion", "scroll", "weapon", "armor", "ring", "wand", "amulet", "other"]
ARMOR = {"leather armor": 2, "ring mail": 3, "scale mail": 4, "chain mail": 5,
         "banded mail": 6, "splint mail": 6, "plate mail": 7}
WEAPONS = {"two-handed sword": 12, "long sword": 7.5, "mace": 5, "spear": 4, "dagger": 2,
           "short bow": 1, "arrow": 1, "dart": 1, "shuriken": 1}
INV_RE = re.compile(r"(?:^|\s)([a-z])\) (.+?)\s*$")
PICKUP_RE = re.compile(r"\([a-z]\)$")
ENCHANT_RE = re.compile(r"([+-]\d+)(?:,([+-]\d+))?\s")
BRACKET_RE = re.compile(r"\[(-?\d+)\]")
# What a potion or scroll did, read from rogue's messages. (+) good, (-) bad.
GOOD_EFFECTS = ("moving much faster", "watching over you", "feel stronger", "better now",
                "much better", "welcome to level")
BAD_EFFECTS = ("cloak of darkness", "so cosmic", "feel confused", "slowing down",
               "fall asleep", "feel weaker", "humming noise", "very sick")
SLOT_FEATURES = len(KINDS) + 4  # kind one-hot, equipped, count, strength, learned effect value


def kind(desc: str) -> str:
    d = desc.lower()
    if "potion" in d:
        return "potion"
    if "scroll" in d:
        return "scroll"
    if any(w in d for w in ("food", "ration", "mango")):
        return "food"
    if any(a in d for a in ARMOR):
        return "armor"
    if "ring" in d:
        return "ring"
    if "wand" in d or "staff" in d:
        return "wand"
    if "amulet" in d:
        return "amulet"
    if any(w in d for w in WEAPONS):
        return "weapon"
    return "other"


def strength(desc: str) -> float:
    """Armor class, or average weapon damage: the base type plus any known enchantment."""
    d = desc.lower()
    m = ENCHANT_RE.search(desc)
    armor = next((v for k, v in ARMOR.items() if k in d), None)
    if armor is not None:
        b = BRACKET_RE.search(desc)
        return float(b.group(1)) if b else armor + (int(m.group(1)) if m else 0)
    weapon = next((v for k, v in WEAPONS.items() if k in d), None)
    if weapon is None:
        return 0.0
    if m and m.group(2) is not None:
        return weapon + int(m.group(2)) + 0.5 * int(m.group(1))
    return float(weapon)


def item_key(desc: str) -> str:
    """What kind of potion or scroll this is, ignoring count: '2 plaid potions' -> 'plaid potion'."""
    d = re.sub(r"^(a|an|some|\d+)\s+", "", desc.lower())
    d = d.replace("potions", "potion").replace("scrolls", "scroll")
    return re.sub(r"\s*\(.*\)$", "", d).strip()


def effect_value(before: dict, after: dict, text: str) -> float:
    """How good a potion or scroll turned out to be, from what changed and what rogue said."""
    v = 0.2 * max(0, after["hp"] - before["hp"]) + 1.0 * (after["maxhp"] - before["maxhp"])
    v += 2.0 * (after["str"] - before["str"]) + 3.0 * (after["xlevel"] - before["xlevel"])
    v += 1.0 * (after["arm"] - before["arm"])
    v += sum(1.0 for m in GOOD_EFFECTS if m in text) - sum(1.0 for m in BAD_EFFECTS if m in text)
    return v


@dataclass
class Rewards:
    """What we value, from knowing rogue. Condition (HP, hunger, gear) is potential-based
    shaping: improving it pays and worsening it costs, and a loop always nets zero, so it
    can't be farmed."""
    # Progress
    new_level: float = 3.0       # each level deeper than before this game
    stairs_found: float = 1.0    # first sight of the stairs on a level: the real sub-goal
    new_room: float = 1.0        # each room exposed for the first time on a level
    new_square: float = 0.01     # each map square seen for the first time on a level
    new_visit: float = 0.05      # stepping onto a square for the first time on a level
    revisit: float = -0.01       # per earlier visit, stepping onto a square for the 3rd+ time
    revisit_cap: float = -0.1    # ...but never worse than this per step
    step: float = -0.002         # every action: time is food
    # Fighting
    exp: float = 0.3             # per experience point
    kill: float = 1.0            # per monster killed
    hurt_attack: float = -0.5    # starting a fight below hurt_below of max HP
    hurt_below: float = 0.75
    # Condition potential: hp_weight * HP share - hunger_weight * hunger + gear_weight * gear
    hp_weight: float = 2.0
    hunger_weight: float = 0.5
    gear_weight: float = 0.5
    gamma: float = 0.995         # must match the learner's discount for exact shaping
    # Items
    gold: float = 0.01           # per piece of gold
    pickup: float = 0.3          # per item picked up
    eat_hungry: float = 0.5      # eating when hungry
    eat_full: float = -0.5       # eating when not hungry wastes scarce food
    safe_use: float = 0.2        # drinking or reading with no monster in sight
    effect: float = 1.0          # times how good the potion or scroll's effect was
    death: float = -10.0


def condition(state: dict, r: Rewards) -> float:
    """How well-off we are: HP share, hunger level (0-3), equipped gear strength."""
    return (r.hp_weight * state.get("hp", 0) / max(1, state.get("maxhp", 1))
            - r.hunger_weight * state.get("hunger", 0) + r.gear_weight * state.get("gear", 0))


def reward(before: dict, after: dict, events: dict, r: Rewards = Rewards()) -> float:
    """Reward for one move: status before/after plus what happened (see Rewards)."""
    e = events.get
    total = r.step
    total += r.new_level * max(0, after["depth"] - before["max_depth"])
    total += r.gold * max(0, after["gold"] - before["gold"])
    total += r.exp * max(0, after.get("exp", 0) - before.get("exp", 0))
    total += r.new_square * e("new_squares", 0) + r.new_visit * e("new_visit", 0)
    if e("revisits", 0) >= 2:
        total += max(r.revisit_cap, r.revisit * (e("revisits") - 1))
    total += r.new_room * e("new_rooms", 0) + r.stairs_found * e("stairs_found", 0)
    total += r.kill * e("kills", 0) + r.hurt_attack * e("hurt_attack", 0)
    total += r.pickup * e("pickups", 0)
    if e("ate"):
        total += r.eat_hungry if before.get("hunger", 0) > 0 else r.eat_full
    total += r.safe_use * e("safe_use", 0) + r.effect * e("effect", 0)
    # Condition shaping; after death there is no condition left to have.
    total += (0.0 if e("died") else r.gamma * condition(after, r)) - condition(before, r)
    if e("died"):
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
            "local": gym.spaces.Box(0, 127, LOCAL, np.uint8),
            "visits": gym.spaces.Box(0, 10, (MAP_ROWS, COLS), np.uint8),
            "status": gym.spaces.Box(0.0, 10.0, (8,), np.float32),
            "pack": gym.spaces.Box(-10.0, 10.0, (len(LETTERS), SLOT_FEATURES), np.float32),
        })
        self.term: Terminal | None = None
        self.messages: list[str] = []
        self.turn_msgs: list[str] = []
        self.inventory: dict[str, str] = {}

    # ---- interface plumbing (no strategy) ------------------------------------

    def _lines(self) -> list[str]:
        return self.term.lines()

    def _dead(self) -> bool:
        return not self.term.alive or any(m in line.lower() for line in self._lines() for m in DEATH_MARKS)

    def _note(self, msg: str) -> None:
        msg = msg.strip()
        if msg:
            self.messages = (self.messages + [msg])[-4:]
            self.turn_msgs.append(msg)
            if PICKUP_RE.search(msg):
                self.pickups += 1
                self.inv_dirty = True

    def _settle(self) -> None:
        for _ in range(30):
            lines = self._lines()
            top = lines[0].rstrip()
            if self._dead():
                return
            if "-more-" in top:
                self._note(top.replace("-more-", ""))
                self.term.send(" ")
            elif any("--press space to continue--" in line for line in lines):
                self.term.send(" ")
            elif any(OPTIONS_SCREEN in line for line in lines[:3]) or top.endswith("?"):
                self.term.send("\x1b")
            else:
                self._note(top)
                return
            self.term.pump()

    def _send(self, keys: str) -> None:
        self.term.send(keys)
        self.term.pump()
        self._settle()

    def _use(self, cmd: str, letter: str) -> None:
        """Answer the item prompt only if rogue actually asks, so the letter can't become a command."""
        self._settle()
        if cmd == "W" and any("being worn" in d for d in self.inventory.values()):
            self._send("T")  # wearing something new means taking the old armor off first
        self.term.send(cmd)
        self.term.pump()
        if self._lines()[0].rstrip().endswith("?"):
            self.term.send(letter)
            self.term.pump()
        self._settle()
        self.inv_dirty = True

    def _read_inventory(self) -> None:
        self.term.send("i")
        self.term.pump()
        items = {}
        for _ in range(3):
            lines = self._lines()
            for line in lines[:23]:
                m = INV_RE.search(line)
                if m:
                    items[m.group(1)] = m.group(2)
            if not any("--press space" in line for line in lines):
                break
            self.term.send(" ")
            self.term.pump()
        if any(INV_RE.search(line) for line in self._lines()[:23]):
            self.term.send("\x1b")  # only if the list is still up: a stray Escape is a command
            self.term.pump()
        self.inventory, self.inv_dirty = items, False

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
        return {"depth": st.depth, "gold": st.gold, "hp": st.hp, "maxhp": st.maxhp, "str": st.str,
                "arm": st.arm, "xlevel": st.xlevel, "exp": st.exp,
                "hunger": HUNGER.get(st.hunger.lower(), 0)}

    def _pack(self) -> np.ndarray:
        pack = np.zeros((len(LETTERS), SLOT_FEATURES), np.float32)
        for letter, desc in self.inventory.items():
            row = pack[LETTERS.index(letter)]
            row[KINDS.index(kind(desc))] = 1
            row[len(KINDS)] = "in hand" in desc or "being worn" in desc or "hand)" in desc
            count = re.match(r"(\d+)\s", desc)
            row[len(KINDS) + 1] = min(int(count.group(1)) if count else 1, 50) / 10
            row[len(KINDS) + 2] = strength(desc) / 5
            row[len(KINDS) + 3] = max(-10.0, min(10.0, self.effects.get(item_key(desc), 0.0)))
        return pack

    def _obs(self, status: dict) -> dict:
        rows = self._lines()[MAP_TOP:MAP_BOTTOM + 1]
        screen = np.array([[min(ord(ch), 127) for ch in row[:COLS].ljust(COLS)] for row in rows], np.uint8)
        # Close-up centred on the agent; off-map is blank.
        padded = np.full((MAP_ROWS + LOCAL[0], COLS + LOCAL[1]), 32, np.uint8)
        padded[LOCAL[0] // 2:LOCAL[0] // 2 + MAP_ROWS, LOCAL[1] // 2:LOCAL[1] // 2 + COLS] = screen
        r, c = self.pos if self.pos else (MAP_ROWS // 2, COLS // 2)
        local = padded[r:r + LOCAL[0], c:c + LOCAL[1]]
        visits = np.zeros((MAP_ROWS, COLS), np.uint8)
        for (vr, vc), n in self.visits.items():
            visits[vr, vc] = min(n, 10)
        vec = np.array([status["hp"] / max(1, status["maxhp"]), status["maxhp"] / 100,
                        status["depth"] / 26, status["str"] / 20, status["arm"] / 10,
                        status["xlevel"] / 20, status["hunger"] / 3, min(status["gold"], 5000) / 1000],
                       np.float32)
        return {"screen": screen, "local": local, "visits": visits, "status": vec, "pack": self._pack()}

    def action_masks(self) -> np.ndarray:
        """Which actions are possible right now: item commands only for a matching item."""
        mask = np.zeros(len(ACTIONS), bool)
        mask[:len(MOVES) + 2] = True
        for i, (cmd, want) in enumerate(ITEM_COMMANDS.items()):
            for j, letter in enumerate(LETTERS):
                desc = self.inventory.get(letter)
                if desc and kind(desc) == want and "in hand" not in desc and "being worn" not in desc:
                    mask[len(MOVES) + 2 + i * len(LETTERS) + j] = True
        return mask

    def _monsters(self) -> list[tuple[int, int]]:
        rows = self._lines()[MAP_TOP:MAP_BOTTOM + 1]
        return [(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row[:COLS]) if ch.isupper()]

    def _nearest_monster(self) -> int:
        if not self.pos:
            return 99
        return min((max(abs(r - self.pos[0]), abs(c - self.pos[1])) for r, c in self._monsters()),
                   default=99)

    def _track_position(self) -> tuple[bool, int]:
        """Note where the agent stands. Returns (moved, visits to this square before now)."""
        found = find_player(self._lines())
        if not found:
            return False, 0
        pos = (found[0] - MAP_TOP, found[1])
        moved, self.pos = pos != self.pos, pos
        before = self.visits[pos]
        self.visits[pos] += 1
        return moved, before

    def _new_squares(self) -> int:
        """Map squares showing something for the first time on this level."""
        rows = self._lines()[MAP_TOP:MAP_BOTTOM + 1]
        now = {(r, c) for r, row in enumerate(rows) for c, ch in enumerate(row[:COLS]) if ch != " "}
        fresh = now - self.seen
        self.seen |= now
        return len(fresh)

    def _rooms(self) -> int:
        """Rooms exposed so far on this level (remembered, since rogue hides rooms you leave)."""
        found = find_player(self._lines())
        if found:
            self.level.update(self._lines(), found)
        return self.level.rooms_seen()

    def _gear(self) -> float:
        """Strength of what's equipped: armor class plus the wielded weapon's damage."""
        return sum(strength(d) for d in self.inventory.values() if "in hand" in d or "being worn" in d)

    # ---- gym API ----------------------------------------------------------------

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        from .cli import wait_for_fresh_seed  # rogue seeds from the clock; keep starts apart
        self.close()
        wait_for_fresh_seed()
        self.term = Terminal([self.rogue], env={"ROGUEOPTS": "noskull,fruit=mango,name=roguebot"},
                             first_wait=self.read_wait, idle=self.read_idle)
        self.term.pump(first_wait=2.0)
        self.messages, self.turn_msgs, self.pickups, self.inv_dirty = [], [], 0, False
        self._settle()
        self.last_status = {"depth": 1, "gold": 0, "hp": 12, "maxhp": 12, "str": 16, "arm": 4,
                            "xlevel": 1, "exp": 0, "hunger": 0}
        self.last_status = self._status()
        self.max_depth, self.steps, self.seen = self.last_status["depth"], 0, set()
        self.visits: Counter = Counter()
        self.pos = None
        self.fighting_until = 0  # a swing within the last few moves means we're mid-fight
        self.level = LevelMap()
        self.effects: dict[str, float] = {}  # potion/scroll kind -> how good it was, this game only
        self._read_inventory()
        self.last_status["gear"] = self._gear()
        self.stairs_seen = False
        self._track_position()
        self._new_squares()
        self.rooms = self._rooms()
        return self._obs(self.last_status), {}

    def step(self, action: int):
        key = ACTIONS[int(action)]
        near_before = self._nearest_monster()
        self.turn_msgs, self.pickups = [], 0
        events = {}
        if len(key) == 2:  # an item command: quaff/read/wield/wear/eat + slot letter
            cmd, letter = key[0], key[1]
            used = self.inventory.get(letter, "")
            self._use(cmd, letter)
        else:
            cmd, used = None, ""
            self._send(key)
        self.steps += 1
        died = self._dead()
        status = self.last_status if died else self._status()
        if status["depth"] != self.last_status["depth"]:
            # A new level: everything on it is new.
            self.seen, self.visits, self.level, self.rooms = set(), Counter(), LevelMap(), 0
            self.stairs_seen = False
        events["died"] = died
        text = "  ".join(self.turn_msgs).lower()
        if not died:
            moved, before_visits = self._track_position()
            events["new_visit"] = moved and before_visits == 0
            events["revisits"] = before_visits if moved else 0
            if not self.stairs_seen and any("%" in row for row in self._lines()[MAP_TOP:MAP_BOTTOM + 1]):
                self.stairs_seen = events["stairs_found"] = True
            events["new_squares"] = self._new_squares()
            rooms = self._rooms()
            events["new_rooms"], self.rooms = max(0, rooms - self.rooms), max(rooms, self.rooms)
            if self.inv_dirty:
                self._read_inventory()
            status = {**status, "gear": self._gear()}
            events["ate"] = cmd == "e"
            if cmd in ("q", "r") and used:
                value = effect_value(self.last_status, status, text)
                self.effects[item_key(used)] = value
                # Once identified, rogue renames the rest of the stack: remember that name too.
                if letter in self.inventory:
                    self.effects[item_key(self.inventory[letter])] = value
                events["effect"] = value
                events["safe_use"] = near_before == 99
        events["pickups"] = self.pickups
        swung = "you hit" in text or "you miss" in text or "defeated" in text
        events["kills"] = text.count("defeated")
        hurt = self.last_status["hp"] < self.last_status["maxhp"] * self.rewards.hurt_below
        events["hurt_attack"] = swung and hurt and self.steps > self.fighting_until
        if swung:
            self.fighting_until = self.steps + 3
        before = {**self.last_status, "max_depth": self.max_depth}
        r = reward(before, status, events, self.rewards)
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
