"""Tests for browser.json sanitizing."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import AUTH_MARKER, sanitize_browser_json


def _write(tmp_path, data):
    p = tmp_path / "browser.json"
    p.write_text(json.dumps(data))
    return p


def test_strips_replay_hostile_headers(tmp_path):
    p = _write(tmp_path, {
        "POST /youtubei/v1/browse?prettyPrint=false HTTP/3": "",
        "cookie": "SID=x; SAPISID=y",
        "user-agent": "Mozilla/5.0",
        "content-encoding": "gzip",
        "connection": "keep-alive",
        "authorization": "SAPISIDHASH 123_deadbeef",
        "host": "music.youtube.com",
    })
    removed = sanitize_browser_json(str(p))

    assert set(removed) == {
        "POST /youtubei/v1/browse?prettyPrint=false HTTP/3",
        "content-encoding", "connection", "host",
    }
    remaining = json.loads(p.read_text())
    assert remaining == {
        "cookie": "SID=x; SAPISID=y",
        "user-agent": "Mozilla/5.0",
        "authorization": "SAPISIDHASH 123_deadbeef",
    }


def test_bearer_authorization_untouched(tmp_path):
    p = _write(tmp_path, {"cookie": "SID=x", "authorization": "Bearer abc"})
    assert sanitize_browser_json(str(p)) == []
    assert json.loads(p.read_text())["authorization"] == "Bearer abc"


def test_adds_auth_marker_when_missing(tmp_path):
    p = _write(tmp_path, {"cookie": "SID=x"})
    assert sanitize_browser_json(str(p)) == []
    assert json.loads(p.read_text())["authorization"] == AUTH_MARKER
    # idempotent: existing marker is not duplicated or replaced
    assert sanitize_browser_json(str(p)) == []
    assert json.loads(p.read_text())["authorization"] == AUTH_MARKER


def test_keeps_replayable_headers(tmp_path):
    keep = {
        "cookie": "SID=x",
        "user-agent": "Mozilla/5.0",
        "origin": "https://music.youtube.com",
        "referer": "https://music.youtube.com/",
        "accept-language": "en-US,en;q=0.5",
        "x-goog-visitor-id": "abc",
        "x-youtube-client-name": "1",
        "x-youtube-client-version": "2.20260101.00.00",
    }
    p = _write(tmp_path, keep)
    assert sanitize_browser_json(str(p)) == []
    remaining = json.loads(p.read_text())
    assert remaining == {**keep, "authorization": AUTH_MARKER}


def test_second_call_is_noop(tmp_path):
    p = _write(tmp_path, {
        "cookie": "SID=x",
        "GET /anything HTTP/2": "",
        "content-encoding": "br",
    })
    assert len(sanitize_browser_json(str(p))) == 2
    assert sanitize_browser_json(str(p)) == []


def test_non_browser_json_untouched(tmp_path):
    oauth = {"refresh_token": "r", "token_type": "Bearer", "client_id": "c"}
    p = _write(tmp_path, oauth)
    assert sanitize_browser_json(str(p)) == []
    assert json.loads(p.read_text()) == oauth


def test_case_insensitive_matching(tmp_path):
    p = _write(tmp_path, {
        "Cookie": "SID=x",
        "Content-Encoding": "gzip",
        "post /youtubei/v1/browse HTTP/3": "",
    })
    removed = sanitize_browser_json(str(p))
    assert set(removed) == {"Content-Encoding", "post /youtubei/v1/browse HTTP/3"}
