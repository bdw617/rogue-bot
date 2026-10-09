# rogue-bot

A bot that plays BSD rogue (`/usr/games/rogue`, from `bsdgames-nonfree`) and shows the game live in your terminal.

## Watch it play

```bash
uv run rogue-bot                       # one game, live screen
uv run rogue-bot --games 0             # keep playing forever
uv run rogue-bot --games 5 --delay 0.1 # slower, five games in a row
```

Press `Ctrl-C` to stop. The bot's status panel sits under the game screen and shows its current goal and recent messages. The map also shows where the bot has been: the remembered map is dimmed, the trail is blue and the current target is magenta.

## Rules it plays by

The bot only uses what a player sees on screen. It never reads rogue's memory, save files or random seed, and it never restarts a game.

## Monster book

The bot learns from its own fights and remembers across games. For each monster it records hits, misses, damage per hit, worst hit, swings needed to kill, and deaths caused, all parsed from combat messages and HP changes. It uses the book to decide when to fight, when to run, when to shoot, and when to use emergency items. The book lives at `~/.local/share/rogue-bot/monsters.json`; pass `--book PATH` to use another one.

## Benchmark

```bash
uv run rogue-bot --headless --games 20 --log runs.jsonl
```

This prints the depth, gold and cause of death for each game. Each game is also appended to the `--log` file as one JSON line. Add `--trace trace.tsv` to log every decision the bot makes (position, HP, food, goal), with `# new game` and `# end: <cause>` markers between games.

You can run several benchmarks in parallel. Rogue seeds its dungeon from the clock, so the bot automatically keeps game starts at least 1.2s apart across processes to avoid duplicate games.

## How it works

| File | Job |
|---|---|
| `term.py` | Runs rogue in a pseudo-terminal and keeps an emulated 80×24 screen (`pyte`) |
| `level.py` | Parses the status line, remembers the map per level, and finds paths with BFS using rogue's movement rules (no diagonal moves through doors or past rock) |
| `bot.py` | Picks one action per turn, in priority order (below) |
| `knowledge.py` | The monster book |
| `cli.py` | Live view, multiple games, results log |

The bot's priority order each turn:
1. Eat when hungry. Track blindness, hallucination and levitation from rogue's messages. If HP drops with no monster on screen (blind, or an invisible attacker), swing at the neighboring squares.
2. At 40% HP or below: use healing, teleport or unknown items, or run for the stairs (monsters don't follow you down).
3. Run from a fight the monster book says it would lose (kiting: a same-speed monster never gets a swing while you keep moving, and you regenerate). Otherwise fight an adjacent monster. Step back from `I` (ice monster, whose freeze can kill outright) and `F` (flytrap).
4. Shoot arrows at monsters in a clear line 4+ squares away, then switch back to the best melee weapon.
5. Wait for a monster that's approaching. Rest when HP is under 70%. When it's safe, read scrolls, drink unknown potions to identify them, and wear better armor.
6. Pick up items, then explore, then take the stairs (resting first). It heads straight for known stairs when it runs out of food or spends 600+ steps on one level. If it's stuck, it searches dead ends and doors that open onto nothing first, then walls facing unexplored space.

The bot writes to rogue's shared top-ten score file.

## Tests

```bash
uv run pytest
```
