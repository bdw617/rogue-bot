"""Decision loop: read the screen, pick one action, send it."""

import re
import time
from collections import deque
from dataclasses import dataclass, field

from .knowledge import NAMES, MonsterBook
from .params import Params
from .level import (DIRS, LevelMap, Pos, Status, direction, find_player, neighbors8,
                    parse_status, step)
from .term import Terminal

INV_RE = re.compile(r"(?:^|\s)([a-z])\) (.+?)\s*$")
PICKUP_RE = re.compile(r"\([a-z]\)$")
# Base armor class by type; the game showed "+1 ring mail [4]", so ring mail is 3.
ARMOR = {"leather armor": 2, "ring mail": 3, "scale mail": 4, "chain mail": 5,
         "banded mail": 6, "splint mail": 6, "plate mail": 7}
ENCHANT_RE = re.compile(r"([+-]\d+)(?:,([+-]\d+))?\s")
BRACKET_RE = re.compile(r"\[(-?\d+)\]")
SAFE_SCROLLS = ("enchant", "protect armor", "remove curse", "magic mapping", "identify")
DEATH_MARKS = ("killed by", "died of", "top  ten")
FOOD = ("food", "ration", "mango")
OPTIONS_SCREEN = 'Show position only at end of run ("jump")'
# Ice monster freezes (can kill outright); flytrap is stationary and holds you.
AVOID = "IF"
# Average melee damage by weapon base name.
MELEE = {"two-handed sword": 12, "long sword": 7.5, "mace": 5, "dagger": 2}
# Status effects rogue announces when they start and end (matched lowercase).
EFFECTS = {
    "blind": ("a cloak of darkness falls around you", "the veil of darkness lifts"),
    "hallucinating": ("everything seems so cosmic", "everything looks so boring now"),
    "levitating": ("you start to float in the air", "you float gently to the ground"),
}


def dist(a: Pos, b: Pos) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


@dataclass
class Result:
    depth: int = 0
    gold: int = 0
    xlevel: int = 0
    steps: int = 0
    cause: str = ""


@dataclass
class Item:
    letter: str
    desc: str

    def has(self, *words) -> bool:
        return any(w in self.desc for w in words)

    @property
    def unknown(self) -> bool:
        return " of " not in self.desc and "called" not in self.desc

    def armor_class(self) -> int | None:
        """Shown as [n] once known; otherwise base type plus any known enchantment."""
        base = next((v for k, v in ARMOR.items() if k in self.desc), None)
        if base is None:
            return None
        if m := BRACKET_RE.search(self.desc):
            return int(m.group(1))
        m = ENCHANT_RE.search(self.desc)
        return base + (int(m.group(1)) if m else 0)

    def weapon_value(self) -> float | None:
        """Average damage: base type, plus known damage enchantment, plus half the hit bonus."""
        base = next((v for k, v in MELEE.items() if k in self.desc), None)
        if base is None:
            return None
        m = ENCHANT_RE.search(self.desc)
        if not m or m.group(2) is None:
            return base
        return base + int(m.group(2)) + 0.5 * int(m.group(1))


