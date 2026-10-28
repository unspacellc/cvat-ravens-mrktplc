"""Shared harness for portal-API tests: real RS256 tokens verified by the production JwtIdentity, real CVAT behind it."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from api.labeling import operations, provisioning, service
from api.labeling.app import create_app
from api.labeling.auth import JwtConfig, JwtIdentity
from api.labeling.models import LabelingMember

ISSUER, CLIENT = "https://issuer.test/pool", "portal-client"
LABELS = [{"name": "car", "type": "rectangle", "attributes": []}]


class _Keys:
    def __init__(self, key):
        self._pub = key.public_key()

    def signing_key(self, token):
        return self._pub


@dataclass
class Portal:
    client: TestClient
    key: object
    ctx: service.Ctx
    session_factory: object

    def token(self, sub: str, *, exp: int = 300) -> str:
        claims = {"sub": sub, "iss": ISSUER, "client_id": CLIENT, "token_use": "access", "exp": int(time.time()) + exp}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def call(self, method: str, path: str, sub: str | None, tenant: str | None = None, **kw):
        headers = kw.pop("headers", {})
        if sub is not None:
            headers["Authorization"] = f"Bearer {self.token(sub)}"
        if tenant:
            headers["X-Labeling-Tenant"] = tenant
        return self.client.request(method, f"/api/v1/labeling{path}", headers=headers, **kw)

    def add_member(self, tenant: str, sub: str, *roles: str):
        with self.session_factory() as s:
            s.merge(LabelingMember(tenant_id=tenant, user_id=sub, roles=sorted(roles), status="active", email=f"{sub}@example.com"))
            s.commit()

    def run_operations(self, op_id):
        h = {"provision_batch": provisioning.provision_batch(self.ctx)}
        while operations.run_one(self.session_factory, h, op_id=uuid.UUID(str(op_id))):
            pass


def make_portal(session_factory, ctx: service.Ctx) -> Portal:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity = JwtIdentity(JwtConfig(ISSUER, (CLIENT,)), _Keys(key), session_factory)
    app = create_app(ctx.settings, session_factory, identity=identity, ctx=ctx)
    return Portal(TestClient(app), key, ctx, session_factory)


def make_batch(portal: Portal, tenant: str, admin_sub: str, asset: str, name: str = "b") -> dict:
    with portal.session_factory() as s:
        schema = service.register_schema(s, tenant, "1.0.0", LABELS)
        s.commit()
        schema_id = str(schema.id)
    r = portal.call("POST", "/batches", admin_sub, json={"name": name, "asset_ids": [asset], "schema_version_id": schema_id},
                    headers={"Idempotency-Key": f"k-{tenant}"})
    assert r.status_code == 202, r.text
    portal.run_operations(r.json()["operation_id"])
    return r.json()
