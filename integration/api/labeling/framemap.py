"""Source-frame <-> clip-frame <-> CVAT-frame mapping with explicit timestamps.

CVAT exposes no per-frame timestamps for video (`/data/meta` has one `frames` entry), so the integration
persists its own map built from ffprobe presentation timestamps of the clip handed to CVAT.

Index conventions
- `clip_frame`: 0-based decode-order index into the clip file as a decoder emits frames.
- `cvat_frame`: CVAT's frame number. CVAT applies `start_frame` and `frame_filter=step=N`, so
  `clip_frame = start_frame + cvat_frame * step`.
- `source_time_s`: seconds on the original source asset's timeline = `clip_start_offset_s + pts_s[clip_frame]`.
"""

from __future__ import annotations

import bisect
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class FrameMap:
    pts_s: tuple[float, ...]  # presentation time of every decoded clip frame, seconds, ascending
    clip_start_offset_s: float = 0.0  # where the clip starts on the source asset timeline
    start_frame: int = 0  # CVAT task data start_frame (clip index)
    step: int = 1  # CVAT frame_filter step
    variable_frame_rate: bool = False
    schema_version: int = field(default=SCHEMA_VERSION)

    @property
    def cvat_frame_count(self) -> int:
        n = len(self.pts_s)
        return max(0, (n - self.start_frame + self.step - 1) // self.step)

    def cvat_to_clip(self, cvat_frame: int) -> int:
        if not 0 <= cvat_frame < self.cvat_frame_count:
            raise IndexError(f"cvat frame {cvat_frame} outside 0..{self.cvat_frame_count - 1}")
        return self.start_frame + cvat_frame * self.step

    def clip_to_cvat(self, clip_frame: int) -> int | None:
        """None if CVAT does not expose this clip frame (before start or filtered out by step)."""
        rel = clip_frame - self.start_frame
        if rel < 0 or rel % self.step or clip_frame >= len(self.pts_s):
            return None
        return rel // self.step

    def cvat_to_source_time(self, cvat_frame: int) -> float:
        return self.clip_start_offset_s + self.pts_s[self.cvat_to_clip(cvat_frame)]

    def source_time_to_cvat(self, source_time_s: float, *, tolerance_s: float | None = None) -> int | None:
        """Nearest exposed CVAT frame at or before `source_time_s`'s nearest clip frame; None if outside tolerance."""
        t = source_time_s - self.clip_start_offset_s
        exposed = [self.start_frame + i * self.step for i in range(self.cvat_frame_count)]
        times = [self.pts_s[c] for c in exposed]
        i = bisect.bisect_left(times, t)
        cands = [j for j in (i - 1, i) if 0 <= j < len(times)]
        if not cands:
            return None
        best = min(cands, key=lambda j: abs(times[j] - t))
        if tolerance_s is not None and abs(times[best] - t) > tolerance_s:
            return None
        return best

    def to_json(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "pts_s": list(self.pts_s),
            "clip_start_offset_s": self.clip_start_offset_s,
            "start_frame": self.start_frame,
            "step": self.step,
            "variable_frame_rate": self.variable_frame_rate,
        }

    @classmethod
    def from_json(cls, d: dict) -> FrameMap:
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported frame map schema {d.get('schema_version')}")
        return cls(tuple(d["pts_s"]), d["clip_start_offset_s"], d["start_frame"], d["step"], d["variable_frame_rate"])


def probe_pts(path: Path) -> list[float]:
    """Presentation timestamps (sorted) of every video frame, via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time,best_effort_timestamp_time",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    frames = json.loads(out)["frames"]
    return sorted(float(f.get("pts_time") or f["best_effort_timestamp_time"]) for f in frames)


def build_frame_map(
    clip: Path, *, clip_start_offset_s: float = 0.0, start_frame: int = 0, step: int = 1, vfr_tolerance: float = 0.05
) -> FrameMap:
    pts = probe_pts(clip)
    if len(pts) < 2:
        raise ValueError("clip has fewer than 2 frames")
    deltas = [b - a for a, b in zip(pts, pts[1:], strict=False)]
    vfr = (max(deltas) - min(deltas)) / max(deltas) > vfr_tolerance
    return FrameMap(tuple(pts), clip_start_offset_s, start_frame, step, vfr)
