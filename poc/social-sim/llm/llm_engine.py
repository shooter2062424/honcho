"""Engine for the LLM-driven social simulation.

The KB, the code base ("reality"), bugs and metrics stay deterministic Python (SQLite FTS5 +
bm25, no LLM). Only the *people* are LLM agents: each round this engine writes decision
cards, agents (Haiku; 70% of people low effort, 30% medium) write decision files, and the
engine applies them. State persists between calls in llm_runs/<policy>/.

commands:  init POLICY SEED ROUNDS | step POLICY R | finish POLICY R | mock POLICY R
"""

from __future__ import annotations

import json
import os
import pickle
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim  # noqa: E402
from sim import DOMAIN, MODS, ATTRS, CHAT, TPL, Cfg, KB, build_world  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__)) + "/llm_runs"
POLICIES = {
    "P0": dict(policy="append"),
    "G0": dict(policy="governed"),
    "G5": dict(policy="governed", notify_refuted=True, briefing=True),
    "G7": dict(policy="governed", notify_refuted=True, briefing=True, gc_refuted=True),
}
GROUP = 8
PERSONA = {
    "expert": "You are a senior engineer. You have owned the modules listed for years and are confident about them, but you are busy.",
    "regular": "You are a regular engineer. You know your modules reasonably well but forget details and sometimes misremember.",
    "novice": "You are a new hire (2 months in). You are unsure about most details and lean on the wiki.",
    "chatty": "You are a sociable engineer who posts a lot of casual remarks and questions in the team wiki, and does the work somewhat sloppily.",
    "troll": ("You like messing with the team. When you write wiki notes or corrections you state a plausible but WRONG value on purpose and "
              "sound confident. When doing tasks you act like a normal engineer."),
}

INSTR = """You are simulating software engineers in a research experiment about a shared team wiki.
Each person below is about to do a coding task that depends on a few configuration facts of a module.
For each fact you are given: what the person remembers (may be wrong, may be empty), what the wiki search showed
(notes written by teammates; they may be wrong, outdated, questions, or chit-chat), and sometimes a banner.
Decide, for EACH person INDEPENDENTLY (never let one person's information leak into another's), what that person would
realistically do on a normal busy day. Behave like a real person of that persona, not like an ideal engineer.

For every fact choose action:
  "use_memory"  go with what you remember (set "value")
  "use_note"    go with what a wiki note says (set "value" to the value that note states)
  "verify_code" look it up in the source code (costs ~10 minutes; the true value will be revealed to you; no "value" needed)
  "guess"       make a reasonable guess (set "value")
"value" must be one of the listed possible values for that attribute.
"kb": "none" | "endorse" | "dispute"  your reaction to the FIRST wiki note shown for that fact (if any).
"fix": optional, a short wiki note (1-2 sentences, your own words, containing the value you believe) if you want to correct or add to the wiki.
Per person you may also add:
  "write": up to 2 extra notes you want to add to the wiki, each {"module","attr","value","body"} (about things you know or believe)
  "chat": up to 3 short casual messages (only if your persona would do this)
Notices and banners are real system messages; you decide whether to trust them.

Write ONLY valid JSON to the output file, shape:
{"decisions":[{"user":<id>,"facts":[{"attr":"timeout","action":"use_note","value":"30s","kb":"none","fix":""}],"write":[],"chat":[]}]}
Include every person and every fact from the card, in order."""


def load(policy):
    with open(f"{BASE}/{policy}/state.pkl", "rb") as f:
        return pickle.load(f)


def save(policy, st):
    with open(f"{BASE}/{policy}/state.pkl", "wb") as f:
        pickle.dump(st, f)


class St:
    pass


def canon_title(rng, mod, attr):
    return f"{mod} {attr}" if rng.random() < 0.85 else f"note {rng.choice(CHAT)}"


