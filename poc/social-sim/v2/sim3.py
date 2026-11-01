"""v2: shared code KB, 100 blank-slate people, discrete days. No LLM in the KB.
KB spec (from user): same key -> overwrite; same module+topic but different sub-key -> append;
every entry has timestamp (time-weighted retrieval) and source path. Retrieval = SQLite FTS5 bm25.
"""
import random, sqlite3, os, re
from dataclasses import dataclass

MODS = ["auth", "billing", "cache", "queue", "search", "export", "notify", "sched", "storage", "report", "gateway", "sync"]
A_WORDS = ["retry", "token", "batch", "index", "lock", "page", "ttl", "rate", "shard", "audit", "quota", "stream",
           "replay", "mask", "route", "probe", "flush", "trim", "merge", "scope", "sign", "drain", "pin", "spill"]
B_WORDS = ["limit", "delay", "size", "mode", "order", "window", "floor", "cap", "step", "depth", "unit", "level",
           "budget", "grace", "ratio", "bound", "width", "stride", "phase", "rank"]
FILL = ["note", "see", "legacy", "team", "ticket", "migrated", "check", "default", "review", "hotfix", "doc", "wiki", "owner", "todo"]
CHAT = ["lunch", "weekend", "coffee", "meeting", "holiday", "game", "movie", "birthday", "traffic", "weather"]


@dataclass
class Cfg:
    name: str = "P0"
    seed: int = 0
    days: int = 35
    n_people: int = 100
    relayer_frac: float = 0.25
    seed_cov: float = 0.9        # share of facts present in KB v1
    seed_wrong: float = 0.08     # share of seeded entries that are wrong
    p_modify: float = 0.4        # share of tasks that edit code
    churn: float = 0.15          # modify-task actually changes a fact's value
    p_catch: float = 0.7         # test/verify catches a wrong-info bug
    base_mis: float = 0.08       # misread-code base prob
    p_relay: float = 0.3         # lookups that go through a relayer
    relay_distort: float = 0.06
    p_wb_mean: float = 0.6       # mean prob a person writes back to KB
    pv_mean: float = 0.06        # default prob of verifying w/o warning
    p_mismatch: float = 0.12     # writer uses a variant key (-> append instead of overwrite)
    noise_rate: float = 0.05
    heed: float = 0.7            # verify prob when a warning is shown
    flag_pass: float = 0.5       # relayer forwards the warning
    half_life: float = 15.0      # 0 = no time weight
    stale_flag: bool = False     # pull: warn if source file changed since entry written
    commit_hook: bool = False    # push: code changer is prompted to update KB
    outcome_fb: bool = False     # failed-use refute flag + hold single overwrite of confirmed entry
    canon_key: bool = False      # writer must pick existing key (tool-enforced)
    p_wb_hook: float = 0.92
    # --- v3 architecture knobs
    wb_verify: float = -1.0      # >=0: flat write-back prob at miss/verify-disagree/caught moments
    wb_change: float = -1.0      # >=0: flat write-back prob for the person who changed the code
    delay_days: int = 0          # writes become visible after N days (PR review / curator schedule)
    review_catch: float = 0.0    # reviewer rejects a wrong-valued write with this prob
    echo_confirm: bool = False   # auto-capture re-records unverified uses (refreshes ts/ver, hides staleness)
    opt_out: float = 0.0         # share of developers who never write / are never recorded (privacy)
    noise_mult: float = 1.0
    dispute_hold: int = -1       # -1: follow outcome_fb; 0/1 explicit
    anchor: float = 0.0          # share of facts mechanically extractable from code (regex/AST, no LLM)
    p_mismatch_canon: float = 0.02


