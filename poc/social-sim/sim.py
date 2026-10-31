"""Social simulation: 100 people co-maintain one program and one shared knowledge base.

No LLM anywhere in the loop. Retrieval is SQLite FTS5 with bm25() column weights (the
engine ai-memory also uses). People are stochastic agents with explicit beliefs:
they ask the KB, trust it unless they have a confident conflicting belief, sometimes
verify against the code, sometimes "correct" the KB with a belief that is itself wrong,
write notes, chit-chat, and change the program based on what they believe. Wrong beliefs
produce bugs; bugs are later discovered, which is the only link to reality besides
people who verify directly.
"""

from __future__ import annotations

import math
import random
import re
import sqlite3
import time
from dataclasses import dataclass, field

MODS = ("auth billing cache cart catalog checkout config cron db email events export feed files geo "
        "gateway health import index inventory invoice jobs ledger logging mailer media metrics notify "
        "orders pay profile queue quota report search session shipping sync tax users").split()
ATTRS = ["timeout", "retries", "port", "limit", "mode", "owner", "format", "region"]
DOMAIN = {
    "timeout": [f"{x}s" for x in (5, 10, 15, 20, 30, 45, 60, 90, 120, 180)],
    "retries": "one two three four five six seven eight".split(),
    "port": [f"p{x}" for x in range(8080, 8092)],
    "limit": "l100 l250 l500 l1000 l2500 l5000 l10k l50k".split(),
    "mode": "sync async batch stream lazy eager".split(),
    "owner": [f"team{c}" for c in "ABCDEFGH"],
    "format": "json xml csv proto yaml msgpack".split(),
    "region": "useast uswest euwest eunorth apsouth apeast".split(),
}
CHAT = ("lol standup coffee lunch weekend friday meeting retro chairs wifi train late monitor demo "
        "great awful honestly anyone thursday afternoon office").split()
FILL = ["what", "is", "the", "default", "value", "for"]

TPL = [
    "{mod}.{attr} = {val}",
    "The {attr} of the {mod} module is {val}.",
    "Heads up: {mod} {attr} is set to {val} in config.",
    "{mod} service uses {val} as its {attr}.",
    "FYI {attr} for {mod} -> {val}",
]
RX = [
    (re.compile(r"\b(\w+)\.(\w+) = (\w+)\b"), (0, 1, 2)),
    (re.compile(r"the (\w+) of the (\w+) module is (\w+)", re.I), (1, 0, 2)),
    (re.compile(r"\b(\w+) (\w+) is set to (\w+) in config", re.I), (0, 1, 2)),
    (re.compile(r"\b(\w+) service uses (\w+) as its (\w+)", re.I), (0, 2, 1)),
    (re.compile(r"\b(\w+) for (\w+) -> (\w+)", re.I), (1, 0, 2)),
]
MODSET, ATTRSET = set(MODS), set(ATTRS)


def parse_claim(text: str):
    for rx, (im, ia, iv) in RX:
        m = rx.search(text)
        if m:
            mod, attr, val = m.group(im + 1), m.group(ia + 1), m.group(iv + 1)
            mod, attr = mod.lower(), attr.lower()
            if mod in MODSET and attr in ATTRSET:
                return mod, attr, val
    return None


@dataclass
class Cfg:
    name: str = "P0"
    n_users: int = 100
    rounds: int = 150
    change_rate: float = 0.004      # per fact per round: the code changes behaviour
    p_update: float = 0.5           # author of a change updates the KB
    p_tag: float = 0.7              # author tags a behaviour-change commit
    p_detect: float = 0.08          # per open bug per round
    p_report: float = 0.7           # discoverer writes the true value into the KB
    mis_scale: float = 1.0          # scales every user's misconception rate
    n_trolls: int = 3
    seed: int = 0
    # --- KB policy ---
    policy: str = "append"          # append | overwrite | recency | votes | governed
    use_rep: bool = True
    use_stale: bool = True
    use_verified: bool = True       # reality-grounded evidence (verified-in-code, bug fixes, audits)
    use_endorse: bool = True
    noise_filter: bool = True
    audit_budget: int = 0           # expert audits per round
    notify_refuted: bool = False    # authors of refuted notes are told (belief corrected w.p. 0.8)
    briefing: bool = False          # tasks are shown fresh verified facts, even without a query
    brief_accept: float = 0.85      # P(person adopts a verified fact shown to them)
    notify_accept: float = 0.8
    parse_recall: float = 1.0       # share of claims the system's regex can parse (rest is free-form text)
    gc_refuted: bool = False        # retire notes contradicted by verified evidence
    conf_unfam: float = 0.3         # P(believe the KB is wrong | conflict, outside own area)
    p_fix: float = 0.7              # P(write the correction after verifying the KB is wrong)
    own_first: float = 0.6          # P(skip the KB when you already hold a belief in your own area)
    bm25_title: float = 3.0


