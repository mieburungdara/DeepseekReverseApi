"""Keccak-f[1600] 23-ronde - varian DeepSeek "DeepSeekHashV1".

BUKAN SHA3-256 standar. Perbedaan krusial hanya jumlah ronde:

    worker DeepSeek:  for (let i = 1; i < 24; i++)  ->  RC[1..23], 23 ronde
    SHA3-256 standar: RC[0..23], 24 ronde

Parameter sponge identik dengan SHA3-256: rate 136 byte, padding 0x06,
output 32 byte.

Validasi: dengan 24 ronde, fungsi ini HARUS sama persis dengan
``hashlib.sha3_256`` (dicek di tests/test_offline.py).
"""

from __future__ import annotations

MASK = (1 << 64) - 1

RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

#: Rotasi per lane, diindeks [x][y] sesuai konvensi Keccak (x = kolom, y = baris).
ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)

#: Indeks ronde yang dijalankan DeepSeek (i = 1..23).
DEEPSEEK_ROUNDS = tuple(range(1, 24))
#: Indeks ronde SHA3-256 standar (untuk verifikasi).
SHA3_ROUNDS = tuple(range(24))

RATE = 136          # 1088 bit
OUTPUT_LEN = 32
PAD = 0x06


def _rol(v: int, n: int) -> int:
    n &= 63
    if n == 0:
        return v & MASK
    return ((v << n) | (v >> (64 - n))) & MASK


def keccak_f(a: list[int], rounds=DEEPSEEK_ROUNDS) -> None:
    """Keccak-f[1600] in-place untuk daftar indeks ronde tertentu.

    State ``a`` adalah 25 lane 64-bit, diindeks ``x + 5*y``.
    """
    for rnd in rounds:
        # ---- theta ----
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] ^= d[x]

        # ---- rho + pi ----
        # B[y][(2x+3y) mod 5] = ROT(A[x][y], r[x][y])
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(a[x + 5 * y], ROT[x][y])

        # ---- chi ----
        for x in range(5):
            for y in range(5):
                a[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]
                )

        # ---- iota ----
        a[0] ^= RC[rnd]


def keccak_hex(message: bytes, rounds=DEEPSEEK_ROUNDS) -> str:
    """Sponge Keccak dengan jumlah ronde tertentu -> 64 karakter hex."""
    a = [0] * 25

    padded = bytearray(message)
    padded.append(PAD)
    while len(padded) % RATE != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    for off in range(0, len(padded), RATE):
        block = padded[off:off + RATE]
        for i in range(RATE // 8):
            a[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        keccak_f(a, rounds)

    out = bytearray()
    for i in range(OUTPUT_LEN // 8):
        out += a[i].to_bytes(8, "little")
    return out.hex()


def deepseek_hash_v1(message: str | bytes) -> str:
    """Hash DeepSeek (Keccak 23 ronde). Input str di-encode UTF-8."""
    if isinstance(message, str):
        message = message.encode("utf-8")
    return keccak_hex(message, DEEPSEEK_ROUNDS)


def sha3_256_hex(message: bytes) -> str:
    """Pembanding: 24 ronde = SHA3-256 standar (harus == hashlib)."""
    return keccak_hex(message, SHA3_ROUNDS)
