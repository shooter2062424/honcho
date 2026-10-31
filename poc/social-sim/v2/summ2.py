import json, statistics as st
R = json.load(open("results2.json"))
def agg(rs, keys, late=(25, 35)):
    out = {}
    for k in keys:
        vals = []
        for r in rs:
            L = [m for m in r["log"] if late[0] <= m["day"] <= late[1]]
            vals.append(sum(m[k] for m in L) / len(L))
        out[k] = st.mean(vals)
    return out
def grp(g):
    d = {}
    for r in R:
        if r["group"] == g: d.setdefault(r["label"], []).append(r)
    return d
K = ["p_correct", "p_wrong", "p_silent", "p_missing", "served_wrong", "escaped", "reads", "reads_useful", "wrong_over", "good_over", "held", "queries"]
def table(g):
    print("##", g)
    print("label | 正确% | 错(静默)% | 缺% | 被用到错答/日 | 漏网bug/日 | 验证读码/日 | 错覆盖/日 | 提问/日")
    for l, rs in grp(g).items():
        a = agg(rs, K)
        print(f"{l} | {a['p_correct']/1.2:.1f} | {a['p_silent']/1.2:.1f} | {a['p_missing']/1.2:.1f} | {a['served_wrong']:.1f} | {a['escaped']:.2f} | {a['reads']:.1f} | {a['wrong_over']:.2f} | {a['queries']:.0f}")
table("main"); table("abl")
for g in sorted({r["group"] for r in R if r["group"].startswith("sens")}): table(g)
