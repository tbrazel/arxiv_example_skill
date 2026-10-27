#!/usr/bin/env python3
"""Run every task x arm x repeat through headless Claude Code, in random order.
Run from the repo root, with `mitmdump -q -s eval/log_arxiv.py` running in another terminal.

  python eval/run.py                                  # run (or resume) everything
  python eval/run.py --rerun-errors                   # rerun runs lost to infrastructure errors
  python eval/run.py --rerun fake_id:skill:3 --reason "arXiv outage 14:02-14:30 UTC"

Every attempt gets its own folder; the manifest is append-only and score.py uses the
latest attempt for each (task, arm, run). Reruns and their reasons go to reruns.jsonl."""
import argparse, glob, hashlib, json, os, pathlib, random, signal, socket, ssl, subprocess, sys, time
import urllib.request

NAME = "researching-arxiv-papers"
PROXY_HOST, PROXY_PORT = "127.0.0.1", 8080
PROXY = f"http://{PROXY_HOST}:{PROXY_PORT}"
CA = os.path.expanduser(os.environ.get("MITMPROXY_CA", "~/.mitmproxy/mitmproxy-ca-cert.pem"))

ap = argparse.ArgumentParser()
ap.add_argument("--arms-file", default="~/arxiv-eval-arms/arms.json")
ap.add_argument("--data", default="data")
ap.add_argument("--tasks", nargs="+", default=sorted(glob.glob("eval/tasks/*.jsonl")))
ap.add_argument("--runs", type=int, default=5, help="runs per task per arm (preregistered: 5)")
ap.add_argument("--seed", type=int, default=42, help="schedule seed (preregistered: 42)")
ap.add_argument("--only-arms", help="comma-separated subset of arms to run")
ap.add_argument("--timeout", type=int, default=1200, help="seconds per run")
ap.add_argument("--rerun-errors", action="store_true",
                help="rerun runs whose latest attempt ended in an infrastructure error")
ap.add_argument("--rerun", action="append", default=[], metavar="TASK:ARM:RUN")
ap.add_argument("--reason", help="required with --rerun; recorded in reruns.jsonl")
a = ap.parse_args()
if a.rerun and not a.reason:
    sys.exit("--rerun needs --reason (it goes in the record of exclusions).")

# ---------------------------------------------------------------- preflight
if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
    sys.exit("Commit your changes first, so every result maps to an exact commit.")
COMMIT = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
CLAUDE_VERSION = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()

SETTINGS_PATH = pathlib.Path("eval/arm-settings.json")
SETTINGS = json.loads(SETTINGS_PATH.read_text())
SETTINGS_SHA = hashlib.sha256(SETTINGS_PATH.read_bytes()).hexdigest()

ARMS_INFO = json.load(open(os.path.expanduser(a.arms_file)))
for arm, v in ARMS_INFO.items():
    f = pathlib.Path(v["path"], ".claude", "skills", NAME, "SKILL.md")
    sha = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    if sha != v["skill_sha256"]:
        sys.exit(f"Arm {arm}: installed SKILL.md does not match arms.json. Rerun make_arms.py.")
ARMS = list(ARMS_INFO)
if a.only_arms:
    ARMS = [x for x in a.only_arms.split(",") if x]
    if missing := set(ARMS) - set(ARMS_INFO):
        sys.exit(f"Unknown arms: {sorted(missing)}")

DATA = pathlib.Path(a.data)
DATA.mkdir(parents=True, exist_ok=True)
REQUESTS = DATA / "requests.jsonl"

def proxy_up():
    try:
        socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=2).close()
        return True
    except OSError:
        return False

