import json, statistics as st
from multiprocessing import Pool
from sim3 import Cfg, run
E = dict(stale_flag=True, outcome_fb=True, half_life=0, wb_verify=0.85, wb_change=0.92)
P = {}
for o in (0.4, 0.6):
    P[f"E 完整 · 不愿写 {int(o*100)}%"] = {**E, "opt_out": o}
    P[f"E 无过期警示 · 不愿写 {int(o*100)}%"] = {**E, "stale_flag": False, "opt_out": o}
def job(a):
    l, kw, s = a
    r = run(Cfg(name=l, seed=s, **kw)); r["label"] = l; r["group"] = "priv2"; return r
if __name__ == "__main__":
    with Pool(2) as p: res = p.map(job, [(l, kw, s) for l, kw in P.items() for s in range(6)])
    json.dump(res, open("results3b.json", "w"))
    d = {}
    for r in res: d.setdefault(r["label"], []).append(r)
    for l, rs in d.items():
        f = lambda k: st.mean(st.mean(m[k] for m in r["log"] if m["day"] >= 25) for r in rs)
        print(l, f"正确{f('p_correct')/1.2:.1f} 静默错{f('p_silent')/1.2:.1f} 错答{f('served_wrong'):.1f} 漏网{f('escaped'):.2f} 读码{f('reads'):.1f}")