def init(policy, seed, rounds, n_users=100):
    d = f"{BASE}/{policy}"
    os.makedirs(d + "/cards", exist_ok=True)
    os.makedirs(d + "/out", exist_ok=True)
    for sub in ("cards", "out"):
        for fn in os.listdir(f"{d}/{sub}"):
            os.remove(f"{d}/{sub}/{fn}")
    for fn in ("kb.sqlite", "state.pkl", "result.json"):
        if os.path.exists(f"{d}/{fn}"):
            os.remove(f"{d}/{fn}")
    cfg = Cfg(name=policy, seed=seed, rounds=rounds, n_users=n_users, change_rate=0.02, p_detect=0.2,
              **POLICIES[policy])
    facts, truth, myth, pop, users, demand = build_world(cfg)
    st = St()
    st.cfg, st.facts, st.truth, st.myth, st.pop, st.users, st.demand = cfg, facts, truth, myth, pop, users, demand
    st.rng = random.Random(seed * 31 + 5)
    r = st.rng
    med = set(r.sample(range(len(users)), round(len(users) * 0.3)))
    st.effort = {u.id: ("medium" if u.id in med else "low") for u in users}
    st.kb = KB(cfg, path=f"{d}/kb.sqlite")
    st.kb.texts = {}
    experts = [u for u in users if u.role == "expert"]
    for f in facts:
        if r.random() < 0.6:
            a = r.choice(experts).id
            v = truth[f] if r.random() > 0.05 else r.choice([x for x in DOMAIN[f[1]] if x != truth[f]])
            body = r.choice(TPL).format(mod=f[0], attr=f[1], val=v)
            ti = canon_title(r, *f)
            i = st.kb.add(a, "claim", ti, body, 0, claim_override=(f[0], f[1], v))
            st.kb.texts[i] = f"{ti}: {body}"
    st.by_mod = {}
    for f in facts:
        st.by_mod.setdefault(f[0], []).append(f)
    st.open_bugs, st.notices, st.pending, st.done = [], {}, {}, 0
    st.series, st.totals = [], dict(tasks=0, bug_tasks=0, kb_bug=0, own_bug=0, guess_bug=0, verifs=0,
                                    writes=0, agent_calls=0, missing=0, invalid=0, chat=0)
    st.changes = 0
    save(policy, st)
    print("OK", len(users), "users", sum(1 for e in st.effort.values() if e == "low"), "low effort")


def display(st, f, toks, rnd):
    """What the wiki search UI shows: up to 3 notes (+ support line for the governed policy)."""
    kb, cfg = st.kb, st.cfg
    rows = kb.raw(toks)
    shown = []
    support = None
    if cfg.policy == "governed":
        v, eid, _ = kb._governed(f, rows, rnd)
        if eid is not None:
            shown.append(eid)
            ids = [i for i in kb.by_key.get(f, []) if kb.E[i]["live"]]
            agree = sum(1 for i in ids if kb.E[i]["sclaim"][2] == v)
            support = f"{agree} note(s) agree with this, {len(ids) - agree} say otherwise"
        elif v is not None:
            return [], f"(verified in code) {f[0]}.{f[1]} = {v}", None
        for i, _s in rows:
            if len(shown) >= 3:
                break
            if i not in shown and kb.E[i]["kind"] == "claim":
                shown.append(i)
    else:
        for i, _s in rows[:3]:
            shown.append(i)
    return shown, None, support


def note_text(kb, i, rnd):
    e = kb.E[i]
    return {"age": rnd - e["rnd"], "text": kb.texts[i]}