class KB:
    def __init__(self, cfg: Cfg):
        self.cfg = cfg
        self.db = sqlite3.connect(":memory:")
        self.db.execute("create virtual table fts using fts5(title, body, tokenize='porter unicode61')")
        self.E: dict[int, dict] = {}
        self.n = 0
        self.endorse: dict[int, list] = {}
        self.dispute: dict[int, list] = {}
        self.V: dict[tuple, list] = {}          # key -> [(value, rnd, acc)]
        self.key_changes: dict[tuple, list] = {}
        self.mod_changes: dict[str, list] = {}
        self.rep_s: dict[int, float] = {}
        self.rep_f: dict[int, float] = {}
        self.by_key: dict[tuple, list] = {}
        self.search_ms = 0.0
        self.searches = 0
        self.pending_notify: list = []
        self.prng = random.Random(cfg.seed + 99)

    # ---------------- writes
    def add(self, author, kind, title, body, rnd):
        self.n += 1
        i = self.n
        claim = parse_claim(body) if kind == "claim" else None
        tkey = None
        if kind == "claim":
            t = title.split()
            if len(t) == 2 and t[0] in MODSET and t[1] in ATTRSET:
                tkey = (t[0], t[1])
        sclaim = claim if (claim and self.prng.random() < self.cfg.parse_recall) else None
        e = dict(id=i, author=author, kind=kind, rnd=rnd, claim=claim, sclaim=sclaim, tkey=tkey, live=True,
                 judged=None)
        self.E[i] = e
        self.db.execute("insert into fts(rowid,title,body) values(?,?,?)", (i, title, body))
        if sclaim:
            self.by_key.setdefault(sclaim[:2], []).append(i)
        if self.cfg.policy == "overwrite" and tkey:
            for j in self.by_key.get(tkey, []):
                if j != i and self.E[j]["live"] and self.E[j]["tkey"] == tkey:
                    self.kill(j)
        return i

    def kill(self, i):
        if self.E[i]["live"]:
            self.E[i]["live"] = False
            self.db.execute("delete from fts where rowid=?", (i,))

    def do_endorse(self, eid, user, verified, rnd):
        if eid in self.E:
            self.endorse.setdefault(eid, []).append((user, verified, rnd))

    def do_dispute(self, eid, user, verified, rnd):
        if eid in self.E:
            self.dispute.setdefault(eid, []).append((user, verified, rnd))

    def tag_change(self, key, rnd, key_level):
        if key_level:
            self.key_changes.setdefault(key, []).append(rnd)
        else:
            self.mod_changes.setdefault(key[0], []).append(rnd)

    def verified(self, key, value, user, rnd, acc, source):
        """Someone checked the code (or fixed a bug, or an auditor looked). Strong evidence."""
        if not self.cfg.use_verified:
            return
        self.V.setdefault(key, []).append((value, rnd, acc, source))
        for j in self.by_key.get(key, []):
            e = self.E[j]
            if not e["live"] or e["judged"] == (value, rnd):
                continue
            e["judged"] = (value, rnd)
            a = e["author"]
            if e["sclaim"][2] != value:
                self.pending_notify.append((a, key, value))
                if self.cfg.gc_refuted:
                    self.rep_f[a] = self.rep_f.get(a, 0) + 1
                    self.kill(j)
                    continue
            if e["sclaim"][2] == value:
                self.rep_s[a] = self.rep_s.get(a, 0) + 1
            else:
                self.rep_f[a] = self.rep_f.get(a, 0) + 1

    def fresh_verified(self, key):
        Vs = self.V.get(key)
        if not Vs:
            return None
        val, r, acc, src = Vs[-1]
        last = max(max(self.key_changes.get(key, [-1])), max(self.mod_changes.get(key[0], [-1])) if self.cfg.use_stale else -1)
        return val if r >= last else None

    def rep(self, u):
        s, f = self.rep_s.get(u, 0), self.rep_f.get(u, 0)
        return (s + 1) / (s + f + 2)

    # ---------------- reads
    def raw(self, toks, n=40):
        t0 = time.perf_counter()
        q = " OR ".join(toks)
        w = self.cfg.bm25_title
        rows = self.db.execute(
            f"select rowid, -bm25(fts,{w},1.0) from fts where fts match ? order by bm25(fts,{w},1.0) limit ?",
            (q, n)).fetchall()
        self.search_ms += (time.perf_counter() - t0) * 1000
        self.searches += 1
        return rows

    def lookup(self, fact, toks, rnd):
        """Return (value|None, entry_id|None, noise_in_top3)."""
        p = self.cfg.policy
        rows = self.raw(toks)
        if p == "governed":
            return self._governed(fact, rows, rnd)
        scored = []
        for i, s in rows:
            e = self.E[i]
            if p == "recency":
                s *= math.exp(-(rnd - e["rnd"]) / 40.0)
            elif p == "votes":
                en = len(self.endorse.get(i, ()))
                di = len(self.dispute.get(i, ()))
                s *= min(3.0, max(0.2, 1 + 0.5 * (en - di)))
            scored.append((s, i))
        scored.sort(reverse=True)
        top = [i for _, i in scored[:3]]
        noise = any(self.E[i]["kind"] != "claim" for i in top)
        for i in top:
            c = self.E[i]["claim"]
            if c and c[:2] == fact:
                return c[2], i, noise
        return None, None, noise

    def _governed(self, fact, rows, rnd):
        cfg = self.cfg
        groups: dict[str, float] = {}
        best: dict[str, tuple] = {}
        kch = self.key_changes.get(fact, [])
        mch = self.mod_changes.get(fact[0], [])
        last_k = max(kch) if kch else -1
        last_m = max(mch) if mch else -1
        Vs = self.V.get(fact, []) if cfg.use_verified else []
        cand = rows
        if not cfg.noise_filter:
            # without the claim parser, whatever BM25 ranks first is what the reader sees
            top = [i for i, _ in rows[:3]]
            noise = any(self.E[i]["kind"] != "claim" for i in top)
            for i in top:
                c = self.E[i]["claim"]
                if c and c[:2] == fact:
                    return c[2], i, noise
            return None, None, noise
        for i, s in cand:
            e = self.E[i]
            c = e["sclaim"]
            if not c or c[:2] != fact:
                continue
            w = 1.0
            if cfg.use_rep:
                w *= self.rep(e["author"]) ** 2 * 4
            w *= math.exp(-(rnd - e["rnd"]) / 80.0)
            if cfg.use_stale:
                if e["rnd"] < last_k:
                    w *= 0.25
                if e["rnd"] < last_m:
                    w *= 0.6
            if cfg.use_endorse:
                for u, ver, r in self.endorse.get(i, ()):
                    w *= 1 + (0.6 if ver else 0.1) * (self.rep(u) if cfg.use_rep else 1)
                for u, ver, r in self.dispute.get(i, ()):
                    w *= 1 - (0.7 if ver else 0.12) * (self.rep(u) if cfg.use_rep else 1)
                w = max(w, 0.0)
            v = c[2]
            groups[v] = groups.get(v, 0.0) + w
            if v not in best or w > best[v][0]:
                best[v] = (w, i)
        if Vs:
            val, r, acc, src = Vs[-1]
            fresh = r >= last_k
            groups[val] = groups.get(val, 0.0) + (4.0 if fresh else 1.0) * acc
        if not groups:
            top = [i for i, _ in rows[:3]]
            noise = any(self.E[i]["kind"] != "claim" for i in top)
            for i in top:
                c = self.E[i]["claim"]
                if c and c[:2] == fact:
                    return c[2], i, noise
            return None, None, noise
        v = max(groups, key=groups.get)
        return v, (best[v][1] if v in best else None), False

    # ---------------- audits (governed + budget)
    def pick_audit_keys(self, demand, budget, rnd):
        pr = []
        for key, ids in self.by_key.items():
            live = [self.E[i] for i in ids if self.E[i]["live"]]
            if not live:
                continue
            vals = {e["sclaim"][2] for e in live}
            u = 0.2
            if len(vals) > 1:
                u += 1.0
            if any(self.dispute.get(e["id"]) for e in live):
                u += 0.8
            Vs = self.V.get(key, [])
            last_k = max(self.key_changes.get(key, [-1]))
            last_m = max(self.mod_changes.get(key[0], [-1]))
            if not Vs:
                u += 0.2
            elif Vs[-1][1] < max(last_k, last_m):
                u += 0.8
            pr.append((u * demand.get(key, 0.001), key))
        pr.sort(reverse=True)
        return [k for _, k in pr[:budget]]


