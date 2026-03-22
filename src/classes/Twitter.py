import re
import sys
import time
import os
import json
import shutil
import tempfile
import platform
from difflib import SequenceMatcher

from cache import *
from config import *
from status import *
from llm_provider import generate_text
from typing import List, Optional
from datetime import datetime
from termcolor import colored
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class Twitter:
    """
    Class for the Bot, that grows a Twitter account.
    """

    def __init__(
        self, account_uuid: str, account_nickname: str, fp_profile_path: str, topic: str
    ) -> None:
        """
        Initializes the Twitter Bot.

        Args:
            account_uuid (str): The account UUID
            account_nickname (str): The account nickname
            fp_profile_path (str): The path to the Firefox profile

        Returns:
            None
        """
        self.account_uuid: str = account_uuid
        self.account_nickname: str = account_nickname
        self.fp_profile_path: str = fp_profile_path
        self.topic: str = topic

        # Initialize the Firefox profile
        self.options: Options = Options()

        firefox_app_binary = "/Applications/Firefox.app/Contents/MacOS/firefox"
        if platform.system() == "Darwin" and os.path.exists(firefox_app_binary):
            self.options.binary_location = firefox_app_binary

        # Set headless state of browser
        if get_headless():
            self.options.add_argument("--headless")

        if not os.path.isdir(fp_profile_path):
            raise ValueError(
                f"Firefox profile path does not exist or is not a directory: {fp_profile_path}"
            )

        for lock_file_name in [".parentlock", "parent.lock", "lock"]:
            lock_file_path = os.path.join(fp_profile_path, lock_file_name)
            if os.path.exists(lock_file_path):
                try:
                    os.remove(lock_file_path)
                except OSError:
                    pass

        # Set the profile path
        self.options.add_argument("-profile")
        self.options.add_argument(fp_profile_path)

        # Set the service
        self.service: Service = Service(GeckoDriverManager().install())

        # Initialize the browser
        try:
            self.browser: webdriver.Firefox = webdriver.Firefox(
                service=self.service, options=self.options
            )
        except WebDriverException:
            fallback_profile_path = tempfile.mkdtemp(prefix="mpv2_ff_profile_")
            shutil.copytree(fp_profile_path, fallback_profile_path, dirs_exist_ok=True)

            for lock_file_name in [".parentlock", "parent.lock", "lock"]:
                lock_file_path = os.path.join(fallback_profile_path, lock_file_name)
                if os.path.exists(lock_file_path):
                    try:
                        os.remove(lock_file_path)
                    except OSError:
                        pass

            fallback_options: Options = Options()
            if platform.system() == "Darwin" and os.path.exists(firefox_app_binary):
                fallback_options.binary_location = firefox_app_binary
            if get_headless():
                fallback_options.add_argument("--headless")
            fallback_options.add_argument("-profile")
            fallback_options.add_argument(fallback_profile_path)

            self.options = fallback_options
            self.browser = webdriver.Firefox(service=self.service, options=self.options)

        self.wait: WebDriverWait = WebDriverWait(self.browser, 30)

    def post(self, text: Optional[str] = None) -> None:
        """
        Posts a tweet, then quits the browser.
        Always closes the browser window — even on error.

        Args:
            text (str): The text to post

        Returns:
            None
        """
        try:
            self._do_post(text)
        finally:
            try:
                self.browser.quit()
            except Exception:
                pass

    def _do_post(self, text: Optional[str] = None) -> None:
        """
        Internal post implementation — browser lifecycle managed by post().
        """
        bot: webdriver.Firefox = self.browser
        verbose: bool = get_verbose()

        post_content: str = text if text is not None else self.generate_post()
        post_content = self._clean_tweet(post_content)
        now: datetime = datetime.now()

        # Deduplication guard: skip if recent content is identical or too similar
        existing_posts = self.get_posts()
        if self._is_too_similar_to_recent(post_content, existing_posts):
            warning("Post is too similar to recent content — skipping to avoid spam.")
            return

        # Cooldown guard: enforce minimum gap between posts (default 30 min)
        if existing_posts:
            last_post = existing_posts[-1]
            try:
                last_dt = datetime.strptime(last_post["date"], "%m/%d/%Y, %H:%M:%S")
                elapsed = (now - last_dt).total_seconds()
                min_gap = 1800  # 30 minutes in seconds
                if elapsed < min_gap:
                    remaining = int((min_gap - elapsed) / 60)
                    warning(
                        f"Post cooldown active — last post was {int(elapsed / 60)}m ago. "
                        f"Wait {remaining}m more to avoid spam flags."
                    )
                    return
            except (ValueError, KeyError):
                pass  # Malformed date entry — allow post

        bot.get("https://x.com/compose/post")

        print(colored(" => Posting to Twitter:", "blue"), post_content[:50] + "...")
        body = post_content

        text_box = None
        text_box_selectors = [
            (By.CSS_SELECTOR, "div[data-testid='tweetTextarea_0'][role='textbox']"),
            (By.XPATH, "//div[@data-testid='tweetTextarea_0']//div[@role='textbox']"),
            (By.XPATH, "//div[@role='textbox']"),
        ]

        for selector in text_box_selectors:
            try:
                text_box = self.wait.until(EC.element_to_be_clickable(selector))
                text_box.click()
                text_box.send_keys(body)
                break
            except Exception:
                continue

        if text_box is None:
            raise RuntimeError(
                "Could not find tweet text box. Ensure you are logged into X in this Firefox profile."
            )

        post_button = None
        post_button_selectors = [
            (By.XPATH, "//button[@data-testid='tweetButtonInline']"),
            (By.XPATH, "//button[@data-testid='tweetButton']"),
            (By.XPATH, "//span[text()='Post']/ancestor::button"),
        ]

        for selector in post_button_selectors:
            try:
                post_button = self.wait.until(EC.element_to_be_clickable(selector))
                post_button.click()
                break
            except Exception:
                continue

        if post_button is None:
            raise RuntimeError("Could not find the Post button on X compose screen.")

        if verbose:
            print(colored(" => Pressed [ENTER] Button on Twitter..", "blue"))

        # Wait for compose dialog to close — confirms X accepted the post
        try:
            self.wait.until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, "div[data-testid='tweetTextarea_0']")
                )
            )
        except Exception:
            time.sleep(2)  # Non-fatal fallback

        # Add the post to the cache
        self.add_post({"content": body, "date": now.strftime("%m/%d/%Y, %H:%M:%S")})

        success("Posted to Twitter successfully!")

    def get_posts(self) -> List[dict]:
        """
        Gets the posts from the cache.

        Returns:
            posts (List[dict]): The posts
        """
        if not os.path.exists(get_twitter_cache_path()):
            # Create the cache file
            with open(get_twitter_cache_path(), "w") as file:
                json.dump({"accounts": []}, file, indent=4)

        with open(get_twitter_cache_path(), "r") as file:
            parsed = json.load(file)

            # Find our account
            accounts = parsed["accounts"]
            for account in accounts:
                if account["id"] == self.account_uuid:
                    posts = account["posts"]

                    if posts is None:
                        return []

                    # Return the posts
                    return posts

        return []

    def add_post(self, post: dict) -> None:
        """
        Adds a post to the cache using an atomic write to prevent corruption.

        Args:
            post (dict): The post to add

        Returns:
            None
        """
        cache_path = get_twitter_cache_path()

        try:
            with open(cache_path, "r") as file:
                previous_json = json.load(file)
        except (json.JSONDecodeError, OSError):
            previous_json = {"accounts": []}

        accounts = previous_json.get("accounts", [])
        account_found = False
        for account in accounts:
            if account["id"] == self.account_uuid:
                account.setdefault("posts", []).append(post)
                account_found = True
                break

        if not account_found:
            # Safety: shouldn't happen but don't silently swallow
            warning(f"Account {self.account_uuid} not found in cache while saving post.")

        # Atomic write — prevents half-written cache files on crash/interrupt
        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(previous_json, f, indent=4)
        os.replace(tmp_path, cache_path)

    def _clean_tweet(self, text: str) -> str:
        """
        Strips LLM preamble phrases and enforces the 280-char Twitter limit.

        Args:
            text (str): Raw LLM output

        Returns:
            cleaned (str): Tweet-ready string
        """
        # Remove asterisks and stray quotes
        text = re.sub(r"[\*\"]", "", text).strip()

        # Strip common LLM preamble lines (case-insensitive)
        preamble_patterns = [
            r"^here'?s? (?:is )?(?:a )?(?:possible )?(?:twitter )?post[:\s]*",
            r"^tweet[:\s]+",
            r"^post[:\s]+",
            r"^sure[,!]?\.?\s*",
        ]
        for pattern in preamble_patterns:
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

        # Enforce 280-char limit cleanly at a word boundary
        if len(text) > 280:
            text = text[:277].rsplit(" ", 1)[0] + "..."

        return text

    def _normalize_tweet(self, text: str) -> str:
        """
        Normalizes tweet text for similarity comparison.

        Args:
            text (str): Raw or cleaned tweet text

        Returns:
            normalized (str): Simplified text for comparison
        """
        text = text.lower()
        text = re.sub(r"https?://\S+", " ", text)
        text = re.sub(r"[@#]", "", text)
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _is_too_similar_to_recent(self, text: str, posts: List[dict], limit: int = 10) -> bool:
        """
        Returns True when the candidate tweet is too similar to recent posts.

        Args:
            text (str): Candidate tweet text
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to compare against

        Returns:
            is_too_similar (bool): Whether the tweet should be rejected
        """
        candidate = self._normalize_tweet(text)
        if not candidate:
            return True

        candidate_tokens = set(candidate.split())
        recent_posts = posts[-limit:]

        for prev in recent_posts:
            previous = self._normalize_tweet(prev.get("content", ""))
            if not previous:
                continue

            if previous == candidate:
                return True

            similarity = SequenceMatcher(None, candidate, previous).ratio()
            if similarity >= 0.72:
                return True

            previous_tokens = set(previous.split())
            if candidate_tokens and previous_tokens:
                shared_tokens = len(candidate_tokens & previous_tokens)
                overlap = shared_tokens / len(candidate_tokens | previous_tokens)
                overlap_of_smaller = shared_tokens / min(len(candidate_tokens), len(previous_tokens))
                if overlap >= 0.68 or overlap_of_smaller >= 0.70:
                    return True

        return False

    def _has_strong_hook(self, text: str) -> bool:
        """
        Heuristic to reject flat openings and prefer stronger first lines.

        Args:
            text (str): Cleaned tweet text

        Returns:
            has_hook (bool): Whether the opening is strong enough
        """
        first_line = text.splitlines()[0].strip()
        if not first_line:
            return False

        first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
        hook_text = first_sentence[:100]

        strong_starts = (
            "did you know",
            "want to",
            "stop ",
            "try ",
            "boost ",
            "use ",
            "the fastest",
            "the easiest",
            "most people",
            "your ",
            "why ",
            "what if",
            "here's how",
        )

        if "?" in hook_text:
            return True

        if re.match(r"^(\d+|[A-Z][a-z]+:\s)", first_sentence):
            return True

        lowered = hook_text.lower()
        return lowered.startswith(strong_starts)

    def _build_prompt(self, existing_posts: list[dict]) -> str:
        """
        Builds a context-aware LLM prompt that steers the model away from
        recently used ideas and toward fresh angles.

        Args:
            existing_posts (list[dict]): Cached posts for this account

        Returns:
            prompt (str): Fully formatted LLM prompt
        """
        recent_snippets = [
            p.get("content", "")[:80].strip()
            for p in existing_posts[-8:]
            if p.get("content")
        ]

        avoid_block = ""
        if recent_snippets:
            joined = "\n".join(f"  - {s}" for s in recent_snippets)
            avoid_block = (
                f"\n\nRecently posted (DO NOT repeat these ideas, angles, or examples):\n{joined}\n"
            )

        return (
            f"You are a concise, engaging Twitter writer for the topic: '{self.topic}'.\n"
            f"Language: {get_twitter_language()}.\n"
            "Write exactly ONE tweet — maximum 2 sentences, under 270 characters.\n"
            "Rules:\n"
            "  1. Open with a strong hook: a question, surprising fact, bold claim, or specific actionable tip.\n"
            "  2. Choose a SPECIFIC sub-angle — avoid generic advice.\n"
            "  3. Do NOT repeat any idea, phrasing, tool name, or example from the recent posts listed below.\n"
            "  4. No preamble, no labels, no hashtag spam (max 2 hashtags if used).\n"
            "  5. Return ONLY the raw tweet text."
            f"{avoid_block}"
        )

    def generate_post(self) -> str:
        """
        Generates a post for the Twitter account based on the topic.
        Uses context-aware prompting and retries up to 5 times.

        Returns:
            post (str): The post
        """
        if get_verbose():
            info("Generating a post...")

        existing_posts = self.get_posts()
        rejection_reasons: list[str] = []

        for attempt in range(5):
            try:
                prompt = self._build_prompt(existing_posts)
                completion = generate_text(prompt)
                if not completion:
                    rejection_reasons.append(f"Attempt {attempt + 1}: empty response")
                    time.sleep(1)
                    continue
            except Exception as exc:
                if attempt < 4:
                    wait = 2 ** attempt
                    if get_verbose():
                        warning(f"LLM error (attempt {attempt + 1}): {exc}. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                error(f"LLM failed after 5 attempts: {exc}")
                sys.exit(1)

            cleaned = self._clean_tweet(completion)

            if not self._has_strong_hook(cleaned):
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: weak opening hook"
                )
                continue

            if self._is_too_similar_to_recent(cleaned, existing_posts):
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: too similar to recent posts"
                )
                continue

            if get_verbose():
                info(f"Tweet length: {len(cleaned)} chars")

            return cleaned

        if rejection_reasons and get_verbose():
            for reason in rejection_reasons:
                warning(reason)

        error("Failed to generate a strong, non-duplicate post. Please try again.")
        sys.exit(1)