@dataclass
class Bot:
    term: Terminal
    render: callable = None
    delay: float = 0.0
    max_steps: int = 30000
    trace: object = None
    book: MonsterBook = field(default_factory=lambda: MonsterBook(path=None))
    params: Params = field(default_factory=Params)
    result: Result = field(default_factory=Result)
    note: str = ""
    messages: deque = field(default_factory=lambda: deque(maxlen=4))
    inv: list[Item] = field(default_factory=list)
    trail: deque = field(default_factory=lambda: deque(maxlen=40))
    target: Pos | None = None

    def __post_init__(self):
        self.map = self.new_map()
        self.depth = 0
        self.level_steps = 0
        self.effects: set[str] = set()
        self.turn_msgs: list[str] = []
        self.fighting: str | None = None    # monster name we swung at this turn
        self.prev_monsters: dict[Pos, str] = {}
        self.still: dict[Pos, int] = {}     # turns each visible monster has not moved
        self.approaching: set[Pos] = set()
        self.last_hp: int | None = None
        self.inv_dirty = False
        self.waited = 0
        self.backed_off = 0
        self.unseen_turns = 0                # keep fighting an unseen attacker this many turns
        self.unseen_dir: str | None = None   # direction we last connected with it
        self.unseen_tried: set[str] = set()
        self.calm = 0
        self.tried_armor: set[str] = set()
        self.bad_weapons: set[str] = set()
        self.tried_wands: dict[str, int] = {}
        self.weapon_stuck = False            # a cursed weapon in hand blocks swapping
        self.armor_stuck = False             # cursed armor can't be taken off
        self.repeats = 0                     # steps the bot has made no visible progress
        self.last_sig = None
        self.items_off_until = 0             # watchdog: skip item actions until this step
        self.kiting = 0                      # turns spent running from the current fight

    # ---- terminal plumbing -------------------------------------------------

    def lines(self) -> list[str]:
        return self.term.lines()

    def dead(self) -> bool:
        if not self.term.alive:
            return True
        return any(m in line.lower() for line in self.lines() for m in DEATH_MARKS)

    def settle(self, expect_prompt: bool = False) -> None:
        """Dismiss -more- and overlays; escape stray prompts."""
        for _ in range(50):
            lines = self.lines()
            top = lines[0].rstrip()
            if any(m in line.lower() for line in lines for m in DEATH_MARKS):
                self.note_death(lines)
                return
            if "-more-" in top:
                self.log(top.replace("-more-", ""))
                self.send(" ", settle=False)
            elif any("--press space to continue--" in line for line in lines):
                self.send(" ", settle=False)
            elif any(OPTIONS_SCREEN in line for line in lines[:3]):
                self.send("\x1b", settle=False)  # rogue's options screen: get out of it
            elif top.endswith("?") and not expect_prompt:
                self.send("\x1b", settle=False)
            else:
                if top:
                    self.log(top)
                return

    def send(self, keys: str, settle: bool = True, expect_prompt: bool = False) -> None:
        self.term.send(keys)
        self.term.pump()
        if settle:
            self.settle(expect_prompt)

    def log(self, msg: str) -> None:
        msg = msg.strip()
        if not msg:
            return
        if not self.messages or self.messages[-1] != msg:
            self.messages.append(msg)
        # The same line can be re-read by several settles within one turn.
        if not self.turn_msgs or self.turn_msgs[-1] != msg:
            self.turn_msgs.append(msg)
        if PICKUP_RE.search(msg):
            self.inv_dirty = True
        low = msg.lower()
        if "cursed" in low and "can't" in low:
            self.weapon_stuck = True
        if "watching over you" in low:  # remove curse
            self.weapon_stuck = False
            self.bad_weapons.clear()
        for effect, (start, end) in EFFECTS.items():
            if start in low:
                self.effects.add(effect)
            if end in low:
                self.effects.discard(effect)

    def note_death(self, lines: list[str]) -> None:
        if self.result.cause:
            return
        for line in lines:
            low = line.lower()
            if "top  ten" in low:
                return
            if "killed by" in low or "died of" in low:
                self.result.cause = line.strip().replace("-more-", "").strip()
                return

    def show(self) -> None:
        if self.render:
            self.render(self)
        if self.delay:
            time.sleep(self.delay)

    # ---- inventory -----------------------------------------------------------

    def refresh_inventory(self) -> None:
        self.inv_dirty = False
        self.term.send("i")
        self.term.pump()
        items = []
        for _ in range(3):
            lines = self.lines()
            for line in lines[: 23]:
                m = INV_RE.search(line)
                if m:
                    items.append(Item(m.group(1), m.group(2)))
            if not any("--press space" in line for line in lines):
                break
            self.send(" ", settle=False)
        self.settle()
        if items:
            self.inv = items

    def find(self, pred) -> Item | None:
        return next((i for i in self.inv if pred(i)), None)

    def use(self, cmd: str, item: Item, extra: str = "") -> None:
        # A pending message would swallow the command key and turn the item letter into a
        # command of its own ("o" opens the options screen), so clear it, and answer only
        # prompts that actually appear.
        self.settle()
        self.term.send(cmd)
        self.term.pump()
        for key in ([extra] if cmd in "zt" else []) + [item.letter]:
            if not self.lines()[0].rstrip().endswith("?"):
                self.send("\x1b")
                return
            self.term.send(key)
            self.term.pump()
        if "identify" in self.lines()[0]:
            target = self.find(lambda i: i.has("potion", "scroll", "wand", "staff", "ring")
                               and i.unknown and i.letter != item.letter)
            self.term.send(target.letter if target else "\x1b")
            self.term.pump()
        self.settle()
        self.refresh_inventory()

    # ---- main loop -----------------------------------------------------------

    def run(self) -> Result:
        self.term.pump(first_wait=2.0)
        self.settle()
        self.refresh_inventory()
        if self.trace:
            self.trace.write("# new game\n")
        steps = 0
        while steps < self.max_steps and not self.dead():
            lines = self.lines()
            st = parse_status(lines[23])
            pos = find_player(lines)
            if st is None or pos is None:
                self.send("\x1b")
                steps += 1
                continue
            self.result.depth = max(self.result.depth, st.depth)
            self.result.gold, self.result.xlevel = st.gold, st.xlevel
            if st.depth != self.depth:
                self.depth, self.map, self.level_steps = st.depth, self.new_map(), 0
                self.backed_off = 0
                self.trail.clear()
                self.prev_monsters, self.still = {}, {}
                self.book.fight_swings.clear()
            if not self.trail or self.trail[-1] != pos:
                self.trail.append(pos)
            self.target = None
            hp_drop = max(0, (self.last_hp or st.hp) - st.hp)
            self.last_hp = st.hp
            self.book.observe(self.turn_msgs, hp_drop, self.fighting)
            self.turn_msgs, self.fighting = [], None
            monsters = self.map.update(lines, pos, "hallucinating" in self.effects)
            self.track(monsters, lines, pos)
            if self.inv_dirty:
                self.refresh_inventory()
            self.act(st, pos, monsters, hp_drop)
            self.watchdog(pos, steps)
            if self.trace and self.level_steps == 800:
                self.dump_map(pos)
            if self.trace:
                food = sum(i.has(*FOOD) for i in self.inv)
                self.trace.write(f"{steps}\td{st.depth}\t{pos}\thp {st.hp}/{st.maxhp}\t{st.hunger or '-'}"
                                 f"\tfood {food}\tlvl {self.level_steps}\t{self.note}\n")
            steps += 1
            self.level_steps += 1
            self.result.steps = steps
            self.show()
        if not self.dead():
            self.result.cause = f"step limit ({self.max_steps})"
            self.send("Qy")
        else:
            self.note_death(self.lines())
        self.book.observe(self.turn_msgs, 0, self.fighting)
        self.book.died_to(self.result.cause)
        if self.trace:
            self.trace.write(f"# end: {self.result.cause}\n")
        self.show()
        return self.result

    def new_map(self) -> LevelMap:
        return LevelMap(dead_end_tier=self.params.dead_end_tier, wall_tier=self.params.wall_tier)

    def watchdog(self, pos: Pos, steps: int) -> None:
        """Break loops where an action silently fails and no game time passes."""
        sig = (pos, self.note, tuple(self.lines()))
        waiting = self.note.startswith(("rest", "blind", "search", "levitating", "wait"))
        self.repeats = self.repeats + 1 if sig == self.last_sig and not waiting else 0
        self.last_sig = sig
        if self.repeats >= 30:
            self.repeats = 0
            self.items_off_until = steps + 150
            self.note = f"watchdog: '{self.note}' was going nowhere; items off for a while"
            self.send("s")

    def items_ok(self) -> bool:
        return self.result.steps >= self.items_off_until

    def dump_map(self, pos: Pos) -> None:
        """Write the remembered map to the trace: S/s = searched a lot/a little, v = visited."""
        m = self.map
        self.trace.write(f"# map at depth {self.depth}, stairs {m.stairs()}, effects {sorted(self.effects)}\n")
        for r in range(1, 23):
            row = ""
            for c in range(80):
                k = m.searches.get((r, c), 0)
                row += ("@" if (r, c) == pos else "S" if k >= 30 else "s" if k else
                        "v" if (r, c) in m.visited and m.terrain[r][c] == "." else m.terrain[r][c])
            self.trace.write(f"# {r:2d} {row.rstrip()}\n")

    def track(self, monsters: list[Pos], lines: list[str], pos: Pos) -> None:
        """Note which monsters are sitting still (often asleep) and which are closing in."""
        current = {p: lines[p[0]][p[1]] for p in monsters}
        self.still = {p: self.still.get(p, 0) + 1 if self.prev_monsters.get(p) == ch else 0
                      for p, ch in current.items()}
        self.approaching = set()
        for p, ch in current.items():
            before = [q for q, c in self.prev_monsters.items() if c == ch and dist(q, p) <= 2]
            if before and min(dist(q, pos) for q in before) > dist(p, pos):
                self.approaching.add(p)
        self.prev_monsters = current

    # ---- judgement -----------------------------------------------------------

    def worst_hit(self, letter: str, depth: int) -> int:
        return self.book.threat(letter, depth)[1]

    def fight_cost(self, letter: str, depth: int) -> float:
        """Expected damage we take killing this monster, from experience."""
        rate, _ = self.book.threat(letter, depth)
        landed = self.book.fight_swings.get(NAMES.get(letter, ""), 0)
        return rate * max(1.0, self.book.swings_to_kill(letter, depth) - landed)

    # ---- decisions -----------------------------------------------------------

    def act(self, st: Status, pos: Pos, monsters: list[Pos], hp_drop: int) -> None:
        m, P = self.map, self.params
        lines = self.lines()
        chars = {p: lines[p[0]][p[1]] for p in monsters}
        adjacent = [p for p in monsters
                    if dist(p, pos) == 1 and m.can_step(pos, p, ignore_blocked=True)]
        food = self.find(lambda i: i.has(*FOOD))
        hungry = st.hunger.lower() in ("hungry", "weak", "faint")
        # Batched searches aren't interrupted by attacks, so only batch when it's been quiet.
        self.calm = 0 if (hp_drop or monsters) else self.calm + 1
        if self.calm > 10:
            self.book.fight_swings.clear()  # whatever we were fighting is gone

        if hungry and food:
            self.note = f"eat {food.desc}"
            return self.use("e", food)

        if hp_drop and not monsters:
            self.unseen_turns = 6  # something we can't see is hitting us
        if self.unseen_turns and not monsters:
            self.unseen_turns -= 1
            if (st.hp <= max(4, st.maxhp * P.flee_frac) and self.items_ok()
                    and self.emergency(pos, None)):
                return
            return self.fight_unseen(pos)
        self.unseen_turns, self.unseen_dir = 0, None

        worst = max((self.worst_hit(chars[p], st.depth) for p in adjacent), default=0)
        # One more worst-case hit could kill us.
        critical = adjacent and st.hp <= max(3, worst * P.critical_mult)
        if critical and self.items_ok() and self.emergency(pos, adjacent[0]):
            return
        losing = [p for p in monsters if p in self.approaching or p in adjacent]
        if (critical or st.hp <= st.maxhp * P.flee_frac) and losing and self.flee_downstairs(pos, monsters):
            return

        scary = {p for p in monsters if chars[p] in AVOID}
        close_scary = [p for p in scary if dist(p, pos) == 1]
        if close_scary and self.backed_off < 5 and all(chars[p] in AVOID for p in adjacent):
            # Step out of reach of freezers; capped per level since many never move.
            self.backed_off += 1
            escape = max((n for n in neighbors8(pos) if n not in chars and m.can_step(pos, n)),
                         key=lambda n: min(dist(n, q) for q in close_scary), default=None)
            if escape and min(dist(escape, q) for q in close_scary) > 1:
                self.note = f"back away from {chars[close_scary[0]]}"
                return self.move(pos, escape)

        if self.kite(st, pos, monsters, adjacent, chars, hp_drop):
            return

        if adjacent:
            # Finish whatever dies fastest; ice monsters last.
            target = min(adjacent, key=lambda p: (chars[p] in AVOID,
                                                  self.book.swings_to_kill(chars[p], st.depth)))
            bow = self.find(lambda i: i.has("bow") and "in hand" in i.desc)
            melee = self.best_melee()
            if bow and melee and not self.weapon_stuck and self.items_ok():
                self.note = f"switch to {melee.desc}"
                return self.wield(melee)
            self.fighting = NAMES.get(chars[target])
            self.note = f"fight {chars[target]} (hp {st.hp}/{st.maxhp}, worst hit {worst})"
            return self.move(pos, target)

        if self.items_ok() and self.shoot(pos, monsters, chars, st):
            return

        coming = [p for p in self.approaching if dist(p, pos) <= P.wait_range and p not in scary]
        if coming and self.waited < P.wait_turns:
            # Let it come to us: we get the first swing.
            self.waited += 1
            self.note = f"wait for {chars[coming[0]]} to come"
            return self.search(pos)
        if not coming:
            self.waited = 0

        if "blind" in self.effects:
            self.note = "blind: wait for sight"
            return self.search(pos, 5 if self.calm > 5 else 1)

        # Resting burns food; only top up fully when there's food to spare.
        spare_food = sum(i.has(*FOOD) for i in self.inv) >= 2
        full_rest = spare_food and (st.xlevel <= P.full_rest_xl or any(p not in scary for p in monsters))
        rest_to = st.maxhp if full_rest else st.maxhp * (P.rest_frac if food else P.rest_frac_no_food)
        if st.hp < rest_to and not coming:
            self.note = f"rest (hp {st.hp}/{st.maxhp})"
            return self.search(pos, 10 if self.calm > 5 else 1)

        if not monsters and self.items_ok() and self.safe_upgrade(st):
            return

        # Don't wake sleepers we'd struggle to beat, or step next to freezers.
        sleepers = {p for p in monsters if self.still.get(p, 0) >= 3
                    and self.fight_cost(chars[p], st.depth) > st.hp * P.sleeper_cost_frac}
        keep_clear = scary | sleepers
        avoid = (set(monsters) | keep_clear | {n for p in keep_clear for n in neighbors8(p)}) - {pos}
        stairs = m.stairs()
        # Long levels starve you; new levels bring food.
        # Seen enough rooms and know the way down: take it, grabbing only nearby items.
        rush = stairs and (self.level_steps > P.level_budget or not food
                           or m.rooms_seen() >= P.rooms_before_stairs)
        if rush and food:
            hit = m.nearest(pos, lambda p: p in m.items, avoid)
            if hit and len(m.path(pos, hit[0], avoid)) <= P.loot_detour:
                self.note = f"grab item on the way -> {hit[0]}"
                return self.move(pos, hit[0], hit[1])
        goals = [] if rush else [
            ("loot", lambda p: p in m.items),
            ("explore", m.is_frontier),
        ]
        for name, pred in goals:
            hit = m.nearest(pos, pred, avoid) or m.nearest(pos, pred, set(monsters))
            if hit:
                self.note = f"{name} -> {hit[0]}"
                return self.move(pos, hit[0], hit[1])

        if stairs:
            if pos == stairs:
                return self.descend(pos)
            hit = (m.nearest(pos, lambda p: p == stairs, avoid)
                   or m.nearest(pos, lambda p: p == stairs, set(monsters))
                   or m.nearest(pos, lambda p: p == stairs, set(monsters), allow_traps=True))
            if hit:
                self.note = "to stairs"
                return self.move(pos, hit[0], hit[1])

        teleport = self.find(lambda i: i.has("scroll") and i.has("teleport"))
        if teleport and not food and self.level_steps > P.level_budget:
            # Stuck with no food: the scroll we saved for emergencies may land us somewhere new.
            self.note = f"stuck and hungry: read {teleport.desc}"
            return self.use("r", teleport)

        if m.unblock():
            self.note = "unstick"
            return self.search(pos)
        return self.hunt_secret(pos, avoid)

    # ---- actions ---------------------------------------------------------------

    def fight_unseen(self, pos: Pos) -> None:
        """Hit an invisible attacker (or anything, while blind). Walking into its square is
        an attack: if we don't move and rogue reports a hit or miss, we've found it."""
        m = self.map
        if self.unseen_dir:
            d = self.unseen_dir
        else:
            options = [d for d in DIRS if d not in self.unseen_tried and m.can_step(pos, step(pos, d))]
            if not options:
                self.unseen_tried.clear()
                options = [d for d in DIRS if m.can_step(pos, step(pos, d))]
            if not options:
                self.note = "unseen attacker: nowhere to swing"
                return self.search(pos)
            # Only squares we could step on can hold it (in a corridor, just along it);
            # straight lines come first in DIRS.
            d = options[0]
            self.unseen_tried.add(d)
        self.note = f"swing {d} at unseen attacker"
        before = len(self.turn_msgs)
        self.send(d)  # not move(): a swing that hits must not be recorded as a wall
        connected = find_player(self.lines()) == pos and any(
            "you hit" in x or "you miss" in x or "defeated" in x for x in self.turn_msgs[before:])
        if connected:
            self.unseen_dir = d
        else:
            self.unseen_dir = None
            if find_player(self.lines()) != pos:
                self.unseen_tried = set()  # we moved; it's somewhere around the new spot

    def kite(self, st: Status, pos: Pos, monsters: list[Pos], adjacent: list[Pos],
             chars: dict[Pos, str], hp_drop: int) -> bool:
        """Run from a fight we'd lose. A same-speed chaser spends every turn catching up,
        so it never swings, and we regenerate while it follows."""
        chasers = [p for p in monsters if (p in adjacent or p in self.approaching)
                   and chars[p] not in AVOID]
        if not chasers:
            self.kiting = 0
            return False
        if self.kiting and hp_drop and adjacent:
            for p in adjacent:
                self.book.caught_us(chars[p])  # it caught us mid-run: maybe too fast to kite
        chasers = [p for p in chasers if not self.book.outruns_us(chars[p])]
        P = self.params
        if not chasers or self.kiting > P.kite_max_turns:
            return False
        worst = max(self.worst_hit(chars[p], st.depth) for p in chasers)
        cost = sum(self.fight_cost(chars[p], st.depth) for p in chasers)
        # Running only pays while there's HP to regenerate, and a short fight is worth finishing.
        if not (st.hp < st.maxhp * P.kite_hp_frac and cost + worst > st.hp + P.kite_margin):
            self.kiting = 0
            return False
        d = self.map.flee_step(pos, chasers, set(monsters))
        if not d:
            return False
        if not self.kiting:
            self.book.ran_from(chars[chasers[0]])
        self.kiting += 1
        self.note = f"kite {chars[chasers[0]]} (hp {st.hp}/{st.maxhp}, fight would cost ~{cost:.0f})"
        self.move(pos, step(pos, d), d)
        return True

    def move(self, pos: Pos, target: Pos, d: str | None = None) -> None:
        d = d or direction(pos, target)
        self.target = target
        nxt = step(pos, d)
        occupied = self.lines()[nxt[0]][nxt[1]].isupper()
        self.send(d)
        if not occupied and find_player(self.lines()) == pos:
            self.map.blocked.add((pos, nxt))

    def search(self, pos: Pos, n: int = 1) -> None:
        # A count prefix repeats the search; rogue interrupts it if a monster shows up.
        self.map.record_search(pos, n)
        self.send(f"{n}s" if n > 1 else "s")

    def descend(self, pos: Pos) -> None:
        if "levitating" in self.effects:
            self.note = "levitating: wait to land"
            return self.search(pos)
        self.note = "descend"
        depth = self.depth
        self.send(">")
        if parse_status(self.lines()[23]) and parse_status(self.lines()[23]).depth == depth:
            top = " ".join(self.turn_msgs[-2:]).lower()
            if "no way down" in top:
                self.map.forget_stairs(pos)  # those stairs were a hallucination
            elif "floating" in top:
                self.effects.add("levitating")

    def hunt_secret(self, pos: Pos, avoid: set[Pos]) -> None:
        m = self.map
        spots, reachable = [], set()
        for p, d, dist_ in m.bfs(pos, avoid):
            reachable.add(p)
            score = m.search_score(p)
            if score is not None:
                spots.append((score, dist_, p, d))
        if not spots:
            self.note = f"search here ({m.searches[pos]})"
            return self.search(pos, 5 if self.calm > 5 else 1)
        # Things we've seen but can't reach (a monster in an unvisited room, stairs, items,
        # floor) show where the hidden way in leads: search the spots nearest them first.
        hints = m.unreached(reachable)

        def toward_hint(p: Pos) -> int:
            return min(dist(p, h) for h in hints) if hints else 0

        # Same round of searching first; then nearest the hints; then dead ends before walls;
        # then nearest to us, since walking costs food too.
        score, _, best, d = min(spots, key=lambda s: (s[0][0], toward_hint(s[2]), s[0][1], s[1], s[0][2]))
        hint = f", toward what we saw at {min(hints, key=lambda h: dist(best, h))}" if hints else ""
        if best == pos:
            self.note = f"search here ({m.searches[pos]}){hint}"
            return self.search(pos, 5 if self.calm > 5 else 1)
        self.note = f"go search at {best}{hint}"
        return self.move(pos, best, d)

    def shoot(self, pos: Pos, monsters: list[Pos], chars: dict[Pos, str], st: Status) -> bool:
        arrows = self.find(lambda i: i.has("arrow"))
        bow = self.find(lambda i: i.has("bow"))
        wand = self.find(lambda i: i.has("wand", "staff") and i.unknown
                         and self.tried_wands.get(i.letter, 0) < 2)
        for p in sorted(monsters, key=lambda p: dist(p, pos)):
            d = self.line_of_fire(pos, p, set(monsters))
            if not d or (chars[p] in AVOID and dist(p, pos) < 3):
                continue
            # A fight we'd likely lose is worth spending an unknown wand on.
            if wand and p in self.approaching and self.fight_cost(chars[p], st.depth) > st.hp * self.params.wand_cost_frac:
                self.tried_wands[wand.letter] = self.tried_wands.get(wand.letter, 0) + 1
                self.note = f"zap {wand.desc} at {chars[p]}"
                self.use("z", wand, extra=d)
                return True
            if not (arrows and bow):
                continue
            if "in hand" not in bow.desc:
                if self.weapon_stuck:
                    continue
                # Swapping costs a turn; not worth it for close or erratic (bat) targets.
                others = [q for q in monsters if q != p and dist(q, pos) <= 3]
                P = self.params
                costly = self.fight_cost(chars[p], st.depth) > st.hp * P.bow_danger_frac
                far_enough = P.bow_range_danger if costly else P.bow_range
                if chars[p] == "B" or dist(p, pos) < far_enough or others:
                    continue
                self.note = f"wield bow for {chars[p]}"
                self.wield(bow)
            else:
                self.note = f"shoot {chars[p]}"
                self.use("t", arrows, extra=d)
            return True
        return False

    def line_of_fire(self, a: Pos, b: Pos, monsters: set[Pos]) -> str | None:
        dr, dc = b[0] - a[0], b[1] - a[1]
        n = max(abs(dr), abs(dc))
        if not (2 <= n <= 7) or not (dr == 0 or dc == 0 or abs(dr) == abs(dc)):
            return None
        sr, sc = (dr > 0) - (dr < 0), (dc > 0) - (dc < 0)
        for i in range(1, n):
            cell = (a[0] + sr * i, a[1] + sc * i)
            if self.map.t(cell) not in ".#+%" or cell in monsters:
                return None
        return direction(a, (a[0] + sr, a[1] + sc))

    def flee_downstairs(self, pos: Pos, monsters: list[Pos]) -> bool:
        """Monsters don't follow you down, and same-speed chasers spend turns catching up."""
        stairs = self.map.stairs()
        if not stairs or "levitating" in self.effects:
            return False
        if pos == stairs:
            self.note = "FLEE down the stairs"
            self.descend(pos)
            return True
        for p, d, dist_ in self.map.bfs(pos, set(monsters)):
            if dist_ > self.params.flee_stairs_dist:
                return False
            if p == stairs:
                self.note = f"FLEE to stairs ({dist_} away)"
                self.move(pos, step(pos, d), d)
                return True
        return False

    def emergency(self, pos: Pos, target: Pos | None) -> bool:
        options = [
            ("q", lambda i: i.has("potion of healing", "potion of extra healing",
                                  "potions of healing", "potions of extra healing")),
            ("r", lambda i: i.has("teleport", "hold monster")),
            ("q", lambda i: i.has("potion") and i.unknown),
            ("r", lambda i: i.has("scroll") and i.unknown),
            ("z", lambda i: i.has("wand", "staff") and self.tried_wands.get(i.letter, 0) < 2),
        ]
        for cmd, pred in options:
            item = self.find(pred)
            if item:
                self.note = f"EMERGENCY {cmd} {item.desc}"
                if cmd == "z":
                    self.tried_wands[item.letter] = self.tried_wands.get(item.letter, 0) + 1
                aim = direction(pos, target) if target else (self.unseen_dir or "h")
                self.use(cmd, item, extra=aim)
                return True
        return False

    def wield(self, item: Item) -> bool:
        self.use("w", item)
        now = self.find(lambda i: i.letter == item.letter)
        if now and "in hand" in now.desc:
            return True
        # Rogue refuses to swap away from a cursed weapon.
        self.weapon_stuck = True
        return False

    def best_melee(self) -> Item | None:
        weapons = [i for i in self.inv if i.weapon_value() is not None
                   and i.letter not in self.bad_weapons]
        if not weapons:
            return None
        # Ties go to what's already in hand, so equal weapons don't get swapped back and forth.
        return max(weapons, key=lambda i: (i.weapon_value(), "in hand" in i.desc))

    def safe_upgrade(self, st: Status) -> bool:
        best = self.best_melee()
        if best and "in hand" not in best.desc and not self.weapon_stuck:
            self.note = f"wield {best.desc}"
            if not self.wield(best):
                self.bad_weapons.add(best.letter)
            return True
        if self.params.taste_potions and st.hp >= st.maxhp * self.params.taste_hp_frac and not self.effects:
            # Learn what potions are while safe, so emergencies use a known one.
            potion = self.find(lambda i: i.has("potion") and i.unknown)
            if potion:
                self.note = f"taste-test {potion.desc}"
                self.use("q", potion)
                return True
        if st.hp >= st.maxhp * self.params.read_hp_frac:
            scroll = self.find(lambda i: i.has("scroll") and (i.unknown or i.has(*SAFE_SCROLLS)))
            if scroll:
                self.note = f"read {scroll.desc}"
                self.use("r", scroll)
                return True
        if self.armor_stuck:
            return False
        worn = self.find(lambda i: "being worn" in i.desc)
        worn_ac = worn.armor_class() if worn else 0
        better = [i for i in self.inv if i.armor_class() is not None and "being worn" not in i.desc
                  and i.letter not in self.tried_armor and i.armor_class() > worn_ac]
        if not better:
            return False
        item = max(better, key=lambda i: i.armor_class())
        self.tried_armor.add(item.letter)
        self.note = f"wear {item.desc} (now {worn.desc if worn else 'nothing'})"
        if worn:
            self.send("T")
            self.refresh_inventory()
            if self.find(lambda i: "being worn" in i.desc):
                self.armor_stuck = True  # cursed armor won't come off
                return True
        self.use("W", item)
        return True
