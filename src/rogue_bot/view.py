"""What to draw for each square, shared by the terminal and browser views."""

import shutil
import sys

from .bot import Bot
from .level import ITEMS

Cell = tuple[str, str, str]  # (character, foreground kind, background kind)


def cells(bot: Bot) -> list[list[Cell]]:
    """Game screen plus remembered map, the bot's recent trail, and its current target."""
    terrain = bot.map.terrain
    trail = set(bot.trail)
    grid = []
    for r, line in enumerate(bot.lines()):
        row = []
        for c, ch in enumerate(line):
            fg = bg = ""
            if 1 <= r <= 22:
                if ch == "@":
                    fg = "player"
                elif ch == " " and terrain[r][c] != " ":
                    ch, fg = terrain[r][c], "memory"
                elif ch.isupper():
                    fg = "monster"
                elif ch in ITEMS:
                    fg = "item"
                elif ch == "%":
                    fg = "stairs"
                elif ch == "+":
                    fg = "door"
                if ch != "@":
                    bg = "target" if (r, c) == bot.target else "trail" if (r, c) in trail else ""
            row.append((ch, fg, bg))
        grid.append(row)
    return grid


def panel(bot: Bot, label: str) -> dict:
    r = bot.result
    return {"label": label, "step": r.steps, "deepest": r.depth, "note": bot.note,
            "messages": list(bot.messages)[-3:]}


ANSI = {"player": "1;30;43", "monster": "1;31", "item": "36", "stairs": "1;32", "door": "33",
        "memory": "2;37", "target": "45", "trail": "44"}


class TerminalView:
    def __init__(self, game_label):
        self.game_label = game_label
        sys.stdout.write("\x1b[?1049h\x1b[?25l")

    def __call__(self, bot: Bot) -> None:
        rows = []
        for row in cells(bot):
            out = []
            for ch, fg, bg in row:
                codes = ";".join(ANSI[k] for k in (fg, bg) if k)
                out.append(f"\x1b[{codes}m{ch}\x1b[0m" if codes else ch)
            rows.append("".join(out))
        info = panel(bot, self.game_label())
        rows.append("-" * 80)
        rows.append(f" {info['label']}  step {info['step']}  deepest {info['deepest']}")
        rows.append(f" bot: {info['note'][:72]}")
        rows.append(" \x1b[44m \x1b[0m trail  \x1b[45m \x1b[0m target  \x1b[2;37m#\x1b[0m remembered map")
        rows += [f"  - {msg[:74]}" for msg in info["messages"]]
        # Absolute positioning so a short terminal truncates instead of scrolling.
        height = shutil.get_terminal_size((80, 24)).lines
        out = [f"\x1b[{i + 1};1H{row}\x1b[K" for i, row in enumerate(rows[:height])]
        sys.stdout.write("".join(out) + "\x1b[J")
        sys.stdout.flush()

    def close(self) -> None:
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
