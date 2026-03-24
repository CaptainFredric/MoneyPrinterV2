from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


def _candidate_binaries(preferred_binary: str = "") -> list[str]:
    candidates: list[str] = []

    if preferred_binary:
        candidates.append(preferred_binary)

    env_binary = os.environ.get("MPV2_FIREFOX_BINARY", "").strip()
    if env_binary:
        candidates.append(env_binary)

    if platform.system() == "Darwin":
        candidates.extend(
            [
                "/Applications/Firefox.app/Contents/MacOS/firefox",
                "/Applications/Firefox Developer Edition.app/Contents/MacOS/firefox",
                str(Path.home() / "Applications/Firefox.app/Contents/MacOS/firefox"),
                str(Path.home() / "Applications/Firefox Developer Edition.app/Contents/MacOS/firefox"),
            ]
        )

    which_firefox = shutil.which("firefox")
    if which_firefox:
        candidates.append(which_firefox)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def resolve_firefox_binary(preferred_binary: str = "") -> str:
    for candidate in _candidate_binaries(preferred_binary):
        if os.path.exists(candidate):
            return candidate
    return ""