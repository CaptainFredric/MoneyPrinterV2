from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"
BACKUP_ROOT = ROOT_DIR / ".mp" / "profile_backups"
RETENTION_COUNT = int(os.environ.get("MPV2_PROFILE_BACKUP_RETENTION", "3"))
MAX_BACKUP_AGE_SECONDS = int(os.environ.get("MPV2_PROFILE_BACKUP_MAX_AGE_SECONDS", str(24 * 60 * 60)))
METADATA_NAME = "mpv2_profile_backup_metadata.json"

EXCLUDED_DIR_NAMES = {
    "cache2",
    "crashes",
    "datareporting",
    "jumpListCache",
    "minidumps",
    "offlineCache",
    "shader-cache",
    "startupCache",
    "thumbnails",
    "weave",
}
EXCLUDED_FILE_NAMES = {
    ".parentlock",
    "lock",
    "parent.lock",
}
STATE_FILES = [
    "cookies.sqlite",
    "cookies.sqlite-shm",
    "cookies.sqlite-wal",
    "sessionstore.jsonlz4",
    "sessionCheckpoints.json",
    "storage/default",
    "storage/permanent",
    "storage-sync-v2.sqlite",
    "storage-sync-v2.sqlite-shm",
    "storage-sync-v2.sqlite-wal",
    "webappsstore.sqlite",
    "webappsstore.sqlite-shm",
    "webappsstore.sqlite-wal",
    "prefs.js",
    "user.js",
    "containers.json",
    "permissions.sqlite",
    "permissions.sqlite-shm",
    "permissions.sqlite-wal",
    "xulstore.json",
]


