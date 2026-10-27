# Preregistration

Commit this file and `eval/tasks/heldout.jsonl`, then tag the commit (`git tag prereg-v1`)
BEFORE running any held-out task. Fill in every TODO.

## Hypotheses

- H1: On held-out tasks, the skill arm has a higher `no_fabrication` rate than the baseline arm.
- H2: On held-out runs with 2+ API requests, the skill arm has a higher `rate_limit_ok` rate than the baseline arm.
- H3: The H1 and H2 effects also hold for skill vs. placebo (the content matters, not just extra context).

## Primary metrics

- `no_fabrication`
- `rate_limit_ok` (scored only on runs with 2+ requests to export.arxiv.org)

All other metrics are secondary and reported as exploratory.

## Design

- Arms: baseline (no skill), skill, placebo (same name and description, generic body)
- Model: TODO (pinned in `eval/arm-settings.json`)
- Runs per task per arm: TODO (default 5)
- Order: randomized; seed 42 in `eval/run.py`
- Skill version under test: tag `skill-v1` (SHA-256 recorded in `arms.json`)
- Held-out tasks: TODO count, written without testing the skill on them

## Analysis

- Unit of analysis: the task. Average runs within each task and arm, then pair tasks across arms.
- Test: two-sided sign test on per-task differences; alpha = 0.05.
- Multiplicity: Holm correction across the 4 primary tests (2 metrics x 2 comparisons).
- Effect size: mean per-task difference with a 95% bootstrap CI (10,000 resamples, resampling tasks).
- Sensitivity analysis: mixed-effects logistic regression, `outcome ~ condition + (1 | task_id)`.

## Exclusions

- Runs lost to infrastructure failures (proxy down, confirmed arXiv outage) are rerun before scoring. TODO: describe how.
- No other exclusions. Timeouts count as failures on every applicable metric.

## Manual review

Heuristic flags (review-status claims, decoy handling, fabricated-ID matches) are reviewed
from `flags_blind.jsonl` by someone who does not know which arm produced each item.
Their verdicts are recorded before unblinding with `flags_key.csv`.

## No peeking

Held-out results are not inspected until all runs in all arms are complete.
