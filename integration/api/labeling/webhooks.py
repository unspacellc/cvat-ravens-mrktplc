"""CVAT webhook ingestion: verify -> persist -> ack; processing is asynchronous and refetches authoritative state.

CVAT sends only `X-Signature-256: sha256=<hmac of the raw body>` (no delivery id), never retries 4xx, and may deliver
duplicates or out of order. So: (1) verify the raw bytes against the *tenant's* secret, (2) dedupe on a key derived from
the payload, (3) never act on payload content — refetch the resource, (4) rely on periodic reconciliation for drops.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .config import Settings
from .cvat_client import CvatError
from .models import LabelingJob, TenantCvatOrg, WebhookInbox
from .operations import now
from .projection import apply_cvat_job
from .service import Ctx

log = logging.getLogger(__name__)
RESOURCES = ("job", "task", "issue")


class BadSignature(Exception):
    pass


def verify_signature(secret: str, raw_body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def derive_key(payload: dict, raw_body: bytes) -> tuple[str, str | None, int | None]:
    """(dedupe_key, resource_type, resource_id). Includes the resource's `updated_date` so successive updates differ
    while redeliveries of the same change collapse. Deletes (no new updated_date) fall back to the body hash."""
    event = payload.get("event", "")
    rtype = next((r for r in RESOURCES if r in payload and isinstance(payload[r], dict)), None)
    rid = payload[rtype].get("id") if rtype else None
    stamp = payload[rtype].get("updated_date") if rtype else None
    if rid is not None and stamp and not event.startswith("delete:"):
        return f"{event}:{rid}:{stamp}", rtype, rid
    return f"{event}:{rid}:{hashlib.sha256(raw_body).hexdigest()}", rtype, rid


def receive(session: Session, settings: Settings, raw_body: bytes, signature: str | None) -> str:
    """Returns 'stored' or 'duplicate'. Raises BadSignature (-> 401) for unsigned/unknown/forged deliveries."""
    try:
        payload = json.loads(raw_body)
        hook_id = int(payload["webhook_id"])
    except (ValueError, KeyError, TypeError):
        raise BadSignature("malformed payload") from None
    org = session.scalar(select(TenantCvatOrg).where(TenantCvatOrg.cvat_instance == settings.cvat_instance, TenantCvatOrg.cvat_webhook_id == hook_id))
    if org is None or not verify_signature(settings.tenant_webhook_secret(org.tenant_id), raw_body, signature):
        raise BadSignature("signature verification failed")  # same answer for unknown hook and bad signature
    if payload.get("event") == "ping":
        return "stored"
    key, rtype, rid = derive_key(payload, raw_body)
    res = session.execute(insert(WebhookInbox).values(cvat_instance=settings.cvat_instance, dedupe_key=key, event=payload["event"],
                                                      resource_type=rtype, resource_id=rid, payload=payload)
                          .on_conflict_do_nothing(constraint="webhook_inbox_cvat_instance_dedupe_key_key")
                          .returning(WebhookInbox.id))  # RETURNING is the reliable "was inserted" signal (rowcount is driver-dependent)
    inserted = res.first() is not None
    session.commit()
    return "stored" if inserted else "duplicate"


def _refetch_job(session: Session, ctx: Ctx, cvat_job_id: int) -> str:
    job = session.scalar(select(LabelingJob).where(LabelingJob.cvat_instance == ctx.settings.cvat_instance, LabelingJob.cvat_job_id == cvat_job_id))
    if job is None:
        return "unknown job (not provisioned by this integration)"
    org = session.get(TenantCvatOrg, (job.tenant_id, job.cvat_instance))
    try:
        remote = ctx.cvat.get_job(org.cvat_org_slug, cvat_job_id)
    except CvatError as e:
        if e.status == 404:
            job.cvat_state, job.synced_at = "missing", now()  # deleted out-of-band: record the observation
            return "job missing in CVAT"
        raise
    apply_cvat_job(session, job, remote)
    return "applied"


def process_inbox(session_factory, ctx: Ctx, batch: int = 50, max_attempts: int = 8) -> int:
    done = 0
    with session_factory() as s:
        rows = s.scalars(select(WebhookInbox).where(WebhookInbox.processed_at.is_(None), WebhookInbox.attempts < max_attempts)
                         .order_by(WebhookInbox.id).limit(batch).with_for_update(skip_locked=True)).all()
        for row in rows:
            row.attempts += 1
            try:
                if row.resource_type == "job" and row.resource_id is not None:
                    _refetch_job(s, ctx, row.resource_id)
                # task/issue events: acknowledged; their projections are reconciled (Phase 2/3 consume issues)
                row.processed_at, row.error = now(), None
                done += 1
            except Exception as e:  # noqa: BLE001 - leave for retry, keep the reason
                row.error = f"{type(e).__name__}: {e}"[:500]
        s.commit()
    return done
