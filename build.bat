@echo off
REM ============================================================
REM  build.bat - Kompilasi ulang solver PoW native (opsional)
REM
REM  DLL yang sudah disertakan di repo biasanya cukup. Jalankan ini
REM  hanya bila ingin membangun ulang dari sumber, atau bila arsitektur
REM  CPU Anda berbeda (flag -march=native membuat DLL teroptimasi
REM  untuk mesin pembuatnya).
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   BUILD solver PoW native
echo ============================================================
echo.

REM --- Cari compiler ---------------------------------------------------
set "CC="
for %%C in (gcc clang) do (
    where %%C >nul 2>&1
    if not errorlevel 1 if not defined CC set "CC=%%C"
)

if not defined CC (
    echo [GAGAL] Compiler C tidak ditemukan.
    echo.
    echo   Install salah satu:
    echo     - MinGW-w64  ^(gcc^)  : https://www.mingw-w64.org/
    echo     - LLVM/Clang         : https://llvm.org/
    echo     - Visual Studio Build Tools
    echo.
    echo   Tanpa DLL, solver otomatis memakai Python murni
    echo   ^(sekitar 1000x lebih lambat, ~2 menit per challenge^).
    echo.
    pause
    exit /b 1
)

echo [1/3] Compiler: %CC%
%CC% --version 2>&1 | findstr /R "." | findstr /N "^" | findstr "^1:"
echo.

if not exist "native" mkdir native

REM --- Deteksi platform ------------------------------------------------
set "EXT=dll"
set "FLAGS=-O3 -shared"
if not "%OS%"=="Windows_NT" set "EXT=so"

echo [2/3] Mengompilasi native\deepseek_pow.%EXT% ...
%CC% %FLAGS% -o native\deepseek_pow.%EXT% native\deepseek_pow.c
if errorlevel 1 (
    echo.
    echo [GAGAL] Kompilasi gagal. Lihat pesan di atas.
    pause
    exit /b 1
)
echo.

REM --- Verifikasi ------------------------------------------------------
echo [3/3] Verifikasi hasil kompilasi ...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0,'.'); from deepseek_web_api.pow import native_available, native_hash; import hashlib; assert native_available(), 'DLL tidak termuat'; ok = native_hash('abc', deepseek=False) == hashlib.sha3_256(b'abc').hexdigest(); print('  DLL termuat, SHA3-256 24-ronde cocok:', ok); sys.exit(0 if ok else 1)"
    if errorlevel 1 (
        echo.
        echo [GAGAL] DLL tidak lolos verifikasi.
        pause
        exit /b 1
    )
) else (
    echo   [lewati] .venv belum ada, jalankan setup.bat untuk verifikasi penuh.
)

echo.
echo ============================================================
echo   BUILD SELESAI
echo ============================================================
echo.
pause
endlocal
