#!/usr/bin/env python3
"""
Score arXiv-skill eval runs, using the arXiv API as ground truth.

  python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl \
      --manifest data/manifest.jsonl --requests data/requests.jsonl \
      --compare skill:baseline --compare skill:placebo

Run AFTER all runs finish, WITHOUT the proxy env vars, so the scorer's own
lookups never land in requests.jsonl. scipy is optional (adds Wilcoxon).
"""
import argparse, csv, json, math, os, pathlib, random, re, statistics, time, unicodedata
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
USER_AGENT = "skill-eval-harness (mailto:YOUR_EMAIL)"   # <- put your email here
MIN_GAP, GAP_TOLERANCE = 3.0, 0.05                       # arXiv API terms: 1 request / 3 s
LOWER_IS_BETTER = {"n_api_requests", "n_arxiv_requests", "latency_s", "input_tokens", "output_tokens"}
NEUTRAL = {"n_cited"}

# ---------------------------------------------------------------- arXiv IDs
ID_RE = re.compile(r"(?<![\w.\-])"
                   r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Za-z\-]+)?/\d{7})"
                   r"(?P<v>v\d+)?(?!\w)")

def normalize(raw):
    raw = re.sub(r"^arxiv:", "", raw.strip(), flags=re.I)
    raw = re.sub(r"v\d+$", "", raw)
    if "/" in raw:                                    # math.AG/0211159 -> math/0211159
        arch, num = raw.split("/", 1)
        raw = arch.split(".")[0] + "/" + num
    return raw

def plausible(base):
    """Reject numbers that merely look like IDs (invalid month, wrong digit count for era)."""
    if "/" in base:
        yymm = base.split("/")[1][:4]
        yy, mm = int(yymm[:2]), int(yymm[2:])
        return 1 <= mm <= 12 and (yy >= 91 or yy <= 7)
    yymm, num = base.split(".")
    yy, mm = int(yymm[:2]), int(yymm[2:])
    if not (1 <= mm <= 12 and yy >= 7):
        return False
    return int(yymm) <= 1412 if len(num) == 4 else int(yymm) >= 1501

def extract_ids(text):
    out = []
    for m in ID_RE.finditer(text):
        base = normalize(m.group("id"))
        if plausible(base):
            out.append((base, m.group("v"), m.start()))
    return out

# ---------------------------------------------------------------- API client
class ArxivClient:
    def __init__(self, cache_path):
        self.cache_path = cache_path
        self.cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
        self._last = 0.0

    def _get(self, params):
        url = API + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            wait = MIN_GAP + 0.1 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=60) as r:
                    data = r.read()
                self._last = time.time()
                return data
            except urllib.error.HTTPError as e:
                self._last = time.time()
                if e.code in (429, 503):
                    ra = e.headers.get("Retry-After")
                    time.sleep(float(ra) if ra and ra.isdigit() else 10 * 2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"arXiv API kept refusing: {url}")

    @staticmethod
    def _parse(data):
        root, out, error = ET.fromstring(data), [], False
        for e in root.findall("a:entry", NS):
            title = " ".join((e.findtext("a:title", "", NS) or "").split())
            eid = e.findtext("a:id", "", NS) or ""
            if title == "Error" or "/api/errors" in eid:
                error = True
                continue
            if not title:
                continue
            full = eid.rsplit("/abs/", 1)[-1]
            m = re.match(r"(.+?)v(\d+)$", full)
            base, latest = (m.group(1), int(m.group(2))) if m else (full, None)
            jr, cm, pc = e.find("x:journal_ref", NS), e.find("x:comment", NS), e.find("x:primary_category", NS)
            out.append({
                "id": base, "latest_version": latest, "title": title,
                "authors": [" ".join((a.findtext("a:name", "", NS) or "").split())
                            for a in e.findall("a:author", NS)],
                "published": (e.findtext("a:published", "", NS) or "")[:10],
                "updated": (e.findtext("a:updated", "", NS) or "")[:10],
                "journal_ref": jr.text.strip() if jr is not None and jr.text else None,
                "comment": cm.text.strip() if cm is not None and cm.text else None,
                "primary_category": pc.get("term") if pc is not None else None,
            })
        return out, error

    def lookup(self, ids):
        todo = sorted({i for i in ids if i not in self.cache})
        for k in range(0, len(todo), 50):
            chunk = todo[k:k + 50]
            try:
                entries, error = self._parse(self._get({"id_list": ",".join(chunk), "max_results": len(chunk)}))
            except urllib.error.HTTPError:
                entries, error = [], True
            if error:  # one malformed ID can spoil a batch: retry singly
                for i in chunk:
                    try:
                        es, _ = self._parse(self._get({"id_list": i, "max_results": 1}))
                    except urllib.error.HTTPError:
                        es = []
                    self.cache[i] = next((e for e in es if e["id"] == i), None)
            else:
                found = {e["id"]: e for e in entries}
                for i in chunk:
                    self.cache[i] = found.get(i)
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path, "w") as f:
                json.dump(self.cache, f, indent=1)
        return {i: self.cache.get(i) for i in ids}

