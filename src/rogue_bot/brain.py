"""Show what rogue-bot has learned so far and where it keeps it."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .knowledge import DEFAULT_PATH as BOOK_PATH
from .knowledge import MonsterBook
from .params import Params, default_path
from .term import state_dir


def show_book(path: Path) -> None:
    print(f"Monster book: {path}")
    if not path.exists():
        print("  empty: nothing fought yet\n")
        return
    book = MonsterBook(path, persist=False).known
    print(f"  {len(book)} monsters, updated after every game rogue-bot plays")
    print(f"  {'monster':15s} {'swings at us':>12s} {'hit %':>6s} {'dmg/hit':>8s} {'worst':>6s}"
          f" {'our swings/kill':>15s} {'killed bot':>10s} {'ran / caught':>12s}")
    for name, r in sorted(book.items(), key=lambda kv: -kv[1].attacks):
        hit = f"{100 * r.hits / r.attacks:.0f}" if r.attacks else "-"
        dmg = f"{r.damage / r.damaged_hits:.1f}" if r.damaged_hits else "-"
        swings = f"{r.kill_swings / r.kills:.1f}" if r.kills else "-"
        print(f"  {name:15s} {r.attacks:12d} {hit:>6s} {dmg:>8s} {r.max_hit:6d}"
              f" {swings:>15s} {r.deaths:10d} {f'{r.kite_runs} / {r.caught}':>12s}")
    print()


def show_params(path: Path) -> None:
    print(f"Judgement numbers: {path}")
    if not path.exists():
        print("  not tuned yet: playing with the defaults in params.py\n")
        return
    info = json.loads(path.read_text())
    tuned, defaults = asdict(Params.load(path)), asdict(Params())
    if "confirmed_avg_depth" in info:
        print(f"  from {info.get('source')}: avg depth {info['replaced_avg_depth']:.2f} -> "
              f"{info['confirmed_avg_depth']:.2f} over {info.get('confirm_games')} confirmation games")
    changed = [k for k in defaults if tuned[k] != defaults[k]]
    for k in changed:
        fmt = (lambda v: f"{v:.2f}") if isinstance(defaults[k], float) else str
        print(f"  {k:20s} {fmt(defaults[k]):>6s} -> {fmt(tuned[k])}")
    if not changed:
        print("  same as the defaults")
    print()


def show_tuning(db: Path) -> None:
    print(f"Tuning progress: {db}")
    if not db.exists():
        print("  no tuning runs yet: run `uv run rogue-bot-tune`\n")
        return
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.load_study(study_name="rogue-bot", storage=f"sqlite:///{db}")
    states = [t.state.name for t in study.trials]
    done = [t for t in study.trials if t.value is not None]
    print(f"  {len(states)} trials: {states.count('COMPLETE')} finished, "
          f"{states.count('PRUNED')} stopped early, {states.count('RUNNING')} running")
    if done:
        best = max(done, key=lambda t: t.value)
        baseline = study.trials[0].value
        base = f" (defaults scored {baseline:.2f})" if baseline is not None else ""
        print(f"  best so far: trial {best.number}, avg depth {best.value:.2f}{base}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Show what rogue-bot has learned.")
    ap.add_argument("--book", type=Path, default=BOOK_PATH)
    ap.add_argument("--params", type=Path, default=default_path())
    ap.add_argument("--db", type=Path, default=state_dir() / "tune.db")
    args = ap.parse_args()
    show_book(args.book)
    show_params(args.params)
    show_tuning(args.db)
