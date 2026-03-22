import os
import sys
import json
import threading
import srt_equalizer

from termcolor import colored

ROOT_DIR = os.path.dirname(sys.path[0])

# ---------------------------------------------------------------------------
# Config cache — reads config.json once per process, reloads only if the file
# has been modified since the last read.  Prevents hundreds of redundant
# open()/json.load() calls during a single session.
# ---------------------------------------------------------------------------
_config_lock = threading.Lock()
_config_cache: dict | None = None
_config_mtime: float = 0.0
_CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")


def _load_config() -> dict:
    """
    Returns the parsed config.json, using a cached copy when the file has not
    changed since the last read.  Thread-safe.
    """
    global _config_cache, _config_mtime
    with _config_lock:
        try:
            mtime = os.path.getmtime(_CONFIG_PATH)
        except OSError:
            mtime = 0.0

        if _config_cache is None or mtime != _config_mtime:
            try:
                with open(_CONFIG_PATH, "r") as f:
                    _config_cache = json.load(f)
                _config_mtime = mtime
            except (OSError, json.JSONDecodeError) as exc:
                if _config_cache is not None:
                    # Return stale cache rather than crash
                    pass
                else:
                    raise RuntimeError(
                        f"Could not read config.json: {exc}. "
                        "Make sure config.json exists (copy from config.example.json)."
                    ) from exc

        return _config_cache  # type: ignore[return-value]

def assert_folder_structure() -> None:
    """
    Make sure that the nessecary folder structure is present.

    Returns:
        None
    """
    # Create the .mp folder
    if not os.path.exists(os.path.join(ROOT_DIR, ".mp")):
        if get_verbose():
            print(colored(f"=> Creating .mp folder at {os.path.join(ROOT_DIR, '.mp')}", "green"))
        os.makedirs(os.path.join(ROOT_DIR, ".mp"))

def get_first_time_running() -> bool:
    """
    Checks if the program is running for the first time by checking if .mp folder exists.

    Returns:
        exists (bool): True if the program is running for the first time, False otherwise
    """
    return not os.path.exists(os.path.join(ROOT_DIR, ".mp"))

def get_email_credentials() -> dict:
    """
    Gets the email credentials from the config file.

    Returns:
        credentials (dict): The email credentials
    """
    cfg = _load_config()
    return cfg["email"]

def get_verbose() -> bool:
    """
    Gets the verbose flag from the config file.

    Returns:
        verbose (bool): The verbose flag
    """
    cfg = _load_config()
    return cfg["verbose"]

def get_firefox_profile_path() -> str:
    """
    Gets the path to the Firefox profile.

    Returns:
        path (str): The path to the Firefox profile
    """
    cfg = _load_config()
    return cfg["firefox_profile"]

def get_headless() -> bool:
    """
    Gets the headless flag.
    Environment variable MPV2_HEADLESS=1 always overrides config.json,
    so daemon.py and run_once.py can force headless without editing the file.

    Returns:
        headless (bool): The headless flag
    """
    if os.environ.get("MPV2_HEADLESS", "") == "1":
        return True
    cfg = _load_config()
    return cfg.get("headless", False)

def get_ollama_base_url() -> str:
    """
    Gets the Ollama base URL.

    Returns:
        url (str): The Ollama base URL
    """
    cfg = _load_config()
    return cfg.get("ollama_base_url", "http://127.0.0.1:11434")

def get_ollama_model() -> str:
    """
    Gets the Ollama model name from the config file.

    Returns:
        model (str): The Ollama model name, or empty string if not set.
    """
    cfg = _load_config()
    return cfg.get("ollama_model", "")

def get_twitter_language() -> str:
    """
    Gets the Twitter language from the config file.

    Returns:
        language (str): The Twitter language
    """
    cfg = _load_config()
    return cfg["twitter_language"]

def get_nanobanana2_api_base_url() -> str:
    """
    Gets the Nano Banana 2 (Gemini image) API base URL.

    Returns:
        url (str): API base URL
    """
    cfg = _load_config()
    return cfg.get(
            "nanobanana2_api_base_url",
            "https://generativelanguage.googleapis.com/v1beta",
        )

def get_nanobanana2_api_key() -> str:
    """
    Gets the Nano Banana 2 API key.

    Returns:
        key (str): API key
    """
    cfg = _load_config()
    configured = cfg.get("nanobanana2_api_key", "")
    return configured or os.environ.get("GEMINI_API_KEY", "")

def get_nanobanana2_model() -> str:
    """
    Gets the Nano Banana 2 model name.

    Returns:
        model (str): Model name
    """
    cfg = _load_config()
    return cfg.get("nanobanana2_model", "gemini-3.1-flash-image-preview")

