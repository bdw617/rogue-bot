from rogue_bot.knowledge import MonsterBook


def test_observe_attributes_damage_and_kills(tmp_path):
    book = MonsterBook(tmp_path / "m.json")
    book.observe(["you hit  the hobgoblin hit"], hp_drop=4, target="hobgoblin")
    book.observe(["you miss  the hobgoblin misses"], hp_drop=0, target="hobgoblin")
    book.observe(["defeated the hobgoblin"], hp_drop=0, target="hobgoblin")
    r = book.known["hobgoblin"]
    assert (r.attacks, r.hits, r.damage, r.max_hit) == (2, 1, 4, 4)
    assert (r.swings, r.kills, r.kill_swings) == (2, 1, 3)
    book.observe(["the arrow hit"], hp_drop=0, target=None)
    assert "arrow" not in book.known


def test_two_hitters_in_one_turn_do_not_guess_damage(tmp_path):
    book = MonsterBook(tmp_path / "m.json")
    book.observe(["the bat hit  the snake hit"], hp_drop=5, target=None)
    assert book.known["bat"].damaged_hits == 0
    assert book.known["snake"].hits == 1


def test_save_merges_parallel_games(tmp_path):
    path = tmp_path / "m.json"
    a, b = MonsterBook(path), MonsterBook(path)
    a.observe(["the emu hit"], hp_drop=2, target=None)
    b.observe(["the emu hit"], hp_drop=3, target=None)
    b.died_to("Killed by an emu with 3 gold")
    a.save()
    b.save()
    r = MonsterBook(path).known["emu"]
    assert (r.hits, r.damage, r.max_hit, r.deaths) == (2, 5, 3, 1)


def test_threat_uses_priors_then_experience(tmp_path):
    book = MonsterBook(tmp_path / "m.json")
    unseen_rate, unseen_worst = book.threat("C", depth=7)
    assert unseen_worst == 9
    for _ in range(20):
        book.observe(["the snake hit"], hp_drop=1, target=None)
    rate, worst = book.threat("S", depth=1)
    assert worst == 1 and rate < unseen_rate
