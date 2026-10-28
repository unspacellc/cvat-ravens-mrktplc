import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from openapi_spec_validator import validate as validate_openapi
from referencing import Registry, Resource

from api.labeling import adapters, fixtures, legacy
from api.labeling.framemap import FrameMap

pytestmark = pytest.mark.contract
C = Path(__file__).resolve().parents[2] / "contracts"
SCHEMAS = {p.name.removesuffix(".schema.json"): json.loads(p.read_text()) for p in (C / "schemas").glob("*.schema.json")}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())


def validator(name):
    return Draft202012Validator(SCHEMAS[name], registry=REGISTRY, format_checker=FormatChecker())


def example(name):
    return json.loads((C / "examples" / f"{name}.json").read_text())


@pytest.mark.parametrize("name", ["frame_map", "media_reference", "dataset_manifest", "dataset_version_published_v1"])
def test_examples_validate(name):
    validator(name).validate(example(name))


@pytest.mark.parametrize("name", SCHEMAS)
def test_schemas_are_valid(name):
    Draft202012Validator.check_schema(SCHEMAS[name])


def test_frame_map_example_is_loadable_by_the_library():
    fm = FrameMap.from_json(example("frame_map"))
    assert fm.cvat_frame_count == 5


def _bad(name, mutate):
    doc = copy.deepcopy(example(name))
    mutate(doc)
    with pytest.raises(ValidationError):
        validator(name).validate(doc)


def test_manifest_rejects_bad_hash_unknown_field_and_missing_review():
    _bad("dataset_manifest", lambda d: d["artifacts"][0].__setitem__("sha256", "xyz"))
    _bad("dataset_manifest", lambda d: d.__setitem__("surprise", 1))
    _bad("dataset_manifest", lambda d: d.__setitem__("review_evidence", []))
    _bad("dataset_manifest", lambda d: d["review_evidence"][0].__setitem__("decision", "rejected"))


def test_event_is_versioned_and_strict():
    _bad("dataset_version_published_v1", lambda d: d.__setitem__("event_type", "labeling.dataset_version.published.v2"))
    _bad("dataset_version_published_v1", lambda d: d.pop("event_id"))


def test_openapi_is_valid():
    spec = yaml.safe_load((C / "openapi.yaml").read_text())
    validate_openapi(spec, base_uri=(C / "openapi.yaml").as_uri())
    ops = {m["operationId"] for p in spec["paths"].values() for m in p.values()}
    assert ops == {"createBatch", "getBatch", "listJobs", "editorLaunch", "transitionJob", "startPrelabelRun",
                   "startSendOff", "getOperation", "cvatWebhook"}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if path != "/labeling/webhooks/cvat" and method == "post" and path != "/labeling/jobs/{job_id}/editor-launch":
                names = [p.get("$ref", "").split("/")[-1] for p in op.get("parameters", [])]
                assert "IdempotencyKey" in names, f"{method} {path} must require Idempotency-Key"


def test_fixture_adapters_satisfy_protocols():
    p = adapters.Principal("u1", "t1", frozenset({"labeler"}))
    assert isinstance(fixtures.FixtureIdentity({"tok": p}), adapters.IdentityContext)
    assert isinstance(fixtures.FixtureMedia({}), adapters.MediaCatalog)
    assert isinstance(fixtures.FixtureModels({}), adapters.ModelCatalog)
    assert isinstance(fixtures.FixturePublisher(), adapters.DatasetPublisher)
    assert isinstance(fixtures.RecordingSink(), adapters.NotificationSink)


def test_media_catalog_never_crosses_tenants_and_publisher_is_idempotent():
    media = fixtures.FixtureMedia({("t1", "a"): example("media_reference")})
    assert media.get("t1", "a")["sha256"]
    with pytest.raises(KeyError):
        media.get("t2", "a")
    pub = fixtures.FixturePublisher()
    assert pub.register("t1", "v1", "s3://m", "0" * 64) == pub.register("t1", "v1", "s3://m", "0" * 64)
    assert len(pub.acks) == 1


def test_legacy_coexistence_never_collides_with_versions_and_matches_legacy_key_shape():
    key = legacy.legacy_manifest_key("abc123", "rail_defect")
    assert legacy.LEGACY_KEY_RE.match(key)
    assert not legacy.dataset_version_prefix("t1", "b", "v").startswith("ground-truth/")
    m = legacy.to_legacy_manifest(video_name="v.mp4", video_hash="abc123", model_type="rail_defect", run_id="r", saved_by="u",
                                  successes=[], failures=[], source_manifest_url="s3://x", source_output_prefix="s3://y/")
    assert {"videoName", "failures", "successes", "saved_by", "saved_at", "run_id", "model_type", "video_hash"} <= m.keys()
