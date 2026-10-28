import pytest

from api.labeling.framemap import FrameMap

pytestmark = pytest.mark.contract

# 3 frames at 30 fps then 3 at 10 fps (variable rate)
PTS = (0.0, 1 / 30, 2 / 30, 0.1 + 0.1, 0.3, 0.4)


def test_roundtrip_with_step_and_start():
    fm = FrameMap(PTS, clip_start_offset_s=12.5, start_frame=1, step=2, variable_frame_rate=True)
    assert fm.cvat_frame_count == 3  # clip frames 1,3,5
    assert [fm.cvat_to_clip(i) for i in range(3)] == [1, 3, 5]
    assert fm.clip_to_cvat(3) == 1 and fm.clip_to_cvat(2) is None and fm.clip_to_cvat(0) is None
    assert fm.cvat_to_source_time(1) == pytest.approx(12.5 + PTS[3])
    assert fm.source_time_to_cvat(12.5 + PTS[5]) == 2


def test_nearest_and_tolerance():
    fm = FrameMap(PTS)
    assert fm.source_time_to_cvat(0.29) == 4
    assert fm.source_time_to_cvat(5.0, tolerance_s=0.05) is None


def test_json_roundtrip_and_version_gate():
    fm = FrameMap(PTS, 1.0, 0, 1, True)
    assert FrameMap.from_json(fm.to_json()) == fm
    bad = fm.to_json() | {"schema_version": 99}
    with pytest.raises(ValueError):
        FrameMap.from_json(bad)


def test_out_of_range():
    with pytest.raises(IndexError):
        FrameMap(PTS).cvat_to_clip(6)
