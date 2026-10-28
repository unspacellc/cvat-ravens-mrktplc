"""Durable operation queue in the integration DB: leases, bounded retries with backoff, idempotency keys.

Phase 1 uses the DB as the queue (SKIP LOCKED). The spec's SQS/outbox publication is a deployment concern for
Phase 6/7; handlers are queue-agnostic: they only see an Operation row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Operation

TERMINAL = {"failed", "cancelled", "succeeded"}


class IdempotencyConflict(Exception):
    """Same idempotency key reused with different input."""


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def enqueue(session: Session, *, tenant_id: str, kind: str, idempotency_key: str, payload: dict,
            subject_id: uuid.UUID | None = None, max_attempts: int = 5) -> tuple[Operation, bool]:
    h = request_hash(payload)
    existing = session.scalar(select(Operation).where(Operation.tenant_id == tenant_id, Operation.kind == kind,
                                                      Operation.idempotency_key == idempotency_key))
    if existing:
        if existing.request_hash != h:
            raise IdempotencyConflict(f"idempotency key {idempotency_key!r} reused with different input")
        return existing, False
    op = Operation(tenant_id=tenant_id, kind=kind, idempotency_key=idempotency_key, request_hash=h, subject_id=subject_id,
                   max_attempts=max_attempts, progress={})
    session.add(op)
    session.flush()
    return op, True


def claim_next(session: Session, *, lease_s: int = 300, kinds: set[str] | None = None, op_id: uuid.UUID | None = None) -> Operation | None:
    t = now()
    q = (select(Operation)
         .where(or_(Operation.state.in_(("queued", "retrying")),
                    (Operation.state == "running") & (Operation.lease_until < t)),  # crashed worker: lease expired
                Operation.run_after <= t)
         .order_by(Operation.run_after).limit(1).with_for_update(skip_locked=True))
    if kinds:
        q = q.where(Operation.kind.in_(kinds))
    if op_id:
        q = q.where(Operation.id == op_id)
    op = session.scalar(q)
    if op is None:
        return None
    op.state, op.attempts, op.lease_until = "running", op.attempts + 1, t + timedelta(seconds=lease_s)
    session.flush()
    return op


def succeed(op: Operation, result: dict | None = None) -> None:
    op.state, op.lease_until, op.error = "succeeded", None, None
    if result is not None:
        op.progress = {**op.progress, "result": result}


def fail(op: Operation, err: Exception, *, retryable: bool, backoff_base_s: float = 5.0) -> None:
    op.error = {"type": type(err).__name__, "message": str(err)[:1000], "attempt": op.attempts}
    op.lease_until = None
    if retryable and op.attempts < op.max_attempts:
        op.state = "retrying"
        op.run_after = now() + timedelta(seconds=backoff_base_s * 2 ** (op.attempts - 1))
    else:
        op.state = "failed"


def retry(op: Operation) -> None:
    """Manual recovery: re-queue a failed operation; completed steps in `progress` are skipped."""
    if op.state not in ("failed", "cancelled"):
        raise ValueError(f"cannot retry operation in state {op.state}")
    op.state, op.attempts, op.run_after, op.error = "queued", 0, now(), None


Handler = Callable[[Operation], None]


def run_one(session_factory, handlers: dict[str, Callable], *, lease_s: int = 300, op_id: uuid.UUID | None = None) -> bool:
    """Claim and run a single operation. Each handler step commits its own progress; this only manages state."""
    with session_factory() as s:
        op = claim_next(s, lease_s=lease_s, kinds=set(handlers), op_id=op_id)
        if op is None:
            return False
        s.commit()
        op_id = op.id
    with session_factory() as s:
        op = s.get(Operation, op_id)
        try:
            handlers[op.kind](s, op)
            if op.state == "running":
                succeed(op)
        except Exception as e:  # noqa: BLE001 - classify and persist, never lose the failure
            s.rollback()
            op = s.get(Operation, op_id)
            fail(op, e, retryable=getattr(e, "retryable", True))
        s.commit()
    return True
