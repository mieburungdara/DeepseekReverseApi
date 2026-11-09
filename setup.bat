@echo off
REM ============================================================
REM  setup.bat - Siapkan environment sekali saja
REM  Membuat virtualenv, install dependensi, cek Python.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   SETUP DeepSeek Web API
echo ============================================================
echo.

REM --- 1. Cari Python -------------------------------------------------
set "PYEXE="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>&1
    if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
    echo [GAGAL] Python tidak ditemukan di PATH.
    echo.
    echo   Install Python 3.10+ dari https://www.python.org/downloads/
    echo   Pastikan centang "Add Python to PATH" saat instalasi.
    echo.
    pause
    exit /b 1
)

echo [1/4] Python ditemukan: %PYEXE%
%PYEXE% --version
echo.

REM --- 2. Buat virtualenv ---------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo [2/4] Virtualenv .venv sudah ada, dilewati.
) else (
    echo [2/4] Membuat virtualenv .venv ...
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo [GAGAL] Tidak bisa membuat virtualenv.
        pause
        exit /b 1
    )
)
echo.

REM --- 3. Install dependensi ------------------------------------------
echo [3/4] Install dependensi dari requirements.txt ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [GAGAL] Instalasi dependensi gagal. Cek koneksi internet.
    pause
    exit /b 1
)
echo.

REM --- 4. Verifikasi ---------------------------------------------------
echo [4/4] Verifikasi instalasi ...
".venv\Scripts\python.exe" -m tests.test_offline
if errorlevel 1 (
    echo.
    echo [PERINGATAN] Sebagian test gagal. Cek pesan di atas.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   SETUP SELESAI
echo ============================================================
echo.
echo   Langkah berikutnya:
echo     1. login.bat      ^(ambil token DeepSeek^)
echo     2. start.bat      ^(jalankan server^)
echo.
pause
endlocal
