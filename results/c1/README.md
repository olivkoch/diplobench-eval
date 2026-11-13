# Campaign c1: mistral-large-4 against six DiploBench league models

21 full games of Diplomacy, played 2026-11-10 18:59 to 2026-11-11 10:57 with the harness in this repo.

## What we did

- **Protocol**: the DiploBench league protocol (tournament `openrouter-38-v2`), reimplemented in `dbcom/`.
  - Each movement season has 3 rounds of private and public press, then orders and a draw vote, then a
    rewrite of the player's private memory (at most 4000 characters).
  - Every reply must match a strict JSON schema, with up to 6 attempts.
  - A game ends with a solo win (18 supply centers), a unanimous draw vote, or the cutoff after winter 1920.
  - The prompts and JSON schemas are verified against the league's published traces: 226/226 identical
    (see the main README).
- **Players**: `mistral-large-4` (ML4), which is not in the league, against six league models. All seven
  were reached through the Mistral LiteLLM gateway:

  | Model | League rank | Gateway spec |
  |---|---|---|
  | gpt-5.6-sol | 1 | `azure_ai/gpt-5.6-sol` |
  | glm-5.2 | 3 | `mistral/zai-glm-5-2@high` |
  | claude-opus-5 | 5 | `vertex_ai/claude-opus-5` |
  | gpt-5.6-terra | 8 | `azure_ai/gpt-5.6-terra` |
  | claude-sonnet-5 | 15 | `vertex_ai/claude-sonnet-5` |
  | mistral-medium-3.5 | 24 | `mistral/mistral-medium-3-5@medium` |
  | mistral-large-4 | — | `mistral/mistral-large-4` |

  The `@effort` settings were chosen so that GLM and mistral-medium reason about as much as they did in the
  league.
- **Lineups**: the 21 games are 3 blocks of 7, each block a Latin square. Every model played every power
  3 times, with different neighbours in each block. The lineups are in `campaign.json`.
- **Running**: 7 games ran in parallel, each taking 3.8–6.3 hours (median 4.5). No game crashed or had to be
  resumed, and no call failed for transport reasons. Total spend was $1728 (the gateway reports ML4 at $0).

## Results

The main metric is the number of supply centers (SCs) a player owns when the game ends. "Mean rank" is the
player's rank within its game by SCs (1 = most SCs; ties share the mean rank).

| # | Model | Mean SCs (± s.e.) | Mean rank in game | Solos | Draws | Eliminations |
|---|---|---|---|---|---|---|
| 1 | gpt-5.6-sol | 8.48 ± 0.80 | 2.24 | 1 | 4 | 0 |
| 2 | claude-opus-5 | 7.52 ± 0.48 | 2.14 | 0 | 4 | 0 |
| 3 | gpt-5.6-terra | 5.29 ± 0.54 | 3.60 | 0 | 4 | 1 |
| 4 | zai-glm-5-2@high | 4.05 ± 0.55 | 4.64 | 0 | 4 | 3 |
| 5 | **mistral-large-4** | **3.90 ± 0.55** | 4.36 | 0 | 3 | 3 |
| 6 | claude-sonnet-5 | 3.29 ± 0.54 | 4.93 | 0 | 3 | 5 |
| 7 | mistral-medium-3-5@medium | 1.48 ± 0.37 | 6.10 | 0 | 4 | 9 |

- **How games ended**: 16 at the 1920 cutoff, 4 by an agreed draw, and 1 by a solo win (gpt-5.6-sol as Italy,
  game-14).
- **ML4**: 5th of 7. Below, each difference is ML4's mean minus the other model's.
  - Not statistically different from GLM-5.2 (−0.15 SCs), claude-sonnet-5 (+0.61) or gpt-5.6-terra (−1.39,
    about 1.8 standard errors).
  - Clearly behind gpt-5.6-sol (−4.58) and claude-opus-5 (−3.62).
  - Clearly ahead of mistral-medium-3.5 (+2.42).
  - By mean rank within the game, ML4 is ahead of GLM (4.36 vs 4.64), so the order of those two depends on
    the metric.
  - These standard errors treat games as independent and ignore that players in the same game compete for
    the same 34 centers, so they are approximate.
- **Comparison with the league**: the six league models finish in nearly their league order. The exception
  is GLM-5.2, which is 3rd in the league but 4th here; the token cap below hurt it the most. On this
  evidence, ML4 would sit among the league's ranks 3–15, near GLM-5.2 and claude-sonnet-5.

### ML4 game by game

