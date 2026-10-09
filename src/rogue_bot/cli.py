"""Entry point: play N games, show the live screen, log results."""

import argparse
import fcntl
import itertools
import json
from pathlib import Path
import shutil
import sys
import time

from .bot import Bot
from .knowledge import DEFAULT_PATH, MonsterBook
from .term import Terminal, state_dir

COLORS = {"%": "\x1b[1;32m", "+": "\x1b[33m"}
ITEM_COLOR, MONSTER_COLOR, RESET = "\x1b[36m", "\x1b[1;31m", "\x1b[0m"
PLAYER = "\x1b[1;30;43m"
REMEMBERED = "\x1b[2;37m"
TRAIL_BG, TARGET_BG = "\x1b[44m", "\x1b[45m"


def paint(bot: Bot) -> list[str]:
    """Game screen plus remembered map, the bot's recent trail, and its current target."""
    lines = bot.lines()
    terrain = bot.map.terrain
    trail = set(bot.trail)
    rows = []
    for r, line in enumerate(lines):
        if not 1 <= r <= 22:
            rows.append(line)
            continue
        out = []
        for c, ch in enumerate(line):
            if ch == "@":
                out.append(f"{PLAYER}@{RESET}")
                continue
            style = ""
            if ch == " " and terrain[r][c] != " ":
                ch, style = terrain[r][c], REMEMBERED
            elif ch.isupper():
                style = MONSTER_COLOR
            elif ch in "*!?/=):],":
                style = ITEM_COLOR
            else:
                style = COLORS.get(ch, "")
            if (r, c) == bot.target:
                style += TARGET_BG
            elif (r, c) in trail:
                style += TRAIL_BG
            out.append(f"{style}{ch}{RESET}" if style else ch)
        rows.append("".join(out))
    return rows


class View:
    def __init__(self, game_label):
        self.game_label = game_label
        sys.stdout.write("\x1b[?1049h\x1b[?25l")

    def __call__(self, bot: Bot) -> None:
        r = bot.result
        rows = paint(bot)
        rows.append("-" * 80)
        rows.append(f" {self.game_label()}  step {r.steps}  deepest {r.depth}")
        rows.append(f" bot: {bot.note[:72]}")
        rows.append(f" \x1b[44m \x1b[0m trail  \x1b[45m \x1b[0m target  \x1b[2;37m#\x1b[0m remembered map")
        rows += [f"  - {msg[:74]}" for msg in list(bot.messages)[-3:]]
        # Absolute positioning so a short terminal truncates instead of scrolling.
        height = shutil.get_terminal_size((80, 24)).lines
        out = [f"\x1b[{i + 1};1H{row}\x1b[K" for i, row in enumerate(rows[:height])]
        sys.stdout.write("".join(out) + "\x1b[J")
        sys.stdout.flush()

    def close(self) -> None:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


def wait_for_fresh_seed() -> None:
    """Rogue seeds from the clock: keep game starts across parallel runs >1s apart."""
    with open(state_dir() / "start.lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = float(f.read() or 0)
        time.sleep(max(0.0, last + 1.2 - time.time()))
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Play BSD rogue automatically.")
    ap.add_argument("--games", type=int, default=1, help="0 = keep playing forever")
    ap.add_argument("--delay", type=float, default=0.01, help="seconds between moves")
    ap.add_argument("--headless", action="store_true", help="no live screen")
    ap.add_argument("--max-steps", type=int, default=30000)
    ap.add_argument("--rogue", default=shutil.which("rogue") or "/usr/games/rogue")
    ap.add_argument("--log", default="results.jsonl")
    ap.add_argument("--trace", help="append one line per bot decision to this file")
    ap.add_argument("--book", type=Path, default=DEFAULT_PATH,
                    help="monster book the bot learns from and adds to (default: %(default)s)")
    args = ap.parse_args()

    game = 0
    results = []

    def label() -> str:
        total = f"/{args.games}" if args.games else ""
        if not results:
            return f"game {game}{total}"
        depths = [r.depth for r in results]
        return (f"game {game}{total}  so far: avg depth {sum(depths) / len(depths):.1f}, "
                f"best {max(depths)}")

    view = None if args.headless else View(label)
    trace = open(args.trace, "a", buffering=1) if args.trace else None
    book = MonsterBook(args.book)
    try:
        for game in itertools.count(1) if args.games == 0 else range(1, args.games + 1):
            wait_for_fresh_seed()
            term = Terminal([args.rogue], env={"ROGUEOPTS": "noskull,fruit=mango,name=roguebot"})
            bot = Bot(term, render=view, delay=0 if args.headless else args.delay,
                      max_steps=args.max_steps, trace=trace, book=book)
            try:
                r = bot.run()
            finally:
                book.save()
                if view:
                    time.sleep(1.5)
                term.close()
            results.append(r)
            with open(args.log, "a") as f:
                f.write(json.dumps({"time": time.time(), **r.__dict__}) + "\n")
            if args.headless:
                print(f"game {game}: depth {r.depth} gold {r.gold} xl {r.xlevel} "
                      f"steps {r.steps} :: {r.cause}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if view:
            view.close()
    if results:
        depths = [r.depth for r in results]
        print(f"{len(results)} games  avg depth {sum(depths) / len(depths):.1f}  "
              f"max depth {max(depths)}  avg gold {sum(r.gold for r in results) / len(results):.0f}")
        for i, r in enumerate(results, 1):
            print(f"  {i}: depth {r.depth} gold {r.gold} xl {r.xlevel} :: {r.cause}")
