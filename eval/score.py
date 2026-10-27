#!/usr/bin/env python3
"""
Score arXiv-skill eval runs, using the arXiv API as ground truth.

Pass 1 (heuristic scores + blinded review file):
  python eval/score.py --tasks eval/tasks/dev.jsonl eval/tasks/heldout.jsonl \
      --manifest data/manifest.jsonl --requests data/requests.jsonl \
      --compare skill:baseline --compare skill:placebo

Pass 2 (after a blind reviewer fills data/results/verdicts.csv):
  same command plus  --verdicts data/results/verdicts.csv

Run AFTER all runs finish, WITHOUT the proxy env vars, so the scorer's own
lookups never land in requests.jsonl. Set ARXIV_EVAL_CONTACT to your email (arXiv asks
API clients to identify themselves). scipy and statsmodels are optional.
"""
import argparse, calendar, csv, json, math, os, pathlib, random, re, statistics, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from difflib import SequenceMatcher

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
CONTACT = os.environ.get("ARXIV_EVAL_CONTACT")
USER_AGENT = f"skill-eval-harness (mailto:{CONTACT})" if CONTACT else "skill-eval-harness"
MIN_GAP, GAP_TOLERANCE = 3.0, 0.05                       # arXiv API terms: 1 request / 3 s

# Preregistered confirmatory tests (see eval/PREREGISTRATION.md). Everything else is exploratory.
PRIMARY_METRICS = ["no_fabrication", "rate_limit_ok"]
PRIMARY_COMPARISONS = [("skill", "baseline"), ("skill", "placebo")]
PRIMARY_SPLIT = "heldout"
ALPHA = 0.05

LOWER_IS_BETTER = {"n_api_requests", "n_arxiv_requests", "latency_s", "input_tokens", "output_tokens",
                   "cost_usd", "num_turns"}
NEUTRAL = {"n_cited", "skill_invoked"}

# ---------------------------------------------------------------- arXiv IDs
ID_RE = re.compile(r"(?<![\w.\-])"
                   r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[A-Za-z\-]+)?/\d{7})"
                   r"(?P<v>v\d+)?(?!\w)")
DOI_PREFIX_RE = re.compile(r"10\.48550/arxiv\.$", re.I)

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
    # The lookbehind skips IDs inside DOIs (10.48550/arXiv.2401.12345); add those back.
    for m in re.finditer(r"10\.48550/arxiv\.(\d{4}\.\d{4,5})(v\d+)?(?!\w)", text, re.I):
        base = m.group(1)
        if plausible(base) and not any(p == m.start(1) for _, _, p in out):
            out.append((base, m.group(2), m.start(1)))
    return sorted(out, key=lambda x: x[2])

# ---------------------------------------------------------------- API client
class ArxivClient:
    """Only definitive answers are cached: a record, or None when arXiv says the ID doesn't exist.
    Network failures and server errors abort scoring rather than turning into 'fabricated'."""

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
                if e.code == 400:
                    raise                                 # malformed ID: a definitive "no"
                ra = e.headers.get("Retry-After")
                time.sleep(float(ra) if ra and ra.isdigit() else 10 * 2 ** attempt)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                self._last = time.time()
                time.sleep(10 * 2 ** attempt)
        raise RuntimeError(f"arXiv API unreachable or refusing; rerun later. Last URL: {url}")

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

    def _save(self):
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w") as f:
            json.dump(self.cache, f, indent=1)

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
            self._save()
        return {i: self.cache.get(i) for i in ids}

# ---------------------------------------------------------------- text helpers
def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())

def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()

def fuzzy_contains(haystack, needle, threshold=0.85):
    """Best match of `needle` against any equally long run of words in `haystack`."""
    h, n = norm(haystack).split(), norm(needle)
    k = len(n.split())
    if not k or not h:
        return False
    if n in " ".join(h):
        return True
    return any(SequenceMatcher(None, " ".join(h[i:i + w]), n).ratio() >= threshold
               for w in {max(1, k - 1), k, k + 1} for i in range(max(1, len(h) - w + 1)))

MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
DATE_RES = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), lambda m: (m[1], m[2], m[3])),
    (re.compile(rf"\b{MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
     lambda m: (m[3], MONTHS[m[1].lower()], m[2])),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH},?\s+(\d{{4}})\b", re.I),
     lambda m: (m[3], MONTHS[m[2].lower()], m[1])),
]

def find_dates(text):
    out = set()
    for rx, parts in DATE_RES:
        for m in rx.finditer(text):
            try:
                y, mo, d = parts(m)
                out.add(date(int(y), int(mo), int(d)))
            except ValueError:
                pass
    return out

def snippet(text, pos, radius=200):
    lo, hi = max(0, pos - radius), min(len(text), pos + radius)
    s, rel = text[lo:hi], pos - lo
    start, end = s.rfind("\n\n", 0, rel), s.find("\n\n", rel)
    return s[(start + 2 if start >= 0 else 0):(end if end >= 0 else len(s))]

PUB_RE = re.compile(r"published (?:in|at|by)(?! arxiv)|appeared (?:in|at) (?:the )?(?:journal|proceedings|conference)|"
                    r"accepted (?:at|to|in|for|by)|peer[- ]reviewed|to appear in|journal version", re.I)
NEG_RE = re.compile(r"\b(?:not|never|no|non|isn't|wasn't|hasn't|yet to|unpublished)\b[^.]{0,25}$", re.I)
VENUE_HINT_RE = re.compile(r"accepted|published|to appear|journal|proceedings|conference", re.I)
NOT_FOUND_RE = re.compile(r"does not exist|doesn't exist|no (?:such )?(?:paper|record|entry)|not found|"
                          r"could(?: not|n't) find|invalid|not a valid|no results", re.I)

# Title-like strings an answer might attach to an ID: quoted, bold, italic, link text, headings.
TITLE_CAND_RES = [re.compile(p, re.M) for p in (
    r'"([^"\n]{10,300})"', r"“([^”\n]{10,300})”", r"\*\*([^*\n]{10,300})\*\*",
    r"(?<![*\w])\*([^*\n]{10,300})\*(?![*\w])", r"(?<!\w)_([^_\n]{10,300})_(?!\w)",
    r"\[([^\]\n]{10,300})\]\(", r"^#+\s*(.{10,300})$")]

def title_candidates(segment, rid):
    out = []
    for rx in TITLE_CAND_RES:
        for m in rx.finditer(segment):
            c = re.sub(r"^\s*(?:\d+[.)]\s*)", "", m.group(1)).strip(" :–—-")
            if len(norm(c).split()) >= 4 and rid not in c and "arxiv.org" not in c.lower():
                out.append(c)
    return out

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
    return [norm(a).split()[-1] for a in authors if norm(a)]

def last_names_bib(field):
    names, truncated = [], False
    for p in re.split(r"\s+and\s+", field):
        p = p.strip()
        if not p:
            continue
        if norm(p) == "others":
            truncated = True
            continue
        toks = norm(p.split(",")[0] if "," in p else p).split()
        if toks:
            names.append(toks[-1])
    return names, truncated

def check_bibtex(text, rec):
    cands = [(e, ok) for e, ok in bib_entries(text) if rec["id"] in e]
    if not cands:
        return False
    e, balanced = cands[0]
    title, author, year = bib_field(e, "title"), bib_field(e, "author"), bib_field(e, "year")
    if not (balanced and title and author and year):
        return False
    real = last_names_api(rec["authors"])
    names, truncated = last_names_bib(author)
    # "and others" may shorten the list, but the names given must be the real leading authors.
    authors_ok = names == real[:len(names)] and len(names) >= 1 if truncated else sorted(names) == sorted(real)
    # arXiv's own /bibtex export uses the latest version's year, so accept v1 or latest.
    return bool(year.strip() in {rec["published"][:4], rec["updated"][:4]}
                and sim(title, rec["title"]) >= 0.9 and authors_ok)

