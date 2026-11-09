@echo off
REM ============================================================
REM  start.bat - Jalankan server OpenAI-compatible DeepSeek
REM
REM  Pemakaian:
REM    start.bat              -> port default 17395, mode open
REM    start.bat 19999        -> port tertentu
REM    start.bat auto         -> cari port bebas otomatis
REM    start.bat any          -> mode API key: wajib diisi
REM    start.bat strict       -> mode API key: harus sama
REM
REM  CATATAN: skrip ini memakai `python` LANGSUNG (bukan
REM  .venv\Scripts\python.exe) supaya hanya ada SATU proses.
REM  Launcher venv men-spawn proses anak, sehingga menutup jendela
REM  bisa meninggalkan proses yatim yang menahan port.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title DeepSeek Web API - Server

echo ============================================================
echo   DeepSeek Web - OpenAI Compatible API
echo ============================================================
echo.

REM --- Cari interpreter Python -----------------------------------------
REM Pakai python.exe asli di dalam venv (bukan launcher di Scripts\).
set "PY="
if exist ".venv\pyvenv.cfg" (
    for /f "tokens=2 delims==" %%A in ('findstr /B /C:"home = " ".venv\pyvenv.cfg"') do (
        set "HOME_DIR=%%A"
    )
)
if defined HOME_DIR (
    if exist "!HOME_DIR!\python.exe" set "PY=!HOME_DIR!\python.exe"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    echo [GAGAL] Python tidak ditemukan.
    echo   Jalankan setup.bat terlebih dahulu.
    pause
    exit /b 1
)

REM --- Validasi dependensi ---------------------------------------------
"%PY%" -c "import fastapi, uvicorn, httpx" >nul 2>&1
if errorlevel 1 (
    echo [GAGAL] Dependensi belum terpasang.
    echo   Jalankan setup.bat terlebih dahulu.
    pause
    exit /b 1
)

REM --- Cookie browser (wajib untuk melewati AWS WAF) --------------------
REM Endpoint create_pow_challenge diblokir WAF (HTTP 202) tanpa cookie
REM browser yang valid. Cookie diambil dari DevTools > Application >
REM Cookies > https://chat.deepseek.com (atau Local Storage).
REM Client membaca cookie.txt secara otomatis, jadi di sini cukup dicek
REM keberadaannya. Env DEEPSEEK_COOKIES (kalau diset) mengungguli file ini.
if exist "cookie.txt" (
    echo [OK] cookie.txt ada - client akan memuatnya otomatis.
) else (
    echo [PERINGATAN] cookie.txt tidak ada.
    echo   Tanpa cookie, endpoint PoW diblokir AWS WAF ^(HTTP 202^).
    echo   Ambil cookie dari browser: DevTools ^> Application ^>
    echo   Cookies ^> https://chat.deepseek.com, lalu simpan ke cookie.txt.
)

REM --- Validasi token ---------------------------------------------------
set "TOKEN_OK="
if exist "token.json" set "TOKEN_OK=1"
if defined DEEPSEEK_TOKEN set "TOKEN_OK=1"
if not defined TOKEN_OK (
    echo [PERINGATAN] Token belum ada.
    echo   Jalankan set-token.bat dulu, atau server akan menolak chat.
    echo.
    choice /C YN /N /M "Lanjutkan tanpa token? [Y/N] "
    if !errorlevel! equ 2 (
        echo Dibatalkan.
        pause
        exit /b 1
    )
    echo.
) else (
    if exist "token.json" set "DEEPSEEK_TOKEN_FILE=token.json"
    echo [OK] Token ditemukan.
)

REM --- Tentukan argumen ------------------------------------------------
set "ARGS="
if /I "%~1"=="auto" (
    set "ARGS=--auto-port"
    echo [OK] Mode auto-port
) else if /I "%~1"=="any" (
    set "ARGS=--auth any"
    echo [OK] Mode API key: any
) else if /I "%~1"=="strict" (
    set "ARGS=--auth strict"
    echo [OK] Mode API key: strict
) else if not "%~1"=="" (
    set "ARGS=--port %~1"
    echo [OK] Port %~1
) else (
    echo [OK] Port default 17395
)
echo.

echo ============================================================
echo   KOLOM API KEY DI AGEN AI
echo ============================================================
echo.
echo   Banyak agen ^(Open WebUI, LobeChat, Cursor, dll.^) mewajibkan
echo   API key. Pada mode default, isi dengan string APA SAJA:
echo.
echo     sk-lokal        123        bebas
echo.
echo   Base URL : http://127.0.0.1:17395/v1
echo   Model    : deepseek-chat  /  deepseek-reasoner
echo.
echo ============================================================
echo   Server berjalan. Tekan CTRL+C untuk menghentikan.
echo   Menutup jendela ini juga menghentikan server.
echo ============================================================
echo.

REM --- Jalankan server -------------------------------------------------
REM Tidak memakai `start` / proses latar: server menjadi proses anak dari
REM jendela ini, jadi CTRL+C dan penutupan jendela benar-benar mematikannya.
"%PY%" -m deepseek_web_api.server %ARGS%
set "EXITCODE=%errorlevel%"

echo.
if not "%EXITCODE%"=="0" (
    echo ============================================================
    echo   SERVER BERHENTI DENGAN ERROR ^(kode %EXITCODE%^)
    echo ============================================================
    echo.
    echo   Bila penyebabnya port bentrok, coba:
    echo     start.bat auto
    echo.
    echo   Periksa sisa proses dengan:
    echo     stop.bat
    echo.
)
echo Server dihentikan.
pause
endlocal & exit /b %EXITCODE%
