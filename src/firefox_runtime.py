from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

_DARWIN_DEV_BINARY = "/Applications/Firefox Developer Edition.app/Contents/MacOS/firefox"
_DARWIN_STABLE_BINARY = "/Applications/Firefox.app/Contents/MacOS/firefox"


def clear_profile_locks(profile_path: str) -> None:
    """Remove stale Firefox lock files from a profile directory.

    Firefox leaves .parentlock / parent.lock / lock behind when it crashes or
    is killed.  A locked profile refuses to open even with a fresh binary, which
    produces the misleading 'Process unexpectedly closed with status 0' error.
    Call this once before creating a webdriver.Firefox instance.
    """
    for lock_name in (".parentlock", "parent.lock", "lock"):
        lp = os.path.join(profile_path, lock_name)
        try:
            if os.path.exists(lp):
                os.remove(lp)
        except OSError:
            pass


def _profile_expected_binary(profile_path: str) -> str:
    """Return the binary path recorded in compatibility.ini for this profile.

    Firefox stores the last-used application path in compatibility.ini.
    When a profile was last used with Developer Edition we MUST keep using
    Developer Edition — stable Firefox refuses to open a profile written by a
    newer build.

    Returns an empty string when the file is absent or unreadable.
    """
    compat = Path(profile_path) / "compatibility.ini"
    if not compat.exists():
        return ""
    try:
        text = compat.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    for line in text.splitlines():
        if line.startswith("LastPlatformDir=") or line.startswith("LastAppDir="):
            value = line.split("=", 1)[1].strip()
            if "Developer Edition" in value or "FirefoxDeveloperEdition" in value:
                return _DARWIN_DEV_BINARY
    return ""


def _candidate_binaries(preferred_binary: str = "", profile_path: str = "") -> list[str]:
    candidates: list[str] = []

    # Profile-based binary takes highest priority — prevents version downgrade.
    if profile_path:
        profile_bin = _profile_expected_binary(profile_path)
        if profile_bin:
            candidates.append(profile_bin)

    if preferred_binary:
        candidates.append(preferred_binary)

    env_binary = os.environ.get("MPV2_FIREFOX_BINARY", "").strip()
    if env_binary:
        candidates.append(env_binary)

    if platform.system() == "Darwin":
        candidates.extend(
            [
                _DARWIN_DEV_BINARY,
                _DARWIN_STABLE_BINARY,
                str(Path.home() / "Applications/Firefox Developer Edition.app/Contents/MacOS/firefox"),
                str(Path.home() / "Applications/Firefox.app/Contents/MacOS/firefox"),
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


def resolve_firefox_binary(preferred_binary: str = "", profile_path: str = "") -> str:
    """Return the first existing Firefox binary suitable for the given profile.

    Args:
        preferred_binary: Explicit binary path from account config (may be empty).
        profile_path: Firefox profile directory.  When provided, compatibility.ini
            is consulted so the correct binary edition is always selected.
    """
    for candidate in _candidate_binaries(preferred_binary, profile_path=profile_path):
        if os.path.exists(candidate):
            return candidate
    return ""
