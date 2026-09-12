import json
import logging
import re
import sys
from pathlib import Path

from ytmusicapi import YTMusic, setup

logger = logging.getLogger(__name__)

REQUEST_LINE_RE = re.compile(r"^\S+\s+\S+\s+HTTP/\d(\.\d+)?$", re.IGNORECASE)

# Headers the browser sends per-connection or per-body that break replayed requests:
# content-encoding claims a compressed body (we send plain JSON), the HTTP request
# line has spaces in the name (invalid header).
# "authorization" is NOT in here: ytmusicapi classifies a header file as browser
# auth only when it contains a SAPISIDHASH authorization value (auth_parse.py
# determine_auth_type), even though it recomputes the hash per request anyway.
UNSAFE_HEADERS = {
    "content-encoding",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "upgrade",
    "te",
    "trailer",
    "proxy-connection",
    "host",
}

# Marker ytmusicapi needs to detect browser auth; value is overwritten per request.
AUTH_MARKER = "SAPISIDHASH 0_placeholder"


def _ensure_auth_marker(data: dict) -> bool:
    if any(k.lower() == "authorization" for k in data):
        return False  # leave existing values alone (a Bearer value means custom OAuth, not browser)
    data["authorization"] = AUTH_MARKER
    return True


def sanitize_browser_json(path: str) -> list[str]:
    """Drop replay-hostile headers from a browser.json in place. Return removed keys."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not any(k.lower() == "cookie" for k in data):
        return []  # not a browser-headers file (e.g. an OAuth token json)
    removed = [k for k in data if k.lower() in UNSAFE_HEADERS or REQUEST_LINE_RE.match(k)]
    marker_added = _ensure_auth_marker(data)
    if removed or marker_added:
        for key in removed:
            del data[key]
        Path(path).write_text(json.dumps(data, indent=4))
        logger.debug("Sanitized browser.json: removed %s, auth marker added: %s", removed, marker_added)
    return removed


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
    path = input("Path to your headers file [./headers.txt]: ").strip().strip("'\"") or "./headers.txt"

    if not Path(path).exists():
        print(f"File not found: {path}")
        return False

    headers_raw = Path(path).read_text()
    if not headers_raw.strip():
        print("File is empty.")
        return False

    try:
        setup(filepath=auth_path, headers_raw=headers_raw)
    except Exception as e:
        logger.debug("Browser auth setup detail: %s", e)
        print(f"\nSetup failed ({type(e).__name__}). Check that you copied the full request headers.")
        return False

    removed = sanitize_browser_json(auth_path)
    if removed:
        print(f"Stripped headers that cannot be replayed: {', '.join(removed)}")
    print(f"\nCredentials saved to {auth_path}")
    return True


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
    if Path(auth_path).exists():
        try:
            sanitize_browser_json(auth_path)
        except Exception:
            pass  # not a json file; YTMusic() below reports the real problem

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