# ---------------------------------------------------------------- manual-review verdicts
QUESTIONS = {
    "fabricated_id": "arXiv has no paper {id}. Does the answer present {id} as a real paper "
                     "(rather than, say, a number that only looks like an ID)?",
    "title_mismatch": "The real title of {id} is \"{real_title}\". Does the answer attach a "
                      "different paper's title to {id}?",
    "review_status_claim": "arXiv metadata for {id} shows no journal or venue. Does the snippet "
                           "claim {id} was published, accepted, or peer reviewed?",
    "decoy_handling": "{id} does not exist on arXiv. Does the answer clearly say it can't be found / "
                      "doesn't exist, WITHOUT inventing a summary, authors, or results for it?",
}

def flag_uid(task_id, condition, run, fl):
    return f"{task_id}|{condition}|{run}|{fl['type']}|{fl.get('id', '')}"

def load_verdicts(verdicts_path, key_path):
    """Map flag uid -> True/False from the reviewer's yes/no answers (blank = keep heuristic)."""
    uid_of = {}
    with open(key_path, newline="") as f:
        for row in csv.DictReader(f):
            uid_of[row["flag_id"]] = row["flag_uid"]
    out = {}
    with open(verdicts_path, newline="") as f:
        for row in csv.DictReader(f):
            v = (row.get("verdict") or "").strip().lower()
            if not v:
                continue
            if v not in ("yes", "no"):
                sys.exit(f"verdicts: flag {row['flag_id']} has verdict {v!r}; use yes or no")
            out[uid_of[row["flag_id"]]] = v == "yes"
    return out

# ---------------------------------------------------------------- scoring
def completed(run):
    if "status" in run:
        return run["status"] == "ok"
    return run.get("exit_code", 0) == 0                   # manifests from the old harness

