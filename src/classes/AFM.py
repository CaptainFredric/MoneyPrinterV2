import os
import platform
import shutil
import tempfile
from urllib.parse import urlparse
from typing import Any

from status import *
from config import *
from constants import *
from llm_provider import generate_text
from .Twitter import Twitter
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager


class AffiliateMarketing:
    """
    This class will be used to handle all the affiliate marketing related operations.
    """

    def __init__(
        self,
        affiliate_link: str,
        fp_profile_path: str,
        twitter_account_uuid: str,
        account_nickname: str,
        topic: str,
    ) -> None:
        """
        Initializes the Affiliate Marketing class.

        Args:
            affiliate_link (str): The affiliate link
            fp_profile_path (str): The path to the Firefox profile
            twitter_account_uuid (str): The Twitter account UUID
            account_nickname (str): The account nickname
            topic (str): The topic of the product

        Returns:
            None
        """
        self._fp_profile_path: str = fp_profile_path

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

        # Remove stale lock files before launch
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

        # Initialize the browser — with fallback clone on WebDriverException
        try:
            self.browser: webdriver.Firefox = webdriver.Firefox(
                service=self.service, options=self.options
            )
        except WebDriverException:
            fallback_profile_path = tempfile.mkdtemp(prefix="mpv2_afm_ff_profile_")
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

        # Set the affiliate link
        self.affiliate_link: str = affiliate_link

        parsed_link = urlparse(self.affiliate_link)
        if parsed_link.scheme not in ["http", "https"] or not parsed_link.netloc:
            raise ValueError(
                f"Affiliate link is invalid. Expected a full URL, got: {self.affiliate_link}"
            )

        # Set the Twitter account UUID
        self.account_uuid: str = twitter_account_uuid

        # Set the Twitter account nickname
        self.account_nickname: str = account_nickname

        # Set the Twitter topic
        self.topic: str = topic

        # Scrape the product information
        self.scrape_product_information()

    def scrape_product_information(self) -> None:
        """
        This method will be used to scrape the product
        information from the affiliate link.
        """
        # Open the affiliate link
        self.browser.get(self.affiliate_link)

        # Wait for page load — try title element; fall back to page title
        product_title: str = ""
        try:
            title_el = self.wait.until(
                EC.presence_of_element_located((By.ID, AMAZON_PRODUCT_TITLE_ID))
            )
            product_title = title_el.text.strip()
        except Exception:
            # Generic fallback: use <title> tag text
            try:
                product_title = self.browser.title.strip()
            except Exception:
                product_title = "Unknown Product"

        # Get the features of the product — non-fatal if missing
        features: Any = []
        try:
            features = self.browser.find_elements(By.ID, AMAZON_FEATURE_BULLETS_ID)
            if not features:
                # Try alternate CSS selector used on some locales
                features = self.browser.find_elements(
                    By.CSS_SELECTOR, "#feature-bullets li span.a-list-item"
                )
        except Exception:
            pass

        if get_verbose():
            info(f"Product Title: {product_title}")

        if get_verbose():
            info(f"Features: {features}")

        if not product_title:
            raise RuntimeError(
                "Could not scrape product title from the affiliate link. "
                "Check the URL and ensure the browser is not blocked by CAPTCHA."
            )

        # Set the product title
        self.product_title: str = product_title

        # Set the features
        self.features: Any = features

    def generate_response(self, prompt: str) -> str:
        """
        This method will be used to generate the response for the user.

        Args:
            prompt (str): The prompt for the user.

        Returns:
            response (str): The response for the user.
        """
        return generate_text(prompt)

    def generate_pitch(self) -> str:
        """
        This method will be used to generate a pitch for the product.

        Returns:
            pitch (str): The pitch for the product.
        """
        # Generate the response
        pitch: str = (
            self.generate_response(
                f'I want to promote this product on my website. Generate a brief pitch about this product, return nothing else except the pitch. Information:\nTitle: "{self.product_title}"\nFeatures: "{str(self.features)}"'
            )
            + "\nYou can buy the product here: "
            + self.affiliate_link
        )

        self.pitch: str = pitch

        # Return the response
        return pitch

    def share_pitch(self, where: str) -> None:
        """
        This method will be used to share the pitch on the specified platform.

        Args:
            where (str): The platform where the pitch will be shared.
        """
        if where == "twitter":
            # Initialize the Twitter class
            twitter: Twitter = Twitter(
                self.account_uuid,
                self.account_nickname,
                self._fp_profile_path,
                self.topic,
                "",
            )

            # Share the pitch
            twitter.post(self.pitch)

    def quit(self) -> None:
        """
        This method will be used to quit the browser.
        """
        # Quit the browser
        self.browser.quit()
