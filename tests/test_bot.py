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


def test_armor_class_reads_brackets_then_base_plus_enchant():
    from rogue_bot.bot import Item
    assert Item("b", "+1 ring mail [4] being worn").armor_class() == 4
    assert Item("c", "-2 banded mail [4]").armor_class() == 4
    assert Item("d", "scale mail").armor_class() == 4
    assert Item("e", "+2 plate mail").armor_class() == 9
    assert Item("f", "a pink potion").armor_class() is None


def test_weapon_value_counts_enchantment():
    from rogue_bot.bot import Item
    good, bad = Item("c", "a +1,+1 mace in hand"), Item("h", "a -2,-2 mace")
    assert good.weapon_value() > Item("g", "a mace").weapon_value() > bad.weapon_value()
    assert Item("i", "a long sword").weapon_value() > good.weapon_value()
    assert Item("j", "31 +0,+0 arrows").weapon_value() is None


def test_best_melee_keeps_the_weapon_in_hand_on_ties():
    from rogue_bot.bot import Item
    bot = Bot(FakeTerm())
    bot.inv = [Item("c", "a mace in hand"), Item("g", "a mace")]
    assert bot.best_melee().letter == "c"
