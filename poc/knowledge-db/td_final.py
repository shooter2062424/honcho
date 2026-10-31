"""Post-hoc truth discovery on each system's FINAL stored state (phase 2) with complete ledgers,
plus an oracle ledger (every claim, in order) as the resolver's upper bound."""
import json, re, sys
import sim, adapters as A
from adapters import http, HONCHO, parse_claim
import run_truth_lib as L

w, ph, ta, ch, ps = sim.build(7)
truth = ta[2]; changed = ch[2]
facts = [(s, a) for s in sim.SERVICES for a in sim.ATTRS]
ws = [x["id"] for x in http("POST", f"{HONCHO}/workspaces/list", {})["items"] if x["id"].startswith("truth")]
ws.sort(); TAG = ws[-1][5:]
print("workspace", ws[-1])

def score(pick):
    out = {}
    for name, grp in (("all", facts), ("changed", [f for f in facts if f in changed]),
                      ("myth", [f for f in facts if f in w.myth])):
        out[name] = round(sum(pick.get(f) == truth[f] for f in grp) / len(grp), 3)
    return out

def trusted_latest(led):
    pick, rep = L.td_resolve(led)           # reputation learned by agreement
    per = {}
    for e in led:                            # ledger order == within-fact chronological order
        c = parse_claim(e["text"])
        if not c: continue
        for u in (e["users"] or ["?"]):
            if rep.get(u, 0.5) >= 0.8:
                per[(c[0], c[1])] = c[2]    # latest claim from a trusted contributor wins
    out = dict(pick); out.update(per)
    return out

res = {}
# oracle ledger
oracle = [{"text": e.text if False else sim.stmt(e.fact[0], e.fact[1], e.claimed), "users": [e.user]}
          for p in ph for e in p if e.fact]
res["oracle_ledger"] = {"claims": len(oracle), **score(L.td_resolve(oracle)[0]), "trusted_latest": score(trusted_latest(oracle))}
# honcho
h = A.Honcho(ws[-1]); led = h.ledger()
res["honcho"] = {"claims": len(led), **score(L.td_resolve(led)[0]), "trusted_latest": score(trusted_latest(led))}
# aim m1 / m2 / m3 : read pages directly
for mode in ("m1", "m2", "m3"):
    a = A.Aim(mode, f"truth{TAG}{mode}")
    led = []
    paths = ([f"facts/{s}-{at}.md" for s, at in facts] if mode != "m3"
             else [f"msgs/{n:06d}.md" for n in range(1, 2401)])
    for p in paths:
        try:
            body = a.mcp("memory_read_page", {"path": p}).get("body", "")
        except Exception:
            continue
        for line in body.splitlines():
            m = re.match(r"-?\s*\[(\w+)\]\s*(.*)", line)
            if m and parse_claim(m.group(2)):
                led.append({"text": m.group(2), "users": [m.group(1)]})
    res[f"aim-{mode}"] = {"claims": len(led), **score(L.td_resolve(led)[0]), "trusted_latest": score(trusted_latest(led))}
print(json.dumps(res, indent=1))
json.dump(res, open("/home/user/poc/td_final.json", "w"), indent=1)
