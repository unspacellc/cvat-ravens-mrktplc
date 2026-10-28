"""CPU orchestration worker: operations -> webhook inbox -> periodic reconciliation. No GPU or model dependencies."""

from __future__ import annotations

import logging
import signal
import time

from api.labeling import operations, provisioning, reconcile, webhooks
from api.labeling.config import Settings
from api.labeling.cvat_client import CvatClient
from api.labeling.db import make_engine, make_session_factory
from api.labeling.fixtures import FixtureMedia, LocalClipSource
from api.labeling.service import Ctx

log = logging.getLogger("labeling.worker")
_stop = False


def build_ctx(settings: Settings, clip_root: str) -> Ctx:
    # Production binds MediaCatalog/ClipSource to the host (S3-backed); the standalone worker uses local fixtures.
    return Ctx(settings=settings, cvat=CvatClient(settings.cvat_url, settings.cvat_service_token), media=FixtureMedia({}), clips=LocalClipSource(clip_root))


def tick(session_factory, ctx: Ctx, last_reconcile: float) -> float:
    """One pass: run queued operations, drain the webhook inbox, reconcile if the interval elapsed. Returns the
    timestamp of the last reconcile."""
    handlers = {"provision_batch": provisioning.provision_batch(ctx)}
    while operations.run_one(session_factory, handlers):
        pass
    while webhooks.process_inbox(session_factory, ctx):
        pass
    if time.time() - last_reconcile >= ctx.settings.reconcile_interval_s:
        stats = reconcile.reconcile_all(session_factory, ctx, stale_after_s=ctx.settings.reconcile_interval_s / 2)
        log.info("reconcile %s", stats)
        return time.time()
    return last_reconcile


def _request_stop(*_):
    global _stop
    _stop = True


def main(clip_root: str = "fixtures/generated", poll_s: float = 2.0) -> None:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    settings = Settings()
    sf, ctx, last = make_session_factory(make_engine()), build_ctx(settings, clip_root), 0.0
    while not _stop:
        try:
            last = tick(sf, ctx, last)
        except Exception:  # noqa: BLE001 - the loop must survive transient CVAT/DB faults
            log.exception("worker tick failed")
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
