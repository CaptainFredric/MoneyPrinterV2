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
from pathlib import Path
from uuid import uuid4
import requests
from urllib.parse import quote_plus, urlparse

from cache import *
from config import *
from status import *
from firefox_runtime import resolve_firefox_binary, clear_profile_locks
from publish_verification_hardener import PublishVerificationHardener
from typing import List, Optional
from datetime import datetime, timedelta
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
        self,
        account_uuid: str,
        account_nickname: str,
        fp_profile_path: str,
        topic: str,
        browser_binary: str = "",
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
        # Resolve binary with profile awareness: compatibility.ini is read so
        # the correct edition (stable vs Developer) is automatically selected.
        self.browser_binary: str = resolve_firefox_binary(browser_binary, profile_path=fp_profile_path)
        self.using_fallback_profile: bool = False
        self.fallback_profile_path: str = ""
        self.post_attempt_timestamp: Optional[datetime] = None
        self.session_health_check_cache: Optional[dict] = None
        self.last_cooldown_warning_time: Optional[datetime] = None
        self.last_permalink_debug: dict = {}
        self._visibility_issue_cached: Optional[bool] = None


        # Initialize the Firefox profile
        self.options: Options = Options()

        if self.browser_binary:
            self.options.binary_location = self.browser_binary

        # Set headless state of browser
        if get_headless():
            self.options.add_argument("--headless")

        if not os.path.isdir(fp_profile_path):
            raise ValueError(
                f"Firefox profile path does not exist or is not a directory: {fp_profile_path}"
            )

        # Set the profile path
        self.options.add_argument("-profile")
        self.options.add_argument(fp_profile_path)

        # Clear any stale Firefox lock files before launching the browser.
        # A leftover .parentlock from a previous crash causes status-0 failures.
        clear_profile_locks(fp_profile_path)

        # Set the service (prefer local/cached geckodriver for offline resilience)
        self.service: Service = Service(self._resolve_geckodriver_path())

        # Initialize the browser
        try:
            self.browser: webdriver.Firefox = webdriver.Firefox(
                service=self.service, options=self.options
            )
        except WebDriverException:
            self.using_fallback_profile = True
            fallback_profile_path = tempfile.mkdtemp(prefix="mpv2_ff_profile_")
            shutil.copytree(fp_profile_path, fallback_profile_path, dirs_exist_ok=True)
            self.fallback_profile_path = fallback_profile_path

            for lock_file_name in [".parentlock", "parent.lock", "lock"]:
                lock_file_path = os.path.join(fallback_profile_path, lock_file_name)
                if os.path.exists(lock_file_path):
                    try:
                        os.remove(lock_file_path)
                    except OSError:
                        pass

            fallback_options: Options = Options()
            if self.browser_binary:
                fallback_options.binary_location = self.browser_binary
            if get_headless():
                fallback_options.add_argument("--headless")
            fallback_options.add_argument("-profile")
            fallback_options.add_argument(fallback_profile_path)

            self.options = fallback_options
            self.browser = webdriver.Firefox(service=self.service, options=self.options)

        self.wait: WebDriverWait = WebDriverWait(self.browser, 30)

    def _sync_fallback_cookies_to_real_profile(self) -> None:
        """Copy cookies.sqlite (and WAL/SHM) from the fallback temp profile
        back to the real profile so auth tokens are preserved across sessions.

        Called just before browser.quit() whenever using_fallback_profile=True.
        Safe to call when not in fallback mode (no-op).
        """
        if not self.using_fallback_profile or not self.fallback_profile_path:
            return
        src_dir = Path(self.fallback_profile_path)
        dst_dir = Path(self.fp_profile_path)
        if not dst_dir.is_dir():
            return
        for fname in ("cookies.sqlite", "cookies.sqlite-shm", "cookies.sqlite-wal"):
            src = src_dir / fname
            dst = dst_dir / fname
            if src.exists():
                try:
                    shutil.copy2(str(src), str(dst))
                except Exception:
                    pass

    def _generate_text(self, prompt: str) -> str:
        """
        Lazily imports the LLM provider only when text generation is needed.
        This keeps session checks and login utilities resilient when optional
        LLM-related dependencies are temporarily unavailable.

        Args:
            prompt (str): Prompt for the text generator.

        Returns:
            text (str): Generated text or empty string.
        """
        from llm_provider import generate_text

        return generate_text(prompt)

    def _resolve_geckodriver_path(self) -> str:
        """
        Resolves geckodriver path with offline-first behavior.

        Resolution order:
        1) `GECKODRIVER_PATH` env var (if valid)
        2) local webdriver-manager cache under `~/.wdm`
        3) webdriver-manager online install

        Returns:
            path (str): geckodriver executable path
        """
        env_path = os.environ.get("GECKODRIVER_PATH", "").strip()
        if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
            return env_path

        cached_root = os.path.expanduser("~/.wdm/drivers/geckodriver/mac64")
        if os.path.isdir(cached_root):
            try:
                version_dirs = sorted(os.listdir(cached_root), reverse=True)
                for version in version_dirs:
                    candidate = os.path.join(cached_root, version, "geckodriver")
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        return candidate
            except Exception:
                pass

        return GeckoDriverManager().install()

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
            # Preserve auth cookies if we were running on a fallback temp profile.
            self._sync_fallback_cookies_to_real_profile()
            try:
                self.browser.quit()
            except Exception:
                pass
        return status

    def quit(self) -> None:
        """Sync fallback cookies and close the browser.

        Call ``twitter.quit()`` instead of ``twitter.browser.quit()`` so that
        auth cookies are always persisted back to the real profile when a
        fallback temp profile was used.
        """
        self._sync_fallback_cookies_to_real_profile()
        try:
            self.browser.quit()
        except Exception:
            pass

    def _do_post(self, text: Optional[str] = None) -> str:
        """
        Internal post implementation — browser lifecycle managed by post().
        """
        bot: webdriver.Firefox = self.browser
        verbose: bool = get_verbose()

        existing_posts = self.get_posts()
        post_mode = "text"
        media_path: Optional[str] = None

        session_status = self.check_session()
        if not session_status.get("ready", False):
            reason = session_status.get("reason", "session-not-ready")
            warning(f"X session not ready for posting: {reason}")
            return f"failed:{reason}"

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
        self.post_attempt_timestamp = now

        # Deduplication guard: skip if recent content is identical or too similar
        if self._is_too_similar_to_recent(post_content, existing_posts):
            warning("Post is too similar to recent content — skipping to avoid spam.")
            self._log_transaction('post_attempt', 'skipped', {'reason': 'similarity', 'attempt_time': now.isoformat()})
            return "skipped:similarity"

        # Enhanced cooldown guard with transaction log checking
        cooldown_reason = self._verify_cooldown_strict(existing_posts)
        if cooldown_reason:
            warning(f"Cooldown active: {cooldown_reason}")
            self._log_transaction('post_attempt', 'skipped', {'reason': cooldown_reason, 'attempt_time': now.isoformat()})
            return f"skipped:{cooldown_reason}"

        bot.get(self._home_url())
        time.sleep(2)
        if self._is_x_error_page():
            raise RuntimeError("X home timeline is returning an error page in Firefox.")

        composer_launch_selectors = [
            (By.CSS_SELECTOR, "a[data-testid='SideNav_NewTweet_Button']"),
            (By.CSS_SELECTOR, "button[data-testid='SideNav_NewTweet_Button']"),
        ]
        for selector in composer_launch_selectors:
            try:
                launch_button = self.wait.until(EC.element_to_be_clickable(selector))
                launch_button.click()
                time.sleep(2)
                break
            except Exception:
                continue

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
            (By.XPATH, "//button[@data-testid='tweetButton']"),
            (By.XPATH, "//button[@data-testid='tweetButtonInline']"),
            (By.XPATH, "//span[text()='Post']/ancestor::button"),
        ]

        for selector in post_button_selectors:
            try:
                post_button = self.wait.until(EC.element_to_be_clickable(selector))
                # Use JS click — avoids headless intercept issues with overlapping elements
                self.browser.execute_script("arguments[0].click();", post_button)
                break
            except Exception:
                continue

        if post_button is None:
            raise RuntimeError("Could not find the Post button on X compose screen.")

        if verbose:
            print(colored(" => Pressed [ENTER] Button on Twitter..", "blue"))

        # Confirm compose dialog closed. On home timeline X always keeps ONE
        # tweetTextarea_0 for the inline composer — the modal compose dialog adds
        # a SECOND one plus an aria-modal=true element. We confirm success when
        # the aria-modal overlay disappears (count drops to 0).
        compose_confirmed = False
        try:
            self.wait.until(
                EC.invisibility_of_element_located(
                    (By.CSS_SELECTOR, "[aria-modal='true']")
                )
            )
            compose_confirmed = True
        except Exception:
            # Grace period — sometimes the modal is slow to dismiss
            time.sleep(3)
            try:
                modal_elements = self.browser.find_elements(
                    By.CSS_SELECTOR, "[aria-modal='true']"
                )
                compose_confirmed = len(modal_elements) == 0
            except Exception:
                compose_confirmed = False

        if not compose_confirmed:
            warning(
                "X compose dialog did not close after Post click — "
                "X likely rejected or rate-limited this post. NOT saving to cache."
            )
            self._log_transaction('post_attempt', 'failed', {
                'reason': 'compose-not-confirmed',
                'text_snippet': body[:80],
                'attempt_time': now.isoformat()
            })
            return "failed:compose-not-confirmed"

        post_urls = self._extract_urls(body)
        post_category = self._infer_category_from_text(body)
        citation_source = self._extract_citation_source(body)
        angle_signature = self._extract_angle_signature(body, post_category)
        tweet_url = self._resolve_post_permalink(body)
        resolved_format = "media" if media_path and post_mode == "media" else ("link" if post_urls else "text")
        confidence_payload = self._compute_post_confidence(tweet_url=tweet_url)
        publish_likelihood = self._classify_publish_likelihood(tweet_url, confidence_payload)
        publish_evidence = self._build_publish_evidence_snapshot(tweet_url, confidence_payload)

        if not tweet_url:
            warning(
                "X accepted compose, but permalink lookup is delayed/unavailable. "
                "Saving as pending verification for later backfill."
            )
            self.add_post(
                {
                    "content": body,
                    "date": now.strftime("%m/%d/%Y, %H:%M:%S"),
                    "pending_since": now.isoformat(timespec="seconds"),
                    "category": post_category,
                    "format": resolved_format,
                    "citation_source": citation_source,
                    "angle_signature": angle_signature,
                    "tweet_url": "",
                    "post_verified": False,
                    "verification_state": "pending",
                    "verification_attempts": 0,
                    "last_verification_checked_at": "",
                    "publish_likelihood": publish_likelihood,
                    "publish_evidence": publish_evidence,
                    "confidence_score": confidence_payload["score"],
                    "confidence_level": confidence_payload["level"],
                    "confidence_signals": confidence_payload["signals"],
                }
            )
            self._record_angle_signature(angle_signature, post_category)
            phase3_result = self._verify_latest_post_phase3(attempts=3, retry_delay_seconds=12)
            self._log_transaction('post_attempt', 'pending', {
                'reason': 'unverified',
                'text_snippet': body[:80],
                'permalink_debug': self.last_permalink_debug,
                'confidence_score': confidence_payload["score"],
                'confidence_level': confidence_payload["level"],
                'phase3_verified': bool(phase3_result.get('verified', False)),
                'phase3_match_method': phase3_result.get('match_method', ''),
                'attempt_time': now.isoformat()
            })
            if phase3_result.get("error"):
                warning(f"Phase 3 post-completion verify error: {phase3_result['error']}")
            elif phase3_result.get("verified"):
                success(
                    "Phase 3 verified the new post immediately"
                    + (f" via {phase3_result.get('match_method', 'unknown')}" if phase3_result.get("match_method") else "")
                )
            return (
                "posted:pending-verification:"
                f"confidence={confidence_payload['score']}:"
                f"level={confidence_payload['level']}"
            )

        self.add_post(
            {
                "content": body,
                "date": now.strftime("%m/%d/%Y, %H:%M:%S"),
                "category": post_category,
                "format": resolved_format,
                "citation_source": citation_source,
                "angle_signature": angle_signature,
                "tweet_url": tweet_url,
                "post_verified": True,
                "verification_state": "verified",
                "publish_likelihood": publish_likelihood,
                "publish_evidence": publish_evidence,
                "confidence_score": confidence_payload["score"],
                "confidence_level": confidence_payload["level"],
                "confidence_signals": confidence_payload["signals"],
            }
        )

        self._record_angle_signature(angle_signature, post_category)
        phase3_result = self._verify_latest_post_phase3(attempts=3, retry_delay_seconds=12)

        self._log_transaction('post_attempt', 'success', {
            'text_snippet': body[:80],
            'tweet_url': tweet_url,
            'category': post_category,
            'confidence_score': confidence_payload["score"],
            'confidence_level': confidence_payload["level"],
            'phase3_verified': bool(phase3_result.get('verified', False)),
            'phase3_match_method': phase3_result.get('match_method', ''),
            'attempt_time': now.isoformat()
        })

        if phase3_result.get("error"):
            warning(f"Phase 3 post-completion verify error: {phase3_result['error']}")

        success(f"Posted to Twitter successfully! URL: {tweet_url}")
        return f"posted:confidence={confidence_payload['score']}:level={confidence_payload['level']}"

    def _media_state_path(self) -> str:
        """
        Returns path to media generation state file.

        Returns:
            path (str): Absolute JSON path
        """
        return os.path.join(ROOT_DIR, ".mp", "twitter_media_state.json")

    def _load_media_state(self) -> dict:
        """
        Loads media generation state from disk.

        Returns:
            state (dict): Persisted state
        """
        path = self._media_state_path()
        if not os.path.exists(path):
            return {"accounts": {}}

        try:
            with open(path, "r") as file:
                parsed = json.load(file)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {"accounts": {}}

    def _save_media_state(self, state: dict) -> None:
        """
        Saves media generation state atomically.

        Args:
            state (dict): State payload
        """
        path = self._media_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as file:
            json.dump(state, file, indent=4)
        os.replace(tmp_path, path)

    def _is_media_generation_temporarily_disabled(self) -> bool:
        """
        Returns True when media generation is in cooldown for this account.

        Returns:
            disabled (bool): Whether media generation should be skipped
        """
        state = self._load_media_state()
        entry = state.get("accounts", {}).get(self.account_uuid, {})
        until_raw = str(entry.get("disabled_until", "")).strip()
        if not until_raw:
            return False

        try:
            disabled_until = datetime.fromisoformat(until_raw)
        except Exception:
            return False

        return datetime.now() < disabled_until

    def _record_media_generation_failure(self, reason: str, hours: int = 12) -> None:
        """
        Records a temporary media generation cooldown for this account.

        Args:
            reason (str): Human-readable reason
            hours (int): Cooldown duration
        """
        state = self._load_media_state()
        accounts = state.setdefault("accounts", {})
        accounts[self.account_uuid] = {
            "disabled_until": (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds"),
            "reason": reason,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save_media_state(state)

    def _clear_media_generation_failure(self) -> None:
        """
        Clears media generation cooldown for this account.
        """
        state = self._load_media_state()
        accounts = state.get("accounts", {})
        if self.account_uuid in accounts:
            del accounts[self.account_uuid]
            self._save_media_state(state)

    def _canonical_status_url(self, url: str) -> str:
        """
        Canonicalizes status URLs to x.com/<user>/status/<id> form.

        Args:
            url (str): Candidate URL

        Returns:
            canonical (str): Canonical status URL or empty string
        """
        if not isinstance(url, str) or not url.strip():
            return ""

        try:
            parsed = urlparse(url.strip())
        except Exception:
            return ""

        host = (parsed.netloc or "").lower().replace("www.", "")
        if host not in {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"}:
            return ""

        match = re.search(r"/([A-Za-z0-9_]+)/status/(\d+)", parsed.path or "")
        if not match:
            return ""

        handle, status_id = match.group(1), match.group(2)
        return f"https://x.com/{handle}/status/{status_id}"

    def _collect_status_link_candidates(self) -> list[str]:
        """
        Collects possible status links visible on the current page.

        Returns:
            candidates (list[str]): Canonical status links
        """
        candidates: list[str] = []
        seen = set()

        for anchor in self.browser.find_elements(By.XPATH, "//a[contains(@href, '/status/')]"):
            href = anchor.get_attribute("href") or ""
            canonical = self._canonical_status_url(href)
            if canonical and canonical not in seen:
                seen.add(canonical)
                candidates.append(canonical)

        try:
            page_source = self.browser.page_source or ""
            for raw_url in re.findall(r"https?://(?:x|twitter)\.com/[A-Za-z0-9_]+/status/\d+", page_source):
                canonical = self._canonical_status_url(raw_url)
                if canonical and canonical not in seen:
                    seen.add(canonical)
                    candidates.append(canonical)
        except Exception:
            pass

        return candidates

    def _timeline_url_for_handle(self, handle: str) -> str:
        """
        Builds canonical profile timeline URL.

        Args:
            handle (str): Username without @

        Returns:
            url (str): Timeline URL
        """
        return f"https://x.com/{handle}"

    def _home_url(self) -> str:
        """
        Returns the logged-in home timeline URL.

        Returns:
            url (str): Home URL
        """
        return "https://x.com/home"

    def _is_x_error_page(self) -> bool:
        """
        Detects the X fullscreen error page.

        Returns:
            is_error (bool): Whether current page is the X error shell
        """
        try:
            title = (self.browser.title or "").strip().lower()
            if title == "x / error":
                return True

            page_source = self.browser.page_source or ""
            return 'class="icecream"' in page_source or "This page is down" in page_source
        except Exception:
            return False

    def _profile_visibility_issue(self, handle: str) -> bool:
        """
        Detects accounts whose live profile shows no authored posts despite cached history.

        Args:
            handle (str): Username without @

        Returns:
            has_issue (bool): Whether authored posts are unavailable live
        """
        # Return cached result so we don't navigate away from home on every check_session call.
        if self._visibility_issue_cached is not None:
            return self._visibility_issue_cached

        cached_posts = self.get_posts()
        if not handle or not cached_posts:
            self._visibility_issue_cached = False
            return False

        # Skip expensive check for accounts that have never had a verified post —
        # they haven't proven visibility yet so the check adds noise, not signal.
        verified_count = sum(1 for p in cached_posts if p.get("post_verified"))
        if verified_count == 0:
            self._visibility_issue_cached = False
            return False

        try:
            self.browser.get(self._timeline_url_for_handle(handle))
            time.sleep(3)
            if self._is_x_error_page():
                self._visibility_issue_cached = False
                return False

            live_posts = self._collect_timeline_posts_from_current_page(limit=5)
            if live_posts:
                self._visibility_issue_cached = False
                return False

            body_text = ""
            try:
                body_text = self.browser.find_element(By.TAG_NAME, "body").text or ""
            except Exception:
                body_text = ""

            if re.search(r"\b0\s+posts\b", body_text.lower()):
                self._visibility_issue_cached = True
                return True
        except Exception:
            self._visibility_issue_cached = False
            return False

        self._visibility_issue_cached = False
        return False

    def _resolve_account_handle(self) -> str:
        """
        Resolves currently logged-in account handle from profile nav links.

        Returns:
            handle (str): Username without @, or empty string
        """
        # Strategy 1: test-ID nav link (desktop sidebar — may be absent in headless/mobile layout)
        testid_selectors = [
            (By.CSS_SELECTOR, "a[data-testid='AppTabBar_Profile_Link']"),
            (By.XPATH, "//a[contains(@href,'/') and contains(@href,'x.com/') and @data-testid='AppTabBar_Profile_Link']"),
            (By.XPATH, "//a[contains(@href,'/') and contains(@href,'twitter.com/') and @data-testid='AppTabBar_Profile_Link']"),
        ]
        for selector in testid_selectors:
            try:
                elem = self.browser.find_element(*selector)
                href = elem.get_attribute("href") or ""
                match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)$", href)
                if match:
                    return match.group(1)
            except Exception:
                continue

        # Strategy 2: configured handle direct href (works when X removes test-IDs in headless mode)
        configured = (self._configured_account_handle() or "").strip().lstrip("@")
        if configured:
            try:
                sel = f"a[href*='/{configured}']"
                elems = self.browser.find_elements(By.CSS_SELECTOR, sel)
                for elem in elems:
                    href = elem.get_attribute("href") or ""
                    match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)$", href)
                    if match and match.group(1).lower() == configured.lower():
                        return match.group(1)
            except Exception:
                pass

        # Strategy 3: scan ALL links on the page for a unique profile-looking href.
        # Exclude known non-profile paths.
        _SKIP = {"tos", "privacy", "home", "explore", "notifications", "messages",
                 "settings", "search", "i", "support", "about"}
        try:
            all_links = self.browser.find_elements(By.CSS_SELECTOR, "a[href]")
            for elem in all_links:
                href = elem.get_attribute("href") or ""
                match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)$", href)
                if not match:
                    continue
                candidate = match.group(1)
                if candidate.lower() in _SKIP:
                    continue
                if configured and candidate.lower() == configured.lower():
                    return candidate
        except Exception:
            pass

        # Strategy 4: load home and retry all strategies once.
        try:
            self.browser.get(self._home_url())
            time.sleep(3)
            for selector in testid_selectors:
                try:
                    elem = self.browser.find_element(*selector)
                    href = elem.get_attribute("href") or ""
                    match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)$", href)
                    if match:
                        return match.group(1)
                except Exception:
                    continue
            if configured:
                try:
                    elems = self.browser.find_elements(By.CSS_SELECTOR, f"a[href*='/{configured}']")
                    for elem in elems:
                        href = elem.get_attribute("href") or ""
                        match = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]+)$", href)
                        if match and match.group(1).lower() == configured.lower():
                            return match.group(1)
                except Exception:
                    pass
        except Exception:
            pass

        return ""

    def get_live_account_handle(self) -> str:
        """
        Public wrapper to resolve the current logged-in handle.

        Returns:
            handle (str): Username without @, or empty string
        """
        return self._resolve_account_handle()

    def check_session(self) -> dict:
        """
        Checks whether the Firefox profile is ready to post on X.

        Returns:
            status (dict): Readiness details
        """
        compose_url = self._home_url()
        text_box_selectors = [
            (By.CSS_SELECTOR, "div[data-testid='tweetTextarea_0'][role='textbox']"),
            (By.XPATH, "//div[@data-testid='tweetTextarea_0']//div[@role='textbox']"),
            (By.XPATH, "//div[@role='textbox']"),
        ]

        self.browser.get(compose_url)
        time.sleep(2)
        current_url = self.browser.current_url

        if self._is_x_error_page():
            return {
                "ready": False,
                "reason": "x-error-page",
                "current_url": current_url,
                "handle": "",
                "configured_handle": (self._configured_account_handle() or "").strip().lstrip("@"),
            }

        for selector in text_box_selectors:
            try:
                self.browser.find_element(*selector)
                live_handle = (self.get_live_account_handle() or "").strip().lstrip("@")
                configured_handle = (self._configured_account_handle() or "").strip().lstrip("@")

                if configured_handle and not live_handle:
                    return {
                        "ready": False,
                        "reason": "handle-unresolved",
                        "current_url": current_url,
                        "handle": "",
                        "configured_handle": configured_handle,
                    }

                if configured_handle and live_handle and live_handle.lower() != configured_handle.lower():
                    return {
                        "ready": False,
                        "reason": "handle-mismatch",
                        "current_url": current_url,
                        "handle": live_handle,
                        "configured_handle": configured_handle,
                    }

                if live_handle and self._profile_visibility_issue(live_handle):
                    return {
                        "ready": True,
                        "reason": "ready-profile-visibility-warning",
                        "current_url": self.browser.current_url,
                        "handle": live_handle,
                        "configured_handle": configured_handle,
                        "using_fallback_profile": self.using_fallback_profile,
                        "profile_visibility_issue": True,
                    }

                return {
                    "ready": True,
                    "reason": "ready-fallback" if self.using_fallback_profile else "ready",
                    "current_url": current_url,
                    "handle": live_handle,
                    "configured_handle": configured_handle,
                    "using_fallback_profile": self.using_fallback_profile,
                }
            except Exception:
                continue

        lowered_url = current_url.lower()
        if any(token in lowered_url for token in ("/i/flow/login", "/login", "/signup")):
            return {
                "ready": False,
                "reason": "login-required",
                "current_url": current_url,
                "handle": self.get_live_account_handle(),
                "using_fallback_profile": self.using_fallback_profile,
            }

        login_selectors = [
            (By.NAME, "text"),
            (By.XPATH, "//span[text()='Sign in']"),
            (By.XPATH, "//span[text()='Log in']"),
        ]
        for selector in login_selectors:
            try:
                self.browser.find_element(*selector)
                return {
                    "ready": False,
                    "reason": "login-required",
                    "current_url": current_url,
                    "handle": self.get_live_account_handle(),
                    "using_fallback_profile": self.using_fallback_profile,
                }
            except Exception:
                continue

        # UI fallback: some layouts hide compose textbox until an interaction,
        # but still expose logged-in nav + compose entry points.
        compose_entry_selectors = [
            (By.CSS_SELECTOR, "a[data-testid='SideNav_NewTweet_Button']"),
            (By.CSS_SELECTOR, "button[data-testid='SideNav_NewTweet_Button']"),
            (By.CSS_SELECTOR, "a[href='/compose/post']"),
            (By.XPATH, "//a[contains(@href,'/compose/post') and @role='link']"),
        ]
        shell_selectors = [
            (By.CSS_SELECTOR, "nav[aria-label='Primary']"),
            (By.CSS_SELECTOR, "header[role='banner']"),
            (By.CSS_SELECTOR, "a[data-testid='AppTabBar_Home_Link']"),
        ]

        has_compose_entry = False
        for selector in compose_entry_selectors:
            try:
                self.browser.find_element(*selector)
                has_compose_entry = True
                break
            except Exception:
                continue

        has_logged_shell = False
        for selector in shell_selectors:
            try:
                self.browser.find_element(*selector)
                has_logged_shell = True
                break
            except Exception:
                continue

        if has_compose_entry or has_logged_shell:
            live_handle = (self.get_live_account_handle() or "").strip().lstrip("@")
            configured_handle = (self._configured_account_handle() or "").strip().lstrip("@")

            if configured_handle and live_handle and live_handle.lower() != configured_handle.lower():
                return {
                    "ready": False,
                    "reason": "handle-mismatch",
                    "current_url": current_url,
                    "handle": live_handle,
                    "configured_handle": configured_handle,
                }

            return {
                "ready": True,
                "reason": "ready-ui-fallback-fallback-profile" if self.using_fallback_profile else "ready-ui-fallback",
                "current_url": current_url,
                "handle": live_handle,
                "configured_handle": configured_handle,
                "using_fallback_profile": self.using_fallback_profile,
            }

        return {
            "ready": False,
            "reason": "compose-ui-missing",
            "current_url": current_url,
            "handle": self.get_live_account_handle(),
            "using_fallback_profile": self.using_fallback_profile,
        }

    def _log_transaction(self, action: str, status: str, metadata: Optional[dict] = None) -> None:
        """
        Logs a post transaction attempt for debugging and audit trail.
        
        Args:
            action (str): e.g., 'post_attempt', 'session_check', 'profile_healthcheck'
            status (str): e.g., 'success', 'failed', 'skipped'
            metadata (dict): Additional context (text snippet, error, reason, etc.)
        """
        import os
        from config import ROOT_DIR
        
        log_dir = os.path.join(ROOT_DIR, 'logs', 'transaction_log')
        os.makedirs(log_dir, exist_ok=True)
        
        log_entry = {
            'timestamp': datetime.now().isoformat(),
            'account_uuid': self.account_uuid,
            'account_nickname': self.account_nickname,
            'action': action,
            'status': status,
            'using_fallback_profile': self.using_fallback_profile,
            **(metadata or {})
        }
        
        log_file = os.path.join(log_dir, f"{self.account_nickname}.log")
        try:
            existing_logs = []
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r') as f:
                        existing_logs = json.load(f)
                except (json.JSONDecodeError, IOError):
                    existing_logs = []
            
            # Keep last 100 transaction logs per account
            existing_logs.append(log_entry)
            existing_logs = existing_logs[-100:]

            fd, temp_path = tempfile.mkstemp(
                dir=os.path.dirname(log_file), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(existing_logs, temp_file, indent=2)
                os.replace(temp_path, log_file)
            except Exception:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            # Non-fatal: transaction logging should not break posting
            pass

    def _clean_stale_locks(self) -> int:
        """
        Cleans stale Firefox lock files that indicate crashed/hung processes.
        Checks for zombie processes before removing locks.
        
        Returns:
            count (int): Number of locks cleaned
        """
        import subprocess
        
        cleaned = 0
        lock_files = ['.parentlock', 'parent.lock', 'lock']
        
        for lock_name in lock_files:
            lock_path = os.path.join(self.fp_profile_path, lock_name)
            if not os.path.exists(lock_path):
                continue
            
            try:
                # On macOS, check if the process owning this lock is still alive
                result = subprocess.run(
                    ['lsof', lock_path],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                # If lsof returns no process holding the lock, it's stale
                lines = result.stdout.strip().split('\n')[1:]  # Skip header
                if not lines or (len(lines) == 1 and not lines[0]):
                    # Lock is stale; safe to remove
                    try:
                        os.remove(lock_path)
                        cleaned += 1
                        if get_verbose():
                            info(f"Cleaned stale lock: {lock_path}")
                    except OSError:
                        pass
            except (subprocess.TimeoutExpired, FileNotFoundError):
                # lsof not available or timeout; skip this check
                pass
        
        return cleaned

    def _verify_cooldown_strict(self, existing_posts: list[dict]) -> Optional[str]:
        """
        Strict cooldown verification that also checks transaction logs for recent attempts.
        Returns reason string if cooldown active, None if OK to post.
        """
        if not existing_posts:
            return None
        
        now = datetime.now()
        min_gap = 1800  # 30 minutes
        
        # Check cached posts
        last_post = existing_posts[-1]
        try:
            last_dt = datetime.strptime(last_post["date"], "%m/%d/%Y, %H:%M:%S")
            elapsed = (now - last_dt).total_seconds()
            
            if elapsed < min_gap:
                remaining = int((min_gap - elapsed) / 60)
                return f"cooldown:{remaining}m"
        except (ValueError, KeyError):
            pass
        
        # Check transaction logs for very recent failed attempts (last 5 min)
        log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                '..', 'logs', 'transaction_log', f"{self.account_nickname}.log")
        try:
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    logs = json.load(f)
                
                for log_entry in reversed(logs[-20:]):  # Check last 20 attempts
                    try:
                        log_time = datetime.fromisoformat(log_entry['timestamp'])
                        time_since = (now - log_time).total_seconds()
                        
                        # If we see a failed post within 2 min, add a short buffer
                        if time_since < 120 and log_entry.get('action') == 'post_attempt':
                            if log_entry.get('status') == 'failed':
                                return f"recent_failure:{int(120 - time_since)}s"
                    except (ValueError, KeyError):
                        continue
        except (json.JSONDecodeError, IOError):
            pass
        
        return None

    def _cache_integrity_check(self) -> dict:
        """
        Validates cache file integrity before and after posts.
        
        Returns:
            status (dict): integrity report
        """
        from cache import get_twitter_cache_path
        
        cache_path = get_twitter_cache_path()
        issues = []
        
        if not os.path.exists(cache_path):
            return {"valid": True, "issues": [], "warning": "cache_not_yet_created"}
        
        try:
            with open(cache_path, 'r') as f:
                cache_data = json.load(f)
            
            account_uuid = self.account_uuid
            accounts = cache_data.get("accounts", []) if isinstance(cache_data, dict) else []
            account_cache = None

            for account in accounts:
                if isinstance(account, dict) and account.get("id") == account_uuid:
                    account_cache = account
                    break

            if account_cache is None:
                issues.append(f"account_uuid_missing:{account_uuid}")

            if account_cache:
                posts = account_cache.get("posts", [])
                if posts:
                    for i, post in enumerate(posts):
                        if not isinstance(post, dict):
                            issues.append(f"post_{i}_not_dict")
                            continue
                        if "date" in post and not isinstance(post["date"], str):
                            issues.append(f"post_{i}_date_invalid_type")
                        if "content" in post and not isinstance(post["content"], str):
                            issues.append(f"post_{i}_content_not_string")
            
            return {
                "valid": len(issues) == 0,
                "issues": issues,
                "post_count": len((account_cache or {}).get("posts", []))
            }
        
        except json.JSONDecodeError as e:
            return {
                "valid": False,
                "issues": [f"json_decode_error:{str(e)}"],
                "corrupted": True
            }
        except Exception as e:
            return {
                "valid": False,
                "issues": [f"check_error:{str(e)}"]
            }

    def _collect_timeline_posts(self, handle: str, limit: int = 5) -> list[dict]:
        """
        Collects visible posts from the account timeline.

        Args:
            handle (str): Username without @
            limit (int): Number of timeline items to return

        Returns:
            posts (list[dict]): Timeline post previews with text and URL
        """
        self.browser.get(self._timeline_url_for_handle(handle))
        # X uses React lazy-loading; wait for article elements to render.
        time.sleep(5)
        posts = self._collect_timeline_posts_from_current_page(limit=limit)
        if not posts:
            # One retry for slow connections or brief rate-limit pauses
            time.sleep(4)
            posts = self._collect_timeline_posts_from_current_page(limit=limit)
        return posts

    def _collect_timeline_posts_from_current_page(self, limit: int = 5) -> list[dict]:
        """
        Collects visible posts from the currently loaded page.

        Args:
            limit (int): Number of timeline items to return

        Returns:
            posts (list[dict]): Timeline post previews with text and URL
        """

        posts: list[dict] = []
        seen_urls: set[str] = set()
        anchors = self.browser.find_elements(By.XPATH, "//a[contains(@href, '/status/')]")

        for anchor in anchors[: max(limit * 8, 20)]:
            canonical_url = ""
            href = anchor.get_attribute("href") or ""
            canonical = self._canonical_status_url(href)
            if canonical:
                canonical_url = canonical

            if canonical_url and canonical_url in seen_urls:
                continue

            try:
                article = anchor.find_element(By.XPATH, "ancestor::article[1]")
                article_text = (article.text or "").strip()
            except Exception:
                article_text = (anchor.text or "").strip()

            if canonical_url:
                seen_urls.add(canonical_url)

            if not article_text and not canonical_url:
                continue

            posts.append(
                {
                    "text": article_text,
                    "normalized_text": self._normalize_tweet(article_text),
                    "tweet_url": canonical_url,
                }
            )

            if len(posts) >= limit:
                break

        return posts

    def _cache_update_post_verification(self, target_post: dict, tweet_url: Optional[str], verified: bool) -> None:
        """
        Updates cached metadata for a previously stored post.

        Args:
            target_post (dict): Cached post to update
            tweet_url (Optional[str]): Resolved canonical tweet URL; empty clears stale URL
            verified (bool): Verification flag
        """
        cache_path = get_twitter_cache_path()

        try:
            with open(cache_path, "r") as file:
                parsed = json.load(file)
        except (json.JSONDecodeError, OSError):
            return

        updated = False
        for account in parsed.get("accounts", []):
            if account.get("id") != self.account_uuid:
                continue
            posts = account.get("posts", [])
            for cached_post in reversed(posts):
                if (
                    cached_post.get("date") == target_post.get("date")
                    and cached_post.get("content") == target_post.get("content")
                ):
                    attempts_raw = cached_post.get("verification_attempts", 0)
                    try:
                        attempts = int(attempts_raw)
                    except Exception:
                        attempts = 0
                    cached_post["post_verified"] = verified
                    cached_post["verification_state"] = "verified" if verified else "pending"
                    cached_post["last_verification_checked_at"] = datetime.now().isoformat(timespec="seconds")
                    cached_post["verification_attempts"] = 0 if verified else attempts + 1
                    if verified:
                        cached_post["verified_at"] = datetime.now().isoformat(timespec="seconds")
                        cached_post["publish_likelihood"] = "published-confirmed"
                        cached_post["confidence_score"] = 100
                        cached_post["confidence_level"] = "verified"
                    else:
                        existing_score_raw = cached_post.get("confidence_score", 35)
                        try:
                            existing_score = int(existing_score_raw)
                        except Exception:
                            existing_score = 35
                        normalized_score = max(20, min(existing_score, 60))
                        cached_post["publish_likelihood"] = self._pending_publish_likelihood(cached_post)
                        cached_post["confidence_score"] = normalized_score
                        cached_post["confidence_level"] = self._confidence_level(normalized_score)
                    if tweet_url is not None:
                        cached_post["tweet_url"] = tweet_url
                    evidence = cached_post.get("publish_evidence")
                    if not isinstance(evidence, dict):
                        evidence = {}
                    evidence["last_verification_checked_at"] = cached_post["last_verification_checked_at"]
                    evidence["verification_attempts"] = cached_post["verification_attempts"]
                    evidence["verified"] = bool(verified)
                    if verified:
                        evidence["verified_at"] = cached_post.get("verified_at", "")
                    cached_post["publish_evidence"] = evidence
                    updated = True
                    break
            break

        if not updated:
            return

        tmp_path = cache_path + ".tmp"
        with open(tmp_path, "w") as file:
            json.dump(parsed, file, indent=4)
        os.replace(tmp_path, cache_path)

    def _verify_latest_post_phase3(self, attempts: int = 2, retry_delay_seconds: int = 8) -> dict:
        """
        Runs the Phase 3 verification pipeline against the newest cached post.

        Returns:
            result (dict): Lightweight verification summary.
        """
        attempts = max(1, int(attempts or 1))
        retry_delay_seconds = max(0, int(retry_delay_seconds or 0))

        last_result = {
            "verified": False,
            "tweet_url": "",
            "match_method": "",
            "error": "",
        }

        for attempt_index in range(attempts):
            try:
                result = self.verify_recent_cached_posts(limit=1, backfill=True)
            except Exception as exc:
                last_result = {
                    "verified": False,
                    "tweet_url": "",
                    "match_method": "",
                    "error": str(exc),
                }
                break

            latest = (result.get("results", []) or [{}])[0]
            last_result = {
                "verified": bool(latest.get("verified", False)),
                "tweet_url": str(latest.get("tweet_url", "") or ""),
                "match_method": str(latest.get("match_method", "") or ""),
                "error": str(result.get("error", "") or ""),
            }
            if last_result["verified"] or attempt_index >= attempts - 1:
                break
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)

        return last_result

    def _parse_cached_post_datetime(self, date_text: str) -> Optional[datetime]:
        """
        Parses cached post date strings into datetime.

        Args:
            date_text (str): Cached date text

        Returns:
            parsed (Optional[datetime]): Parsed datetime or None
        """
        raw = (date_text or "").strip()
        if not raw:
            return None

        formats = [
            "%m/%d/%Y, %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None

    def _should_attempt_verification_search(self, cached_post: dict) -> bool:
        """
        Determines whether expensive search-based backfill should run.

        Purpose:
        - Avoid ghost searches on low-signal posts that were likely never published.
        - Keep verification focused on recent/high-likelihood pending posts.

        Args:
            cached_post (dict): Cached post entry

        Returns:
            should_search (bool): Whether to run search fallback
        """
        content = str(cached_post.get("content", "")).strip()
        if len(content) < 25:
            return False

        signals = cached_post.get("confidence_signals") or {}
        compose_accepted = bool(signals.get("compose_accepted", False))
        existing_tweet_url = bool(str(cached_post.get("tweet_url", "")).strip())

        confidence_raw = cached_post.get("confidence_score", 0)
        try:
            confidence_score = int(confidence_raw)
        except Exception:
            confidence_score = 0

        created_at = self._parse_cached_post_datetime(str(cached_post.get("date", "")))
        attempts_raw = cached_post.get("verification_attempts", 0)
        try:
            verification_attempts = int(attempts_raw)
        except Exception:
            verification_attempts = 0
        age_hours = None
        if created_at is not None:
            try:
                age_hours = (datetime.now() - created_at).total_seconds() / 3600.0
            except Exception:
                age_hours = None

        likelihood = self._pending_publish_likelihood(cached_post)
        high_likelihood = (
            compose_accepted
            or existing_tweet_url
            or confidence_score >= 80
            or likelihood in {"published-likely", "published-confirmed"}
        )

        max_age_hours = 36
        retry_cap = 8
        if likelihood == "published-likely":
            max_age_hours = 120
            retry_cap = 20
        elif likelihood == "published-ambiguous":
            max_age_hours = 72
            retry_cap = 12

        recent_enough = age_hours is None or age_hours <= max_age_hours
        under_retry_cap = verification_attempts < retry_cap
        return recent_enough and high_likelihood and under_retry_cap

    def _pending_publish_likelihood(self, cached_post: dict) -> str:
        """Infer publish likelihood for a cached pending post."""
        explicit = str(cached_post.get("publish_likelihood", "")).strip()
        if explicit and explicit != "pending-unclassified":
            return explicit

        if str(cached_post.get("tweet_url", "")).strip():
            return "published-confirmed"

        signals = cached_post.get("confidence_signals") or {}
        compose_candidates = int(signals.get("compose_candidates", 0) or 0)
        compose_matching_candidates = int(signals.get("compose_matching_candidates", 0) or 0)
        timeline_items = int(signals.get("timeline_items", 0) or 0)

        if compose_matching_candidates > 0:
            return "published-likely"
        if compose_candidates >= 3 and timeline_items >= 3:
            return "published-likely"
        if compose_candidates > 0 or timeline_items > 0:
            return "published-ambiguous"
        return "pending-unclassified"

    def _pending_priority_key(self, cached_post: dict) -> tuple[int, int, float]:
        """Sort key for pending-post recovery priority.

        Lower is better: stronger publish evidence first, fewer attempts next,
        newer posts before older ones when all else is equal.
        """
        likelihood = self._pending_publish_likelihood(cached_post)
        rank_map = {
            "published-confirmed": 0,
            "published-likely": 1,
            "published-ambiguous": 2,
            "publish-signal-weak": 3,
            "pending-unclassified": 4,
        }
        attempts_raw = cached_post.get("verification_attempts", 0)
        try:
            attempts = int(attempts_raw)
        except Exception:
            attempts = 0

        created_at = self._parse_cached_post_datetime(str(cached_post.get("date", "")))
        timestamp_score = 0.0
        if created_at is not None:
            timestamp_score = -created_at.timestamp()

        return (rank_map.get(likelihood, 9), attempts, timestamp_score)

    def _reclassify_exhausted_recovery_posts(self) -> int:
        """
        Marks pending posts whose recovery has been exhausted so they no longer
        block the readiness gate.

        A post is considered recovery-exhausted when:
        - It is still in 'pending' verification state (no confirmed URL)
        - It is older than EXHAUSTED_AGE_HOURS
        - It has had at least EXHAUSTED_MIN_ATTEMPTS failed verification attempts

        Returns:
            count (int): Number of posts reclassified
        """
        EXHAUSTED_AGE_HOURS = 20
        EXHAUSTED_MIN_ATTEMPTS = 12

        cache_path = get_twitter_cache_path()
        try:
            with open(cache_path, "r") as fh:
                parsed = json.load(fh)
        except Exception:
            return 0

        reclassified = 0
        now = datetime.now()
        for account in parsed.get("accounts", []):
            if account.get("id") != self.account_uuid:
                continue
            for post in account.get("posts", []):
                if bool(post.get("post_verified", False)):
                    continue
                if str(post.get("verification_state", "")).strip().lower() != "pending":
                    continue
                if str(post.get("publish_likelihood", "")).strip() == "recovery-exhausted":
                    continue
                attempts_raw = post.get("verification_attempts", 0)
                try:
                    attempts = int(attempts_raw)
                except Exception:
                    attempts = 0
                if attempts < EXHAUSTED_MIN_ATTEMPTS:
                    continue
                created_at = self._parse_cached_post_datetime(str(post.get("date", "")))
                if created_at is None:
                    continue
                age_hours = (now - created_at).total_seconds() / 3600.0
                if age_hours < EXHAUSTED_AGE_HOURS:
                    continue
                post["publish_likelihood"] = "recovery-exhausted"
                reclassified += 1
            break

        if reclassified > 0:
            tmp = cache_path + ".tmp"
            try:
                with open(tmp, "w") as fh:
                    json.dump(parsed, fh, indent=4)
                os.replace(tmp, cache_path)
            except Exception:
                pass

        return reclassified

    def verify_recent_cached_posts(self, limit: int = 3, backfill: bool = True, pending_only: bool = False) -> dict:
        """
        Verifies recent cached posts against the live account timeline.

        Args:
            limit (int): Number of most recent cached posts to verify
            backfill (bool): Whether to persist recovered permalinks

        Returns:
            result (dict): Verification summary
        """
        # Reclassify any posts whose recovery is genuinely exhausted before
        # selecting candidates so the priority sort and gate checks are accurate.
        self._reclassify_exhausted_recovery_posts()
        cached_posts = self.get_posts()
        candidates = cached_posts
        if pending_only:
            candidates = [
                post for post in cached_posts
                if (not bool(post.get("post_verified", False)))
                or (str(post.get("verification_state", "")).strip().lower() == "pending")
                or (not str(post.get("tweet_url", "")).strip())
            ]
            candidates = sorted(candidates, key=self._pending_priority_key)

        recent_cached = candidates[:limit] if pending_only and limit > 0 else (candidates[-limit:] if limit > 0 else [])
        handle = self.get_live_account_handle()
        if not handle:
            return {
                "account": self.account_nickname,
                "handle": "",
                "verified_count": 0,
                "checked_count": len(recent_cached),
                "results": [],
                "error": "Could not resolve logged-in X handle from browser profile.",
            }

        try:
            live_posts = self._collect_timeline_posts(handle, limit=max(limit + 2, 5))
        except Exception as exc:
            return {
                "account": self.account_nickname,
                "handle": handle,
                "verified_count": 0,
                "checked_count": len(recent_cached),
                "results": [],
                "error": f"Timeline verification failed: {exc}",
            }

        results: list[dict] = []
        verified_count = 0
        for cached_post in reversed(recent_cached):
            cached_url = self._canonical_status_url(str(cached_post.get("tweet_url", "")))
            cached_content = str(cached_post.get("content", ""))
            cached_norm = self._normalize_tweet(cached_content)
            match_url = ""
            match_method = ""

            # Strategy 1: URL match (highest confidence)
            for live_post in live_posts:
                live_url = live_post.get("tweet_url", "")
                if cached_url and live_url and cached_url == live_url:
                    match_url = live_url
                    match_method = "permalink-url"
                    break

            # Strategy 2: Enhanced text matching (using hardener)
            if not match_url and cached_content:
                for live_post in live_posts:
                    live_content = live_post.get("normalized_text", "")
                    if not live_content:
                        continue

                    # Use enhanced matching from hardener
                    is_match = PublishVerificationHardener.is_strong_match(
                        cached_content, live_content, url_match=False
                    )
                    if is_match:
                        match_url = live_post.get("tweet_url", "")
                        match_method = "enhanced-text"
                        break

                    # Fallback: basic similarity check
                    if cached_norm and live_content:
                        match_score = PublishVerificationHardener.compute_match_score(
                            cached_norm, live_content
                        )
                        if match_score >= 80.0:
                            match_url = live_post.get("tweet_url", "")
                            match_method = "text-similarity"
                            break

            # Strategy 3: Direct profile/page recovery before broader search
            if not match_url and cached_content and self._should_attempt_verification_search(cached_post):
                created_at = self._parse_cached_post_datetime(str(cached_post.get("date", "")))
                age_hours = (
                    (datetime.now() - created_at).total_seconds() / 3600.0
                    if created_at else 0.0
                )
                stale_post = age_hours > 48
                recovered = self._recover_permalink_via_profile_pages(
                    handle=handle,
                    normalized_target=cached_norm,
                    stale=stale_post,
                )
                if recovered:
                    match_url = recovered
                    match_method = "profile-page-recovery"

            # Strategy 4: Enhanced search fallback (multi-query, guarded)
            if not match_url and cached_content and self._should_attempt_verification_search(cached_post):
                search_queries = PublishVerificationHardener.build_search_queries(
                    cached_content, max_queries=2
                )
                for query in search_queries:
                    recovered = self._resolve_post_permalink_via_search(
                        handle=handle,
                        normalized_target=query,
                        raw_text=cached_content,
                        max_queries=2,
                    )
                    if recovered:
                        match_url = recovered
                        match_method = "multi-query-search"
                        break

            verified = bool(match_url)
            if verified:
                verified_count += 1
                if backfill:
                    self._cache_update_post_verification(cached_post, match_url, True)
            elif backfill:
                self._cache_update_post_verification(cached_post, "", False)

            debug_payload = {}
            if isinstance(self.last_permalink_debug, dict):
                pages_tried = self.last_permalink_debug.get("pages_tried", []) or []
                search_queries_tried = self.last_permalink_debug.get("search_queries_tried", []) or []
                debug_payload = {
                    "match_method": str(self.last_permalink_debug.get("match_method", "") or ""),
                    "pages_tried": len(pages_tried),
                    "search_queries_tried": len(search_queries_tried),
                    "compose_candidates": int(self.last_permalink_debug.get("compose_candidates", 0) or 0),
                    "profile_candidates": int(self.last_permalink_debug.get("profile_candidates", 0) or 0),
                    "timeline_items": int(self.last_permalink_debug.get("timeline_items", 0) or 0),
                }

            results.append(
                {
                    "date": cached_post.get("date", ""),
                    "preview": (cached_post.get("content", "") or "")[:90],
                    "verified": verified,
                    "tweet_url": match_url or cached_url,
                    "match_method": match_method,
                    "publish_likelihood": self._pending_publish_likelihood(cached_post),
                    "verification_attempts": cached_post.get("verification_attempts", 0),
                    "recovery_debug": debug_payload,
                }
            )

        return {
            "account": self.account_nickname,
            "handle": handle,
            "verified_count": verified_count,
            "checked_count": len(recent_cached),
            "results": results,
            "error": "",
        }

    def verify_pending_cached_posts(self, limit: int = 20, backfill: bool = True) -> dict:
        """
        Verifies only pending/unverified cached posts against the live timeline.

        Args:
            limit (int): Number of pending cached posts to verify
            backfill (bool): Whether to persist recovered permalinks

        Returns:
            result (dict): Verification summary
        """
        return self.verify_recent_cached_posts(limit=limit, backfill=backfill, pending_only=True)

    def _resolve_post_permalink(self, posted_text: str) -> str:
        """
        Best-effort permalink resolution after posting.

        Args:
            posted_text (str): Posted tweet text

        Returns:
            tweet_url (str): Canonical tweet URL if found
        """
        normalized_target = self._normalize_tweet(posted_text)
        expected_handle = (
            self._configured_account_handle() or self._resolve_account_handle() or ""
        ).strip().lstrip("@").lower()
        self.last_permalink_debug = {
            "expected_handle": expected_handle,
            "compose_candidates": 0,
            "compose_matching_candidates": 0,
            "compose_samples": [],
            "profile_candidates": 0,
            "timeline_items": 0,
            "search_queries_tried": [],
            "match_method": "",
            "pages_tried": [],
        }

        for _ in range(6):
            candidates = self._collect_status_link_candidates()
            self.last_permalink_debug["compose_candidates"] = max(
                int(self.last_permalink_debug.get("compose_candidates", 0)), len(candidates)
            )
            if candidates and not self.last_permalink_debug.get("compose_samples"):
                self.last_permalink_debug["compose_samples"] = candidates[:5]
            if candidates:
                if expected_handle:
                    matching_count = 0
                    for candidate in candidates:
                        match = re.search(r"^https://x\.com/([A-Za-z0-9_]+)/status/\d+$", candidate)
                        if not match:
                            continue
                        if match.group(1).lower() == expected_handle:
                            matching_count += 1
                            self.last_permalink_debug["compose_matching_candidates"] = max(
                                int(self.last_permalink_debug.get("compose_matching_candidates", 0)),
                                matching_count,
                            )
                            if self._is_permalink_conflict(candidate, normalized_target):
                                continue
                            self.last_permalink_debug["match_method"] = "compose-candidates"
                            return candidate
                else:
                    if candidates and self._is_permalink_conflict(candidates[0], normalized_target):
                        continue
                    self.last_permalink_debug["match_method"] = "compose-first"
                    return candidates[0]
            time.sleep(1)

        handle = (self._configured_account_handle() or self._resolve_account_handle() or "").strip().lstrip("@")
        if not handle:
            return ""

        try:
            direct_match = self._recover_permalink_via_profile_pages(
                handle=handle,
                normalized_target=normalized_target,
                stale=False,  # post-time: always fresh, scan all pages
            )
            if direct_match:
                return direct_match
        except Exception:
            return ""

        debug = self.last_permalink_debug if isinstance(self.last_permalink_debug, dict) else {}
        compose_candidates = int(debug.get("compose_candidates", 0) or 0)
        timeline_items = int(debug.get("timeline_items", 0) or 0)
        enough_signal_for_search = (
            len((posted_text or "").split()) >= 6
            and len((posted_text or "").strip()) >= 35
            and (compose_candidates > 0 or timeline_items > 0)
        )

        if not enough_signal_for_search:
            if isinstance(self.last_permalink_debug, dict):
                self.last_permalink_debug["match_method"] = "search-skipped-low-signal"
            return ""

        search_match = self._resolve_post_permalink_via_search(
            handle=handle,
            normalized_target=normalized_target,
            raw_text=posted_text,
            max_queries=3,
        )
        if search_match:
            self.last_permalink_debug["match_method"] = "search-text"
            return search_match

        return ""

    def _build_text_search_urls(self, handle: str, raw_text: str, max_queries: int = 3) -> list[str]:
        """
        Builds X live-search URLs likely to surface a recently posted tweet.

        Args:
            handle (str): Username without @
            raw_text (str): Original post text
            max_queries (int): Maximum query URLs to generate

        Returns:
            urls (list[str]): Candidate search URLs
        """
        compact = re.sub(r"\s+", " ", (raw_text or "")).strip()
        if not compact:
            return []

        search_terms = PublishVerificationHardener.build_search_queries(compact, max_queries=max_queries)
        if not search_terms:
            return []

        urls: list[str] = []
        for snippet in search_terms[:max_queries]:
            cleaned = re.sub(r'[^A-Za-z0-9#_\s]', ' ', snippet)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            if not cleaned:
                continue
            query = f"from:{handle} {cleaned}"
            encoded = quote_plus(query)
            urls.append(f"https://x.com/search?q={encoded}&src=typed_query&f=live")

        return urls[:max_queries]

    def _profile_recovery_pages(self, handle: str, stale: bool = False) -> list[str]:
        """Return direct pages worth checking before broader text search.

        stale=True means the post is older than ~48h: skip media/search pages
        since recent feed pages are cheapest and most likely to hit.
        """
        if not handle:
            return []

        if stale:
            return [
                f"https://x.com/{handle}",
                f"https://x.com/{handle}/with_replies",
            ]
        return [
            f"https://x.com/{handle}",
            f"https://x.com/{handle}/with_replies",
            f"https://x.com/{handle}/media",
            f"https://x.com/search?q=from%3A{handle}&src=typed_query&f=live",
        ]

    def _recover_permalink_from_loaded_page(
        self,
        handle: str,
        normalized_target: str,
        limit: int = 18,
    ) -> str:
        """Recover a permalink from the currently loaded page if possible."""
        handle_lower = handle.lower().lstrip("@")

        live_posts = self._collect_timeline_posts_from_current_page(limit=limit)
        if isinstance(self.last_permalink_debug, dict):
            self.last_permalink_debug["timeline_items"] = max(
                int(self.last_permalink_debug.get("timeline_items", 0)), len(live_posts)
            )

        for live_post in live_posts:
            live_norm = live_post.get("normalized_text", "")
            if normalized_target and live_norm and not self._is_probable_post_match(normalized_target, live_norm):
                continue
            canonical = live_post.get("tweet_url", "")
            if canonical and not self._is_permalink_conflict(canonical, normalized_target):
                return canonical

        status_candidates = self._collect_status_link_candidates()
        filtered_candidates: list[str] = []
        for candidate in status_candidates:
            match = re.search(r"^https://x\.com/([A-Za-z0-9_]+)/status/\d+$", candidate)
            if not match:
                continue
            if match.group(1).lower() == handle_lower:
                filtered_candidates.append(candidate)

        if isinstance(self.last_permalink_debug, dict):
            self.last_permalink_debug["profile_candidates"] = max(
                int(self.last_permalink_debug.get("profile_candidates", 0)), len(filtered_candidates)
            )

        for candidate in filtered_candidates:
            if not self._is_permalink_conflict(candidate, normalized_target):
                return candidate

        return ""

    def _recover_permalink_via_profile_pages(
        self,
        handle: str,
        normalized_target: str,
        stale: bool = False,
    ) -> str:
        """Sweep direct profile-scoped pages before broader search fallback.

        Cost controls:
        - stale=True limits pages to profile + with_replies only
        - Returns immediately after finding a match on the initial page load
        - Only scrolls deeper when the first pass found status links but no match
        - Skips further scroll steps when the page returned 0 timeline items
        """
        if not handle:
            return ""

        for page_url in self._profile_recovery_pages(handle, stale=stale):
            if isinstance(self.last_permalink_debug, dict):
                self.last_permalink_debug.setdefault("pages_tried", []).append(page_url)

            try:
                self.browser.get(page_url)
                time.sleep(2)
            except Exception:
                continue

            # Fast check immediately after page load (no scroll yet)
            initial = self._recover_permalink_from_loaded_page(
                handle=handle,
                normalized_target=normalized_target,
                limit=18,
            )
            if initial:
                if isinstance(self.last_permalink_debug, dict):
                    if page_url.endswith("/with_replies"):
                        self.last_permalink_debug["match_method"] = "profile-with-replies"
                    elif page_url.endswith("/media"):
                        self.last_permalink_debug["match_method"] = "profile-media"
                    elif "/search?" in page_url:
                        self.last_permalink_debug["match_method"] = "profile-live-search"
                    else:
                        self.last_permalink_debug["match_method"] = "profile-direct"
                return initial

            # Only scroll if the page already has some status links (worth deeper look)
            page_has_links = bool(self._collect_status_link_candidates())
            if not page_has_links:
                continue

            for scroll_target in (900, 2000):
                try:
                    self.browser.execute_script(f"window.scrollTo(0, {scroll_target});")
                    time.sleep(1)
                except Exception:
                    pass

                recovered = self._recover_permalink_from_loaded_page(
                    handle=handle,
                    normalized_target=normalized_target,
                    limit=18,
                )
                if recovered:
                    if isinstance(self.last_permalink_debug, dict):
                        if page_url.endswith("/with_replies"):
                            self.last_permalink_debug["match_method"] = "profile-with-replies"
                        elif page_url.endswith("/media"):
                            self.last_permalink_debug["match_method"] = "profile-media"
                        elif "/search?" in page_url:
                            self.last_permalink_debug["match_method"] = "profile-live-search"
                        else:
                            self.last_permalink_debug["match_method"] = "profile-direct"
                    return recovered

        return ""

    def _resolve_post_permalink_via_search(
        self,
        handle: str,
        normalized_target: str,
        raw_text: str,
        max_queries: int = 3,
    ) -> str:
        """
        Attempts permalink discovery via text search scoped to account handle.

        Args:
            handle (str): Username without @
            normalized_target (str): Normalized target tweet text
            raw_text (str): Original target tweet text
            max_queries (int): Maximum live-search queries to try

        Returns:
            tweet_url (str): Canonical tweet URL if found
        """
        if not handle:
            return ""

        search_urls = self._build_text_search_urls(handle=handle, raw_text=raw_text, max_queries=max_queries)
        if not search_urls:
            return ""

        try:
            for search_url in search_urls:
                if isinstance(self.last_permalink_debug, dict):
                    self.last_permalink_debug.setdefault("search_queries_tried", []).append(search_url)

                self.browser.get(search_url)
                time.sleep(3)
                for scroll_target in (1200, 2600):
                    try:
                        self.browser.execute_script(f"window.scrollTo(0, {scroll_target});")
                        time.sleep(1)
                    except Exception:
                        pass

                    live_posts = self._collect_timeline_posts_from_current_page(limit=25)
                    if isinstance(self.last_permalink_debug, dict):
                        self.last_permalink_debug["timeline_items"] = max(
                            int(self.last_permalink_debug.get("timeline_items", 0)), len(live_posts)
                        )

                    for live_post in live_posts:
                        live_norm = live_post.get("normalized_text", "")
                        if normalized_target and live_norm and not self._is_probable_post_match(normalized_target, live_norm):
                            continue
                        canonical = live_post.get("tweet_url", "")
                        if canonical:
                            return canonical

                    status_candidates = self._collect_status_link_candidates()
                    handle_lower = handle.lower()
                    filtered = []
                    for candidate in status_candidates:
                        match = re.search(r"^https://x\.com/([A-Za-z0-9_]+)/status/\d+$", candidate)
                        if not match:
                            continue
                        if match.group(1).lower() == handle_lower:
                            filtered.append(candidate)

                    for candidate in filtered:
                        if not self._is_permalink_conflict(candidate, normalized_target):
                            return candidate
        except Exception:
            return ""

        return ""

    def _is_permalink_conflict(self, candidate_url: str, target_norm: str) -> bool:
        """
        Prevents reusing a permalink that is already tied to another cached post.

        Args:
            candidate_url (str): Candidate tweet permalink
            target_norm (str): Normalized text for the post being resolved

        Returns:
            conflict (bool): Whether candidate appears to belong to a different cached post
        """
        canonical = self._canonical_status_url(candidate_url)
        if not canonical:
            return False

        for cached_post in self.get_posts():
            cached_url = self._canonical_status_url(str(cached_post.get("tweet_url", "")))
            if cached_url != canonical:
                continue

            cached_norm = self._normalize_tweet(str(cached_post.get("content", "")))
            if not cached_norm or not target_norm:
                return True

            similarity = SequenceMatcher(None, cached_norm, target_norm).ratio()
            if similarity >= 0.90 or cached_norm[:80] in target_norm or target_norm[:80] in cached_norm:
                return False

            return True

        return False

    def _is_probable_post_match(self, target_norm: str, live_norm: str) -> bool:
        """
        Heuristic matcher for posted text vs timeline text.

        Handles truncation and minor rendering differences more robustly than strict ratio checks.

        Args:
            target_norm (str): Normalized candidate text that was posted
            live_norm (str): Normalized timeline text

        Returns:
            matched (bool): Whether the two texts are likely the same post
        """
        if not target_norm or not live_norm:
            return False

        # Fast direct containment checks for truncation/full-text cases.
        if target_norm[:80] in live_norm or live_norm[:80] in target_norm:
            return True

        # Sequence similarity with slightly lower threshold than strict verification.
        similarity = SequenceMatcher(None, target_norm, live_norm).ratio()
        if similarity >= 0.74:
            return True

        # Token overlap fallback to handle punctuation/format differences.
        target_tokens = set(target_norm.split())
        live_tokens = set(live_norm.split())
        if not target_tokens or not live_tokens:
            return False

        overlap = len(target_tokens & live_tokens)
        smaller = min(len(target_tokens), len(live_tokens))
        return smaller > 0 and (overlap / smaller) >= 0.70

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

    def _confidence_level(self, score: int) -> str:
        """
        Maps confidence score into a level label.

        Args:
            score (int): 0..100 score

        Returns:
            level (str): low|medium|high|verified
        """
        if score >= 100:
            return "verified"
        if score >= 80:
            return "high"
        if score >= 50:
            return "medium"
        return "low"

    def _compute_post_confidence(self, tweet_url: str) -> dict:
        """
        Computes a Phase 1 publish-confidence score for a just-posted tweet.

        Signals considered:
        - Compose accepted by X UI
        - Canonical permalink found
        - Match method quality
        - Handle-consistent candidate evidence

        Args:
            tweet_url (str): Resolved permalink (or empty)

        Returns:
            payload (dict): score, level, and signal details
        """
        debug = self.last_permalink_debug if isinstance(self.last_permalink_debug, dict) else {}
        match_method = str(debug.get("match_method", "")).strip().lower()
        compose_candidates = int(debug.get("compose_candidates", 0) or 0)
        compose_matching_candidates = int(debug.get("compose_matching_candidates", 0) or 0)
        timeline_items = int(debug.get("timeline_items", 0) or 0)

        score = 35
        if tweet_url:
            score += 40

        if match_method in {"timeline-text", "search-text"}:
            score += 20
        elif match_method in {"compose-candidates", "page-weak", "compose-first"}:
            score += 10

        if compose_matching_candidates > 0:
            score += 8

        if compose_candidates > 0 and timeline_items > 0:
            score += 4

        score = max(0, min(score, 95))
        level = self._confidence_level(score)

        return {
            "score": score,
            "level": level,
            "signals": {
                "compose_accepted": True,
                "tweet_url_found": bool(tweet_url),
                "match_method": match_method,
                "compose_candidates": compose_candidates,
                "compose_matching_candidates": compose_matching_candidates,
                "timeline_items": timeline_items,
            },
        }

    def _sanitize_debug_value(self, value):
        """Return a JSON-safe lightweight snapshot value."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [self._sanitize_debug_value(item) for item in value[:12]]
        if isinstance(value, dict):
            return {str(key): self._sanitize_debug_value(val) for key, val in list(value.items())[:20]}
        return str(value)

    def _classify_publish_likelihood(self, tweet_url: str, confidence_payload: dict) -> str:
        """Classify how likely a pending post was actually published."""
        if tweet_url:
            return "published-confirmed"

        signals = confidence_payload.get("signals", {}) if isinstance(confidence_payload, dict) else {}
        compose_candidates = int(signals.get("compose_candidates", 0) or 0)
        compose_matching_candidates = int(signals.get("compose_matching_candidates", 0) or 0)
        timeline_items = int(signals.get("timeline_items", 0) or 0)

        if compose_matching_candidates > 0:
            return "published-likely"
        if compose_candidates >= 3 and timeline_items >= 3:
            return "published-likely"
        if compose_candidates > 0 or timeline_items > 0:
            return "published-ambiguous"
        return "publish-signal-weak"

    def _build_publish_evidence_snapshot(self, tweet_url: str, confidence_payload: dict) -> dict:
        """Build a lightweight evidence snapshot for post-time publish diagnostics."""
        debug = self.last_permalink_debug if isinstance(self.last_permalink_debug, dict) else {}
        return {
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "tweet_url": str(tweet_url or ""),
            "publish_likelihood": self._classify_publish_likelihood(tweet_url, confidence_payload),
            "confidence_score": int((confidence_payload or {}).get("score", 0) or 0),
            "confidence_level": str((confidence_payload or {}).get("level", "") or ""),
            "signals": self._sanitize_debug_value((confidence_payload or {}).get("signals", {})),
            "permalink_debug": self._sanitize_debug_value(debug),
        }

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

    def _configured_account_handle(self) -> str:
        """
        Returns account handle from cache settings when available.

        Returns:
            handle (str): Username without @, or empty string
        """
        account = self._get_account_settings()
        for key in ("x_username", "username", "handle"):
            value = str(account.get(key, "")).strip()
            if not value:
                continue
            value = value.lstrip("@").strip()
            if re.match(r"^[A-Za-z0-9_]{1,15}$", value):
                return value
        return ""

    def _extract_source_label_from_url(self, url: str) -> str:
        """
        Converts a URL into a short human-readable source label.

        Args:
            url (str): URL string

        Returns:
            label (str): Source label
        """
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return ""

        host = host.replace("www.", "")
        source_map = {
            "jamesclear.com": "James Clear",
            "todoist.com": "Todoist",
            "atlassian.com": "Atlassian",
            "zapier.com": "Zapier",
            "calnewport.com": "Cal Newport",
            "nationalgeographic.com": "National Geographic",
            "scientificamerican.com": "Scientific American",
            "britannica.com": "Britannica",
            "smithsonianmag.com": "Smithsonian",
            "livescience.com": "Live Science",
        }

        for domain, label in source_map.items():
            if host.endswith(domain):
                return label

        base = host.split(".")[0]
        if not base:
            return ""
        return base.replace("-", " ").title()

    def _trusted_source_labels(self) -> list[str]:
        """
        Returns source labels derived from trusted links.

        Returns:
            labels (list[str]): Distinct source labels
        """
        labels: list[str] = []
        for url in self._trusted_link_pool():
            label = self._extract_source_label_from_url(url)
            if label and label not in labels:
                labels.append(label)
        return labels

    def _extract_citation_source(self, text: str) -> str:
        """
        Extracts a citation source from '(source: ...)' style suffix.

        Args:
            text (str): Tweet text

        Returns:
            source (str): Parsed source label or empty string
        """
        match = re.search(r"\(\s*source\s*:\s*([^\)]+)\)", text or "", flags=re.IGNORECASE)
        if not match:
            return ""
        return match.group(1).strip()

    def _target_citation_ratio(self) -> float:
        """
        Returns desired fraction of posts containing short source citations.

        Account-level override: citation_post_ratio in .mp/twitter.json

        Returns:
            ratio (float): Clamped to [0.0, 0.6]
        """
        account = self._get_account_settings()
        configured = account.get("citation_post_ratio")
        if isinstance(configured, (int, float)):
            return max(0.0, min(0.6, float(configured)))

        topic = (self.topic or "").lower()
        if any(key in topic for key in ("fact", "trivia", "weird", "wierd", "odd")):
            return 0.25
        return 0.12

    def _recent_citation_sources(self, posts: List[dict], limit: int = 12) -> list[str]:
        """
        Collects citation sources from recent posts.

        Args:
            posts (List[dict]): Existing cached posts
            limit (int): Number of recent posts to inspect

        Returns:
            sources (list[str]): Distinct recent source labels
        """
        sources: list[str] = []
        for prev in posts[-limit:]:
            source = str(prev.get("citation_source", "")).strip()
            if not source:
                source = self._extract_citation_source(prev.get("content", ""))
            if source and source not in sources:
                sources.append(source)
        return sources

    def _should_try_source_citation(self, posts: List[dict]) -> bool:
        """
        Decides whether to request a short '(source: ...)' suffix in this post.

        Returns:
            use_citation_mode (bool): Whether to include source citation guidance
        """
        source_labels = self._trusted_source_labels()
        if not source_labels:
            return False

        recent = posts[-12:]
        if not recent:
            return random.random() < self._target_citation_ratio()

        recent_with_citation = 0
        for prev in recent:
            if self._extract_citation_source(prev.get("content", "")):
                recent_with_citation += 1

        current_ratio = recent_with_citation / len(recent)
        target_ratio = self._target_citation_ratio()

        if current_ratio < target_ratio:
            return random.random() < 0.70
        return random.random() < 0.10

    def _angle_memory_path(self) -> str:
        """
        Returns path to cross-run angle memory file.

        Returns:
            path (str): Absolute path to angle memory JSON
        """
        return os.path.join(ROOT_DIR, ".mp", "twitter_angle_memory.json")

    def _load_angle_memory(self) -> dict:
        """
        Loads angle memory file.

        Returns:
            memory (dict): Parsed memory object
        """
        path = self._angle_memory_path()
        if not os.path.exists(path):
            return {"accounts": {}}

        try:
            with open(path, "r") as file:
                parsed = json.load(file)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"accounts": {}}

    def _save_angle_memory(self, memory: dict) -> None:
        """
        Saves angle memory atomically.

        Args:
            memory (dict): Memory object to persist
        """
        path = self._angle_memory_path()
        tmp_path = path + ".tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, "w") as file:
            json.dump(memory, file, indent=4)
        os.replace(tmp_path, path)

    def _extract_angle_signature(self, text: str, category: str = "") -> str:
        """
        Builds a compact angle signature from post opening + category.

        Args:
            text (str): Tweet text
            category (str): Optional category label

        Returns:
            signature (str): Stable signature for repetition checks
        """
        normalized = self._normalize_tweet(text)
        if not normalized:
            return ""
        lead = " ".join(normalized.split()[:6])
        if category and category != "general":
            return f"{category}:{lead}"
        return lead

    def _recent_angle_signatures(self, posts: List[dict], days: int = 45) -> list[str]:
        """
        Returns recent angle signatures from cache + memory file.

        Args:
            posts (List[dict]): Existing cached posts
            days (int): Lookback window in days

        Returns:
            signatures (list[str]): Distinct angle signatures
        """
        signatures: list[str] = []

        for prev in posts[-20:]:
            signature = str(prev.get("angle_signature", "")).strip().lower()
            if not signature:
                category = str(prev.get("category", "")).strip().lower()
                signature = self._extract_angle_signature(prev.get("content", ""), category)
            if signature and signature not in signatures:
                signatures.append(signature)

        cutoff = datetime.now().timestamp() - (days * 86400)
        memory = self._load_angle_memory()
        entries = memory.get("accounts", {}).get(self.account_uuid, [])
        for entry in entries:
            try:
                date_raw = entry.get("date", "")
                date_ts = datetime.fromisoformat(date_raw).timestamp()
                if date_ts < cutoff:
                    continue
            except Exception:
                continue
            signature = str(entry.get("angle", "")).strip().lower()
            if signature and signature not in signatures:
                signatures.append(signature)

        return signatures

    def _record_angle_signature(self, signature: str, category: str) -> None:
        """
        Persists angle signature to memory for long-horizon anti-repeat checks.

        Args:
            signature (str): Angle signature
            category (str): Category label
        """
        if not signature:
            return

        memory = self._load_angle_memory()
        accounts = memory.setdefault("accounts", {})
        history = accounts.setdefault(self.account_uuid, [])

        now_iso = datetime.now().isoformat(timespec="seconds")
        history.append(
            {
                "date": now_iso,
                "month": datetime.now().strftime("%Y-%m"),
                "angle": signature,
                "category": category,
            }
        )

        # Keep recent window only, cap growth.
        cutoff = datetime.now().timestamp() - (120 * 86400)
        pruned = []
        for entry in history[-200:]:
            try:
                if datetime.fromisoformat(entry.get("date", "")).timestamp() >= cutoff:
                    pruned.append(entry)
            except Exception:
                continue
        accounts[self.account_uuid] = pruned

        self._save_angle_memory(memory)

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
        return bool(get_nanobanana2_api_key()) and not self._is_media_generation_temporarily_disabled()

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
                        self._clear_media_generation_failure()
                        return path
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            if 400 <= status_code < 500:
                self._record_media_generation_failure(
                    f"HTTP {status_code} from media generation endpoint",
                    hours=12,
                )
            if get_verbose():
                warning(f"Media image generation failed: {exc}")
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
            "do you know",
            "have you",
            "want to",
            "stop ",
            "try ",
            "boost ",
            "use ",
            "the fastest",
            "the easiest",
            "the real",
            "the secret",
            "most people",
            "most of us",
            "your ",
            "why ",
            "what if",
            "what most",
            "here's how",
            "here's why",
            "here's the",
            "ever wonder",
            "ever tried",
            "ever notice",
            "ever feel",
            "if you",
            "when you",
            "how to",
            "how many",
            "imagine ",
            "turns out",
            "forget ",
            "not all",
            "not every",
            "one of the",
            "one thing",
            "the one ",
            "this is",
            "you don't",
            "you might",
            "you can ",
            "you're ",
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

    def _verified_winning_categories(self, posts: List[dict], limit: int = 20) -> list[str]:
        """Return recent categories that have produced verified posts."""
        winners: list[str] = []
        for prev in reversed(posts[-limit:]):
            if not bool(prev.get("post_verified", False)):
                continue
            category = str(prev.get("category", "")).strip().lower()
            if not category:
                category = self._infer_category_from_text(prev.get("content", ""))
            if category and category not in winners:
                winners.append(category)
        return winners

    def _high_pending_categories(self, posts: List[dict], limit: int = 24) -> set[str]:
        """Return categories with repeated pending outcomes and no verified win in recent history."""
        pending_counts: dict[str, int] = {}
        verified_categories: set[str] = set()
        for prev in posts[-limit:]:
            category = str(prev.get("category", "")).strip().lower()
            if not category:
                category = self._infer_category_from_text(prev.get("content", ""))
            if not category:
                continue
            if bool(prev.get("post_verified", False)):
                verified_categories.add(category)
                continue
            if str(prev.get("verification_state", "")).strip().lower() == "pending":
                pending_counts[category] = pending_counts.get(category, 0) + 1

        return {category for category, count in pending_counts.items() if count >= 2 and category not in verified_categories}

    def _build_prompt(
        self,
        existing_posts: list[dict],
        use_link_mode: bool = False,
        use_source_citation: bool = False,
    ) -> str:
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
        winning_categories = self._verified_winning_categories(existing_posts, limit=20)
        pending_heavy_categories = self._high_pending_categories(existing_posts, limit=24)
        category_block = ""
        if category_pool:
            ordered_categories = [category for category in winning_categories if category in category_pool]
            ordered_categories.extend(
                category
                for category in category_pool
                if category not in ordered_categories and category not in pending_heavy_categories
            )
            ordered_categories.extend(
                category
                for category in category_pool
                if category not in ordered_categories
            )
            category_list = ", ".join(ordered_categories)
            recent_list = ", ".join(recent_categories[:4]) if recent_categories else "none"
            winner_list = ", ".join(winning_categories[:3]) if winning_categories else "none"
            pending_list = ", ".join(sorted(pending_heavy_categories)[:4]) if pending_heavy_categories else "none"
            category_block = (
                "\nCategory rotation:\n"
                f"  - Allowed categories (ordered by proven performance): {category_list}\n"
                f"  - Verified-winning categories to favor: {winner_list}\n"
                f"  - High-pending categories to use sparingly: {pending_list}\n"
                f"  - Recently used categories: {recent_list}\n"
                "  - Favor verified-winning categories when they are not too recent.\n"
                "  - Pick a different category than recent posts when possible.\n"
            )

        recent_angles = self._recent_angle_signatures(existing_posts, days=45)
        angle_block = ""
        if recent_angles:
            joined_angles = "\n".join(f"  - {sig}" for sig in recent_angles[:8])
            angle_block = (
                "\nLong-horizon angle memory (avoid reusing these recent angles):\n"
                f"{joined_angles}\n"
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

        citation_block = ""
        trusted_sources = self._trusted_source_labels()
        if use_source_citation and trusted_sources:
            recent_sources = self._recent_citation_sources(existing_posts, limit=12)
            source_pool = [src for src in trusted_sources if src not in recent_sources] or trusted_sources
            joined_sources = "\n".join(f"  - {src}" for src in source_pool[:6])
            citation_block = (
                "\nSource citation mode is ON:\n"
                "  - Optionally append a short source note at the end in this exact style: (source: Name).\n"
                "  - Use ONLY one approved source name below.\n"
                "  - Keep citation brief and natural; no extra claims.\n"
                f"{joined_sources}\n"
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
            f"{avoid_block}{opening_block}{hashtag_block}{cta_block}{category_block}{angle_block}{link_block}{citation_block}"
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

        try:
            completion = self._generate_text(prompt)
        except Exception:
            return ""
        if not completion:
            return ""
        return self._clean_tweet(completion)

    def generate_post(
        self,
        force_link_mode: Optional[bool] = None,
        force_source_citation: Optional[bool] = None,
    ) -> str:
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
        recent_categories = self._recent_categories(existing_posts, limit=3)
        # Only treat a category as "blocked" if it appears at least twice in
        # the last 3 posts — one prior use is fine; back-to-back is not.
        from collections import Counter as _Counter
        _raw_cats = [
            (
                str(p.get("category", "")).strip().lower()
                or self._infer_category_from_text(p.get("content", ""))
            )
            for p in existing_posts[-3:]
        ]
        _cat_counts = _Counter(_raw_cats)
        recent_category_set = {cat for cat, cnt in _cat_counts.items() if cnt >= 2 and cat}
        category_pool = self._topic_category_pool()
        trusted_links = self._trusted_link_pool()
        trusted_sources = self._trusted_source_labels()
        recent_urls = self._recent_urls(existing_posts, limit=12)
        recent_citation_sources = self._recent_citation_sources(existing_posts, limit=12)
        recent_angles = set(self._recent_angle_signatures(existing_posts, days=45))
        use_link_mode = force_link_mode if force_link_mode is not None else self._should_try_link_post(existing_posts)
        use_source_citation = (
            force_source_citation
            if force_source_citation is not None
            else self._should_try_source_citation(existing_posts)
        )
        rejection_reasons: list[str] = []
        best_soft_candidate = ""
        best_soft_reasons: list[str] = []

        def _consider_soft_candidate(candidate: str, reasons: list[str]) -> None:
            nonlocal best_soft_candidate, best_soft_reasons
            if not candidate or not reasons:
                return
            if not best_soft_candidate or len(reasons) < len(best_soft_reasons):
                best_soft_candidate = candidate
                best_soft_reasons = list(reasons)

        for attempt in range(5):
            try:
                prompt = self._build_prompt(
                    existing_posts,
                    use_link_mode=use_link_mode,
                    use_source_citation=use_source_citation,
                )
                completion = self._generate_text(prompt)
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
            soft_rejections: list[str] = []

            if not self._has_strong_hook(cleaned):
                soft_rejections.append("weak opening hook")

            if self._is_too_similar_to_recent(cleaned, existing_posts):
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: too similar to recent posts"
                )
                continue

            opening_signature = self._opening_signature(cleaned)
            if opening_signature and opening_signature in recent_openings:
                soft_rejections.append("opening too similar to recent hooks")

            candidate_hashtags = self._extract_hashtags(cleaned)
            if candidate_hashtags and len(candidate_hashtags & recent_hashtags) >= 2:
                soft_rejections.append("reusing too many recent hashtags")

            cta_signature = self._cta_signature(cleaned)
            if cta_signature and cta_signature in recent_cta_signatures:
                soft_rejections.append("CTA ending too repetitive")

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

            citation_source = self._extract_citation_source(cleaned)
            if citation_source:
                if trusted_sources and citation_source not in trusted_sources:
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: unapproved citation source '{citation_source}'"
                    )
                    continue

                if (
                    citation_source in recent_citation_sources
                    and len(set(trusted_sources) - set(recent_citation_sources)) > 0
                ):
                    rejection_reasons.append(
                        f"Attempt {attempt + 1}: citation source reused too soon"
                    )
                    continue
            elif use_source_citation and trusted_sources and attempt < 3:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: citation mode expected '(source: ...)'."
                )
                continue

            if category_pool:
                category = self._infer_category_from_text(cleaned)
                can_rotate = len(set(category_pool) - recent_category_set) > 0
                if attempt < 4 and can_rotate and category in recent_category_set:
                    soft_rejections.append(f"category '{category}' repeated too soon")

            angle_signature = self._extract_angle_signature(
                cleaned,
                self._infer_category_from_text(cleaned),
            )
            if angle_signature and angle_signature in recent_angles and attempt < 4:
                soft_rejections.append("angle reused from monthly memory")

            if soft_rejections:
                rejection_reasons.append(
                    f"Attempt {attempt + 1}: {'; '.join(soft_rejections)}"
                )
                _consider_soft_candidate(cleaned, soft_rejections)
                continue

            if get_verbose():
                info(f"Tweet length: {len(cleaned)} chars")

            return cleaned

        if rejection_reasons and get_verbose():
            for reason in rejection_reasons:
                warning(reason)

        if best_soft_candidate:
            warning(
                "Using best available post after quality filters rejected stronger variants. "
                f"Soft issues: {', '.join(best_soft_reasons)}"
            )
            if get_verbose():
                info(f"Tweet length: {len(best_soft_candidate)} chars")
            return best_soft_candidate

        error("Failed to generate a strong, non-duplicate post. Please try again.")
        sys.exit(1)