def load_accounts() -> list[dict]:
    try:
        with open(TWITTER_CACHE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data.get("accounts", [])
    except Exception:
        return []


def resolve_accounts(identifier: str) -> list[dict]:
    accounts = load_accounts()
    lowered = (identifier or "all").strip().lower()
    if lowered == "all":
        return accounts
    return [
        account
        for account in accounts
        if account.get("id", "").lower() == lowered or account.get("nickname", "").lower() == lowered
    ]


def is_firefox_profile_running(profile_path: Path) -> bool:
    try:
        import subprocess

        result = subprocess.run(
            ["pgrep", "-af", "Firefox.app/Contents/MacOS/firefox|/firefox"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in (result.stdout or "").splitlines():
            if str(profile_path) in line:
                return True
        return False
    except Exception:
        return False


def count_auth_cookies(profile_path: Path) -> int:
    cookies_db = profile_path / "cookies.sqlite"
    if not cookies_db.exists():
        return 0

    with tempfile.NamedTemporaryFile(prefix="mpv2_cookies_", suffix=".sqlite", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    try:
        shutil.copy2(cookies_db, temp_path)
        conn = sqlite3.connect(str(temp_path))
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM moz_cookies
                WHERE (host LIKE '%x.com%' OR host LIKE '%twitter.com%')
                  AND name IN ('auth_token', 'ct0')
                """
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _state_snapshot(profile_path: Path) -> dict:
    snapshot: dict[str, dict] = {}
    for rel_name in STATE_FILES:
        candidate = profile_path / rel_name
        if not candidate.exists():
            continue
        stats = candidate.stat()
        snapshot[rel_name] = {
            "mtime_ns": stats.st_mtime_ns,
            "size": stats.st_size,
            "is_dir": candidate.is_dir(),
        }
    return snapshot


def _iter_profile_files(profile_path: Path):
    for current_root, dir_names, file_names in os.walk(profile_path):
        current_path = Path(current_root)
        dir_names[:] = sorted(name for name in dir_names if name not in EXCLUDED_DIR_NAMES)
        for file_name in sorted(file_names):
            if file_name in EXCLUDED_FILE_NAMES:
                continue
            file_path = current_path / file_name
            yield file_path, file_path.relative_to(profile_path)


def _account_backup_dir(account: dict) -> Path:
    nickname = str(account.get("nickname", account.get("id", "unknown"))).strip() or "unknown"
    return BACKUP_ROOT / nickname


def _read_backup_metadata(archive_path: Path) -> dict:
    if not archive_path.exists():
        return {}
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            member = archive.getmember(METADATA_NAME)
            extracted = archive.extractfile(member)
            if extracted is None:
                return {}
            return json.loads(extracted.read().decode("utf-8"))
    except Exception:
        return {}


def list_backups(account: dict) -> list[dict]:
    backup_dir = _account_backup_dir(account)
    if not backup_dir.exists():
        return []

    backups = []
    for archive_path in sorted(backup_dir.glob("*.tar.gz"), reverse=True):
        metadata = _read_backup_metadata(archive_path)
        backups.append(
            {
                "path": archive_path,
                "size": archive_path.stat().st_size,
                "modified_at": datetime.fromtimestamp(archive_path.stat().st_mtime).isoformat(timespec="seconds"),
                "metadata": metadata,
            }
        )
    return backups


def _should_create_backup(account: dict, profile_path: Path, force: bool) -> tuple[bool, str, dict]:
    current_state = _state_snapshot(profile_path)
    if force:
        return True, "forced", current_state

    backups = list_backups(account)
    if not backups:
        return True, "missing-backup", current_state

    latest = backups[0]
    latest_metadata = latest.get("metadata", {})
    if latest_metadata.get("source_state") != current_state:
        return True, "profile-state-changed", current_state

    latest_path = latest.get("path")
    if isinstance(latest_path, Path):
        age_seconds = max(0, int(datetime.now().timestamp() - latest_path.stat().st_mtime))
        if age_seconds >= MAX_BACKUP_AGE_SECONDS:
            return True, "backup-stale", current_state

    return False, "up-to-date", current_state


def backup_account_profile(account: dict, force: bool = False) -> dict:
    profile_path = Path(str(account.get("firefox_profile", "")).strip())
    nickname = str(account.get("nickname", account.get("id", "unknown"))).strip() or "unknown"
    if not profile_path.exists() or not profile_path.is_dir():
        return {
            "account": nickname,
            "created": False,
            "reason": "profile-not-found",
            "path": None,
        }

    should_create, reason, source_state = _should_create_backup(account, profile_path, force)
    if not should_create:
        existing = list_backups(account)
        return {
            "account": nickname,
            "created": False,
            "reason": reason,
            "path": existing[0]["path"] if existing else None,
        }

    backup_dir = _account_backup_dir(account)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = backup_dir / f"{nickname}_{stamp}.tar.gz"

    metadata = {
        "account": nickname,
        "configured_handle": str(account.get("x_username", "")).lstrip("@"),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "profile_path": str(profile_path),
        "auth_cookie_count": count_auth_cookies(profile_path),
        "source_state": source_state,
    }

    with tarfile.open(archive_path, "w:gz") as archive:
        for file_path, rel_path in _iter_profile_files(profile_path):
            archive.add(file_path, arcname=str(rel_path))

        payload = json.dumps(metadata, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name=METADATA_NAME)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    backups = list(_account_backup_dir(account).glob("*.tar.gz"))
    for old_path in sorted(backups, reverse=True)[RETENTION_COUNT:]:
        old_path.unlink(missing_ok=True)

    return {
        "account": nickname,
        "created": True,
        "reason": reason,
        "path": archive_path,
        "auth_cookie_count": metadata["auth_cookie_count"],
    }


def backup_profiles(identifier: str, force: bool = False) -> list[dict]:
    return [backup_account_profile(account, force=force) for account in resolve_accounts(identifier)]


def _safe_extract_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target_dir: Path) -> None:
    member_name = Path(member.name)
    if member_name.name == METADATA_NAME:
        return
    destination = (target_dir / member_name).resolve()
    if target_dir.resolve() not in destination.parents and destination != target_dir.resolve():
        raise ValueError(f"Unsafe archive member: {member.name}")
    archive.extract(member, path=target_dir)


def restore_account_profile(account: dict, archive_name: str = "latest", allow_missing: bool = False) -> dict:
    nickname = str(account.get("nickname", account.get("id", "unknown"))).strip() or "unknown"
    profile_path = Path(str(account.get("firefox_profile", "")).strip())
    backup_dir = _account_backup_dir(account)
    backups = list_backups(account)

    if not backups:
        return {
            "account": nickname,
            "restored": False,
            "reason": "backup-not-found" if not allow_missing else "backup-missing-allowed",
            "path": None,
        }

    selected = backups[0]
    if archive_name not in {"", "latest"}:
        requested = backup_dir / archive_name
        matching = [item for item in backups if item["path"].name == requested.name]
        if not matching:
            return {
                "account": nickname,
                "restored": False,
                "reason": "backup-not-found",
                "path": requested,
            }
        selected = matching[0]

    if is_firefox_profile_running(profile_path):
        return {
            "account": nickname,
            "restored": False,
            "reason": "profile-open",
            "path": selected["path"],
        }

    archive_path = selected["path"]
    restore_root = profile_path.parent
    restore_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safety_path = restore_root / f"{profile_path.name}.pre_restore_{stamp}"
    temp_dir = Path(tempfile.mkdtemp(prefix=f"mpv2_restore_{nickname}_"))
    extracted_dir = temp_dir / "profile"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                _safe_extract_member(archive, member, extracted_dir)

        if profile_path.exists():
            shutil.move(str(profile_path), str(safety_path))
        shutil.copytree(extracted_dir, profile_path, dirs_exist_ok=True)

        return {
            "account": nickname,
            "restored": True,
            "reason": "restored",
            "path": archive_path,
            "safety_path": safety_path,
            "auth_cookie_count": count_auth_cookies(profile_path),
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
