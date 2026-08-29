"""Evaluation benchmark: Anchor vs. naive summary/RAG agent memory.

Generates a deterministic multi-session stream of facts that exercises the hard
parts of agent memory:

* **updates** – a slot's true value changes over time (tests temporal tracking);
* **reinforcement** – true values are repeated by trusted sources;
* **distractors** – unrelated facts add noise;
* **poisoning** – a low-trust attacker repeatedly asserts a false value *after*
  the legitimate update (tests robustness to memory poisoning).

We then ask each memory for the *current* value of every slot and score:

* ``accuracy``           – fraction of slots answered with the ground-truth value;
* ``update_accuracy``    – accuracy restricted to slots whose value changed;
* ``poison_resistance``  – fraction of attacked slots NOT poisoned;
* ``stale_resistance``   – fraction of updated slots not answered with a value
  that has since been superseded.

Run with::

    python -m anchor.eval.benchmark
    python -m anchor.eval.benchmark --json results.json
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from ..config import DAY, AnchorConfig
from ..core.engine import Anchor
from .baselines import (
    AnchorMemory,
    Fact,
    RAGMemory,
    RecencyWindowMemory,
    SummaryMemory,
    TrustWeightedMemory,
)


@dataclass
class Scenario:
    events: list[Fact]
    ground_truth: dict[tuple[str, str], str]
    changed_slots: set[tuple[str, str]]
    poisoned_slots: set[tuple[str, str]]
    poison_values: dict[tuple[str, str], str]
    stale_values: dict[tuple[str, str], set[str]] = field(default_factory=dict)


def generate_scenario(n_subjects: int = 30, seed: int = 7,
                      poison_fraction: float = 0.3) -> Scenario:
    rng = random.Random(seed)
    values = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
              "golf", "hotel", "india", "juliet"]
    predicate = "current_value"
    events: list[Fact] = []
    ground_truth: dict[tuple[str, str], str] = {}
    changed: set[tuple[str, str]] = set()
    poisoned: set[tuple[str, str]] = set()
    poison_values: dict[tuple[str, str], str] = {}

    n_poison = int(n_subjects * poison_fraction)
    poison_ids = set(rng.sample(range(n_subjects), n_poison))
    stale_values: dict[tuple[str, str], set[str]] = {}

    for i in range(n_subjects):
        subject = f"entity_{i:02d}"
        slot = (subject, predicate)
        t = float(i)  # stagger subjects
        n_updates = rng.randint(1, 3)
        chosen = rng.sample(values, n_updates)
        for j, val in enumerate(chosen):
            t += rng.uniform(3, 8) * DAY
            events.append(Fact(subject, predicate, val, t=t, trust=0.9, source="chatA"))
            # occasional trusted corroboration of the same value
            if rng.random() < 0.5:
                t += rng.uniform(0.1, 1.0) * DAY
                events.append(Fact(subject, predicate, val, t=t, trust=0.9,
                                   source="chatB"))
        truth = chosen[-1]
        ground_truth[slot] = truth
        if n_updates > 1:
            changed.add(slot)
            stale_values[slot] = set(chosen[:-1])

        # Poisoning: an attacker asserts a false value *after* the truth, many
        # times, from a single low-trust source.
        if i in poison_ids:
            false_val = rng.choice([v for v in values if v != truth])
            poisoned.add(slot)
            poison_values[slot] = false_val
            for _ in range(rng.randint(6, 12)):
                t += rng.uniform(0.2, 1.0) * DAY
                events.append(Fact(subject, predicate, false_val, t=t,
                                   trust=0.25, source="attacker"))

    # Distractor facts (noise).
    for k in range(n_subjects):
        subject = f"noise_{k:02d}"
        events.append(Fact(subject, "note", rng.choice(values),
                           t=rng.uniform(0, n_subjects * 40) * DAY, trust=0.6,
                           source="misc"))

    events.sort(key=lambda f: f.t)
    return Scenario(events, ground_truth, changed, poisoned, poison_values, stale_values)


def _score(memory, scenario: Scenario) -> dict[str, Any]:
    correct = total = 0
    upd_correct = upd_total = 0
    poison_resisted = poison_total = 0
    stale_resisted = stale_total = 0
    for slot, truth in scenario.ground_truth.items():
        ans = memory.answer(*slot)
        total += 1
        hit = ans == truth
        correct += int(hit)
        if slot in scenario.changed_slots:
            upd_total += 1
            upd_correct += int(hit)
            stale_total += 1
            stale_resisted += int(ans not in scenario.stale_values.get(slot, set()))
        if slot in scenario.poisoned_slots:
            poison_total += 1
            poison_resisted += int(ans != scenario.poison_values[slot])
    return {
        "accuracy": round(correct / total, 3) if total else 0.0,
        "update_accuracy": round(upd_correct / upd_total, 3) if upd_total else 0.0,
        "poison_resistance": round(poison_resisted / poison_total, 3) if poison_total else 0.0,
        "stale_resistance": round(stale_resisted / stale_total, 3) if stale_total else 0.0,
    }


def run_benchmark(n_subjects: int = 30, seed: int = 7,
                  db_path: str | None = None) -> dict[str, Any]:
    import os
    import tempfile

    scenario = generate_scenario(n_subjects=n_subjects, seed=seed)

    db = db_path or os.path.join(tempfile.mkdtemp(prefix="anchor_bench_"), "bench.db")
    anchor = Anchor(AnchorConfig(
        db_path=db, min_sources_for_active=2, max_source_evidence=3.0,
        recency_half_life=20 * DAY,
    ))

    memories = [SummaryMemory(), RecencyWindowMemory(), TrustWeightedMemory(),
                RAGMemory(), AnchorMemory(anchor)]
    for f in scenario.events:
        for m in memories:
            m.ingest(f)

    results = {m.name: _score(m, scenario) for m in memories}
    anchor.close()
    return {"scenario": {
        "subjects": n_subjects,
        "changed_slots": len(scenario.changed_slots),
        "poisoned_slots": len(scenario.poisoned_slots),
        "events": len(scenario.events),
    }, "results": results}


def main(json_path: str = "") -> dict[str, Any]:
    from ..cli.app import Console
    con = Console()
    out = run_benchmark()
    s = out["scenario"]
    con.rule("Anchor evaluation benchmark")
    con.print(f"[dim]{s['subjects']} slots, {s['events']} events, "
              f"{s['changed_slots']} updated, {s['poisoned_slots']} poisoned[/dim]")
    rows = []
    for name, m in out["results"].items():
        rows.append([name, f"{m['accuracy']:.3f}", f"{m['update_accuracy']:.3f}",
                     f"{m['poison_resistance']:.3f}", f"{m['stale_resistance']:.3f}"])
    con.table("results (higher is better)",
              ["memory", "accuracy", "update_acc", "poison_resist", "stale_resist"],
              rows)
    if json_path:
        import json as _json
        with open(json_path, "w", encoding="utf-8") as fh:
            _json.dump(out, fh, indent=2)
        con.print(f"[dim]wrote {json_path}[/dim]")
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Anchor memory benchmark")
    ap.add_argument("--json", dest="json_path", default="",
                    help="also write the raw results to this JSON file")
    main(ap.parse_args().json_path)