def get_nanobanana2_aspect_ratio() -> str:
    """
    Gets the aspect ratio for Nano Banana 2 image generation.

    Returns:
        ratio (str): Aspect ratio
    """
    cfg = _load_config()
    return cfg.get("nanobanana2_aspect_ratio", "9:16")

def get_threads() -> int:
    """
    Gets the amount of threads to use for example when writing to a file with MoviePy.

    Returns:
        threads (int): Amount of threads
    """
    cfg = _load_config()
    return cfg["threads"]
    
def get_zip_url() -> str:
    """
    Gets the URL to the zip file containing the songs.

    Returns:
        url (str): The URL to the zip file
    """
    cfg = _load_config()
    return cfg["zip_url"]

def get_is_for_kids() -> bool:
    """
    Gets the is for kids flag from the config file.

    Returns:
        is_for_kids (bool): The is for kids flag
    """
    cfg = _load_config()
    return cfg["is_for_kids"]

def get_google_maps_scraper_zip_url() -> str:
    """
    Gets the URL to the zip file containing the Google Maps scraper.

    Returns:
        url (str): The URL to the zip file
    """
    cfg = _load_config()
    return cfg["google_maps_scraper"]

def get_google_maps_scraper_niche() -> str:
    """
    Gets the niche for the Google Maps scraper.

    Returns:
        niche (str): The niche
    """
    cfg = _load_config()
    return cfg["google_maps_scraper_niche"]

def get_scraper_timeout() -> int:
    """
    Gets the timeout for the scraper.

    Returns:
        timeout (int): The timeout
    """
    cfg = _load_config()
    return cfg["scraper_timeout"] or 300

def get_outreach_message_subject() -> str:
    """
    Gets the outreach message subject.

    Returns:
        subject (str): The outreach message subject
    """
    cfg = _load_config()
    return cfg["outreach_message_subject"]
    
def get_outreach_message_body_file() -> str:
    """
    Gets the outreach message body file.

    Returns:
        file (str): The outreach message body file
    """
    cfg = _load_config()
    return cfg["outreach_message_body_file"]

def get_tts_voice() -> str:
    """
    Gets the TTS voice from the config file.

    Returns:
        voice (str): The TTS voice
    """
    cfg = _load_config()
    return cfg.get("tts_voice", "Jasper")

def get_assemblyai_api_key() -> str:
    """
    Gets the AssemblyAI API key.

    Returns:
        key (str): The AssemblyAI API key
    """
    cfg = _load_config()
    return cfg["assembly_ai_api_key"]

def get_stt_provider() -> str:
    """
    Gets the configured STT provider.

    Returns:
        provider (str): The STT provider
    """
    cfg = _load_config()
    return cfg.get("stt_provider", "local_whisper")

def get_whisper_model() -> str:
    """
    Gets the local Whisper model name.

    Returns:
        model (str): Whisper model name
    """
    cfg = _load_config()
    return cfg.get("whisper_model", "base")

def get_whisper_device() -> str:
    """
    Gets the target device for Whisper inference.

    Returns:
        device (str): Whisper device
    """
    cfg = _load_config()
    return cfg.get("whisper_device", "auto")

def get_whisper_compute_type() -> str:
    """
    Gets the compute type for Whisper inference.

    Returns:
        compute_type (str): Whisper compute type
    """
    cfg = _load_config()
    return cfg.get("whisper_compute_type", "int8")
    
def equalize_subtitles(srt_path: str, max_chars: int = 10) -> None:
    """
    Equalizes the subtitles in a SRT file.

    Args:
        srt_path (str): The path to the SRT file
        max_chars (int): The maximum amount of characters in a subtitle

    Returns:
        None
    """
    srt_equalizer.equalize_srt_file(srt_path, srt_path, max_chars)
    
def get_font() -> str:
    """
    Gets the font from the config file.

    Returns:
        font (str): The font
    """
    cfg = _load_config()
    return cfg["font"]

def get_fonts_dir() -> str:
    """
    Gets the fonts directory.

    Returns:
        dir (str): The fonts directory
    """
    return os.path.join(ROOT_DIR, "fonts")

def get_imagemagick_path() -> str:
    """
    Gets the path to ImageMagick.

    Returns:
        path (str): The path to ImageMagick
    """
    cfg = _load_config()
    return cfg["imagemagick_path"]

def get_script_sentence_length() -> int:
    """
    Gets the forced script's sentence length.
    In case there is no sentence length in config, returns 4 when none

    Returns:
        length (int): Length of script's sentence
    """
    cfg = _load_config()
    config_json = cfg
    if (config_json.get("script_sentence_length") is not None):
        return config_json["script_sentence_length"]
    else:
        return 4