class World:
    def __init__(s, seed):
        r = random.Random(seed * 7919 + 1)
        s.facts = []
        for m in MODS:
            pairs = set()
            while len(pairs) < 10:
                pairs.add((r.choice(A_WORDS), r.choice(B_WORDS)))
            for k, (a, b) in enumerate(sorted(pairs)):
                der = r.random() < 0.3
                base = r.randint(1, 5) if der else 0
                val = r.randint(base, 11)
                s.facts.append(dict(id=len(s.facts), mod=m, a=a, b=b, path=f"{m}/part{k % 3}.py",
                                    derived=der, base=base, val=val, diff=0.75 if der else 0.15))
        s.ver = {f["path"]: 0 for f in s.facts}
        s.by_mod = {m: [f for f in s.facts if f["mod"] == m] for m in MODS}

    @staticmethod
    def name(f):
        return f"{f['a']}_{f['b']}".upper()

    def render(s, path):
        out = [f'"""{path} (synthetic module)"""']
        for f in s.facts:
            if f["path"] != path:
                continue
            n = s.name(f)
            if f["derived"]:
                out += [f"BASE_{n} = {f['base']}", f"{n} = BASE_{n} + {f['val'] - f['base']}"]
            else:
                out.append(f"{n} = {f['val']}")
        return "\n".join(out) + "\n"

    def dump(s, root):
        for p in s.ver:
            os.makedirs(os.path.join(root, os.path.dirname(p)), exist_ok=True)
            open(os.path.join(root, p), "w").write(s.render(p))

    def check_code(s, root):
        """exec every file; real code must equal the fact table."""
        for p in s.ver:
            ns = {}
            exec(open(os.path.join(root, p)).read(), ns)
            for f in s.facts:
                if f["path"] == p:
                    assert ns[s.name(f)] == f["val"], (p, f)
        return True

    def change(s, f, r):
        lo = f["base"] if f["derived"] else 0
        new = r.choice([v for v in range(lo, 12) if v != f["val"]])
        f["val"] = new
        s.ver[f["path"]] += 1
        return new


class KB:
    def __init__(s, H):
        s.H = H
        s.db = sqlite3.connect(":memory:")
        s.db.row_factory = sqlite3.Row
        s.db.execute("create virtual table fts using fts5(text)")
        s.db.execute("create table e(id integer primary key, mod, a, b, val, path, ver, author, day,"
                     " refuted, confirms, disputed, kind, alive)")

    def insert(s, mod, a, b, val, path, ver, author, day, kind="fact", filler=""):
        cur = s.db.execute("insert into e(mod,a,b,val,path,ver,author,day,refuted,confirms,disputed,kind,alive)"
                           " values(?,?,?,?,?,?,?,?,0,0,0,?,1)", (mod, a, b, val, path, ver, author, day, kind))
        s.db.execute("insert into fts(rowid,text) values(?,?)", (cur.lastrowid, f"{mod} {a} {b} {filler}"))
        return cur.lastrowid

    def exact(s, mod, a, b):
        return s.db.execute("select * from e where kind='fact' and alive=1 and mod=? and a=? and b=?", (mod, a, b)).fetchone()

    def write(s, mod, a, b, val, path, ver, author, day, authoritative, hold, filler):
        e = s.exact(mod, a, b)
        if e is None:
            s.insert(mod, a, b, val, path, ver, author, day, filler=filler)
            return "insert"
        if val == e["val"]:
            s.db.execute("update e set ver=?, day=?, refuted=0, disputed=0, confirms=confirms+1 where id=?", (ver, day, e["id"]))
            return "confirm"
        if hold and not authoritative and e["confirms"] >= 2 and not e["refuted"] and not e["disputed"]:
            s.db.execute("update e set disputed=1 where id=?", (e["id"],))
            return "held"
        s.db.execute("update e set val=?, ver=?, author=?, day=?, refuted=0, disputed=0, confirms=0 where id=?",
                     (val, ver, author, day, e["id"]))
        return "overwrite"

    def search(s, mod, a, b, today):
        rows = s.db.execute("select rowid, bm25(fts) as s from fts where fts match ? order by bm25(fts) limit 8",
                            (f"{mod} OR {a} OR {b}",)).fetchall()
        best, bs = None, -1
        for r in rows:
            e = s.db.execute("select * from e where id=?", (r["rowid"],)).fetchone()
            if not e["alive"]:
                continue
            dec = 0.5 ** ((today - e["day"]) / s.H) if s.H > 0 else 1.0
            sc = abs(r["s"]) * dec
            if sc > bs:
                best, bs = e, sc
        return best


