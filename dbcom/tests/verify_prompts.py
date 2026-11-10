"""Replay c01-g01 (diplobench.com) through python-diplomacy and diff our prompts against their published ones.
Run fetch_reference.py first to download the reference files."""
import difflib, json, os, sys
from diplomacy import Game
ROOT = __file__.rsplit("/dbcom/", 1)[0]
sys.path.insert(0, ROOT)
from dbcom import protocol as P

D = os.path.join(ROOT, "reference", "c01-g01")
phases = ["S1901M", "F1901M", "F1901R", "W1901A", "S1902M"]
game, ok, bad = Game(), 0, 0

def check(label, ours, theirs):
    global ok, bad
    if ours == theirs:
        ok += 1; return
    bad += 1
    print(f"--- MISMATCH {label}")
    for l in list(difflib.unified_diff(theirs.splitlines(), ours.splitlines(), "theirs", "ours", lineterm="", n=0))[:12]:
        print("   ", l[:160])

memory = {}
season_conv_phases = {}
for ph in phases:
    pdata = json.load(open(f"{D}/{ph}.phase.json"))
    traces = json.load(open(f"{D}/{ph}.traces.json"))["traces"]
    assert game.get_current_phase() == ph, (game.get_current_phase(), ph)
    print(ph, "message keys:", list(pdata["messages"][0]) if pdata["messages"] else None)
    for power, trs in traces.items():
        legal = P.legal_orders_by_loc(game, power)
        req = P.required_orders(game, power, legal)
        for tr in trs:
            st, rnd, theirs = tr["stage"], tr["round"], tr["request"]["userPrompt"]
            vis = lambda r: [dict(m, seq=i) for i, m in enumerate(pdata["messages"]) if m["round"] == r and
                             (m["sender"] == power or power in m["recipients"] or "GLOBAL" in m["recipients"])]
            if st == "negotiation" and rnd == 1:
                mem = tr["request"]["userPrompt"].split("PRIVATE MEMORY FROM EARLIER SEASONS\n")[1].split("\n\nAVAILABLE ORDERS")[0]
                ours = P.season_opening_prompt(game, power, None if mem == "(none yet)" else mem, legal, req, [])
            elif st == "negotiation":
                ours = P.negotiation_round_prompt(game, power, rnd, vis(rnd - 1))
            elif st == "orders":
                ours = P.orders_prompt(game, power, req, vis(P.NEGOTIATION_ROUNDS))
            elif st in ("retreat", "adjustment"):
                mem = None
                if st == "adjustment":
                    mem = theirs.split("PRIVATE MEMORY FROM EARLIER SEASONS\n")[1].split("\n\nNEW PRESS")[0]
                ours = P.retreat_or_adjustment_prompt(game, power, legal, req, mem)
            else:
                continue  # memory prompts are checked after the season resolves
            check(f"{ph} {power} {st} r{rnd}", ours, theirs)
    orders = pdata["orders"]
    for power, os_ in orders.items():
        game.set_orders(power, [o for o in os_ if o != "WAIVE"])
    game.process()
    # memory prompts (asked after retreats): check them once the season is fully resolved
    if game.phase_type != "R":
        hist = game.get_phase_history()
        season = [h for h in hist[-2:] if h.name[0] in "SF" and h.name[-1] in "MR"]
        if season and season[-1].name == hist[-1].name:
            mv = [h for h in season if h.name[-1] == "M"][-1].name
            adj = [h for h in season if h.name[:5] == mv[:5]]
            mt = json.load(open(f"{D}/{mv}.traces.json"))["traces"]
            for power, trs in mt.items():
                for tr in trs:
                    if tr["stage"] == "memory":
                        check(f"{mv} {power} memory", P.memory_prompt(game, power, mv, adj), tr["request"]["userPrompt"])

print(f"\n{ok} prompts identical, {bad} mismatches")
