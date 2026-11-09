/*
 * deepseek_pow.c - DeepSeekHashV1 proof-of-work solver.
 *
 * Algoritma (hasil reverse engineering dari worker 76608 chat.deepseek.com):
 *
 *   prefix = salt + "_" + expire_at + "_"
 *   answer = i terkecil (0 <= i < difficulty) dengan
 *            keccak23(prefix + str(i)) == challenge
 *
 * "keccak23" = Keccak-f[1600] dengan HANYA 23 ronde (RC[1..23]), bukan 24
 * seperti SHA3-256 standar. Rate 136 byte, padding 0x06, output 32 byte.
 *
 * Build:
 *   gcc -O3 -march=native -shared -o deepseek_pow.dll deepseek_pow.c
 */

#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define RATE 136
#define OUTPUT_LEN 32

static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808AULL,
    0x8000000080008000ULL, 0x000000000000808BULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008AULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000AULL,
    0x000000008000808BULL, 0x800000000000008BULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800AULL, 0x800000008000000AULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL,
};

/* Rotasi rho, diindeks [x][y] */
static const int ROT[5][5] = {
    {0, 36, 3, 41, 18},
    {1, 44, 10, 45, 2},
    {62, 6, 43, 15, 61},
    {28, 55, 25, 21, 56},
    {27, 20, 39, 8, 14},
};

#define ROL64(v, n) (((n) == 0) ? (v) : (((v) << (n)) | ((v) >> (64 - (n)))))

static void keccak_f(uint64_t *a, int start_round, int end_round)
{
    int rnd, x, y;
    uint64_t c[5], d[5], b[25];

    for (rnd = start_round; rnd < end_round; rnd++) {
        /* theta */
        for (x = 0; x < 5; x++)
            c[x] = a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20];
        for (x = 0; x < 5; x++)
            d[x] = c[(x + 4) % 5] ^ ROL64(c[(x + 1) % 5], 1);
        for (x = 0; x < 5; x++)
            for (y = 0; y < 5; y++)
                a[x + 5 * y] ^= d[x];

        /* rho + pi */
        for (x = 0; x < 5; x++)
            for (y = 0; y < 5; y++)
                b[y + 5 * ((2 * x + 3 * y) % 5)] = ROL64(a[x + 5 * y], ROT[x][y]);

        /* chi */
        for (y = 0; y < 5; y++)
            for (x = 0; x < 5; x++)
                a[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]);

        /* iota */
        a[0] ^= RC[rnd];
    }
}

/*
 * Hash satu pesan. `out` menerima 32 byte digest.
 * rounds: 24 = SHA3-256 standar, 23 = DeepSeek (mulai dari ronde 1).
 */
static void keccak_hash(const uint8_t *msg, size_t len, uint8_t *out, int deepseek)
{
    uint64_t a[25];
    uint8_t block[RATE];
    size_t off;
    int i, start, end;

    memset(a, 0, sizeof(a));

    start = deepseek ? 1 : 0;
    end = 24;

    /* Absorb blok penuh */
    for (off = 0; off + RATE <= len; off += RATE) {
        for (i = 0; i < RATE / 8; i++) {
            uint64_t w = 0;
            memcpy(&w, msg + off + i * 8, 8);
            a[i] ^= w;
        }
        keccak_f(a, start, end);
    }

    /* Blok terakhir + padding */
    {
        size_t rem = len - off;
        memset(block, 0, RATE);
        memcpy(block, msg + off, rem);
        block[rem] ^= 0x06;
        block[RATE - 1] ^= 0x80;
        for (i = 0; i < RATE / 8; i++) {
            uint64_t w = 0;
            memcpy(&w, block + i * 8, 8);
            a[i] ^= w;
        }
        keccak_f(a, start, end);
    }

    /* Squeeze 32 byte */
    for (i = 0; i < OUTPUT_LEN / 8; i++)
        memcpy(out + i * 8, &a[i], 8);
}

static void to_hex(const uint8_t *in, int len, char *out)
{
    static const char *H = "0123456789abcdef";
    int i;
    for (i = 0; i < len; i++) {
        out[i * 2] = H[in[i] >> 4];
        out[i * 2 + 1] = H[in[i] & 0x0F];
    }
    out[len * 2] = '\0';
}

/*
 * Ekspor utama: cari jawaban PoW.
 *
 *   prefix    : string prefix (salt_expire_at_)
 *   target    : 64 karakter hex target
 *   difficulty: batas atas pencarian (eksklusif)
 *   threads   : jumlah thread (0/1 = single thread)
 *
 * Return: nilai i jika ketemu, -1 jika tidak.
 */
#ifdef _WIN32
__declspec(dllexport)
#endif
long long deepseek_pow_solve(const char *prefix, const char *target, long long difficulty)
{
    char buf[512];
    size_t plen = strlen(prefix);
    char hex[65];
    uint8_t digest[OUTPUT_LEN];
    long long i;

    if (plen + 24 >= sizeof(buf))
        return -2;

    memcpy(buf, prefix, plen);

    for (i = 0; i < difficulty; i++) {
        int n = snprintf(buf + plen, sizeof(buf) - plen, "%lld", i);
        size_t total = plen + (size_t)n;

        keccak_hash((const uint8_t *)buf, total, digest, 1);
        to_hex(digest, OUTPUT_LEN, hex);

        if (memcmp(hex, target, 64) == 0)
            return i;
    }
    return -1;
}

/*
 * Ekspor pembantu untuk verifikasi: hash satu string.
 * deepseek=1 -> 23 ronde, deepseek=0 -> SHA3-256 standar (24 ronde).
 */
#ifdef _WIN32
__declspec(dllexport)
#endif
void deepseek_hash(const char *msg, char *out_hex, int deepseek)
{
    uint8_t digest[OUTPUT_LEN];
    keccak_hash((const uint8_t *)msg, strlen(msg), digest, deepseek);
    to_hex(digest, OUTPUT_LEN, out_hex);
}
