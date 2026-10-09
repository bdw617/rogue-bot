"""Monster book: what the bot has learned about each monster from its own fights.

Built only from what a player sees (combat messages and HP changes), and kept
across games so later games fight smarter than earlier ones.
"""

import fcntl
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# Monster letters in this rogue (bsdgames rogue-clone).
NAMES = {
    "A": "aquator", "B": "bat", "C": "centaur", "D": "dragon", "E": "emu",
    "F": "venus fly-trap", "G": "griffin", "H": "hobgoblin", "I": "ice monster",
    "J": "jabberwock", "K": "kestrel", "L": "leprechaun", "M": "medusa",
    "N": "nymph", "O": "orc", "P": "phantom", "Q": "quagga", "R": "rattlesnake",
    "S": "snake", "T": "troll", "U": "black unicorn", "V": "vampire",
    "W": "wraith", "X": "xeroc", "Y": "yeti", "Z": "zombie",
}
LETTERS = {name: letter for letter, name in NAMES.items()}
_NAME_RE = "|".join(sorted(map(re.escape, LETTERS), key=len, reverse=True))
ATTACK_RE = re.compile(rf"the ({_NAME_RE}) (hit|misses)")
KILL_RE = re.compile(rf"defeated the ({_NAME_RE})")
YOU_RE = re.compile(r"\byou (hit|miss)\b")

DEFAULT_PATH = Path(os.environ.get("ROGUE_BOT_BOOK", Path.home() / ".local/share/rogue-bot/monsters.json"))


@dataclass
class Record:
    attacks: int = 0      # its swings at us
    hits: int = 0
    damage: int = 0       # total damage from hits we could attribute
    damaged_hits: int = 0
    max_hit: int = 0
    swings: int = 0       # our swings at it
    kills: int = 0
    kill_swings: int = 0  # our swings summed over fights we won
    deaths: int = 0       # games it ended

    def merge(self, other: "Record") -> None:
        for f in fields(self):
            if f.name == "max_hit":
                self.max_hit = max(self.max_hit, other.max_hit)
            else:
                setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))


class MonsterBook:
    def __init__(self, path: Path | None = DEFAULT_PATH):
        self.path = path
        self.known: dict[str, Record] = {}
        self.session: dict[str, Record] = {}  # this game's additions, merged on save
        self.fight_swings: dict[str, int] = {}
        if path and path.exists():
            self.known = {k: Record(**v) for k, v in json.loads(path.read_text()).items()}

    def _rec(self, book: dict[str, Record], name: str) -> Record:
        return book.setdefault(name, Record())

    def _both(self, name: str):
        return self._rec(self.known, name), self._rec(self.session, name)

    def observe(self, messages: list[str], hp_drop: int, target: str | None) -> None:
        """Record one turn: the messages it printed and how much HP we lost."""
        text = "  ".join(messages)
        hitters = []
        for name, verb in ATTACK_RE.findall(text):
            for r in self._both(name):
                r.attacks += 1
                r.hits += verb == "hit"
            if verb == "hit":
                hitters.append(name)
        if len(hitters) == 1 and hp_drop > 0:
            for r in self._both(hitters[0]):
                r.damage += hp_drop
                r.damaged_hits += 1
                r.max_hit = max(r.max_hit, hp_drop)
        if target:
            swings = len(YOU_RE.findall(text))
            for r in self._both(target):
                r.swings += swings
            self.fight_swings[target] = self.fight_swings.get(target, 0) + swings
        for name in KILL_RE.findall(text):
            for r in self._both(name):
                r.kills += 1
                # The killing blow prints "defeated", not "you hit".
                r.kill_swings += self.fight_swings.get(name, 0) + 1
            self.fight_swings.pop(name, None)

    def died_to(self, cause: str) -> None:
        low = cause.lower()
        for name in sorted(LETTERS, key=len, reverse=True):
            if f"by a {name}" in low or f"by an {name}" in low:
                for r in self._both(name):
                    r.deaths += 1
                return

    def threat(self, letter: str, depth: int) -> tuple[float, int]:
        """(expected damage per monster turn, worst single hit) with priors for unseen monsters."""
        r = self.known.get(NAMES.get(letter, ""), Record())
        prior_hit = 2 + depth          # deeper monsters hit harder
        hit_rate = (r.hits + 3) / (r.attacks + 5)
        avg = (r.damage + 2 * prior_hit) / (r.damaged_hits + 2)
        worst = max(r.max_hit, prior_hit if r.damaged_hits < 3 else 0)
        return hit_rate * avg, worst

    def swings_to_kill(self, letter: str, depth: int) -> float:
        r = self.known.get(NAMES.get(letter, ""), Record())
        return (r.kill_swings + 4 + depth) / (r.kills + 1)

    def save(self) -> None:
        """Merge this game's observations into the shared file (safe with parallel games)."""
        if not self.path or not self.session:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            on_disk = {}
            if self.path.exists():
                on_disk = {k: Record(**v) for k, v in json.loads(self.path.read_text()).items()}
            for name, rec in self.session.items():
                on_disk.setdefault(name, Record()).merge(rec)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: asdict(v) for k, v in sorted(on_disk.items())}, indent=1))
            tmp.replace(self.path)
        self.known, self.session = on_disk, {}
