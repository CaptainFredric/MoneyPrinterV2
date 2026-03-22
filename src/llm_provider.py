import time
import ollama

from config import get_ollama_base_url

_selected_model: str | None = None


def _client() -> ollama.Client:
    return ollama.Client(host=get_ollama_base_url())


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
    Generates text using the local Ollama server.
    Retries up to `retries` times with exponential back-off on transient failures.

    Args:
        prompt (str): User prompt
        model_name (str): Optional model name override
        retries (int): Maximum number of attempts (default 3)

    Returns:
        response (str): Generated text

    Raises:
        RuntimeError: If no model is selected, or all attempts fail.
    """
    model = model_name or _selected_model
    if not model:
        raise RuntimeError(
            "No Ollama model selected. Call select_model() first or pass model_name."
        )

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
            # Empty response — treat as soft failure and retry
            raise ValueError("Ollama returned an empty response.")
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = 2 ** attempt  # 1s, 2s, 4s …
                time.sleep(wait)
            continue

    raise RuntimeError(
        f"Ollama generate_text failed after {retries} attempts. Last error: {last_exc}"
    )
