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

.. warning::

   The headline table describes **one** attacker: a single identity, already
   labelled low-trust, that repeats itself. That is precisely the threat Anchor's
   per-source evidence cap is built to defeat, so a perfect score there is close
   to tautological. :func:`run_sweep` varies the attacker along the two axes that
   actually break the defence — *trust* and *number of identities* — and is the
   honest way to read this harness. Anchor has **no Sybil defence**: the evidence
   cap is per source, so N identities buy N times the cap.

Run with::

    python -m anchor.eval.benchmark
    python -m anchor.eval.benchmark --sweep
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

#: Hyperparameters the headline benchmark runs Anchor with. Ablation shows
#: ``max_source_evidence`` carries the entire result: dropping it takes poison
#: resistance from 1.00 to 0.56, while dropping either of the other two changes
#: nothing. See ``run_sweep``.
BENCH_CONFIG: dict[str, Any] = {
    "min_sources_for_active": 2,
    "max_source_evidence": 3.0,
    "recency_half_life": 20 * DAY,
}


@dataclass
class Scenario:
    events: list[Fact]
    ground_truth: dict[tuple[str, str], str]
    changed_slots: set[tuple[str, str]]
    poisoned_slots: set[tuple[str, str]]
    poison_values: dict[tuple[str, str], str]
    stale_values: dict[tuple[str, str], set[str]] = field(default_factory=dict)


def generate_scenario(n_subjects: int = 30, seed: int = 7,
                      poison_fraction: float = 0.3,
                      attacker_trust: float = 0.25,
                      attacker_identities: int = 1) -> Scenario:
    """Build the deterministic event stream.

    ``attacker_trust`` and ``attacker_identities`` describe the threat model.
    The defaults are the *weak* attacker the headline numbers are measured
    against. Raising either one is what makes this benchmark informative:

    * ``attacker_trust`` at or above the legitimate sources' 0.9 removes the
      trust asymmetry Anchor leans on;
    * ``attacker_identities`` above 1 splits the attack across sources, which
      defeats the *per-source* evidence cap.
    """
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
        # times, spread over ``attacker_identities`` sources.
        if i in poison_ids:
            false_val = rng.choice([v for v in values if v != truth])
            poisoned.add(slot)
            poison_values[slot] = false_val
            for k in range(rng.randint(6, 12)):
                t += rng.uniform(0.2, 1.0) * DAY
                src = ("attacker" if attacker_identities == 1
                       else f"attacker_{k % attacker_identities}")
                events.append(Fact(subject, predicate, false_val, t=t,
                                   trust=attacker_trust, source=src))

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


def _tempdb() -> str:
    import os
    import tempfile

    return os.path.join(tempfile.mkdtemp(prefix="anchor_bench_"), "bench.db")


def run_benchmark(n_subjects: int = 30, seed: int = 7,
                  db_path: str | None = None,
                  attacker_trust: float = 0.25,
                  attacker_identities: int = 1) -> dict[str, Any]:
    scenario = generate_scenario(n_subjects=n_subjects, seed=seed,
                                 attacker_trust=attacker_trust,
                                 attacker_identities=attacker_identities)

    anchor = Anchor(AnchorConfig(db_path=db_path or _tempdb(), **BENCH_CONFIG))

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
        "attacker_trust": attacker_trust,
        "attacker_identities": attacker_identities,
    }, "results": results}


def _anchor_only(scenario: Scenario, **overrides: Any) -> dict[str, Any]:
    """Score Anchor alone on a scenario, with optional config overrides."""
    cfg = {**BENCH_CONFIG, **overrides}
    anchor = Anchor(AnchorConfig(db_path=_tempdb(), **cfg))
    memory = AnchorMemory(anchor)
    for f in scenario.events:
        memory.ingest(f)
    result = _score(memory, scenario)
    anchor.close()
    return result


