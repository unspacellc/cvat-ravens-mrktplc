from api.labeling.metrics import LIMITATIONS, METHOD, correction_metrics


def box(x, y=0, w=40, h=30):
    return [x, y, x + w, y + h]


def b(frame, x, label="car"):
    return {"frame": frame, "label": label, "box": box(x)}


def test_unchanged_edited_deleted_added_and_rate():
    base = [b(0, 10), b(0, 100), b(1, 10), b(2, 10)]
    final = [b(0, 10), b(0, 108), b(1, 10, "truck"), b(5, 50)]  # kept; nudged; relabeled; (frame-2 box deleted); new box on frame 5
    r = correction_metrics(base, final)
    assert (r["baseline"], r["unchanged"], r["edited"], r["deleted"], r["added"]) == (4, 1, 2, 1, 1)
    assert r["correction_rate"] == 3 / 4


def test_zero_baseline_has_no_rate_and_additions_are_not_corrections():
    r = correction_metrics([], [b(0, 10)])
    assert r["correction_rate"] is None and r["added"] == 1 and r["baseline"] == 0


def test_a_merge_is_ambiguous_not_a_deletion():
    base = [b(0, 10), b(0, 20)]  # two predictions...
    final = [{"frame": 0, "label": "car", "box": [10, 0, 60, 30]}]  # ...replaced by one box overlapping both
    r = correction_metrics(base, final)
    assert r["ambiguous"] + r["deleted"] + r["edited"] + r["unchanged"] == 2 and r["ambiguous"] >= 1


def test_method_is_versioned_and_limits_are_stated():
    assert METHOD["version"] == 1 and any("Time-on-task" in x for x in LIMITATIONS)
