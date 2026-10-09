import json

from rogue_bot.bot import Bot
from rogue_bot.web import frame


class Screen:
    alive = True

    def __init__(self, rows):
        self.rows = [r.ljust(80) for r in rows] + [" " * 80] * (24 - len(rows))

    def lines(self):
        return self.rows


def test_frame_marks_player_monsters_and_trail():
    bot = Bot(Screen(["", "|..@.H|"]))
    bot.trail.append((1, 2))
    f = json.loads(frame(bot, "game 1"))
    assert len(f["rows"]) == 24 and f["label"] == "game 1"
    runs = f["rows"][1]
    assert ["@", "player"] in runs and ["H", "monster"] in runs
    assert [".", "trail"] in runs
    assert "".join(text for text, _ in runs) == "|..@.H|".ljust(80)