class Dev:
    def __init__(s, i, r, cfg, relayer):
        s.id, s.relayer = i, relayer
        s.skill = r.betavariate(4, 2)
        s.pv = min(0.6, r.betavariate(1.5, 15) * cfg.pv_mean / 0.09)
        s.wb = min(1.0, r.betavariate(3, 2) * cfg.p_wb_mean / 0.6)
        s.mis = {}


class Sim:
    def __init__(s, cfg):
        s.c = cfg
        s.r = random.Random(cfg.seed * 104729 + 17)
        s.w = World(cfg.seed)
        s.kb = KB(cfg.half_life)
        n_rel = int(cfg.n_people * cfg.relayer_frac)
        s.devs = [Dev(i, s.r, cfg, i < n_rel) for i in range(cfg.n_people)]
        for d in s.devs:
            d.opt = False
            if cfg.opt_out > 0 and not d.relayer and s.r.random() < cfg.opt_out:
                d.opt = True
        s.pending = []
        s.anch = set()
        if cfg.anchor > 0:
            for f in s.w.facts:
                if s.r.random() < cfg.anchor * (0.4 if f["derived"] else 1.0):
                    s.anch.add(f["id"])
        s.day = 0
        for f in s.w.facts:
            if s.r.random() < cfg.seed_cov:
                v = f["val"]
                if s.r.random() < cfg.seed_wrong:
                    v = s.r.choice([x for x in range(12) if x != f["val"]])
                s.kb.insert(f["mod"], f["a"], f["b"], v, f["path"], 0, -1, 0, filler=s.filler())
        s.log = []

    def filler(s):
        return " ".join(s.r.choices(FILL, k=s.r.randint(0, 6)))

    # --- helpers
    def read_code(s, dev, f):
        key = (f["id"], s.w.ver[f["path"]])
        if key not in dev.mis:
            p = s.c.base_mis * (0.4 + 1.6 * f["diff"]) * (1.6 - 1.2 * dev.skill)
            if s.r.random() < p:
                near = [v for v in range(f["val"] - 2, f["val"] + 3) if v != f["val"] and 0 <= v < 12]
                dev.mis[key] = s.r.choice(near)
            else:
                dev.mis[key] = None
        return dev.mis[key] if dev.mis[key] is not None else f["val"]

    def equiv(s, e, f):
        return e is not None and e["kind"] == "fact" and e["mod"] == f["mod"] and e["a"] == f["a"] and \
            e["b"] in (f["b"], f["b"] + "x", f["b"] + "z")

    def hold(s):
        return bool(s.c.outcome_fb) if s.c.dispute_hold < 0 else bool(s.c.dispute_hold)

    def flagged(s, e):
        c = s.c
        if c.stale_flag and e["ver"] < s.w.ver[e["path"]]:
            return True
        if (c.outcome_fb and e["refuted"]) or (s.hold() and e["disputed"]):
            return True
        return False

    def p_verify_wb(s, dev):
        if dev.opt:
            return 0.0
        return s.c.wb_verify if s.c.wb_verify >= 0 else dev.wb

    def p_change_wb(s, dev):
        if dev.opt:
            return 0.0
        if s.c.wb_change >= 0:
            return s.c.wb_change
        return s.c.p_wb_hook if s.c.commit_hook else dev.wb

    def write_back(s, dev, f, val, authoritative, m, due=None):
        c = s.c
        if c.review_catch > 0 and val != f["val"] and s.r.random() < c.review_catch:
            m["rejected"] += 1
            return
        if c.delay_days > 0 and due is None:
            s.pending.append((s.day + c.delay_days, dev, f, val, authoritative, s.w.ver[f["path"]]))
            return
        ver_w = due if due is not None else s.w.ver[f["path"]]
        mm = c.p_mismatch_canon if c.canon_key else c.p_mismatch
        b = f["b"] if s.r.random() >= mm else f["b"] + s.r.choice("xz")
        old = s.kb.exact(f["mod"], f["a"], b)
        res = s.kb.write(f["mod"], f["a"], b, val, f["path"], ver_w, dev.id, s.day,
                         authoritative, s.hold(), s.filler())
        if res == "overwrite" and old is not None:
            if old["val"] == f["val"] and val != f["val"]:
                m["wrong_over"] += 1
            elif old["val"] != f["val"] and val == f["val"]:
                m["good_over"] += 1
        if res == "held":
            m["held"] += 1
        if b != f["b"]:
            m["mismatch_writes"] += 1

    # --- one lookup + use of a fact
    def use_fact(s, dev, f, m):
        c = s.c
        relayed = s.r.random() < c.p_relay
        e = s.kb.search(f["mod"], f["a"], f["b"], s.day)
        m["queries"] += 1
        if e is not None and e["kind"] == "fact" and not s.equiv(e, f):
            if s.r.random() < 0.8:
                e = None  # reader notices it is a sibling item
        elif e is not None and e["kind"] != "fact":
            e = None
        if e is None:
            m["miss"] += 1
            m["reads"] += 1
            m["reads_useful"] += 1
            v = s.read_code(dev, f)
            if s.r.random() < s.p_verify_wb(dev):
                s.write_back(dev, f, v, False, m)
            used, ent = v, None
        else:
            v_kb = e["val"]
            if v_kb != f["val"]:
                m["served_wrong"] += 1
            if relayed and s.r.random() < c.relay_distort:
                v_kb = s.r.choice([x for x in range(12) if x != e["val"]])
            fl = s.flagged(e)
            if fl:
                m["flag_seen"] += 1
            vis = fl and (not relayed or s.r.random() < c.flag_pass)
            pv = max(dev.pv, c.heed) if vis else dev.pv
            used, ent = v_kb, e
            if s.r.random() < pv:
                m["reads"] += 1
                v = s.read_code(dev, f)
                if v != e["val"]:
                    m["reads_useful"] += 1
                    s.write_back(dev, f, v, False, m) if s.r.random() < s.p_verify_wb(dev) else None
                else:
                    s.kb.db.execute("update e set ver=?, day=?, refuted=0, disputed=0, confirms=confirms+1 where id=?",
                                    (s.w.ver[e["path"]], s.day, e["id"]))
                used, ent = v, None
        # the "test"
        if used == f["val"]:
            if ent is not None and (c.outcome_fb or s.hold()):
                s.kb.db.execute("update e set confirms=confirms+1 where id=?", (ent["id"],))
            if ent is not None and c.echo_confirm and not dev.opt:
                s.kb.db.execute("update e set ver=?, day=? where id=?", (s.w.ver[ent["path"]], s.day, ent["id"]))
        else:
            if s.r.random() < c.p_catch:
                m["caught"] += 1
                m["reads"] += 1
                v = s.read_code(dev, f)
                if ent is not None and c.outcome_fb:
                    s.kb.db.execute("update e set refuted=1 where id=?", (ent["id"],))
                if v == f["val"]:
                    m["reads_useful"] += 1
                cur = s.kb.exact(f["mod"], f["a"], f["b"])
                if (cur is None or cur["val"] != v) and s.r.random() < s.p_verify_wb(dev):
                    s.write_back(dev, f, v, False, m)
            else:
                m["escaped"] += 1
                if ent is not None and c.echo_confirm and not dev.opt:
                    s.kb.db.execute("update e set ver=?, day=? where id=?", (s.w.ver[ent["path"]], s.day, ent["id"]))

    def person_day(s, dev, m):
        c = s.c
        if dev.relayer:
            for _ in range(2):
                f = s.r.choice(s.w.facts)
                e = s.kb.search(f["mod"], f["a"], f["b"], s.day)
                m["relay_q"] += 1
                if s.equiv(e, f) and e["val"] != f["val"]:
                    m["relay_wrong"] += 1
            return
        mod = s.r.choices(MODS, weights=[1 / (i + 1) ** 0.8 for i in range(len(MODS))])[0]
        facts = s.r.sample(s.w.by_mod[mod], s.r.choice([1, 2, 2, 3]))
        for f in facts:
            s.use_fact(dev, f, m)
        if s.r.random() < c.p_modify:
            m["edits"] += 1
            if s.r.random() < c.churn:
                f = s.r.choice(facts)
                new = s.w.change(f, s.r)
                m["changes"] += 1
                if f["id"] in s.anch:
                    s.sync_fact(f, m)
                if s.r.random() < s.p_change_wb(dev):
                    s.write_back(dev, f, new, True, m)
        if not dev.opt and s.r.random() < min(1.0, c.noise_rate * c.noise_mult):
            s.kb.insert(s.r.choice(MODS), s.r.choice(A_WORDS), " ".join(s.r.choices(CHAT, k=2)), None, "-", 0, dev.id,
                        s.day, kind="noise", filler=" ".join(s.r.choices(CHAT, k=3)))

    def sync_fact(s, f, m):
        """Mechanical re-extraction from code (CI/nightly): set cached value = code value. No judgement, no inserts."""
        rows = s.kb.db.execute("select id,val from e where kind='fact' and alive=1 and mod=? and a=? and b in (?,?,?)",
                               (f["mod"], f["a"], f["b"], f["b"] + "x", f["b"] + "z")).fetchall()
        for r in rows:
            if r["val"] != f["val"]:
                m["auto_fix"] += 1
                s.kb.db.execute("update e set val=?, ver=?, day=?, refuted=0, disputed=0 where id=?",
                                (f["val"], s.w.ver[f["path"]], s.day, r["id"]))
            else:
                s.kb.db.execute("update e set ver=? where id=?", (s.w.ver[f["path"]], r["id"]))

    def probe(s, m):
        for f in s.w.facts:
            e = s.kb.search(f["mod"], f["a"], f["b"], s.day)
            if not s.equiv(e, f):
                m["p_missing"] += 1
            elif e["val"] == f["val"]:
                m["p_correct"] += 1
            else:
                m["p_wrong"] += 1
                if s.flagged(e):
                    m["p_wrong_flagged"] += 1
                else:
                    m["p_silent"] += 1
        m["kb_facts"] = s.kb.db.execute("select count(*) from e where kind='fact'").fetchone()[0]
        m["ghosts"] = s.kb.db.execute("select count(*) from e where kind='fact' and (b like '%x' or b like '%z')"
                                      " and b not in (select b from e group by b having 0)").fetchone()[0]
        m["noise_n"] = s.kb.db.execute("select count(*) from e where kind='noise'").fetchone()[0]

    def run(s):
        keys = ["rejected", "auto_fix", "queries", "miss", "served_wrong", "flag_seen", "reads", "reads_useful", "caught", "escaped", "edits",
                "changes", "wrong_over", "good_over", "held", "mismatch_writes", "relay_q", "relay_wrong",
                "p_correct", "p_wrong", "p_missing", "p_wrong_flagged", "p_silent"]
        for d in range(1, s.c.days + 1):
            s.day = d
            m = {k: 0 for k in keys}
            due = [x for x in s.pending if x[0] <= d]
            s.pending = [x for x in s.pending if x[0] > d]
            for _, dev, f, val, auth, ver0 in due:
                s.write_back(dev, f, val, auth, m, due=ver0)
            for fid in sorted(s.anch):
                s.sync_fact(s.w.facts[fid], m)
            order = list(s.devs)
            s.r.shuffle(order)
            for dev in order:
                s.person_day(dev, m)
            s.probe(m)
            m["day"] = d
            s.log.append(m)
        return s.log


def run(cfg):
    sim = Sim(cfg)
    return dict(cfg=cfg.__dict__, log=sim.run())


if __name__ == "__main__":
    import tempfile
    sim = Sim(Cfg())
    d = tempfile.mkdtemp()
    sim.w.dump(d)
    print("code ok", sim.w.check_code(d), len(sim.w.facts), "facts")
    log = sim.run()
    sim.w.dump(d)
    print("code after sim ok", sim.w.check_code(d))
    for m in log[::7] + [log[-1]]:
        print({k: m[k] for k in ["day", "p_correct", "p_wrong", "p_silent", "p_missing", "served_wrong", "escaped", "reads", "changes", "wrong_over", "good_over", "kb_facts"]})
