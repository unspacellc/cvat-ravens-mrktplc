"""SageMaker async adapter against fake boto clients. This proves request shape and idempotency only; it is NOT evidence
that a real endpoint works (see docs/phases/phase-4.md)."""

import json

import pytest
from botocore.exceptions import ClientError

from api.labeling.inference import SageMakerAsyncBackend


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[(Bucket, Key)] = Body

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


class FakeRuntime:
    def __init__(self):
        self.calls = []

    def invoke_endpoint_async(self, **kw):
        self.calls.append(kw)
        return {"OutputLocation": f"s3://out-bucket/out/{kw['InferenceId']}.out", "FailureLocation": f"s3://out-bucket/fail/{kw['InferenceId']}.err"}


@pytest.fixture
def be():
    s3, rt = FakeS3(), FakeRuntime()
    return SageMakerAsyncBackend(endpoint_name="vehicle-det", bucket="in-bucket", prefix="/prelabel/", region="us-east-1", sagemaker_runtime=rt, s3=s3), s3, rt


def test_submit_stages_input_and_invokes_once_per_inference_id(be):
    b, s3, rt = be
    b.submit(inference_id="abc", payload=b'{"x":1}')
    b.submit(inference_id="abc", payload=b'{"x":1}')  # ambiguous-timeout retry: same id => no second invocation
    assert len(rt.calls) == 1
    call = rt.calls[0]
    assert call["EndpointName"] == "vehicle-det" and call["InferenceId"] == "abc" and call["InputLocation"] == "s3://in-bucket/prelabel/in/abc.json"
    assert s3.objects[("in-bucket", "prelabel/in/abc.json")] == b'{"x":1}'


def test_poll_pending_then_success_or_failure(be):
    b, s3, rt = be
    b.submit(inference_id="ok", payload=b"{}")
    assert b.poll("ok").state == "pending"
    s3.put_object("out-bucket", "out/ok.out", json.dumps({"schema": 1, "frames": []}).encode())
    res = b.poll("ok")
    assert res.state == "succeeded" and json.loads(res.output)["schema"] == 1
    b.submit(inference_id="bad", payload=b"{}")
    s3.put_object("out-bucket", "fail/bad.err", b"ModelError: OOM")
    res = b.poll("bad")
    assert res.state == "failed" and "OOM" in res.error


def test_non_404_s3_errors_are_not_swallowed(be):
    b, s3, rt = be

    def boom(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
    s3.head_object = boom
    with pytest.raises(ClientError):
        b.submit(inference_id="x", payload=b"{}")
