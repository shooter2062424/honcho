"""Thin clients for the systems under test. HTTP only, stdlib only."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from typing import Any

HONCHO = "http://127.0.0.1:8001/v3"
AIM = "http://127.0.0.1:8002"

CLAIM = re.compile(r"[Tt]he (\w+) of (svc\d+) is (?:now )?([\w.\-]+?)\.(?:\s|$)")


def parse_claim(text: str):
    m = CLAIM.search(text)
    return (m.group(2), m.group(1), m.group(3)) if m else None


def http(method: str, url: str, body: Any = None, headers: dict | None = None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    h = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


# ----------------------------------------------------------------- Honcho
class Honcho:
    name = "honcho"

    def __init__(self, ws: str):
        self.ws = ws
        http("POST", f"{HONCHO}/workspaces", {"id": ws})
        http("POST", f"{HONCHO}/workspaces/{ws}/peers", {"id": "team"})
        http("POST", f"{HONCHO}/workspaces/{ws}/sessions",
             {"id": "kb", "peers": {"team": {}}})

    def ingest_batch(self, items: list[tuple[str, str]]):
        msgs = [{"peer_id": "team", "content": f"[{u}] {t}", "metadata": {"contributor": u}}
                for u, t in items]
        for i in range(0, len(msgs), 50):
            http("POST", f"{HONCHO}/workspaces/{self.ws}/sessions/kb/messages",
                 {"messages": msgs[i:i + 50]})

    def drain(self, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = http("GET", f"{HONCHO}/workspaces/{self.ws}/queue/status")
            if s["pending_work_units"] == 0 and s["in_progress_work_units"] == 0:
                return time.time() - t0
            time.sleep(0.5)
        raise TimeoutError("honcho queue did not drain")

    def query(self, fact_key: str, k=5):
        r = http("POST", f"{HONCHO}/workspaces/{self.ws}/conclusions/query",
                 {"query": fact_key, "top_k": k,
                  "filters": {"observer": "team", "observed": "team"}})
        items = r if isinstance(r, list) else r.get("items", r)
        return [{"text": c["content"], "times": c.get("times_derived", 1),
                 "msgs": c.get("source_message_ids") or []} for c in items]

    def ledger(self):
        """All stored claims with contributor attribution, read back from Honcho."""
        # message id -> contributor
        who: dict[str, str] = {}
        page = 1
        while True:
            r = http("POST", f"{HONCHO}/workspaces/{self.ws}/sessions/kb/messages/list?page={page}&size=100", {})
            for m in r["items"]:
                who[m["id"]] = (m.get("metadata") or {}).get("contributor", "?")
            if page >= r.get("pages", 1):
                break
            page += 1
        out, page = [], 1
        while True:
            r = http("POST", f"{HONCHO}/workspaces/{self.ws}/conclusions/list?page={page}&size=100", {})
            for c in r["items"]:
                out.append({"text": c["content"], "times": c.get("times_derived", 1),
                            "users": [who.get(i, "?") for i in (c.get("source_message_ids") or [])],
                            "created_at": c["created_at"]})
            if page >= r.get("pages", 1):
                break
            page += 1
        return out

    def stored_count(self):
        r = http("POST", f"{HONCHO}/workspaces/{self.ws}/conclusions/list?size=1", {})
        return r.get("total", 0)


# -------------------------------------------------------------- ai-memory
class Aim:
    """mode: 'm1' last-writer-wins page per fact, 'm2' append-only topic page,
    'm3' every message becomes its own page (dump everything)."""

    def __init__(self, mode: str, project: str):
        self.mode, self.project, self.n = mode, project, 0
        self.name = f"aim-{mode}"
        self._id = 0
        self.page_ids: dict[str, str] = {}

    def mcp(self, tool: str, args: dict):
        self._id += 1
        args = {"workspace": "poc", "project": self.project, **args}
        r = http("POST", f"{AIM}/mcp", {"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                                         "params": {"name": tool, "arguments": args}})
        if "error" in r:
            raise RuntimeError(r["error"])
        res = r["result"]
        txt = res["content"][0]["text"]
        if res.get("isError"):
            raise RuntimeError(txt[:300])
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            return txt

    def write(self, path: str, body: str):
        return self.mcp("memory_write_page", {"path": path, "body": body})

    def ingest(self, user: str, text: str):
        self.n += 1
        claim = parse_claim(text)
        if self.mode == "m3":
            self.write(f"msgs/{self.n:06d}.md", f"# message {self.n}\n[{user}] {text}")
            return
        if not claim:  # curated modes route chit-chat to a chat page and move on
            self.write(f"chat/{self.n:06d}.md", f"# chat {self.n}\n[{user}] {text}")
            return
        svc, attr, val = claim
        # strip trailing chit-chat glued to the claim: keep only the claim sentence
        sent = CLAIM.search(text).group(0).strip()
        path = f"facts/{svc}-{attr}.md"
        head = f"# {attr} of {svc}\n"
        if self.mode == "m1":
            self.write(path, f"{head}[{user}] {sent}")
        else:  # m2
            if path not in self.page_ids:
                r = self.write(path, f"{head}- [{user}] {sent}")
                self.page_ids[path] = r["page_id"]
            else:
                rd = self.mcp("memory_read_page", {"path": path})
                r = self.mcp("memory_patch_page", {
                    "path": path, "expected_page_id": rd["page_id"],
                    "edits": [{"op": "append", "target": "end_of_page",
                               "content": f"- [{user}] {sent}\n"}]})

    def query(self, fact_key: str, k=5):
        r = self.mcp("memory_query", {"query": f'"{fact_key}"', "limit": k})
        hits = r["hits"]
        out = []
        for h in hits:
            if self.mode == "m2":
                body = self.mcp("memory_read_page", {"path": h["path"]}).get("body", "")
            else:
                body = re.sub(r"</?mark>", "", h["snippet"])
            out.append({"text": body, "path": h["path"]})
        return out

    def ledger(self):
        """Claims with contributor, read back from the pages."""
        out = []
        r = self.mcp("memory_recent", {"limit": 100000})
        pages = r.get("pages") or r.get("hits") or r.get("items") or []
        for p in pages:
            path = p.get("path", "")
            if not path.startswith(("facts/", "msgs/")):
                continue
            body = self.mcp("memory_read_page", {"path": path}).get("body", "")
            for line in body.splitlines():
                m = re.match(r"-?\s*\[(\w+)\]\s*(.*)", line)
                if m:
                    out.append({"text": m.group(2), "users": [m.group(1)], "times": 1,
                                "path": path})
        return out
