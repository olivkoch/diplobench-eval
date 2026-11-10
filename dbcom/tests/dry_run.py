"""Full game with a fake model (random legal-ish replies, no API calls) to exercise the game loop."""
import json, random, re, sys, logging, shutil
sys.path.insert(0, __file__.rsplit("/dbcom/", 1)[0])
from dbcom import client, game as G, protocol as P

random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 0)

def fake_chat(model, messages, schema_name, schema):
    last = messages[-1]["content"]
    if schema_name == "negotiation":
        out = {"messages": [{"recipients": [random.choice(["GLOBAL", "FRANCE", "ITALY"])], "body": "hello"}]}
        if random.random() < 0.05: out = {"messages": [{"recipients": ["GLOBAL"]}]}   # schema error
    elif schema_name == "memory":
        out = {"private_summary": f"memory written at {last.split(chr(10))[1]}"}
    else:
        src = next(m["content"] for m in reversed(messages) if re.search(r"^AVAILABLE ORDERS", m["content"], re.M))
        blocks = re.split(r"^[A-Z]{3}:\n", re.split(r"^AVAILABLE ORDERS", src, flags=re.M)[-1].split("\n\nPRIVATE MEMORY")[0].split("\n\nNEW PRESS")[0], flags=re.M)[1:]
        orders = [random.choice(re.findall(r"^- (.+)$", b, re.M)) for b in blocks if re.findall(r"^- (.+)$", b, re.M)]
        if random.random() < 0.1: orders.append("A XXX - YYY")  # illegal -> repair
        out = {"orders": orders, "draw_vote": False}
    return {"text": json.dumps(out), "thinking": "", "usage": {}, "finish_reason": "stop", "duration_s": 0, "http429_retries": 0}

client.chat = fake_chat
shutil.rmtree("/tmp/dbtest/dry", ignore_errors=True)
logging.basicConfig(level=logging.WARNING)
r = G.Runner({p: "fake" for p in P.POWERS}, "/tmp/dbtest/dry").run()
print(r)
ev = [json.loads(l) for l in open("/tmp/dbtest/dry/events.jsonl")]
from collections import Counter
print(Counter(e.get("stage") for e in ev if e["type"] == "request"))
print("repairs:", sum(1 for e in ev if e["type"] == "orders" and e["repair"]), "schema retries:",
      sum(len(e["attempts"]) - 1 for e in ev if e["type"] == "request"))
print("phases:", len(json.load(open("/tmp/dbtest/dry/game.json"))["phases"]))
