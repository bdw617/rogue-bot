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


class Corridor:
    """A corridor running east-west with an invisible monster one square east."""

    alive = True

    def __init__(self):
        self.top = ""

    def lines(self):
        rows = [self.top.ljust(80)] + [" " * 80] * 23
        rows[5] = "    ###@###".ljust(80)
        rows[23] = "Level: 4  Gold: 0      Hp: 20(30)   Str: 16(16) Arm: 5  Exp: 3/40".ljust(80)
        return rows

    def send(self, keys):
        # Moving east hits the invisible monster; any other way would move us (not modelled).
        self.top = "you miss" if keys == "l" else ""

    def pump(self, *a, **k):
        return True


def test_finds_and_keeps_hitting_an_invisible_attacker():
    bot = Bot(Corridor())
    bot.map.update(bot.lines(), (5, 7))
    bot.map.visited |= {(5, c) for c in range(4, 11)}
    bot.unseen_dir = None
    bot.fight_unseen((5, 7))         # tries west first: no hit (screen says nothing)
    bot.fight_unseen((5, 7))         # then east: "you miss", and we didn't move
    assert bot.unseen_dir == "l"
    assert ((5, 7), (5, 8)) not in bot.map.blocked
    bot.fight_unseen((5, 7))
    assert bot.note == "swing l at unseen attacker"


def test_search_heads_toward_a_monster_seen_beyond_the_walls():
    from rogue_bot.level import LevelMap
    rows = {
        8: "                    ----------",
        9: "                    |........|",
        10: "                    |........|",
        11: "                    |........|",
        12: "                    ----------",
    }
    screen = [" " * 80] * 24
    for r, text in rows.items():
        screen[r] = text.ljust(80)
    bot = Bot(FakeTerm())
    bot.map = LevelMap()
    bot.map.update(screen, (10, 24))
    bot.map.seen_monsters.add((10, 60))   # a monster glimpsed in a room we can't reach
    bot.hunt_secret((10, 24), set())
    assert bot.target is not None and bot.target[1] >= 27, bot.note
    assert "toward what we saw at (10, 60)" in bot.note


class Prompting:
    """Shows `prompt` on the top line after the first key, and records everything sent."""

    alive = True

    def __init__(self, prompt, top=""):
        self.prompt, self.top, self.sent = prompt, top, []

    def lines(self):
        return [self.top.ljust(80)] + [" " * 80] * 23

    def send(self, keys):
        self.sent.append(keys)
        self.top = self.prompt if len(self.sent) == 1 else ""

    def pump(self, *a, **k):
        return True


def test_item_letter_is_only_sent_to_a_real_prompt():
    from rogue_bot.bot import Item
    scroll = Item("o", "a scroll entitled: 'bloto blech'")
    asked = Prompting("read what?")
    Bot(asked).use("r", scroll)
    assert asked.sent[:2] == ["r", "o"]
    swallowed = Prompting("")   # the "r" was eaten, no prompt shown
    Bot(swallowed).use("r", scroll)
    assert "o" not in swallowed.sent and "\x1b" in swallowed.sent


def test_escapes_the_options_screen():
    from rogue_bot.bot import OPTIONS_SCREEN
    term = Prompting("", top=OPTIONS_SCREEN + ": False")
    Bot(term).settle()
    assert term.sent and term.sent[0] == "\x1b"



class SilentDirection(Prompting):
    """Like rogue's throw: no prompt for the direction, then "throw what?"."""

    def send(self, keys):
        self.sent.append(keys)
        self.top = "throw what?" if len(self.sent) == 2 else ""


def test_throw_sends_direction_without_waiting_for_a_prompt():
    from rogue_bot.bot import Item
    term = SilentDirection("")
    Bot(term).use("t", Item("e", "31 +0,+0 arrows"), extra="l")
    assert term.sent[:3] == ["t", "l", "e"]


def test_hurt_bot_backs_off_from_an_approaching_monster():
    from rogue_bot.level import LevelMap, parse_status
    rows = [" " * 80] * 24
    rows[5] = "     |...........|".ljust(80)
    rows[6] = "     |.@...S.....|".ljust(80)
    rows[7] = "     |...........|".ljust(80)
    rows[4] = rows[8] = "     -------------".ljust(80)
    bot = Bot(FakeTerm())
    bot.map = LevelMap()
    bot.map.update(rows, (6, 7))
    bot.approaching = {(6, 11)}
    hurt = parse_status("Level: 2  Gold: 0      Hp: 12(20)   Str: 16(16) Arm: 4  Exp: 2/20")
    assert bot.kite(hurt, (6, 7), [(6, 11)], [], {(6, 11): "S"}, 0)
    assert bot.note.startswith("kite S")
    healthy = parse_status("Level: 2  Gold: 0      Hp: 19(20)   Str: 16(16) Arm: 4  Exp: 2/20")
    bot.kiting = 0
    assert not bot.kite(healthy, (6, 7), [(6, 11)], [], {(6, 11): "S"}, 0)


def test_unreachable_stairs_mean_explore_the_doors_not_search_walls():
    from rogue_bot.level import parse_status

    class Map:
        alive = True

        def __init__(self):
            rows = [" " * 80] * 24
            rows[3] = "   ----------- ".ljust(80)
            rows[4] = "   |....%....| ".ljust(80)
            rows[5] = "   ----------- ".ljust(80)
            rows[17] = " " * 50 + "---------"
            rows[18] = " " * 50 + "+...@...|"
            rows[19] = " " * 50 + "---------"
            rows[23] = "Level: 9  Gold: 0      Hp: 50(50)   Str: 16(16) Arm: 5  Exp: 7/300"
            self.rows = [r.ljust(80) for r in rows]
            self.sent = []

        def lines(self):
            return self.rows

        def send(self, keys):
            self.sent.append(keys)

        def pump(self, *a, **k):
            return True

    term = Map()
    bot = Bot(term)
    st = parse_status(term.rows[23])
    pos = (18, 54)
    bot.map.update(term.rows, pos)
    bot.map.visited |= {(18, c) for c in range(51, 58)}
    assert bot.map.rooms_seen() >= 2 and bot.map.stairs() == (4, 8)
    bot.act(st, pos, [], 0)
    assert bot.note.startswith("find a way to the stairs"), bot.note


def test_bot_sticks_with_its_exploration_target():
    from rogue_bot.level import parse_status

    class Corridor:
        alive = True

        def __init__(self):
            rows = [" " * 80] * 24
            rows[13] = "          ##########@#####".ljust(80)
            rows[23] = "Level: 4  Gold: 0      Hp: 40(40)   Str: 16(16) Arm: 5  Exp: 4/60".ljust(80)
            self.rows = [r.ljust(80) for r in rows]

        def lines(self):
            return self.rows

        def send(self, keys):
            pass

        def pump(self, *a, **k):
            return True

    term = Corridor()
    bot = Bot(term)
    pos = (13, 20)
    bot.map.update(term.rows, pos)
    st = parse_status(term.rows[23])
    bot.goal = ("explore", (13, 10))            # the far end, chosen earlier
    bot.act(st, pos, [], 0)
    assert bot.note == "explore -> (13, 10)"    # not the nearer east end