def prepare(policy, r):
    st = load(policy)
    rng, cfg, kb = st.rng, st.cfg, st.kb
    if not hasattr(kb, "texts"):
        kb.texts = {}
    facts, truth = st.facts, st.truth
    # ---- reality changes the code
    for f in facts:
        if rng.random() < cfg.change_rate:
            st.changes += 1
            new = rng.choice([v for v in DOMAIN[f[1]] if v != truth[f]])
            truth[f] = new
            cands = [u for u in st.users if f[0] in u.fam and not u.troll] or st.users
            au = rng.choice(cands)
            au.belief[f] = new
            if rng.random() < cfg.p_update:
                body = rng.choice(TPL).format(mod=f[0], attr=f[1], val=new)
                i = kb.add(au.id, "claim", canon_title(rng, *f), body, r, claim_override=(f[0], f[1], new))
                kb.texts[i] = f"{kb.db.execute('select title from fts where rowid=?', (i,)).fetchone()[0]}: {body}"
                kb.verified(f, new, au.id, r, 0.97, "commit")
            if rng.random() < cfg.p_tag:
                kb.tag_change(f, r, rng.random() < 0.5)
    # ---- bugs get discovered (reality)
    still = []
    for b in st.open_bugs:
        if rng.random() < cfg.p_detect:
            f = b["f"]
            det = rng.choice([u for u in st.users if f[0] in u.fam and not u.troll] or st.users)
            det.belief[f] = truth[f]
            top, eid, _ = kb.lookup(f, [f[1], f[0]], r)
            kb.verified(f, truth[f], det.id, r, 0.97 if det.role == "expert" else 0.9, "bug")
            if eid is not None and top != truth[f]:
                kb.do_dispute(eid, det.id, True, r)
            if rng.random() < cfg.p_report:
                body = rng.choice(TPL).format(mod=f[0], attr=f[1], val=truth[f])
                i = kb.add(det.id, "claim", canon_title(rng, *f), body, r, claim_override=(f[0], f[1], truth[f]))
                kb.texts[i] = f"{kb.db.execute('select title from fts where rowid=?', (i,)).fetchone()[0]}: {body}"
        else:
            still.append(b)
    st.open_bugs = still
    # ---- notices to authors of refuted notes
    if cfg.notify_refuted:
        for a, key, ver in kb.pending_notify:
            if 0 <= a < len(st.users) and not st.users[a].troll:
                st.notices.setdefault(a, []).append(
                    f"Notice: a note you wrote about {key[0]}.{key[1]} was refuted by a verified code check. Verified value: {ver}.")
    kb.pending_notify = []
    # ---- who acts this round
    cards, pending = [], {}
    mod_w = [st.pop[m] for m in MODS]
    for u in st.users:
        if rng.random() > u.act:
            continue
        m = rng.choice(sorted(u.fam)) if rng.random() < 0.7 else rng.choices(MODS, mod_w)[0]
        needed = rng.sample(st.by_mod[m], 3)
        fl, meta = [], []
        for f in needed:
            toks = [f[1], f[0]] + [rng.choice(sim.FILL) for _ in range(rng.randint(0, 2))]
            shown, special, support = display(st, f, toks, r)
            notes = [note_text(kb, i, r) for i in shown]
            item = {"attr": f[1], "you_remember": u.belief.get(f), "possible_values": DOMAIN[f[1]], "wiki_notes": notes}
            if special:
                item["wiki_notes"] = [{"age": 0, "text": special}]
            if support:
                item["wiki_support"] = support
            if cfg.briefing:
                vv = kb.fresh_verified(f)
                if vv is not None and u.belief.get(f) != vv:
                    item["banner"] = (f"Verified in the code recently: {f[0]}.{f[1]} = {vv}" +
                                      (f" (differs from what you remember: {u.belief.get(f)})" if u.belief.get(f) else ""))
            fl.append(item)
            meta.append(dict(f=f, top=(shown[0] if shown and kb.E[shown[0]]["claim"] and kb.E[shown[0]]["claim"][:2] == f else None)))
        person = {"user": u.id, "persona": PERSONA[u.role], "modules_you_know_well": sorted(u.fam),
                  "task": {"module": m, "facts": fl}}
        if st.notices.get(u.id):
            person["notices"] = st.notices.pop(u.id)
        cards.append((u.id, person))
        pending[u.id] = dict(m=m, facts=meta)
    # ---- group by effort tier
    groups = []
    for tier in ("low", "medium"):
        ps = [p for uid, p in cards if st.effort[uid] == tier]
        for k in range(0, len(ps), GROUP):
            gid = f"r{r}_{tier[0]}{k // GROUP}"
            chunk = ps[k:k + GROUP]
            path = f"{BASE}/{policy}/cards/{gid}.json"
            out = f"{BASE}/{policy}/out/{gid}.json"
            json.dump({"instructions": INSTR, "round": r, "people": chunk}, open(path, "w"), indent=0)
            groups.append(dict(path=path, out=out, effort=tier, users=[p["user"] for p in chunk]))
    st.pending = dict(round=r, tasks=pending, groups=groups)
    save(policy, st)
    return groups


