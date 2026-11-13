"""Summarize a campaign from its committed results.

    python3 results/summarize.py results/c1                 # tables from results/c1/games/*/result.json
    python3 results/summarize.py results/c1 --events runs/c1 # also rebuild calls.csv from the full event logs

Writes per_game.csv, models.csv (and calls.csv with --events) into the results directory and prints the
Markdown tables used in its README.
"""
import argparse
import csv
import glob
import json
import os
import statistics
from collections import Counter, defaultdict

FOCUS = "mistral-large-4"


def short(model):
    return model.split("/")[-1]


def load_games(d):
    games = []
    for f in sorted(glob.glob(os.path.join(d, "games", "game-*", "result.json"))):
        r = json.load(open(f))
        r["game"] = os.path.basename(os.path.dirname(f))
        games.append(r)
    return games


def outcome(r, power):
    c = r["centers"][power]
    if c == 0:
        return "eliminated"
    if r["reason"] == "solo":
        return "solo" if c >= 18 else "lost"
    if r["reason"] == "stalemate_vote":
        return "draw"
    return "survived"  # cutoff after W1920A


def ranks(centers):
    """Rank within the game by supply centers (1 = most); ties share the mean rank."""
    order = sorted(centers.values(), reverse=True)
    return {p: statistics.mean(i + 1 for i, v in enumerate(order) if v == c) for p, c in centers.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--events", help="full run directory (runs/<name>) to rebuild calls.csv from events.jsonl")
    a = ap.parse_args()
    games = load_games(a.dir)

    rows = []
    for r in games:
        rk = ranks(r["centers"])
        for p, m in r["models"].items():
            rows.append({"game": r["game"], "power": p, "model": short(m), "centers": r["centers"][p],
                         "rank": rk[p], "outcome": outcome(r, p), "end": r["reason"], "final_phase": r["phase"]})
    with open(os.path.join(a.dir, "per_game.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    by_model = defaultdict(list)
    for x in rows:
        by_model[x["model"]].append(x)
    models = []
    for m, xs in by_model.items():
        sc = [x["centers"] for x in xs]
        oc = Counter(x["outcome"] for x in xs)
        models.append({"model": m, "games": len(xs), "mean_centers": round(statistics.mean(sc), 2),
                       "se": round(statistics.stdev(sc) / len(sc) ** 0.5, 2),
                       "mean_rank": round(statistics.mean(x["rank"] for x in xs), 2),
                       "solos": oc["solo"], "draws": oc["draw"], "eliminations": oc["eliminated"]})
    models.sort(key=lambda x: -x["mean_centers"])
    with open(os.path.join(a.dir, "models.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(models[0]))
        w.writeheader()
        w.writerows(models)

    print("| # | Model | Mean SCs (± s.e.) | Mean rank in game | Solos | Draws | Eliminations |")
    print("|---|---|---|---|---|---|---|")
    for i, x in enumerate(models, 1):
        b = "**" if x["model"] == FOCUS else ""
        print(f"| {i} | {b}{x['model']}{b} | {b}{x['mean_centers']:.2f} ± {x['se']:.2f}{b} | {x['mean_rank']:.2f} | "
              f"{x['solos']} | {x['draws']} | {x['eliminations']} |")

    print("\n| Game | ML4 power | ML4 SCs | Rank | Game end | Final SCs (A E F G I R T) |")
    print("|---|---|---|---|---|---|")
    for r in games:
        p = next(p for p, m in r["models"].items() if short(m) == FOCUS)
        sc = " ".join(str(r["centers"][q]) for q in sorted(r["centers"]))
        print(f"| {r['game']} | {p.title()} | {r['centers'][p]} | {ranks(r['centers'])[p]:g} | "
              f"{r['reason']} ({r['phase']}) | {sc} |")

    if a.events:
        calls = defaultdict(Counter)
        for f in sorted(glob.glob(os.path.join(a.events, "game-*", "events.jsonl"))):
            for line in open(f):
                e = json.loads(line)
                if e["type"] != "request":
                    continue
                c = calls[short(e["model"])]
                c["requests"] += 1
                c["attempts"] += len(e["attempts"])
                if not e.get("accepted"):
                    c["agent_failures"] += 1
                    c[f"agent_failures_{e['stage']}"] += 1
                for at in e["attempts"]:
                    u = at.get("usage") or {}
                    c["truncated_attempts"] += at.get("finish_reason") == "length"
                    c["prompt_tokens"] += u.get("prompt_tokens") or 0
                    c["completion_tokens"] += u.get("completion_tokens") or 0
                    c["cost_microusd"] += round((u.get("cost") or 0) * 1e6)
        keys = ["requests", "attempts", "agent_failures", "truncated_attempts", "prompt_tokens",
                "completion_tokens", "cost_usd"]
        keys += sorted({k for c in calls.values() for k in c if k.startswith("agent_failures_")})
        with open(os.path.join(a.dir, "calls.csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["model"] + keys)
            for m in sorted(calls, key=lambda m: [x["model"] for x in models].index(m)):
                c = calls[m]
                c["cost_usd"] = round(c["cost_microusd"] / 1e6, 2)
                w.writerow([m] + [c[k] for k in keys])

    calls_csv = os.path.join(a.dir, "calls.csv")
    if os.path.exists(calls_csv):
        print("\n| Model | Requests | Retried attempts | Agent failures | Cut off at 16000 tokens | Tokens out | Cost |")
        print("|---|---|---|---|---|---|---|")
        for c in csv.DictReader(open(calls_csv)):
            n, at = int(c["requests"]), int(c["attempts"])
            print(f"| {c['model']} | {n} | {at - n} ({100 * (at - n) / at:.1f}%) | {c['agent_failures']} | "
                  f"{c['truncated_attempts']} ({100 * int(c['truncated_attempts']) / at:.1f}%) | "
                  f"{int(c['completion_tokens']) / 1e6:.1f}M | ${float(c['cost_usd']):.2f} |")


if __name__ == "__main__":
    main()
