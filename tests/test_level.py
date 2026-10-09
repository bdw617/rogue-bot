from rogue_bot.level import LevelMap, find_player, parse_status

STATUS = "Level: 3  Gold: 54     Hp: 9(12)    Str: 16(16) Arm: 4  Exp: 2/14 Hungry"


def screen(rows: dict[int, str]) -> list[str]:
    lines = [" " * 80 for _ in range(24)]
    for r, text in rows.items():
        lines[r] = text.ljust(80)
    lines[23] = STATUS.ljust(80)
    return lines


ROOM = screen({
    1: "-----+---",
    2: "|.......|",
    3: "|..@..!.+",
    4: "|...B...|",
    5: "---------",
})


def test_parse_status():
    st = parse_status(STATUS)
    assert (st.depth, st.gold, st.hp, st.maxhp, st.xlevel, st.hunger) == (3, 54, 9, 12, 2, "Hungry")


def test_update_tracks_items_and_monsters():
    m = LevelMap()
    pos = find_player(ROOM)
    assert pos == (3, 3)
    assert m.update(ROOM, pos) == [(4, 4)]
    assert m.items == {(3, 6): "!"}


def test_no_diagonal_into_door():
    m = LevelMap()
    m.update(ROOM, (3, 3))
    assert m.can_step((2, 4), (1, 5)) is False
    assert m.can_step((2, 5), (1, 5)) is True


def test_no_diagonal_past_rock():
    lines = screen({5: "  #", 6: "  ##", 7: "   #"})
    m = LevelMap()
    m.update(lines, (5, 2))
    assert m.can_step((5, 2), (6, 3)) is False
    assert m.can_step((5, 2), (6, 2)) is True
    assert m.can_step((6, 3), (7, 3)) is True


def test_frontier_and_path_to_door():
    m = LevelMap()
    m.update(ROOM, (3, 3))
    target, first = m.nearest((3, 3), lambda p: p in m.items)
    assert target == (3, 6) and first == "l"
    assert m.is_frontier((3, 8))
    assert not m.is_frontier((2, 2))


def test_blind_door_is_top_search_spot():
    m = LevelMap()
    m.update(ROOM, (3, 3))
    m.visited.add((3, 8))
    assert m.search_score((3, 8)) == (0, 0, 0)
    assert m.search_score((2, 2))[1] == 1


def test_flee_step_runs_toward_open_space_not_dead_end():
    lines = screen({
        5: "----------",
        6: "|........|",
        7: "|........+######",
        8: "|........|",
        9: "----------",
    })
    m = LevelMap()
    m.update(lines, (7, 8))
    # Chaser just inside the room to our west; the corridor east is the long way out.
    assert m.flee_step((7, 8), [(7, 6)], {(7, 6)}) == "l"
    # Chaser in the corridor to our east: run back into the room, away from it.
    m.update(lines, (7, 7))
    assert m.flee_step((7, 7), [(7, 9)], {(7, 9)}) in ("h", "y", "b")
