"""Screen parsing and per-level map memory with pathfinding."""

import re
from collections import Counter, deque
from dataclasses import dataclass

ROWS, COLS = 24, 80
MAP_TOP, MAP_BOTTOM = 1, 22
WALKABLE = set(".#+%")
ITEMS = set("*!?/=):],")
# Orthogonal first so BFS prefers straight lines.
DIRS = {"h": (0, -1), "j": (1, 0), "k": (-1, 0), "l": (0, 1),
        "y": (-1, -1), "u": (-1, 1), "b": (1, -1), "n": (1, 1)}

STATUS_RE = re.compile(
    r"Level:\s*(\d+)\s+Gold:\s*(\d+)\s+Hp:\s*(\d+)\((\d+)\)\s+"
    r"Str:\s*(\d+)\((\d+)\)\s+Arm:\s*(-?\d+)\s+Exp:\s*(\d+)/(\d+)\s*(\w*)")

Pos = tuple[int, int]


@dataclass
class Status:
    depth: int
    gold: int
    hp: int
    maxhp: int
    str: int
    arm: int
    xlevel: int
    hunger: str


def parse_status(line: str) -> Status | None:
    m = STATUS_RE.search(line)
    if not m:
        return None
    g = m.groups()
    return Status(int(g[0]), int(g[1]), int(g[2]), int(g[3]), int(g[4]),
                  int(g[6]), int(g[7]), g[9])


def find_player(lines: list[str]) -> Pos | None:
    for r in range(MAP_TOP, MAP_BOTTOM + 1):
        c = lines[r].find("@")
        if c >= 0:
            return (r, c)
    return None


def step(p: Pos, d: str) -> Pos:
    dr, dc = DIRS[d]
    return (p[0] + dr, p[1] + dc)


def direction(a: Pos, b: Pos) -> str:
    for d, (dr, dc) in DIRS.items():
        if (a[0] + dr, a[1] + dc) == b:
            return d
    raise ValueError(f"{a} and {b} are not adjacent")


def neighbors8(p: Pos):
    for dr, dc in DIRS.values():
        r, c = p[0] + dr, p[1] + dc
        if MAP_TOP <= r <= MAP_BOTTOM and 0 <= c < COLS:
            yield (r, c)


