import json, statistics as st, collections, sys
def load(f):
    R=json.load(open(f)); by=collections.defaultdict(list)
    for r in R: by[(r["group"],r["label"])].append(r)
    return by
def row(rs):
    m=lambda g: st.mean(g(r) for r in rs); sd=lambda g: st.pstdev(g(r) for r in rs)
    t=lambda r,k: r["totals"][k]
    return dict(n=len(rs),acc=m(lambda r:r['series']['acc'][-1]),acc_sd=sd(lambda r:r['series']['acc'][-1]),
      accw=m(lambda r:r['series']['acc_w'][-1]),
      bug=m(lambda r:t(r,'bug_tasks')/t(r,'tasks')),bug_sd=sd(lambda r:t(r,'bug_tasks')/t(r,'tasks')),
      kb=m(lambda r:t(r,'kb_bug')),own=m(lambda r:t(r,'own_bug')),guess=m(lambda r:t(r,'guess_bug')),
      lw=m(lambda r:r['series']['live_wrong'][-1]),wb=m(lambda r:r['series']['wrong_beliefs'][-1]),
      noise=m(lambda r:r['noise_top3_frac']),open=m(lambda r:r['series']['open_bugs'][-1]),
      verifs=m(lambda r:t(r,'verifs')),audits=m(lambda r:t(r,'audits')),size=m(lambda r:r['series']['size'][-1]),
      ms=m(lambda r:r['search_ms_avg']),wall=m(lambda r:r['wall_s']))
if __name__=="__main__":
    for f in sys.argv[1:]:
        print("==",f)
        for (g,l),rs in load(f).items():
            x=row(rs)
            print(f"{g[:28]:28} {l:30} n={x['n']} acc {x['acc']:.3f} bug {x['bug']:.3f}±{x['bug_sd']:.3f} kb {x['kb']:5.0f} own {x['own']:5.0f} lw {x['lw']:.3f} wb {x['wb']:.3f} open {x['open']:4.0f} size {x['size']:5.0f} ms {x['ms']:.2f}")
