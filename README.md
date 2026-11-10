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

- No `temperature` is sent; `max_tokens` is 16000.
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

On the gateway, Claude models don't always respect the strict schema (e.g. an array returned as a string).
The schema retry handles this.

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

## Verification

```bash
python3 dbcom/tests/fetch_reference.py   # downloads DiploBench game c01-g01, S1901M..S1902M -> reference/
python3 dbcom/tests/verify_prompts.py    # replays it and diffs our prompts against theirs
python3 dbcom/tests/dry_run.py           # full game with a fake model, no API calls
```

`verify_prompts.py` currently reports 113/113 identical prompts, covering negotiation, orders, retreats,
adjustments and memory. A separate check on game c01-g25 matched all 54 order-repair prompts and all 402
order validations.
