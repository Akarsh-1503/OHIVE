"""Bearer auth for the routes vLLM's own `--api-key` middleware leaves open.

vLLM only guards paths under `/v1`, `/v2`, `/inference` and `/cohere`. That leaves
`/invocations` — a full inference route that accepts the same bodies as
`/v1/chat/completions` — reachable by anyone who learns the deployment URL, which on a
metered GPU is a direct billing hole. This middleware is loaded via `--middleware` and
guards everything except the liveness probes the backend needs.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# `/health` stays open so the backend can check liveness without holding the key, and
# `/metrics` so Modal/Prometheus scraping keeps working. Neither runs the model.
OPEN_PATHS = frozenset({"/health", "/metrics"})


class RequireBearer:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._expected = hashlib.sha256(os.environ["VLM_API_KEY"].encode()).digest()

    def _authorized(self, scope: Scope) -> bool:
        header = Headers(scope=scope).get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        digest = hashlib.sha256(token.encode()).digest()
        return secrets.compare_digest(digest, self._expected)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only HTTP is filtered here: vLLM's own middleware already covers the
        # websocket routes, which all live under the /v1 prefix.
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = scope["path"].removeprefix(scope.get("root_path", ""))
        if path in OPEN_PATHS or self._authorized(scope):
            await self.app(scope, receive, send)
            return

        response = JSONResponse({"error": "Unauthorized"}, status_code=401)
        await response(scope, receive, send)
