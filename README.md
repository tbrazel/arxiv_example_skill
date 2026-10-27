# researching-arxiv-papers

An Agent Skill for finding, reading, and citing arXiv preprints, plus a controlled
experiment measuring whether the skill actually helps.

## Install the skill

- **Claude Code (personal):** `cp -r skills/researching-arxiv-papers ~/.claude/skills/`
- **Claude Code (one project):** copy the folder into that project's `.claude/skills/`
- **claude.ai:** upload it via the Skills section of your settings
- **Other agents** that support the open Agent Skills format: point them at `skills/researching-arxiv-papers/`

## Results

See `results/`. Each experiment is a frozen folder with raw answers, request logs,
the arXiv metadata snapshot used for scoring, and `summary.txt`.

## Reproduce

Run everything from the repo root.

1. `pip install -r eval/requirements.txt`, then run `mitmdump` once to generate its CA certificate.
2. Disable any copy of this skill in `~/.claude/skills/` and in claude.ai, so it can't leak into the baseline arm.
3. Check `eval/arm-settings.json` (model, permissions), then commit.
4. `python eval/make_arms.py`  (builds the arms in `~/arxiv-eval-arms`, outside this repo)
5. Terminal 1: `mitmdump -q -s eval/log_arxiv.py`
6. Terminal 2: `python eval/run.py`
7. Stop the proxy, then run:
   `python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl --manifest data/manifest.jsonl --requests data/requests.jsonl --compare skill:baseline --compare skill:placebo`
8. Have someone blind to the arms review `data/results/flags_blind.jsonl`, then unblind with `flags_key.csv`.
9. Copy `data/` and `~/arxiv-eval-arms/arms.json` into `results/<date>-<model>/` and commit.

## Layout

- `skills/`: the skill itself (only `SKILL.md`; no human docs inside the skill folder)
- `eval/`: preregistration, tasks, placebo skill, and harness scripts
- `data/`: gitignored scratch space for the run in progress
- `results/`: frozen, committed experiments
- `talk/`: slides and notes
