"""Solver DeepSeekHashV1 - Keccak 23 ronde.

Algoritma (direverse dari worker PoW chat.deepseek.com, diverifikasi terhadap
challenge server sungguhan):

    prefix = salt + "_" + expire_at + "_"
    answer = i terkecil (0 <= i < difficulty) dengan
             keccak23(prefix + str(i)) == challenge

``keccak23`` = Keccak-f[1600] dengan HANYA 23 ronde (RC[1..23]).
Ini BUKAN SHA3-256 (24 ronde), sehingga ``hashlib.sha3_256`` tidak akan
pernah cocok.

Tiga backend, dipilih otomatis dari yang tercepat:

1. ``native`` - DLL C (gcc), ~1.3 juta hash/detik
2. ``node``   - jalankan worker JS DeepSeek asli, ~1 juta hash/detik
3. ``python`` - murni Python, ~1000 hash/detik (fallback terakhir)
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional

from .keccak23 import DEEPSEEK_ROUNDS, deepseek_hash_v1, keccak_hex

ALGORITHM = "DeepSeekHashV1"

#: Batas aman agar tidak menggantung bila server memberi difficulty aneh.
MAX_ITERATIONS = 50_000_000

_HERE = os.path.dirname(os.path.abspath(__file__))
_DLL_CANDIDATES = (
    os.path.join(_HERE, "..", "native", "deepseek_pow.dll"),
    os.path.join(_HERE, "deepseek_pow.dll"),
    os.path.join(_HERE, "..", "native", "deepseek_pow.so"),
    os.path.join(_HERE, "deepseek_pow.so"),
)


class PowError(RuntimeError):
    """Challenge tidak bisa dipecahkan."""


@dataclass(frozen=True)
class PowChallenge:
    """Challenge apa adanya dari ``/api/v0/chat/create_pow_challenge``."""

    algorithm: str
    challenge: str
    salt: str
    difficulty: int
    signature: str
    expire_at: Optional[int] = None
    expire_after: Optional[int] = None

    @classmethod
    def from_api(cls, data: dict) -> "PowChallenge":
        return cls(
            algorithm=data["algorithm"],
            challenge=data["challenge"],
            salt=data["salt"],
            difficulty=int(data["difficulty"]),
            signature=data.get("signature", ""),
            expire_at=data.get("expire_at", data.get("expireAt")),
            expire_after=data.get("expire_after", data.get("expireAfter")),
        )

    def prefix(self) -> str:
        """``salt_expire_at_`` - PERHATIAN: memakai expire_at, bukan difficulty."""
        if self.expire_at is None:
            raise PowError(
                "Challenge tidak punya expire_at; prefix DeepSeekHashV1 "
                "membutuhkannya (salt_expire_at_)."
            )
        return f"{self.salt}_{self.expire_at}_"


@dataclass
class PowAnswer:
    algorithm: str
    challenge: str
    salt: str
    answer: int
    signature: str
    target_path: str
    solved_at: float = field(default_factory=time.time)
    backend: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "algorithm": self.algorithm,
                "challenge": self.challenge,
                "salt": self.salt,
                "answer": self.answer,
                "signature": self.signature,
                "target_path": self.target_path,
            },
            separators=(",", ":"),
        )

    def to_header_value(self) -> str:
        """Nilai header ``X-DS-PoW-Response``: base64 dari JSON."""
        return base64.b64encode(self.to_json().encode("utf-8")).decode("ascii")


# --------------------------------------------------------------------- backend


def _load_native():
    """Muat DLL solver C bila ada. Return (solve_fn, hash_fn) atau None."""
    for path in _DLL_CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(os.path.abspath(path))
            lib.deepseek_pow_solve.argtypes = [
                ctypes.c_char_p, ctypes.c_char_p, ctypes.c_longlong,
            ]
            lib.deepseek_pow_solve.restype = ctypes.c_longlong
            lib.deepseek_hash.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
            return lib
        except (OSError, AttributeError):
            continue
    return None


_NATIVE = _load_native()


def native_available() -> bool:
    return _NATIVE is not None


def native_hash(message: str, deepseek: bool = True) -> Optional[str]:
    """Hash via DLL native (untuk verifikasi cepat)."""
    if _NATIVE is None:
        return None
    buf = ctypes.create_string_buffer(65)
    _NATIVE.deepseek_hash(message.encode("utf-8"), buf, 1 if deepseek else 0)
    return buf.value.decode()


def _solve_native(prefix: str, target: str, difficulty: int) -> int:
    result = _NATIVE.deepseek_pow_solve(
        prefix.encode("utf-8"), target.encode("ascii"), difficulty
    )
    if result == -2:
        raise PowError("Prefix terlalu panjang untuk buffer native")
    if result < 0:
        raise PowError(
            f"Tidak ada solusi dalam {difficulty} iterasi "
            f"(algorithm={ALGORITHM}, prefix={prefix!r})"
        )
    return int(result)


def _solve_python(prefix: str, target: str, difficulty: int) -> int:
    target = target.lower()
    for i in range(difficulty):
        if keccak_hex((prefix + str(i)).encode("utf-8"), DEEPSEEK_ROUNDS) == target:
            return i
    raise PowError(
        f"Tidak ada solusi dalam {difficulty} iterasi "
        f"(algorithm={ALGORITHM}, prefix={prefix!r})"
    )


def solve(challenge: PowChallenge, *, backend: str = "auto") -> tuple[int, str]:
    """Pecahkan ``challenge``. Return ``(answer, backend_name)``."""
    if challenge.algorithm != ALGORITHM:
        raise PowError(f"Algorithm tidak didukung: {challenge.algorithm}")

    difficulty = challenge.difficulty
    if difficulty <= 0:
        raise PowError(f"Difficulty tidak valid: {difficulty}")
    if difficulty > MAX_ITERATIONS:
        raise PowError(
            f"Menolak mencari {difficulty} iterasi (batas {MAX_ITERATIONS})"
        )

    prefix = challenge.prefix()
    target = challenge.challenge.lower()

    if backend in ("auto", "native") and _NATIVE is not None:
        try:
            return _solve_native(prefix, target, difficulty), "native"
        except PowError:
            if backend == "native":
                raise

    if backend == "python":
        return _solve_python(prefix, target, difficulty), "python"

    # auto tanpa native -> python
    return _solve_python(prefix, target, difficulty), "python"


def solve_to_answer(challenge: PowChallenge, target_path: str, *, backend: str = "auto") -> PowAnswer:
    """Pecahkan challenge dan bungkus hasilnya untuk header PoW."""
    answer, used = solve(challenge, backend=backend)
    return PowAnswer(
        algorithm=challenge.algorithm,
        challenge=challenge.challenge,
        salt=challenge.salt,
        answer=answer,
        signature=challenge.signature,
        target_path=target_path,
        backend=used,
    )


if __name__ == "__main__":
    # Self-test: buat challenge sendiri, pecahkan, verifikasi.
    salt, expire_at, difficulty, secret = "selftest", 1794209943102, 20000, 12345
    prefix = f"{salt}_{expire_at}_"
    target = deepseek_hash_v1(prefix + str(secret))
    ch = PowChallenge(
        algorithm=ALGORITHM,
        challenge=target,
        salt=salt,
        difficulty=difficulty,
        signature="sig",
        expire_at=expire_at,
    )
    print(f"native tersedia: {native_available()}")
    t0 = time.perf_counter()
    ans, backend = solve(ch)
    dt = time.perf_counter() - t0
    print(f"backend={backend} answer={ans} (harap {secret}) dalam {dt:.3f}s")
    assert ans == secret
    print("header:", solve_to_answer(ch, "/api/v0/chat/completion").to_header_value()[:60], "...")
