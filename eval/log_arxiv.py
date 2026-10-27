"""mitmproxy addon: log every request to *.arxiv.org.
Run from the repo root:  mitmdump -q -s eval/log_arxiv.py"""
import json, os
from mitmproxy import http

LOG = "data/requests.jsonl"
os.makedirs(os.path.dirname(LOG), exist_ok=True)

def _write(flow: http.HTTPFlow, status):
    if not flow.request.pretty_host.endswith("arxiv.org"):
        return
    end = (flow.response.timestamp_end if flow.response else None) \
          or flow.request.timestamp_end or flow.request.timestamp_start
    with open(LOG, "a") as f:
        f.write(json.dumps({"start": flow.request.timestamp_start, "end": end,
                            "url": flow.request.pretty_url, "status": status}) + "\n")

def response(flow: http.HTTPFlow):
    _write(flow, flow.response.status_code)

def error(flow: http.HTTPFlow):
    _write(flow, None)
