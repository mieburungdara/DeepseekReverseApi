"""Parser delta SSE untuk chat.deepseek.com.

Protokol sebenarnya (diverifikasi terhadap stream sungguhan):

* Event ``message`` membawa JSON-Patch dengan kunci opsional:
  ``{"p": path, "o": "SET"|"APPEND"|"BATCH", "v": value}``
* **``p`` dan ``o`` bersifat stateful**: bila tidak ada, nilainya diwarisi dari
  delta sebelumnya. Contoh nyata: setelah
  ``{"p":"response/fragments/-1/content","o":"APPEND","v":"ah"}``, event
  berikutnya cukup ``{"v":","}`` dan tetap menambah ke content yang sama.
* Path **tidak** diawali ``/`` dan berakar pada objek ``response``.
  Indeks ``-1`` berarti elemen terakhir.
* ``BATCH`` berisi daftar delta dengan path **relatif** terhadap path batch.
"""

from __future__ import annotations

from typing import Any, Optional

#: Fragment yang berisi jawaban terlihat (modul 87287 ``RESPONSE_TYPES``).
ANSWER_FRAGMENT_TYPES = {"RESPONSE", "TEMPLATE_RESPONSE"}
#: Fragment reasoning.
THINK_FRAGMENT_TYPES = {"THINK"}


class DeltaAccumulator:
    """Menerapkan aliran delta ke pohon respons asisten."""

    def __init__(self) -> None:
        self.response: dict[str, Any] = {}
        self._last_path: str = ""
        self._last_op: str = "SET"

    # ------------------------------------------------------------ public API
    def apply(self, delta: Any) -> None:
        """Terapkan satu objek delta (boleh BATCH)."""
        if isinstance(delta, dict):
            self._apply_dict(delta, parent_path="")
        elif isinstance(delta, list):
            for item in delta:
                self.apply(item)

    def apply_event(self, data: Any) -> None:
        """Terapkan payload event ``message``.

        Payload dari server dibungkus: ``{"v": {delta}}`` atau langsung delta.
        Path di dalamnya berakar pada objek ``response``, jadi akar akumulator
        adalah ``data["v"]["response"]`` (bila ada).
        """
        if isinstance(data, dict) and "v" in data and "response" in (
            data["v"] if isinstance(data["v"], dict) else {}
        ):
            # Event pembuka: seluruh objek respons dikirim sekaligus.
            self.response = data["v"]["response"]
            # Delta berikutnya memakai path relatif terhadap `response`,
            # dan mewarisi p/o dari delta terakhir yang *bermakna*.
            self._last_path = ""
            self._last_op = "SET"
            return

        if isinstance(data, dict) and data.get("p") == "response":
            # BATCH pada root respons. Item di dalamnya memakai path relatif,
            # dan item tanpa "o" berarti SET (bukan mewarisi APPEND dari delta
            # sebelumnya). State warisan disimpan & dikembalikan.
            inner = data.get("v")
            if isinstance(inner, list):
                saved_path, saved_op = self._last_path, self._last_op
                for item in inner:
                    if not isinstance(item, dict):
                        continue
                    item = dict(item)
                    item.setdefault("o", "SET")
                    self._apply_dict(item, parent_path="")
                self._last_path, self._last_op = saved_path, saved_op
            return

        if isinstance(data, dict) and str(data.get("p", "")).startswith("response/"):
            # Path absolut terhadap respons.
            self._apply_dict(data, parent_path="", strip_prefix="response/")
            return

        self.apply(data)

    # ------------------------------------------------------------- internals
    def _apply_dict(self, delta: dict[str, Any], parent_path: str, strip_prefix: str = "") -> None:
        # Warisi p/o bila tidak ada (perilaku stateful parser DeepSeek).
        if "p" in delta:
            raw_path = str(delta.get("p") or "")
            if strip_prefix and raw_path.startswith(strip_prefix):
                raw_path = raw_path[len(strip_prefix):]
            self._last_path = raw_path
        else:
            raw_path = self._last_path

        if "o" in delta:
            self._last_op = str(delta.get("o") or "SET")
        op = self._last_op

        value = delta.get("v")

        if op == "BATCH":
            items = value if isinstance(value, list) else []
            for item in items:
                if isinstance(item, dict):
                    self._apply_dict(item, parent_path=_join(parent_path, raw_path))
            return

        full_path = _join(parent_path, raw_path)
        self._set(full_path, op, value)

    def _set(self, path: str, op: str, value: Any) -> None:
        parts = [p for p in path.split("/") if p != ""]

        # Root: objek respons utuh
        if not parts:
            if op == "SET" and isinstance(value, dict):
                self.response = value
            elif op == "APPEND" and isinstance(self.response, dict) and isinstance(value, dict):
                self.response.update(value)
            return

        node: Any = self.response
        for i, part in enumerate(parts[:-1]):
            key = _key(part, node)
            if isinstance(node, list):
                idx = _index(key, len(node))
                if idx is None:
                    return
                if node[idx] is None or not isinstance(node[idx], (dict, list)):
                    node[idx] = {}
                node = node[idx]
            else:
                if key not in node or not isinstance(node[key], (dict, list)):
                    node[key] = {}
                node = node[key]

        last = parts[-1]
        if isinstance(node, list):
            idx = _index(_key(last, node), len(node))
            if idx is None:
                return
            if op == "APPEND" and isinstance(node[idx], str):
                node[idx] += _as_str(value)
            else:
                node[idx] = value
        elif isinstance(node, dict):
            if op == "APPEND":
                existing = node.get(last)
                if isinstance(existing, str):
                    node[last] = existing + _as_str(value)
                elif existing is None:
                    node[last] = _as_str(value)
                else:
                    node[last] = _as_str(existing) + _as_str(value)
            else:
                node[last] = value

    # -------------------------------------------------------------- views
    @property
    def fragments(self) -> list[dict[str, Any]]:
        frags = self.response.get("fragments")
        if not isinstance(frags, list):
            return []
        return [f for f in frags if isinstance(f, dict)]

    def answer_text(self) -> str:
        return "".join(
            f.get("content") or ""
            for f in self.fragments
            if f.get("type") in ANSWER_FRAGMENT_TYPES
            and isinstance(f.get("content"), str)
        )

    def thinking_text(self) -> str:
        return "".join(
            f.get("content") or ""
            for f in self.fragments
            if f.get("type") in THINK_FRAGMENT_TYPES
            and isinstance(f.get("content"), str)
        )

    @property
    def status(self) -> Optional[str]:
        status = self.response.get("status")
        return status if isinstance(status, str) else None


def _join(parent: str, child: str) -> str:
    parent = (parent or "").strip("/")
    child = (child or "").strip("/")
    if parent and child:
        return f"{parent}/{child}"
    return parent or child


def _key(part: str, container: Any) -> Any:
    return int(part) if part.lstrip("-").isdigit() else part


def _index(key: Any, length: int) -> Optional[int]:
    """Ubah kunci path menjadi indeks list yang valid (dukung -1)."""
    if isinstance(key, int):
        idx = key if key >= 0 else length + key
        return idx if 0 <= idx < length else None
    return None


def _as_str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)
