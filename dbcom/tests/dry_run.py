"""Full game with a fake model (random legal-ish replies, no API calls) to exercise the game loop."""
import json, random, re, sys, logging, shutil
sys.path.insert(0, __file__.rsplit("/dbcom/", 1)[0])
from dbcom import client, game as G, protocol as P
from dbcom.tests.fake import fake_chat

random.seed(int(sys.argv[1]) if len(sys.argv) > 1 else 0)

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
