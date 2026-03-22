import re
import sys
import time
import os
import json
import shutil
import tempfile
import platform

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

        # Deduplication guard: skip if identical content was posted recently
        existing_posts = self.get_posts()
        for prev in existing_posts:
            if prev.get("content", "").strip() == post_content.strip():
                warning("Duplicate post detected — skipping to avoid spam.")
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

    def generate_post(self) -> str:
        """
        Generates a post for the Twitter account based on the topic.
        Retries up to 3 times on LLM failure.

        Returns:
            post (str): The post
        """
        if get_verbose():
            info("Generating a post...")

        completion: str | None = None
        for attempt in range(3):
            try:
                completion = generate_text(
                    f"Generate a Twitter post about: {self.topic} in {get_twitter_language()}. "
                    "The Limit is 2 sentences. Choose a specific sub-topic of the provided topic. "
                    "Return ONLY the tweet text, no preamble or explanation."
                )
                if completion:
                    break
            except Exception as exc:
                if attempt < 2:
                    time.sleep(2 ** attempt)  # exponential back-off: 1s, 2s
                    continue
                error(f"LLM failed after 3 attempts: {exc}")
                sys.exit(1)

        if not completion:
            error("Failed to generate a post. Please try again.")
            sys.exit(1)

        cleaned = self._clean_tweet(completion)

        if get_verbose():
            info(f"Tweet length: {len(cleaned)} chars")

        return cleaned
