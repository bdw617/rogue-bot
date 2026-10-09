from rogue_bot.bot import Bot


class FakeTerm:
    """A frozen screen: nothing the bot sends changes it."""

    alive = True

    def __init__(self):
        self.sent = []

    def lines(self):
        return [" " * 80] * 24

    def send(self, keys):
        self.sent.append(keys)

    def pump(self, *a, **k):
        return False


def test_watchdog_breaks_a_silent_loop():
    term = FakeTerm()
    bot = Bot(term)
    for step in range(31):  # the first call only records the screen
        bot.note = "wield bow for S"
        bot.watchdog((5, 5), step)
    assert bot.result.steps < bot.items_off_until
    assert not bot.items_ok()
    assert term.sent[-1] == "s"


def test_watchdog_leaves_resting_alone():
    bot = Bot(FakeTerm())
    for step in range(100):
        bot.note = "rest (hp 5/12)"
        bot.watchdog((5, 5), step)
    assert bot.items_ok()


def test_cursed_message_blocks_weapon_swaps():
    bot = Bot(FakeTerm())
    bot.log("you can't, it appears to be cursed")
    assert bot.weapon_stuck
    bot.log("you feel as though someone is watching over you")
    assert not bot.weapon_stuck
