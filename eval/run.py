#!/usr/bin/env python3
"""Run every task x arm x repeat through headless Claude Code, in random order.
Run from the repo root, with `mitmdump -q -s eval/log_arxiv.py` running in another terminal."""
import glob, json, os, pathlib, random, subprocess, sys, time

ARMS_FILE = os.path.expanduser("~/arxiv-eval-arms/arms.json")
RUNS_PER_TASK = 5
SEED = 42
PROXY = "http://127.0.0.1:8080"
CA = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")

if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
    sys.exit("Commit your changes first, so every result maps to an exact commit.")
COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

ARMS_INFO = json.load(open(ARMS_FILE))
ARMS = {arm: v["path"] for arm, v in ARMS_INFO.items()}

env = dict(os.environ,
           HTTPS_PROXY=PROXY, HTTP_PROXY=PROXY, https_proxy=PROXY, http_proxy=PROXY,
           NO_PROXY="anthropic.com", no_proxy="anthropic.com",
           SSL_CERT_FILE=CA, REQUESTS_CA_BUNDLE=CA, CURL_CA_BUNDLE=CA, NODE_EXTRA_CA_CERTS=CA)

tasks = [json.loads(l) for f in sorted(glob.glob("eval/tasks/*.jsonl")) for l in open(f) if l.strip()]
schedule = [(t, arm, r) for t in tasks for arm in ARMS for r in range(1, RUNS_PER_TASK + 1)]
random.Random(SEED).shuffle(schedule)  # no arm gets "the quiet hour" on arXiv

pathlib.Path("data").mkdir(exist_ok=True)
with open("data/manifest.jsonl", "a") as man:
    for task, arm, r in schedule:
        out = pathlib.Path("data", "runs", arm, task["task_id"], str(r))
        out.mkdir(parents=True, exist_ok=True)
        if (out / "answer.md").exists():
            continue  # resumable
        t0 = time.time()
        try:
            p = subprocess.run(["claude", "-p", task["prompt"]], cwd=ARMS[arm], env=env,
                               capture_output=True, text=True, timeout=1200)
            answer, err, code = p.stdout, p.stderr, p.returncode
        except subprocess.TimeoutExpired:
            answer, err, code = "", "TIMEOUT", -1
        t1 = time.time()
        (out / "answer.md").write_text(answer)
        (out / "stderr.txt").write_text(err)
        man.write(json.dumps({
            "task_id": task["task_id"], "condition": arm, "run": r,
            "answer": str(out / "answer.md"), "t_start": t0, "t_end": t1,
            "latency_s": round(t1 - t0, 2), "exit_code": code,
            "git_commit": COMMIT, "skill_sha256": ARMS_INFO[arm]["skill_sha256"],
        }) + "\n")
        man.flush()
        time.sleep(5)  # clean gap so request logs can't bleed across run windows
