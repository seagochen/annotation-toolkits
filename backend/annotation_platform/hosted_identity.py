"""Application-owned PKCE login using only the platform's identity REST API."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

TOKEN_MAX_AGE = 3600
LOGIN_MAX_AGE = 300
TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def valid_token(value: object) -> bool:
    return isinstance(value, str) and 20 <= len(value) <= 128 and set(value) <= TOKEN_CHARS


def expiry_seconds(value: object) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return 0
        return parsed.timestamp() - datetime.now(timezone.utc).timestamp()
    except (AttributeError, TypeError, ValueError):
        return 0


class HostedIdentity:
    def __init__(self, public_origin: str, platform_origin: str, client: httpx.AsyncClient):
        self.public_origin = public_origin
        self.platform_origin = platform_origin
        self.client = client
        self.secure = public_origin.startswith("https:")
        prefix = "__Host-" if self.secure else ""
        self.session_cookie = prefix + "annotation_identity"
        self.login_cookie = prefix + "annotation_login"
        self.pending: dict[str, dict] = {}
        self.api = "/platform/app-sessions/annotation"

    async def platform(self, path: str, **kwargs) -> httpx.Response:
        try:
            reply = await self.client.request(
                kwargs.pop("method", "GET"), self.platform_origin + path,
                follow_redirects=False, timeout=10, **kwargs,
            )
        except httpx.HTTPError as error:
            raise HTTPException(503, "Platform identity service is unavailable") from error
        if reply.status_code >= 500 or 300 <= reply.status_code < 400:
            raise HTTPException(503, "Platform identity service is unavailable")
        return reply

    async def authenticate(self, request: Request) -> dict:
        authorization = request.headers.get("authorization")
        token = request.cookies.get(self.session_cookie) if authorization is None else (
            authorization[7:] if authorization.startswith("Bearer ") else None
        )
        if not valid_token(token):
            raise HTTPException(401, "Platform login required")
        reply = await self.platform(self.api + "/me", headers={"Authorization": "Bearer " + token})
        if reply.status_code != 200:
            raise HTTPException(401, "Platform login required")
        try:
            owner = reply.json()
        except ValueError as error:
            raise HTTPException(503, "Invalid platform identity response") from error
        if (not isinstance(owner, dict) or owner.get("appId") != "annotation"
            or not isinstance(owner.get("userId"), str) or not 0 < len(owner["userId"]) <= 200
            or not isinstance(owner.get("scopes"), list) or "platform.auth" not in owner["scopes"]
            or not all(isinstance(scope, str) for scope in owner["scopes"])
            or expiry_seconds(owner.get("expiresAt")) <= 0):
            raise HTTPException(401, "Invalid platform identity")
        return {**owner, "token": token}

    def assert_write_origin(self, request: Request) -> None:
        origin = request.headers.get("origin")
        bearer = request.headers.get("authorization") is not None
        if (bearer and origin is not None and origin != self.public_origin) or (
            not bearer and (origin != self.public_origin
                or request.headers.get("sec-fetch-site") in {"same-site", "cross-site"})
        ):
            raise HTTPException(403, "Cross-origin request rejected")

    def cookie(self, reply: Response, name: str, value: str, max_age: int) -> None:
        reply.set_cookie(name, value, max_age=max_age, path="/", secure=self.secure,
                         httponly=True, samesite="lax")

    async def login(self, request: Request) -> Response:
        now = time.monotonic()
        self.pending = {state: login for state, login in self.pending.items() if login["expires"] > now}
        if len(self.pending) >= 1000:
            raise HTTPException(429, "Too many pending logins")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        path = request.query_params.get("next", "/")
        parsed = urlsplit(path)
        if (len(path) > 2048 or not path.startswith("/") or path.startswith("//")
            or "\\" in path or parsed.netloc or parsed.scheme or parsed.path.startswith("/auth/")):
            path = "/"
        self.pending[state] = {"verifier": verifier, "expires": now + LOGIN_MAX_AGE, "next": path}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        url = self.platform_origin + self.api + "/authorize?" + urlencode({
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        reply = RedirectResponse(url, status_code=303)
        self.cookie(reply, self.login_cookie, state, LOGIN_MAX_AGE)
        return reply

    async def callback(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        login = self.pending.get(state)
        if (login is None or login["expires"] <= time.monotonic()
            or request.cookies.get(self.login_cookie) != state):
            raise HTTPException(401, "Invalid login callback")
        self.pending.pop(state)
        code = request.query_params.get("code")
        if not valid_token(code):
            raise HTTPException(401, "Invalid login callback")
        reply = await self.platform(self.api + "/exchange", method="POST",
                                    json={"code": code, "codeVerifier": login["verifier"]})
        if reply.status_code != 200:
            raise HTTPException(401, "Platform login exchange failed")
        try:
            session = reply.json()
            max_age = int(expiry_seconds(session.get("expiresAt")))
            token = session.get("token")
        except (ValueError, AttributeError, TypeError) as error:
            raise HTTPException(503, "Invalid platform session response") from error
        if not valid_token(token) or not 0 < max_age <= TOKEN_MAX_AGE:
            raise HTTPException(503, "Invalid platform session response")
        result = RedirectResponse(login["next"], status_code=303)
        self.cookie(result, self.session_cookie, token, max_age)
        self.cookie(result, self.login_cookie, "", 0)
        return result

    async def me(self, request: Request) -> Response:
        owner = await self.authenticate(request)
        return JSONResponse({key: owner[key] for key in ("userId", "appId", "scopes", "expiresAt")})

    async def logout(self, request: Request) -> Response:
        self.assert_write_origin(request)
        owner = await self.authenticate(request)
        reply = await self.platform(self.api, method="DELETE", headers={"Authorization": "Bearer " + owner["token"]})
        if reply.status_code not in {200, 204, 401}:
            raise HTTPException(503, "Platform logout failed")
        result = Response(status_code=204)
        self.cookie(result, self.session_cookie, "", 0)
        return result
