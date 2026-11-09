# Reverse Engineering `chat.deepseek.com` → OpenAI-compatible API

Dokumen ini mencatat **apa yang dibongkar, dari mana, dan mengapa**, termasuk
koreksi besar terhadap pemahaman awal. Semua klaim di sini **diverifikasi
terhadap server sungguhan**.

- Target: `https://chat.deepseek.com`
- Build dianalisis: `commit-id f4c70f87`, `appVersion 2.7.1`
- Status: **PoW terpecahkan, chat & streaming terbukti berjalan end-to-end**

> **Etika.** Ini membongkar antarmuka web milik DeepSeek dan memakai sesi akun
> Anda sendiri. Antarmuka web bukan API publik: bisa berubah sewaktu-waktu dan
> pemakaian otomatis berisiko akun diblokir. Untuk produksi gunakan
> `platform.deepseek.com`.

---

## 1. Ringkasan: tiga kesalahan yang dikoreksi

Pemahaman awal saya (dari membaca bundle saja) **salah pada tiga hal krusial**.
Semuanya hanya ketahuan setelah diuji ke server asli.

| # | Dugaan awal | Kenyataan (terverifikasi) |
|---|---|---|
| 1 | Hash = SHA3-256 standar | **Keccak 23 ronde** (`RC[1..23]`), bukan 24 ronde |
| 2 | `prefix = salt + "_" + difficulty + "_"` | **`prefix = salt + "_" + expire_at + "_"`** |
| 3 | PoW boleh di-cache selama belum kedaluwarsa | **PoW sekali pakai**; pakai ulang → stream kosong |

Pelajaran: membaca kode minified tidak cukup. Loop `for(let i=1;i<24;i++)`
terlihat seperti "24 ronde" bagi mata yang tidak teliti, padahal itu **23
ronde** karena `i` mulai dari 1.

---

## 2. Bukti kode: algoritma PoW

### 2.1 Loop ronde yang menipu

Dari worker `76608.8f2a9fa413.js`:

```js
a = {
  keccak: (e = new Uint32Array(10), r = new Uint32Array(10), n = new Uint32Array(2), t => {
      for (let i = 1; i < 24; i++)          // <-- 23 iterasi: 1,2,...,23
          B({A:t,C:e,D:r,W:n}),             // theta
          m({A:t,C:e,W:n}),                 // rho+pi
          y({A:t,C:e}),                     // chi
          b({A:t,I:i});                     // iota, RC[i]
      e.fill(0), r.fill(0), n.fill(0)
  }),
  state: new Uint32Array(50),
  queue: o.hp.allocUnsafe(u),               // u = 200 - capacity/4
  queueOffset: 0
};
```

Dengan `capacity: 256` → `u = 200 - 64 = 136` byte (rate SHA3-256), padding
`0x06`, output 32 byte. **Semua parameter sama dengan SHA3-256, kecuali jumlah
ronde.**

Keccak-f standar menjalankan `RC[0..23]` (24 ronde). DeepSeek menjalankan
`RC[1..23]` — **ronde 0 tidak pernah dijalankan**.

### 2.2 Rumus final

```js
// worker: f = "".concat(r,"_").concat(i,"_")
let f = `${salt}_${expireAt}_`;            // BUKAN difficulty!
for (i = 0; i < difficulty; i++)
    if (keccak23(f + String(i)).hex() === challenge) return i;
```

Verifikasi empiris: dengan `salt=411fb8f88b6fac7094d0`,
`expire_at=1794209943102`, `difficulty=144000`, ditemukan `answer=53240` dan
`keccak23(prefix + "53240")` **persis sama** dengan `challenge`.

### 2.3 Bukti bahwa 23 ronde itu nyata

Implementasi 24 ronde saya **identik dengan `hashlib.sha3_256`** untuk berbagai
panjang input (0, 3, 135, 136, 300 byte). Dengan 23 ronde hasilnya berbeda.
Ini membuktikan kode Keccak saya benar dan perbedaannya memang di jumlah ronde.

Konfirmasi pihak ketiga (repo publik):
- `lza6/DeepSeek-271-APP-2api` — menyebut **"23轮Keccak"** (Keccak 23 ronde)
- `xlocux/deepseek-pow-poc` — `pow.js` memakai `prefix = salt_expire_at_` dan
  WASM `deepseek_hash_v1`
- `Fly143/deepseek-free-api` — `pow_solver.js` memakai prefix yang sama

---

## 3. Bukti kode: header PoW

Modul `84212`:

```js
d = (e, t, n) => ["X-DS-PoW-Response", n(JSON.stringify({
    algorithm: e.algorithm, challenge: e.challenge, salt: e.salt,
    answer: e.answer, signature: e.signature, target_path: t,
}))];
```

Encoder dipasang sebagai `base64Encode: btoa`, jadi nilainya **base64 dari
JSON**, bukan JSON mentah.

---

## 4. PoW sekali pakai

Diuji dengan tiga request berurutan memakai satu `DeepSeekWebClient`:

| Skenario | Hasil |
|---|---|
| Cache dipakai ulang (header sama) | request ke-2 & ke-3 → **0 event**, tanpa error |
| Challenge baru tiap request | ketiga request → **8 event**, sukses |

