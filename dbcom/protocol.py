"""Prompt text, response schemas and response validation, replicating the
diplobench.com (openrouter-38-v2) harness as observed in its published traces.

Every prompt builder here reproduces the exact wording of their userPrompt
fields; see /tmp/dbcom/*.traces.json for the reference transcripts.
"""
import json
import os
import re

POWERS = ["AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY"]
NEGOTIATION_ROUNDS = 3
MAX_MESSAGES = 14
MAX_MESSAGE_CHARS = 1200
MAX_MEMORY_CHARS = 4000
LAST_YEAR = 1920  # the game ends after the complete 1920 game-year

with open(os.path.join(os.path.dirname(__file__), "system_prompt.txt")) as _f:
    _SYSTEM_TEMPLATE = _f.read()


def system_prompt(power):
    return _SYSTEM_TEMPLATE.replace("{POWER}", power)


# ---------------------------------------------------------------- schemas

# The schemas are the league's `responseSchema`s, verbatim (see verify_prompts.py). The message count/length
# and recipient limits are not in the league's negotiation schema: they are only checked in parse_response.

def negotiation_schema():
    return {
        "type": "object", "additionalProperties": False, "required": ["messages"],
        "properties": {"messages": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False, "required": ["recipients", "body"],
                "properties": {
                    "recipients": {"type": "array", "items": {"type": "string", "enum": POWERS + ["GLOBAL"]}},
                    "body": {"type": "string"},
                },
            },
        }},
    }


def orders_schema():
    return {
        "type": "object", "additionalProperties": False, "required": ["orders", "draw_vote"],
        "properties": {"orders": {"type": "array", "items": {"type": "string"}},
                       "draw_vote": {"type": "boolean",
                                     "description": "Whether you vote to end the game now as an agreed stalemate."}},
    }


def memory_schema():
    return {
        "type": "object", "additionalProperties": False, "required": ["private_summary"],
        "properties": {"private_summary": {
            "type": "string", "maxLength": MAX_MEMORY_CHARS,
            "description": "Complete private memory to carry into future seasons, including important commitments, "
                           "relationships, betrayals, and plans."}},
    }


SCHEMAS = {"negotiation": negotiation_schema, "orders": orders_schema, "memory": memory_schema}


# ---------------------------------------------------------------- validation

class ProtocolError(Exception):
    pass


def _extract_json(text):
    """Parse the first JSON object in the reply (tolerates ```json fences)."""
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start:end + 1]) if start >= 0 else None
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        raise ProtocolError("response did not contain a valid JSON object")
    return data


def _check_keys(obj, required, where="response"):
    missing = [k for k in required if k not in obj]
    unknown = [k for k in obj if k not in required]
    if missing:
        raise ProtocolError(f"{where} is missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise ProtocolError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")


def _destringify(data, key):
    """Claude on the gateway (LiteLLM turns response_format into a forced tool call) sometimes returns an
    array field as a string holding the JSON array followed by junk, e.g. '[...]}\\n'. Decode it, as the
    league's native structured output would never produce this."""
    if isinstance(data.get(key), str):
        try:
            value = json.JSONDecoder(strict=False).raw_decode(data[key].strip())[0]
        except json.JSONDecodeError:
            return
        if isinstance(value, list):
            data[key] = value


