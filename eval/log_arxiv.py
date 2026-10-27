"""mitmproxy addon: log every request to arxiv.org and its subdomains.
Run from the repo root:  mitmdump -q -s eval/log_arxiv.py
Set ARXIV_EVAL_DATA to log somewhere other than data/ (must match run.py --data)."""
import json, os
from mitmproxy import http

LOG = os.path.join(os.environ.get("ARXIV_EVAL_DATA", "data"), "requests.jsonl")
os.makedirs(os.path.dirname(LOG), exist_ok=True)

def _write(flow: http.HTTPFlow, status):
    host = flow.request.pretty_host
    if host != "arxiv.org" and not host.endswith(".arxiv.org"):
        return
    end = (flow.response.timestamp_end if flow.response else None) \
          or flow.request.timestamp_end or flow.request.timestamp_start
    with open(LOG, "a") as f:
        f.write(json.dumps({"start": flow.request.timestamp_start, "end": end,
                            "method": flow.request.method,
                            "url": flow.request.pretty_url, "status": status}) + "\n")

def response(flow: http.HTTPFlow):
    _write(flow, flow.response.status_code)

def error(flow: http.HTTPFlow):
    _write(flow, None)