class User:
    __slots__ = ("id", "role", "fam", "act", "care", "mis", "ego", "pc", "chat", "belief", "troll")


def build_world(cfg: Cfg):
    r = random.Random(cfg.seed)
    facts = []
    for m in MODS:
        for a in r.sample(ATTRS, 5):
            facts.append((m, a))
    truth = {f: r.choice(DOMAIN[f[1]]) for f in facts}
    myth = {f: r.choice([v for v in DOMAIN[f[1]] if v != truth[f]]) for f in facts}
    pop = {m: 1.0 / (i + 1) ** 0.8 for i, m in enumerate(r.sample(MODS, len(MODS)))}
    roles = ([("expert", 10), ("regular", 60), ("novice", 20), ("chatty", 7)] +
             [("troll", cfg.n_trolls)])
    total = sum(n for _, n in roles)
    roles = [(k, n if k != "regular" else n + cfg.n_users - total) for k, n in roles]
    users, uid = [], 0
    spec = {"expert": (6, .60, .80, .02, .6, .35), "regular": (4, .40, .40, .10, .4, .30),
            "novice": (2, .50, .20, .25, .5, .20), "chatty": (2, .80, .30, .10, .6, .15),
            "troll": (2, .50, .0, .0, .0, .5)}
    for role, n in roles:
        for _ in range(n):
            u = User()
            nf, act, care, mis, ego, pc = spec[role]
            u.id, u.role = uid, role
            u.fam = set(r.sample(MODS, nf))  # iterate via sorted(): str hash order is per-process
            u.act, u.care = act, care
            u.mis = min(.9, mis * cfg.mis_scale)
            u.ego, u.pc = ego, pc
            u.chat = 0.7 if role == "chatty" else 0.04
            u.troll = role == "troll"
            u.belief = {}
            for f in facts:
                if f[0] in u.fam and r.random() < 0.9:
                    if r.random() < u.mis:
                        u.belief[f] = myth[f] if r.random() < 0.7 else r.choice(
                            [v for v in DOMAIN[f[1]] if v != truth[f]])
                    else:
                        u.belief[f] = truth[f]
            users.append(u)
            uid += 1
    demand = {}
    for f in facts:
        demand[f] = pop[f[0]] / 5
    z = sum(demand.values())
    demand = {f: v / z for f, v in demand.items()}
    return facts, truth, myth, pop, users, demand