def check_proxy_logging():
    """One request through the proxy, before any run window, to prove requests get logged."""
    if not os.path.exists(CA):
        sys.exit(f"No mitmproxy CA at {CA}; run mitmdump once to create it.")
    if not proxy_up():
        sys.exit(f"Proxy not listening on {PROXY}. Start: mitmdump -q -s eval/log_arxiv.py")
    before = REQUESTS.stat().st_size if REQUESTS.exists() else 0
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": PROXY}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=CA)))
    opener.open("https://arxiv.org/robots.txt", timeout=30).read()
    time.sleep(2)
    if not REQUESTS.exists() or REQUESTS.stat().st_size == before:
        sys.exit(f"Proxy is up but nothing reached {REQUESTS}. Is mitmdump running from the repo root "
                 f"with ARXIV_EVAL_DATA matching --data?")

check_proxy_logging()

env = dict(os.environ,
           HTTPS_PROXY=PROXY, HTTP_PROXY=PROXY, https_proxy=PROXY, http_proxy=PROXY,
           NO_PROXY="anthropic.com", no_proxy="anthropic.com",
           SSL_CERT_FILE=CA, REQUESTS_CA_BUNDLE=CA, CURL_CA_BUNDLE=CA, NODE_EXTRA_CA_CERTS=CA,
           CLAUDE_CODE_DISABLE_AUTO_MEMORY="1")

# Only project settings: user-level skills, plugins, CLAUDE.md, and claude.ai connectors would
# otherwise reach every arm (including a synced copy of this very skill). Project
# permissions are ignored in untrusted folders, so tools and model are passed as flags.
CMD = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--no-session-persistence",
       "--setting-sources", "project", "--strict-mcp-config",
       "--model", SETTINGS["model"], "--allowedTools", *SETTINGS["permissions"]["allow"]]

# ---------------------------------------------------------------- schedule
tasks = [json.loads(l) for f in a.tasks for l in open(f) if l.strip()]
schedule = [(t, arm, r) for t in tasks for arm in ARMS_INFO for r in range(1, a.runs + 1)]
random.Random(a.seed).shuffle(schedule)  # no arm gets "the quiet hour" on arXiv
schedule = [s for s in schedule if s[1] in ARMS]

MANIFEST = DATA / "manifest.jsonl"
history = {}
if MANIFEST.exists():
    for l in open(MANIFEST):
        if l.strip():
            row = json.loads(l)
            history.setdefault((row["task_id"], row["condition"], row["run"]), []).append(row)

reruns = {}
for spec in a.rerun:
    task_id, arm, r = spec.split(":")
    reruns[(task_id, arm, int(r))] = a.reason
if a.rerun_errors:
    for key, rows in history.items():
        if rows[-1].get("status") == "error" and key not in reruns:
            reruns[key] = f"infrastructure error (exit {rows[-1]['exit_code']}): {rows[-1].get('error', '')}"[:300]

# ---------------------------------------------------------------- one run
def parse_stream(stdout):
    init, result, tools, invoked = None, None, {}, False
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = e
        elif e.get("type") == "result":
            result = e
        elif e.get("type") == "assistant":
            for b in e.get("message", {}).get("content", []):
                if b.get("type") != "tool_use":
                    continue
                tools[b["name"]] = tools.get(b["name"], 0) + 1
                inp = b.get("input", {})
                if b["name"] == "Skill" and str(inp.get("skill", "")).split(":")[-1] == NAME:
                    invoked = True
                if b["name"] == "Read" and f"skills/{NAME}/" in str(inp.get("file_path", "")):
                    invoked = True
    return init, result, tools, invoked

def check_isolation(arm, init):
    """Stop the whole experiment if an arm sees the wrong skills or any MCP server."""
    if init is None:
        return None  # the run died before starting; recorded as an error
    arxiv = sorted(s for s in init.get("skills", []) if "arxiv" in s.lower())
    expected = [NAME] if ARMS_INFO[arm]["skill_sha256"] else []
    if arxiv != expected:
        return f"arm {arm} sees arXiv skills {arxiv}, expected {expected}"
    if init.get("mcp_servers"):
        return f"arm {arm} sees MCP servers {[m.get('name') for m in init['mcp_servers']]}"
    return None

