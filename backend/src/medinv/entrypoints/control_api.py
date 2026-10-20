import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from medinv.platform.auth.config import AuthSettings
from medinv.platform.auth.provider import IdentityProvider, KeycloakProvider
from medinv.platform.auth.service import AuthService
from medinv.platform.database.uow import check_runtime_role
from medinv.platform.errors import BusinessError


class TenantChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID


class MemberChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{0,18}$")]
    role_codes: list[StrictStr] = Field(min_length=1, max_length=5)
    entity_ids: list[UUID] = Field(max_length=200)
    active: StrictBool


def create_app(
    settings: AuthSettings | None = None, provider: IdentityProvider | None = None
) -> FastAPI:
    settings = settings or AuthSettings()  # type: ignore[call-arg]
    engine = create_engine(
        settings.control_auth_database_url.get_secret_value(),
        pool_size=5,
        max_overflow=10,
        pool_timeout=5,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await anyio.to_thread.run_sync(check_runtime_role, engine, "control")
            identity: IdentityProvider
            if provider is None:
                identity = await anyio.to_thread.run_sync(lambda: KeycloakProvider(settings))
            else:
                identity = provider
            app.state.auth = AuthService(engine, settings, identity)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="药械进销存 · 身份与企业管理", version="0.26.0", lifespan=lifespan)

    def error(request: Request, exc: BusinessError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
                "request_id": str(request.state.request_id),
            },
        )

    @app.middleware("http")
    async def security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid4()
        response: Response
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and request.headers.get("Origin") != settings.public_origin
        ):
            response = error(request, BusinessError("ORIGIN_REJECTED", "请求来源校验失败", 403))
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Request-ID": str(request.state.request_id),
            }
        )
        return response

    @app.exception_handler(BusinessError)
    async def business_error(request: Request, exc: BusinessError) -> JSONResponse:
        return error(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(request, BusinessError("VALIDATION_ERROR", "请求字段或格式不正确", 422))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logging.getLogger("medinv.auth").error(
            "control_db_error request_id=%s class=%s", request.state.request_id, type(exc).__name__
        )
        return error(
            request, BusinessError("DATABASE_BUSY", "系统繁忙，请稍后重试", 503, retryable=True)
        )

    def service(request: Request) -> AuthService:
        result: AuthService = request.app.state.auth
        return result

    def credentials(request: Request) -> tuple[str, str, str]:
        return (
            request.cookies.get("MED_SESSION", ""),
            request.headers.get("X-Context-Generation", ""),
            request.headers.get("X-CSRF-Token", ""),
        )

    def envelope(request: Request, data: dict[str, Any]) -> dict[str, Any]:
        return {"data": data, "request_id": str(request.state.request_id)}

    @app.get("/auth/login", include_in_schema=False)
    def login(request: Request) -> RedirectResponse:
        url, browser = service(request).begin_login()
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(
            "MED_LOGIN",
            browser,
            max_age=600,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="lax",
            path="/auth",
        )
        return response

    @app.get("/auth/callback", include_in_schema=False)
    def callback(request: Request, state: str = "", code: str = "") -> RedirectResponse:
        try:
            token = service(request).finish_login(
                state, request.cookies.get("MED_LOGIN"), code, request.cookies.get("MED_SESSION")
            )
        except BusinessError as exc:
            logging.getLogger("medinv.auth").info("login_rejected code=%s", exc.code)
            # Never put identity provider error text, authorization codes or tokens in redirects.
            response = RedirectResponse(settings.public_origin + "/?login_error=1", status_code=303)
            response.delete_cookie("MED_LOGIN", path="/auth")
            return response
        response = RedirectResponse(settings.public_origin + "/", status_code=303)
        response.set_cookie(
            "MED_SESSION",
            token,
            max_age=settings.session_seconds,
            httponly=True,
            secure=settings.secure_cookie,
            samesite="lax",
            path="/",
        )
        response.delete_cookie("MED_LOGIN", path="/auth")
        return response

    @app.get("/auth/session")
    def session(request: Request) -> dict[str, Any]:
        return envelope(request, service(request).view(request.cookies.get("MED_SESSION")))

    @app.post("/auth/refresh")
    def refresh(request: Request) -> dict[str, Any]:
        return envelope(request, service(request).refresh(*credentials(request)))

    @app.post("/auth/tenant")
    def switch(request: Request, body: TenantChange) -> dict[str, Any]:
        return envelope(
            request, service(request).switch_tenant(*credentials(request), body.tenant_id)
        )

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        service(request).logout(*credentials(request))
        response = Response(status_code=204)
        response.delete_cookie("MED_SESSION", path="/")
        return response

    @app.get("/control/members")
    def members(request: Request) -> dict[str, Any]:
        return envelope(
            request,
            service(request).members(
                request.cookies.get("MED_SESSION", ""),
                request.headers.get("X-Context-Generation", ""),
            ),
        )

    @app.patch("/control/members/{principal_id}")
    def change_member(request: Request, principal_id: UUID, body: MemberChange) -> dict[str, Any]:
        service(request).change_member(
            *credentials(request),
            principal_id,
            int(body.expected_version),
            body.role_codes,
            body.entity_ids,
            body.active,
        )
        return envelope(request, {"updated": True})

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> dict[str, str]:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app
