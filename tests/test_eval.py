from anchor.eval import generate_scenario, run_benchmark


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
