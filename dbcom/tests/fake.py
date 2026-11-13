"""Fake model: random legal-ish replies, with occasional schema errors and illegal orders. No API calls."""
import json, random, re


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
