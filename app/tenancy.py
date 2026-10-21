import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from azure.cosmos.aio import ContainerProxy
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.models import Plan, Tenant

LOGIN = "https://login.microsoftonline.com"
CREDENTIAL_ISSUER = "azure-ai-saas-platform"
JWKS_DOCUMENT, JWKS_PARTITION = "jwks", "platform"
EXEMPT = ("/healthz", "/console")


class AccessError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int = 0) -> None:
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant: Tenant | None = None
    admin: bool = False

    def require_tenant(self) -> Tenant:
        if self.tenant is None:
            raise AccessError(403, "this operation needs a tenant credential")
        return self.tenant


class KeySource(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


class Resolving(Protocol):
    async def resolve(self, authorization: str | None) -> Principal: ...


class TenantStore(Protocol):
    async def tenant(self, tenant_id: str) -> Tenant | None: ...
    async def credential_revoked(self, tenant_id: str, digest: str) -> bool | None: ...
    async def signing_keys(self) -> list[dict[str, Any]]: ...


class CosmosTenantStore:
    def __init__(self, tenants: ContainerProxy, credentials: ContainerProxy) -> None:
        self.tenants, self.credentials = tenants, credentials

    async def tenant(self, tenant_id: str) -> Tenant | None:
        try:
            return Tenant.model_validate(dict(await self.tenants.read_item(item=tenant_id, partition_key=tenant_id)))
        except CosmosResourceNotFoundError:
            return None

    async def credential_revoked(self, tenant_id: str, digest: str) -> bool | None:
        try:
            record = await self.credentials.read_item(item=digest, partition_key=tenant_id)
        except CosmosResourceNotFoundError:
            return None
        return record.get("revoked") is not None

    async def signing_keys(self) -> list[dict[str, Any]]:
        document = await self.credentials.read_item(item=JWKS_DOCUMENT, partition_key=JWKS_PARTITION)
        return list(document["keys"])


def digest_of(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


class TenantResolver:
    """Derives the tenant from a verified claim: an Entra app role or the subject of a platform-signed credential."""

    def __init__(self, settings: Settings, store: TenantStore, entra_keys: KeySource) -> None:
        self.settings, self.store, self.entra_keys = settings, store, entra_keys
        self._issuer = f"{LOGIN}/{settings.entra_tenant_id}/v2.0"
        self._keys: dict[str, Any] = {}

    async def resolve(self, authorization: str | None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AccessError(401, "a bearer token is required")
        unverified = jwt.decode(token, options={"verify_signature": False})
        if unverified.get("iss") == CREDENTIAL_ISSUER:
            return await self._from_credential(token)
        return await self._from_entra(token)

    async def _from_entra(self, token: str) -> Principal:
        claims = self._verify(token, lambda: self.entra_keys.get_signing_key_from_jwt(token).key, self._issuer)
        roles: list[str] = claims.get("roles", [])
        if self.settings.admin_role in roles:
            return Principal(subject=str(claims.get("oid", "")), admin=True)
        tenant_roles = [role for role in roles if role.startswith(self.settings.tenant_role_prefix)]
        if len(tenant_roles) != 1:
            raise AccessError(403, "exactly one tenant app role is required")
        tenant_id = tenant_roles[0].removeprefix(self.settings.tenant_role_prefix)
        return Principal(subject=str(claims.get("oid", "")), tenant=await self._tenant(tenant_id))

    async def _from_credential(self, token: str) -> Principal:
        kid = str(jwt.get_unverified_header(token).get("kid", ""))
        claims = self._verify(token, lambda: self._credential_key(kid), CREDENTIAL_ISSUER)
        tenant_id, digest = str(claims.get("sub", "")), digest_of(token)
        if await self.store.credential_revoked(tenant_id, digest) is not False:
            raise AccessError(401, "unknown or revoked credential")
        return Principal(subject=f"credential:{digest[:12]}", tenant=await self._tenant(tenant_id))

    def _verify(self, token: str, key: Any, issuer: str) -> dict[str, Any]:
        try:
            if not self.settings.require_auth:
                return jwt.decode(token, options={"verify_signature": False})
            return jwt.decode(token, key(), algorithms=["RS256"], audience=self.settings.api_audience, issuer=issuer)
        except jwt.PyJWTError as error:
            raise AccessError(401, f"invalid token: {error}") from error

    def _credential_key(self, kid: str) -> Any:
        if kid not in self._keys:
            raise jwt.PyJWTError(f"unknown signing key {kid}")
        return self._keys[kid]

    async def refresh_keys(self) -> None:
        self._keys = {str(jwk["kid"]): jwt.PyJWK.from_dict(jwk).key for jwk in await self.store.signing_keys()}

    async def _tenant(self, tenant_id: str) -> Tenant:
        tenant = await self.store.tenant(tenant_id)
        if tenant is None or tenant.suspended or tenant.status != "ready":
            raise AccessError(403, "unknown, suspended or unfinished tenant")
        scope = self.settings.tenant_scope
        if scope and (tenant.plan is not Plan.STARTER if scope == "starter" else tenant.id != scope):
            raise AccessError(403, "this gateway does not serve that tenant")
        return tenant


class TenantMiddleware:
    """Resolves the tenant once, before routing, so that no handler and no store sees an unattributed request.

    Without an explicit resolver it uses the gateway the lifespan builds, which exists only after the middleware does.
    """

    def __init__(self, app: ASGIApp, resolver: Resolving | None = None, exempt: tuple[str, ...] = EXEMPT) -> None:
        self.app, self.resolver, self.exempt = app, resolver, exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(self.exempt):
            return await self.app(scope, receive, send)
        resolver: Resolving = self.resolver or scope["app"].state.gateway.resolver
        try:
            principal = await resolver.resolve(Headers(scope=scope).get("authorization"))
        except AccessError as rejected:
            return await problem(rejected, scope["path"])(scope, receive, send)
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def problem(rejected: AccessError, instance: str) -> JSONResponse:
    body = {"type": "about:blank", "title": "request rejected", "status": rejected.status, "detail": rejected.detail}
    headers = {"WWW-Authenticate": "Bearer"} if rejected.status == 401 else {}
    if rejected.retry_after:
        headers["Retry-After"] = str(rejected.retry_after)
    return JSONResponse(body | {"instance": instance}, rejected.status, headers, media_type="application/problem+json")
