"""Authenticated streaming proxy used by the private Sites gateway."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask


ROOT = Path(__file__).resolve().parent.parent
KEY_FILE = Path(os.getenv("DSA_GATEWAY_KEY_FILE", ROOT / "data" / ".online_gateway_key"))
UPSTREAM = os.getenv("DSA_GATEWAY_UPSTREAM", "http://127.0.0.1:8010").rstrip("/")
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.AsyncClient(timeout=None, follow_redirects=False)


def _expected_key() -> str:
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _is_authorized(request: Request) -> bool:
    expected = _expected_key()
    provided = request.headers.get("x-dsa-gateway-key", "")
    return bool(expected and provided and secrets.compare_digest(expected, provided))


@app.get("/__local_gateway_health")
async def local_gateway_health(request: Request):
    if not _is_authorized(request):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    try:
        response = await client.get(f"{UPSTREAM}/api/health")
        upstream_ok = response.is_success
    except httpx.HTTPError:
        upstream_ok = False
    return {"status": "ok" if upstream_ok else "degraded", "upstream": upstream_ok}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
)
async def proxy_request(path: str, request: Request):
    if not _is_authorized(request):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    target = f"{UPSTREAM}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "x-dsa-gateway-key"
    }
    headers["host"] = "127.0.0.1:8010"
    body = await request.body()
    upstream_request = client.build_request(
        request.method,
        target,
        headers=headers,
        content=body if body else None,
    )
    upstream_response = await client.send(upstream_request, stream=True)
    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    return StreamingResponse(
        upstream_response.aiter_raw(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream_response.aclose),
    )
