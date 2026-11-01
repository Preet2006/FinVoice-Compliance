"""The offline smoke script must keep passing, and must never reach a provider."""

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND_DIR / "scripts" / "smoke_test.py"


def test_smoke_script_passes_offline():
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]
    assert "ALL CHECKS PASSED" in done.stdout
    assert "[FAIL]" not in done.stdout


def test_smoke_script_has_no_live_option():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "argparse" not in source and "sys.argv" not in source  # nothing can switch it to a real provider
