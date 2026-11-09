"""OpenAI-compatible facade over the reverse-engineered web client.

Exposes ``POST /v1/chat/completions`` (streaming and non-streaming) plus
``GET /v1/models``, so existing OpenAI SDKs and tools can point at
``http://127.0.0.1:17395/v1``.

Run with::

    python -m deepseek_web_api.server                 # port 17395
    python -m deepseek_web_api.server --port 19999    # port lain
    python -m deepseek_web_api.server --auto-port     # geser bila bentrok

Port default sengaja dipilih **di luar** rentang ephemeral Windows
(49152-65535) dan bukan port yang lazim dipakai layanan lain, sehingga kecil
kemungkinan bentrok. Bisa juga diatur lewat env ``DEEPSEEK_PORT``.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .client import DeepSeekWebClient
from .http import ApiError, AuthError, DeepSeekWebError

#: Port default: di luar rentang ephemeral (49152-65535), tidak dipakai layanan
#: umum, dan tidak terdaftar di IANA untuk protokol apa pun.
DEFAULT_PORT = 17395
DEFAULT_HOST = "127.0.0.1"

#: Rentang ephemeral Windows pada mesin ini; port di dalamnya bisa "direbut"
#: koneksi keluar kapan saja, jadi kita hindari.
EPHEMERAL_RANGE = (49152, 65535)

#: OpenAI ``model`` name -> DeepSeek web ``model_type``.
#: ``default`` = Instant mode, ``expert`` = Expert (thinking) mode.
MODEL_MAP = {
    "deepseek-chat": "default",
    "deepseek-instant": "default",
    "deepseek-reasoner": "expert",
    "deepseek-expert": "expert",
    "deepseek-vision": "vision",
    # pass-through of the raw web values
    "default": "default",
    "expert": "expert",
    "vision": "vision",
}

DEFAULT_MODEL = "deepseek-chat"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Tutup koneksi upstream saat server berhenti."""
    yield
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


app = FastAPI(
    title="DeepSeek Web -> OpenAI compatible API",
    version="0.1.0",
    lifespan=lifespan,
)

_client: Optional[DeepSeekWebClient] = None


def get_client() -> DeepSeekWebClient:
    """Lazily build the singleton upstream client from the environment."""
    global _client
    if _client is None:
        token = os.environ.get("DEEPSEEK_TOKEN")
        token_file = os.environ.get("DEEPSEEK_TOKEN_FILE")
        email = os.environ.get("DEEPSEEK_EMAIL")
        password = os.environ.get("DEEPSEEK_PASSWORD")
        _client = DeepSeekWebClient(
            token=token,
            token_file=token_file,
            email=email,
            password=password,
        )
    return _client


# --------------------------------------------------------------------- models


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    now = int(time.time())
    return {
        "object": "list",
        "data": [
            {
                "id": name,
                "object": "model",
                "created": now,
                "owned_by": "deepseek-web",
            }
            for name in ("deepseek-chat", "deepseek-reasoner", "deepseek-vision")
        ],
    }


# ------------------------------------------------------------- request shaping


def _resolve_model(requested: Optional[str]) -> str:
    if not requested:
        return MODEL_MAP[DEFAULT_MODEL]
    if requested in MODEL_MAP:
        return MODEL_MAP[requested]
    lowered = requested.lower()
    for prefix, model_type in MODEL_MAP.items():
        if lowered.startswith(prefix):
            return model_type
    # Unknown names: pass through, the upstream API validates it.
    return requested


def _flatten_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Collapse an OpenAI message list into ``(system_prompt, user_prompt)``.

    The web endpoint accepts a single ``prompt`` string, so we fold system and
    prior turns into one labelled transcript. This is a lossy but faithful
    mapping: the web app has no equivalent of a structured message array.
    """
    system_parts: list[str] = []
    turns: list[str] = []

    for message in messages or []:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, list):  # multimodal content parts
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content)
        if role == "system":
            system_parts.append(content)
        elif role == "assistant":
            turns.append(f"Assistant: {content}")
        else:
            turns.append(f"User: {content}")

    prompt = "\n\n".join(turns)
    system = "\n\n".join(system_parts)
    if system:
        prompt = f"[System instructions]\n{system}\n\n{prompt}"
    return system, prompt


def _extract_options(body: dict[str, Any], model_type: str) -> dict[str, Any]:
    """Map OpenAI-ish flags onto the web client's options."""
    thinking = model_type == "expert"
    if isinstance(body.get("thinking"), dict):
        thinking = bool(body["thinking"].get("type") == "enabled")
    elif "thinking_enabled" in body:
        thinking = bool(body["thinking_enabled"])

    return {
        "model_type": model_type,
        "thinking_enabled": thinking,
        "search_enabled": bool(body.get("search_enabled", body.get("web_search", False))),
    }


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AuthError):
        status, etype, code = 401, "authentication_error", "invalid_api_key"
    elif isinstance(exc, ApiError):
        status, etype, code = 400, "invalid_request_error", "upstream_error"
    else:
        status, etype, code = 502, "upstream_error", "upstream_error"
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": str(exc),
                "type": etype,
                "code": code,
                "param": None,
            }
        },
    )


