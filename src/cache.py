import os
import json
import tempfile

from typing import List
from config import ROOT_DIR


def _atomic_write(path: str, data: dict) -> None:
    """
    Writes JSON to a temp file in the same directory, then atomically
    replaces the target file.  Prevents half-written / corrupt cache files.
    """
    dir_name = os.path.dirname(path)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

def get_cache_path() -> str:
    """
    Gets the path to the cache file.

    Returns:
        path (str): The path to the cache folder
    """
    return os.path.join(ROOT_DIR, '.mp')

def get_afm_cache_path() -> str:
    """
    Gets the path to the Affiliate Marketing cache file.

    Returns:
        path (str): The path to the AFM cache folder
    """
    return os.path.join(get_cache_path(), 'afm.json')

def get_twitter_cache_path() -> str:
    """
    Gets the path to the Twitter cache file.

    Returns:
        path (str): The path to the Twitter cache folder
    """
    return os.path.join(get_cache_path(), 'twitter.json')

def get_youtube_cache_path() -> str:
    """
    Gets the path to the YouTube cache file.

    Returns:
        path (str): The path to the YouTube cache folder
    """
    return os.path.join(get_cache_path(), 'youtube.json')

def get_provider_cache_path(provider: str) -> str:
    """
    Gets the cache path for a supported account provider.

    Args:
        provider (str): The provider name ("twitter" or "youtube")

    Returns:
        path (str): The provider-specific cache path

    Raises:
        ValueError: If the provider is unsupported
    """
    if provider == "twitter":
        return get_twitter_cache_path()
    if provider == "youtube":
        return get_youtube_cache_path()

    raise ValueError(f"Unsupported provider '{provider}'. Expected 'twitter' or 'youtube'.")

def get_accounts(provider: str) -> List[dict]:
    """
    Gets the accounts from the cache.

    Args:
        provider (str): The provider to get the accounts for

    Returns:
        account (List[dict]): The accounts
    """
    cache_path = get_provider_cache_path(provider)

    if not os.path.exists(cache_path):
        _atomic_write(cache_path, {"accounts": []})

    try:
        with open(cache_path, 'r') as file:
            parsed = json.load(file)
    except (json.JSONDecodeError, OSError):
        # Corrupt or unreadable cache — back it up and start fresh
        backup_path = cache_path + ".bak"
        try:
            os.replace(cache_path, backup_path)
        except OSError:
            pass
        _atomic_write(cache_path, {"accounts": []})
        return []

    if not isinstance(parsed, dict) or 'accounts' not in parsed:
        return []

    return parsed['accounts']

def add_account(provider: str, account: dict) -> None:
    """
    Adds an account to the cache.

    Args:
        provider (str): The provider to add the account to ("twitter" or "youtube")
        account (dict): The account to add

    Returns:
        None
    """
    cache_path = get_provider_cache_path(provider)

    # Get the current accounts
    accounts = get_accounts(provider)

    # Add the new account
    accounts.append(account)

    # Write the new accounts to the cache
    _atomic_write(cache_path, {"accounts": accounts})

def remove_account(provider: str, account_id: str) -> None:
    """
    Removes an account from the cache.

    Args:
        provider (str): The provider to remove the account from ("twitter" or "youtube")
        account_id (str): The ID of the account to remove

    Returns:
        None
    """
    # Get the current accounts
    accounts = get_accounts(provider)

    # Remove the account
    accounts = [account for account in accounts if account['id'] != account_id]

    # Write the new accounts to the cache
    cache_path = get_provider_cache_path(provider)

    _atomic_write(cache_path, {"accounts": accounts})

def get_products() -> List[dict]:
    """
    Gets the products from the cache.

    Returns:
        products (List[dict]): The products
    """
    if not os.path.exists(get_afm_cache_path()):
        _atomic_write(get_afm_cache_path(), {"products": []})

    try:
        with open(get_afm_cache_path(), 'r') as file:
            parsed = json.load(file)
    except (json.JSONDecodeError, OSError):
        backup_path = get_afm_cache_path() + ".bak"
        try:
            os.replace(get_afm_cache_path(), backup_path)
        except OSError:
            pass
        _atomic_write(get_afm_cache_path(), {"products": []})
        return []

    return parsed.get("products", [])
    
def add_product(product: dict) -> None:
    """
    Adds a product to the cache.

    Args:
        product (dict): The product to add

    Returns:
        None
    """
    # Get the current products
    products = get_products()

    # Add the new product
    products.append(product)

    # Write the new products to the cache
    _atomic_write(get_afm_cache_path(), {"products": products})
    
def get_results_cache_path() -> str:
    """
    Gets the path to the results cache file.

    Returns:
        path (str): The path to the results cache folder
    """
    return os.path.join(get_cache_path(), 'scraper_results.csv')