def parse_response(stage, text, power, surviving):
    """Validate a raw reply against the stage schema. Returns the parsed object or raises ProtocolError."""
    data = _extract_json(text)
    _destringify(data, {"negotiation": "messages", "orders": "orders"}.get(stage, ""))
    if stage == "negotiation":
        _check_keys(data, ["messages"])
        msgs = data["messages"]
        if not isinstance(msgs, list):
            raise ProtocolError("messages must be an array")
        if len(msgs) > MAX_MESSAGES:
            raise ProtocolError(f"too many messages: {len(msgs)} > {MAX_MESSAGES}")
        for m in msgs:
            if not isinstance(m, dict):
                raise ProtocolError("each message must be an object")
            _check_keys(m, ["recipients", "body"])
            rec, body = m["recipients"], m["body"]
            if not isinstance(rec, list) or not rec or not all(isinstance(r, str) for r in rec):
                raise ProtocolError("recipients must be a non-empty array of strings")
            if not isinstance(body, str):
                raise ProtocolError("body must be a string")
            if len(body) > MAX_MESSAGE_CHARS:
                raise ProtocolError(f"message body exceeds {MAX_MESSAGE_CHARS} characters")
            if "GLOBAL" in rec and len(rec) > 1:
                raise ProtocolError("GLOBAL cannot be combined with named powers")
            for r in rec:
                if r != "GLOBAL" and (r == power or r not in surviving):
                    raise ProtocolError(f"invalid recipient: {r}")
        return data
    if stage == "orders":
        _check_keys(data, ["orders", "draw_vote"])
        if not isinstance(data["orders"], list) or not all(isinstance(o, str) for o in data["orders"]):
            raise ProtocolError("orders must be an array of strings")
        if not isinstance(data["draw_vote"], bool):
            raise ProtocolError("draw_vote must be a boolean")
        return data
    if stage == "memory":
        _check_keys(data, ["private_summary"])
        if not isinstance(data["private_summary"], str):
            raise ProtocolError("private_summary must be a string")
        if len(data["private_summary"]) > MAX_MEMORY_CHARS:
            raise ProtocolError(f"private_summary exceeds {MAX_MEMORY_CHARS} characters")
        return data
    raise ValueError(stage)


def schema_retry_prompt(error):
    return (f"Your previous reply could not be accepted: ProtocolError: {error}. "
            "Please reply again using the required response format.")


# ---------------------------------------------------------------- game-state text

def phase_title(game):
    return f"{game.phase} ({game.get_current_phase()})"


def position_body(game):
    """'Phase: ...' + Units + Supply centers (+ dislodged units in retreat phases)."""
    lines = [f"Phase: {phase_title(game)}", "Units:"]
    for p in POWERS:
        units = sorted(game.get_units(p))
        lines.append(f"- {p}: {', '.join(units) if units else '(none)'}")
    lines.append("Supply centers:")
    owned = set()
    for p in POWERS:
        centers = sorted(game.get_centers(p))
        owned.update(centers)
        lines.append(f"- {p} ({len(centers)}): {', '.join(centers) if centers else '(none)'}")
    neutral = sorted(set(game.map.scs) - owned)
    lines.append(f"- NEUTRAL ({len(neutral)}): {', '.join(neutral) if neutral else '(none)'}")
    if game.phase_type == "R":
        lines.append("Dislodged units and retreat destinations:")
        for p in POWERS:
            for unit, dests in sorted(game.get_power(p).retreats.items()):
                lines.append(f"- {p}: {unit} -> {', '.join(sorted(dests)) if dests else '(none)'}")
    return "\n".join(lines)


def _loc(order):
    # 'A BUD - SER' -> 'BUD'; 'F STP/SC - FIN' -> 'STP'
    parts = order.split()
    return parts[1].split("/")[0] if len(parts) > 1 else order


def results_block(phase_data):
    """'Adjudicated phase: X' + per-power orders + result flags, from a python-diplomacy GamePhaseData."""
    lines = [f"Adjudicated phase: {phase_data.name}"]
    for p in POWERS:
        orders = phase_data.orders.get(p) or []
        if orders:
            lines.append(f"- {p} orders: {'; '.join(sorted(orders, key=_loc))}")
    flagged = {u: ", ".join(str(r) for r in res) for u, res in phase_data.results.items() if res}
    if flagged:
        lines.append("- Results: " + "; ".join(f"{u}: {flagged[u]}" for u in sorted(flagged)))
    else:
        lines.append("- Results: all listed orders resolved without a result flag")
    return "\n".join(lines)


def previous_results(game):
    history = game.get_phase_history()
    if not history:
        return "PREVIOUS RESULTS\nNo earlier phase has been adjudicated."
    return "PREVIOUS RESULTS\n" + results_block(history[-1])


