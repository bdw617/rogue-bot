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
    # A wall with almost nothing unexplored behind it isn't worth searching.
    assert m.search_score((2, 2)) is None


def test_search_dead_ends_and_open_walls_not_every_corridor_square():
    lines = screen({
        12: "       |....|",
        13: "       |....+######",
        14: "       ------",
    })
    m = LevelMap()
    m.update(lines, (13, 18))
    m.visited |= {(13, c) for c in range(12, 19)}
    assert m.search_score((13, 18)) == (0, 0, 0)      # corridor dead end
    assert m.search_score((13, 15)) is None           # middle of the corridor
    assert m.search_score((12, 8))[1] == 1            # wall facing unexplored map


def test_rooms_seen_counts_floor_areas_not_stray_squares():
    lines = screen({
        2: "|....|     |...|",
        3: "|....+#####+...|",
        4: "|....|  .  |...|",
    })
    m = LevelMap()
    m.update(lines, (3, 2))
    assert m.rooms_seen() == 2


def test_items_get_a_second_chance_then_are_dropped():
    m = LevelMap()
    with_item = screen({3: "|..!.|"})
    m.update(with_item, (3, 1))
    for _ in range(2):
        assert (3, 3) in m.items
        m.update(screen({3: "|..@.|"}), (3, 3))   # stand on it; pickup fails
        m.update(with_item, (3, 1))               # step off; it's still there
    assert (3, 3) not in m.items


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


def test_search_sweeps_every_wall_of_a_doorless_room():
    # The room the live bot got stuck in: top-right of the map, no visible doors.
    lines = screen({
        1: " " * 55 + "----------------",
        2: " " * 55 + "|..............|",
        3: " " * 55 + "|..............|",
        4: " " * 55 + "|@.............|",
        5: " " * 55 + "|..............|",
        6: " " * 55 + "----------------",
    })
    m = LevelMap(wall_tier=15)
    m.update(lines, (4, 56))
    doors_possible = {(r, c) for r in range(1, 23) for c in range(80) if m.hides_door((r, c))}
    # Left wall, right wall and bottom wall can hide doors; the top wall is the map edge.
    assert (6, 62) in doors_possible and (4, 55) in doors_possible and (4, 70) in doors_possible
    assert not any(r == 1 for r, _ in doors_possible)
    spots = [(r, c) for r in range(2, 6) for c in range(56, 70)]
    picked = []
    for _ in range(40):
        best = min((m.search_score(p), p) for p in spots if m.search_score(p) is not None)[1]
        picked.append(best)
        m.record_search(best, 5)
    # Within 40 picks every possible door square has had a full round of searching.
    assert all(m.wall_searches[w] >= 15 for w in doors_possible)
    # It moved along the bottom wall instead of circling the corners.
    assert {(5, 59), (5, 62), (5, 65)} <= set(picked)


def test_walls_on_the_map_edge_do_not_crash():
    lines = screen({5: " " * 70 + "|.........", 6: " " * 70 + "|.........", 7: " " * 70 + "----------"})
    m = LevelMap()
    m.update(lines, (6, 75))
    assert m.t((6, 80)) == " " and m.t((6, -1)) == " "
    for r in range(5, 8):
        for c in range(70, 80):
            m.search_score((r, c))
            m.hides_door((r, c))


def test_bot_walks_out_through_a_door_into_the_unseen():
    # The live game: a room whose doors show nothing beyond them.
    lines = screen({
        10: " " * 62 + "-------------",
        11: " " * 62 + "+...........|",
        12: " " * 62 + "|...........|",
        13: " " * 62 + "|@..........|",
        14: " " * 62 + "-------+-----",
    })
    m = LevelMap()
    m.update(lines, (13, 63))
    assert m.can_step((11, 62), (11, 61))        # out the west door
    assert m.can_step((14, 69), (15, 69))        # out the south door
    assert not m.can_step((11, 62), (12, 61))    # never diagonally out of a door
    assert not m.can_step((12, 63), (12, 61))    # not through a wall
    target, _ = m.nearest((13, 63), m.is_frontier)
    assert target == (11, 62)                    # first the door...
    m.visited.add((11, 62))
    target, _ = m.nearest((13, 63), m.is_frontier)
    assert target == (11, 61)                    # ...then the unseen square beyond it
    # If stepping out fails (a hidden passage), that way is marked blocked and skipped.
    m.blocked.add(((11, 62), (11, 61)))
    m.blocked.add(((14, 69), (15, 69)))
    assert m.nearest((13, 63), lambda p: m.t(p) == " ") is None
