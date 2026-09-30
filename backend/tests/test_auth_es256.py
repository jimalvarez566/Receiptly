import base64
import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from jose.backends.cryptography_backend import CryptographyECKey

import app.auth as auth_mod
from app.auth import CurrentUser, get_current_user

_TEST_KID = "test-es256-key"

_priv = ec.generate_private_key(ec.SECP256R1())
_PRIV_PEM = _priv.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
_PUB_JWK = CryptographyECKey(_priv.public_key(), "ES256").to_dict()
_PUB_JWK["kid"] = _TEST_KID


def make_es256_token(
    sub: str,
    *,
    kid: str = _TEST_KID,
    exp_delta: int = 3600,
    aud: str = "authenticated",
    email: str = "es@example.com",
) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "email": email, "aud": aud, "exp": now + exp_delta, "iat": now},
        _PRIV_PEM,
        algorithm="ES256",
        headers={"kid": kid},
    )


@pytest.fixture(autouse=True)
def _clear_jwks_cache():
    auth_mod._jwks_cache.clear()
    auth_mod._jwks_last_fetch_attempt = 0.0
    yield
    auth_mod._jwks_cache.clear()
    auth_mod._jwks_last_fetch_attempt = 0.0


@pytest.fixture
def probe_client():
    probe = FastAPI()

    @probe.get("/whoami")
    async def whoami(user: CurrentUser = Depends(get_current_user)):
        return {"user_id": str(user.user_id), "email": user.email}

    transport = ASGITransport(app=probe)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_valid_es256_token_verifies_from_cache(probe_client):
    uid = str(uuid.uuid4())
    auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK
    token = make_es256_token(uid)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid


async def test_valid_es256_token_triggers_one_jwks_fetch(probe_client, monkeypatch):
    uid = str(uuid.uuid4())
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return {_TEST_KID: _PUB_JWK}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
    token = make_es256_token(uid)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert calls["n"] == 1


async def test_unknown_kid_is_401(probe_client, monkeypatch):
    async def fake_fetch():
        return {"another-kid": _PUB_JWK}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
    token = make_es256_token(str(uuid.uuid4()), kid="missing-kid")
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_alg_none_is_401(probe_client):
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    token = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"sub": "x", "aud": "authenticated"}) + "."
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_expired_es256_token_is_401(probe_client):
    auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK
    token = make_es256_token(str(uuid.uuid4()), exp_delta=-60)
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_jwks_fetch_failure_is_401(probe_client, monkeypatch):
    async def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(auth_mod, "_fetch_jwks", boom)
    token = make_es256_token(str(uuid.uuid4()))
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_hs256_token_still_verifies(probe_client):
    from tests.conftest import make_token

    uid = str(uuid.uuid4())
    token = make_token(uid, "hs@example.com")
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == uid


async def test_wrong_signing_key_is_401(probe_client):
    """A token claiming the legitimate kid but signed by a different private
    key must fail signature verification, not just kid lookup."""
    other_priv = ec.generate_private_key(ec.SECP256R1())
    other_pem = other_priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    now = int(time.time())
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": now + 3600, "iat": now},
        other_pem,
        algorithm="ES256",
        headers={"kid": _TEST_KID},  # claims the real kid, signed by a different key
    )
    auth_mod._jwks_cache[_TEST_KID] = _PUB_JWK  # cache holds the REAL public key
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401


async def test_unsupported_alg_is_401(probe_client):
    """A syntactically well-formed but unsupported alg (e.g. RS256) is
    rejected by the allowlist before any signature verification is attempted."""
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    now = int(time.time())
    token = (
        b64({"alg": "RS256", "typ": "JWT", "kid": "whatever"})
        + "."
        + b64({"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": now + 3600})
        + ".not-a-real-signature"
    )
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_es256_token_missing_kid_is_401(probe_client):
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "authenticated", "exp": now + 3600, "iat": now},
        _PRIV_PEM,
        algorithm="ES256",
        # no `kid` header
    )
    async with probe_client as c:
        resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_filter_signing_keys_excludes_non_ec_and_non_signing():
    ec_sig = {"kid": "ec-sig", "kty": "EC", "use": "sig"}
    ec_no_use = {"kid": "ec-default-use", "kty": "EC"}  # use defaults to "sig"
    rsa_sig = {"kid": "rsa-sig", "kty": "RSA", "use": "sig"}
    ec_enc = {"kid": "ec-enc", "kty": "EC", "use": "enc"}
    no_kid = {"kty": "EC", "use": "sig"}

    result = auth_mod._filter_signing_keys([ec_sig, ec_no_use, rsa_sig, ec_enc, no_kid])

    assert set(result) == {"ec-sig", "ec-default-use"}


async def test_unknown_kid_refetch_is_throttled(probe_client, monkeypatch):
    """Repeated requests with an unknown kid must not each trigger a fetch."""
    calls = {"n": 0}

    async def fake_fetch():
        calls["n"] += 1
        return {"another-kid": _PUB_JWK}

    monkeypatch.setattr(auth_mod, "_fetch_jwks", fake_fetch)
    token = make_es256_token(str(uuid.uuid4()), kid="missing-kid")

    async with probe_client as c:
        for _ in range(3):
            resp = await c.get("/whoami", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 401

    assert calls["n"] == 1  # only the first request actually hit the network