def available_orders(game, power, legal, required):
    lines = [f"AVAILABLE ORDERS FOR {power}", f"Orders required: {required}"]
    if game.phase_type == "A":
        lines.append(f"Centers minus units: {len(game.get_centers(power)) - len(game.get_units(power))}")
    if not legal:
        lines.append("(no orderable locations)")
    for loc in sorted(legal):
        lines.append(f"{loc}:")
        lines.extend(f"- {o}" for o in legal[loc])
    if build_count(game, power) > 0:
        lines.append("WAIVE may be submitted more than once; each occurrence consumes one available build.")
    return "\n".join(lines)


def legal_orders_by_loc(game, power):
    """{location: sorted legal orders}, as listed under AVAILABLE ORDERS."""
    possible = game.get_all_possible_orders()
    out = {}
    for loc in game.get_orderable_locations(power):
        orders = sorted(set(possible.get(loc, [])), key=lambda o: (o == "WAIVE", o))
        if orders:
            out[loc.split("/")[0]] = orders
    return out


def build_count(game, power):
    """Centers minus units in an adjustment phase (negative = disbands owed), else 0."""
    if game.phase_type != "A":
        return 0
    return len(game.get_centers(power)) - len(game.get_units(power))


def required_orders(game, power, legal):
    # Adjustments: |centers - units| even when fewer home centers are free (the rest must be WAIVE)
    if game.phase_type == "A":
        return abs(build_count(game, power))
    return len(legal)


def format_press(messages, power):
    """messages: list of dicts {round, sender, recipients, body}, already filtered for visibility."""
    if not messages:
        return "NEW PRESS SINCE YOUR PREVIOUS TURN\n(none)"
    order = {p: i for i, p in enumerate(POWERS)}
    msgs = sorted(messages, key=lambda m: (m["round"], order[m["sender"]], m["seq"]))
    lines = ["NEW PRESS SINCE YOUR PREVIOUS TURN"]
    lines += [f"[round {m['round']}] {m['sender']} -> {','.join(m['recipients'])}: {m['body']}" for m in msgs]
    return "\n".join(lines)


NEGOTIATION_DECISION = (
    "YOUR DECISION\nChoose what {power} says in this round. You may send at most 14 messages, each at most "
    "1200 characters. Address public press to GLOBAL. Address private press to one or more other surviving "
    "powers; do not combine GLOBAL with named powers. Messages from this round are delivered together after "
    "every player has finished the round. Do not submit orders yet."
)


# ---------------------------------------------------------------- prompts

def season_opening_prompt(game, power, memory, legal, required, press):
    """Round 1 of a movement season: full state + memory + available orders."""
    return "\n\n".join([
        f"CURRENT TURN\n{phase_title(game)}\nNegotiation round 1 of {NEGOTIATION_ROUNDS}.",
        "CURRENT POSITION\n" + position_body(game),
        previous_results(game),
        "PRIVATE MEMORY FROM EARLIER SEASONS\n" + (memory or "(none yet)"),
        available_orders(game, power, legal, required),
        format_press(press, power),
        NEGOTIATION_DECISION.format(power=power),
    ])


def negotiation_round_prompt(game, power, rnd, press):
    return "\n\n".join([
        f"CURRENT TURN\n{phase_title(game)}\nNegotiation round {rnd} of {NEGOTIATION_ROUNDS}.",
        format_press(press, power),
        NEGOTIATION_DECISION.format(power=power),
    ])


def orders_prompt(game, power, required, press):
    return "\n\n".join([
        f"CURRENT TURN\n{phase_title(game)}\nAll {NEGOTIATION_ROUNDS} negotiation rounds are complete.",
        format_press(press, power),
        f"YOUR DECISION\nSubmit exactly {required} orders using the exact wording from the AVAILABLE ORDERS "
        "already shown for this phase. Vote for an immediate agreed stalemate only if you want the game to end "
        "now. The game ends only if every surviving power votes for it while submitting orders.",
    ])


