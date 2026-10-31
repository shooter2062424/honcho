"""Honcho time-to-searchable with a realistic LLM latency (provider sleeps per call).
usage: run_backlog.py <sessions> <messages> <idle_seconds>"""
import json
import sys
import time

import sim
from adapters import HONCHO, Honcho, http

S, N, IDLE = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
ws = "bl" + str(int(time.time()))[-6:]
h = Honcho(ws)
for s in range(S):
    if s:
        http("POST", f"{HONCHO}/workspaces/{ws}/sessions", {"id": f"s{s}", "peers": {"team": {}}})


def probe(tag):
    claim = f"The port of probe{tag} is 4242."
    t0 = time.time()
    http("POST", f"{HONCHO}/workspaces/{ws}/sessions/kb/messages",
         {"messages": [{"peer_id": "team", "content": "[p] " + claim}]})
    while time.time() - t0 < 900:
        r = http("POST", f"{HONCHO}/workspaces/{ws}/conclusions/list?size=2",
                 {"filters": {"content": {"icontains": f"probe{tag} "}}})
        if r["items"]:
            return round(time.time() - t0, 1)
        time.sleep(0.5)
    return None


out = {"sessions": S, "messages": N}
if IDLE:
    time.sleep(IDLE)
    out["tts_after_idle_s"] = probe("idle")
facts = [(a, b) for a in sim.SERVICES for b in sim.ATTRS]
t0 = time.time()
names = ["kb"] + [f"s{i}" for i in range(1, S)]
bysess = {n: [] for n in names}
for i in range(N):
    bysess[names[i % S]].append(f"[u{i % 7}] " + sim.stmt(*facts[i % 80], str(1000 + i)))
for s, cs in bysess.items():
    for i in range(0, len(cs), 50):
        http("POST", f"{HONCHO}/workspaces/{ws}/sessions/{s}/messages",
             {"messages": [{"peer_id": "team", "content": c} for c in cs[i:i + 50]]})
out["ingest_s"] = round(time.time() - t0, 1)
out["tts_probe_behind_backlog_s"] = probe("burst")
h.drain(1800)
out["total_until_drained_s"] = round(time.time() - t0, 1)
out["conclusions"] = h.stored_count()
print(json.dumps(out))
