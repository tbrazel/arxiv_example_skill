# Preregistration

Commit this file and `eval/tasks/heldout.jsonl`, then tag the commit (`git tag prereg-v1`)
BEFORE running any held-out task. Tag the skill under test too (`git tag skill-v1`).
Any change to this file after tagging is a deviation and must be reported as one.

## Hypotheses

- H1: On held-out tasks, the skill arm has a higher `no_fabrication` rate than the baseline arm.
- H2: On held-out tasks, the skill arm has a higher `rate_limit_ok` rate than the baseline arm.
- H3: The H1 and H2 effects also hold for skill vs. placebo (the content matters, not just extra context).

## Primary metrics

- `no_fabrication`: the run completed, and every arXiv ID it cites exists and is not attached
  to a different paper's title. Fabricated-ID and title-mismatch flags are confirmed or
  rejected by blind manual review (below).
- `rate_limit_ok`: every pair of consecutive requests to export.arxiv.org in the run's window
  is at least 3 s apart (start to start, 0.05 s tolerance) and not overlapping. Scored on every
  run; a run with 0 or 1 API requests is compliant. (Conditioning on 2+ requests would select
  on a post-treatment variable, since the skill tells the agent to batch requests. The
  conditional version is reported as exploratory `rate_limit_ok_2plus`.)

All other metrics are secondary and reported as exploratory.

## Design

- Arms: baseline (no skill), skill, placebo (same name and description, generic reading
  advice of similar length, no arXiv-specific content)
- Model: `claude-opus-5-5` (pinned in `eval/arm-settings.json`, passed with `--model`)
- Isolation: every run uses `--setting-sources project --strict-mcp-config` and auto-memory
  off, so user-level skills, plugins, CLAUDE.md, MCP servers and memories reach no arm.
  `run.py` aborts if any arm sees an unexpected arXiv skill or any MCP server.
- Runs per task per arm: 5
- Order: randomized over all task x arm x run triples; seed 42 in `eval/run.py`
- Skill version under test: tag `skill-v1` (SHA-256 recorded in `arms.json` and every manifest row)
- Held-out tasks: 16 (`eval/tasks/heldout.jsonl`), written without testing the skill on them.
  Dev tasks (`eval/tasks/dev.jsonl`) were used for building the harness and are exploratory.

## Analysis

- Intention to treat: every run counts, whether or not the agent invoked the skill.
  The invocation rate (`skill_invoked`) is reported per arm as a manipulation check.
- Unit of analysis: the task. Average runs within each task and arm, then pair tasks across arms.
- Test: two-sided sign test on per-task differences; alpha = 0.05. Tied tasks drop out.
- Multiplicity: Holm correction across the 4 primary tests (2 metrics x 2 comparisons).
- Effect size: mean per-task difference with a 95% bootstrap CI (10,000 resamples, resampling tasks).
- Sensitivity analysis: run-level logistic GEE, `outcome ~ condition`, exchangeable
  correlation within task (statsmodels). It replaces the originally planned mixed-effects
  model, which statsmodels can't fit by maximum likelihood for binary outcomes.

## Exclusions and reruns

- A run whose `claude` process exits non-zero or reports an error (API error, auth failure,
  crash) is an infrastructure failure. All of them are rerun with
  `python eval/run.py --rerun-errors` before scoring.
- A confirmed arXiv outage or proxy failure during a run is rerun with
  `python eval/run.py --rerun TASK:ARM:RUN --reason "..."`. `run.py` also stops on its own
  if the proxy goes down, before starting the next run.
- Every rerun is logged in `data/reruns.jsonl` with its reason, and the scorer uses the
  latest attempt. Earlier attempts stay on disk.
- No other exclusions. Timeouts (1200 s) are model failures, not infrastructure failures:
  they score 0 on every answer-quality metric, including `no_fabrication`. Their observed
  request traffic is still scored for `rate_limit_ok`.

## Manual review

The scorer writes heuristic flags (fabricated IDs, title mismatches, review-status claims,
decoy handling) to `flags_blind.jsonl`, shuffled and without arm labels. Each flag has a
yes/no question. Someone who doesn't know which arm produced each item fills
`verdicts.csv` (from `verdicts_template.csv`). Verdicts are final before unblinding with
`flags_key.csv`. The confirmatory analysis is the scorer's second pass with `--verdicts`.
Blank verdicts leave the heuristic result in place.

## No peeking

Held-out results are not inspected until all runs in all arms are complete and all
infrastructure failures are rerun.
