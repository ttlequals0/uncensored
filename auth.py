import logging
import sys
from pathlib import Path

from ytmusicapi import YTMusic, setup

logger = logging.getLogger(__name__)


def run_browser_setup(auth_path: str) -> bool:
    """Run ytmusicapi browser auth setup interactively.

    Saves pasted headers to a temp file, then passes to ytmusicapi's setup.
    """
    print("To authenticate, paste request headers from your browser.\n")
    print("  1. Open https://music.youtube.com and make sure you are logged in")
    print("  2. Open Developer Tools (F12), go to the Network tab")
    print("  3. Find any POST request to music.youtube.com")
    print("  4. In the Headers tab, select and copy all the request headers")
    print("  5. Save them to a text file, e.g. headers.txt")

    print()
    path = input("Path to your headers file [headers.txt]: ").strip().strip("'\"") or "headers.txt"

    if not Path(path).exists():
        print(f"File not found: {path}")
        return False

    headers_raw = Path(path).read_text()
    if not headers_raw.strip():
        print("File is empty.")
        return False

    try:
        setup(filepath=auth_path, headers_raw=headers_raw)
        print(f"\nCredentials saved to {auth_path}")
        return True
    except Exception as e:
        logger.debug("Browser auth setup detail: %s", e)
        print(f"\nSetup failed ({type(e).__name__}). Check that you copied the full request headers.")
        return False


def _prompt_and_setup(auth_path: str, message: str) -> None:
    """Print a message, prompt user for auth setup, run it if accepted.

    Returns True if setup succeeded, calls sys.exit(1) on decline or failure.
    """
    print(message)
    response = input("Would you like to run auth setup now? [y/N] ").strip().lower()
    if response != "y":
        sys.exit(1)
    if run_browser_setup(auth_path):
        print("\nAuth setup complete.\n")
        return True
    print("\nAuth setup failed. Please try again.")
    sys.exit(1)


def get_client(auth_path: str) -> YTMusic:
    """Create and return an authenticated YTMusic client.

    Raises SystemExit if auth is missing/invalid and user declines setup.
    """
    try:
        client = YTMusic(auth_path)
        logger.debug("Authenticated YTMusic client created from %s", auth_path)
        return client
    except Exception as e:
        is_missing = not Path(auth_path).exists()
        if is_missing:
            msg = (
                f"Auth credentials not found at: {auth_path}\n"
                f"Run 'uncensored --setup' or 'uv run uncensored.py --setup' to authenticate.\n"
            )
        else:
            logger.debug("Auth error detail: %s", e)
            msg = (
                f"Failed to authenticate with credentials at {auth_path} "
                f"({type(e).__name__}). Your headers may be expired.\n"
            )

        _prompt_and_setup(auth_path, msg)
        return YTMusic(auth_path)
