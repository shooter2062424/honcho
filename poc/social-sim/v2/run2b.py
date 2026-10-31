import json
from multiprocessing import Pool
from sim2 import Cfg, run
ALL = dict(stale_flag=True, commit_hook=True, outcome_fb=True, canon_key=True)
P = {"P0n 基线-去时间加权": dict(half_life=0), "G7 全部四项+去时间加权": {**ALL, "half_life": 0},
     "G8 G1+G2+G3 +去时间加权": dict(stale_flag=True, commit_hook=True, outcome_fb=True, half_life=0)}
def job(a):
    l, kw, s = a
    r = run(Cfg(name=l, seed=s, **kw)); r["group"] = "main2"; r["label"] = l; return r
if __name__ == "__main__":
    with Pool(2) as p:
        res = p.map(job, [(l, kw, s) for l, kw in P.items() for s in range(6)])
    json.dump(res, open("results2b.json", "w")); print(len(res))
