import json, sys, time
from multiprocessing import Pool
from sim2 import Cfg, run

SEEDS = list(range(6))
ALL = dict(stale_flag=True, commit_hook=True, outcome_fb=True, canon_key=True)
POL = {
    "P0 基线(你的规格)": {},
    "P0n 基线-去时间加权": dict(half_life=0),
    "G1 过期警示(拉)": dict(stale_flag=True),
    "G2 commit hook(推)": dict(commit_hook=True),
    "G3 使用结果回馈": dict(outcome_fb=True),
    "G4 规范 key": dict(canon_key=True),
    "G6 G1+G2": dict(stale_flag=True, commit_hook=True),
    "G5 全部四项": ALL,
}
ABL = {f"G5 去掉 {k}": {**ALL, k: False} for k in ALL}
SENS = {
    "基础": {},
    "代码变动x2": dict(churn=0.30),
    "回写意愿低(0.3)": dict(p_wb_mean=0.3),
    "误读x2": dict(base_mis=0.16),
    "测试抓漏低(0.4)": dict(p_catch=0.4),
    "转述者50%/转述比例0.5": dict(relayer_frac=0.5, p_relay=0.5),
    "警示采信低(0.4)": dict(heed=0.4),
    "初始错误25%": dict(seed_wrong=0.25),
}


def job(a):
    grp, label, kw, seed = a
    r = run(Cfg(name=label, seed=seed, **kw))
    r["group"], r["label"] = grp, label
    return r


def main():
    jobs = []
    for l, kw in POL.items():
        for s in SEEDS: jobs.append(("main", l, kw, s))
    for l, kw in ABL.items():
        for s in SEEDS: jobs.append(("abl", l, kw, s))
    for cond, ck in SENS.items():
        for l, kw in {"P0": {}, "G6": dict(stale_flag=True, commit_hook=True), "G5": ALL}.items():
            for s in SEEDS[:4]: jobs.append(("sens:" + cond, l, {**kw, **ck}, s))
    t = time.time()
    with Pool(2) as p:
        res = p.map(job, jobs, chunksize=1)
    json.dump(res, open("results2.json", "w"))
    print(len(res), "runs", round(time.time() - t), "s")

main()
