import time
from uuid import uuid4

import pytest
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet, RSAKey
from pydantic import SecretStr

from medinv.platform.auth.config import AuthSettings
from medinv.platform.auth.provider import KeycloakProvider


@pytest.fixture
def provider():
    settings = AuthSettings(
        environment="test",
        public_origin="https://app.example",
        control_auth_database_url=SecretStr("unused"),
        oidc_issuer_url="https://idp.example/realms/test",
        oidc_client_secret=SecretStr("secret"),
        session_encryption_key=SecretStr("unused"),
    )
    service = KeycloakProvider.__new__(KeycloakProvider)
    service.settings = settings
    key = RSAKey.generate_key(2048)
    service.keys = KeySet([key])
    return service, key


@pytest.mark.parametrize("bad", ["nonce", "issuer", "audience", "expired", "signature"])
def test_oidc_rejects_forged_claims(provider, bad):
    service, key = provider
    claims = {
        "iss": service.settings.oidc_issuer_url,
        "sub": str(uuid4()),
        "aud": "medinv-web",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "expected",
    }
    if bad == "nonce":
        claims["nonce"] = "wrong"
    if bad == "issuer":
        claims["iss"] = "https://evil.invalid"
    if bad == "audience":
        claims["aud"] = "other-client"
    if bad == "expired":
        claims["exp"] = int(time.time()) - 300
    if bad == "signature":
        key = RSAKey.generate_key(2048)
    token = jwt.encode({"alg": "RS256"}, claims, key)
    with pytest.raises(JoseError):
        service.validate_id_token({"id_token": token, "access_token": "access"}, "expected")


def test_oidc_accepts_valid_signed_token(provider):
    service, key = provider
    claims = {
        "iss": service.settings.oidc_issuer_url,
        "sub": str(uuid4()),
        "aud": "medinv-web",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": "expected",
    }
    token = jwt.encode({"alg": "RS256"}, claims, key)
    assert (
        service.validate_id_token({"id_token": token, "access_token": "access"}, "expected")["sub"]
        == claims["sub"]
    )


@pytest.mark.parametrize(
    "refreshing,nonce,accepted",
    [
        (False, None, False),
        (True, None, True),
        (True, "wrong", False),
        (True, "expected", True),
    ],
)
def test_refresh_nonce_is_optional_but_must_match_if_present(provider, refreshing, nonce, accepted):
    service, key = provider
    claims = {
        "iss": service.settings.oidc_issuer_url,
        "sub": str(uuid4()),
        "aud": "medinv-web",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    if nonce is not None:
        claims["nonce"] = nonce
    token = {"id_token": jwt.encode({"alg": "RS256"}, claims, key), "access_token": "access"}
    if accepted:
        assert (
            service.validate_id_token(token, "expected", refreshing=refreshing)["sub"]
            == claims["sub"]
        )
    else:
        with pytest.raises(JoseError):
            service.validate_id_token(token, "expected", refreshing=refreshing)