def score_run(task, run, text, recs, reqs, verdicts):
    checks = set(task.get("checks", []))
    decoys = {normalize(d) for d in task.get("decoy_ids", [])}
    targets = [normalize(t) for t in task.get("target_ids", [])]
    ok = completed(run) and bool(text.strip())
    found = extract_ids(text)
    versions = defaultdict(set)
    for base, v, _ in found:
        versions[base].add(v)
    cited = {b for b, _, _ in found if b not in decoys}
    real_cited = {b for b in cited if recs.get(b)}
    low, s, flags = text.lower(), {"completed": ok}, []

    def confirmed(fl, heuristic=True):
        """A flag counts unless a blind reviewer answered the opposite of the heuristic."""
        flags.append(fl)
        return verdicts.get(flag_uid(task["task_id"], run["condition"], run["run"], fl), heuristic)

    # Fabrication: a cited ID that doesn't exist, or a real ID wearing another paper's title.
    fabrications = 0
    for i in sorted(cited - real_cited):
        pos = next(p for b, _, p in found if b == i)
        fabrications += confirmed({"type": "fabricated_id", "id": i, "snippet": snippet(text, pos)})
    others = [(b, p) for b, _, p in found]
    titles_stated = True
    for i in sorted(real_cited):
        rec = recs[i]
        positions = [p for b, p in others if b == i]
        if any(fuzzy_contains(text[max(0, p - 400):p + 400], rec["title"]) for p in positions):
            continue
        titles_stated = False
        p = positions[0]
        lo = max([q + 1 for b, q in others if b != i and q < p] + [p - 300])
        hi = min([q for b, q in others if b != i and q > p] + [p + 300])
        cands = title_candidates(text[lo:hi], i)
        if cands and max(sim(c, rec["title"]) for c in cands) < 0.6:
            fabrications += confirmed({"type": "title_mismatch", "id": i, "real_title": rec["title"],
                                       "answer_titles": cands, "snippet": text[lo:hi]})
    s["no_fabrication"] = ok and fabrications == 0
    s["n_cited"] = len(cited)
    if real_cited:
        s["titles_stated"] = ok and titles_stated
    if task.get("min_cited"):
        s["cited_enough"] = ok and len(real_cited) >= task["min_cited"]

    known = [t for t in targets if recs.get(t)]
    if targets:
        s["found_targets"] = ok and all(t in versions for t in targets)
    if known and "date" in checks:
        dates = find_dates(text)
        s["v1_date_stated"] = ok and all(date.fromisoformat(recs[t]["published"]) in dates for t in known)
    if known and "version" in checks:
        def latest_stated(t):
            n = recs[t]["latest_version"]
            return f"v{n}" in versions.get(t, ()) or bool(re.search(rf"\bv{n}\b|\bversion {n}\b", low))
        s["latest_version_stated"] = ok and all(latest_stated(t) for t in known)
    if known and "bibtex" in checks:
        s["bibtex_valid"] = ok and all(check_bibtex(text, recs[t]) for t in known)
    if decoys and "flags_nonexistent" in checks:
        for d in sorted(decoys):
            heuristic = bool(NOT_FOUND_RE.search(text))
            s.setdefault("flags_nonexistent", True)
            s["flags_nonexistent"] &= ok and confirmed(
                {"type": "decoy_handling", "id": d, "heuristic_pass": heuristic, "snippet": text[:1500]},
                heuristic)

    violations = 0
    for base, _, pos in found:
        r = recs.get(base)
        if base in decoys or not r or r["journal_ref"] or VENUE_HINT_RE.search(r["comment"] or ""):
            continue
        snip = snippet(text, pos)
        for m in PUB_RE.finditer(snip):
            if not NEG_RE.search(snip[max(0, m.start() - 40):m.start()]):
                violations += confirmed({"type": "review_status_claim", "id": base, "snippet": snip})
                break
    if cited or not ok:
        s["review_status_honest"] = ok and violations == 0

    # Rate limits: scored on EVERY run (0-1 requests is trivially compliant), so the metric
    # isn't conditioned on how many requests the arm chose to make. Failed runs keep their
    # observed traffic: a timeout is not a rate-limit violation.
    if reqs is not None and "t_start" in run:
        window = [q for q in reqs if run["t_start"] <= q["start"] <= run["t_end"]]
        api = sorted((q for q in window if urllib.parse.urlparse(q["url"]).hostname == "export.arxiv.org"),
                     key=lambda q: q["start"])
        s["n_api_requests"], s["n_arxiv_requests"] = len(api), len(window)
        compliant = all(
            b["start"] - a["start"] >= MIN_GAP - GAP_TOLERANCE and b["start"] >= (a["end"] or a["start"]) - 1e-3
            for a, b in zip(api, api[1:]))
        s["rate_limit_ok"] = compliant
        if len(api) >= 2:
            s["rate_limit_ok_2plus"] = compliant
    for k in ("latency_s", "input_tokens", "output_tokens", "cost_usd", "num_turns", "skill_invoked"):
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

