"""Uji _pid_alive: PID hidup, PID mati, PID tidak valid, dan PID induk sendiri."""

import os
import subprocess
import sys
import time

sys.path.insert(0, ".")
from deepseek_web_api.server import _pid_alive, _getppid

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:42s} got={got} want={want}")
    if not ok:
        failures.append(label)


print("=== _pid_alive ===")

# 1. proses ini sendiri -> hidup
check("PID sendiri", _pid_alive(os.getpid()), True)

# 2. PID induk (cmd/pwsh) -> hidup
ppid = _getppid()
print(f"  (PPID = {ppid})")
if ppid > 0:
    check("PID induk", _pid_alive(ppid), True)

# 3. PID yang pasti tidak ada (di atas batas wajar Windows)
check("PID 999999 (tidak ada)", _pid_alive(999999), False)

# 4. PID 0 / negatif
check("PID 0", _pid_alive(0), False)
check("PID -1", _pid_alive(-1), False)

# 5. Proses yang baru kita matikan
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
time.sleep(1)
check("proses anak (hidup)", _pid_alive(child.pid), True)
child.kill()
child.wait()
time.sleep(0.5)
check("proses anak (sudah mati)", _pid_alive(child.pid), False)

# 6. Proses sistem yang tidak bisa dibuka (PID 4) -> False.
# Itu perilaku yang benar: OpenProcess ditolak karena hak akses, dan kita
# lebih memilih melaporkan "tidak hidup" daripada mematikan diri salah.
if os.name == "nt":
    print(f"  (PID 4 -> {_pid_alive(4)}, akses ditolak untuk proses sistem)")

print()
if failures:
    print("GAGAL:", failures)
    sys.exit(1)
print("SEMUA LULUS - _pid_alive benar, tanpa WinError 87")
