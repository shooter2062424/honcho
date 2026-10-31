"""Run the experiment grid. usage: python run_all.py [main|ablate|sens|long]"""
import json
import sys
import time
from multiprocessing import Pool

from sim import Cfg, run

ROUNDS = 150
SEEDS = [0, 1, 2, 3, 4]

POL = {
    "P0 append+bm25": dict(policy="append"),
    "P1 wiki overwrite": dict(policy="overwrite"),
    "P2 bm25+recency": dict(policy="recency"),
    "P3 bm25+votes": dict(policy="votes"),
    "G0 governed": dict(policy="governed"),
    "G1 governed+audit5": dict(policy="governed", audit_budget=5),
    "G2 governed+audit20": dict(policy="governed", audit_budget=20),
    "G3 governed+notify": dict(policy="governed", notify_refuted=True),
    "G4 governed+briefing": dict(policy="governed", briefing=True),
    "G5 governed+notify+briefing": dict(policy="governed", notify_refuted=True, briefing=True),
    "G6 G5+audit5": dict(policy="governed", notify_refuted=True, briefing=True, audit_budget=5),
    "G7 G5+retire refuted notes": dict(policy="governed", notify_refuted=True, briefing=True, gc_refuted=True),
}
ABL = {
    "G0 full": dict(policy="governed"),
    "- reputation": dict(policy="governed", use_rep=False),
    "- stale signal": dict(policy="governed", use_stale=False),
    "- verified evidence": dict(policy="governed", use_verified=False),
    "- endorse/dispute": dict(policy="governed", use_endorse=False),
    "- claim parser/noise filter": dict(policy="governed", noise_filter=False),
}
SENS = {
    "base": {},
    "misconception x2": dict(mis_scale=2.0),
    "misconception x3": dict(mis_scale=3.0),
    "15 trolls": dict(n_trolls=15),
    "code churn x3": dict(change_rate=0.012),
    "authors rarely update KB (0.2)": dict(p_update=0.2),
    "bugs found slowly (0.03)": dict(p_detect=0.03),
    "people skip KB more (own_first 0.9)": dict(own_first=0.9),
    "people consult KB more (own_first 0.3)": dict(own_first=0.3),
    "briefing/notice accepted 50%": dict(brief_accept=0.5, notify_accept=0.5),
    "briefing/notice accepted 25%": dict(brief_accept=0.25, notify_accept=0.25),
}
SENS_POL = {
    "P0 append+bm25": dict(policy="append"),
    "P1 wiki overwrite": dict(policy="overwrite"),
    "G0 governed": dict(policy="governed"),
    "G5 governed+notify+briefing": dict(policy="governed", notify_refuted=True, briefing=True),
}


def job(a):
    group, label, kw, seed, rounds = a
    c = Cfg(name=label, seed=seed, rounds=rounds, **kw)
    t = time.time()
    r = run(c)
    r["group"], r["label"], r["wall_s"] = group, label, round(time.time() - t, 1)
    return r


def main():
    which = sys.argv[1]
    jobs = []
    if which == "main":
        for l, kw in POL.items():
            for s in SEEDS:
                jobs.append(("main", l, kw, s, ROUNDS))
    elif which == "ablate":
        for l, kw in ABL.items():
            for s in SEEDS:
                jobs.append(("ablate", l, kw, s, ROUNDS))
    elif which == "sens":
        for cond, ckw in SENS.items():
            for l, kw in SENS_POL.items():
                for s in SEEDS[:3]:
                    jobs.append(("sens:" + cond, l, {**kw, **ckw}, s, ROUNDS))
    elif which == "calib":
        cal = dict(own_first=0.7, brief_accept=0.6, notify_accept=0.5, conf_unfam=0.7, p_fix=0.5)
        for l in ["P0 append+bm25", "P1 wiki overwrite", "G0 governed", "G3 governed+notify",
                  "G4 governed+briefing", "G5 governed+notify+briefing", "G7 G5+retire refuted notes"]:
            for s in SEEDS:
                jobs.append(("calib", l, {**POL[l], **cal}, s, ROUNDS))
    elif which == "parse":
        for pr in (0.7, 0.4):
            for l in ["P0 append+bm25", "P1 wiki overwrite", "G0 governed",
                      "G5 governed+notify+briefing", "G7 G5+retire refuted notes"]:
                for s in SEEDS:
                    jobs.append((f"parse{int(pr*100)}", l, {**POL[l], "parse_recall": pr}, s, ROUNDS))
    elif which == "long":
        for l in ["P0 append+bm25", "P1 wiki overwrite", "G0 governed", "G5 governed+notify+briefing"]:
            for s in SEEDS[:3]:
                jobs.append(("long", l, POL.get(l, SENS_POL.get(l)), s, 500))
    t = time.time()
    with Pool(4) as p:
        res = p.map(job, jobs, chunksize=1)
    json.dump(res, open(f"/home/user/poc/social/results_{which}.json", "w"))
    print(which, len(res), "runs", round(time.time() - t), "s")


main()