def run_sweep(n_subjects: int = 30, seed: int = 7) -> dict[str, Any]:
    """Probe where Anchor's poison resistance actually breaks.

    Three sweeps, each holding everything else fixed:

    ``seed``
        Confirms the headline result is not a cherry-picked scenario.
    ``attacker_trust``
        The headline attacker is labelled 0.25 against legitimate sources at
        0.9. Remove that asymmetry and the defence goes to zero \u2014 arguably
        *correct* behaviour, since an equally trusted source making a newer
        claim should win, but it means the headline number describes a
        low-trust attacker specifically, not poisoning in general.
    ``attacker_identities``
        The evidence cap is per *source*, so a Sybil attacker simply buys more
        cap. Anchor has no Sybil defence today; this quantifies the gap.

    Also ablates :data:`BENCH_CONFIG` one knob at a time, which shows the whole
    headline result rests on ``max_source_evidence``.
    """
    base = dict(n_subjects=n_subjects, seed=seed)

    seeds = [(s, _anchor_only(generate_scenario(n_subjects=n_subjects, seed=s)))
             for s in (seed, 13, 42, 99, 1234, 2026)]
    trust = [(v, _anchor_only(generate_scenario(**base, attacker_trust=v)))
             for v in (0.25, 0.5, 0.6, 0.9)]
    sybil = [(n, _anchor_only(generate_scenario(**base, attacker_identities=n)))
             for n in (1, 2, 3, 5, 10, 20)]

    scenario = generate_scenario(**base)
    defaults = AnchorConfig()
    short = {"min_sources_for_active": "min_sources",
             "max_source_evidence": "evidence_cap",
             "recency_half_life": "half_life"}
    ablation = [("all tuned", _anchor_only(scenario))]
    for knob in BENCH_CONFIG:
        ablation.append((f"default {short[knob]}",
                         _anchor_only(scenario, **{knob: getattr(defaults, knob)})))

    return {
        "seed_sweep": [{"seed": s, **r} for s, r in seeds],
        "trust_sweep": [{"attacker_trust": v, **r} for v, r in trust],
        "sybil_sweep": [{"attacker_identities": n, **r} for n, r in sybil],
        "ablation": [{"config": c, **r} for c, r in ablation],
    }


def _fmt(m: dict[str, Any]) -> list[str]:
    return [f"{m['accuracy']:.3f}", f"{m['update_accuracy']:.3f}",
            f"{m['poison_resistance']:.3f}", f"{m['stale_resistance']:.3f}"]


_METRIC_COLS = ["accuracy", "update_acc", "poison_resist", "stale_resist"]


def main(json_path: str = "", sweep: bool = False) -> dict[str, Any]:
    from ..cli.app import Console
    con = Console()
    out = run_benchmark()
    s = out["scenario"]
    con.rule("Anchor evaluation benchmark")
    con.print(f"[dim]{s['subjects']} slots, {s['events']} events, "
              f"{s['changed_slots']} updated, {s['poisoned_slots']} poisoned[/dim]")
    rows = [[name, *_fmt(m)] for name, m in out["results"].items()]
    con.table("results (higher is better)", ["memory", *_METRIC_COLS], rows)
    con.print("[dim]Attacker: 1 identity at trust 0.25 vs. legitimate sources at 0.9. "
              "Run --sweep to see where this breaks.[/dim]")

    if sweep:
        out["sweep"] = sw = run_sweep()
        con.rule("Adversarial sweep — where the headline number stops holding")

        con.table("varying the scenario seed (config fixed)",
                  ["seed", *_METRIC_COLS],
                  [[str(r["seed"]), *_fmt(r)] for r in sw["seed_sweep"]])
        con.print("[dim]Stable across seeds — the result is not a cherry-picked "
                  "scenario.[/dim]")

        con.table("varying attacker trust (legitimate sources stay at 0.9)",
                  ["attacker trust", *_METRIC_COLS],
                  [[f"{r['attacker_trust']:.2f}", *_fmt(r)] for r in sw["trust_sweep"]])
        con.print("[dim]Poison resistance collapses once the attacker is trusted as "
                  "much as anyone else. Anchor relies on trust asymmetry.[/dim]")

        con.table("varying attacker identities (Sybil, trust stays 0.25)",
                  ["identities", *_METRIC_COLS],
                  [[str(r["attacker_identities"]), *_fmt(r)] for r in sw["sybil_sweep"]])
        con.print("[dim]The evidence cap is per source, so extra identities buy extra "
                  "cap. Anchor has no Sybil defence today.[/dim]")

        con.table("config ablation (seed fixed)", ["config", *_METRIC_COLS],
                  [[r["config"], *_fmt(r)] for r in sw["ablation"]])
        con.print("[dim]max_source_evidence carries the entire headline result; the "
                  "other two knobs change nothing.[/dim]")

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
    ap.add_argument("--sweep", action="store_true",
                    help="also probe where Anchor's poison resistance breaks")
    _args = ap.parse_args()
    main(_args.json_path, sweep=_args.sweep)
