"""dbcom.run with the fake model, for campaign tests (DBCOM_RUN_MODULE=dbcom.tests.fake_run).
Every call costs FAKE_COST_USD. FAKE_CRASH_AT=<phase>: the process dies once when it reaches that phase."""
import os, random, sys, time
from dbcom import game as G, run as R
from dbcom.tests.fake import fake_chat

out = sys.argv[sys.argv.index("--out") + 1]
crash_at, marker = os.getenv("FAKE_CRASH_AT"), os.path.join(out, "crashed_once")


def chat(model, messages, schema_name, schema):
    if crash_at and messages[-1]["content"].find(f"({crash_at})") >= 0 and not os.path.exists(marker):
        open(marker, "w").close()
        os._exit(3)
    time.sleep(0.005)
    r = fake_chat(model, messages, schema_name, schema)
    r["usage"] = {"cost": float(os.getenv("FAKE_COST_USD", "0"))}
    return r


random.seed(hash(out) % 1000)
G.client.chat = chat
R.main()