def retreat_or_adjustment_prompt(game, power, legal, required, memory=None):
    """Retreats (inside the season conversation) and winter adjustments (fresh context, with memory)."""
    parts = [
        f"CURRENT TURN\n{phase_title(game)}\nThis retreat/adjustment phase has no negotiation rounds.",
        "CURRENT POSITION\n" + position_body(game),
        previous_results(game),
        available_orders(game, power, legal, required),
    ]
    if memory is not None:
        parts.append("PRIVATE MEMORY FROM EARLIER SEASONS\n" + (memory or "(none yet)"))
    parts += [
        format_press([], power),
        f"YOUR DECISION\nSubmit exactly {required} orders using the exact wording under AVAILABLE ORDERS. "
        "There is no stalemate vote in this phase.",
    ]
    return "\n\n".join(parts)


def memory_prompt(game, power, season_phase, adjudicated):
    """After the season (movement + retreats) is resolved. `adjudicated`: GamePhaseData list for the season."""
    return "\n\n".join([
        f"RESOLVED MOVEMENT SEASON\n{season_phase}",
        "FINAL POSITION AFTER ALL RETREATS\n" + position_body(game),
        "ACTUAL ORDERS AND RESULTS\n" + "\n\n".join(results_block(pd) for pd in adjudicated),
        f"YOUR DECISION\nRewrite {power}'s complete private memory for future seasons using the memory and "
        "negotiations already in this conversation. Compare the actual orders and final position with the "
        "negotiations and commitments from this season. Record strategically important cooperation, broken "
        "promises or stabs, trust changes, threats, and future plans. Keep durable facts from earlier seasons "
        "when they still matter. Do not include chain-of-thought or a play-by-play; return concise "
        "decision-relevant memory only.",
    ])


def order_repair_prompt(problems, required, legal, builds=0):
    lines = ["Your previous order response could not be accepted.", "", "PROBLEMS"]
    lines += [f"- {p}" for p in problems]
    lines += ["", "REPAIR",
              f"Submit the complete response again with exactly {required} legal orders. Copy each order exactly "
              "from AVAILABLE ORDERS below. Do not add location labels, combine two orders into one string, or "
              "include commentary. Preserve your intended strategy where possible by replacing only invalid or "
              "missing orders.", "", "AVAILABLE ORDERS"]
    for loc in sorted(legal):
        lines.append(f"{loc}:")
        lines.extend(f"- {o}" for o in legal[loc])
    if builds > 0:
        lines.append("WAIVE may be used more than once, with each occurrence consuming one build.")
    return "\n".join(lines)


# ---------------------------------------------------------------- order checking

def check_orders(orders, legal, required, phase_type, builds=0):
    """Return (accepted_orders, problems). Illegal orders are dropped; missing ones are filled
    with the default (hold / disband / WAIVE), and each fix is reported as a problem.
    Warning wording follows the orderValidationWarnings seen in diplobench.com traces."""
    by_order = {o: loc for loc, os_ in legal.items() for o in os_}
    accepted, problems, used_locs, waives = [], [], set(), 0
    for raw in orders:
        o = " ".join(raw.strip().split())
        if phase_type == "A" and len(accepted) + waives >= required:
            continue  # extra adjustment orders are ignored silently, as in the reference harness
        if o == "WAIVE" and builds > 0:
            waives += 1
            continue
        loc = by_order.get(o)
        if loc is None:
            problems.append(f"rejected illegal order: '{raw}'")
        elif loc in used_locs:
            problems.append(f"rejected duplicate order for one location: '{raw}'")
        else:
            used_locs.add(loc)
            accepted.append(o)
    if phase_type in ("M", "R"):
        for loc in sorted(legal):
            if loc not in used_locs:
                default = next((o for o in legal[loc] if o.endswith(" H")), None) if phase_type == "M" \
                    else next((o for o in legal[loc] if o.endswith(" D")), None)
                if default:
                    accepted.append(default)
                    problems.append(f"filled missing order for {loc} with {'hold' if phase_type == 'M' else 'disband'}")
    else:
        while builds > 0 and len(accepted) + waives < required:
            waives += 1
            problems.append("filled missing build with WAIVE")
        for loc in sorted(legal):
            if builds > 0 or len(accepted) >= required:
                break
            if loc not in used_locs:
                # Assumption: their choice of unit to disband is unknown; take the first location alphabetically
                used_locs.add(loc)
                accepted.append(legal[loc][0])
                problems.append(f"filled missing adjustment for {loc} with disband")
    return accepted, problems