# ----------------------------------------------------------------- completion


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """Accept an OpenAI chat completion request and proxy it to DeepSeek web."""
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty array")

    model_type = _resolve_model(body.get("model"))
    options = _extract_options(body, model_type)
    _, prompt = _flatten_messages(messages)
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty")

    client = get_client()
    completion_id = _completion_id()
    created = int(time.time())
    requested_model = body.get("model") or DEFAULT_MODEL

    if not body.get("stream", False):
        try:
            text = await client.chat(prompt, **options)
        except (DeepSeekWebError, Exception) as exc:  # noqa: BLE001
            return _error_response(exc)

        return JSONResponse(
            content={
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": requested_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": len(prompt) // 4,
                    "completion_tokens": len(text) // 4,
                    "total_tokens": (len(prompt) + len(text)) // 4,
                },
            }
        )

    async def event_stream() -> AsyncIterator[bytes]:
        yield _sse_chunk(completion_id, created, requested_model, {"role": "assistant"})
        try:
            async for chunk in client.stream_text(prompt, **options):
                if chunk:
                    yield _sse_chunk(
                        completion_id, created, requested_model, {"content": chunk}
                    )
        except Exception as exc:  # noqa: BLE001
            yield _sse_error(completion_id, created, requested_model, str(exc))
            return
        yield _sse_chunk(
            completion_id, created, requested_model, {}, finish_reason="stop"
        )
        yield b"data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    *,
    finish_reason: Optional[str] = None,
) -> bytes:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {"index": 0, "delta": delta, "finish_reason": finish_reason}
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _sse_error(completion_id: str, created: int, model: str, message: str) -> bytes:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": f"\n\n[upstream error: {message}]"},
                "finish_reason": "stop",
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def port_is_free(port: int, host: str = DEFAULT_HOST) -> bool:
    """True bila ``port`` bisa di-bind sekarang."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Di Windows, SO_REUSEADDR justru mengizinkan "mencuri" port yang
        # sedang dipakai, jadi jangan dipasang di sana.
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(
    start: int = DEFAULT_PORT,
    *,
    host: str = DEFAULT_HOST,
    attempts: int = 50,
) -> int:
    """Cari port bebas mulai dari ``start``, melewati rentang ephemeral."""
    low, high = EPHEMERAL_RANGE
    port = start
    for _ in range(attempts):
        if port > 65535:
            break
        if not (low <= port <= high) and port_is_free(port, host):
            return port
        port += 1
    raise RuntimeError(
        f"Tidak menemukan port bebas mulai dari {start} "
        f"(dicoba {attempts} port, melewati rentang ephemeral {low}-{high})"
    )


def _describe_port(port: int) -> str:
    low, high = EPHEMERAL_RANGE
    if low <= port <= high:
        return f"{port} (PERINGATAN: di dalam rentang ephemeral {low}-{high})"
    return str(port)


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Server OpenAI-compatible untuk chat.deepseek.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            f"  python -m deepseek_web_api.server                  # port {DEFAULT_PORT}\n"
            "  python -m deepseek_web_api.server --port 19999\n"
            "  python -m deepseek_web_api.server --auto-port      # geser bila bentrok\n"
        ),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("DEEPSEEK_HOST", DEFAULT_HOST),
        help=f"alamat bind (default {DEFAULT_HOST}, env DEEPSEEK_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"port (default {DEFAULT_PORT}, env DEEPSEEK_PORT)",
    )
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="cari port bebas berikutnya bila port yang diminta sedang dipakai",
    )
    parser.add_argument("--reload", action="store_true", help="auto-reload (mode dev)")
    args = parser.parse_args()

    requested = args.port
    if requested is None:
        env_port = os.environ.get("DEEPSEEK_PORT")
        requested = int(env_port) if env_port else DEFAULT_PORT

    if port_is_free(requested, args.host):
        port = requested
    elif args.auto_port:
        port = find_free_port(requested, host=args.host)
        print(
            f"Port {requested} sedang dipakai, memakai port {port}.",
            file=sys.stderr,
        )
    else:
        print(
            f"ERROR: port {requested} sudah dipakai proses lain.\n"
            f"  Pilih port lain : --port {find_free_port(requested, host=args.host)}\n"
            "  Atau biarkan otomatis : --auto-port",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if EPHEMERAL_RANGE[0] <= port <= EPHEMERAL_RANGE[1]:
        print(
            f"PERINGATAN: port {port} berada di rentang ephemeral Windows "
            f"{EPHEMERAL_RANGE[0]}-{EPHEMERAL_RANGE[1]}; koneksi keluar bisa "
            "memakainya lebih dulu.",
            file=sys.stderr,
        )

    print(f"DeepSeek Web API siap di http://{args.host}:{_describe_port(port)}")
    print(f"  OpenAI base_url : http://{args.host}:{port}/v1")
    print(f"  Model tersedia  : {', '.join(MODEL_MAP)}")

    uvicorn.run(
        "deepseek_web_api.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
