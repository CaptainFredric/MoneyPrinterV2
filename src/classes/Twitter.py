import re
import sys
import time
import random
import os
import base64
import json
import shutil
import tempfile
import platform
from difflib import SequenceMatcher
from uuid import uuid4
import requests

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

    def post(self, text: Optional[str] = None) -> str:
        """
        Posts a tweet, then quits the browser.
        Always closes the browser window — even on error.

        Args:
            text (str): The text to post

        Returns:
            status (str): 'posted' or 'skipped:<reason>'
        """
        status = "skipped:unknown"
        try:
            status = self._do_post(text)
        finally:
            try:
                self.browser.quit()
            except Exception:
                pass
        return status

    def _do_post(self, text: Optional[str] = None) -> str:
        """
        Internal post implementation — browser lifecycle managed by post().
        """
        bot: webdriver.Firefox = self.browser
        verbose: bool = get_verbose()

        existing_posts = self.get_posts()
        post_mode = "text"
        media_path: Optional[str] = None

        if text is not None:
            post_content = text
        else:
            post_mode = self._select_post_mode(existing_posts)
            if verbose:
                info(f"Selected post mode: {post_mode}")

            if post_mode == "media":
                media_prompt = self._build_media_prompt(existing_posts)
                media_path = self._generate_media_image(media_prompt)
                if media_path:
                    caption = self._generate_media_caption(existing_posts)
                    if caption:
                        post_content = caption
                    else:
                        post_content = self.generate_post(force_link_mode=False)
                else:
                    warning("Media generation unavailable — falling back to text/link mode.")
                    post_mode = "text"
                    post_content = self.generate_post(force_link_mode=None)
            elif post_mode == "link":
                post_content = self.generate_post(force_link_mode=True)
            else:
                post_content = self.generate_post(force_link_mode=False)

        post_content = self._clean_tweet(post_content)
        now: datetime = datetime.now()

        # Deduplication guard: skip if recent content is identical or too similar
        if self._is_too_similar_to_recent(post_content, existing_posts):
            warning("Post is too similar to recent content — skipping to avoid spam.")
            return "skipped:similarity"

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
                    return "skipped:cooldown"
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

        if media_path:
            file_input = None
            file_input_selectors = [
                (By.CSS_SELECTOR, "input[data-testid='fileInput']"),
                (By.CSS_SELECTOR, "input[type='file'][accept*='image']"),
                (By.CSS_SELECTOR, "input[type='file']"),
            ]
            for selector in file_input_selectors:
                try:
                    file_input = self.wait.until(EC.presence_of_element_located(selector))
                    file_input.send_keys(media_path)
                    # Give X a short moment to bind/upload attachment client-side.
                    time.sleep(2)
                    break
                except Exception:
                    continue

            if file_input is None:
                warning("Could not attach media file — posting as text instead.")
                post_mode = "text"

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
        post_urls = self._extract_urls(body)
        resolved_format = "media" if media_path and post_mode == "media" else ("link" if post_urls else "text")
        self.add_post(
            {
                "content": body,
                "date": now.strftime("%m/%d/%Y, %H:%M:%S"),
                "category": self._infer_category_from_text(body),
                "format": resolved_format,
            }
        )

        success("Posted to Twitter successfully!")
        return "posted"

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

    def _extract_urls(self, text: str) -> list[str]:
        """
        Extracts URLs from tweet text.

        Args:
            text (str): Tweet text

        Returns:
            urls (list[str]): URLs in order of appearance
        """
        return re.findall(r"https?://[^\s)]+", text or "")

    def _get_account_settings(self) -> dict:
        """
        Loads account settings from twitter cache for this account UUID.

        Returns:
            settings (dict): Account settings dict or empty dict
        """
        try:
            with open(get_twitter_cache_path(), "r") as file:
                parsed = json.load(file)
            for account in parsed.get("accounts", []):
                if account.get("id") == self.account_uuid:
                    return account
        except Exception:
            pass
        return {}

    def _trusted_link_pool(self) -> list[str]:
        """
        Returns deduplicated trusted links for this account.

        Supports cache keys: trusted_links, link_pool, source_links

        Returns:
            links (list[str]): Valid http/https URLs
        """
        account = self._get_account_settings()
        links = []
        for key in ("trusted_links", "link_pool", "source_links"):
            value = account.get(key)
            if isinstance(value, list):
                links.extend(value)

        seen = set()
        cleaned: list[str] = []
        for link in links:
            if not isinstance(link, str):
                continue
            link = link.strip()
            if not re.match(r"^https?://", link):
                continue
            if link in seen:
                continue
            seen.add(link)
            cleaned.append(link)

        return cleaned

    def _recent_urls(self, posts: List[dict], limit: int = 12) -> set[str]:
        """
        Collects URLs used in recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            urls (set[str]): Unique recent URLs
        """
        urls: set[str] = set()
        for prev in posts[-limit:]:
            for url in self._extract_urls(prev.get("content", "")):
                urls.add(url)
        return urls

    def _target_link_ratio(self) -> float:
        """
        Returns desired fraction of link posts.

        Account-level override: link_post_ratio in .mp/twitter.json

        Returns:
            ratio (float): Clamped to [0.0, 0.8]
        """
        account = self._get_account_settings()
        configured = account.get("link_post_ratio")
        if isinstance(configured, (int, float)):
            return max(0.0, min(0.8, float(configured)))

        topic = (self.topic or "").lower()
        if any(key in topic for key in ("productivity", "tools", "workflow")):
            return 0.30
        if any(key in topic for key in ("fact", "trivia", "weird", "wierd")):
            return 0.15
        return 0.20

    def _target_media_ratio(self) -> float:
        """
        Returns desired fraction of media posts.

        Account-level override: media_post_ratio in .mp/twitter.json

        Returns:
            ratio (float): Clamped to [0.0, 0.7]
        """
        account = self._get_account_settings()
        configured = account.get("media_post_ratio")
        if isinstance(configured, (int, float)):
            return max(0.0, min(0.7, float(configured)))

        topic = (self.topic or "").lower()
        if any(key in topic for key in ("fact", "trivia", "weird", "wierd")):
            return 0.20
        if any(key in topic for key in ("productivity", "tools", "workflow")):
            return 0.12
        return 0.10

    def _recent_formats(self, posts: List[dict], limit: int = 12) -> list[str]:
        """
        Returns recent post formats, inferring legacy entries when needed.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            formats (list[str]): Recent formats (text/link/media)
        """
        formats: list[str] = []
        for prev in posts[-limit:]:
            fmt = str(prev.get("format", "")).strip().lower()
            if fmt in ("text", "link", "media"):
                formats.append(fmt)
                continue

            content = prev.get("content", "")
            if self._extract_urls(content):
                formats.append("link")
            else:
                formats.append("text")
        return formats

    def _has_image_generation_support(self) -> bool:
        """
        Checks whether image generation is configured.

        Returns:
            available (bool): True when an API key is configured
        """
        return bool(get_nanobanana2_api_key())

    def _should_try_media_post(self, posts: List[dict]) -> bool:
        """
        Decides whether this run should attempt media mode.

        Returns:
            use_media_mode (bool): Whether to generate/upload an image
        """
        if not self._has_image_generation_support():
            return False

        recent_formats = self._recent_formats(posts, limit=12)
        if not recent_formats:
            return random.random() < self._target_media_ratio()

        media_count = sum(1 for fmt in recent_formats if fmt == "media")
        current_ratio = media_count / len(recent_formats)
        target_ratio = self._target_media_ratio()

        # Avoid back-to-back media posts by default.
        if recent_formats and recent_formats[-1] == "media":
            return False

        if current_ratio < target_ratio:
            return random.random() < 0.70
        return random.random() < 0.08

    def _select_post_mode(self, posts: List[dict]) -> str:
        """
        Selects post mode for this run: media, link, or text.

        Returns:
            mode (str): One of media|link|text
        """
        if self._should_try_media_post(posts):
            return "media"
        if self._should_try_link_post(posts):
            return "link"
        return "text"

    def _build_media_prompt(self, existing_posts: List[dict]) -> str:
        """
        Builds a concise image prompt for social media visual generation.

        Args:
            existing_posts (List[dict]): Existing posts for anti-repeat context

        Returns:
            prompt (str): Image model prompt
        """
        account = self._get_account_settings()
        style_hint = str(account.get("image_style_prompt", "")).strip()
        branding_hint = str(account.get("banner_idea") or account.get("avatar_idea") or "").strip()
        recent_topics = "\n".join(
            f"- {p.get('content', '')[:70].strip()}"
            for p in existing_posts[-5:]
            if p.get("content")
        )

        return (
            f"Create a striking social image concept for a Twitter post about: {self.topic}.\n"
            "Output one concise image description only (no bullets, no labels).\n"
            "Keep it realistic, high-contrast, modern, and eye-catching.\n"
            "Aspect ratio portrait-friendly (4:5). Avoid text overlays and logos.\n"
            f"Branding cues: {branding_hint or 'clean modern visual style'}.\n"
            f"Style preference: {style_hint or 'bold lighting, cinematic details, vivid but believable colors'}.\n"
            "Avoid repeating visual ideas implied by recent post topics:\n"
            f"{recent_topics or '- none'}"
        )

    def _generate_media_image(self, prompt: str) -> Optional[str]:
        """
        Generates one media image via Nano Banana 2 and stores it in .mp.

        Args:
            prompt (str): Image generation prompt

        Returns:
            image_path (Optional[str]): Absolute path to PNG image or None
        """
        api_key = get_nanobanana2_api_key()
        if not api_key:
            return None

        base_url = get_nanobanana2_api_base_url().rstrip("/")
        model = get_nanobanana2_model()
        endpoint = f"{base_url}/models/{model}:generateContent"

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "4:5"},
            },
        }

        try:
            response = requests.post(
                endpoint,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            body = response.json()

            for candidate in body.get("candidates", []):
                content = candidate.get("content", {})
                for part in content.get("parts", []):
                    inline_data = part.get("inlineData") or part.get("inline_data")
                    if not inline_data:
                        continue
                    data = inline_data.get("data")
                    mime_type = inline_data.get("mimeType") or inline_data.get("mime_type", "")
                    if data and str(mime_type).startswith("image/"):
                        image_bytes = base64.b64decode(data)
                        path = os.path.join(ROOT_DIR, ".mp", f"{uuid4()}.png")
                        with open(path, "wb") as image_file:
                            image_file.write(image_bytes)
                        return path
        except Exception as exc:
            if get_verbose():
                warning(f"Media image generation failed: {exc}")

        return None

    def _should_try_link_post(self, posts: List[dict]) -> bool:
        """
        Decides whether this generation should attempt a link-style post.

        Returns:
            use_link_mode (bool): Whether to request one trusted URL
        """
        trusted_links = self._trusted_link_pool()
        if not trusted_links:
            return False

        recent = posts[-12:]
        if not recent:
            return random.random() < self._target_link_ratio()

        recent_link_count = 0
        for prev in recent:
            if self._extract_urls(prev.get("content", "")):
                recent_link_count += 1

        current_ratio = recent_link_count / len(recent)
        target_ratio = self._target_link_ratio()

        # If the latest post already contains a URL, bias against back-to-back links.
        latest_has_url = bool(self._extract_urls(recent[-1].get("content", "")))
        if latest_has_url:
            return False

        if current_ratio < target_ratio:
            return random.random() < 0.75
        return random.random() < 0.10

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

    def _opening_signature(self, text: str) -> str:
        """
        Returns a compact signature of the tweet opening for repetition checks.

        Args:
            text (str): Tweet text

        Returns:
            signature (str): First few normalized words of the opening
        """
        first_line = text.splitlines()[0].strip() if text else ""
        first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
        normalized = self._normalize_tweet(first_sentence)
        if not normalized:
            return ""
        return " ".join(normalized.split()[:4])

    def _recent_opening_signatures(self, posts: List[dict], limit: int = 8) -> list[str]:
        """
        Collects unique opening signatures from recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            signatures (list[str]): Unique opening signatures
        """
        signatures: list[str] = []
        for prev in posts[-limit:]:
            signature = self._opening_signature(prev.get("content", ""))
            if signature and signature not in signatures:
                signatures.append(signature)
        return signatures

    def _extract_hashtags(self, text: str) -> set[str]:
        """
        Extracts normalized hashtags from a tweet.

        Args:
            text (str): Tweet text

        Returns:
            hashtags (set[str]): Lowercased hashtags without '#'
        """
        return {tag.lower() for tag in re.findall(r"#([A-Za-z0-9_]+)", text or "")}

    def _recent_hashtags(self, posts: List[dict], limit: int = 12) -> set[str]:
        """
        Collects hashtags from recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            hashtags (set[str]): Distinct recent hashtags
        """
        hashtags: set[str] = set()
        for prev in posts[-limit:]:
            hashtags.update(self._extract_hashtags(prev.get("content", "")))
        return hashtags

    def _cta_signature(self, text: str) -> str:
        """
        Produces a compact signature for the ending CTA sentence.

        Args:
            text (str): Tweet text

        Returns:
            signature (str): First words of last sentence, normalized
        """
        if not text:
            return ""
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        last_sentence = sentences[-1].strip() if sentences else ""
        lowered = last_sentence.lower()

        cta_markers = (
            "try",
            "share",
            "follow",
            "save",
            "comment",
            "reply",
            "tell",
            "bookmark",
            "retweet",
            "use",
        )
        if not any(marker in lowered for marker in cta_markers):
            return ""

        normalized = self._normalize_tweet(last_sentence)
        return " ".join(normalized.split()[:5])

    def _recent_cta_signatures(self, posts: List[dict], limit: int = 10) -> set[str]:
        """
        Collects CTA ending signatures from recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            signatures (set[str]): Distinct CTA signatures
        """
        signatures: set[str] = set()
        for prev in posts[-limit:]:
            signature = self._cta_signature(prev.get("content", ""))
            if signature:
                signatures.add(signature)
        return signatures

    def _topic_category_pool(self) -> list[str]:
        """
        Returns suggested categories for the current account topic.

        Returns:
            categories (list[str]): Ordered category labels
        """
        topic = (self.topic or "").lower()

        if any(key in topic for key in ("fact", "trivia", "weird", "wierd", "odd")):
            return [
                "science",
                "history",
                "space",
                "animals",
                "human-body",
                "language",
                "technology",
                "psychology",
                "food",
                "geography",
            ]

        if any(key in topic for key in ("productivity", "focus", "workflow", "tools")):
            return [
                "focus",
                "time-management",
                "planning",
                "automation",
                "task-management",
                "habits",
                "energy",
                "collaboration",
            ]

        return []

    def _infer_category_from_text(self, text: str) -> str:
        """
        Infers a coarse content category from tweet text.

        Args:
            text (str): Tweet content

        Returns:
            category (str): Lowercase category label
        """
        normalized = self._normalize_tweet(text)
        if not normalized:
            return "general"

        tokens = set(normalized.split())
        topic = (self.topic or "").lower()

        if any(key in topic for key in ("fact", "trivia", "weird", "wierd", "odd")):
            category_keywords = {
                "science": {"science", "physics", "chemistry", "molecule", "atom", "lab"},
                "history": {"history", "ancient", "empire", "war", "century", "roman"},
                "space": {"space", "planet", "galaxy", "moon", "sun", "nasa", "orbit"},
                "animals": {"animal", "animals", "bird", "cat", "dog", "whale", "shark"},
                "human-body": {"brain", "heart", "body", "human", "muscle", "sleep", "eye", "dream", "dreams", "neuro", "neural"},
                "language": {"word", "language", "letters", "english", "latin", "grammar"},
                "technology": {"tech", "computer", "internet", "software", "ai", "robot"},
                "psychology": {"mind", "memory", "habit", "emotion", "behavior", "bias", "dream", "dreams", "perception", "cognition"},
                "food": {"food", "eat", "coffee", "chocolate", "salt", "sugar", "fruit"},
                "geography": {"country", "city", "ocean", "river", "mountain", "desert"},
            }
        elif any(key in topic for key in ("productivity", "focus", "workflow", "tools")):
            category_keywords = {
                "focus": {"focus", "distraction", "deep", "attention", "concentrate"},
                "time-management": {"time", "calendar", "schedule", "pomodoro", "deadline"},
                "planning": {"plan", "weekly", "priority", "roadmap", "goal"},
                "automation": {"automate", "automation", "script", "workflow", "system"},
                "task-management": {"task", "todo", "kanban", "trello", "asana", "notion"},
                "habits": {"habit", "routine", "consistency", "daily", "streak"},
                "energy": {"energy", "sleep", "break", "rest", "burnout"},
                "collaboration": {"team", "collaboration", "meeting", "async", "delegate"},
            }
        else:
            category_keywords = {}

        best_category = "general"
        best_score = 0
        for category, words in category_keywords.items():
            score = len(tokens & words)
            if score > best_score:
                best_score = score
                best_category = category

        return best_category

    def _recent_categories(self, posts: List[dict], limit: int = 10) -> list[str]:
        """
        Returns ordered, unique categories from recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            categories (list[str]): Recent categories, newest order preserved
        """
        categories: list[str] = []
        for prev in posts[-limit:]:
            category = str(prev.get("category", "")).strip().lower()
            if not category:
                category = self._infer_category_from_text(prev.get("content", ""))
            if category and category not in categories:
                categories.append(category)
        return categories

    def _build_prompt(self, existing_posts: list[dict], use_link_mode: bool = False) -> str:
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

        opening_signatures = self._recent_opening_signatures(existing_posts)
        opening_block = ""
        if opening_signatures:
            joined_openings = "\n".join(f"  - {sig}" for sig in opening_signatures[:6])
            opening_block = (
                "\nRecent opening patterns to avoid reusing:\n"
                f"{joined_openings}\n"
            )

        recent_hashtags = sorted(self._recent_hashtags(existing_posts))
        hashtag_block = ""
        if recent_hashtags:
            joined_hashtags = "\n".join(f"  - #{tag}" for tag in recent_hashtags[:8])
            hashtag_block = (
                "\nRecent hashtags to avoid reusing too often:\n"
                f"{joined_hashtags}\n"
            )

        recent_cta_signatures = sorted(self._recent_cta_signatures(existing_posts))
        cta_block = ""
        if recent_cta_signatures:
            joined_ctas = "\n".join(f"  - {sig}" for sig in recent_cta_signatures[:6])
            cta_block = (
                "\nRecent CTA endings to vary away from:\n"
                f"{joined_ctas}\n"
            )

        category_pool = self._topic_category_pool()
        recent_categories = self._recent_categories(existing_posts, limit=8)
        category_block = ""
        if category_pool:
            category_list = ", ".join(category_pool)
            recent_list = ", ".join(recent_categories[:4]) if recent_categories else "none"
            category_block = (
                "\nCategory rotation:\n"
                f"  - Allowed categories: {category_list}\n"
                f"  - Recently used categories: {recent_list}\n"
                "  - Pick a different category than recent posts when possible.\n"
            )

        link_block = ""
        trusted_links = self._trusted_link_pool()
        if use_link_mode and trusted_links:
            recent_urls = self._recent_urls(existing_posts, limit=12)
            fresh_links = [link for link in trusted_links if link not in recent_urls]
            candidate_links = (fresh_links or trusted_links)[:6]
            joined_links = "\n".join(f"  - {link}" for link in candidate_links)
            link_block = (
                "\nTrusted link mode is ON:\n"
                "  - Include exactly one URL from this approved list.\n"
                "  - Never invent or alter URLs.\n"
                "  - Keep the tweet readable even with the link included.\n"
                f"{joined_links}\n"
            )
        elif trusted_links:
            link_block = (
                "\nTrusted links are configured, but this post is text-only mode.\n"
                "Do not include any URL in this tweet.\n"
            )

        return (
            f"You are a concise, engaging Twitter writer for the topic: '{self.topic}'.\n"
            f"Language: {get_twitter_language()}.\n"
            "Write exactly ONE tweet — maximum 2 sentences, under 270 characters.\n"
            "Rules:\n"
            "  1. Open with a strong hook: a question, surprising fact, bold claim, or specific actionable tip.\n"
            "  2. Choose a SPECIFIC sub-angle — avoid generic advice.\n"
            "  3. Do NOT repeat any idea, phrasing, tool name, or example from the recent posts listed below.\n"
            "  4. Use a different opening pattern than recent posts (vary lead-in wording and structure).\n"
            "  5. If you use hashtags, prefer fresh ones not recently used. Max 2 hashtags.\n"
            "  6. Vary CTA endings when present (do not keep ending with the same ask).\n"
            "  7. Rotate sub-categories across posts instead of repeating the same lane.\n"
            "  8. No preamble, no labels, no hashtag spam (max 2 hashtags if used).\n"
            "  9. Return ONLY the raw tweet text."
            f"{avoid_block}{opening_block}{hashtag_block}{cta_block}{category_block}{link_block}"
        )

    def _generate_media_caption(self, existing_posts: List[dict]) -> str:
        """
        Generates a caption designed for an accompanying image post.

        Args:
            existing_posts (List[dict]): Existing posts for anti-repeat context

        Returns:
            caption (str): Tweet caption text
        """
        recent_snippets = "\n".join(
            f"- {p.get('content', '')[:80].strip()}"
            for p in existing_posts[-6:]
            if p.get("content")
        )

        prompt = (
            f"Write one X/Twitter caption for topic '{self.topic}' in {get_twitter_language()}.\n"
            "Context: this caption will be paired with an image, so make it punchy and concise.\n"
            "Rules:\n"
            "- Max 2 short sentences, under 220 characters.\n"
            "- Strong hook in sentence 1.\n"
            "- No URL.\n"
            "- Max 2 hashtags.\n"
            "- Return only the caption text.\n"
            "Avoid repeating these recent posts:\n"
            f"{recent_snippets or '- none'}"
        )

        completion = generate_text(prompt)
        if not completion:
            return ""
        return self._clean_tweet(completion)

    def generate_post(self, force_link_mode: Optional[bool] = None) -> str:
        """
        Generates a post for the Twitter account based on the topic.
        Uses context-aware prompting and retries up to 5 times.

        Returns:
            post (str): The post
        """
        if get_verbose():
            info("Generating a post...")

        existing_posts = self.get_posts()
        recent_openings = set(self._recent_opening_signatures(existing_posts))
        recent_hashtags = self._recent_hashtags(existing_posts)
        recent_cta_signatures = self._recent_cta_signatures(existing_posts)
        recent_categories = self._recent_categories(existing_posts, limit=6)
        recent_category_set = set(recent_categories)
        category_pool = self._topic_category_pool()
        trusted_links = self._trusted_link_pool()
        recent_urls = self._recent_urls(existing_posts, limit=12)
        use_link_mode = force_link_mode if force_link_mode is not None else self._should_try_link_post(existing_posts)
        rejection_reasons: list[str] = []

        for attempt in range(5):
            try:
                prompt = self._build_prompt(existing_posts, use_link_mode=use_link_mode)
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

            opening_signature = self._opening_signature(cleaned)
            if opening_signature and opening_signature in recent_openings:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: opening too similar to recent hooks"
                )
                continue

            candidate_hashtags = self._extract_hashtags(cleaned)
            if candidate_hashtags and len(candidate_hashtags & recent_hashtags) >= 2:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: reusing too many recent hashtags"
                )
                continue

            cta_signature = self._cta_signature(cleaned)
            if cta_signature and cta_signature in recent_cta_signatures:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: CTA ending too repetitive"
                )
                continue

            candidate_urls = self._extract_urls(cleaned)
            if candidate_urls:
                if len(candidate_urls) > 1:
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: too many links in one post"
                    )
                    continue

                if trusted_links and candidate_urls[0] not in trusted_links:
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: untrusted or invented link"
                    )
                    continue

                if candidate_urls[0] in recent_urls and len(set(trusted_links) - recent_urls) > 0:
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: link reused too soon"
                    )
                    continue
            elif use_link_mode and trusted_links and attempt < 3:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: link mode expected one trusted URL"
                )
                continue

            if category_pool:
                category = self._infer_category_from_text(cleaned)
                can_rotate = len(set(category_pool) - recent_category_set) > 0
                if attempt < 4 and can_rotate and category in recent_category_set:
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: category '{category}' repeated too soon"
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
