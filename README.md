# rogue-bot

A bot that plays BSD rogue (`/usr/games/rogue`, from `bsdgames-nonfree`) and shows the game live in your terminal.

## Install

1. Install rogue. On Debian or Ubuntu it's in the `bsdgames-nonfree` package:

   ```bash
   sudo apt install bsdgames-nonfree
   ```

   That puts the game at `/usr/games/rogue`. Run `/usr/games/rogue` once to check it starts, then press `Q` and `y` to quit.

2. Install [uv](https://docs.astral.sh/uv/). It fetches Python 3.14 and the project's dependencies for you:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Get the bot and run it:

   ```bash
   git clone git@github.com:bdw617/rogue-bot.git
   cd rogue-bot
   uv run rogue-bot
   ```

The bot looks for `rogue` on your `PATH`, then falls back to `/usr/games/rogue`. If yours is somewhere else, pass `--rogue /path/to/rogue`. It's written for this BSD rogue (the "rogue-clone" in bsdgames); other versions of rogue draw the screen differently.

## Watch it play

```bash
uv run rogue-bot                       # one game, live screen
uv run rogue-bot --games 0             # keep playing forever
uv run rogue-bot --games 5 --delay 0.1 # slower, five games in a row
uv run rogue-bot --games 0 --web       # also watch in a browser at http://127.0.0.1:8765/
uv run rogue-bot --headless --web 9000 # browser only, on port 9000
```

The browser view only listens on this machine (127.0.0.1). Watching in a browser or in the terminal doesn't change how the bot plays; it's the same game either way.

Press `Ctrl-C` to stop. The bot's status panel sits under the game screen and shows its current goal and recent messages. The map also shows where the bot has been: the remembered map is dimmed, the trail is blue and the current target is magenta.

## Rules it plays by

The bot only uses what a player sees on screen. It never reads rogue's memory, save files or random seed, and it never restarts a game.

## Monster book

The bot learns from its own fights and remembers across games. For each monster it records hits, misses, damage per hit, worst hit, swings needed to kill, and deaths caused, all parsed from combat messages and HP changes. It uses the book to decide when to fight, when to run, when to shoot, and when to use emergency items. The book lives at `~/.local/share/rogue-bot/monsters.json`; pass `--book PATH` to use another one.

## Tuning: make it better over time

The bot's judgement comes down to about 20 numbers in `params.py`: how much HP to rest back to, when to run from a fight, how far away to start shooting, how long to stay on a level, how hard to search. The tuner uses [Optuna](https://optuna.org) to find better values by playing real games:

```bash
uv run rogue-bot-tune                # 40 trials of 24 games, 12 at a time: a few hours
uv run rogue-bot-tune --trials 10    # a shorter session; studies resume where they left off
uv run rogue-bot-tune --trials 0     # skip new trials, just replay the best ones
```

- Each trial picks a set of numbers, plays a batch of games in parallel, and scores the average depth reached. Clearly bad trials stop early.
- Every game in a tuning run starts from the same frozen copy of the monster book, so trials compare fairly.
- Rogue games are noisy, so at the end the top 3 trials are replayed on 48 fresh games each, alongside the numbers `rogue-bot` currently uses. A new set is saved only if it beats the current one in that replay.
- The winner goes to `~/.local/state/rogue-bot/params.json`, and `rogue-bot` loads it automatically. The live view shows "tuned params" when it does. Use `--params PATH` to play with a different set.
- The study (`tune.db`) and a log of every tuning game (`tune-games.jsonl`) are in the same folder. Run the tuner again any time to keep improving.

## What it remembers between runs

Everything the bot learns is saved to files, so each run picks up where the last one left off.

| File | What's in it | Written by |
|---|---|---|
| `~/.local/share/rogue-bot/monsters.json` | The monster book: hits, damage, worst hit, swings to kill, deaths caused, and which monsters are too fast to run from | Every game `rogue-bot` plays |
| `~/.local/state/rogue-bot/params.json` | The tuned judgement numbers | `rogue-bot-tune`, when a new set wins its replay |
| `~/.local/state/rogue-bot/tune.db` | Every tuning trial so far | `rogue-bot-tune` |

To see what it has learned:

```bash
uv run rogue-bot-brain
```

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
| `params.py` | The judgement numbers and the ranges the tuner may try |
| `tune.py` | The Optuna tuner |
| `brain.py` | `rogue-bot-brain`: shows what's been learned |
| `view.py`, `web.py` | The terminal view and the browser view |
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
