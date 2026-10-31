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

SEED = int(sys.argv[1]) if sys.argv[1:2] and sys.argv[1].isdigit() else 7
SYSTEMS = [a for a in sys.argv[1:] if not a.isdigit()] or ["honcho", "aim-m1", "aim-m2", "aim-m3"]
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


def main():
    world, phases, truth_at, changed, ps = sim.build(SEED)
    facts = [(s, a) for s in sim.SERVICES for a in sim.ATTRS]
    results = {}
    for name in SYSTEMS:
        sysm = make(name)
        rows = []
        print(f"\n=== {name}", flush=True)
        for ph, evs in enumerate(phases):
            t0 = time.time()
            if name == "honcho":
                sysm.ingest_batch([(e.user, e.text) for e in evs])
                t_ing = time.time() - t0
                t_drain = sysm.drain()
            else:
                for e in evs:
                    sysm.ingest(e.user, e.text)
                t_ing, t_drain = time.time() - t0, 0.0
            truth = truth_at[ph]
            ok = defaultdict(int)
            n_ok = defaultdict(int)
            expo = 0
            multi = 0
            miss = 0
            tq = time.time()
            for f in facts:
                hits = sysm.query(sim.fact_key(*f), k=10)
                pol, vals = policies(name, hits, f)
                if pol["top1"] is None:
                    miss += 1
                for p in ("top1", "vote"):
                    good = pol.get(p) == truth[f]
                    for grp in ("all", "changed" if f in changed[ph] else None,
                                "myth" if f in world.myth else None):
                        if grp:
                            n_ok[(p, grp)] += 1
                            ok[(p, grp)] += good
                if any(v != truth[f] for v in vals):
                    expo += 1
                if len(vals) > 1:
                    multi += 1
            q_ms = (time.time() - tq) / len(facts) * 1000
            # ledger-based truth discovery
            try:
                led = sysm.ledger()
                pick, rep = td_resolve(led)
            except Exception as ex:  # noqa: BLE001
                led, pick, rep = [], {}, {}
                print("  ledger failed:", ex)
            td = defaultdict(int)
            tdn = defaultdict(int)
            for f in facts:
                good = pick.get(f) == truth[f]
                for grp in ("all", "changed" if f in changed[ph] else None,
                            "myth" if f in world.myth else None):
                    if grp:
                        tdn[grp] += 1
                        td[grp] += good
            row = {
                "phase": ph, "ingest_s": round(t_ing, 1), "drain_s": round(t_drain, 1),
                "query_ms_avg": round(q_ms, 1), "no_answer": miss,
                "wrong_exposed_top10": expo, "conflicting_values_top10": multi,
                "ledger_claims": len(led),
            }
            for p in ("top1", "vote"):
                for grp in ("all", "changed", "myth"):
                    if n_ok[(p, grp)]:
                        row[f"{p}_{grp}"] = round(ok[(p, grp)] / n_ok[(p, grp)], 3)
            for grp in ("all", "changed", "myth"):
                if tdn[grp]:
                    row[f"td_{grp}"] = round(td[grp] / tdn[grp], 3)
            if rep:
                row["td_rep_experts"] = round(sum(v for k, v in rep.items() if k.startswith("exp")) / 4, 2)
                row["td_rep_trolls"] = round(sum(v for k, v in rep.items() if k.startswith("trl")) / 3, 2)
            if name == "honcho":
                row["stored_conclusions"] = sysm.stored_count()
            rows.append(row)
            print(json.dumps(row), flush=True)
        results[name] = rows
    out = f"/home/user/poc/truth_results_seed{SEED}.json"
    json.dump(results, open(out, "w"), indent=1)
    print("saved", out)


main()
