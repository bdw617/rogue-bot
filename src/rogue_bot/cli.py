"""Entry point: play N games, show the live screen, log results."""

import argparse
import fcntl
import itertools
import json
from pathlib import Path
import shutil
import signal
import sys
import time

from .bot import Bot, Result
from .knowledge import DEFAULT_PATH, MonsterBook
from .params import Params, default_path
from .term import Terminal, reap_orphans, state_dir
from .view import TerminalView
from .web import WebView

def exit_cleanly_on_signals() -> None:
    """Turn hangup/terminate into a normal exit so open games get closed, not orphaned."""
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(1))


def wait_for_fresh_seed() -> None:
    """Rogue seeds from the clock: keep game starts across parallel runs >1s apart."""
    with open(state_dir() / "start.lock", "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = float(f.read() or 0)
        time.sleep(max(0.0, last + 1.2 - time.time()))
        f.seek(0)
        f.truncate()
        f.write(str(time.time()))


ROGUE = shutil.which("rogue") or "/usr/games/rogue"


def play_game(book: MonsterBook, params: Params, rogue: str = ROGUE, max_steps: int = 30000,
              render=None, delay: float = 0.0, trace=None) -> Result:
    """Play one full game of rogue and return how it went."""
    wait_for_fresh_seed()
    term = Terminal([rogue], env={"ROGUEOPTS": "noskull,fruit=mango,name=roguebot"})
    bot = Bot(term, render=render, delay=delay, max_steps=max_steps, trace=trace,
              book=book, params=params)
    try:
        return bot.run()
    finally:
        book.save()
        if render:
            time.sleep(1.5)
        term.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Play BSD rogue automatically.")
    ap.add_argument("--games", type=int, default=1, help="0 = keep playing forever")
    ap.add_argument("--delay", type=float, default=0.01, help="seconds between moves")
    ap.add_argument("--headless", action="store_true", help="no live screen in the terminal")
    ap.add_argument("--web", nargs="?", const=8765, type=int, metavar="PORT",
                    help="also show the game in a browser at http://localhost:PORT (default 8765)")
    ap.add_argument("--max-steps", type=int, default=30000)
    ap.add_argument("--rogue", default=ROGUE)
    ap.add_argument("--log", default="results.jsonl")
    ap.add_argument("--trace", help="append one line per bot decision to this file")
    ap.add_argument("--book", type=Path, default=DEFAULT_PATH,
                    help="monster book the bot learns from and adds to (default: %(default)s)")
    ap.add_argument("--params", type=Path, default=None,
                    help="judgement numbers to play with (default: the tuner's saved best, if any)")
    args = ap.parse_args()
    exit_cleanly_on_signals()
    reap_orphans()

    params_path = args.params or default_path()
    params = Params.load(params_path) if params_path.exists() else Params()
    tuned = "tuned params" if params_path.exists() else "default params"

    game = 0
    results = []

    def label() -> str:
        total = f"/{args.games}" if args.games else ""
        if not results:
            return f"game {game}{total}  ({tuned})"
        depths = [r.depth for r in results]
        return (f"game {game}{total}  so far: avg depth {sum(depths) / len(depths):.1f}, "
                f"best {max(depths)}")

    views = []
    if args.web:
        try:
            views.append(WebView(label, args.web))
        except OSError as e:
            sys.exit(f"can't serve the browser view on port {args.web}: {e}")
        print(f"watch in your browser: {views[-1].url}", flush=True)
        if not args.headless:
            time.sleep(2)  # leave the URL on screen for a moment before the game takes over
    if not args.headless:
        views.append(TerminalView(label))
    render = (lambda bot: [v(bot) for v in views]) if views else None
    trace = open(args.trace, "a", buffering=1) if args.trace else None
    book = MonsterBook(args.book)
    try:
        for game in itertools.count(1) if args.games == 0 else range(1, args.games + 1):
            r = play_game(book, params, args.rogue, args.max_steps, render=render,
                          delay=args.delay if views else 0, trace=trace)
            results.append(r)
            with open(args.log, "a") as f:
                f.write(json.dumps({"time": time.time(), **r.__dict__}) + "\n")
            if args.headless and not args.web:
                print(f"game {game}: depth {r.depth} gold {r.gold} xl {r.xlevel} "
                      f"steps {r.steps} :: {r.cause}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        for v in views:
            v.close()
    if results:
        depths = [r.depth for r in results]
        print(f"[{tuned}: {params_path}] " if params_path.exists() else "[default params] ", end="")
        print(f"{len(results)} games  avg depth {sum(depths) / len(depths):.1f}  "
              f"max depth {max(depths)}  avg gold {sum(r.gold for r in results) / len(results):.0f}")
        for i, r in enumerate(results, 1):
            print(f"  {i}: depth {r.depth} gold {r.gold} xl {r.xlevel} :: {r.cause}")
