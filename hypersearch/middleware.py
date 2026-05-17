"""API-key authentication middleware."""

from __future__ import annotations

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Validate ``X-API-Key`` header against a configured secret.

    If ``api_key`` is ``None``, all requests are allowed (auth disabled).
    The ``/health`` and ``/metrics`` endpoints are always exempt.
    """

    EXEMPT_PATHS = {"/health", "/metrics", "/docs", "/openapi.json", "/redoc"}

    def __init__(self, app, api_key: str | None = None) -> None:  # type: ignore[override]
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if self.api_key is None:
            return await call_next(request)

        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        provided = request.headers.get("X-API-Key")
        if provided != self.api_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
            )

        return await call_next(request)
