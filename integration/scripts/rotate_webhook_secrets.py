"""Re-keys every tenant's CVAT webhook after the webhook master secret changed. See docs/runbooks/release-operations.md.
  LABELING_WEBHOOK_MASTER_SECRET=<new> LABELING_WEBHOOK_PREVIOUS_MASTER_SECRET=<old> CVAT_SERVICE_TOKEN=... \\
    uv run python scripts/rotate_webhook_secrets.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.labeling import provisioning  # noqa: E402
from api.labeling.config import Settings  # noqa: E402
from api.labeling.cvat_client import CvatClient  # noqa: E402
from api.labeling.db import make_engine, make_session_factory  # noqa: E402
from api.labeling.fixtures import FixtureMedia, LocalClipSource  # noqa: E402
from api.labeling.service import Ctx  # noqa: E402

if __name__ == "__main__":
    settings = Settings()
    ctx = Ctx(settings=settings, cvat=CvatClient(settings.cvat_url, settings.cvat_service_token), media=FixtureMedia({}), clips=LocalClipSource("."))
    with make_session_factory(make_engine())() as s:
        result = provisioning.rotate_webhook_secrets(s, ctx)
    print(json.dumps(result))
    sys.exit(1 if result["failed"] else 0)
