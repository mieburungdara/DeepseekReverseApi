@echo off
REM ============================================================
REM  start.bat - Jalankan server OpenAI-compatible DeepSeek
REM
REM  Pemakaian:
REM    start.bat                 -> port default 17395
REM    start.bat 19999           -> port tertentu
REM    start.bat auto            -> cari port bebas otomatis
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title DeepSeek Web API - Server

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo   DeepSeek Web - OpenAI Compatible API
echo ============================================================
echo.

REM --- Validasi Python -------------------------------------------------
"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [GAGAL] Python tidak ditemukan.
    echo   Jalankan setup.bat terlebih dahulu.
    pause
    exit /b 1
)

REM --- Validasi token --------------------------------------------------
set "TOKEN_OK="
if exist "token.json" set "TOKEN_OK=1"
if defined DEEPSEEK_TOKEN set "TOKEN_OK=1"
if not defined TOKEN_OK (
    echo [PERINGATAN] Token belum ada.
    echo.
    echo   Server tetap bisa dijalankan, tapi setiap request chat akan
    echo   gagal dengan error autentikasi.
    echo.
    echo   Jalankan login.bat dulu untuk mendapatkan token.
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
    echo.
)

REM --- Tentukan argumen port -------------------------------------------
set "PORTARG="
if /I "%~1"=="auto" (
    set "PORTARG=--auto-port"
    echo [OK] Mode auto-port: akan mencari port bebas.
) else if /I "%~1"=="any" (
    set "PORTARG=--auth any"
    echo [OK] Mode API key: any ^(key wajib, isi bebas^)
) else if /I "%~1"=="strict" (
    set "PORTARG=--auth strict"
    echo [OK] Mode API key: strict ^(harus sama dengan DEEPSEEK_API_KEY^)
) else if not "%~1"=="" (
    set "PORTARG=--port %~1"
    echo [OK] Memakai port %~1
) else (
    echo [OK] Memakai port default 17395
    echo [OK] Mode API key: open ^(isi kolom API key dengan string APA SAJA^)
)
echo.

echo ============================================================
echo   PENTING - kolom API key di agen AI
echo ============================================================
echo.
echo   Banyak agen ^(Open WebUI, LobeChat, Cursor, dll.^) mewajibkan
echo   API key diisi. Pada mode default, isi dengan string APA SAJA:
echo.
echo     sk-lokal        atau    123        atau    bebas
echo.
echo   Base URL  : http://127.0.0.1:17395/v1
echo   Model     : deepseek-chat  /  deepseek-reasoner
echo.
echo ============================================================
echo   Server berjalan. Tekan CTRL+C untuk menghentikan.
echo ============================================================
echo.

REM --- Jalankan server -------------------------------------------------
"%PY%" -m deepseek_web_api.server %PORTARG%

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
)
echo Server dihentikan.
pause
endlocal
