"""Async client for the reverse-engineered chat.deepseek.com web API.

Flow (traced from the web bundle, see docs/REVERSE_ENGINEERING.md):

1. ``POST /api/v0/users/login`` with ``{email, password, device_id, os}``
   returns ``biz_data.biz_data.token``.
2. ``POST /api/v0/chat/create_pow_challenge`` with ``{target_path}`` returns a
   ``DeepSeekHashV1`` challenge.
3. Solve it, then send ``X-DS-PoW-Response`` on the real request.
4. ``POST /api/v0/chat/completion`` streams SSE events.

Note the double nesting on login: the HTTP envelope's ``data`` holds a
``biz_data`` string which itself is JSON containing ``biz_data.token``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import httpx

from .http import (
    BASE_URL,
    DEFAULT_UA,
    ApiError,
    AuthError,
    ChallengeRequiredError,
    DeepSeekWebError,
    RateLimitError,
    NetworkError,
    TokenState,
    client_headers,
    make_client,
    unwrap_envelope,
)
from .config import min_interval, pow_timeout
from .pow import PowChallenge, PowError, solve_to_answer
from .stream import DeltaAccumulator

LOGIN_PATH = "/api/v0/users/login"
POW_CHALLENGE_PATH = "/api/v0/chat/create_pow_challenge"
COMPLETION_PATH = "/api/v0/chat/completion"
SESSION_CREATE_PATH = "/api/v0/chat_session/create"
SESSION_FETCH_PATH = "/api/v0/chat_session/fetch_page"
HISTORY_PATH = "/api/v0/chat/history_messages"
CURRENT_USER_PATH = "/api/v0/users/current"


@dataclass
class TurnResult:
    """Hasil satu giliran percakapan."""

    text: str
    thinking: str
    response_id: str


def _default_cookies(explicit: Optional[str] = None) -> Optional[str]:
    """Tentukan cookie browser yang dipakai.

    Urutan:
      1. argumen ``cookies``
      2. env ``DEEPSEEK_COOKIES``
      3. file ``cookie.txt`` di folder proyek (dibuat manual dari browser)

    Tanpa cookie, endpoint ``create_pow_challenge`` diblokir AWS WAF dengan
    HTTP 202 - tidak ada error lain yang sampai ke pengguna.
    """
    if explicit:
        return explicit.strip() or None

    env = os.environ.get("DEEPSEEK_COOKIES")
    if env and env.strip():
        return env.strip()

    # File cookie.txt di folder proyek atau di cwd.
    candidates = [
        os.path.join(os.getcwd(), "cookie.txt"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cookie.txt"),
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read().strip()
            if text:
                return text
        except OSError:
            continue
    return None


class DeepSeekWebClient:
    """Minimal async client for chat.deepseek.com."""

    def __init__(
        self,
        token: Optional[str] = None,
        *,
        token_file: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        user_agent: str = DEFAULT_UA,
        base_url: str = BASE_URL,
        locale: str = "en_US",
        device_id: Optional[str] = None,
        cookies: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.locale = locale
        self.user_agent = user_agent
        self.device_id = device_id or uuid.uuid4().hex
        self.token_file = token_file
        #: Cookie mentah dari browser, mis. "aws-waf-token=...; ds_session_id=..."
        self.cookies = _default_cookies(cookies)
        #: Backend PoW yang terakhir dipakai ("native" / "python").
        self.last_pow_backend: Optional[str] = None
        #: Jeda minimum antar-pesan (detik) agar tidak kena rate limit.
        self._min_interval = min_interval()
        self._last_chat_at = 0.0
        #: Serialisasi pembacaan/penulisan ``_last_chat_at``. Tanpa ini dua
        #: request bersamaan menghitung jeda dari timestamp yang sama lalu
        #: berangkat bersamaan, sehingga throttle tidak berguna.
        self._throttle_lock = asyncio.Lock()
        self._http = client
        self._owns_http = client is None

        self.token_state: Optional[TokenState] = None
        if token:
            self.token_state = TokenState(token=token, email=email)
        elif token_file:
            try:
                self.token_state = TokenState.load(token_file)
            except FileNotFoundError:
                self.token_state = None

    # ------------------------------------------------------------------ infra

    async def __aenter__(self) -> "DeepSeekWebClient":
        await self._ensure_http()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = make_client()
            self._owns_http = True
        return self._http

    def _headers(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        headers = client_headers(
            user_agent=self.user_agent,
            locale=self.locale,
            cookies=self.cookies,
        )
        if self.token_state and self.token_state.token:
            headers["authorization"] = f"Bearer {self.token_state.token}"
        if extra:
            headers.update(extra)
        return headers

    # ------------------------------------------------------------------- auth

    @property
    def token(self) -> Optional[str]:
        return self.token_state.token if self.token_state else None

    async def login(self, email: Optional[str] = None, password: Optional[str] = None) -> TokenState:
        """Authenticate and cache the bearer token."""
        email = email or self.email
        password = password or self.password
        if not email or not password:
            raise AuthError("login() requires email and password")

        client = await self._ensure_http()
        body = {
            "email": email,
            "password": password,
            "device_id": self.device_id,
            "os": "web",
        }
        response = await client.post(
            self.base_url + LOGIN_PATH,
            json=body,
            headers=self._headers(),
        )
        payload = self._parse_json(response, context="login")

        biz_data = unwrap_envelope(payload)
        # The login endpoint double-encodes: biz_data is a JSON *string*.
        if isinstance(biz_data, str):
            try:
                biz_data = json.loads(biz_data)
            except json.JSONDecodeError as exc:
                raise AuthError(f"Login response was not valid JSON: {exc}") from exc
        if not isinstance(biz_data, dict):
            raise AuthError(f"Unexpected login payload: {biz_data!r}"[:400])

        inner = biz_data.get("biz_data") or biz_data
        token = inner.get("token") if isinstance(inner, dict) else None
        if not token:
            raise AuthError(
                f"Login succeeded but no token was returned: {str(biz_data)[:300]}"
            )

        user = (inner.get("user") or {}) if isinstance(inner, dict) else {}
        state = TokenState(
            token=token,
            user_id=str(user.get("id")) if user.get("id") is not None else None,
            email=user.get("email") or email,
        )
        self.token_state = state
        self.email = email
        self.password = password
        if self.token_file:
            state.save(self.token_file)
        return state

    async def ensure_token(self) -> str:
        """Return a token, logging in if necessary."""
        if self.token_state and self.token_state.token:
            return self.token_state.token
        if self.email and self.password:
            state = await self.login()
            return state.token
        raise AuthError(
            "No token available. Pass token=..., set token_file=..., "
            "or provide email/password."
        )

    async def get_current_user(self) -> dict[str, Any]:
        client = await self._ensure_http()
        token = await self.ensure_token()
        response = await client.get(
            self.base_url + CURRENT_USER_PATH,
            headers=self._headers({"authorization": f"Bearer {token}"}),
        )
        return unwrap_envelope(self._parse_json(response, context="users/current")) or {}

    # -------------------------------------------------------------------- pow

    async def create_pow_challenge(self, target_path: str) -> PowChallenge:
        """Ask the server for a fresh proof-of-work challenge."""
        client = await self._ensure_http()
        token = await self.ensure_token()
        response = await client.post(
            self.base_url + POW_CHALLENGE_PATH,
            json={"target_path": target_path},
            headers=self._headers({"authorization": f"Bearer {token}"}),
        )
        biz_data = unwrap_envelope(
            self._parse_json(response, context="create_pow_challenge")
        )
        challenge = (biz_data or {}).get("challenge")
        if not isinstance(challenge, dict):
            raise DeepSeekWebError(f"Challenge missing from response: {biz_data!r}"[:400])
        return PowChallenge.from_api(challenge)

    async def _pow_header(self, target_path: str) -> dict[str, str]:
        """Return ``{"x-ds-pow-response": ...}`` untuk ``target_path``.

        CATATAN PENTING: challenge PoW bersifat **sekali pakai**. Memakai ulang
        jawaban yang sama membuat server membalas stream kosong (tanpa error),
        jadi setiap request wajib meminta challenge baru. Ini terverifikasi
        lewat pengujian: request kedua dengan header sama menghasilkan 0 event,
        sedangkan challenge baru selalu berhasil.
        """
        challenge = await self.create_pow_challenge(target_path)

        # Solver PoW adalah kerja CPU murni: native ~0,1 detik, fallback Python
        # bisa menit-an. Menjalankannya langsung di sini akan membekukan event
        # loop (semua request lain, termasuk /healthz, ikut berhenti).
        # ``to_thread`` melepas GIL lewat ctypes / interpreter dan menjaga
        # server tetap responsif; ``wait_for`` memberi batas waktu.
        timeout = pow_timeout()
        try:
            coro = asyncio.to_thread(solve_to_answer, challenge, target_path)
            if timeout > 0:
                answer = await asyncio.wait_for(coro, timeout=timeout)
            else:
                answer = await coro
        except asyncio.TimeoutError as exc:
            raise DeepSeekWebError(
                f"PoW melebihi batas waktu {timeout:g} detik "
                "(DEEPSEEK_POW_TIMEOUT). Solver native tidak tersedia atau "
                "difficulty terlalu tinggi."
            ) from exc
        except PowError as exc:
            raise DeepSeekWebError(str(exc)) from exc

        self.last_pow_backend = answer.backend
        return {"x-ds-pow-response": answer.to_header_value()}

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _parse_json(response: httpx.Response, *, context: str) -> Any:
        if response.status_code == 202:
            # AWS WAF interstitial; the API itself answers 200.
            #
            # Penyebab paling umum: cookie browser (aws-waf-token) hilang atau
            # kedaluwarsa. Pesannya dibuat eksplisit karena tanpa ini pengguna
            # hanya melihat "coba lagi" tanpa tahu apa yang harus diperbaiki.
            raise NetworkError(
                f"{context}: diblokir AWS WAF (HTTP 202 challenge). "
                "Cookie browser diperlukan: simpan cookie 'aws-waf-token' dari "
                "chat.deepseek.com (DevTools > Application > Cookies) ke file "
                "'cookie.txt' di folder proyek, atau set env DEEPSEEK_COOKIES. "
                "Kalau cookie sudah ada, kemungkinan kedaluwarsa - ambil ulang.",
                payload=response.text[:300],
            )
        if response.status_code >= 500:
            raise NetworkError(
                f"{context}: server error HTTP {response.status_code}",
                payload=response.text[:300],
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise NetworkError(
                f"{context}: expected JSON, got {response.headers.get('content-type')!r}",
                payload=response.text[:300],
            ) from exc

    async def _post_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        context: str,
        with_pow: bool = False,
    ) -> Any:
        client = await self._ensure_http()
        token = await self.ensure_token()
        headers = self._headers({"authorization": f"Bearer {token}"})
        if with_pow:
            headers.update(await self._pow_header(path))
        response = await client.post(self.base_url + path, json=body, headers=headers)
        return unwrap_envelope(self._parse_json(response, context=context))

    # --------------------------------------------------------------- sessions

    async def create_session(self, *, model_type: str = "default") -> str:
        """Create a chat session and return its id.

        Respons berbentuk ``{"chat_session": {"id": ...}, "ttl_seconds": ...}``.
        """
        data = await self._post_json(
            SESSION_CREATE_PATH,
            {"model_type": model_type},
            context="chat_session/create",
        ) or {}

        session = data.get("chat_session") if isinstance(data, dict) else None
        if isinstance(session, dict) and session.get("id"):
            return str(session["id"])

        for key in ("id", "chat_session_id"):
            if isinstance(data, dict) and data.get(key):
                return str(data[key])

        raise DeepSeekWebError(f"No session id in response: {data!r}"[:400])

    async def list_sessions(self, count: int = 20) -> list[dict[str, Any]]:
        client = await self._ensure_http()
        token = await self.ensure_token()
        response = await client.get(
            self.base_url + SESSION_FETCH_PATH,
            params={"count": count},
            headers=self._headers({"authorization": f"Bearer {token}"}),
        )
        data = unwrap_envelope(self._parse_json(response, context="fetch_page")) or {}
        return data.get("chat_sessions") or data.get("sessions") or []

    # ------------------------------------------------------------------- chat

    async def _throttle(self) -> None:
        """Jaga jarak antar-request chat agar tidak kena ``rate_limit_reached``.

        DeepSeek membalas "Messages too frequent. Try again later." bila dua
        pesan dikirim terlalu rapat. Jeda ini berlaku per-instance client.
        Atur lewat ``DEEPSEEK_MIN_INTERVAL`` (detik, 0 = matikan).
        """
        interval = self._min_interval if self._min_interval else min_interval()
        if interval <= 0:
            return
        # Kunci harus dipegang *selama* menunggu, bukan hanya saat menulis:
        # kalau tidak, permintaan kedua ikut tidur dengan jeda yang sama dan
        # keduanya berangkat berbarengan.
        async with self._throttle_lock:
            now = time.monotonic()
            wait = interval - (now - self._last_chat_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_chat_at = time.monotonic()

    async def stream_completion(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        model_type: str = "default",
        thinking_enabled: bool = False,
        search_enabled: bool = False,
        ref_file_ids: Optional[list[str]] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a prompt and yield raw SSE events as dicts.

        Each yielded dict has the shape ``{"event": str, "data": <parsed>}``.
        """
        await self._throttle()
        if chat_session_id is None:
            chat_session_id = await self.create_session(model_type=model_type)

        body = {
            "chat_session_id": chat_session_id,
            "model_type": model_type,
            "prompt": prompt,
            "ref_file_ids": ref_file_ids or [],
            "thinking_enabled": thinking_enabled,
            "search_enabled": search_enabled,
        }

        # PENTING: field "parent_message_id" harus DIHILANGKAN, bukan diisi
        # null, untuk pesan pertama. DeepSeek membedakan keduanya: mengirim
        # null membuat server membalas stream kosong (55 byte, tanpa error).
        # Field ini hanya perlu diisi untuk menyambung pesan sebelumnya.
        if parent_message_id is not None:
            body["parent_message_id"] = parent_message_id

        client = await self._ensure_http()
        token = await self.ensure_token()
        headers = self._headers(
            {
                "authorization": f"Bearer {token}",
                "accept": "text/event-stream",
            }
        )
        headers.update(await self._pow_header(COMPLETION_PATH))

        async with client.stream(
            "POST",
            self.base_url + COMPLETION_PATH,
            json=body,
            headers=headers,
        ) as response:
            if response.status_code == 202:
                raise NetworkError(
                    "completion blocked by AWS WAF (HTTP 202 challenge)"
                )
            if response.status_code >= 400:
                text = (await response.aread()).decode("utf-8", "replace")
                raise NetworkError(
                    f"completion failed HTTP {response.status_code}", payload=text[:400]
                )

            # DeepSeek kadang membalas JSON (bukan SSE) walau status 200.
            # Kasus terpenting: 40303 CHALLENGE_REQUIRED (hCaptcha). Tanpa
            # pemeriksaan ini, error tersebut tampak seperti "jawaban kosong".
            content_type = (response.headers.get("content-type") or "").lower()
            if "text/event-stream" not in content_type:
                raw = await response.aread()
                text = raw.decode("utf-8", "replace")
                raise self._explain_non_stream_error(text)

            # Kumpulkan event sambil memeriksa apakah ada isi jawaban.
            # Stream yang kosong total berarti rate limit, bukan jawaban kosong.
            saw_content = False
            rate_limit_msg: Optional[str] = None
            async for event in _iter_sse(response):
                if event["event"] == "message":
                    saw_content = True
                elif event["event"] == "hint":
                    hint = event["data"] or {}
                    if (
                        hint.get("finish_reason") == "rate_limit_reached"
                        or hint.get("type") == "error"
                    ):
                        rate_limit_msg = hint.get("content") or rate_limit_msg
                yield event

            if not saw_content:
                raise RateLimitError(
                    "DeepSeek: "
                    + (
                        rate_limit_msg
                        or "membalas tanpa isi jawaban (kemungkinan rate limit)"
                    )
                    + ". Tunggu sebentar sebelum mencoba lagi, atau naikkan "
                    "DEEPSEEK_MIN_INTERVAL.",
                    code=429,
                )

    @staticmethod
    def _explain_non_stream_error(text: str) -> DeepSeekWebError:
        """Ubah balasan JSON non-SSE menjadi error yang bisa ditindaklanjuti."""
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return NetworkError(
                "completion tidak mengembalikan SSE", payload=text[:400]
            )

        code = payload.get("code")
        msg = payload.get("msg") or ""
        data = payload.get("data") or {}

        if code == 40303 or msg == "CHALLENGE_REQUIRED":
            provider = data.get("provider") or "captcha"
            return ChallengeRequiredError(
                f"DeepSeek meminta verifikasi {provider} (40303 CHALLENGE_REQUIRED). "
                "Buka chat.deepseek.com di browser, selesaikan captcha, lalu coba lagi. "
                "Bila sering terjadi, kurangi laju permintaan.",
                code=code,
                payload=payload,
            )
        if code == 40029:
            return NetworkError(
                "IP dibatasi oleh DeepSeek (40029 IP_ACCESS_RESTRICTED). "
                "Coba lagi nanti atau ganti jaringan.",
                code=code,
                payload=payload,
            )
        if code == 50006:
            return NetworkError(
                "Akun sedang dibisukan sementara oleh DeepSeek (50006 MUTED).",
                code=code,
                payload=payload,
            )
        return NetworkError(
            f"completion gagal: code={code} msg={msg or 'tanpa pesan'}",
            code=code,
            payload=payload,
        )

    @staticmethod
    def _looks_rate_limited(raw: bytes) -> bool:
        """Deteksi stream kosong akibat rate limit.

        DeepSeek kadang membalas SSE yang nyaris kosong (beberapa ratus byte,
        tanpa event ``message``) ketika IP/akun terlalu sering 요청. Ini bukan
        error di sisi server, sehingga harus dikenali sendiri.
        """
        text = raw.decode("utf-8", "replace")
        # Tidak ada event "message" sama sekali -> tidak ada isi jawaban.
        return "event: message" not in text and '"type":"RESPONSE"' not in text

    async def chat(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Convenience wrapper that returns the full assistant answer text."""
        accumulator = DeltaAccumulator()
        async for event in self.stream_completion(
            prompt, chat_session_id=chat_session_id, **kwargs
        ):
            if event["event"] == "message":
                accumulator.apply_event(event["data"])
        return accumulator.answer_text()

    async def chat_turn(
        self,
        prompt: str,
        *,
        chat_session_id: str,
        parent_message_id: Optional[str | int] = None,
        **kwargs: Any,
    ) -> "TurnResult":
        """Satu giliran percakapan.

        Return :class:`TurnResult` berisi ``text`` (jawaban), ``thinking``
        (proses berpikir model expert, kosong untuk mode instant), dan
        ``response_id`` untuk dipakai sebagai ``parent_message_id`` berikutnya.
        """
        accumulator = DeltaAccumulator()
        response_id: Optional[str] = None

        # Server mengharapkan integer untuk parent_message_id (422 bila string).
        parent: Optional[int] = None
        if parent_message_id not in (None, "", "null"):
            try:
                parent = int(parent_message_id)
            except (TypeError, ValueError):
                parent = None

        async for event in self.stream_completion(
            prompt,
            chat_session_id=chat_session_id,
            parent_message_id=parent,
            **kwargs,
        ):
            if event["event"] == "ready":
                data = event["data"] or {}
                if data.get("response_message_id") is not None:
                    response_id = str(data["response_message_id"])
            elif event["event"] == "message":
                accumulator.apply_event(event["data"])

        if response_id is None:
            # Fallback: ambil dari objek respons asisten.
            mid = accumulator.response.get("message_id")
            response_id = str(mid) if mid is not None else ""
        return TurnResult(
            text=accumulator.answer_text(),
            thinking=accumulator.thinking_text(),
            response_id=response_id,
        )

    async def stream_turn(
        self,
        prompt: str,
        *,
        chat_session_id: str,
        parent_message_id: Optional[str | int] = None,
        on_response_id: Optional[Any] = None,
        include_thinking: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[tuple[str, str]]:
        """Streaming satu giliran; yield ``(jenis, teks)``.

        ``jenis`` bernilai ``"thinking"`` untuk proses berpikir model expert
        atau ``"content"`` untuk jawaban terlihat. Model instant hanya
        menghasilkan ``"content"``.

        ``on_response_id`` dipanggil sekali dengan id pesan asisten begitu
        event ``ready`` diterima, supaya pemanggil bisa menyimpannya sebagai
        ``parent_message_id`` untuk giliran berikutnya.
        """
        accumulator = DeltaAccumulator()
        emitted_answer = 0
        emitted_think = 0
        reported = False

        parent: Optional[int] = None
        if parent_message_id not in (None, "", "null"):
            try:
                parent = int(parent_message_id)
            except (TypeError, ValueError):
                parent = None

        async for event in self.stream_completion(
            prompt,
            chat_session_id=chat_session_id,
            parent_message_id=parent,
            **kwargs,
        ):
            name = event["event"]

            if name == "ready":
                data = event["data"] or {}
                rid = data.get("response_message_id")
                if rid is not None and not reported and on_response_id is not None:
                    on_response_id(rid)
                    reported = True
                continue

            if name != "message":
                continue

            accumulator.apply_event(event["data"])

            if include_thinking:
                think = accumulator.thinking_text()
                if len(think) > emitted_think:
                    yield ("thinking", think[emitted_think:])
                    emitted_think = len(think)

            answer = accumulator.answer_text()
            if len(answer) > emitted_answer:
                yield ("content", answer[emitted_answer:])
                emitted_answer = len(answer)

        # Bila "ready" tidak pernah datang, ambil id dari objek respons.
        if not reported and on_response_id is not None:
            mid = accumulator.response.get("message_id")
            if mid is not None:
                on_response_id(mid)

    async def stream_text(
        self,
        prompt: str,
        *,
        chat_session_id: Optional[str] = None,
        include_thinking: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Yield only the newly generated text, chunk by chunk.

        The web app sends JSON-Patch deltas, so we track the accumulated answer
        and emit the difference after each event.
        """
        accumulator = DeltaAccumulator()
        emitted_answer = 0
        emitted_think = 0

        async for event in self.stream_completion(
            prompt, chat_session_id=chat_session_id, **kwargs
        ):
            if event["event"] != "message":
                continue
            accumulator.apply_event(event["data"])

            if include_thinking:
                think = accumulator.thinking_text()
                if len(think) > emitted_think:
                    yield think[emitted_think:]
                    emitted_think = len(think)

            answer = accumulator.answer_text()
            if len(answer) > emitted_answer:
                yield answer[emitted_answer:]
                emitted_answer = len(answer)


async def _iter_sse(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parse a ``text/event-stream`` body into ``{"event", "data"}`` dicts."""
    event_name: Optional[str] = None
    data_lines: list[str] = []

    async for raw_line in response.aiter_lines():
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                yield {"event": event_name or "message", "data": _try_json(payload)}
                event_name, data_lines = None, []
            continue
        if line.startswith(":"):  # comment / keep-alive
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    if data_lines:
        yield {"event": event_name or "message", "data": _try_json("\n".join(data_lines))}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _extract_text(event: dict[str, Any]) -> str:
    """Legacy helper: best-effort text from a single event."""
    data = event.get("data")
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    content = data.get("content") or data.get("text") or ""
    return content if isinstance(content, str) else ""





