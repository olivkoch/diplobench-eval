"""Game runner replicating the diplobench.com protocol.

Per movement season, each power has ONE conversation:
  system prompt -> round 1 (state + memory + available orders) -> rounds 2, 3 (new press only)
  -> orders (+ draw vote) -> retreats (if dislodged) -> memory rewrite.
Winter adjustments use a fresh conversation: system prompt + one message containing the memory.
All players act simultaneously within each step (threads); press is delivered after the round closes.
"""
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from diplomacy import Game
from diplomacy.utils.export import to_saved_game_format

from . import client
from . import protocol as P

logger = logging.getLogger(__name__)
ATTEMPT_LIMIT = 6  # per request, as in the reference traces (attemptLimit: 6)
SOLO_CENTERS = 18


class Runner:
    def __init__(self, models, out_dir, stop_after=None):
        """models: {POWER: model_name}. stop_after: phase id (e.g. 'S1901M') after which to stop, for tests."""
        self.models = models
        self.out_dir = out_dir
        self.stop_after = stop_after
        self.game = Game()
        self.memory = {p: None for p in P.POWERS}
        self.convs = {}
        self.result = None
        self._events_lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)
        self._events = open(os.path.join(out_dir, "events.jsonl"), "a")
        self._log_event({"type": "game_start", "models": models})

    # ------------------------------------------------------------ plumbing

    def _log_event(self, ev):
        ev = {"t": round(time.time(), 1), **ev}
        with self._events_lock:
            self._events.write(json.dumps(ev) + "\n")
            self._events.flush()

    def _save(self):
        with open(os.path.join(self.out_dir, "game.json"), "w") as f:
            json.dump(to_saved_game_format(self.game), f)
        with open(os.path.join(self.out_dir, "memory.json"), "w") as f:
            json.dump(self.memory, f, indent=1)

    def surviving(self):
        return [p for p in P.POWERS if not self.game.get_power(p).is_eliminated()]

    def _parallel(self, fn, powers):
        with ThreadPoolExecutor(max_workers=len(powers) or 1) as ex:
            return dict(zip(powers, ex.map(fn, powers)))

    def ask(self, power, stage, prompt, rnd=None):
        """Append `prompt` to the power's conversation, get a schema-valid reply (up to ATTEMPT_LIMIT
        attempts; failed attempts are re-prompted but not kept in the conversation). Returns parsed data or None."""
        conv = self.convs[power]
        conv.append({"role": "user", "content": prompt})
        schema_stage = stage if stage in ("negotiation", "memory") else "orders"  # retreat/adjustment/repair too
        model, phase = self.models[power], self.game.get_current_phase()
        attempts, scratch, data = [], [], None
        for attempt in range(1, ATTEMPT_LIMIT + 1):
            rec = {"attempt": attempt}
            try:
                resp = client.chat(model, conv + scratch, schema_stage, P.SCHEMAS[schema_stage]())
                rec.update(raw=resp["text"], thinking=resp["thinking"], usage=resp["usage"],
                           finish_reason=resp["finish_reason"], duration_s=resp["duration_s"],
                           http429_retries=resp["http429_retries"])
                if not resp["text"].strip():
                    raise client.BackendError("chat completion message content was empty")
                data = P.parse_response(schema_stage, resp["text"], power, self.surviving())
                rec["ok"] = True
                attempts.append(rec)
                conv.append({"role": "assistant", "content": resp["text"]})
                break
            except P.ProtocolError as e:
                rec.update(ok=False, failure_kind="response_validation", error=f"ProtocolError: {e}")
                scratch = [{"role": "assistant", "content": resp["text"]},
                           {"role": "user", "content": P.schema_retry_prompt(e)}]
            except client.BackendError as e:
                rec.update(ok=False, failure_kind="backend", error=f"BackendError: {e}")
                scratch = []
            attempts.append(rec)
            logger.warning(f"{phase} {power} {stage}: attempt {attempt} failed: {rec['error'][:200]}")
        if data is None:
            # Keep user/assistant alternation; the player simply says/does nothing this step.
            conv.append({"role": "assistant", "content": json.dumps(
                {"negotiation": {"messages": []}, "memory": {"private_summary": self.memory[power] or ""}}
                .get(schema_stage, {"orders": [], "draw_vote": False}))})
            logger.error(f"AGENT_FAILURE {phase} {power} {stage} after {ATTEMPT_LIMIT} attempts")
        self._log_event({"type": "request", "phase": phase, "power": power, "model": model, "stage": stage,
                         "round": rnd, "prompt": prompt, "attempts": attempts, "accepted": data})
        return data

    # ------------------------------------------------------------ orders

    def submit_orders(self, power, stage, prompt, legal, required):
        """Ask for orders, validate, run one repair round if needed. Returns (orders, draw_vote)."""
        builds = P.build_count(self.game, power)
        data = self.ask(power, stage, prompt)
        orders, draw = (data["orders"], data["draw_vote"]) if data else ([], False)
        accepted, problems = P.check_orders(orders, legal, required, self.game.phase_type, builds)
        repair = None
        if problems and data is not None:
            repair = {"problems": problems}
            data2 = self.ask(power, stage + "_repair", P.order_repair_prompt(problems, required, legal, builds))
            if data2:
                accepted, problems2 = P.check_orders(data2["orders"], legal, required, self.game.phase_type, builds)
                draw = data2["draw_vote"]
                repair["remaining_problems"] = problems2  # if any remain, the filled/defaulted orders stand
        if stage != "orders":
            draw = False  # no stalemate vote outside movement phases
        self._log_event({"type": "orders", "phase": self.game.get_current_phase(), "power": power,
                         "orders": accepted, "draw_vote": draw, "repair": repair})
        return [o for o in accepted if o != "WAIVE"], draw

    # ------------------------------------------------------------ phases

    def movement_season(self):
        game = self.game
        season = game.get_current_phase()
        powers = self.surviving()
        for p in powers:
            self.convs[p] = [{"role": "system", "content": P.system_prompt(p)}]
        legal = {p: P.legal_orders_by_loc(game, p) for p in powers}
        required = {p: P.required_orders(game, p, legal[p]) for p in powers}
        press = []  # dicts: round, sender, recipients, body, seq

        def visible(p, rnd):
            return [m for m in press if m["round"] == rnd and
                    (m["sender"] == p or p in m["recipients"] or "GLOBAL" in m["recipients"])]

        for rnd in range(1, P.NEGOTIATION_ROUNDS + 1):
            def negotiate(p):
                if rnd == 1:
                    prompt = P.season_opening_prompt(game, p, self.memory[p], legal[p], required[p], [])
                else:
                    prompt = P.negotiation_round_prompt(game, p, rnd, visible(p, rnd - 1))
                return self.ask(p, "negotiation", prompt, rnd)
            replies = self._parallel(negotiate, powers)
            for p in powers:  # delivered together once every player has finished the round
                for m in (replies[p] or {}).get("messages", []):
                    press.append({"round": rnd, "sender": p, "recipients": m["recipients"],
                                  "body": m["body"], "seq": len(press)})
            logger.info(f"{season} round {rnd}: " + ", ".join(
                f"{p[:3]}={len((replies[p] or {}).get('messages', []))}" for p in powers) + " messages")

        def orders(p):
            return self.submit_orders(p, "orders", P.orders_prompt(game, p, required[p], visible(p, P.NEGOTIATION_ROUNDS)),
                                      legal[p], required[p])
        results = self._parallel(orders, powers)
        for p in powers:
            game.set_orders(p, results[p][0])
        self._log_event({"type": "press", "phase": season, "messages": press})
        draw_votes = {p: results[p][1] for p in powers}
        if all(draw_votes.values()):
            self.result = {"reason": "stalemate_vote", "phase": season}
            return
        game.process()
        logger.info(f"{season} adjudicated: " + "; ".join(
            f"{u}: {', '.join(map(str, r))}" for u, r in sorted(game.get_phase_history()[-1].results.items()) if r))

        if game.phase_type == "R":
            retreating = [p for p in self.surviving() if game.get_orderable_locations(p)]

            def retreat(p):
                lg = P.legal_orders_by_loc(game, p)
                req = P.required_orders(game, p, lg)
                return self.submit_orders(p, "retreat", P.retreat_or_adjustment_prompt(game, p, lg, req), lg, req)
            rr = self._parallel(retreat, retreating)
            for p in retreating:
                game.set_orders(p, rr[p][0])
            game.process()

        season_history = [h for h in game.get_phase_history() if h.name[:5] == season[:5] and h.name[-1] in "MR"]

        def remember(p):
            data = self.ask(p, "memory", P.memory_prompt(game, p, season, season_history))
            if data:
                self.memory[p] = data["private_summary"]
        self._parallel(remember, [p for p in powers if p in self.surviving()])

    def adjustments(self):
        game = self.game
        powers = [p for p in self.surviving() if P.build_count(game, p) != 0]

        def adjust(p):
            self.convs[p] = [{"role": "system", "content": P.system_prompt(p)}]
            lg = P.legal_orders_by_loc(game, p)
            req = P.required_orders(game, p, lg)
            prompt = P.retreat_or_adjustment_prompt(game, p, lg, req, memory=self.memory[p] or "")
            return self.submit_orders(p, "adjustment", prompt, lg, req)
        res = self._parallel(adjust, powers)
        for p in powers:
            game.set_orders(p, res[p][0])
        game.process()

    # ------------------------------------------------------------ main loop

    def run(self):
        game = self.game
        while self.result is None:
            phase = game.get_current_phase()
            year = int(phase[1:5])
            if year > P.LAST_YEAR:
                self.result = {"reason": "cutoff", "phase": phase}
                break
            t0 = time.time()
            if game.phase_type == "M":
                self.movement_season()
            elif game.phase_type == "A":
                self.adjustments()
            else:
                raise RuntimeError(f"unexpected phase {phase}")
            self._save()
            centers = {p: len(game.get_centers(p)) for p in P.POWERS}
            logger.info(f"DONE {phase} in {time.time() - t0:.0f}s -> now {game.get_current_phase()} | SCs "
                        + " ".join(f"{p[:3]}={c}" for p, c in centers.items()))
            logger.info(f"LLM_STATS {client.stats_summary()}")
            solo = [p for p, c in centers.items() if c >= SOLO_CENTERS]
            if solo:
                self.result = {"reason": "solo", "winner": solo[0], "phase": game.get_current_phase()}
            elif self.stop_after and phase == self.stop_after:
                self.result = {"reason": f"stopped after {phase} (test run)", "phase": game.get_current_phase()}
        self.result["centers"] = {p: len(game.get_centers(p)) for p in P.POWERS}
        self._log_event({"type": "game_end", **self.result})
        self._save()
        logger.info(f"GAME_END {json.dumps(self.result)}")
        return self.result