Server membalas HTTP 200 dengan stream kosong — kegagalan senyap yang mudah
disalahartikan sebagai masalah parser. Implementasi sekarang **selalu** meminta
challenge baru.

---

## 5. Protokol streaming (SSE)

### 5.1 Event

| event | isi |
|---|---|
| `ready` | `request_message_id`, `response_message_id`, `model_type` |
| `message` | delta JSON-Patch (lihat bawah) |
| `update_session` | `updated_at` |
| `title` | judul sesi |
| `close` | `click_behavior` |
| `toast` / `hint` | pesan error/informasi |

### 5.2 Bentuk delta — **stateful**

Tiga hal yang tidak terlihat dari kode dan hanya ketahuan dari stream nyata:

1. **Path tanpa slash depan**: `response/fragments/-1/content`
2. **`p` dan `o` diwarisi**: setelah delta pertama, event berikutnya cukup
   `{"v": ","}` dan tetap menambah ke content yang sama
3. **Indeks `-1`** berarti elemen terakhir
4. **Payload dibungkus**: event pembuka berbentuk `{"v": {"response": {...}}}`

Contoh urutan nyata:

```
{"v": {"response": {"message_id": 2, "status": "WIP",
                    "fragments": [{"type":"RESPONSE","content":"Mer"}]}}}
{"p": "response/fragments/-1/content", "o": "APPEND", "v": "ah"}
{"v": ","}          <- p & o diwarisi
{"v": " biru"}
{"v": "."}
{"p": "response", "o": "BATCH",
 "v": [{"p": "accumulated_token_usage", "v": 54}, {"p": "status", "v": "FINISHED"}]}
{"p": "response/status", "o": "SET", "v": "FINISHED"}
```

Di dalam `BATCH`, item tanpa `o` berarti **SET**, bukan mewarisi `APPEND`.

Tipe fragment: jawaban ada di `RESPONSE` / `TEMPLATE_RESPONSE`, reasoning di
`THINK`.

---

## 6. Autentikasi & sesi

- Login: `POST /api/v0/users/login` → token (nesting ganda, `biz_data` berupa
  *string* JSON).
- Token dipakai sebagai `Authorization: Bearer <token>`.
- `POST /api/v0/chat_session/create` → `biz_data.chat_session.id`.
- **Multi-turn**: kirim `parent_message_id` dari `ready.response_message_id`
  giliran sebelumnya. **Harus integer** — string menghasilkan **HTTP 422**.
- AWS WAF bisa memblokir (HTTP 202). Menyertakan cookie browser
  (`aws-waf-token`) membuat request lolos.

---

## 7. Performa solver

| Backend | Throughput | 144000 iterasi |
|---|---|---|
| Python murni | ~1.040 hash/s | ~138 s |
| **DLL C (gcc -O3)** | **~1.345.000 hash/s** | **~0,11 s** |

Solver native: `native/deepseek_pow.c` → `deepseek_pow.dll`.
Bangun ulang:

```bat
gcc -O3 -march=native -shared -o native/deepseek_pow.dll native/deepseek_pow.c
```

---

## 8. Hasil verifikasi end-to-end

| Uji | Hasil |
|---|---|
| PoW solve (144000) | ✅ ~0,11 s, jawaban cocok |
| `/users/current` | ✅ `it*****00@gmail.com` |
| Non-streaming chat | ✅ "Merah, biru, kuning." |
| Streaming bertahap | ✅ chunk `7`,`×`,`8`,`=`,`56` |
| Status akhir | ✅ `FINISHED` |
| Multi-turn | ✅ *"Nama Anda Budi, seperti yang Anda sebutkan tadi."* |
| Server OpenAI `/v1/chat/completions` | ✅ non-stream & stream |
| Header PoW base64 | ✅ diterima server |

---

## 9. Peta endpoint

| Method | Path | Token | PoW |
|---|---|---|---|
| POST | `/api/v0/users/login` | tidak | tidak |
| GET | `/api/v0/users/current` | ya | tidak |
| POST | `/api/v0/chat/create_pow_challenge` | ya | tidak |
| POST | `/api/v0/chat/completion` | ya | **ya** |
| POST | `/api/v0/chat_session/create` | ya | tidak |
| GET | `/api/v0/chat_session/fetch_page` | ya | tidak |
| POST | `/api/v0/file/upload_file` | ya | ya |

Kode error: `40002` MISSING_TOKEN, `40003` INVALID_TOKEN, `40300`
POW_HEADER_ERROR, `40301` INVALID_POW_RESPONSE, `40303` CHALLENGE_REQUIRED,
`40304` CHALLENGE_FAILED, `40029` IP_ACCESS_RESTRICTED.

---

## 10. Kalau frontend berubah

Urutan paling rapuh:

1. Jumlah ronde Keccak → cari `for(let i=1;i<24;i++)` di worker PoW
2. Bentuk prefix → cari `concat(r,"_").concat(i,"_")`
3. Nama event SSE & bentuk delta → cari `NewSSEEventName`
4. `model_type` valid → cari `["default","expert","vision"]`
5. Header PoW → cari `X-DS-PoW-Response`

Cara cepat memvalidasi: jalankan `python -m deepseek_web_api.pow` (self-test),
lalu `python -m tests.live_probe`.
