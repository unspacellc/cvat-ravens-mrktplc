"""A clip whose content encodes its own timeline, so pre-label alignment is checked against pixels, not metadata.

A white 40x30 box moves right at 50 px/s along y=60. It is absent for 1.0-1.3 s (empty-prediction frames). The clip is
variable frame rate (2 s @ 30 fps then 2 s @ 10 fps) with a container start offset of 1.5 s. Expected box for a frame with
presentation time `pts` is therefore a pure function of `pts - pts[0]`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SPEED, X0, Y0, W, H = 50.0, 10.0, 60.0, 40.0, 30.0
ABSENT = (1.0, 1.3)


def _segment(out: Path, rate: int, t0: float) -> None:
    flt = f"[0][1]overlay=x='{X0}+{SPEED}*(t+{t0})':y={Y0}:eval=frame:enable='not(between(t+{t0},{ABSENT[0]},{ABSENT[1]}))'"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s=320x240:r={rate}:d=2",
                    "-f", "lavfi", "-i", f"color=c=white:s={int(W)}x{int(H)}:r={rate}:d=2", "-filter_complex", flt,
                    "-pix_fmt", "yuv420p", str(out)], check=True)


def make_pixel_clip(dir_: Path) -> Path:
    out = dir_ / "pixel_vfr_offset.mp4"
    if out.exists():
        return out
    a, b, lst = dir_ / "_pa.mp4", dir_ / "_pb.mp4", dir_ / "_plist.txt"
    _segment(a, 30, 0.0)
    _segment(b, 10, 2.0)
    lst.write_text(f"file '{a}'\nfile '{b}'\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-output_ts_offset", "1.5", str(out)], check=True)
    return out


def expected_box(t: float) -> list[float] | None:
    """Box in source pixels for intrinsic time t = pts - pts[0]; None while absent (boundary frames are ambiguous: skip them)."""
    if ABSENT[0] <= t <= ABSENT[1]:
        return None
    x = X0 + SPEED * t
    return [x, Y0, x + W, Y0 + H]


def ambiguous(t: float) -> bool:
    return min(abs(t - ABSENT[0]), abs(t - ABSENT[1])) < 0.02
