"""Deterministic simulation of a team that co-maintains a knowledge base.

World: SERVICES x ATTRS facts with a ground truth that changes over time.
People: experts, average contributors and trolls. Noise: chit-chat, wrong values,
a shared "myth" (many average users repeat the same wrong value), and stale
claims after a truth change.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

SERVICES = [f"svc{i:02d}" for i in range(20)]
ATTRS = ["port", "timeout", "owner", "region"]
OWNERS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
REGIONS = ["us-east", "us-west", "eu-west", "eu-north", "ap-south", "ap-east"]

CHAT = [
    "lol the standup ran so long today.",
    "anyone want coffee from downstairs?",
    "I think the new office chairs are too low.",
    "my train was late again this morning.",
    "that demo yesterday was honestly a mess.",
    "happy friday everyone, have a great weekend.",
    "I had ramen for lunch and it was great.",
    "does anyone remember where the spare monitor cable is?",
    "the wifi on the third floor is awful.",
    "can we move the retro to thursday afternoon?",
]


def fact_key(svc: str, attr: str) -> str:
    return f"{attr} of {svc}"


def rand_value(rng: random.Random, attr: str, avoid: str | None = None) -> str:
    while True:
        if attr == "port":
            v = str(rng.randrange(1000, 9999))
        elif attr == "timeout":
            v = f"{rng.choice([5, 10, 15, 30, 45, 60, 90, 120])}s"
        elif attr == "owner":
            v = rng.choice(OWNERS)
        else:
            v = rng.choice(REGIONS)
        if v != avoid:
            return v


@dataclass
class Person:
    name: str
    kind: str  # expert | average | troll
    acc: float
    stale_after_change: float  # P(still says the old value after a change)


@dataclass
class Event:
    tick: int
    user: str
    text: str
    kind: str  # fact | correction | chat | mixed
    fact: tuple[str, str] | None = None
    claimed: str | None = None
    is_true: bool | None = None  # truth AT THE TIME of the message


@dataclass
class World:
    truth: dict[tuple[str, str], str]  # current truth
    history: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    myth: dict[tuple[str, str], str] = field(default_factory=dict)


def people() -> list[Person]:
    ps = [Person(f"exp{i}", "expert", 0.95, 0.05) for i in range(4)]
    ps += [Person(f"avg{i}", "average", 0.75, 0.5) for i in range(5)]
    ps += [Person(f"trl{i}", "troll", 0.25, 0.5) for i in range(3)]
    return ps


def stmt(svc: str, attr: str, val: str) -> str:
    return f"The {attr} of {svc} is {val}."


def correction(svc: str, attr: str, val: str) -> str:
    return f"Correction: the {attr} of {svc} is now {val}."


def build(seed: int = 7, per_phase: int = 800, phases: int = 3):
    """Return (world0, events_by_phase, truth_at_end_of_phase)."""
    rng = random.Random(seed)
    facts = [(s, a) for s in SERVICES for a in ATTRS]
    truth = {f: rand_value(rng, f[1]) for f in facts}
    world = World(truth=dict(truth), history={f: [truth[f]] for f in facts})
    # 12 facts carry a widespread myth (a specific wrong value many people repeat)
    for f in rng.sample(facts, 12):
        world.myth[f] = rand_value(rng, f[1], avoid=truth[f])
    ps = people()
    by_phase: list[list[Event]] = []
    truth_at: list[dict[tuple[str, str], str]] = []
    changed_in_phase: list[set] = []
    for ph in range(phases):
        changed: set = set()
        if ph > 0:  # 20% of facts change at the start of each later phase
            for f in rng.sample(facts, len(facts) // 5):
                world.truth[f] = rand_value(rng, f[1], avoid=world.truth[f])
                world.history[f].append(world.truth[f])
                changed.add(f)
        evs: list[Event] = []
        announced: set = set()
        for i in range(per_phase):
            p = rng.choice(ps)
            r = rng.random()
            if r < 0.30:  # pure chit-chat
                evs.append(Event(ph, p.name, rng.choice(CHAT), "chat"))
                continue
            f = rng.choice(facts)
            truth_now = world.truth[f]
            old = world.history[f][-2] if len(world.history[f]) > 1 else None
            # what this person believes
            if f in changed and old is not None and rng.random() < p.stale_after_change:
                val = old
            elif f in world.myth and p.kind == "average" and rng.random() < 0.6:
                val = world.myth[f]
            elif rng.random() < p.acc:
                val = truth_now
            else:
                val = rand_value(rng, f[1], avoid=truth_now)
            is_true = val == truth_now
            kind = "fact"
            text = stmt(f[0], f[1], val)
            # experts announce explicit corrections for changed facts (once, early)
            if f in changed and p.kind == "expert" and f not in announced and rng.random() < 0.5:
                kind, text = "correction", correction(f[0], f[1], truth_now)
                val, is_true = truth_now, True
                announced.add(f)
            elif p.kind == "troll" and rng.random() < 0.15:
                wrong = rand_value(rng, f[1], avoid=truth_now)
                kind, text = "correction", correction(f[0], f[1], wrong)
                val, is_true = wrong, False
            if rng.random() < 0.2:  # chit-chat glued to the fact in the same message
                text = f"{text} {rng.choice(CHAT)}"
                kind = "mixed" if kind == "fact" else kind
            evs.append(Event(ph, p.name, text, kind, f, val, is_true))
        by_phase.append(evs)
        truth_at.append(dict(world.truth))
        changed_in_phase.append(changed)
    return world, by_phase, truth_at, changed_in_phase, ps


if __name__ == "__main__":
    w, ph, ta, ch, ps = build()
    from collections import Counter

    for i, evs in enumerate(ph):
        c = Counter(e.kind for e in evs)
        wrong = sum(1 for e in evs if e.fact and e.is_true is False)
        total = sum(1 for e in evs if e.fact)
        print(f"phase {i}: {len(evs)} msgs {dict(c)} fact-claims={total} wrong={wrong} "
              f"({wrong / max(1, total):.0%}) changed={len(ch[i])}")
    print("myth facts:", len(w.myth))
