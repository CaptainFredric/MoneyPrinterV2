# RUN THIS N AMOUNT OF TIMES
import sys

from status import *
from cache import get_accounts
from config import get_verbose
from classes.Tts import TTS
from classes.Twitter import Twitter
from classes.YouTube import YouTube
from llm_provider import select_model

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

    if model:
        select_model(model)
    else:
        error("No Ollama model specified. Pass model name as third argument.")
        sys.exit(1)

    verbose = get_verbose()

    if purpose == "twitter":
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
        twitter = Twitter(
            matched_acc["id"],
            matched_acc["nickname"],
            matched_acc["firefox_profile"],
            matched_acc["topic"]
        )
        try:
            post_status = twitter.post()
            print(f"MPV2_POST_STATUS:{post_status}")
        except Exception as exc:
            error(f"Twitter post failed: {exc}")
            sys.exit(1)
        if verbose:
            success("Done posting.")
    elif purpose == "youtube":
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