# ---------------------------------------------------------------- text helpers
def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())

def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def date_variants(iso):
    d = date.fromisoformat(iso)
    B, b = d.strftime("%B"), d.strftime("%b")
    return {v.lower() for v in (iso, f"{B} {d.day}, {d.year}", f"{d.day} {B} {d.year}",
                                f"{b} {d.day}, {d.year}", f"{b}. {d.day}, {d.year}", f"{d.day} {b} {d.year}")}

def snippet(text, pos, radius=200):
    lo, hi = max(0, pos - radius), min(len(text), pos + radius)
    s, rel = text[lo:hi], pos - lo
    start, end = s.rfind("\n\n", 0, rel), s.find("\n\n", rel)
    return s[(start + 2 if start >= 0 else 0):(end if end >= 0 else len(s))]

PUB_RE = re.compile(r"published in|appeared in|accepted (?:at|to|in|for|by)|peer[- ]reviewed|"
                    r"to appear in|journal version", re.I)
NEG_RE = re.compile(r"\b(?:not|never|no|non|isn't|wasn't|hasn't|yet to|unpublished)\b[^.]{0,25}$", re.I)
VENUE_HINT_RE = re.compile(r"accepted|published|to appear|journal|proceedings|conference", re.I)
NOT_FOUND_RE = re.compile(r"does not exist|doesn't exist|no (?:such )?(?:paper|record|entry)|not found|"
                          r"could(?: not|n't) find|invalid|not a valid|no results", re.I)

# ---------------------------------------------------------------- BibTeX
def bib_entries(text):
    out = []
    for m in re.finditer(r"@\w+\s*\{", text):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((text[m.start():i + 1], depth == 0))
    return out

def bib_field(entry, name):
    m = re.search(rf"\b{name}\s*=\s*", entry, re.I)
    if not m:
        return None
    i = m.end()
    if i < len(entry) and entry[i] == "{":
        depth = 0
        for j in range(i, len(entry)):
            if entry[j] == "{":
                depth += 1
            elif entry[j] == "}":
                depth -= 1
                if depth == 0:
                    return entry[i + 1:j]
        return None
    if i < len(entry) and entry[i] == '"':
        j = entry.find('"', i + 1)
        return entry[i + 1:j] if j > 0 else None
    m2 = re.match(r"[\w\-]+", entry[i:])
    return m2.group(0) if m2 else None

def last_names_api(authors):
    return sorted(norm(a).split()[-1] for a in authors if norm(a))

def last_names_bib(field):
    names = []
    for p in re.split(r"\s+and\s+", field):
        p = p.strip()
        if not p or norm(p) == "others":
            continue
        toks = norm(p.split(",")[0] if "," in p else p).split()
        if toks:
            names.append(toks[-1])
    return sorted(names)

def check_bibtex(text, rec):
    cands = [(e, ok) for e, ok in bib_entries(text) if rec["id"] in e]
    if not cands:
        return False
    e, balanced = cands[0]
    title, author, year = bib_field(e, "title"), bib_field(e, "author"), bib_field(e, "year")
    return bool(balanced and title and author and year
                and year.strip() == rec["published"][:4]
                and sim(title, rec["title"]) >= 0.9
                and last_names_bib(author) == last_names_api(rec["authors"]))

