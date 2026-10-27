# researching-arxiv-papers

An Agent Skill for finding, reading, and citing arXiv preprints, plus a controlled
experiment measuring whether the skill actually helps.

## Install the skill

- **Claude Code (personal):** `cp -r skills/researching-arxiv-papers ~/.claude/skills/`
- **Claude Code (one project):** copy the folder into that project's `.claude/skills/`
- **claude.ai:** upload it via the Skills section of your settings
- **Other agents** that support the open Agent Skills format: point them at `skills/researching-arxiv-papers/`

## Results

See `results/`. Each experiment is a frozen folder with raw answers, transcripts, request logs,
the arXiv metadata snapshot used for scoring, and `summary.txt`.

## Reproduce

See [overview.md](overview.md) for the full walkthrough, including how the statistics work
and how to compare a modified skill. Short version, from the repo root:

1. `pip install -r eval/requirements.txt`, then run `mitmdump` once to generate its CA certificate.
2. Check `eval/arm-settings.json` (model, allowed tools), commit, and tag: `git tag prereg-v1 && git tag skill-v1`.
3. `python eval/make_arms.py`  (builds the arms in `~/arxiv-eval-arms`, outside this repo)
4. Terminal 1: `mitmdump -q -s eval/log_arxiv.py`
5. Terminal 2: `python eval/run.py`, then `python eval/run.py --rerun-errors` until no errors remain.
6. Stop the proxy, then score (pass 1):
   `ARXIV_EVAL_CONTACT=you@example.org python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl --manifest data/manifest.jsonl --requests data/requests.jsonl --compare skill:baseline --compare skill:placebo`
7. Have someone blind to the arms answer `data/results/flags_blind.jsonl` in `data/results/verdicts.csv`
   (start from `verdicts_template.csv`), then rerun step 6 with `--verdicts data/results/verdicts.csv`.
8. Copy `data/` and `~/arxiv-eval-arms/arms.json` into `results/<date>-<model>/` and commit.

## Layout

- `skills/`: the skill itself (only `SKILL.md`; no human docs inside the skill folder)
- `eval/`: preregistration, tasks, placebo skill, and harness scripts
- `data/`: gitignored scratch space for the run in progress
- `results/`: frozen, committed experiments
- `talk/`: slides and notes
