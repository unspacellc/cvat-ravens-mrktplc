"""Preprocessing transforms and their exact inverses, so model-space boxes land on the right CVAT pixels.

The scale actually applied is derived from the rounded pasted size, not the ideal ratio, so forward and inverse agree
to floating point precision. `none` means the model sees the frame unchanged.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from PIL import Image

MODES = ("letterbox", "stretch", "none")
FILL = (114, 114, 114)


@dataclass(frozen=True)
class Transform:
    orig_w: int
    orig_h: int
    in_w: int
    in_h: int
    new_w: int  # size of the resized image inside the model canvas
    new_h: int
    pad_x: int
    pad_y: int

    @property
    def scale_x(self) -> float:
        return self.new_w / self.orig_w

    @property
    def scale_y(self) -> float:
        return self.new_h / self.orig_h

    def to_model(self, box: list[float]) -> list[float]:
        x1, y1, x2, y2 = box
        return [x1 * self.scale_x + self.pad_x, y1 * self.scale_y + self.pad_y, x2 * self.scale_x + self.pad_x, y2 * self.scale_y + self.pad_y]

    def to_orig(self, box: list[float]) -> list[float]:
        x1, y1, x2, y2 = box
        return [(x1 - self.pad_x) / self.scale_x, (y1 - self.pad_y) / self.scale_y, (x2 - self.pad_x) / self.scale_x, (y2 - self.pad_y) / self.scale_y]

    def to_json(self) -> dict:
        return asdict(self)


def make_transform(orig_w: int, orig_h: int, in_w: int, in_h: int, mode: str) -> Transform:
    if mode not in MODES:
        raise ValueError(f"unknown preprocessing mode {mode!r}")
    if mode == "none":
        return Transform(orig_w, orig_h, orig_w, orig_h, orig_w, orig_h, 0, 0)
    if mode == "stretch":
        return Transform(orig_w, orig_h, in_w, in_h, in_w, in_h, 0, 0)
    s = min(in_w / orig_w, in_h / orig_h)
    new_w, new_h = max(1, round(orig_w * s)), max(1, round(orig_h * s))
    return Transform(orig_w, orig_h, in_w, in_h, new_w, new_h, (in_w - new_w) // 2, (in_h - new_h) // 2)


def apply(image: Image.Image, t: Transform) -> Image.Image:
    img = image.convert("RGB")
    if (t.new_w, t.new_h) == (t.orig_w, t.orig_h) and (t.in_w, t.in_h) == (t.orig_w, t.orig_h):
        return img
    resized = img.resize((t.new_w, t.new_h), Image.BILINEAR)
    if (t.new_w, t.new_h) == (t.in_w, t.in_h):
        return resized
    canvas = Image.new("RGB", (t.in_w, t.in_h), FILL)
    canvas.paste(resized, (t.pad_x, t.pad_y))
    return canvas


def finite_box(box) -> bool:
    return isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box)
