# RUN THIS N AMOUNT OF TIMES
import os
import sys
from pathlib import Path

from status import *
from cache import get_accounts
from config import get_verbose


ROOT_DIR = Path(__file__).resolve().parent.parent
POST_LOCK_DIR = ROOT_DIR / ".mp" / "runtime" / "post_locks"


def _pid_is_running(pid_value: str) -> bool:
    try:
        pid = int(str(pid_value).strip())
    except Exception:
        return False

    if pid <= 0:
        return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_post_lock(account_nickname: str) -> Path | None:
    POST_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in (account_nickname or "unknown"))
    lock_path = POST_LOCK_DIR / f"{safe_name}.lock"

    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            return lock_path
        except FileExistsError:
            try:
                pid_value = lock_path.read_text(encoding="utf-8").strip()
            except Exception:
                pid_value = ""

            if _pid_is_running(pid_value):
                return None

            try:
                lock_path.unlink(missing_ok=True)
            except Exception:
                return None

    return None


def _release_post_lock(lock_path: Path | None) -> None:
    if not lock_path:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass

def main():
    """Main function to post content to Twitter or upload videos to YouTube.

    This function determines its operation based on command-line arguments:
    - If the purpose is "twitter", it initializes a Twitter account and posts a message.
    - If the purpose is "youtube", it initializes a YouTube account, generates a video with TTS, and uploads it.

    Command-line arguments:
        sys.argv[1]: A string indicating the purpose, either "twitter" or "youtube".
        sys.argv[2]: A string representing the account UUID.

    The function also handles verbose output based on user settings and reports success or errors as appropriate.

    Args:
        None. The function uses command-line arguments accessed via sys.argv.

    Returns:
        None. The function performs operations based on the purpose and account UUID and does not return any value."""
    purpose = str(sys.argv[1])
    account_id = str(sys.argv[2])
    model = str(sys.argv[3]) if len(sys.argv) > 3 else None

    from llm_provider import select_model

    if model:
        select_model(model)
    else:
        error("No Ollama model specified. Pass model name as third argument.")
        sys.exit(1)

    verbose = get_verbose()

    if purpose == "twitter":
        from classes.Twitter import Twitter

        accounts = get_accounts("twitter")

        if not account_id:
            error("Account UUID cannot be empty.")
            sys.exit(1)

        matched_acc = next((a for a in accounts if a["id"] == account_id), None)
        if matched_acc is None:
            error(f"Twitter account '{account_id}' not found in cache. Has it been removed?")
            sys.exit(1)

        if verbose:
            info("Initializing Twitter...")
        lock_path = _acquire_post_lock(matched_acc["nickname"])
        if lock_path is None:
            print("MPV2_POST_STATUS:skipped:account-busy")
            warning(f"Twitter account '{matched_acc['nickname']}' is already being processed by another run.")
            sys.exit(0)
        twitter = Twitter(
            matched_acc["id"],
            matched_acc["nickname"],
            matched_acc["firefox_profile"],
            matched_acc["topic"],
            matched_acc.get("browser_binary", ""),
        )
        try:
            post_status = twitter.post()
            print(f"MPV2_POST_STATUS:{post_status}")
            if post_status.startswith("failed:"):
                error(f"Twitter post verification failed: {post_status}")
                sys.exit(1)
        except Exception as exc:
            error(f"Twitter post failed: {exc}")
            sys.exit(1)
        finally:
            _release_post_lock(lock_path)
        if verbose:
            success("Done posting.")
    elif purpose == "youtube":
        from classes.Tts import TTS
        from classes.YouTube import YouTube

        tts = TTS()

        accounts = get_accounts("youtube")

        if not account_id:
            error("Account UUID cannot be empty.")
            sys.exit(1)

        matched_acc = next((a for a in accounts if a["id"] == account_id), None)
        if matched_acc is None:
            error(f"YouTube account '{account_id}' not found in cache. Has it been removed?")
            sys.exit(1)

        if verbose:
            info("Initializing YouTube...")
        youtube = YouTube(
            matched_acc["id"],
            matched_acc["nickname"],
            matched_acc["firefox_profile"],
            matched_acc["niche"],
            matched_acc["language"]
        )
        try:
            youtube.generate_video(tts)
            youtube.upload_video()
        except Exception as exc:
            error(f"YouTube upload failed: {exc}")
            try:
                youtube.browser.quit()
            except Exception:
                pass
            sys.exit(1)
        if verbose:
            success("Uploaded Short.")
    else:
        error("Invalid Purpose, exiting...")
        sys.exit(1)

if __name__ == "__main__":
    main()
