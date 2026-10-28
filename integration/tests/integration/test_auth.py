"""JWT verification + server-side membership resolution. Tokens are really RS256-signed; keys come from a local keypair
standing in for the issuer's JWKS, so signature, expiry, issuer and audience checks are the production code paths."""

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from api.labeling.auth import JwtConfig, JwtIdentity
from api.labeling.models import LabelingMember

ISSUER = "https://issuer.test/pool"
CLIENT = "portal-client"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Keys:
    def __init__(self, key):
        self.pub = key.public_key()

    def signing_key(self, token):
        return self.pub


@pytest.fixture(scope="module")
def signing():
    return _key()


@pytest.fixture
def ident(session_factory, signing):
    return JwtIdentity(JwtConfig(ISSUER, (CLIENT,)), Keys(signing), session_factory)


def token(key, sub, *, iss=ISSUER, client=CLIENT, exp=300, use="access", **extra):
    claims = {"sub": sub, "iss": iss, "client_id": client, "token_use": use, "exp": int(time.time()) + exp, **extra}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def member(session_factory):
    sub = f"sub-{uuid.uuid4().hex[:8]}"
    t1, t2 = f"t-{uuid.uuid4().hex[:6]}", f"t-{uuid.uuid4().hex[:6]}"
    with session_factory() as s:
        s.add(LabelingMember(tenant_id=t1, user_id=sub, roles=["labeler"]))
        s.commit()
    return sub, t1, t2


def test_valid_token_resolves_membership_not_claims(ident, signing, member):
    sub, t1, _ = member
    p = ident.resolve(token(signing, sub, tenant_id="other-tenant", roles="admin"))  # hostile claims are ignored
    assert (p.user_id, p.tenant_id, p.roles) == (sub, t1, frozenset({"labeler"}))


@pytest.mark.parametrize("kw", [{"exp": -10}, {"iss": "https://evil.test"}, {"client": "other-client"}, {"use": "id"}])
def test_rejects_expired_wrong_issuer_audience_or_token_use(ident, signing, member, kw):
    with pytest.raises(PermissionError):
        ident.resolve(token(signing, member[0], **kw))


def test_rejects_bad_signature_and_alg_none(ident, member):
    with pytest.raises(PermissionError):
        ident.resolve(token(_key(), member[0]))
    unsigned = jwt.encode({"sub": member[0], "iss": ISSUER, "client_id": CLIENT, "exp": int(time.time()) + 60}, None, algorithm="none")
    with pytest.raises(PermissionError):
        ident.resolve(unsigned)


def test_unknown_or_removed_member_denied(ident, signing, session_factory, member):
    sub, t1, _ = member
    with pytest.raises(PermissionError):
        ident.resolve(token(signing, "nobody"))
    with session_factory() as s:
        s.get(LabelingMember, (t1, sub)).status = "removed"
        s.commit()
    with pytest.raises(PermissionError):
        ident.resolve(token(signing, sub))


def test_tenant_switching_requires_membership(ident, signing, session_factory, member):
    sub, t1, t2 = member
    with session_factory() as s:
        s.add(LabelingMember(tenant_id=t2, user_id=sub, roles=["admin"]))
        s.commit()
    tok = token(signing, sub)
    with pytest.raises(PermissionError, match="tenant selection"):
        ident.resolve(tok)  # ambiguous without a hint
    assert ident.resolve(tok, t2).roles >= {"admin", "labeler", "reviewer", "creator"}
    assert ident.resolve(tok, t1).roles == frozenset({"labeler"})
    with pytest.raises(PermissionError):
        ident.resolve(tok, "not-a-tenant-of-mine")