# ---------------------------------------------------------------- scoring
def score_run(task, run, text, recs, reqs):
    checks = set(task.get("checks", []))
    decoys = {normalize(d) for d in task.get("decoy_ids", [])}
    targets = [normalize(t) for t in task.get("target_ids", [])]
    found = extract_ids(text)
    versions = defaultdict(set)
    for base, v, _ in found:
        versions[base].add(v)
    cited = {b for b, _, _ in found if b not in decoys}
    low, s, flags = text.lower(), {}, []

    fabricated = sorted(i for i in cited if recs.get(i) is None)
    s["no_fabrication"] = not fabricated
    s["n_cited"] = len(cited)
    for i in fabricated:
        pos = next(p for b, _, p in found if b == i)
        flags.append({"type": "fabricated_id", "id": i, "snippet": snippet(text, pos)})

    known = [t for t in targets if recs.get(t)]
    if targets:
        s["found_targets"] = all(t in versions for t in targets)
    if known and "date" in checks:
        s["v1_date_stated"] = all(any(v in low for v in date_variants(recs[t]["published"])) for t in known)
    if known and "version" in checks:
        s["version_stated"] = all(any(versions.get(t, ())) for t in known)
    if known and "bibtex" in checks:
        s["bibtex_valid"] = all(check_bibtex(text, recs[t]) for t in known)
    if decoys and "flags_nonexistent" in checks:
        s["flags_nonexistent"] = bool(NOT_FOUND_RE.search(text))
        flags.append({"type": "decoy_handling", "heuristic_pass": s["flags_nonexistent"], "snippet": text[:1000]})

    violations = 0
    for base, _, pos in found:
        r = recs.get(base)
        if base in decoys or not r or r["journal_ref"] or VENUE_HINT_RE.search(r["comment"] or ""):
            continue
        snip = snippet(text, pos)
        for m in PUB_RE.finditer(snip):
            if not NEG_RE.search(snip[max(0, m.start() - 40):m.start()]):
                violations += 1
                flags.append({"type": "review_status_claim", "id": base, "snippet": snip})
                break
    if cited:
        s["review_status_honest"] = violations == 0

    if reqs is not None and "t_start" in run:
        window = [q for q in reqs if run["t_start"] <= q["start"] <= run["t_end"]]
        api = sorted((q for q in window if urllib.parse.urlparse(q["url"]).hostname == "export.arxiv.org"),
                     key=lambda q: q["start"])
        s["n_api_requests"], s["n_arxiv_requests"] = len(api), len(window)
        if len(api) >= 2:  # compliance is only meaningful with 2+ requests
            s["rate_limit_ok"] = all(
                b["start"] - a["start"] >= MIN_GAP - GAP_TOLERANCE and b["start"] >= (a["end"] or a["start"]) - 1e-3
                for a, b in zip(api, api[1:]))
    for k in ("latency_s", "input_tokens", "output_tokens"):
        if run.get(k) is not None:
            s[k] = run[k]
    return s, flags

# ---------------------------------------------------------------- statistics
def sign_test_p(diffs):
    nz = [d for d in diffs if d != 0]
    n = len(nz)
    if n == 0:
        return 1.0
    k = min(sum(d > 0 for d in nz), sum(d < 0 for d in nz))
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)

def wilcoxon_p(diffs):
    try:
        from scipy.stats import wilcoxon
    except ImportError:
        return None
    if all(d == 0 for d in diffs):
        return 1.0
    try:
        return float(wilcoxon(diffs).pvalue)
    except ValueError:
        return None

def bootstrap_ci(diffs, B=10000, seed=0):
    rng, n = random.Random(seed), len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(B))
    return means[int(0.025 * B)], means[int(0.975 * B) - 1]

def compare(rows, metric, treat, ctrl, split):
    """Task is the unit of analysis: average runs within task, then pair tasks."""
    by = defaultdict(list)
    for r in rows:
        if (split == "all" or r["split"] == split) and r.get(metric) is not None:
            by[(r["task_id"], r["condition"])].append(float(r[metric]))
    tasks = sorted({t for t, _ in by if (t, treat) in by and (t, ctrl) in by})
    if not tasks:
        return None
    tm = [statistics.fmean(by[(t, treat)]) for t in tasks]
    cm = [statistics.fmean(by[(t, ctrl)]) for t in tasks]
    diffs = [a - b for a, b in zip(tm, cm)]
    lo, hi = bootstrap_ci(diffs) if len(tasks) > 1 else (float("nan"), float("nan"))
    return {"n": len(tasks), "ctrl": statistics.fmean(cm), "treat": statistics.fmean(tm),
            "diff": statistics.fmean(diffs), "lo": lo, "hi": hi,
            "sign_p": sign_test_p(diffs), "wilcoxon_p": wilcoxon_p(diffs)}