class LevelMap:
    def __init__(self, dead_end_tier: int = 40, wall_tier: int = 15):
        self.dead_end_tier, self.wall_tier = dead_end_tier, wall_tier
        self.terrain = [[" "] * COLS for _ in range(ROWS)]
        self.items: dict[Pos, str] = {}
        self.visited: set[Pos] = set()
        self.pickup_tries: Counter[Pos] = Counter()  # times we stood on an item there
        self.blocked: set[tuple[Pos, Pos]] = set()
        self.searches: Counter[Pos] = Counter()
        self.wall_searches: Counter[Pos] = Counter()  # searches that covered each wall square

    def t(self, p: Pos) -> str:
        return self.terrain[p[0]][p[1]]

    def update(self, lines: list[str], player: Pos, hallucinating: bool = False) -> list[Pos]:
        """Merge the visible screen into memory; return visible monster positions.

        While hallucinating, object symbols (and stairs) on screen are random, so they're ignored.
        """
        monsters = []
        for r in range(MAP_TOP, MAP_BOTTOM + 1):
            for c, ch in enumerate(lines[r][:COLS]):
                if ch == " " or ch == "@":
                    continue
                if hallucinating and (ch in ITEMS or ch == "%"):
                    if self.terrain[r][c] == " ":
                        self.terrain[r][c] = "."
                    continue
                if ch in "-|.#+%^":
                    self.terrain[r][c] = ch
                    self.items.pop((r, c), None)
                elif ch in ITEMS:
                    # Rogue picks items up on contact; one still there after two visits
                    # can't be taken (pack full), so stop going back for it.
                    if self.pickup_tries[(r, c)] < 2:
                        self.items[(r, c)] = ch
                    if self.terrain[r][c] == " ":
                        self.terrain[r][c] = "."
                elif ch.isupper():
                    monsters.append((r, c))
                    if self.terrain[r][c] == " ":
                        self.terrain[r][c] = "."
        if self.t(player) == " ":
            self.terrain[player[0]][player[1]] = "."
        self.visited.add(player)
        if player in self.items:
            self.pickup_tries[player] += 1
        self.items.pop(player, None)
        return monsters

    def can_step(self, a: Pos, b: Pos, allow_traps: bool = False,
                 ignore_blocked: bool = False) -> bool:
        """Mirror rogue's can_move(): no diagonals through doors or past solid rock."""
        r2, c2 = b
        if not (MAP_TOP <= r2 <= MAP_BOTTOM and 0 <= c2 < COLS):
            return False
        t = self.t(b)
        if t not in WALKABLE and not (allow_traps and t == "^"):
            return False
        if not ignore_blocked and (a, b) in self.blocked:
            return False
        r1, c1 = a
        if r1 != r2 and c1 != c2:
            if self.t(a) == "+" or t == "+":
                return False
            if self.terrain[r1][c2] == " " or self.terrain[r2][c1] == " ":
                return False
        return True

    def bfs(self, start: Pos, avoid: set[Pos] = frozenset(), allow_traps: bool = False):
        """Yield (pos, first_step_dir, dist) in BFS order from start."""
        first: dict[Pos, str | None] = {start: None}
        dist = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            yield cur, first[cur], dist[cur]
            for d in DIRS:
                nxt = step(cur, d)
                if nxt in first or nxt in avoid or not self.can_step(cur, nxt, allow_traps):
                    continue
                first[nxt] = first[cur] or d
                dist[nxt] = dist[cur] + 1
                q.append(nxt)

    def distances(self, sources: list[Pos]) -> dict[Pos, int]:
        """Steps for something starting at any of `sources` to reach each known square."""
        dist = {p: 0 for p in sources}
        q = deque(sources)
        while q:
            cur = q.popleft()
            for d in DIRS:
                nxt = step(cur, d)
                if nxt not in dist and self.can_step(cur, nxt, ignore_blocked=True):
                    dist[nxt] = dist[cur] + 1
                    q.append(nxt)
        return dist

    def flee_step(self, start: Pos, chasers: list[Pos], blocked: set[Pos]) -> str | None:
        """First step toward the most room we can reach before any chaser can.

        A square is safe if we get there strictly before the nearest chaser could.
        """
        them = self.distances(chasers)
        best, best_d = None, None
        for d in DIRS:
            first = step(start, d)
            if first in blocked or not self.can_step(start, first) or them.get(first, 99) < 2:
                continue
            seen, q, space = {first: 1}, deque([first]), 0
            while q and space < 200:
                cur = q.popleft()
                space += 1
                for d2 in DIRS:
                    nxt = step(cur, d2)
                    if nxt in seen or nxt in blocked or not self.can_step(cur, nxt):
                        continue
                    if seen[cur] + 1 < them.get(nxt, 99):
                        seen[nxt] = seen[cur] + 1
                        q.append(nxt)
            score = (space, them.get(first, 99))
            if best is None or score > best:
                best, best_d = score, d
        return best_d

    def path(self, start: Pos, goal: Pos, avoid=frozenset()) -> list[Pos]:
        """Squares from start (exclusive) to goal, or [] if unreachable."""
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur == goal:
                out = []
                while cur != start:
                    out.append(cur)
                    cur = prev[cur]
                return out[::-1]
            for d in DIRS:
                nxt = step(cur, d)
                if nxt not in prev and nxt not in avoid and self.can_step(cur, nxt):
                    prev[nxt] = cur
                    q.append(nxt)
        return []

    def nearest(self, start: Pos, pred, avoid=frozenset(), allow_traps=False):
        for p, d, _ in self.bfs(start, avoid, allow_traps):
            if p != start and pred(p):
                return p, d
        return None

    def is_frontier(self, p: Pos) -> bool:
        return p not in self.visited and any(self.t(n) == " " for n in neighbors8(p))

    def forget_stairs(self, p: Pos) -> None:
        if self.t(p) == "%":
            self.terrain[p[0]][p[1]] = "."

    def stairs(self) -> Pos | None:
        for r in range(MAP_TOP, MAP_BOTTOM + 1):
            c = "".join(self.terrain[r]).find("%")
            if c >= 0:
                return (r, c)
        return None

    def search_score(self, p: Pos):
        """Lower is better: dead ends first, then walls facing unexplored space."""
        t = self.t(p)
        orth = sum(self.t(step(p, d)) in WALKABLE for d in "hjkl")
        dead_end = t == "#" and orth <= 1
        blind_door = t == "+" and any(self.t(step(p, d)) == " " for d in "hjkl")
        if (dead_end or blind_door) and p in self.visited:
            # These almost always hide a corridor, and search only reaches 1 square.
            return (self.searches[p] // self.dead_end_tier, 0, 0)
        if t != ".":
            return None
        # A spot beside room walls that could hide a door: prefer the one that covers the most
        # wall squares not yet searched enough, so the bot sweeps the walls instead of circling.
        walls = [w for w in neighbors8(p) if self.hides_door(w)]
        if not walls:
            return None
        sweep = min(self.wall_searches[w] for w in walls) // self.wall_tier
        fresh = sum(self.wall_searches[w] // self.wall_tier == sweep for w in walls)
        return (sweep, 1, -fresh)

    def hides_door(self, w: Pos) -> bool:
        """A wall square (not a corner) with unexplored map right behind it."""
        t = self.t(w)
        if t not in "-|":
            return False
        if t == "-":
            ends, sides = ((w[0], w[1] - 1), (w[0], w[1] + 1)), ((w[0] - 1, w[1]), (w[0] + 1, w[1]))
        else:
            ends, sides = ((w[0] - 1, w[1]), (w[0] + 1, w[1])), ((w[0], w[1] - 1), (w[0], w[1] + 1))
        if not all(self.t(e) in "-|+" for e in ends if MAP_TOP <= e[0] <= MAP_BOTTOM):
            return False  # a corner: rogue never puts doors there
        return any(MAP_TOP <= r <= MAP_BOTTOM and 0 <= c < COLS and self.terrain[r][c] == " "
                   for r, c in sides)

    def record_search(self, p: Pos, n: int) -> None:
        self.searches[p] += n
        for w in neighbors8(p):
            if self.t(w) in "-|":
                self.wall_searches[w] += n

    def rooms_seen(self) -> int:
        """Rooms whose floor we've seen: groups of 4+ connected floor squares."""
        seen: set[Pos] = set()
        rooms = 0
        for r in range(MAP_TOP, MAP_BOTTOM + 1):
            for c in range(COLS):
                if self.terrain[r][c] != "." or (r, c) in seen:
                    continue
                group, q = 0, deque([(r, c)])
                seen.add((r, c))
                while q:
                    cur = q.popleft()
                    group += 1
                    for n in neighbors8(cur):
                        if n not in seen and self.t(n) == ".":
                            seen.add(n)
                            q.append(n)
                rooms += group >= 4
        return rooms
