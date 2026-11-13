# diplobench-eval

A reimplementation of the [DiploBench](https://diplobench.com) game protocol (tournament
`openrouter-38-v2`), used to evaluate models such as `mistral-large-4` at Diplomacy under the same
conditions as the DiploBench league.

## Protocol

Adjudication is done by [python-diplomacy](https://github.com/diplomacy/diplomacy).

- **Movement seasons**: each power has one conversation per season, made of:
  1. the system prompt;
  2. round 1, with the game state, the power's private memory and its available orders;
  3. rounds 2 and 3, with only the new press;
  4. the orders and a draw vote;
  5. retreats, if any of its units were dislodged;
  6. a rewrite of its private memory (at most 4000 characters).
- **Press**: 3 rounds per season, played simultaneously. Each power sends at most 14 messages of at most 1200
  characters per round, addressed either to `GLOBAL` or to named powers (not both).
- **Winter adjustments**: a fresh conversation containing the system prompt and one message, which includes
  the memory.
- **Replies**: every reply must match a strict JSON schema. If it doesn't, the model is asked again, up to 6
  attempts. Illegal orders get one repair round; orders still missing after that are filled with defaults
  (hold, disband or `WAIVE`).
- **End**: a solo at 18 supply centers, a unanimous draw vote, or the cutoff after winter 1920.

`dbcom/system_prompt.txt` is the system prompt published in the DiploBench traces, with the power name
replaced by `{POWER}`. All prompts are reproduced verbatim from those traces (see Verification).

### Assumptions (not visible in the published data)

- No `temperature` is sent.
- `max_tokens` is 16000 by default; `DBCOM_MAX_TOKENS=none` omits it. The league did not cap replies at
  16000: its traces include replies of 55087 completion tokens that finished normally. Campaign c1 used 16000.
  - ML4 reached the cap on 7.4% of its c1 attempts (9.6% of negotiation requests), each time with an empty
    reply after 16000 tokens of reasoning; the retry always succeeded.
  - GLM-5.2@high reached it on 7.8% of attempts, and 8 of its requests failed all 6 attempts this way.
  - Replaying 3 such ML4 requests with no cap, all finished normally, one with 18466 tokens.
- Replies that fail the schema are re-prompted, then dropped from the conversation history.
- When a disband is missing, the first unit in alphabetical order of location is disbanded.
- A unanimous draw vote ends the game without adjudicating that turn.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the keys
```

Model names are routed by prefix:

- `azure_ai/...`, `vertex_ai/...`, `mistral/...` go to the Mistral internal LiteLLM gateway
  (`LITELLM_API_KEY`; list models with `GET https://internal-ai-gateway.mistralai.com/v1/models`).
- Mistral-family names (`mistral-*`, `ministral-*`, `magistral-*`, ...) go to the Mistral API (`MISTRAL_API_KEY`).
- Other names with a vendor prefix (`openai/...`, `anthropic/...`) go to OpenRouter (`OPENROUTER_API_KEY`).

Appending `@<effort>` to a name sends `reasoning_effort=<effort>`, e.g. `mistral/zai-glm-5-2@high`.
Reasoning text, whether it arrives as `reasoning_content` or as thinking blocks, is saved as `thinking` in
`events.jsonl`.

### League opponents on the gateway

These league models are also served by the gateway. The effort settings were chosen by replaying league
round-1 prompts and matching output length. Without them, GLM-5.2 and mistral-medium-3.5 don't reason on the
gateway, although they did in the league. GPT and Claude need no setting: like in the league, they reason
little or not at all by default.

| League (rank) | Gateway spec |
|---|---|
| gpt-5.6-sol (1) | `azure_ai/gpt-5.6-sol` |
| glm-5.2 (3) | `mistral/zai-glm-5-2@high` |
| claude-opus-5 (5) | `vertex_ai/claude-opus-5` |
| gpt-5.6-terra (8) | `azure_ai/gpt-5.6-terra` |
| claude-sonnet-5 (15) | `vertex_ai/claude-sonnet-5` |
| gpt-5.6-luna (22) | `azure_ai/gpt-5.6-luna` |
| mistral-medium-3.5 (24) | `mistral/mistral-medium-3-5@medium` |

Gateway quirks:

- **Caching**: the gateway caches `mistral-large-4` responses, so a retry after an empty reply (all 16000
  tokens spent on reasoning) got the same empty reply back. Gateway requests are therefore sent with
  `"cache": {"no-cache": true}`.
- **Stringified arrays**: for Claude, LiteLLM turns `response_format` into a forced tool call. An array field
  then sometimes comes back as a string holding the array plus junk (`"[...]}\n"`). The parser decodes it, and
  the decoded JSON is what stays in the conversation. The league used native structured output, which never
  does this.
- **Memory length**: neither route enforces `maxLength` for Claude. Neither does Anthropic's native structured
  output through `/v1/messages`, nor a limit stated in the schema description; both were tested on failed
  memory requests. GPT and GLM stop at exactly 4000 characters.
  - Claude's first memory attempt exceeds 4000 characters far more often than in the league. Its seasons here
    carry about 1.6× more press (median 36k vs 22k characters of prompts, 1902–1903), because every opponent
    is a strong, talkative model.
  - The schema retry fixes most cases. In the aborted first campaign, 8 of 96 Claude memory requests failed
    all 6 attempts, and the player kept its previous memory.

- **GLM at winter adjustments**: GLM-5.2@high sometimes degenerates after its build orders, repeating
  `"WAIVE"` or whitespace until it reaches 16000 tokens. When all 6 attempts do this, it loses its builds.
  Replaying the 4 such requests from c1 failed on about half of the attempts, under our old schema and the
  league's alike.

- **Azure content filter**: Azure sometimes rejects a GPT prompt under its content policy (HTTP 400), and the
  same rejection repeats on every attempt. In c1 game-04, at F1920M round 3, this happened to gpt-5.6-sol and
  gpt-5.6-terra, which therefore sent no press that round.

In the league, schema retries are routine for other models as well (about 11% of attempts for
deepseek-v4-pro and mistral-medium-3.5).

The gateway reports the cost of each call (`x-litellm-response-cost`), which is summed into `spend_usd`.
`mistral-large-4` is reported at $0.

### Transport errors

HTTP 429/502/503/504/529, timeouts and connection errors are retried with backoff for up to an hour, so a
gateway outage pauses the game rather than costing a player its turn. Other 5xx errors and malformed
responses are retried 10 times. Other 4xx errors fail at once. Only after these retries does a call count as
a failed attempt against the 6 schema attempts.

## Usage

```bash
# Short test: one season, ML4 as Austria, ministral for everyone else
python3 -m dbcom.run --out runs/t1 --default-model ministral-8b-latest \
    --model AUSTRIA=mistral-large-4 --stop-after S1901M

# Full game
python3 -m dbcom.run --out runs/game1 --default-model ministral-8b-latest --model AUSTRIA=mistral-large-4

# ML4 against league opponents, all through the gateway
python3 -m dbcom.run --out runs/g1 \
    --model AUSTRIA=mistral/mistral-large-4 --model ENGLAND=azure_ai/gpt-5.6-sol \
    --model FRANCE=vertex_ai/claude-opus-5 --model GERMANY=mistral/zai-glm-5-2@high \
    --model ITALY=azure_ai/gpt-5.6-terra --model RUSSIA=vertex_ai/claude-sonnet-5 \
    --model TURKEY=mistral/mistral-medium-3-5@medium --stop-after S1901M
```

Each run directory contains:

- `run.log`: progress, supply-center counts and call stats.
- `events.jsonl`: every request with its prompt, raw reply, thinking, usage and retries; accepted orders and
  repairs; all press.
- `game.json`: the game in python-diplomacy saved-game format.
- `memory.json`: each power's latest private memory.
- `progress.json`: phase, supply centers, units, `spend_usd` and `agent_failures` (requests that got no valid
  reply after 6 attempts). It is rewritten after every phase.
- `result.json`: the final result, written when the game ends.

### Resume

`python3 -m dbcom.run --out runs/game1 --model ... --resume` continues a game from `game.json` and
`memory.json`. A phase that was interrupted is replayed from its start. `events.jsonl` therefore keeps the
events of the interrupted attempt before the `{"type": "resume"}` event. When analysing, drop the events of
that phase that come before the `resume` event.

### Campaigns

```bash
nohup caffeinate -is python3 -m dbcom.campaign --name c1 --games 14 --parallel 7 --budget-usd 1000 \
    --models mistral/mistral-large-4,azure_ai/gpt-5.6-sol,mistral/zai-glm-5-2@high,vertex_ai/claude-opus-5,azure_ai/gpt-5.6-terra,vertex_ai/claude-sonnet-5,mistral/mistral-medium-3-5@medium \
    > runs/c1.out 2>&1 &
```

The campaign runs each game in its own process.
- **Lineups**: assigned by Latin squares. In each block of 7 games, every model plays every power once, and
  the neighbours change from one block to the next.
- **Crashes**: a game that crashes is resumed automatically.
- **Budget**: no new game starts once spend reaches `--budget-usd`, and running games are stopped at 1.25×.
- **Monitoring**: `runs/c1/status.txt` is refreshed every 30 s. The log is `runs/c1/campaign.log`.
- **Restarting**: the supervisor can be killed and restarted with the same command. It adopts the games that
  are still running and resumes the others.

`caffeinate` keeps the Mac awake, provided it stays on AC power with the lid open.

## Verification

```bash
python3 dbcom/tests/fetch_reference.py   # downloads DiploBench game c01-g01, S1901M..S1902M -> reference/
python3 dbcom/tests/verify_prompts.py    # replays it and diffs our prompts against theirs
python3 dbcom/tests/dry_run.py           # full game with a fake model, no API calls
python3 dbcom/tests/resume_test.py       # reload == live game after every phase; kill + --resume
```

Campaign tests use `dbcom/tests/fake_run.py` (fake model, optional `FAKE_CRASH_AT=<phase>`), e.g.
`DBCOM_RUN_MODULE=dbcom.tests.fake_run DBCOM_POLL_S=1 python3 -m dbcom.campaign --name test --models fake,fake,fake,fake,fake,fake,fake --budget-usd 1`.

`verify_prompts.py` currently reports 226/226 identical: 113 prompts, covering negotiation, orders, retreats,
adjustments and memory, plus the JSON schema sent with each of them. A separate check on game c01-g25 matched
all 54 order-repair prompts and all 402 order validations.

Schemas before 2026-11-10 20:50 differed slightly from the league's:
- the negotiation schema also carried the 14-message, 1200-character and non-empty-recipient limits (now only
  checked by the parser);
- the descriptions of `draw_vote` and `private_summary` were missing.

Campaign c1 games 00–06 were played with those schemas, and games 07 onwards with the league's. In games 00–06,
up to 1906–1909, the extra limits almost never bound: about 0.1% of message bodies reached 1200 characters, and
1 negotiation round in about 2000 had 14 messages.
