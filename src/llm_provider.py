import os
import time
import ollama

from config import get_ollama_base_url, get_ollama_model

_selected_model: str | None = None

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client() -> ollama.Client:
    return ollama.Client(host=get_ollama_base_url())


def _gemini_api_key() -> str:
    """Return the Gemini API key from config or environment variable."""
    from config import get_nanobanana2_api_key
    return get_nanobanana2_api_key() or os.environ.get("GEMINI_API_KEY", "")


def _gemini_model() -> str:
    """Return the Gemini text model name to use for generation."""
    return os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.0-flash")


def _generate_via_gemini(prompt: str, retries: int = 3) -> str:
    """Generate text using the Google Gemini API as a fallback."""
    api_key = _gemini_api_key()
    if not api_key:
        raise RuntimeError(
            "Gemini fallback: no API key found. "
            "Set 'nanobanana2_api_key' in config.json or the GEMINI_API_KEY env var."
        )

    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-genai package not installed. Run: pip install google-genai"
        ) from exc

    client = genai.Client(api_key=api_key)
    model = _gemini_model()

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.9,
                    max_output_tokens=300,
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text
            raise ValueError("Gemini returned an empty response.")
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            continue

    raise RuntimeError(
        f"Gemini generate_text failed after {retries} attempts. Last error: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_models() -> list[str]:
    """
    Lists all models available on the local Ollama server.

    Returns:
        models (list[str]): Sorted list of model names.
    """
    response = _client().list()
    return sorted(m.model for m in response.models)


def select_model(model: str) -> None:
    """
    Sets the model to use for all subsequent generate_text calls.

    Args:
        model (str): An Ollama model name (must be already pulled).
    """
    global _selected_model
    _selected_model = model


def get_active_model() -> str | None:
    """
    Returns the currently selected model, or None if none has been selected.
    """
    return _selected_model


def generate_text(prompt: str, model_name: str = None, retries: int = 3) -> str:
    """
    Generates text.

    Tries the local Ollama server first.  If Ollama is unavailable or returns
    an error, falls back automatically to Gemini when an API key is available.

    Args:
        prompt (str): User prompt
        model_name (str): Optional Ollama model name override
        retries (int): Maximum number of attempts per provider (default 3)

    Returns:
        response (str): Generated text

    Raises:
        RuntimeError: If all providers fail.
    """
    model = model_name or _selected_model or get_ollama_model()
    ollama_error: Exception | None = None

    if model:
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                response = _client().chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = response["message"]["content"].strip()
                if result:
                    return result
                raise ValueError("Ollama returned an empty response.")
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                continue
        ollama_error = last_exc

    # Ollama unavailable or no model configured — try Gemini
    if _gemini_api_key():
        try:
            return _generate_via_gemini(prompt, retries=retries)
        except Exception as gemini_exc:
            raise RuntimeError(
                f"All LLM providers failed. "
                f"Ollama: {ollama_error}. Gemini: {gemini_exc}"
            ) from gemini_exc

    raise RuntimeError(
        f"Ollama generate_text failed after {retries} attempts. Last error: {ollama_error}. "
        "No Gemini API key is configured as fallback."
    )