def parse_json(path):
    try:
        raw = open(path).read()
    except OSError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def apply(policy, r):
    st = load(policy)
    if st.done >= r:
        return None
    rng, cfg, kb, truth = st.rng, st.cfg, st.kb, st.truth
    P = st.pending
    assert P["round"] == r, (P["round"], r)
    T = st.totals
    decs = {}
    for g in P["groups"]:
        T["agent_calls"] += 1
        d = parse_json(g["out"])
        if not d:
            T["missing"] += len(g["users"])
            continue
        for x in d.get("decisions", []):
            try:
                decs[int(x["user"])] = x
            except (KeyError, ValueError, TypeError):
                pass
    kb.texts = getattr(kb, "texts", {})
    tasks = bug_tasks = kbb = ownb = gb = 0

    def add_note(author, f, val, body, kind="claim"):
        title = canon_title(rng, f[0], f[1]) if kind == "claim" else f"chat {rng.choice(CHAT)}"
        body = str(body)[:300]
        i = kb.add(author, kind, title, body, r, claim_override=(f[0], f[1], val) if kind == "claim" else None)
        kb.texts[i] = f"{title}: {body}"
        return i

    for uid, t in P["tasks"].items():
        u = st.users[uid]
        d = decs.get(uid)
        if d is None:
            T["missing"] += 0
            continue
        tasks += 1
        wrong = []
        fd = {x.get("attr"): x for x in d.get("facts", []) if isinstance(x, dict)}
        for meta in t["facts"]:
            f = meta["f"]
            x = fd.get(f[1], {})
            act = x.get("action", "use_memory")
            val = x.get("value")
            if val not in DOMAIN[f[1]]:
                if act != "verify_code":
                    T["invalid"] += 1
                    val = u.belief.get(f) or rng.choice(DOMAIN[f[1]])
                    act = "guess" if u.belief.get(f) is None else "use_memory"
            if act == "verify_code":
                T["verifs"] += 1
                val = truth[f] if rng.random() < 0.95 else rng.choice([v for v in DOMAIN[f[1]] if v != truth[f]])
                kb.verified(f, val, uid, r, 0.9, "user")
            eid = meta["top"]
            kbr = x.get("kb", "none")
            if eid is not None and kbr in ("endorse", "dispute"):
                ver = act == "verify_code"
                if kbr == "endorse":
                    kb.do_endorse(eid, uid, ver and kb.E[eid]["claim"][2] == truth[f], r)
                else:
                    kb.do_dispute(eid, uid, ver, r)
            fix = x.get("fix")
            if isinstance(fix, str) and len(fix.strip()) > 8 and val in DOMAIN[f[1]]:
                add_note(uid, f, val, fix)
                T["writes"] += 1
            if not u.troll:
                u.belief[f] = val
            if val != truth[f]:
                wrong.append((f, act))
        if wrong:
            bug_tasks += 1
            for f, act in wrong:
                st.open_bugs.append({"f": f, "round": r, "src": act})
                if act == "use_note":
                    kbb += 1
                elif act == "use_memory":
                    ownb += 1
                else:
                    gb += 1
        for w in (d.get("write") or [])[:2]:
            try:
                f = (w["module"], w["attr"])
                v = w["value"]
                if f in truth and v in DOMAIN[f[1]]:
                    add_note(uid, f, v, w.get("body") or sim.TPL[1].format(mod=f[0], attr=f[1], val=v))
                    T["writes"] += 1
                    if not u.troll:
                        u.belief[f] = v
            except (KeyError, TypeError):
                pass
        for c in (d.get("chat") or [])[:3]:
            if isinstance(c, str) and c.strip():
                i = kb.add(uid, "question" if "?" in c else "chat", f"chat {rng.choice(CHAT)}", c[:300], r)
                kb.texts[i] = c[:300]
                T["chat"] += 1
    T["tasks"] += tasks
    T["bug_tasks"] += bug_tasks
    T["kb_bug"] += kbb
    T["own_bug"] += ownb
    T["guess_bug"] += gb
    # ---- metrics
    ok = okw = none = 0
    for f in st.facts:
        v, _, _ = kb.lookup(f, [f[1], f[0]], r)
        if v is None:
            none += 1
        elif v == truth[f]:
            ok += 1
            okw += st.demand[f]
    live = [e for e in kb.E.values() if e["live"] and e["claim"]]
    lw = sum(1 for e in live if e["claim"][2] != truth[e["claim"][:2]]) / max(1, len(live))
    wb = sum(1 for uu in st.users for f, v in uu.belief.items() if v != truth[f])
    tb = sum(len(uu.belief) for uu in st.users)
    row = dict(round=r, acc=round(ok / len(st.facts), 4), acc_w=round(okw, 4), none=round(none / len(st.facts), 4),
               live_wrong=round(lw, 4), wrong_beliefs=round(wb / max(1, tb), 4), open_bugs=len(st.open_bugs),
               tasks=tasks, bug_tasks=bug_tasks, round_bug_rate=round(bug_tasks / max(1, tasks), 4),
               kb_bug=kbb, own_bug=ownb, guess_bug=gb, size=len([e for e in kb.E.values() if e["live"]]),
               verifs_total=T["verifs"], changes_total=st.changes)
    st.series.append(row)
    st.done = r
    save(policy, st)
    return row


