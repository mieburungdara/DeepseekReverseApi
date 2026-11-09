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
import asyncio
import json
import os
import re
import socket
import sys
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import auth as auth_mod
from . import sessions as sessions_mod
from . import tools as tools_mod
from .client import DeepSeekWebClient
from .http import (
    ApiError,
    AuthError,
    ChallengeRequiredError,
    DeepSeekWebError,
    RateLimitError,
)

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


def _auth_error(message: str) -> JSONResponse:
    """Respons 401 bergaya OpenAI, supaya SDK/agen menampilkan pesan yang jelas."""
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": "invalid_api_key",
                "param": None,
            }
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _require_auth(request: Request) -> Optional[JSONResponse]:
    """Return None bila lolos, atau respons 401 bila ditolak."""
    result = auth_mod.check(dict(request.headers))
    if result.ok:
        return None
    return _auth_error(result.error or "Unauthorized")


# --------------------------------------------------------------------- models


@app.get("/v1/models")
async def list_models(request: Request) -> Any:
    denied = _require_auth(request)
    if denied is not None:
        return denied

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


@app.get("/v1/models/{model_id}")
async def get_model(model_id: str, request: Request) -> Any:
    """Sebagian agen memanggil endpoint ini untuk memeriksa satu model."""
    denied = _require_auth(request)
    if denied is not None:
        return denied

    if model_id not in MODEL_MAP:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": f"Model '{model_id}' tidak ditemukan.",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                    "param": None,
                }
            },
        )
    return {
        "id": model_id,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "deepseek-web",
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


#: Jumlah giliran percakapan (user/assistant/tool) yang dikirim penuh.
#: Sisanya diringkas menjadi satu catatan agar prompt tidak membengkak.
#: Dibaca ulang setiap request (lewat fungsi) supaya bisa diubah saat runtime.
DEFAULT_MAX_HISTORY_TURNS = 12

#: Batas karakter per tool result sebelum dipotong.
DEFAULT_MAX_TOOL_RESULT_CHARS = 4000


def _max_history_turns() -> int:
    raw = os.environ.get("DEEPSEEK_MAX_HISTORY_TURNS")
    if raw is None:
        return globals().get("MAX_HISTORY_TURNS", DEFAULT_MAX_HISTORY_TURNS)
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_HISTORY_TURNS


def _max_tool_result_chars() -> int:
    raw = os.environ.get("DEEPSEEK_MAX_TOOL_RESULT")
    if raw is None:
        return globals().get("MAX_TOOL_RESULT_CHARS", DEFAULT_MAX_TOOL_RESULT_CHARS)
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_TOOL_RESULT_CHARS


def _clip(text: str, limit: int, label: str = "") -> str:
    """Potong teks panjang dengan penanda yang jelas."""
    if limit <= 0 or len(text) <= limit:
        return text
    head = text[:limit]
    return (
        f"{head}\n\n[...{label or 'isi'} dipotong: {len(text) - limit:,} karakter "
        f"lagi tidak ditampilkan]"
    )


def _summarize_dropped(messages: list[dict[str, Any]]) -> str:
    """Ringkas pesan lama yang dibuang dari prompt.

    Alih-alih membuang total (model kehilangan jejak), kita sisakan inti
    informasinya: siapa bilang apa, dan tool apa yang dipanggil.
    """
    bits: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content = str(content).strip()
        if role == "user" and content:
            bits.append("User: " + _clip(content.replace("\n", " "), 160))
        elif role == "assistant":
            calls = tools_mod.format_assistant_tool_calls(message.get("tool_calls"))
            if calls:
                names = re.findall(r'"name":\s*"([^"]+)"', calls)
                bits.append("Assistant memanggil: " + ", ".join(names or ["tool"]))
            elif content:
                bits.append("Assistant: " + _clip(content.replace("\n", " "), 160))
        elif role == "tool":
            name = message.get("name") or "tool"
            bits.append(f"Tool {name} dijalankan")
    return "\n".join(bits)


def _prompt_mode() -> str:
    """Mode penyusunan prompt: ``delta`` (default) atau ``full``.

    ``delta``  - kirim hanya pesan baru bila sesi DeepSeek sudah punya isi
                 percakapan; riwayat penuh hanya saat "seed" (sesi baru,
                 server restart, atau riwayat klien berubah). Jauh lebih hemat.
    ``full``   - selalu kirim riwayat (perilaku konservatif).
    """
    mode = (os.environ.get("DEEPSEEK_PROMPT_MODE") or "delta").strip().lower()
    return mode if mode in ("delta", "full") else "delta"


def _flatten_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Collapse an OpenAI message list into ``(system_prompt, user_prompt)``.

    Endpoint web hanya menerima satu string ``prompt``, jadi riwayat dipadatkan
    menjadi transkrip berlabel.

    Untuk menghemat input, hanya ``MAX_HISTORY_TURNS`` giliran terakhir yang
    dikirim penuh; sisanya diringkas menjadi satu blok catatan. System prompt
    tetap utuh karena berisi aturan yang harus dipatuhi.
    """
    system_parts: list[str] = []
    convo: list[dict[str, Any]] = []

    for message in messages or []:
        role = message.get("role", "user")
        if role == "system":
            content = message.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            system_parts.append(str(content or ""))
        else:
            convo.append(message)

    # Pangkas riwayat lama
    dropped: list[dict[str, Any]] = []
    limit = _max_history_turns()
    if limit > 0 and len(convo) > limit:
        dropped = convo[: len(convo) - limit]
        convo = convo[len(convo) - limit:]

    turns: list[str] = []
    summary = _summarize_dropped(dropped) if dropped else ""
    if summary:
        turns.append(
            f"[Ringkasan percakapan sebelumnya]\n{summary}\n"
            f"[Akhir ringkasan - {len(dropped)} pesan lama tidak dikirim penuh]"
        )

    for message in convo:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, list):  # multimodal content parts
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        content = str(content) if content is not None else ""

        if role == "assistant":
            parts: list[str] = []
            if content.strip():
                parts.append(content)
            calls_text = tools_mod.format_assistant_tool_calls(
                message.get("tool_calls")
            )
            if calls_text:
                parts.append(calls_text)
            turns.append("Assistant: " + ("\n".join(parts) if parts else ""))

        elif role == "tool":
            name = message.get("name") or message.get("tool_call_id") or "tool"
            body = _clip(content, _max_tool_result_chars(), f"hasil tool {name}")
            turns.append(f"Tool result ({name}): {body}")

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
    if isinstance(exc, RateLimitError):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": str(exc),
                    "type": "rate_limit_error",
                    "code": "rate_limit_exceeded",
                    "param": None,
                }
            },
            headers={"Retry-After": "30"},
        )
    if isinstance(exc, ChallengeRequiredError):
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "message": str(exc),
                    "type": "upstream_error",
                    "code": "challenge_required",
                    "param": None,
                }
            },
        )
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


#: Jeda minimum antar-request ke DeepSeek (detik). Mencegah rate limit.
MIN_REQUEST_INTERVAL = float(os.environ.get("DEEPSEEK_MIN_INTERVAL", "1.5"))

#: Waktu request terakhir ke DeepSeek (monotonic), per proses.
_last_request_at = 0.0


async def _throttle() -> None:
    """Tunggu agar jarak antar-request ke DeepSeek cukup jauh.

    Tanpa jeda, beberapa request beruntun memicu rate limit dari DeepSeek
    (respons SSE kosong tanpa error).
    """
    global _last_request_at
    if MIN_REQUEST_INTERVAL <= 0:
        return
    now = time.monotonic()
    wait = MIN_REQUEST_INTERVAL - (now - _last_request_at)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_request_at = time.monotonic()


# ----------------------------------------------------------------- completion


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Accept an OpenAI chat completion request and proxy it to DeepSeek web."""
    denied = _require_auth(request)
    if denied is not None:
        return denied

    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Request body must be JSON")

    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="'messages' must be a non-empty array")

    model_type = _resolve_model(body.get("model"))
    options = _extract_options(body, model_type)

    # Tool calling: suntikkan definisi tool ke prompt (DeepSeek web tidak punya
    # API tool resmi). Model akan membalas dengan baris `do: {...}` atau DSML.
    request_tools = body.get("tools")
    tool_prompt = tools_mod.build_tool_prompt(request_tools, body.get("tool_choice"))

    client = get_client()
    completion_id = _completion_id()
    created = int(time.time())
    requested_model = body.get("model") or DEFAULT_MODEL

    # --- Sesi percakapan ---------------------------------------------------
    # Satu percakapan klien = satu chat_session_id DeepSeek. Tanpa ini setiap
    # request membuat sesi baru: riwayat menumpuk di server DeepSeek dan model
    # kehilangan konteks antar giliran.
    conv_key = sessions_mod.conversation_key(body, dict(request.headers))
    conv = sessions_mod.STORE.get(conv_key)

    # --- Tentukan isi prompt: delta (hemat) atau riwayat penuh --------------
    # DeepSeek menyimpan konteks di sesinya, jadi giliran lanjutan cukup
    # mengirim pesan baru. Riwayat penuh hanya dikirim saat "seed": sesi baru,
    # server baru restart, atau klien mengganti riwayatnya.
    use_delta = False
    delta_messages: list[dict[str, Any]] = []
    if _prompt_mode() == "delta" and conv is not None:
        use_delta, delta_messages = sessions_mod.STORE.should_send_delta(
            conv_key, messages
        )

    if use_delta:
        _, prompt = _flatten_messages(delta_messages)
        # System prompt tetap disertakan: murah, dan menjaga aturan tetap aktif.
        system_text = "\n\n".join(
            str(m.get("content") or "")
            for m in messages
            if isinstance(m, dict) and m.get("role") == "system"
        )
        if system_text:
            prompt = f"[System instructions]\n{system_text}\n\n{prompt}"
    else:
        _, prompt = _flatten_messages(messages)

    if tool_prompt:
        prompt = f"{prompt}\n\n{tool_prompt}"

    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt is empty")

    if conv is not None and conv.chat_session_id:
        chat_session_id = conv.chat_session_id
        parent_message_id = conv.parent_message_id
    else:
        try:
            chat_session_id = await client.create_session(model_type=model_type)
        except Exception as exc:  # noqa: BLE001
            return _error_response(exc)
        parent_message_id = None
        conv = sessions_mod.STORE.put(
            conv_key, chat_session_id, parent_message_id=None, model_type=model_type
        )

    def _remember(response_id: Any) -> None:
        """Simpan id pesan asisten untuk dipakai sebagai induk berikutnya."""
        try:
            pid = int(response_id)
        except (TypeError, ValueError):
            return
        sessions_mod.STORE.touch(conv_key, parent_message_id=pid)

    def _mark_history_sent() -> None:
        """Catat bahwa isi percakapan ini sudah diterima sesi DeepSeek.

        Setelah ini, giliran berikutnya cukup mengirim pesan baru.
        """
        sessions_mod.STORE.mark_sent(conv_key, messages, delta=use_delta)

    if not body.get("stream", False):

        await _throttle()
        try:
            text, response_id = await client.chat_turn(
                prompt,
                chat_session_id=chat_session_id,
                parent_message_id=parent_message_id,
                **options,
            )
        except (DeepSeekWebError, Exception) as exc:  # noqa: BLE001
            return _error_response(exc)

        _remember(response_id)
        _mark_history_sent()

        content, tool_calls = tools_mod.parse_response(text, request_tools)

        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
            finish_reason = "tool_calls"
        else:
            # Pastikan content bukan null untuk jawaban biasa.
            message["content"] = content
            finish_reason = "stop"

        return JSONResponse(
            content={
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": requested_model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
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

        await _throttle()
        yield _sse_chunk(completion_id, created, requested_model, {"role": "assistant"})

        # Model web mengirim potongan sangat kecil (kadang satu karakter), jadi
        # mendeteksi "do:" per potongan tidak mungkin. Strategi: tahan SELALU
        # selama beberapa karakter pertama, dan begitu teks dipastikan bukan
        # tool call, lepas dan alirkan sisanya langsung.
        buffer: list[str] = []
        decided = False  # sudah dipastikan bukan tool call?
        HOLD_CHARS = 40  # cukup untuk melihat "do: {...}" atau awal DSML

        try:
            async for chunk in client.stream_turn(
                prompt,
                chat_session_id=chat_session_id,
                parent_message_id=parent_message_id,
                on_response_id=_remember,
                **options,
            ):
                if not chunk:
                    continue
                buffer.append(chunk)

                if decided:
                    yield _sse_chunk(
                        completion_id, created, requested_model, {"content": chunk}
                    )
                    continue

                held = "".join(buffer)
                if tools_mod.has_dsml(held) or _looks_like_tool_start(held):
                    # Masih mungkin tool call -> tahan.
                    continue

                if len(held) >= HOLD_CHARS:
                    # Cukup panjang dan bukan tool call -> lepas semuanya.
                    decided = True
                    yield _sse_chunk(
                        completion_id, created, requested_model, {"content": held}
                    )
                    buffer = []
        except Exception as exc:  # noqa: BLE001
            yield _sse_error(completion_id, created, requested_model, str(exc))
            return

        # Stream selesai: isi percakapan sudah diterima sesi DeepSeek, jadi
        # giliran berikutnya cukup mengirim pesan baru.
        _mark_history_sent()

        tail = "".join(buffer)
        if tail:
            content, tool_calls = tools_mod.parse_response(tail, request_tools)
            if content:
                yield _sse_chunk(
                    completion_id, created, requested_model, {"content": content}
                )
            if tool_calls:
                for index, call in enumerate(tool_calls):
                    yield _sse_tool_call_delta(
                        completion_id, created, requested_model, index, call
                    )
                yield _sse_chunk(
                    completion_id,
                    created,
                    requested_model,
                    {},
                    finish_reason="tool_calls",
                )
                yield b"data: [DONE]\n\n"
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


def _looks_like_tool_start(text: str) -> bool:
    """Apakah teks (mungkin belum lengkap) tampak seperti awal tool call?

    Dipakai untuk menahan output di tengah streaming agar markup DSML tidak
    terlanjur terkirim ke klien sebagai teks biasa.
    """
    if not text:
        return False
    stripped = text.lstrip()
    # Awal DSML yang mungkin belum lengkap, mis. "<" atau "<｜"
    if stripped.startswith("<") and len(stripped) <= 24:
        if not stripped.endswith(">"):
            return True
    if tools_mod.has_dsml(stripped):
        return True
    if stripped.lower().startswith(tools_mod.DO_PREFIX):
        return True
    # "do:" muncul setelah baris baru
    last_line = stripped.rsplit("\n", 1)[-1].strip().lower()
    return last_line.startswith(tools_mod.DO_PREFIX)


def _sse_tool_call_delta(
    completion_id: str,
    created: int,
    model: str,
    index: int,
    call: dict[str, Any],
) -> bytes:
    """Satu potongan SSE berisi satu tool_call (format streaming OpenAI)."""
    fn = call.get("function") or {}
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": index,
                            "id": call.get("id"),
                            "type": "function",
                            "function": {
                                "name": fn.get("name", ""),
                                "arguments": fn.get("arguments", "{}"),
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


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


@app.get("/v1/sessions")
async def list_sessions(request: Request) -> Any:
    """Lihat percakapan aktif dan sesi DeepSeek yang dipakainya."""
    denied = _require_auth(request)
    if denied is not None:
        return denied
    return sessions_mod.STORE.stats()


@app.delete("/v1/sessions")
async def clear_sessions(request: Request) -> Any:
    """Lupakan semua pemetaan percakapan (bukan menghapus di server DeepSeek)."""
    denied = _require_auth(request)
    if denied is not None:
        return denied
    sessions_mod.STORE.clear()
    return {"ok": True, "message": "Semua pemetaan percakapan dibersihkan."}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _pid_alive(pid: int) -> bool:
    """Cek apakah proses ``pid`` masih hidup, secara portabel.

    Di Windows **jangan** pakai ``os.kill(pid, 0)``: implementasinya memanggil
    ``TerminateProcess`` dengan handle akses yang salah sehingga melempar
    ``OSError [WinError 87]`` ("The parameter is incorrect") untuk proses yang
    justru masih hidup. Cara yang benar adalah membuka handle dengan
    ``PROCESS_QUERY_LIMITED_INFORMATION`` lalu membaca exit code.
    """
    if pid <= 0:
        return False

    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            ERROR_ACCESS_DENIED = 5

            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetLastError.restype = wintypes.DWORD

            handle = k32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                # Bedakan "tidak ada" dari "ada tapi tidak boleh dibuka".
                # Access-denied berarti proses ADA (mis. proses sistem), jadi
                # jangan sampai kita mematikan diri sendiri karena itu.
                if k32.GetLastError() == ERROR_ACCESS_DENIED:
                    return True
                return False
            try:
                code = wintypes.DWORD()
                ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
                if not ok:
                    return True  # tidak bisa dipastikan -> anggap hidup
                return code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - jangan pernah mematikan diri karena error cek
            return True  # anggap hidup: lebih aman daripada mematikan diri salah

    # POSIX
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # ada, tapi bukan milik kita
    except OSError:
        return False
    return True


def _watch_parent(parent_pid: int, interval: float = 3.0) -> None:
    """Hentikan proses bila induknya hilang (jendela terminal ditutup paksa).

    Di Windows, menutup jendela console biasanya mengirim CTRL_CLOSE_EVENT yang
    sudah ditangani Python. Tetapi bila induk dibunuh paksa (mis. Task Manager,
    ``taskkill /F`` pada cmd.exe), proses anak tetap hidup sebagai yatim dan
    terus menahan port. Thread ini menutup celah itu.
    """
    import threading

    def _loop() -> None:
        # Beri waktu singkat agar induk sempat stabil.
        time.sleep(interval)
        while True:
            try:
                if not _pid_alive(parent_pid):
                    os._exit(0)
            except Exception:  # noqa: BLE001
                # Jangan biarkan thread mati; coba lagi pada siklus berikutnya.
                pass
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name="parent-watchdog", daemon=True)
    thread.start()


def _watch_stdin(interval: float = 3.0) -> None:
    """Hentikan proses bila stdin tertutup (jendela terminal hilang).

    Ini jaring pengaman yang paling andal di Windows: saat jendela console
    ditutup, stdin proses anak menjadi tidak valid / EOF. Lebih dapat
    diandalkan daripada memeriksa PID induk, karena ``cmd.exe`` bisa tetap
    hidup sebentar setelah jendela hilang.
    """
    import threading

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                if sys.stdin is None or sys.stdin.closed:
                    os._exit(0)
                # Bila terminal hilang, readline pada stdin console akan
                # langsung mengembalikan EOF / melempar OSError.
            except Exception:  # noqa: BLE001
                os._exit(0)

    thread = threading.Thread(target=_loop, name="stdin-watchdog", daemon=True)
    thread.start()


def _start_parent_watchdog() -> None:
    """Aktifkan watchdog agar tidak ada proses yatim setelah jendela ditutup."""
    if os.environ.get("DEEPSEEK_NO_PARENT_WATCHDOG") == "1":
        return
    parent = _getppid()
    if parent and parent > 0:
        _watch_parent(parent)


def _getppid() -> int:
    """PPID portabel tanpa dependensi tambahan."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.windll.kernel32
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snap == -1:
            return 0
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        me = os.getpid()
        try:
            ok = k32.Process32First(snap, ctypes.byref(entry))
            while ok:
                if entry.th32ProcessID == me:
                    return int(entry.th32ParentProcessID)
                ok = k32.Process32Next(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        pass
    return 0


@app.get("/v1/auth/check")
async def auth_check(request: Request) -> Any:
    """Cek apakah key yang dikirim diterima gateway ini.

    Berguna untuk tombol "Test connection" di agen AI: membalas 200 + detail
    mode bila key valid, atau 401 bila tidak.
    """
    denied = _require_auth(request)
    if denied is not None:
        return denied
    return {
        "ok": True,
        "mode": auth_mod.current_mode(),
        "description": auth_mod.describe(),
        "key_received": bool(auth_mod.extract_key(dict(request.headers))),
    }


@app.get("/")
async def root() -> dict[str, Any]:
    """Landing page sederhana agar mudah diperiksa dari browser."""
    return {
        "service": "DeepSeek Web -> OpenAI compatible API",
        "auth_mode": auth_mod.describe(),
        "base_url": "/v1",
        "endpoints": [
            "GET  /v1/models",
            "GET  /v1/models/{model}",
            "POST /v1/chat/completions",
            "GET  /v1/auth/check",
            "GET  /healthz",
        ],
        "note": (
            "Isi API key dengan string apa pun pada mode open/any."
        ),
    }


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
    parser.add_argument(
        "--auth",
        choices=list(auth_mod.MODES),
        default=None,
        help=(
            "mode API key: open (key apa pun / tanpa key), any (key apa pun "
            "tapi wajib diisi), strict (harus sama dengan DEEPSEEK_API_KEY). "
            "Default: env DEEPSEEK_AUTH_MODE atau 'open'."
        ),
    )
    args = parser.parse_args()

    # Flag CLI menimpa env.
    if args.auth:
        os.environ["DEEPSEEK_AUTH_MODE"] = args.auth
    if auth_mod.current_mode() == "strict" and not auth_mod.expected_key():
        print(
            "ERROR: mode 'strict' butuh DEEPSEEK_API_KEY.\n"
            "  Contoh: set DEEPSEEK_API_KEY=sk-rahasia-saya",
            file=sys.stderr,
        )
        raise SystemExit(1)

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
    print(f"  Mode API key    : {auth_mod.describe()}")
    if auth_mod.current_mode() == "open":
        print("  -> Isi kolom API key di agen dengan string APA SAJA (mis. 'sk-lokal').")
    print("  Tekan CTRL+C untuk berhenti.")

    # Matikan diri bila jendela induk ditutup paksa (mencegah proses yatim).
    _start_parent_watchdog()

    uvicorn.run(
        "deepseek_web_api.server:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()





