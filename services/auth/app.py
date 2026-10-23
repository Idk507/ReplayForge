"""Explicit loopback auth application; no legacy bearer or execution endpoints."""

import hmac
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import SecretStr
from starlette.concurrency import run_in_threadpool

from services.api.app import LocalBoundary
from services.auth.github import AuthDenied, GitHubAuthClient
from services.auth.journal import AuthJournal
from services.auth.settings import GitHubAuthSettings
from services.persistence.store import StoreError


def create_auth_app(
    journal: AuthJournal,
    settings: GitHubAuthSettings,
    client_factory: Callable[[], GitHubAuthClient],
    *,
    allow_test: bool = False,
) -> FastAPI:
    """Trusted dependency injection only; not a public-production startup factory.

    Callback query access logging must be disabled by the trusted HTTP server.
    The old execution API is deliberately not mounted without repo authorization.
    """
    settings = GitHubAuthSettings.model_validate(settings.model_dump())
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    app.add_middleware(LocalBoundary)
    public = urlsplit(settings.public_origin)

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            request.url.scheme != public.scheme
            or request.headers.get("host") != public.netloc
            or len(request.scope.get("query_string", b"")) > 4096
        ):
            return JSONResponse({"error_class": "AUTH_ORIGIN_DENIED"}, status_code=403)
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AuthDenied)
    async def denied(request: Request, error: AuthDenied) -> Response:
        code = str(error)
        if not re.fullmatch(r"AUTH_[A-Z_]{1,60}", code):
            code = "AUTH_DENIED"
        try:
            await run_in_threadpool(journal.denial, code)
        except StoreError:
            return JSONResponse({"error_class": "AUTH_UNAVAILABLE"}, status_code=503)
        response = JSONResponse({"error_class": code}, status_code=401)
        if request.url.path == "/api/v1/auth/github/callback":
            response.delete_cookie(
                settings.attempt_cookie,
                path="/",
                httponly=True,
                secure=not settings.development,
                samesite="lax",
            )
        return response

    @app.exception_handler(StoreError)
    async def unavailable(request: Request, error: StoreError) -> Response:
        return JSONResponse({"error_class": "AUTH_UNAVAILABLE"}, status_code=503)

    def secret(request: Request, name: str) -> SecretStr:
        value = request.cookies.get(name, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            raise AuthDenied("AUTH_REQUIRED")
        return SecretStr(value)

    def cookie(response: Response, name: str, value: SecretStr, age: int) -> None:
        response.set_cookie(
            name,
            value.get_secret_value(),
            max_age=age,
            path="/",
            httponly=True,
            secure=not settings.development,
            samesite="lax",
        )

    @app.get("/api/v1/auth/github/start")
    def start(request: Request) -> Response:
        if request.query_params:
            raise AuthDenied("AUTH_CALLBACK_INVALID")
        old_browser = request.cookies.get(settings.attempt_cookie, "")
        browser = (
            SecretStr(old_browser)
            if re.fullmatch(r"[A-Za-z0-9_-]{43}", old_browser)
            else None
        )
        attempt = journal.start(
            browser=browser, peer=request.client.host if request.client else "unknown"
        )
        with client_factory() as client:
            url = client.authorize_url(
                attempt.state.get_secret_value(), attempt.challenge
            )
        response = RedirectResponse(url, status_code=302)
        cookie(response, settings.attempt_cookie, attempt.browser, 600)
        return response

    @app.get("/api/v1/auth/github/callback")
    def callback(request: Request) -> Response:
        journal.rate(
            request.client.host if request.client else "unknown", "login-callback"
        )
        pairs = request.query_params.multi_items()
        if len({k for k, _ in pairs}) != len(pairs) or any(
            k not in {"state", "code", "error", "error_description", "error_uri"}
            for k, _ in pairs
        ):
            raise AuthDenied("AUTH_CALLBACK_INVALID")
        claim = journal.consume(
            SecretStr(request.query_params.get("state", "")),
            secret(request, settings.attempt_cookie),
        )
        if request.query_params.get("error") or not request.query_params.get("code"):
            raise AuthDenied("AUTH_LOGIN_DENIED")
        with client_factory() as client:
            if client.settings != settings or (
                client.origin == "TEST_TRANSPORT" and not allow_test
            ):
                raise AuthDenied("AUTH_PROVIDER_CONFIG_INVALID")
            token = client.exchange(
                SecretStr(request.query_params["code"]), claim.verifier
            )
            user = client.user(token.access_token)
            old = request.cookies.get(settings.session_cookie, "")
            issued = journal.finish(
                claim,
                user,
                token,
                origin=client.origin,
                old_session=SecretStr(old)
                if re.fullmatch(r"[A-Za-z0-9_-]{43}", old)
                else None,
            )
        response = RedirectResponse(settings.success_url, status_code=303)
        cookie(response, settings.session_cookie, issued.secret, 3600)
        response.delete_cookie(
            settings.attempt_cookie,
            path="/",
            httponly=True,
            secure=not settings.development,
            samesite="lax",
        )
        return response

    @app.get("/api/v1/auth/me")
    def me(request: Request) -> dict[str, object]:
        raw = secret(request, settings.session_cookie)
        user = journal.session(raw, allow_test=allow_test)
        return {
            **user.model_dump(mode="json"),
            "roles": [],
            "csrf_token": journal.vault.digest("csrf", raw),
        }

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request) -> Response:
        raw = secret(request, settings.session_cookie)
        csrf = request.headers.get("x-csrf-token", "")
        if (
            request.headers.get("origin")
            not in {
                settings.public_origin,
                settings.web_origin,
            }
            or not re.fullmatch(r"[a-f0-9]{64}", csrf)
            or not hmac.compare_digest(csrf, journal.vault.digest("csrf", raw))
        ):
            journal.denial("AUTH_CSRF_DENIED")
            return JSONResponse({"error_class": "AUTH_CSRF_DENIED"}, status_code=403)
        journal.logout(raw)
        response = Response(status_code=204)
        response.delete_cookie(
            settings.session_cookie,
            path="/",
            httponly=True,
            secure=not settings.development,
            samesite="lax",
        )
        return response

    return app
