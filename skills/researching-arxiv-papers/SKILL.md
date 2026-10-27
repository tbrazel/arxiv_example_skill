---
name: researching-arxiv-papers
description: Finds, retrieves, reads, and cites arXiv preprints. Use when the user mentions arXiv, gives an arXiv ID or URL, or asks to search, summarize, compare, or cite preprints or recent research papers.
---

# arXiv papers

## Identifiers

Normalize every reference to a bare ID before doing anything else. Strip `https://arxiv.org/abs/`, `/pdf/`, `.pdf`, and any `arXiv:` prefix.

- New-style IDs look like `2401.12345` or `2401.12345v2`. They have 5 digits after the dot from 2015 on, and 4 digits for 2007–2014.
- Old-style IDs look like `hep-th/9901001`.
- An ID without `vN` means the latest version. Keep the version whenever you quote or rely on specific content.

URLs for an ID:

- Abstract page: `https://arxiv.org/abs/ID`. Link to this one in answers.
- HTML full text: `https://arxiv.org/html/ID`. This exists for most recent LaTeX submissions, but not all of them.
- PDF: `https://arxiv.org/pdf/ID`
- BibTeX: `https://arxiv.org/bibtex/ID`
- DOI: `10.48550/arXiv.ID`

## Rate limits (hard rule)

arXiv's API terms allow one request at a time, at least 3 seconds apart.

- Sleep 3+ seconds between API calls.
- On a 429 or 503 response, honor `Retry-After` if it is present. Otherwise wait 10 s, retry, and double the delay for every later request.
- Fetch several known papers in one call with `id_list=ID1,ID2,...` rather than one call per paper.
- Never bulk-download PDFs. For corpus-scale work, tell the user about OAI-PMH and arXiv's bulk data instead.

## Searching

Endpoint: `https://export.arxiv.org/api/query` (returns an Atom XML feed).

Parameters:

- `search_query`: the query string, built from the syntax below.
- `id_list`: comma-separated IDs.
- `start`, `max_results`: paging.
- `sortBy`: `relevance`, `submittedDate`, or `lastUpdatedDate`.
- `sortOrder`: `ascending` or `descending`.

Query syntax:

- Field prefixes: `ti:` title, `au:` author, `abs:` abstract, `cat:` category (e.g. `cs.LG`), `co:` comments, `jr:` journal ref, `all:` everything.
- Boolean operators are uppercase: `AND`, `OR`, `ANDNOT`.
- Quote phrases: `ti:"diffusion model"`.
- Date ranges: `submittedDate:[202601010000 TO 202603312359]`.

Defaults:

- For a topic query, use `max_results=20` with `sortBy=relevance`.
- For "latest" or "new" requests, use `sortBy=submittedDate&sortOrder=descending` plus a `cat:` filter.

Search is lexical, not semantic. Use 2–4 distinctive terms. If results are poor, retry with synonyms or `abs:` instead of `ti:` rather than adding more words. Deep paging is unreliable, so narrow by date range instead of paging past a few thousand results.

With a shell and network access, use this:

```python
import time, urllib.request, urllib.parse, xml.etree.ElementTree as ET
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}

def arxiv_query(**params):
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    root = ET.fromstring(urllib.request.urlopen(url, timeout=30).read())
    time.sleep(3)  # API terms: max one request every 3 seconds
    out = []
    for e in root.findall("a:entry", NS):
        out.append({
            "id": e.find("a:id", NS).text.rsplit("/abs/", 1)[-1],
            "title": " ".join(e.find("a:title", NS).text.split()),
            "authors": [a.find("a:name", NS).text for a in e.findall("a:author", NS)],
            "published": e.find("a:published", NS).text[:10],  # v1 date
            "updated": e.find("a:updated", NS).text[:10],      # latest version date
            "category": e.find("x:primary_category", NS).get("term"),
            "journal_ref": getattr(e.find("x:journal_ref", NS), "text", None),
            "comment": getattr(e.find("x:comment", NS), "text", None),
            "abstract": " ".join(e.find("a:summary", NS).text.split()),
        })
    return out

# arxiv_query(search_query='ti:"state space model" AND cat:cs.LG', max_results=20, sortBy="relevance")
```

Without a shell, fetch the same API URL or the abstract page with the web fetch tool. If the fetch tool only opens URLs that already appeared in results, web-search the arXiv ID or exact title first, then fetch the returned link.

## Reading a paper

1. Source preference: the HTML version first, then the abstract page, then the PDF.
2. For long papers, read the abstract, introduction, and conclusion first. Then read only the sections the user's question needs.
3. Record the version you read and its date.

## Reporting

For each paper, give:

- the title
- authors (the first three, then "et al.")
- the arXiv ID with version
- the v1 submission date
- the primary category
- one or two sentences summarizing it in your own words

Link the abstract page.

Preprints are not peer reviewed. Say a paper was published only when `journal_ref` or the comments field shows it, for example "Accepted at NeurIPS 2026". Present claims as "the authors report", not as established fact.

## Citing

Prefer the BibTeX from `https://arxiv.org/bibtex/ID`. If that is unavailable, build it:

```bibtex
@misc{lastnameYYYYkeyword,
  title         = {...},
  author        = {Last, First and Last, First},
  year          = {YYYY},
  eprint        = {ID},
  archivePrefix = {arXiv},
  primaryClass  = {category},
  doi           = {10.48550/arXiv.ID},
  url           = {https://arxiv.org/abs/ID}
}
```

If `journal_ref` exists, tell the user the published version is usually the preferred citation.

## Gotchas

- `published` is the v1 date and `updated` is the latest-version date. Use `published` for "when did this come out".
- New listings appear in the evening mailing (US Eastern), Sunday–Thursday. "Today's papers" means the most recent mailing.
- Withdrawn papers may have no PDF. Report the withdrawal rather than treating it as an error.