# ---------------------------------------------------------------- main
def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--requests")
    ap.add_argument("--compare", action="append", help="treatment:control, e.g. skill:baseline")
    ap.add_argument("--out", default="data/results")
    ap.add_argument("--cache", default="data/arxiv_cache.json")
    a = ap.parse_args()
    comparisons = [c.split(":") for c in (a.compare or ["skill:baseline"])]
    os.makedirs(a.out, exist_ok=True)

    tasks = {}
    for p in a.tasks:
        for t in load_jsonl(p):
            t.setdefault("split", pathlib.Path(p).stem)   # dev.jsonl -> "dev", heldout.jsonl -> "heldout"
            tasks[t["task_id"]] = t
    runs = load_jsonl(a.manifest)
    reqs = load_jsonl(a.requests) if a.requests and os.path.exists(a.requests) else None
    for r in runs:
        with open(r["answer"], encoding="utf-8") as f:
            r["_text"] = f.read()

    ids = set()
    for r in runs:
        ids |= {b for b, _, _ in extract_ids(r["_text"])}
    for t in tasks.values():
        ids |= {normalize(i) for i in t.get("target_ids", []) + t.get("decoy_ids", [])}
    print(f"Looking up {len(ids)} arXiv IDs (cached; >= {MIN_GAP:.0f} s between API calls)...")
    recs = ArxivClient(a.cache).lookup(sorted(ids))
    for t in tasks.values():
        for d in t.get("decoy_ids", []):
            if recs.get(normalize(d)):
                print(f"WARNING: decoy {d} in task {t['task_id']} actually exists - pick another.")
        for g in t.get("target_ids", []):
            if not recs.get(normalize(g)):
                print(f"WARNING: target {g} in task {t['task_id']} not found on arXiv.")

    rows, flags = [], []
    for r in runs:
        t = tasks[r["task_id"]]
        s, f = score_run(t, r, r["_text"], recs, reqs)
        rows.append({"task_id": r["task_id"], "split": t["split"],
                     "condition": r["condition"], "run": r["run"], **s})
        flags += [{"task_id": r["task_id"], "condition": r["condition"], "run": r["run"], **x} for x in f]

    metrics = sorted({k for row in rows for k in row} - {"task_id", "split", "condition", "run"})
    with open(os.path.join(a.out, "runs_wide.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_id", "split", "condition", "run"] + metrics)
        w.writeheader()
        for row in rows:
            w.writerow({k: (int(v) if isinstance(v, bool) else v) for k, v in row.items()})

    # Blinded manual review: graders see flags without knowing the arm.
    random.Random(1).shuffle(flags)
    with open(os.path.join(a.out, "flags_blind.jsonl"), "w") as fb, \
         open(os.path.join(a.out, "flags_key.csv"), "w", newline="") as fk:
        kw = csv.writer(fk)
        kw.writerow(["flag_id", "task_id", "condition", "run"])
        for n, fl in enumerate(flags):
            fb.write(json.dumps({"flag_id": n, **{k: v for k, v in fl.items()
                                                  if k not in ("condition", "run")}}) + "\n")
            kw.writerow([n, fl["task_id"], fl["condition"], fl["run"]])

    lines = []
    for split in ("heldout", "dev", "all"):
        for treat, ctrl in comparisons:
            lines.append(f"\n=== {treat} vs {ctrl}  |  split: {split} ===")
            lines.append(f"{'metric':<22}{'dir':>4}{'tasks':>6}{ctrl:>11}{treat:>11}"
                         f"{'diff':>9}   95% CI (bootstrap)    sign p   wilcoxon p")
            for m in metrics:
                res = compare(rows, m, treat, ctrl, split)
                if not res:
                    continue
                d = "·" if m in NEUTRAL else ("↓" if m in LOWER_IS_BETTER else "↑")
                wp = f"{res['wilcoxon_p']:.3g}" if res["wilcoxon_p"] is not None else "-"
                lines.append(f"{m:<22}{d:>4}{res['n']:>6}{res['ctrl']:>11.3f}{res['treat']:>11.3f}"
                             f"{res['diff']:>+9.3f}   [{res['lo']:+.3f}, {res['hi']:+.3f}]"
                             f"{res['sign_p']:>12.3g}{wp:>13}")
    report = "\n".join(lines)
    print(report)
    with open(os.path.join(a.out, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\nWrote {a.out}/runs_wide.csv, summary.txt, flags_blind.jsonl ({len(flags)} items), flags_key.csv")

if __name__ == "__main__":
    main()