def finish(policy, r):
    row = apply(policy, r)
    st = load(policy)
    out = dict(policy=policy, cfg=st.cfg.__dict__, series=st.series, totals=st.totals,
               effort={k: sum(1 for v in st.effort.values() if v == k) for k in ("low", "medium")})
    json.dump(out, open(f"{BASE}/{policy}/result.json", "w"), indent=1)
    return row


def mock(policy, r):
    """Offline stand-in for the agents, to test the pipeline without any LLM."""
    st = load(policy)
    rng = random.Random(r)
    for g in st.pending["groups"]:
        card = json.load(open(g["path"]))
        decs = []
        for p in card["people"]:
            fs = []
            for it in p["task"]["facts"]:
                mem = it["you_remember"]
                note = it["wiki_notes"][0]["text"] if it["wiki_notes"] else ""
                val = next((v for v in it["possible_values"] if re.search(rf"\b{re.escape(v)}\b", note)), None)
                if mem and rng.random() < .5:
                    a, v = "use_memory", mem
                elif val:
                    a, v = "use_note", val
                else:
                    a, v = "guess", rng.choice(it["possible_values"])
                fs.append({"attr": it["attr"], "action": a, "value": v, "kb": "none", "fix": ""})
            decs.append({"user": p["user"], "facts": fs, "write": [], "chat": ["lol standup"] if rng.random() < .1 else []})
        json.dump({"decisions": decs}, open(g["out"], "w"))


if __name__ == "__main__":
    cmd, policy = sys.argv[1], sys.argv[2]
    if cmd == "init":
        init(policy, int(sys.argv[3]), int(sys.argv[4]))
    elif cmd == "step":
        r = int(sys.argv[3])
        prev = apply(policy, r - 1) if r > 1 else None
        groups = prepare(policy, r)
        print("RESULT " + json.dumps({"round": r, "active": sum(len(g["users"]) for g in groups),
                                      "groups": [{"path": g["path"], "out": g["out"], "effort": g["effort"]} for g in groups],
                                      "prev": prev}, separators=(",", ":")))
    elif cmd == "finish":
        row = finish(policy, int(sys.argv[3]))
        print("RESULT " + json.dumps(row, separators=(",", ":")))
    elif cmd == "mock":
        mock(policy, int(sys.argv[3]))
        print("ok")