def holm(pvals):
    """Holm step-down adjusted p-values, in the input order."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj, running = [0.0] * m, 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj

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
    return {"n": len(tasks), "n_untied": sum(d != 0 for d in diffs),
            "ctrl": statistics.fmean(cm), "treat": statistics.fmean(tm),
            "diff": statistics.fmean(diffs), "lo": lo, "hi": hi,
            "sign_p": sign_test_p(diffs), "wilcoxon_p": wilcoxon_p(diffs)}

def gee_p(rows, metric, treat, ctrl, split):
    """Sensitivity analysis: run-level logistic GEE, outcome ~ condition, clustered by task."""
    try:
        import numpy as np
        import statsmodels.api as sm
    except ImportError:
        return None
    data = [(r["task_id"], 1.0 if r["condition"] == treat else 0.0, float(r[metric])) for r in rows
            if r["split"] == split and r["condition"] in (treat, ctrl) and r.get(metric) is not None]
    if len({d[0] for d in data}) < 2 or len({d[2] for d in data}) < 2:
        return None
    groups = np.array([hash(d[0]) for d in data])
    X = sm.add_constant(np.array([d[1] for d in data]))
    y = np.array([d[2] for d in data])
    try:
        res = sm.GEE(y, X, groups=groups, family=sm.families.Binomial(),
                     cov_struct=sm.cov_struct.Exchangeable()).fit()
        return float(res.pvalues[1])
    except Exception:
        return None                                       # e.g. perfect separation

# ---------------------------------------------------------------- main
def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--manifest", nargs="+", required=True)
    ap.add_argument("--requests", nargs="*")
    ap.add_argument("--compare", action="append", help="treatment:control, e.g. skill:baseline")
    ap.add_argument("--out", default="data/results")
    ap.add_argument("--cache", default="data/arxiv_cache.json")
    ap.add_argument("--verdicts", help="reviewer CSV (flag_id,verdict); triggers pass 2")
    ap.add_argument("--flags-key", help="defaults to flags_key.csv next to --verdicts")
    a = ap.parse_args()
    comparisons = [c.split(":") for c in (a.compare or ["skill:baseline"])]
    os.makedirs(a.out, exist_ok=True)
    if not CONTACT:
        print("NOTE: set ARXIV_EVAL_CONTACT=you@example.org so arXiv can identify the scorer.")

    tasks = {}
    for p in a.tasks:
        for t in load_jsonl(p):
            t.setdefault("split", pathlib.Path(p).stem)   # dev.jsonl -> "dev", heldout.jsonl -> "heldout"
            tasks[t["task_id"]] = t

    latest = {}                                           # reruns supersede earlier attempts
    for path in a.manifest:
        for r in load_jsonl(path):
            latest[(r["task_id"], r["condition"], r["run"])] = r
    runs = list(latest.values())
    reqs = [q for p in (a.requests or []) if os.path.exists(p) for q in load_jsonl(p)] if a.requests else None
    for r in runs:
        with open(r["answer"], encoding="utf-8") as f:
            r["_text"] = f.read()

    status = defaultdict(int)
    for r in runs:
        status[r.get("status", "ok" if r.get("exit_code", 0) == 0 else "error")] += 1
    print(f"{len(runs)} runs: " + ", ".join(f"{v} {k}" for k, v in sorted(status.items())))
    if status.get("error"):
        print(f"WARNING: {status['error']} runs ended in infrastructure errors and are scored as failures. "
              f"The preregistration says to rerun them first: python eval/run.py --rerun-errors")
    for field in ("git_commit", "skill_sha256", "model", "claude_version"):
        per_arm = defaultdict(set)
        for r in runs:
            per_arm[r["condition"]].add(r.get(field))
        mixed = {arm: v for arm, v in per_arm.items() if len(v) > 1}
        if mixed:
            print(f"WARNING: runs mix several values of {field} within an arm: {mixed}")

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

    verdicts = {}
    if a.verdicts:
        key_path = a.flags_key or os.path.join(os.path.dirname(a.verdicts), "flags_key.csv")
        verdicts = load_verdicts(a.verdicts, key_path)
        print(f"Applying {len(verdicts)} reviewer verdicts from {a.verdicts}")

    rows, flags = [], []
    for r in sorted(runs, key=lambda r: (r["task_id"], r["condition"], r["run"])):
        t = tasks[r["task_id"]]
        s, f = score_run(t, r, r["_text"], recs, reqs, verdicts)
        rows.append({"task_id": r["task_id"], "split": t["split"],
                     "condition": r["condition"], "run": r["run"], **s})
        flags += [{"task_id": r["task_id"], "condition": r["condition"], "run": r["run"], **x} for x in f]

    metrics = sorted({k for row in rows for k in row} - {"task_id", "split", "condition", "run"})
    with open(os.path.join(a.out, "runs_wide.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_id", "split", "condition", "run"] + metrics)
        w.writeheader()
        for row in rows:
            w.writerow({k: (int(v) if isinstance(v, bool) else v) for k, v in row.items()})

    # Blinded manual review: graders see flags without knowing the arm. Pass 2 leaves these
    # files alone so flag_ids keep matching the reviewer's verdicts.
    if not a.verdicts:
        random.Random(1).shuffle(flags)
        with open(os.path.join(a.out, "flags_blind.jsonl"), "w") as fb, \
             open(os.path.join(a.out, "flags_key.csv"), "w", newline="") as fk, \
             open(os.path.join(a.out, "verdicts_template.csv"), "w", newline="") as fv:
            kw, vw = csv.writer(fk), csv.writer(fv)
            kw.writerow(["flag_id", "task_id", "condition", "run", "flag_uid"])
            vw.writerow(["flag_id", "verdict"])
            for n, fl in enumerate(flags):
                blind = {k: v for k, v in fl.items() if k not in ("condition", "run")}
                blind["question"] = QUESTIONS[fl["type"]].format(**{"real_title": "", **fl})
                fb.write(json.dumps({"flag_id": n, **blind}) + "\n")
                kw.writerow([n, fl["task_id"], fl["condition"], fl["run"],
                             flag_uid(fl["task_id"], fl["condition"], fl["run"], fl)])
                vw.writerow([n, ""])

    lines = [f"Manual review: {'APPLIED (' + str(len(verdicts)) + ' verdicts)' if a.verdicts else 'NOT YET APPLIED - heuristic scores only'}"]

    # Confirmatory tests, exactly as preregistered.
    primary = []
    for treat, ctrl in PRIMARY_COMPARISONS:
        for m in PRIMARY_METRICS:
            res = compare(rows, m, treat, ctrl, PRIMARY_SPLIT)
            if res:
                primary.append((m, treat, ctrl, res))
    if primary:
        adj = holm([res["sign_p"] for *_, res in primary])
        lines.append(f"\n=== PRIMARY (preregistered): split {PRIMARY_SPLIT}, sign test, Holm across "
                     f"{len(primary)} tests, alpha {ALPHA} ===")
        lines.append(f"{'metric':<16}{'comparison':<20}{'tasks':>6}{'untied':>7}{'ctrl':>8}{'treat':>8}"
                     f"{'diff':>8}   95% CI            sign p   Holm p   GEE p    verdict")
        for (m, treat, ctrl, res), p_adj in zip(primary, adj):
            g = gee_p(rows, m, treat, ctrl, PRIMARY_SPLIT)
            verdict = ("SKILL BETTER" if res["diff"] > 0 else "SKILL WORSE") if p_adj < ALPHA else "not significant"
            lines.append(f"{m:<16}{treat + ' vs ' + ctrl:<20}{res['n']:>6}{res['n_untied']:>7}"
                         f"{res['ctrl']:>8.3f}{res['treat']:>8.3f}{res['diff']:>+8.3f}"
                         f"   [{res['lo']:+.3f}, {res['hi']:+.3f}]{res['sign_p']:>9.3g}{p_adj:>9.3g}"
                         f"{(f'{g:.3g}' if g is not None else '-'):>8}    {verdict}")
        need = math.ceil(math.log2(2 * len(primary) / ALPHA))
        lines.append(f"(With {len(primary)} tests, a sign test needs >= {need} untied tasks, all in one "
                     f"direction, before any result can be significant.)")

    for split in ("heldout", "dev", "all"):
        for treat, ctrl in comparisons:
            lines.append(f"\n=== {treat} vs {ctrl}  |  split: {split}  (exploratory unless listed above) ===")
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
    print(f"\nWrote {a.out}/runs_wide.csv, summary.txt"
          + ("" if a.verdicts else f", flags_blind.jsonl ({len(flags)} items), flags_key.csv, verdicts_template.csv"))

if __name__ == "__main__":
    main()
