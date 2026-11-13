"""Run a campaign: several games in parallel, each in its own process, with crash recovery and a budget.

  python3 -m dbcom.campaign --name c1 --budget-usd 1000 --parallel 7 --games 7 \\
      --models mistral/mistral-large-4,azure_ai/gpt-5.6-sol,vertex_ai/claude-opus-5,...

Power assignment: exactly 7 models, assigned by Latin squares, so that over each block of 7 games every model
plays every power exactly once. Block b uses power j -> models[(k_b * j - g) mod 7] with a different
multiplier k_b per block, so that the neighbours each model faces also change between blocks.

Everything lives in runs/<name>/: campaign.json (lineups), status.txt (refreshed every poll),
campaign.log, and one directory per game (see dbcom.run). The supervisor can be stopped and restarted at any
time: it adopts games that are still running (pid file) and resumes the others with --resume.
A game whose process exits without result.json is resumed, up to --max-restarts times.
Budget: no new game is started once the total gateway spend reaches --budget-usd; at 1.25x the budget running
games are stopped (they can be resumed later by raising the budget and restarting the supervisor).
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time

from . import protocol as P

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MULTIPLIERS = [1, 3, 2, 5, 4, 6]  # coprime with 7 -> each gives a Latin square
POLL_S = float(os.getenv("DBCOM_POLL_S", "30"))  # tests use a short poll
logger = logging.getLogger("campaign")


def lineups(models, n_games):
    assert len(models) == 7, "a campaign needs exactly 7 models"
    out = []
    for g in range(n_games):
        k = MULTIPLIERS[(g // 7) % len(MULTIPLIERS)]
        out.append({p: models[(k * j - g) % 7] for j, p in enumerate(P.POWERS)})
    return out


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie child still answers kill(0): reap it
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True  # not our child (launched by a previous supervisor) and alive


class Campaign:
    def __init__(self, args):
        self.args = args
        self.dir = os.path.join(ROOT, "runs", args.name)
        os.makedirs(self.dir, exist_ok=True)
        cfg_path = os.path.join(self.dir, "campaign.json")
        cfg = read_json(cfg_path)
        if cfg is None:
            cfg = {"models": args.models, "lineups": lineups(args.models, args.games), "stop_after": args.stop_after}
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=1)
        elif args.games > len(cfg["lineups"]):  # extend an existing campaign with more games
            cfg["lineups"] = lineups(cfg["models"], args.games)
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=1)
        self.cfg = cfg
        self.games = [f"game-{i:02d}" for i in range(len(cfg["lineups"]))]
        self.restarts = {g: 0 for g in self.games}
        self.failed = set()
        self.last_exit = {}

    def gdir(self, g):
        return os.path.join(self.dir, g)

    def pid(self, g):
        try:
            with open(os.path.join(self.gdir(g), "pid")) as f:
                return int(f.read())
        except (OSError, ValueError):
            return None

    def state(self, g):
        if os.path.exists(os.path.join(self.gdir(g), "result.json")):
            return "done"
        if g in self.failed:
            return "failed"
        pid = self.pid(g)
        if pid and pid_alive(pid):
            return "running"
        if os.path.exists(os.path.join(self.gdir(g), "pid")):
            return "crashed"
        return "pending"

    def spend(self):
        return sum((read_json(os.path.join(self.gdir(g), "progress.json")) or {}).get("spend_usd", 0) for g in self.games)

    def launch(self, g):
        lineup = self.cfg["lineups"][self.games.index(g)]
        d = self.gdir(g)
        os.makedirs(d, exist_ok=True)
        cmd = [sys.executable, "-m", os.getenv("DBCOM_RUN_MODULE", "dbcom.run"), "--out", d]  # tests use a fake
        for p, m in lineup.items():
            cmd += ["--model", f"{p}={m}"]
        if self.cfg.get("stop_after"):
            cmd += ["--stop-after", self.cfg["stop_after"]]
        if os.path.exists(os.path.join(d, "game.json")):
            cmd.append("--resume")
        with open(os.path.join(d, "stdout.log"), "a") as out:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        with open(os.path.join(d, "pid"), "w") as f:
            f.write(str(proc.pid))
        logger.info(f"launched {g} pid={proc.pid} {'(resume)' if '--resume' in cmd else ''}")

    def write_status(self):
        focus = self.cfg["models"][0]
        rows = [f"{time.strftime('%Y-%m-%d %H:%M:%S')}  spend ${self.spend():.2f} / budget ${self.args.budget_usd:.0f}",
                f"{'game':<8} {'state':<8} {'phase':<9} {'focus power':<12} {'focus SC':>8}  {'SCs (A E F G I R T)':<22} "
                f"{'spend':>7} {'fails':>5} {'restarts':>8}  result"]
        for i, g in enumerate(self.games):
            lineup = self.cfg["lineups"][i]
            fp = next(p for p, m in lineup.items() if m == focus)
            pr = read_json(os.path.join(self.gdir(g), "progress.json")) or {}
            res = read_json(os.path.join(self.gdir(g), "result.json")) or {}
            c = pr.get("centers", {})
            rows.append(f"{g:<8} {self.state(g):<8} {pr.get('phase', '-'):<9} {fp:<12} {c.get(fp, '-'):>8}  "
                        f"{' '.join(f'{c.get(p, 0):>2}' for p in P.POWERS):<22} ${pr.get('spend_usd', 0):>6.2f} "
                        f"{pr.get('agent_failures', 0):>5} {self.restarts[g]:>8}  {res.get('reason', '')}")
        with open(os.path.join(self.dir, "status.txt"), "w") as f:
            f.write("\n".join(rows) + "\n")

    def run(self):
        logger.info(f"campaign {self.args.name}: {len(self.games)} games, parallel={self.args.parallel}, "
                    f"budget=${self.args.budget_usd}")
        while True:
            spend = self.spend()
            states = {g: self.state(g) for g in self.games}
            if spend >= 1.25 * self.args.budget_usd:
                for g, s in states.items():
                    if s == "running":
                        logger.error(f"spend ${spend:.2f} >= 1.25 x budget: stopping {g}")
                        os.killpg(self.pid(g), signal.SIGTERM)
                self.write_status()
                return
            for g, s in states.items():
                if s == "crashed":
                    if self.restarts[g] >= self.args.max_restarts:
                        logger.error(f"{g} crashed {self.restarts[g]} times: giving up on it")
                        self.failed.add(g)
                        continue
                    if time.time() - self.last_exit.setdefault(g, time.time()) < 2 * POLL_S:
                        continue  # cool down before resuming
                    self.restarts[g] += 1
                    logger.warning(f"{g} exited without result: resume #{self.restarts[g]}")
                    self.launch(g)
                    self.last_exit.pop(g, None)
            states = {g: self.state(g) for g in self.games}
            running = sum(s in ("running", "crashed") for s in states.values())  # a crashed game keeps its slot
            for g, s in states.items():
                if s == "pending" and running < self.args.parallel and spend < self.args.budget_usd:
                    self.launch(g)
                    running += 1
                    time.sleep(min(20, POLL_S))  # stagger starts so that games don't stay in lockstep
            self.write_status()
            states = {g: self.state(g) for g in self.games}
            active = [g for g, s in states.items() if s in ("running", "crashed")]
            pending = [g for g, s in states.items() if s == "pending"]
            if not active and (not pending or spend >= self.args.budget_usd):
                logger.info(f"campaign over: {json.dumps({s: sum(v == s for v in states.values()) for s in set(states.values())})}, "
                            f"spend ${spend:.2f}")
                return
            time.sleep(POLL_S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--models", type=lambda s: s.split(","), help="7 comma-separated models; the first is the focus")
    ap.add_argument("--games", type=int, default=7)
    ap.add_argument("--parallel", type=int, default=7)
    ap.add_argument("--budget-usd", type=float, required=True)
    ap.add_argument("--max-restarts", type=int, default=5)
    ap.add_argument("--stop-after", help="stop each game after this phase (for tests)")
    args = ap.parse_args()
    d = os.path.join(ROOT, "runs", args.name)
    os.makedirs(d, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(os.path.join(d, "campaign.log"))])
    if args.models is None:
        args.models = (read_json(os.path.join(d, "campaign.json")) or {}).get("models")
        assert args.models, "--models is required for a new campaign"
    Campaign(args).run()


if __name__ == "__main__":
    main()
