"""Inference backends for batch pre-labeling.

`InferenceBackend` is deliberately tiny: submit (idempotent on `inference_id`), poll, cancel. The SageMaker async
adapter and the deterministic offline fake satisfy the same contract, so the orchestration is tested once.
Wire format (versioned, `contracts/schemas/prelabel_*.schema.json`):
  input  = {"schema":1,"run_id","seq","frames":[{"frame_id","source_time_s","sha256","width","height","image_png_b64"}]}
  output = {"schema":1,"frames":[{"frame_id","detections":[{"label","score","box":[x1,y1,x2,y2]}]}]}
Boxes are in *model input* coordinates (the preprocessed image); the orchestrator applies the inverse transform.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass, field
from typing import Protocol

from PIL import Image


@dataclass(frozen=True)
class PollResult:
    state: str  # pending | succeeded | failed
    output: bytes | None = None
    error: str | None = None


class InferenceBackend(Protocol):
    def submit(self, *, inference_id: str, payload: bytes) -> None:
        """Idempotent: re-submitting the same `inference_id` must not enqueue a second inference."""

    def poll(self, inference_id: str) -> PollResult: ...

    def cancel(self, inference_id: str) -> None:
        """Best effort; late results for a cancelled run are ignored by the orchestrator regardless."""


@dataclass
class FakeBackend:
    """Offline deterministic 'model': finds the bright rectangle in each (already preprocessed) frame, so any frame-index,
    time-mapping or transform error produces visibly wrong boxes. Failure injection is explicit and per inference id."""

    label: str = "vehicle"
    polls_until_ready: int = 1
    extra: list[dict] = field(default_factory=list)  # appended to every frame, e.g. an unknown-label detection
    detect: bool = True  # False: a healthy model that finds nothing (zero detections, not a failure)
    fail_all: bool = False  # every inference fails (endpoint/container error)
    transient_failures: int = 0  # the next N completions fail once, then succeed on re-submission
    corrupt: bool = False  # responses omit a requested frame (malformed, must not read as "no detections")
    max_inflight_seen: int = 0
    submissions: list[str] = field(default_factory=list)
    payloads: dict[str, dict] = field(default_factory=dict)
    cancelled: set[str] = field(default_factory=set)
    _polls: dict[str, int] = field(default_factory=dict)
    _results: dict[str, PollResult] = field(default_factory=dict)

    def submit(self, *, inference_id: str, payload: bytes) -> None:
        if inference_id in self._results or inference_id in self._polls:
            return  # idempotent
        self.submissions.append(inference_id)
        self.payloads[inference_id] = json.loads(payload)
        self._polls[inference_id] = 0
        self.max_inflight_seen = max(self.max_inflight_seen, sum(1 for i in self._polls if i not in self._results))

    def poll(self, inference_id: str) -> PollResult:
        if inference_id in self._results:
            return self._results[inference_id]
        if inference_id not in self._polls:
            return PollResult("failed", error="unknown inference id")
        self._polls[inference_id] += 1
        if self._polls[inference_id] <= self.polls_until_ready:
            return PollResult("pending")
        if self.transient_failures > 0:
            self.transient_failures -= 1
            res = PollResult("failed", error="transient endpoint error")
        elif self.fail_all:
            res = PollResult("failed", error="model container error")
        else:
            res = PollResult("succeeded", output=json.dumps(self._detect(self.payloads[inference_id])).encode())
        self._results[inference_id] = res
        return res

    def cancel(self, inference_id: str) -> None:
        self.cancelled.add(inference_id)

    def _detect(self, payload: dict) -> dict:
        frames = []
        for f in payload["frames"]:
            img = Image.open(io.BytesIO(base64.b64decode(f["image_png_b64"]))).convert("L")
            box = img.point(lambda v: 255 if v > 128 else 0).getbbox()
            dets = [] if box is None or not self.detect else [{"label": self.label, "score": 0.9, "box": [float(v) for v in box]}]
            frames.append({"frame_id": f["frame_id"], "detections": dets + (list(self.extra) if self.detect else [])})
        if self.corrupt and frames:
            frames.pop()
        return {"schema": 1, "frames": frames}


class SageMakerAsyncBackend:
    """SageMaker asynchronous inference: input is staged in S3, `InvokeEndpointAsync` references it with a caller-chosen
    `InferenceId` (so a retry after an ambiguous timeout does not create a second job), and completion is observed as an
    object appearing at the output (or failure) location. UNVERIFIED against a real endpoint (see phase-4.md)."""

    def __init__(self, *, endpoint_name: str, bucket: str, prefix: str, region: str, sagemaker_runtime=None, s3=None):
        import boto3

        self.endpoint, self.bucket, self.prefix = endpoint_name, bucket, prefix.strip("/")
        self.rt = sagemaker_runtime or boto3.client("sagemaker-runtime", region_name=region)
        self.s3 = s3 or boto3.client("s3", region_name=region)

    def _in_key(self, inference_id: str) -> str:
        return f"{self.prefix}/in/{inference_id}.json"

    def _loc_key(self, inference_id: str) -> str:
        return f"{self.prefix}/locations/{inference_id}.json"

    def submit(self, *, inference_id: str, payload: bytes) -> None:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._loc_key(inference_id))
            return  # already submitted
        except Exception as e:  # noqa: BLE001 - botocore ClientError 404 => not submitted yet
            if getattr(e, "response", {}).get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
                raise
        self.s3.put_object(Bucket=self.bucket, Key=self._in_key(inference_id), Body=payload, ContentType="application/json")
        resp = self.rt.invoke_endpoint_async(EndpointName=self.endpoint, InputLocation=f"s3://{self.bucket}/{self._in_key(inference_id)}",
                                             InferenceId=inference_id, ContentType="application/json", Accept="application/json")
        loc = {"output": resp["OutputLocation"], "failure": resp.get("FailureLocation")}
        self.s3.put_object(Bucket=self.bucket, Key=self._loc_key(inference_id), Body=json.dumps(loc).encode())

    @staticmethod
    def _split(uri: str) -> tuple[str, str]:
        b, _, k = uri.removeprefix("s3://").partition("/")
        return b, k

    def _exists(self, uri: str) -> bool:
        b, k = self._split(uri)
        try:
            self.s3.head_object(Bucket=b, Key=k)
            return True
        except Exception as e:  # noqa: BLE001
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def poll(self, inference_id: str) -> PollResult:
        loc = json.loads(self.s3.get_object(Bucket=self.bucket, Key=self._loc_key(inference_id))["Body"].read())
        if self._exists(loc["output"]):
            b, k = self._split(loc["output"])
            return PollResult("succeeded", output=self.s3.get_object(Bucket=b, Key=k)["Body"].read())
        if loc.get("failure") and self._exists(loc["failure"]):
            b, k = self._split(loc["failure"])
            return PollResult("failed", error=self.s3.get_object(Bucket=b, Key=k)["Body"].read().decode(errors="replace")[:500])
        return PollResult("pending")

    def cancel(self, inference_id: str) -> None:
        return  # SageMaker async has no cancel API; the orchestrator discards late results
