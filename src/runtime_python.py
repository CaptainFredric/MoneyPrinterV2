from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def _candidate_paths() -> list[Path]:
    candidates = [
        Path(os.environ.get("MPV2_PYTHON", "")).expanduser() if os.environ.get("MPV2_PYTHON") else None,
        ROOT_DIR / ".runtime-venv" / "bin" / "python",
        ROOT_DIR / "venv" / "bin" / "python",
        ROOT_DIR / ".venv" / "bin" / "python",
    ]
    return [candidate for candidate in candidates if candidate]


def _is_usable_python(candidate: Path) -> bool:
    if not candidate.exists():
        return False
    try:
        probe = subprocess.run(
            [str(candidate), "-c", "import requests, termcolor, selenium, webdriver_manager"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
        return probe.returncode == 0
    except Exception:
        return False


def resolve_runtime_python() -> str:
    for candidate in _candidate_paths():
        if _is_usable_python(candidate):
            return str(candidate)
    return sys.executable