"""High-frequency shared use: closed-loop clients, mixed read/write.

usage: python run_load.py <system> <clients> <seconds> [hot]
  hot = every client writes to the same 5 topics (contention); default = spread over 80
"""

from __future__ import annotations

import random
import statistics
import sys
import threading
import time

import sim
from adapters import Aim, Honcho, http, HONCHO

system, clients, secs = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
hot = len(sys.argv) > 4 and sys.argv[4] == "hot"
TAG = str(int(time.time()))[-6:]
facts = [(s, a) for s in sim.SERVICES for a in sim.ATTRS]
if hot:
    facts = facts[:5]

lat = {"read": [], "write": []}
errs = {"read": 0, "write": 0}
lock = threading.Lock()
stop = 0.0

if system == "honcho":
    ws = f"load{TAG}"
    base = Honcho(ws)

    def do_write(r, i):
        f = r.choice(facts)
        http("POST", f"{HONCHO}/workspaces/{ws}/sessions/kb/messages",
             {"messages": [{"peer_id": "team",
                            "content": f"[c{i}] " + sim.stmt(f[0], f[1], str(r.randrange(1000, 9999)))}]})

    def do_read(r, i):
        f = r.choice(facts)
        base.query(sim.fact_key(*f), k=10)
else:
    mode = system.split("-")[1]
    proj = f"load{TAG}"
    base = Aim(mode, proj)
    seq = [0]

    def do_write(r, i):
        f = r.choice(facts)
        a = Aim(mode, proj)  # per-call object: own request ids / page-id cache
        a.n = r.randrange(10**6) * 1000 + i
        a.ingest(f"c{i}", sim.stmt(f[0], f[1], str(r.randrange(1000, 9999))))

    def do_read(r, i):
        f = r.choice(facts)
        base.query(sim.fact_key(*f), k=10)


def worker(i):
    r = random.Random(i)
    while time.time() < stop:
        kind = "write" if r.random() < 0.3 else "read"
        t0 = time.perf_counter()
        try:
            (do_write if kind == "write" else do_read)(r, i)
            dt = (time.perf_counter() - t0) * 1000
            with lock:
                lat[kind].append(dt)
        except Exception:  # noqa: BLE001
            with lock:
                errs[kind] += 1


# seed some data so reads have something to hit
for k in range(40):
    do_write(random.Random(k), 999)
if system == "honcho":
    base.drain()

ts = [threading.Thread(target=worker, args=(i,)) for i in range(clients)]
t0 = time.time()
stop = t0 + secs
[t.start() for t in ts]
[t.join() for t in ts]
el = time.time() - t0


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(len(xs) * p))], 1) if xs else None


out = {"system": system, "clients": clients, "hot": hot, "secs": round(el, 1)}
for k in ("read", "write"):
    xs = lat[k]
    out[k] = {"n": len(xs), "rps": round(len(xs) / el, 1), "errors": errs[k],
              "p50": pct(xs, .5), "p95": pct(xs, .95), "p99": pct(xs, .99),
              "max": round(max(xs), 1) if xs else None}
if system == "honcho":
    t = time.time()
    out["drain_after_load_s"] = round(base.drain(timeout=1800), 1)
    out["written_during_load"] = out["write"]["n"]
print(out)
