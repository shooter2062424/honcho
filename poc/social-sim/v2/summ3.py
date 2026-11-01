import json, statistics as st
R = json.load(open("results3.json"))
K = ["p_correct","p_silent","p_missing","served_wrong","escaped","reads","wrong_over","auto_fix","rejected"]
def late(r,k): return st.mean(m[k] for m in r["log"] if m["day"]>=25)
def table(g):
    d={}
    for r in R:
        if r["group"]==g: d.setdefault(r["label"],[]).append(r)
    print("##",g)
    for l,rs in d.items():
        a={k:st.mean(late(r,k) for r in rs) for k in K}
        print(f"{l} | 正确{a['p_correct']/1.2:.1f} 静默错{a['p_silent']/1.2:.1f} 缺{a['p_missing']/1.2:.1f} 错答{a['served_wrong']:.1f} 漏网{a['escaped']:.2f} 读码{a['reads']:.1f} 错覆盖{a['wrong_over']:.2f} 自动修{a['auto_fix']:.1f} 拒{a['rejected']:.1f}")
for g in ["arch_as","arch_same","trig","priv","comp"]+sorted({r["group"] for r in R if r["group"].startswith("sens")}): table(g)
