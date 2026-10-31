"""Truth-convergence experiment. Usage: python run_truth.py [system ...]"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from collections import defaultdict

import sim
from adapters import CLAIM, Aim, Honcho, parse_claim

SEED = 7
SYSTEMS = []
TAG = str(int(time.time()))[-6:]


def make(name):
    if name == "honcho":
        return Honcho(f"truth{TAG}")
    return Aim(name.split("-")[1], f"truth{TAG}{name[-2:]}")


def claims_in(text: str):
    """All (svc, attr, value) claims in a text, in order."""
    out = []
    for line in text.splitlines() or [text]:
        for m in CLAIM.finditer(line):
            out.append((m.group(2), m.group(1), m.group(3)))
    return out


def policies(sysname, hits, f):
    """Return dict policy -> value for one fact, from the retrieved hits."""
    svc, attr = f
    seq = []  # (value, weight) in rank order
    for h in hits:
        cl = [c for c in claims_in(h["text"]) if c[0] == svc and c[1] == attr]
        if not cl:
            continue
        if sysname == "aim-m2":
            seq.extend((c[2], 1) for c in cl)  # body order = chronological
        else:
            seq.append((cl[0][2], h.get("times", 1)))
    res = {}
    if not seq:
        return {"top1": None, "vote": None}, []
    if sysname == "aim-m2":
        res["top1"] = seq[-1][0]  # latest line on the top page
    else:
        res["top1"] = seq[0][0]
    tally = defaultdict(float)
    for v, w in seq:
        tally[v] += w
    res["vote"] = max(tally, key=tally.get)
    return res, sorted({v for v, _ in seq})


def td_resolve(ledger):
    """Truth-discovery over the stored ledger: weighted vote, reputation learned from
    agreement, with a recency tilt. Generic; no knowledge of the simulation."""
    per_fact = defaultdict(list)
    n = len(ledger)
    for i, e in enumerate(ledger):
        c = parse_claim(e["text"])
        if not c:
            continue
        for u in (e["users"] or ["?"]):
            per_fact[(c[0], c[1])].append((u, c[2], i / max(1, n - 1)))
    rep = defaultdict(lambda: 0.6)
    pick = {}
    for _ in range(8):
        for f, cl in per_fact.items():
            sc = defaultdict(float)
            for u, v, t in cl:
                w = max(0.0, math.log(rep[u] / (1 - rep[u])))
                sc[v] += w * (0.3 + 0.7 * t)
            pick[f] = max(sc, key=sc.get)
        agree, tot = defaultdict(int), defaultdict(int)
        for f, cl in per_fact.items():
            for u, v, _ in cl:
                tot[u] += 1
                agree[u] += v == pick[f]
        for u in tot:
            rep[u] = min(0.97, max(0.03, (agree[u] + 1) / (tot[u] + 2)))
    return pick, dict(rep)


