#!/usr/bin/env python3
"""Build the arms OUTSIDE the repo so no arm can see the skill, tasks, or past results.

Usage (from repo root):
  python eval/make_arms.py                      # baseline, skill, placebo in ~/arxiv-eval-arms
  python eval/make_arms.py --root ~/arxiv-eval-v2 \\
      --arm baseline=none --arm skill=skills/researching-arxiv-papers/SKILL.md \\
      --arm skill_v2=/path/to/edited/SKILL.md   # compare a modified skill

Each --arm is NAME=PATH, where PATH is a SKILL.md file, or `none` for an arm with no skill.
The file is always installed under the same skill name, so every arm triggers identically."""
import argparse, hashlib, json, pathlib, shutil, sys

REPO = pathlib.Path(__file__).resolve().parents[1]
NAME = "researching-arxiv-papers"
DEFAULT_ARMS = {
    "baseline": None,
    "skill": REPO / "skills" / NAME / "SKILL.md",
    "placebo": REPO / "eval" / "placebo" / "SKILL.md",
}

ap = argparse.ArgumentParser()
ap.add_argument("--root", default="~/arxiv-eval-arms")
ap.add_argument("--arm", action="append", metavar="NAME=PATH")
a = ap.parse_args()

ROOT = pathlib.Path(a.root).expanduser().resolve()
if ROOT.is_relative_to(REPO):
    sys.exit("Arms must live outside the repo, or the baseline can read the skill.")

sources = DEFAULT_ARMS
if a.arm:
    sources = {}
    for spec in a.arm:
        arm, _, path = spec.partition("=")
        if not arm or not path:
            sys.exit(f"Bad --arm {spec!r}; expected NAME=PATH or NAME=none")
        sources[arm] = None if path == "none" else pathlib.Path(path).expanduser().resolve()
for arm, src in sources.items():
    if src and not src.is_file():
        sys.exit(f"Arm {arm}: {src} is not a file")

settings = REPO / "eval" / "arm-settings.json"
info = {}
for arm, src in sources.items():
    d = ROOT / arm
    shutil.rmtree(d, ignore_errors=True)
    (d / ".claude").mkdir(parents=True)
    shutil.copy(settings, d / ".claude" / "settings.json")
    if src:
        (d / ".claude" / "skills" / NAME).mkdir(parents=True)
        shutil.copy(src, d / ".claude" / "skills" / NAME / "SKILL.md")
    info[arm] = {"path": str(d), "source": str(src) if src else None,
                 "skill_sha256": hashlib.sha256(src.read_bytes()).hexdigest() if src else None}

(ROOT / "arms.json").write_text(json.dumps(info, indent=2))
print(json.dumps(info, indent=2))
print(f"\nNext: python eval/run.py --arms-file {ROOT / 'arms.json'}")
