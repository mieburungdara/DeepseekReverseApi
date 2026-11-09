"""Pengelola sesi percakapan.

Sebelumnya setiap request membuat sesi DeepSeek baru, sehingga:
  * riwayat di server DeepSeek menumpuk percakapan sekali-jalan,
  * konteks multi-turn hilang (model tidak tahu pesan sebelumnya),
  * ``parent_message_id`` tidak pernah dikirim sehingga server tidak bisa
    menyambung pesan.

Modul ini memetakan **satu percakapan klien** ke **satu sesi DeepSeek**, dan
melacak ``response_message_id`` terakhir agar bisa dipakai sebagai
``parent_message_id`` pada giliran berikutnya.

Kunci percakapan ditentukan dari request klien:

1. Header ``X-Conversation-Id`` (eksplisit, paling andal).
2. ``user`` pada body request (OpenAI memakai ini sebagai ID pengguna akhir).
3. Hash dari pesan sistem pertama + pesan pengguna pertama — sehingga
   percakapan yang sama tetap memakai sesi yang sama meski klien tidak
   mengirim penanda apa pun.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Conversation:
    """Satu percakapan klien -> satu sesi DeepSeek."""

    key: str
    chat_session_id: str
    #: ``response_message_id`` asisten terakhir (induk pesan berikutnya).
    parent_message_id: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    turns: int = 0


def conversation_key(
    body: dict[str, Any],
    headers: dict[str, str],
) -> str:
    """Tentukan kunci percakapan dari request klien."""
    # 1. Header eksplisit
    explicit = headers.get("x-conversation-id") or headers.get("x-session-id")
    if explicit and explicit.strip():
        return "hdr:" + explicit.strip()

    # 2. Field "user" ala OpenAI
    user = body.get("user")
    if isinstance(user, str) and user.strip():
        return "user:" + user.strip()

    # 3. Sidik jari dari pesan awal
    messages = body.get("messages") or []
    system_text = ""
    first_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        content = str(content or "")
        if role == "system" and not system_text:
            system_text = content
        elif role == "user" and not first_user:
            first_user = content
        if system_text and first_user:
            break

    seed = f"{system_text}\x00{first_user}"
    if not seed.strip("\x00"):
        seed = json.dumps(messages[:1], ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:16]
    return "fp:" + digest


class SessionStore:
    """Penyimpanan sesi in-memory, aman untuk dipakai async."""

    def __init__(self, *, max_conversations: int = 200, ttl_seconds: float = 6 * 3600) -> None:
        self._items: dict[str, Conversation] = {}
        self._max = max_conversations
        self._ttl = ttl_seconds

    # ------------------------------------------------------------------ API
    def get(self, key: str) -> Optional[Conversation]:
        self._evict()
        return self._items.get(key)

    def put(
        self,
        key: str,
        chat_session_id: str,
        *,
        parent_message_id: Optional[int] = None,
        model_type: str = "default",
    ) -> Conversation:
        conv = self._items.get(key)
        now = time.time()
        if conv is None:
            conv = Conversation(key=key, chat_session_id=chat_session_id)
            self._items[key] = conv
        conv.chat_session_id = chat_session_id
        conv.parent_message_id = parent_message_id
        conv.updated_at = now
        conv.turns += 1
        self._evict()
        return conv

    def touch(self, key: str, *, parent_message_id: Optional[int]) -> None:
        conv = self._items.get(key)
        if conv is None:
            return
        conv.updated_at = time.time()
        conv.turns += 1
        if parent_message_id is not None:
            conv.parent_message_id = parent_message_id

    def forget(self, key: str) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()

    def stats(self) -> dict[str, Any]:
        self._evict()
        return {
            "conversations": len(self._items),
            "max": self._max,
            "ttl_seconds": self._ttl,
            "items": [
                {
                    "key": c.key,
                    "chat_session_id": c.chat_session_id,
                    "parent_message_id": c.parent_message_id,
                    "turns": c.turns,
                    "age_seconds": round(time.time() - c.created_at, 1),
                }
                for c in self._items.values()
            ],
        }

    # ------------------------------------------------------------ internal
    def _evict(self) -> None:
        now = time.time()
        # Buang yang kedaluwarsa
        expired = [k for k, c in self._items.items() if now - c.updated_at > self._ttl]
        for key in expired:
            self._items.pop(key, None)
        # Buang yang paling lama dipakai bila melebihi batas
        if len(self._items) > self._max:
            ordered = sorted(self._items.items(), key=lambda kv: kv[1].updated_at)
            for key, _ in ordered[: len(self._items) - self._max]:
                self._items.pop(key, None)


#: Store global yang dipakai server.
STORE = SessionStore()
