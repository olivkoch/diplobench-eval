"""Resume checks, no API calls (fake model from fake.py).

1. After every phase of a full game, a game reloaded from the saved file must give the same prompts as the
   live game (what --resume relies on).
2. A game process killed midway and restarted with --resume must reach the end with a continuous phase history.
"""
import json, os, shutil, signal, subprocess, sys, time
ROOT = __file__.rsplit("/dbcom/", 1)[0]
sys.path.insert(0, ROOT)
from diplomacy.utils.export import from_saved_game_format, to_saved_game_format
from dbcom import game as G, protocol as P

# ---------------------------------------------------------------- 1. prompt equality after reload
from dbcom.tests.fake import fake_chat
G.client.chat = fake_chat

checked, mismatches = 0, 0


def prompts(game, mem):
    out = {}
    for p in P.POWERS:
        legal = P.legal_orders_by_loc(game, p)
        req = P.required_orders(game, p, legal)
        if game.phase_type == "M":
            out[p] = (P.season_opening_prompt(game, p, mem, legal, req, []), P.orders_prompt(game, p, req, []))
        else:
            out[p] = (P.retreat_or_adjustment_prompt(game, p, legal, req, mem),)
    return out


class CheckingRunner(G.Runner):
    def _save(self):
        global checked, mismatches
        super()._save()
        loaded = from_saved_game_format(json.load(open(os.path.join(self.out_dir, "game.json"))))
        assert loaded.get_current_phase() == self.game.get_current_phase()
        if self.game.get_current_phase() == "COMPLETED":
            return
        a, b = prompts(self.game, "mem"), prompts(loaded, "mem")
        checked += 1
        if a != b:
            mismatches += 1
            print("MISMATCH at", self.game.get_current_phase())
        # memory prompts look at the history of the season just played
        hist = [h.name for h in self.game.get_phase_history()]
        assert hist == [h.name for h in loaded.get_phase_history()]


shutil.rmtree("/tmp/dbtest/resume1", ignore_errors=True)
r = CheckingRunner({p: "fake" for p in P.POWERS}, "/tmp/dbtest/resume1").run()
print(f"1. reload check: {checked} phases checked, {mismatches} mismatches; result {r['reason']} at {r['phase']}")

# ---------------------------------------------------------------- 2. kill + --resume
FAKE_RUN = f"""
import sys; sys.path.insert(0, {ROOT!r}); import random, time, dbcom.tests.fake as D, dbcom.game as G, dbcom.run as R
random.seed(5)
def slow(*a, **k):
    time.sleep(0.002); return D.fake_chat(*a, **k)
G.client.chat = slow
sys.argv = ['run', '--out', '/tmp/dbtest/resume2', '--default-model', 'fake'] + {{extra}}
R.main()
"""
out = "/tmp/dbtest/resume2"
shutil.rmtree(out, ignore_errors=True)
proc = subprocess.Popen([sys.executable, "-c", FAKE_RUN.replace("{extra}", "[]")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
while not os.path.exists(f"{out}/progress.json") or json.load(open(f"{out}/progress.json"))["phase"] < "S1905M" \
        or json.load(open(f"{out}/progress.json"))["phase"][1:5] < "1905":
    time.sleep(0.2)
time.sleep(0.5)
proc.send_signal(signal.SIGKILL)
proc.wait()
killed_at = json.load(open(f"{out}/progress.json"))["phase"]
subprocess.run([sys.executable, "-c", FAKE_RUN.replace("{extra}", "['--resume']")],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
ev = [json.loads(l) for l in open(f"{out}/events.jsonl")]
res = json.load(open(f"{out}/result.json"))
phases = [p["name"] for p in json.load(open(f"{out}/game.json"))["phases"]]
years = sorted({int(p[1:5]) for p in phases if p[1:5].isdigit()})
print(f"2. killed with progress at {killed_at}; resume events: {[e['phase'] for e in ev if e['type'] == 'resume']}; "
      f"final: {res['reason']} at {res['phase']}; years {years[0]}..{years[-1]} contiguous={years == list(range(years[0], years[-1] + 1))}; "
      f"{len(phases)} phases; game_end events: {sum(e['type'] == 'game_end' for e in ev)}")