def run_one(task, arm, r):
    key = (task["task_id"], arm, r)
    attempt = len(history.get(key, [])) + 1
    out = DATA / "runs" / arm / task["task_id"] / str(r) / f"attempt-{attempt}"
    out.mkdir(parents=True, exist_ok=True)
    if not proxy_up():
        sys.exit(f"Proxy went down; stopping before {key}. Restart it and rerun this script.")

    t0 = time.time()
    # The prompt goes on stdin: --allowedTools is variadic and would swallow a trailing argument.
    p = subprocess.Popen(CMD, cwd=ARMS_INFO[arm]["path"], env=env, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = p.communicate(task["prompt"], timeout=a.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    try:
        os.killpg(p.pid, signal.SIGKILL)  # also kills anything the agent left running
    except ProcessLookupError:
        pass
    if timed_out:
        stdout, stderr = p.communicate()
    t1 = time.time()

    init, result, tools, invoked = parse_stream(stdout)
    if leak := check_isolation(arm, init):
        (out / "transcript.jsonl").write_text(stdout)
        sys.exit(f"ISOLATION FAILURE: {leak}. Nothing recorded for {key}; fix it and rerun.")

    answer = (result or {}).get("result") or ""
    if timed_out:
        status, error = "timeout", "TIMEOUT"
    elif p.returncode != 0 or result is None or result.get("is_error"):
        status = "error"
        error = (result or {}).get("api_error_status") or (result or {}).get("subtype") or stderr[-300:]
    else:
        status, error = "ok", None

    (out / "answer.md").write_text(answer)
    (out / "transcript.jsonl").write_text(stdout)
    (out / "stderr.txt").write_text(stderr)
    usage = (result or {}).get("usage") or {}
    row = {
        "task_id": task["task_id"], "condition": arm, "run": r, "attempt": attempt,
        "status": status, "error": str(error) if error else None,
        "answer": str(out / "answer.md"), "transcript": str(out / "transcript.jsonl"),
        "t_start": t0, "t_end": t1, "latency_s": round(t1 - t0, 2),
        "exit_code": -1 if timed_out else p.returncode,
        "model": (init or {}).get("model"), "skill_invoked": invoked, "tool_calls": tools,
        "input_tokens": (usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
                         + usage.get("cache_read_input_tokens", 0)) if usage else None,
        "output_tokens": usage.get("output_tokens") if usage else None,
        "cost_usd": (result or {}).get("total_cost_usd"), "num_turns": (result or {}).get("num_turns"),
        "git_commit": COMMIT, "skill_sha256": ARMS_INFO[arm]["skill_sha256"],
        "arm_settings_sha256": SETTINGS_SHA, "claude_version": CLAUDE_VERSION,
    }
    with open(MANIFEST, "a") as man:
        man.write(json.dumps(row) + "\n")
    history.setdefault(key, []).append(row)
    return status

# ---------------------------------------------------------------- main loop
todo = [(t, arm, r) for t, arm, r in schedule
        if (t["task_id"], arm, r) not in history or (t["task_id"], arm, r) in reruns]
n_reruns = sum(k in history for k in reruns)
print(f"{len(todo)} runs to do ({n_reruns} reruns; {len(schedule) - len(todo)} already recorded); "
      f"model {SETTINGS['model']}")
for n, (task, arm, r) in enumerate(todo, 1):
    key = (task["task_id"], arm, r)
    if key in reruns and key in history:
        with open(DATA / "reruns.jsonl", "a") as f:
            f.write(json.dumps({"task_id": key[0], "condition": arm, "run": r,
                                "replaces_attempt": len(history[key]), "reason": reruns[key],
                                "time": time.time()}) + "\n")
    status = run_one(task, arm, r)
    print(f"[{n}/{len(todo)}] {key[0]} {arm} run {r}: {status}", flush=True)
    time.sleep(5)  # clean gap so request logs can't bleed across run windows

errors = sum(rows[-1]["status"] == "error" for rows in history.values())
if errors:
    print(f"\n{errors} runs ended in infrastructure errors. Rerun them: python eval/run.py --rerun-errors")
