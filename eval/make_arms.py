#!/usr/bin/env python3
"""Build the three arms OUTSIDE the repo so no arm can see the skill, tasks, or past results.
Usage (from repo root):  python eval/make_arms.py [~/arxiv-eval-arms]"""
import hashlib, json, pathlib, shutil, sys

REPO = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "~/arxiv-eval-arms").expanduser().resolve()
NAME = "researching-arxiv-papers"
SOURCES = {
    "baseline": None,
    "skill": REPO / "skills" / NAME / "SKILL.md",
    "placebo": REPO / "eval" / "placebo" / "SKILL.md",
}

if ROOT.is_relative_to(REPO):
    sys.exit("Arms must live outside the repo, or the baseline can read the skill.")

info = {}
for arm, src in SOURCES.items():
    d = ROOT / arm
    shutil.rmtree(d, ignore_errors=True)
    (d / ".claude").mkdir(parents=True)
    shutil.copy(REPO / "eval" / "arm-settings.json", d / ".claude" / "settings.json")
    if src:
        (d / ".claude" / "skills" / NAME).mkdir(parents=True)
        shutil.copy(src, d / ".claude" / "skills" / NAME / "SKILL.md")
    info[arm] = {"path": str(d),
                 "skill_sha256": hashlib.sha256(src.read_bytes()).hexdigest() if src else None}

(ROOT / "arms.json").write_text(json.dumps(info, indent=2))
print(json.dumps(info, indent=2))