| Game | ML4 power | ML4 SCs | Rank | Game end | Final SCs (A E F G I R T) |
|---|---|---|---|---|---|
| game-00 | Austria | 6 | 1.5 | cutoff (S1921M) | 6 3 5 5 5 6 4 |
| game-01 | England | 5 | 4 | cutoff (S1921M) | 0 5 6 3 8 8 4 |
| game-02 | France | 0 | 7 | stalemate_vote (F1920M) | 5 4 0 8 6 6 5 |
| game-03 | Germany | 6 | 4 | cutoff (S1921M) | 0 7 3 6 7 4 7 |
| game-04 | Italy | 4 | 4.5 | cutoff (S1921M) | 7 9 4 0 4 7 3 |
| game-05 | Russia | 6 | 2 | stalemate_vote (S1920M) | 4 6 6 3 4 6 5 |
| game-06 | Turkey | 2 | 6 | cutoff (S1921M) | 8 7 6 4 6 1 2 |
| game-07 | Austria | 5 | 4 | cutoff (S1921M) | 5 10 2 0 1 10 6 |
| game-08 | Russia | 3 | 4 | cutoff (S1921M) | 0 0 0 17 6 3 8 |
| game-09 | Germany | 2 | 5.5 | cutoff (S1921M) | 0 11 4 2 12 3 2 |
| game-10 | England | 9 | 2 | cutoff (S1921M) | 2 9 7 1 3 1 11 |
| game-11 | Turkey | 0 | 6.5 | cutoff (S1921M) | 6 0 5 5 9 9 0 |
| game-12 | Italy | 5 | 4 | cutoff (S1921M) | 0 5 7 5 5 12 0 |
| game-13 | France | 3 | 6 | cutoff (S1921M) | 5 9 3 6 0 6 5 |
| game-14 | Austria | 0 | 6 | solo (COMPLETED) | 0 10 1 0 18 5 0 |
| game-15 | Italy | 7 | 2 | cutoff (S1921M) | 0 11 6 0 7 5 5 |
| game-16 | England | 6 | 3 | cutoff (S1921M) | 4 6 3 5 1 7 8 |
| game-17 | Russia | 5 | 3.5 | cutoff (S1921M) | 4 5 6 6 4 5 4 |
| game-18 | France | 5 | 4.5 | stalemate_vote (F1920M) | 6 7 5 4 5 1 6 |
| game-19 | Turkey | 1 | 6 | stalemate_vote (F1920M) | 8 5 2 7 11 0 1 |
| game-20 | Germany | 2 | 5.5 | cutoff (S1921M) | 9 10 2 2 3 8 0 |

By power, ML4's mean SCs were:

| Austria | England | France | Germany | Italy | Russia | Turkey |
|---|---|---|---|---|---|---|
| 3.7 | 6.7 | 2.7 | 3.3 | 5.3 | 4.7 | 1.0 |

With only 3 games per power, these figures are noisy.

### Calls

| Model | Requests | Retried attempts | Agent failures | Cut off at 16000 tokens | Tokens out | Cost |
|---|---|---|---|---|---|---|
| gpt-5.6-sol | 4419 | 8 (0.2%) | 1 | 2 (0.0%) | 5.3M | $242.13 |
| claude-opus-5 | 4362 | 2627 (37.6%) | 217 | 0 (0.0%) | 14.5M | $921.97 |
| gpt-5.6-terra | 4216 | 9 (0.2%) | 1 | 0 (0.0%) | 3.1M | $96.87 |
| zai-glm-5-2@high | 4229 | 444 (9.5%) | 9 | 373 (8.0%) | 25.2M | $131.23 |
| mistral-large-4 | 3994 | 366 (8.4%) | 0 | 325 (7.5%) | 23.4M | $0.00 |
| claude-sonnet-5 | 4058 | 1248 (23.5%) | 46 | 0 (0.0%) | 5.6M | $187.96 |
| mistral-medium-3-5@medium | 3470 | 54 (1.5%) | 0 | 52 (1.5%) | 16.4M | $147.87 |

- "Retried attempts" are attempts beyond the first, as a share of all attempts.
- An "agent failure" is a request that got no valid reply in 6 attempts. The player then sends no press,
  submits default orders, or keeps its previous memory, depending on the stage.

## Caveats

- **Token cap**: replies were capped at 16000 tokens, but the league had no such cap (its traces include
  replies of 55087 tokens).
  - All 325 of ML4's cut-off attempts spent the full 16000 tokens reasoning and returned an empty reply. The
    retry always succeeded, but the cap systematically discarded ML4's longest deliberations.
  - All 9 of GLM-5.2's agent failures hit the cap on all 6 attempts.
    - 3 were negotiation rounds, all in game-14's last season (fall 1920), the season Italy won its solo.
      There GLM, playing England, reasoned past 16000 tokens and sent no press.
    - The other 6 were winter adjustments where GLM repeated `"WAIVE"` until the cap. Removing the cap might
      only make those loops longer.
  - A rerun with `DBCOM_MAX_TOKENS=none` would remove this bias.
- **Claude's memory**: Claude's memory rewrites often exceed 4000 characters, and the gateway does not
  enforce the schema's `maxLength` for Claude. All of claude-opus-5's 217 agent failures and claude-sonnet-5's
  46 are memory rewrites, which leave the player with its previous memory. This happens more as the game
  gets longer.
- **Schemas**: games 00–06 used our earlier JSON schemas, which carried the message limits inside the
  negotiation schema and lacked two field descriptions. Games 07–20 used the league's schemas verbatim. The
  extra limits almost never bound (see the main README).
- **Azure content filter**: in game-04 (fall 1920, round 3), the filter rejected the prompts of both GPT
  models, which sent no press that round.
- **Sample size**: 21 games per model can reliably separate differences of about 1.5 SCs. Smaller
  differences, such as ML4 vs GLM-5.2 or claude-sonnet-5, would need about 50 games or more.
- **Opponents**: the opponents are 6 of the league's 38 models, mostly strong ones. Results against the full
  league field may differ.

## Files

- `campaign.json`: the model list and the 21 lineups.
- `status.txt`: the campaign's final status table.
- `games/game-NN/result.json`: the final centers, how the game ended, its spend and agent failures.
- `games/game-NN/game.json`: the full game (orders and adjudication results per phase) in python-diplomacy's
  saved-game format.
- `per_game.csv`, `models.csv`, `calls.csv`: the tables above.

The full event logs (every prompt, reply and reasoning text, 642 MB) are not in the repo.

To regenerate the tables, run `python3 results/summarize.py results/c1`. Adding `--events runs/c1` rebuilds
`calls.csv`, but needs the full logs.
