# Overview: does the arXiv skill actually help?

This repo runs a controlled experiment. The same questions go to Claude Code under three
conditions ("arms"). Every answer is scored against the live arXiv API, and statistical
tests decide whether the skill arm does better than the others. This document covers:

1. [What the experiment measures](#1-what-the-experiment-measures)
2. [How the p-values are computed](#2-how-the-p-values-are-computed)
3. [The Holm correction](#3-the-holm-correction)
4. [How many tasks you need](#4-how-many-tasks-you-need)
5. [Running the experiment](#5-running-the-experiment)
6. [Reading the results: does the skill work?](#6-reading-the-results-does-the-skill-work)
7. [Modifying the skill and comparing versions](#7-modifying-the-skill-and-comparing-versions)
8. [File reference](#8-file-reference)

---

## 1. What the experiment measures

### Arms

| Arm | What's installed | What it controls for |
|---|---|---|
| `baseline` | No skill | What Claude does on its own |
| `skill` | `skills/researching-arxiv-papers/SKILL.md` | The thing under test |
| `placebo` | `eval/placebo/SKILL.md`: same name and description, generic reading advice of similar length, nothing arXiv-specific | "Any skill helps because it triggers and adds context." If skill beats placebo, the *content* matters. |

Each arm is a folder outside the repo (`~/arxiv-eval-arms/<arm>`). `run.py` runs
`claude -p` inside it with only project settings loaded (`--setting-sources project`),
no MCP servers (`--strict-mcp-config`) and auto-memory off. Without that, your user-level
skills, plugins, CLAUDE.md, claude.ai connectors and memories would reach every arm. In
particular, a copy of this skill synced to your claude.ai account would quietly turn the
baseline into a second skill arm. `run.py` checks each run's startup event and **stops
the experiment** if an arm sees the wrong arXiv skills or any MCP server.

### Tasks

- `eval/tasks/heldout.jsonl`: 16 tasks. **Only these count for the confirmatory tests.**
- `eval/tasks/dev.jsonl`: 5 tasks for building and tuning. Always exploratory.

A task looks like this:

```json
{"task_id": "unet_and_fake_bib",
 "prompt": "Give me BibTeX entries for arXiv 1505.04597 and arXiv 1505.99999.",
 "target_ids": ["1505.04597"],      // real papers the answer must find
 "decoy_ids": ["1505.99999"],       // IDs that don't exist on arXiv
 "min_cited": 0,                    // optional: minimum number of real IDs the answer must cite
 "checks": ["bibtex", "flags_nonexistent"]}
```

Available `checks`: `date` (states the v1 date), `version` (states the latest version),
`bibtex` (gives a valid BibTeX entry), and `flags_nonexistent` (says the decoy doesn't exist).

The order of all task × arm × repeat runs is shuffled (seed 42), so no arm gets a quiet hour
on arXiv. There are 5 repeats per task per arm, so a full run is (16 + 5) × 3 × 5 = 315 runs.

### Metrics

Each run gets a score for every metric that applies to it. The two **primary** metrics are
the ones the preregistered hypotheses are about. All others are exploratory.

| Metric | Type | Passes when |
|---|---|---|
| **`no_fabrication`** | primary | The run completed, every cited arXiv ID exists, and no real ID has a different paper's title attached. Fails on timeouts and crashes. |
| **`rate_limit_ok`** | primary | Consecutive requests to `export.arxiv.org` in the run's window are ≥ 3 s apart (start to start) and don't overlap. Scored on every run; 0–1 requests is compliant. |
| `completed` | secondary | `claude` exited cleanly with a non-empty answer. |
| `found_targets` | secondary | Every target ID appears in the answer. |
| `v1_date_stated` | secondary | The v1 date of every target appears, in any common format (`2015-12-10`, `Dec 10th 2015`, `10 December 2015`, …). |
| `latest_version_stated` | secondary | The latest version (`v7`, `version 7`) of every target is stated. |
| `bibtex_valid` | secondary | A balanced entry contains the ID, a title ≥ 90% similar to the real one, the year of v1 or of the latest version, and the right authors (`and others` allowed if the listed names are the real leading authors). |
| `flags_nonexistent` | secondary | The answer says the decoy doesn't exist, without inventing content (confirmed by manual review). |
| `review_status_honest` | secondary | The answer doesn't claim "published / accepted / peer reviewed" for a paper whose arXiv metadata shows no venue. |
| `titles_stated` | secondary | Every cited real ID has its correct title nearby. |
| `cited_enough` | secondary | At least `min_cited` real IDs are cited. This guards against a vague answer scoring a perfect `no_fabrication` by citing nothing. |
| `rate_limit_ok_2plus` | secondary | `rate_limit_ok`, restricted to runs with 2+ API requests. |
| `n_api_requests`, `n_arxiv_requests`, `latency_s`, `input_tokens`, `output_tokens`, `cost_usd`, `num_turns` | descriptive | Lower is cheaper. |
| `skill_invoked`, `n_cited` | descriptive | No better direction. `skill_invoked` is the manipulation check: how often the arm's skill actually loaded. |

The heuristics write anything they flag (fabricated IDs, title mismatches, claims of
publication, decoy handling) to a blinded review file. A person who doesn't know the arms
answers a yes/no question per flag, and those verdicts override the heuristics (§5, step 8).

---

## 2. How the p-values are computed

### Step 1: one number per task per arm

Runs of the same task aren't independent: they share a prompt, a difficulty, and a target
paper. Treating 80 runs as 80 independent observations would overstate the evidence. So the
**task** is the unit of analysis. For each task and arm, `score.py` averages the 0/1 scores
of its 5 runs:

```
task          baseline runs    baseline mean   skill runs    skill mean
fake_id       1 0 0 1 0        0.4             1 1 1 1 1     1.0
resnet_date   1 1 1 1 1        1.0             1 1 1 1 1     1.0
gan_bib       0 1 1 1 1        0.8             1 1 1 0 1     0.8
...
```

### Step 2: paired differences

For each task, the difference is `skill mean − baseline mean`: here `+0.6`, `0`, `0`, …
Pairing by task removes the "some tasks are just harder" variation.

### Step 3: the sign test (the preregistered test)

The sign test only asks one question: **across tasks, does the skill win more often than it
loses?**

- Tasks with a difference of exactly 0 are **ties** and drop out.
- Let `n` be the number of untied tasks, and `k` the smaller of the win count and the loss count.
- If the skill had no effect, each untied task is equally likely to go either way, like a
  fair coin. The two-sided p-value is the probability of a split at least as lopsided as
  the one observed:

  ```
  p = 2 × Σ_{i=0..k} C(n, i) / 2^n      (capped at 1)
  ```

**Worked example.** 16 held-out tasks: 9 favour the skill, 1 favours the baseline, 6 are tied.
So n = 10 and k = 1:

```
p = 2 × (C(10,0) + C(10,1)) / 2^10 = 2 × (1 + 10) / 1024 = 0.0215
```

The sign test was chosen because it assumes nothing about the size or shape of the
differences, only their direction. That's appropriate for a handful of tasks with bounded,
lumpy scores. The cost is lower power than tests that use magnitudes.

### Supporting numbers (not used for the decision)

- **Effect size and 95% CI:** the mean per-task difference, with a bootstrap confidence
  interval. The code draws 16 tasks *with replacement* from the 16 differences, takes their
  mean, repeats this 10,000 times, and reports the 2.5th and 97.5th percentiles. It tells you
  *how much* better, not just *whether*.
- **Wilcoxon signed-rank p:** like the sign test, but it also weights by the size of each
  difference. Printed in the exploratory tables if `scipy` is installed.
- **GEE p (sensitivity analysis):** a logistic regression on individual runs
  (`outcome ~ arm`) that accounts for runs within a task being correlated
  (`statsmodels`, exchangeable correlation). If it disagrees sharply with the sign test,
  look into why before believing either. Printed only if `statsmodels` is installed and
  the outcome varies.

---

## 3. The Holm correction

### The problem: more tests, more false positives

At alpha = 0.05, a single test has a 5% chance of a false positive when the skill does
nothing. There are **4** primary tests (2 metrics × 2 comparisons). If you declare victory
whenever *any* of them has p < 0.05, the chance of at least one false positive is up to
about 1 − 0.95⁴ ≈ 19%. A multiple-comparison correction keeps the chance of *any* false
positive across the family at 5% (the "family-wise error rate").

### Bonferroni, and why Holm is better

The bluntest fix (Bonferroni) compares every p to 0.05 / 4 = 0.0125. Holm is a step-down
version that's never less powerful and still controls the family-wise error rate:

1. Sort the m p-values from smallest to largest: p₍₁₎ ≤ p₍₂₎ ≤ … ≤ p₍ₘ₎.
2. Compare p₍₁₎ to 0.05/m, p₍₂₎ to 0.05/(m−1), …, p₍ₘ₎ to 0.05/1.
3. Reject hypotheses in order until the first one fails its threshold. That one and all
   larger ones are not rejected.

`score.py` reports this as **Holm-adjusted p-values**, which you compare directly to 0.05:

```
adjusted p₍ᵢ₎ = max over j ≤ i of  min(1, (m − j + 1) × p₍ⱼ₎)
```

(The running max keeps adjusted p-values in the same order as the raw ones.)

**Worked example** with m = 4:

| Test | raw sign p | rank | multiplier | raw × mult | Holm p | significant? |
|---|---|---|---|---|---|---|
| rate_limit_ok, skill vs baseline | 0.0039 | 1 | ×4 | 0.0156 | **0.0156** | yes |
| rate_limit_ok, skill vs placebo | 0.0078 | 2 | ×3 | 0.0234 | **0.0234** | yes |
| no_fabrication, skill vs baseline | 0.0215 | 3 | ×2 | 0.0430 | **0.0430** | yes |
| no_fabrication, skill vs placebo | 0.1094 | 4 | ×1 | 0.1094 | **0.1094** | no |

The code is `holm()` in `eval/score.py`, and it reproduces exactly this table.

---

## 4. How many tasks you need

A sign test can't produce a small p-value from few tasks. Even if the skill wins *every*
untied task, the smallest possible p is `2 / 2^n`:

| untied tasks (n) | best possible p | best Holm p (×4) |
|---|---|---|
| 5 | 0.0625 | 0.25 |
| 7 | 0.0156 | 0.0625 |
| **8** | 0.0078 | **0.031** ✓ |
| 10 | 0.0020 | 0.0078 |

With 4 primary tests, you need **at least 8 untied tasks all favouring the skill** before any
primary result can be significant. Realistic effects (some losses, some ties) need more.
The old harness had 2 held-out tasks, which could never reach significance. There are now
16, and `summary.txt` prints the minimum under the primary table.

Ties are the main way to lose power. If both arms score 1.0 on a task, that task tells you
nothing. Tasks where the baseline sometimes fails (fake IDs, version and date questions,
BibTeX) are what give the test something to detect. If you add tasks, aim for ones the
baseline plausibly gets wrong.

---

## 5. Running the experiment

### Prerequisites

- Claude Code installed and logged in (`claude --version`).
- Python 3.10+.
- `pip install -r eval/requirements.txt`. That's `mitmproxy`, plus `scipy` and `statsmodels`,
  which are optional and add the Wilcoxon and GEE numbers.
- Run `mitmdump` once and quit it, which creates the CA certificate in `~/.mitmproxy/`.
  The runs trust that CA (only inside the run environment) so the proxy can log HTTPS
  requests to arXiv.

### Step by step (from the repo root)

1. **Freeze the design.** Check `eval/arm-settings.json` (model, allowed tools) and
   `eval/PREREGISTRATION.md`, then commit and tag:

   ```bash
   git add -A && git commit -m "Preregister arXiv skill eval"
   git tag prereg-v1 && git tag skill-v1
   ```

   `run.py` refuses to start with uncommitted changes, so every result maps to an exact commit.

2. **Build the arms** (outside the repo, so the baseline can't find the skill):

   ```bash
   python eval/make_arms.py          # -> ~/arxiv-eval-arms/{baseline,skill,placebo} + arms.json
   ```

3. **Start the proxy** in terminal 1 (leave it running):

   ```bash
   mitmdump -q -s eval/log_arxiv.py  # logs every arxiv.org request to data/requests.jsonl
   ```

4. **Optional but recommended: smoke-test cheaply.** Run the dev tasks once, into a separate
   data folder, so nothing mixes with the real run:

   ```bash
   # terminal 1 instead:  ARXIV_EVAL_DATA=data-smoke mitmdump -q -s eval/log_arxiv.py
   python eval/run.py --data data-smoke --tasks eval/tasks/dev.jsonl --runs 1
   ```

   Look at a few `data-smoke/runs/*/*/1/attempt-1/answer.md` files and the `cost_usd` values
   in `data-smoke/manifest.jsonl`, to estimate the full run's cost and time.

5. **Run everything** in terminal 2:

   ```bash
   python eval/run.py
   ```

   It's resumable: if it stops (Ctrl-C, closed laptop, proxy down), run the same command
   again and it continues where it left off. Before the first run it makes one test request
   through the proxy, to prove requests are being logged. It stops on its own if the proxy dies.

6. **Rerun infrastructure failures**, which the preregistration requires:

   ```bash
   python eval/run.py --rerun-errors                     # API errors, crashes, auth failures
   python eval/run.py --rerun fake_id:skill:3 --reason "arXiv outage 14:02-14:30 UTC"
   ```

   Timeouts are *not* rerun: they count as model failures. Every rerun is logged with its
   reason in `data/reruns.jsonl`. The old attempt stays on disk, and the scorer uses the latest.

7. **Stop the proxy, then score (pass 1).** Score without the proxy env vars, so the scorer's
   own arXiv lookups don't get logged as agent traffic:

   ```bash
   export ARXIV_EVAL_CONTACT=you@example.org   # arXiv asks API clients to identify themselves
   python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl \
       --manifest data/manifest.jsonl --requests data/requests.jsonl \
       --compare skill:baseline --compare skill:placebo
   ```

   This writes `data/results/`: `runs_wide.csv`, `summary.txt`, `flags_blind.jsonl`,
   `flags_key.csv` (the unblinding key: **don't open it yet**), and `verdicts_template.csv`.

8. **Blind manual review.** Give someone `flags_blind.jsonl` and `verdicts_template.csv`,
   *not* `flags_key.csv`. Each line has a `question` and a `snippet`. They answer `yes` or `no`
   per `flag_id` and save the result as `data/results/verdicts.csv`. A blank answer keeps
   the heuristic result.

9. **Score (pass 2): this is the confirmatory result.**

   ```bash
   python eval/score.py ... (same arguments as step 7) ... --verdicts data/results/verdicts.csv
   ```

   Pass 2 doesn't rewrite the flag files, so the flag IDs keep matching the verdicts.

10. **Freeze it:**

    ```bash
    mkdir -p results/2026-10-27-opus-5-5
    cp -r data/* ~/arxiv-eval-arms/arms.json results/2026-10-27-opus-5-5/
    git add results && git commit -m "Results: arXiv skill eval, opus-5-5"
    ```

---

## 6. Reading the results: does the skill work?

`data/results/summary.txt` starts with this block:

```
Manual review: APPLIED (37 verdicts)

=== PRIMARY (preregistered): split heldout, sign test, Holm across 4 tests, alpha 0.05 ===
metric          comparison          tasks untied    ctrl   treat    diff   95% CI            sign p   Holm p   GEE p    verdict
no_fabrication  skill vs baseline      16     10   0.713   0.950  +0.237   [+0.112, +0.375]   0.0215   0.043   0.004    SKILL BETTER
rate_limit_ok   skill vs baseline      16      9   0.588   1.000  +0.412   [+0.237, +0.587]   0.0039   0.0156  0.001    SKILL BETTER
no_fabrication  skill vs placebo       16     10   0.838   0.950  +0.113   [+0.025, +0.212]   0.109    0.109   0.03     not significant
rate_limit_ok   skill vs placebo       16      8   0.613   1.000  +0.388   [+0.200, +0.575]   0.0078   0.0234  0.002    SKILL BETTER
```

(The numbers above are illustrative.) Column by column:

- `tasks`: held-out tasks with data in both arms. `untied`: tasks whose per-task mean differs between the arms.
- `ctrl` / `treat`: the average over tasks of the per-task mean scores (a pass rate).
- `diff` and `95% CI`: the effect size, in percentage points of pass rate.
- `sign p` / `Holm p`: the raw and corrected p-values. **Decide on the Holm p.**
- `verdict`: `SKILL BETTER` / `SKILL WORSE` when Holm p < 0.05, otherwise `not significant`.

### Claims you can make

| Result | What it supports |
|---|---|
| A primary metric is `SKILL BETTER` vs baseline | **H1/H2:** installing the skill improves that behaviour. |
| …and also `SKILL BETTER` vs placebo | **H3:** the improvement comes from the skill's *content*, not just from a skill loading. |
| Better vs baseline but not vs placebo | Something helps, but you can't attribute it to the arXiv-specific content. |
| `not significant` | No detectable effect *at this sample size*. That isn't evidence of no effect: check the CI width and the untied count. |
| `SKILL WORSE` | The skill hurts that behaviour. Read the transcripts. |

### Check these before believing any result

1. **The manipulation check (`skill_invoked`).** In the exploratory tables, look at the
   skill and placebo arms' invocation rates. If the skill loaded in only 30% of runs, the
   effect is diluted. The analysis is intention-to-treat (all runs count), so a low trigger
   rate is itself a finding about the skill's `description`. In the smoke test, the skill
   arm answered `resnet_date` from memory without loading the skill.
2. **`completed`.** It should be about equal across arms. If one arm times out much more, that
   drives its scores.
3. **Warnings at the top of the score output.** Remaining infrastructure errors, mixed
   commits or skill hashes within an arm, decoys that turn out to exist, and targets that
   can't be found.
4. **The secondary metrics** tell you *why*. For example, `no_fabrication` going up while
   `cited_enough` drops would mean the skill is just making Claude cite less.
5. **A handful of actual answers** (`data/runs/<arm>/<task>/<run>/attempt-<n>/answer.md`) and
   transcripts (`transcript.jsonl`, the full tool-call trace).

---

## 7. Modifying the skill and comparing versions

### The rule: iterate on dev, confirm on fresh held-out tasks

Once you've looked at held-out results and changed the skill in response, those held-out
tasks are no longer held out: you've tuned against them. A proper loop looks like this:

1. Edit and iterate using **dev tasks only** (cheap, exploratory).
2. When happy, confirm on the held-out set, ideally a **new** batch of held-out tasks written
   before you test v2 on them. Add them to a new file, e.g. `eval/tasks/heldout2.jsonl`;
   any file whose name starts with `heldout` is a held-out split.

### Recipe: compare a modified skill against v1 (and baseline)

1. **Edit the skill.** Change `skills/researching-arxiv-papers/SKILL.md`, keeping the
   frontmatter `name`. If you change the `description`, that changes *when* it triggers,
   so watch `skill_invoked`. Keep the old version available for comparison:

   ```bash
   git show skill-v1:skills/researching-arxiv-papers/SKILL.md > /tmp/SKILL-v1.md
   ```

2. **Commit and tag** the new version: `git commit -am "Skill v2: ..." && git tag skill-v2`.

3. **Build arms for the comparison.** Use a new root folder, so the v1 experiment's arms
   stay intact:

   ```bash
   python eval/make_arms.py --root ~/arxiv-eval-v2 \
       --arm baseline=none \
       --arm skill_v1=/tmp/SKILL-v1.md \
       --arm skill_v2=skills/researching-arxiv-papers/SKILL.md
   ```

   Every arm installs its file under the same skill name, so they trigger identically.

4. **Run all arms together, into a separate data folder:**

   ```bash
   # terminal 1
   ARXIV_EVAL_DATA=data-v2 mitmdump -q -s eval/log_arxiv.py
   # terminal 2  (dev only while iterating; drop --tasks for the full set)
   python eval/run.py --arms-file ~/arxiv-eval-v2/arms.json --data data-v2 --tasks eval/tasks/dev.jsonl
   python eval/run.py --arms-file ~/arxiv-eval-v2/arms.json --data data-v2 --rerun-errors
   ```

   **Rerun the old version and the baseline alongside the new one**, rather than comparing
   against last month's numbers. Model updates, arXiv load and Claude Code versions all drift
   over time, and a shuffled side-by-side run cancels that out. Reusing old results confounds
   "v2 is better" with "it's a different week".

5. **Score the comparison you care about:**

   ```bash
   python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl \
       --manifest data-v2/manifest.jsonl --requests data-v2/requests.jsonl \
       --out data-v2/results --cache data-v2/arxiv_cache.json \
       --compare skill_v2:skill_v1 --compare skill_v2:baseline
   ```

   The exploratory tables show every metric for each `--compare` pair, per split, with the
   sign test, bootstrap CI and Wilcoxon p.

   **Note:** the PRIMARY block is hard-wired to the preregistered `skill` vs `baseline` and
   `placebo` arm names. For a preregistered v2-vs-v1 test, either name the arms `skill`
   (v2) and `placebo` (v1), or edit `PRIMARY_COMPARISONS` in `eval/score.py` and the
   preregistration *before* running, then tag.

   If you run several comparisons and pick out the significant ones afterwards, apply Holm
   by hand across all of them (§3).

6. **Freeze** `data-v2/` and `~/arxiv-eval-v2/arms.json` into `results/<date>-v2-vs-v1/`.

### Quick checks while iterating

- Want to know if a wording change makes the skill trigger more? Run the dev tasks with
  `--runs 3` and compare `skill_invoked` between arms.
- Want to target one weakness, e.g. BibTeX? Write two or three new dev tasks with
  `"checks": ["bibtex"]` and watch `bibtex_valid`.

---

## 8. File reference

| Path | Purpose |
|---|---|
| `skills/researching-arxiv-papers/SKILL.md` | The skill under test |
| `eval/placebo/SKILL.md` | Placebo: same name and description, generic body |
| `eval/PREREGISTRATION.md` | Hypotheses, primary metrics, analysis plan, exclusions (freeze with a tag) |
| `eval/tasks/{dev,heldout}.jsonl` | Tasks; the split comes from the file name |
| `eval/arm-settings.json` | Model, allowed tools, auto-memory off; copied into every arm |
| `eval/make_arms.py` | Builds arm folders + `arms.json` (paths, skill SHA-256) |
| `eval/log_arxiv.py` | mitmproxy addon that logs arxiv.org requests |
| `eval/run.py` | Runs the shuffled schedule; isolation checks; reruns |
| `eval/score.py` | Ground-truth scoring, blinded review, statistics |

Per-run outputs live in `data/runs/<arm>/<task>/<run>/attempt-<n>/`: `answer.md`,
`transcript.jsonl` and `stderr.txt`. `data/manifest.jsonl` holds one row per attempt: status,
timing, model, `skill_invoked`, tool counts, tokens, cost, commit, skill hash and Claude Code
version. Score outputs go to `data/results/`.

### Troubleshooting

- **"ISOLATION FAILURE: arm baseline sees arXiv skills [...]":** the skill is reaching the
  baseline from somewhere `--setting-sources project` doesn't cover. Disable it in your claude.ai
  skill settings, or remove it from wherever it's installed, then rerun.
- **"Proxy is up but nothing reached data/requests.jsonl":** mitmdump wasn't started from
  the repo root, or `ARXIV_EVAL_DATA` doesn't match `--data`.
- **"Commit your changes first":** `run.py` requires a clean tree. `data/` and `data-*/` are gitignored.
- **The scorer stops with "arXiv API unreachable":** arXiv is rate-limiting or down. Wait and
  rerun. Lookups are cached in `arxiv_cache.json`, and failures are never cached, so a bad
  moment can't turn real papers into "fabrications".
