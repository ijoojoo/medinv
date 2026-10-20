import logging
from typing import Any, Protocol, cast

import requests
from authlib.integrations.requests_client import OAuth2Session
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import KeySet, KeySetSerialization

from medinv.platform.auth.config import AuthSettings
from medinv.platform.errors import BusinessError


class IdentityProvider(Protocol):
    def authorization_url(self, state: str, nonce: str, verifier: str) -> str: ...
    def exchange(self, code: str, verifier: str, nonce: str) -> dict[str, Any]: ...
    def refresh(self, tokens: dict[str, Any]) -> dict[str, Any]: ...
    def revoke(self, tokens: dict[str, Any]) -> None: ...


class KeycloakProvider:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        self.metadata = self._get(settings.oidc_issuer_url + "/.well-known/openid-configuration")
        if self.metadata.get("issuer") != settings.oidc_issuer_url:
            raise RuntimeError("OIDC discovery issuer mismatch")
        for field in (
            "authorization_endpoint",
            "token_endpoint",
            "jwks_uri",
            "revocation_endpoint",
        ):
            if not str(self.metadata.get(field, "")).startswith(settings.oidc_issuer_url + "/"):
                raise RuntimeError("Unexpected Keycloak endpoint")
        self.keys = KeySet.import_key_set(
            cast(KeySetSerialization, self._get(self.metadata["jwks_uri"]))
        )

    @staticmethod
    def _get(url: str) -> dict[str, Any]:
        response = requests.get(url, timeout=(3, 5), allow_redirects=False)
        response.raise_for_status()
        return dict(response.json())

    def _client(self) -> OAuth2Session:
        return OAuth2Session(
            self.settings.oidc_client_id,
            self.settings.oidc_client_secret.get_secret_value(),
            scope="openid profile",
            redirect_uri=self.settings.callback_url,
            code_challenge_method="S256",
            token_endpoint_auth_method="client_secret_basic",
        )

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        with self._client() as client:
            url, _ = client.create_authorization_url(
                self.metadata["authorization_endpoint"],
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                prompt="login",
            )
            return str(url)

    def validate_id_token(
        self, token: dict[str, Any], nonce: str, *, refreshing: bool = False
    ) -> dict[str, Any]:
        decoded = jwt.decode(token["id_token"], self.keys, algorithms=["RS256"])
        claims = CodeIDToken(
            decoded.claims,
            decoded.header,
            options={
                "iss": {"essential": True, "value": self.settings.oidc_issuer_url},
                "aud": {"essential": True, "value": self.settings.oidc_client_id},
            },
            params={
                # OIDC Core 12.2 permits an omitted nonce only on refresh responses.
                "nonce": None if refreshing and "nonce" not in decoded.claims else nonce,
                "client_id": self.settings.oidc_client_id,
                "access_token": token["access_token"],
            },
        )
        claims.validate(leeway=30)
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 200:
            raise ValueError("Invalid subject")
        return dict(claims)

    def exchange(self, code: str, verifier: str, nonce: str) -> dict[str, Any]:
        try:
            with self._client() as client:
                token = dict(
                    client.fetch_token(
                        self.metadata["token_endpoint"],
                        code=code,
                        code_verifier=verifier,
                        timeout=(3, 5),
                        allow_redirects=False,
                    )
                )
            # Fetch signing keys for every login to include rotations without trusting JWT URLs.
            self.keys = KeySet.import_key_set(
                cast(KeySetSerialization, self._get(self.metadata["jwks_uri"]))
            )
            claims = self.validate_id_token(token, nonce)
            return {
                **token,
                "subject": claims["sub"],
                "nonce": nonce,
                "identity_claims": {
                    k: claims[k] for k in ("aud", "azp", "auth_time") if k in claims
                },
            }
        except Exception as exc:
            raise BusinessError("OIDC_LOGIN_FAILED", "登录验证失败，请重新登录", 401) from exc

    def refresh(self, tokens: dict[str, Any]) -> dict[str, Any]:
        try:
            with self._client() as client:
                fresh = dict(
                    client.refresh_token(
                        self.metadata["token_endpoint"],
                        refresh_token=tokens["refresh_token"],
                        timeout=(3, 5),
                        allow_redirects=False,
                    )
                )
            merged = {**tokens, **fresh}
            if "id_token" in fresh:
                self.keys = KeySet.import_key_set(
                    cast(KeySetSerialization, self._get(self.metadata["jwks_uri"]))
                )
                claims = self.validate_id_token(merged, tokens["nonce"], refreshing=True)
                if claims["sub"] != tokens["subject"]:
                    raise ValueError("Refresh changed subject")
                original = tokens["identity_claims"]

                def audiences(value: Any) -> set[str]:
                    return {value} if isinstance(value, str) else set(value)

                if audiences(claims["aud"]) != audiences(original["aud"]):
                    raise ValueError("Refresh changed audience")
                if claims.get("azp") != original.get("azp"):
                    raise ValueError("Refresh changed authorized party")
                if "auth_time" in claims and claims["auth_time"] != original.get("auth_time"):
                    raise ValueError("Refresh changed authentication time")
            return merged
        except Exception as exc:
            logging.getLogger("medinv.auth").warning(
                "oidc_refresh_failed class=%s", type(exc).__name__
            )
            raise BusinessError("REAUTHENTICATE", "会话刷新失败，请重新登录", 401) from exc

    def revoke(self, tokens: dict[str, Any]) -> None:
        with self._client() as client:
            client.revoke_token(  # type: ignore[no-untyped-call]
                self.metadata["revocation_endpoint"],
                token=tokens.get("refresh_token"),
                token_type_hint="refresh_token",
                timeout=(3, 5),
                allow_redirects=False,
            )
