from anchor.eval import generate_scenario, run_benchmark
from anchor.eval.benchmark import _anchor_only


def test_scenario_is_deterministic():
    s1 = generate_scenario(n_subjects=10, seed=1)
    s2 = generate_scenario(n_subjects=10, seed=1)
    assert [f.object for f in s1.events] == [f.object for f in s2.events]
    assert s1.ground_truth == s2.ground_truth


def test_anchor_beats_baselines(tmp_path):
    out = run_benchmark(n_subjects=25, seed=3, db_path=str(tmp_path / "b.db"))
    res = out["results"]
    anchor = res["anchor"]["accuracy"]
    summary = res["summary(overwrite)"]["accuracy"]
    rag = res["rag(vector)"]["accuracy"]
    assert anchor >= summary
    assert anchor >= rag
    # Anchor must resist poisoning strictly better than last-write-wins.
    assert res["anchor"]["poison_resistance"] > res["summary(overwrite)"]["poison_resistance"]


def test_headline_result_is_not_a_cherry_picked_seed():
    """The 1.00 in the README must hold for scenarios we did not choose."""
    for seed in (7, 13, 42, 99, 1234):
        r = _anchor_only(generate_scenario(n_subjects=30, seed=seed))
        assert r["poison_resistance"] == 1.0, f"seed {seed} regressed: {r}"


def test_poison_resistance_depends_on_the_attacker_being_low_trust():
    """Pin a documented limitation: the defence needs a trust asymmetry.

    The headline number describes a *low-trust* attacker. Once the attacker is
    trusted as much as the legitimate sources, a newer claim legitimately wins
    and poison resistance goes to zero. If this ever starts passing at high
    trust, the README claim has become stronger and should be updated.
    """
    weak = _anchor_only(generate_scenario(attacker_trust=0.25))
    strong = _anchor_only(generate_scenario(attacker_trust=0.9))
    assert weak["poison_resistance"] == 1.0
    assert strong["poison_resistance"] < weak["poison_resistance"]


def test_evidence_cap_is_per_source_so_sybil_attacks_degrade_it():
    """Pin the Sybil gap: N identities buy N times the per-source evidence cap."""
    single = _anchor_only(generate_scenario(attacker_identities=1))
    sybil = _anchor_only(generate_scenario(attacker_identities=10))
    assert single["poison_resistance"] == 1.0
    assert sybil["poison_resistance"] < single["poison_resistance"]


def test_correlated_source_groups_limit_known_sybil_clusters():
    ungrouped = _anchor_only(generate_scenario(attacker_identities=10))
    grouped = _anchor_only(
        generate_scenario(attacker_identities=10, correlate_attackers=True)
    )
    assert grouped["poison_resistance"] > ungrouped["poison_resistance"]


def test_the_evidence_cap_is_what_carries_the_headline_result():
    """Ablation: the other two tuned knobs are decorative on this scenario."""
    scenario = generate_scenario(seed=7)
    assert _anchor_only(scenario)["poison_resistance"] == 1.0
    uncapped = _anchor_only(scenario, max_source_evidence=5.0)
    assert uncapped["poison_resistance"] < 1.0
