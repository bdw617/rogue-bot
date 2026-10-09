"""Summarise a benchmark: results log plus (optionally) the decision traces."""

import argparse
import collections
import glob
import json
import re
import statistics


def load_games(log: str) -> list[dict]:
    return [json.loads(line) for line in open(log) if line.strip()]


def load_traces(pattern: str) -> list[dict]:
    """Each game's decisions, split on the trace's '# new game' / '# end:' markers."""
    games = []
    for path in sorted(glob.glob(pattern)):
        rows = None
        for line in open(path):
            if line.startswith("# new game"):
                rows = []
            elif line.startswith("# end:") and rows is not None:
                games.append({"cause": line[7:].strip(), "rows": rows})
                rows = None
            elif rows is not None and not line.startswith("#"):
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 8:
                    rows.append(parts)
    return games


def action(note: str) -> str:
    return re.sub(r" (?:->|\(|(?:at|for|to|from|on|toward)\b).*", "", note).strip()


def report(log: str, traces: str | None) -> str:
    games = load_games(log)
    depths = [g["depth"] for g in games]
    out = [f"# Benchmark: {log}", ""]
    labels = sorted({g.get("label", "") for g in games} - {""})
    timing = sorted({(g.get("read_wait_ms"), g.get("read_idle_ms")) for g in games})
    out += [f"- games: {len(games)}" + (f" ({', '.join(labels)})" if labels else ""),
            f"- read timing (wait ms, quiet-gap ms): {timing}"]

    se = statistics.stdev(depths) / len(depths) ** 0.5 if len(depths) > 1 else 0
    out += ["", "## Depth",
            f"- average {statistics.mean(depths):.2f} ± {se:.2f}, median {statistics.median(depths)}, "
            f"best {max(depths)}, reached 5+: {sum(d >= 5 for d in depths)}"]
    hist = collections.Counter(depths)
    out += ["- distribution: " + ", ".join(f"d{d}: {hist[d]}" for d in sorted(hist))]
    out += [f"- average gold {statistics.mean(g['gold'] for g in games):.0f}, "
            f"experience level {statistics.mean(g['xlevel'] for g in games):.1f}"]

    timed = [g for g in games if g.get("seconds")]
    if timed:
        rates = [g["steps"] / g["seconds"] for g in timed]
        span = max(g["time"] for g in games) - min(g["time"] - g.get("seconds", 0) for g in games)
        out += ["", "## Speed",
                f"- {statistics.mean(rates):.0f} steps/s per game (median {statistics.median(rates):.0f}), "
                f"{statistics.mean(g['seconds'] for g in timed):.0f} s per game on average",
                f"- {len(games)} games in {span / 60:.1f} min wall clock "
                f"({len(games) / (span / 60):.1f} games/min)"]

    causes = collections.Counter(g["cause"].split(" with")[0] for g in games)
    out += ["", "## How games ended"] + [f"- {n} × {c}" for c, n in causes.most_common()]

    if traces:
        tgames = load_traces(traces)
        rows = [r for g in tgames for r in g["rows"]]
        if rows:
            acts = collections.Counter(action(r[7]) for r in rows)
            out += ["", f"## What the bot spent its steps on ({len(rows)} steps)"]
            out += [f"- {100 * n / len(rows):4.1f}% {a}" for a, n in acts.most_common(12)]
            by_depth, searching = collections.Counter(), collections.Counter()
            for r in rows:
                by_depth[r[1]] += 1
                searching[r[1]] += r[7].startswith(("go search", "search here"))
            out += ["", "## Share of steps spent searching, by depth",
                    "- " + ", ".join(f"{d}: {100 * searching[d] / by_depth[d]:.0f}%"
                                     for d in sorted(by_depth, key=lambda d: int(d[1:])))]
            flags = {"unseen attacker": "unseen", "watchdog": "watchdog", "kiting": "kite ",
                     "emergency items": "EMERGENCY", "fled to stairs": "FLEE"}
            out += ["", "## Notable events"] + [
                f"- {name}: {sum(key in r[7] for r in rows)} steps" for name, key in flags.items()]
            starved = [g for g in tgames if "starvation" in g["cause"] and g["rows"]]
            if starved:
                out += ["", "## Starvations: what the final level looked like"]
                for g in starved:
                    last = g["rows"][-1][1]
                    level = [r for r in g["rows"] if r[1] == last]
                    top = collections.Counter(action(r[7]) for r in level).most_common(4)
                    out.append(f"- {last}, {len(level)} steps there: "
                               + ", ".join(f"{a} {n}" for a, n in top))
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Summarise a rogue-bot benchmark.")
    ap.add_argument("log", help="results log written with --log")
    ap.add_argument("--traces", help="glob of trace files written with --trace")
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args()
    text = report(args.log, args.traces)
    print(text, end="")
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