def run(cfg: Cfg):
    facts, truth, myth, pop, users, demand = build_world(cfg)
    rnd_ = random.Random(cfg.seed * 7919 + 17)
    kb = KB(cfg)
    by_mod: dict[str, list] = {}
    for f in facts:
        by_mod.setdefault(f[0], []).append(f)
    mod_w = [pop[m] for m in MODS]
    experts = [u for u in users if u.role == "expert"]

    def tokens(f):
        t = [f[1], f[0]]
        for _ in range(rnd_.randint(0, 2)):
            t.append(rnd_.choice(FILL))
        return t

    def claim_text(f, val, canon=True):
        return rnd_.choice(TPL).format(mod=f[0], attr=f[1], val=val), (
            f"{f[0]} {f[1]}" if (canon and rnd_.random() < 0.85) else f"note {rnd_.choice(CHAT)}")

    def write_claim(author, f, val, rnd):
        body, title = claim_text(f, val)
        return kb.add(author, "claim", title, body, rnd)

    def wrong_value(f):
        return rnd_.choice([v for v in DOMAIN[f[1]] if v != truth[f]])

    # seed KB: 60% of facts documented by an expert, 5% wrong
    for f in facts:
        if rnd_.random() < 0.6:
            a = rnd_.choice(experts).id
            v = truth[f] if rnd_.random() > 0.05 else wrong_value(f)
            write_claim(a, f, v, 0)

    open_bugs: list[dict] = []
    S = dict(round=[], acc=[], acc_w=[], none=[], wrong_beliefs=[], open_bugs=[], tasks=[], bug_tasks=[],
             kb_bug=[], own_bug=[], guess_bug=[], size=[], audits=[], verifs=[], noise_top=[],
             live_wrong=[], bugs_total=[], writes=[], stale_ans=[], changes=[])
    cum_bugs = 0
    totals = dict(tasks=0, bug_tasks=0, kb_bug=0, own_bug=0, guess_bug=0, verifs=0, audits=0, writes=0,
                  queries=0, noise_top1=0, answered_wrong=0, answered=0)
    last_change_round: dict = {}

    def evaluate(rnd):
        ok = okw = none = stale = 0
        for f in facts:
            v, _, _ = kb.lookup(f, [f[1], f[0]], rnd)
            if v is None:
                none += 1
            elif v == truth[f]:
                ok += 1
                okw += demand[f]
            else:
                stale += 1
        live_claims = [e for e in kb.E.values() if e["live"] and e["claim"]]
        lw = sum(1 for e in live_claims if e["claim"][2] != truth[e["claim"][:2]]) / max(1, len(live_claims))
        return ok / len(facts), okw, none / len(facts), lw

    for rnd in range(1, cfg.rounds + 1):
        # ---- reality changes the code
        changed = 0
        for f in facts:
            if rnd_.random() < cfg.change_rate:
                changed += 1
                new = rnd_.choice([v for v in DOMAIN[f[1]] if v != truth[f]])
                truth[f] = new
                last_change_round[f] = rnd
                cands = [u for u in users if f[0] in u.fam and not u.troll] or users
                au = rnd_.choice(cands)
                au.belief[f] = new
                if rnd_.random() < cfg.p_update:
                    write_claim(au.id, f, new, rnd)
                    kb.verified(f, new, au.id, rnd, 0.97, "commit")
                if rnd_.random() < cfg.p_tag:
                    kb.tag_change(f, rnd, rnd_.random() < 0.5)
        # ---- bug discovery
        still = []
        for b in open_bugs:
            if rnd_.random() < cfg.p_detect:
                f = b["f"]
                det = rnd_.choice([u for u in users if f[0] in u.fam and not u.troll] or users)
                det.belief[f] = truth[f]
                acc = 0.97 if det.role == "expert" else 0.9
                top, eid, _ = kb.lookup(f, [f[1], f[0]], rnd)
                kb.verified(f, truth[f], det.id, rnd, acc, "bug")
                if eid is not None and top != truth[f]:
                    kb.do_dispute(eid, det.id, True, rnd)
                if rnd_.random() < cfg.p_report:
                    write_claim(det.id, f, truth[f], rnd)
                    totals["writes"] += 1
            else:
                still.append(b)
        open_bugs = still
        # ---- notifications to authors of refuted notes
        if cfg.notify_refuted and kb.pending_notify:
            for a, key, value in kb.pending_notify:
                if 0 <= a < len(users) and not users[a].troll and users[a].belief.get(key) is not None and users[a].belief.get(key) != value and rnd_.random() < cfg.notify_accept:
                    users[a].belief[key] = value
        kb.pending_notify = []
        # ---- audits
        if cfg.policy == "governed" and cfg.audit_budget and cfg.use_verified:
            for key in kb.pick_audit_keys(demand, cfg.audit_budget, rnd):
                v = truth[key] if rnd_.random() < 0.95 else wrong_value(key)
                kb.verified(key, v, -1, rnd, 0.95, "audit")
                totals["audits"] += 1
        # ---- people act
        tasks = bug_tasks = kbb = ownb = gb = 0
        order = list(users)
        rnd_.shuffle(order)
        for u in order:
            if rnd_.random() > u.act:
                continue
            m = rnd_.choice(sorted(u.fam)) if rnd_.random() < 0.7 else rnd_.choices(MODS, mod_w)[0]
            needed = rnd_.sample(by_mod[m], 3)
            tasks += 1
            wrong_src = []
            for f in needed:
                own = u.belief.get(f)
                val = None
                src = "own"
                vv = kb.fresh_verified(f) if cfg.briefing else None
                if vv is not None and own != vv and not u.troll and rnd_.random() < cfg.brief_accept:
                    val, src = vv, "kb"
                    u.belief[f] = vv
                elif own is not None and m in u.fam and rnd_.random() < cfg.own_first:
                    val = own
                else:
                    totals["queries"] += 1
                    v, eid, noise = kb.lookup(f, tokens(f), rnd)
                    if v is None:
                        if own is not None:
                            val, src = own, "own"
                        elif rnd_.random() < u.care + 0.3 and not u.troll:
                            val, src = truth[f], "verified"
                            totals["verifs"] += 1
                            kb.verified(f, truth[f], u.id, rnd, 0.9, "user")
                        else:
                            val, src = rnd_.choice(DOMAIN[f[1]]), "guess"
                            if rnd_.random() < 0.3:
                                kb.add(u.id, "question", f"{f[0]} {f[1]}",
                                       f"does anyone know the {f[1]} of {f[0]}?", rnd)
                    else:
                        totals["answered"] += 1
                        if own is None or own == v:
                            if own == v and not u.troll and rnd_.random() < 0.1 * u.care * 3:
                                ver = rnd_.random() < u.care
                                if ver:
                                    totals["verifs"] += 1
                                    kb.verified(f, truth[f], u.id, rnd, 0.9, "user")
                                kb.do_endorse(eid, u.id, ver and truth[f] == v, rnd)
                            val, src = v, "kb"
                            u.belief[f] = v
                        else:
                            conf = 0.8 if m in u.fam else cfg.conf_unfam
                            if u.troll:
                                conf = 1.0
                            if rnd_.random() < conf:  # "I think the KB is wrong"
                                if rnd_.random() < u.care and not u.troll:
                                    totals["verifs"] += 1
                                    t = truth[f]
                                    u.belief[f] = t
                                    val, src = t, "verified"
                                    kb.verified(f, t, u.id, rnd, 0.9, "user")
                                    if t != v:
                                        kb.do_dispute(eid, u.id, True, rnd)
                                        if rnd_.random() < cfg.p_fix:
                                            write_claim(u.id, f, t, rnd)
                                            totals["writes"] += 1
                                    else:
                                        kb.do_endorse(eid, u.id, True, rnd)
                                else:
                                    val, src = own, "own"
                                    if rnd_.random() < u.ego:
                                        kb.do_dispute(eid, u.id, False, rnd)
                                        if rnd_.random() < 0.5:
                                            write_claim(u.id, f, own, rnd)
                                            totals["writes"] += 1
                            else:
                                val, src = v, "kb"
                                u.belief[f] = v
                if val != truth[f]:
                    wrong_src.append((f, src))
            if wrong_src:
                bug_tasks += 1
                for f, src in wrong_src:
                    open_bugs.append({"f": f, "round": rnd, "src": src})
                    if src == "kb":
                        kbb += 1
                    elif src == "own":
                        ownb += 1
                    else:
                        gb += 1
            # ---- contributions
            if rnd_.random() < u.pc:
                fam_facts = [f for f in u.belief if f[0] in u.fam]
                if fam_facts:
                    f = rnd_.choice(fam_facts)
                    val = u.belief[f]
                    if u.troll and rnd_.random() < 0.7:
                        val = wrong_value(f)
                    write_claim(u.id, f, val, rnd)
                    totals["writes"] += 1
            if rnd_.random() < u.chat:
                f = rnd_.choice(facts)
                txt = " ".join(rnd_.choices(CHAT, k=rnd_.randint(4, 9)))
                if rnd_.random() < 0.25:
                    txt += f" {f[0]}"
                if rnd_.random() < 0.15:
                    txt = f"why is {f[0]} {f[1]} so confusing " + txt
                kb.add(u.id, "chat", f"chat {rnd_.choice(CHAT)}", txt, rnd)
        cum_bugs += bug_tasks
        totals["tasks"] += tasks
        totals["bug_tasks"] += bug_tasks
        totals["kb_bug"] += kbb
        totals["own_bug"] += ownb
        totals["guess_bug"] += gb
        if rnd % 5 == 0 or rnd == 1:
            acc, accw, none, lw = evaluate(rnd)
            wb = sum(1 for u in users for f, v in u.belief.items() if v != truth[f])
            tb = sum(len(u.belief) for u in users)
            S["round"].append(rnd)
            S["acc"].append(round(acc, 4))
            S["acc_w"].append(round(accw, 4))
            S["none"].append(round(none, 4))
            S["wrong_beliefs"].append(round(wb / max(1, tb), 4))
            S["open_bugs"].append(len(open_bugs))
            S["tasks"].append(totals["tasks"])
            S["bug_tasks"].append(totals["bug_tasks"])
            S["kb_bug"].append(totals["kb_bug"])
            S["own_bug"].append(totals["own_bug"])
            S["guess_bug"].append(totals["guess_bug"])
            S["size"].append(len([e for e in kb.E.values() if e["live"]]))
            S["live_wrong"].append(round(lw, 4))
            S["verifs"].append(totals["verifs"])
            S["audits"].append(totals["audits"])
            S["writes"].append(totals["writes"])
            S["changes"].append(changed)
    # final detail: top-3 noise rate (raw rank) and latency
    noise_q = 0
    for f in facts:
        _, _, nz = kb.lookup(f, [f[1], f[0]], cfg.rounds)
        noise_q += bool(nz)
    kinds = {}
    for e in kb.E.values():
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    out = dict(cfg=cfg.__dict__, series=S, totals=totals,
               noise_top3_frac=noise_q / len(facts),
               search_ms_avg=kb.search_ms / max(1, kb.searches), searches=kb.searches,
               kinds=kinds,
               rep={"expert": sum(kb.rep(u.id) for u in users if u.role == "expert") / 10,
                    "regular": sum(kb.rep(u.id) for u in users if u.role == "regular") / 60,
                    "novice": sum(kb.rep(u.id) for u in users if u.role == "novice") / 20,
                    "chatty": sum(kb.rep(u.id) for u in users if u.role == "chatty") / 7,
                    "troll": sum(kb.rep(u.id) for u in users if u.role == "troll") / max(1, cfg.n_trolls)})
    return out


if __name__ == "__main__":
    import json
    import sys
    pol = sys.argv[1] if len(sys.argv) > 1 else "append"
    c = Cfg(name=pol, policy=pol, rounds=int(sys.argv[2]) if len(sys.argv) > 2 else 40)
    t = time.time()
    r = run(c)
    print(round(time.time() - t, 1), "s")
    s = r["series"]
    for i in range(0, len(s["round"]), max(1, len(s["round"]) // 8)):
        print(s["round"][i], "acc", s["acc"][i], "accw", s["acc_w"][i], "none", s["none"][i],
              "wrongbel", s["wrong_beliefs"][i], "open", s["open_bugs"][i], "size", s["size"][i])
    print(r["totals"], "noise", r["noise_top3_frac"], "ms", round(r["search_ms_avg"], 2))
