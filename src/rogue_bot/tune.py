"""Tune the bot's judgement numbers with Optuna by playing real games.

Each trial plays a batch of games in parallel and scores the average depth reached.
Progress lives in an SQLite study, so runs can be stopped and resumed. At the end the
best few candidates are replayed on fresh games against the current params, and only a
confirmed winner is saved for `rogue-bot` to use.
"""

import argparse
import json
import shutil
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import optuna

from .cli import ROGUE, exit_cleanly_on_signals, play_game
from .knowledge import DEFAULT_PATH as BOOK_PATH
from .knowledge import MonsterBook
from .params import Params, default_path
from .term import reap_orphans, state_dir


def _play(params: dict, book: str, rogue: str, max_steps: int) -> dict:
    """One game in a worker process, from a read-only copy of the monster book."""
    try:
        result = play_game(MonsterBook(Path(book), persist=False), Params(**params), rogue, max_steps)
        return asdict(result)
    except Exception as e:  # one broken game shouldn't end the study
        return {"depth": 0, "gold": 0, "xlevel": 0, "steps": 0, "cause": f"error: {e!r}"}


def suggest(trial: optuna.Trial) -> Params:
    defaults = Params()
    values = {}
    for name, (low, high) in Params.ranges().items():
        default = getattr(defaults, name)
        if isinstance(default, bool):
            values[name] = trial.suggest_categorical(name, [True, False])
        elif isinstance(default, int):
            values[name] = trial.suggest_int(name, low, high)
        else:
            values[name] = trial.suggest_float(name, low, high)
    return Params(**values)


class Runner:
    def __init__(self, pool, book: Path, rogue: str, max_steps: int, log: Path):
        self.pool, self.book, self.rogue, self.max_steps, self.log = pool, book, rogue, max_steps, log

    def evaluate(self, params: Params, games: int, label: str, trial: optuna.Trial | None = None,
                 prune_after: int = 8) -> float:
        futures = [self.pool.submit(_play, asdict(params), str(self.book), self.rogue, self.max_steps)
                   for _ in range(games)]
        depths = []
        try:
            for done in as_completed(futures):
                r = done.result()
                depths.append(r["depth"])
                with open(self.log, "a") as f:
                    f.write(json.dumps({"time": time.time(), "label": label, **r}) + "\n")
                if trial and len(depths) >= prune_after:
                    trial.report(statistics.mean(depths), len(depths))
                    if trial.should_prune():
                        print(f"  {label}: pruned after {len(depths)} games "
                              f"(avg depth {statistics.mean(depths):.2f})", flush=True)
                        raise optuna.TrialPruned()
        finally:
            for f in futures:
                f.cancel()
        score = statistics.mean(depths)
        print(f"  {label}: avg depth {score:.2f} over {len(depths)} games "
              f"(best {max(depths)}, median {statistics.median(depths)})", flush=True)
        return score


def main() -> None:
    ap = argparse.ArgumentParser(description="Tune rogue-bot's judgement by playing games.")
    ap.add_argument("--trials", type=int, default=40, help="new parameter sets to try (0 = just confirm)")
    ap.add_argument("--games", type=int, default=24, help="games per trial")
    ap.add_argument("--workers", type=int, default=12, help="games played at once")
    ap.add_argument("--max-steps", type=int, default=6000, help="cap per game, so stuck games end")
    ap.add_argument("--finalists", type=int, default=3, help="top trials to replay before choosing")
    ap.add_argument("--confirm", type=int, default=48, help="games per finalist in the replay")
    ap.add_argument("--db", type=Path, default=state_dir() / "tune.db")
    ap.add_argument("--save", type=Path, default=default_path(),
                    help="where a confirmed winner is saved; rogue-bot reads it from here")
    ap.add_argument("--rogue", default=ROGUE)
    args = ap.parse_args()
    exit_cleanly_on_signals()
    reap_orphans()

    # Every game starts from the same frozen monster book, so trials compare fairly.
    book = state_dir() / "tune-book.json"
    if BOOK_PATH.exists():
        shutil.copy(BOOK_PATH, book)
    elif not book.exists():
        book.write_text("{}")
    log = state_dir() / "tune-games.jsonl"

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        study_name="rogue-bot", storage=f"sqlite:///{args.db}", direction="maximize",
        load_if_exists=True, sampler=optuna.samplers.TPESampler(multivariate=True),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=8))
    if not study.trials:
        study.enqueue_trial(asdict(Params()))  # the hand-picked defaults are trial 0

    current_path = args.save
    current = Params.load(current_path) if current_path.exists() else Params()

    with ProcessPoolExecutor(args.workers) as pool:
        runner = Runner(pool, book, args.rogue, args.max_steps, log)

        def objective(trial: optuna.Trial) -> float:
            print(f"trial {trial.number}", flush=True)
            return runner.evaluate(suggest(trial), args.games, f"trial {trial.number}", trial)

        try:
            if args.trials:
                study.optimize(objective, n_trials=args.trials)
        except KeyboardInterrupt:
            print("stopped; progress is saved in", args.db)
            return

        done = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not done:
            return
        finalists = sorted(done, key=lambda t: t.value, reverse=True)[: args.finalists]
        print(f"\nreplaying {len(finalists)} finalists and the current params, "
              f"{args.confirm} fresh games each", flush=True)
        contenders = {"current": current}
        contenders |= {f"trial {t.number}": Params(**t.params) for t in finalists}
        scores = {name: runner.evaluate(p, args.confirm, f"confirm {name}")
                  for name, p in contenders.items()}

    winner = max(scores, key=scores.get)
    print("\nconfirmed averages: " + ", ".join(f"{n} {s:.2f}" for n, s in scores.items()))
    if winner == "current":
        print("the current params held up; nothing changed")
        return
    contenders[winner].save(current_path, source=winner, confirmed_avg_depth=scores[winner],
                            replaced_avg_depth=scores["current"], confirm_games=args.confirm)
    print(f"saved {winner} to {current_path}: avg depth {scores['current']:.2f} -> {scores[winner]:.2f}")
