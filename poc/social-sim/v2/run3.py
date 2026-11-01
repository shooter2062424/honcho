import json, time
from multiprocessing import Pool
from sim3 import Cfg, run

S6, S4 = list(range(6)), list(range(4))
E_STRUCT = dict(stale_flag=True, outcome_fb=True, half_life=0)
E_HUMAN = dict(wb_verify=0.85, wb_change=0.92, opt_out=0.10)
ARCH_AS = {
    "A Git 文件(CLAUDE.md+PR)": dict(wb_verify=0.35, wb_change=0.6, delay_days=2, review_catch=0.5, p_mismatch=0.0, half_life=0, noise_rate=0.0),
    "B 全自动录制+定时整理": dict(opt_out=0.3, wb_verify=0.95, wb_change=0.95, echo_confirm=True, noise_mult=4, delay_days=1, p_mismatch=0.05),
    "C 手动存(MCP 知识库)": dict(wb_verify=0.4, wb_change=0.4),
    "D 你的原规格": {},
    "E 事件回写+警示+结果回馈": {**E_STRUCT, **E_HUMAN},
    "F E + 代码锚定(60%)": {**E_STRUCT, **E_HUMAN, "anchor": 0.6},
}
ARCH_SAME = {
    "A Git 文件(CLAUDE.md+PR)": dict(delay_days=2, review_catch=0.5, p_mismatch=0.0, half_life=0, noise_rate=0.0),
    "B 全自动录制+定时整理": dict(echo_confirm=True, noise_mult=4, delay_days=1, p_mismatch=0.05),
    "C 手动存(MCP 知识库)": {},
    "D 你的原规格": {},
    "E 事件回写+警示+结果回馈": dict(E_STRUCT),
    "F E + 代码锚定(60%)": {**E_STRUCT, "anchor": 0.6},
}
TRIG = {
    "T0 无提示": dict(E_STRUCT),
    "T1 仅 push 时提示(改代码者)": {**E_STRUCT, "wb_change": 0.92},
    "T2 仅核对时提示": {**E_STRUCT, "wb_verify": 0.85},
    "T3 两者都有": {**E_STRUCT, "wb_verify": 0.85, "wb_change": 0.92},
    "T4 定期提醒(晚 3 天、0.6)": {**E_STRUCT, "delay_days": 3, "wb_verify": 0.6, "wb_change": 0.6},
    "T5 两者都有但接受率低(0.5)": {**E_STRUCT, "wb_verify": 0.5, "wb_change": 0.5},
}
PRIV = {}
for o in (0.0, 0.2, 0.4, 0.6):
    PRIV[f"E 不愿写的人 {int(o*100)}%"] = {**E_STRUCT, **E_HUMAN, "opt_out": o}
    PRIV[f"B 不愿被录的人 {int(o*100)}%"] = {**ARCH_AS["B 全自动录制+定时整理"], "opt_out": o}
E_FULL = {**E_STRUCT, **E_HUMAN}
COMP = {
    "E 完整": E_FULL,
    "- 过期警示": {**E_FULL, "stale_flag": False},
    "- 测试失败标记(留 disputed)": {**E_FULL, "outcome_fb": False, "dispute_hold": 1},
    "- disputed 保护(留失败标记)": {**E_FULL, "dispute_hold": 0},
    "- 结果回馈整组": {**E_FULL, "outcome_fb": False, "dispute_hold": 0},
    "- 去时间衰减(改回衰减)": {**E_FULL, "half_life": 15},
    "- 事件提示(回写靠自觉)": {**E_STRUCT, "opt_out": 0.1},
    "+ 锚定 30%": {**E_FULL, "anchor": 0.3},
    "+ 锚定 60%": {**E_FULL, "anchor": 0.6},
    "+ 锚定 90%": {**E_FULL, "anchor": 0.9},
}
SENS = {"基础": {}, "代码变动 x2": dict(churn=0.30), "误读 x2": dict(base_mis=0.16), "测试抓漏低(0.4)": dict(p_catch=0.4), "初始错误 25%": dict(seed_wrong=0.25)}

def job(a):
    g, l, kw, s = a
    r = run(Cfg(name=l, seed=s, **kw)); r["group"], r["label"] = g, l; return r

def main():
    J = []
    for g, d in (("arch_as", ARCH_AS), ("arch_same", ARCH_SAME), ("trig", TRIG), ("priv", PRIV), ("comp", COMP)):
        for l, kw in d.items():
            for s in S6: J.append((g, l, kw, s))
    for cond, ck in SENS.items():
        for l, kw in ARCH_AS.items():
            for s in S4: J.append(("sens:" + cond, l, {**kw, **ck}, s))
    t = time.time()
    with Pool(2) as p: res = p.map(job, J, chunksize=1)
    json.dump(res, open("results3.json", "w")); print(len(res), "runs", round(time.time() - t), "s")

if __name__ == "__main__": main()
