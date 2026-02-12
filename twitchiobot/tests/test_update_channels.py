import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import update_channels
from update_channels import ChannelDiscoveryError


def test_fetch_top_channels_requires_credentials(monkeypatch):
    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_OAUTH_TOKEN", raising=False)

    with pytest.raises(ChannelDiscoveryError):
        update_channels.fetch_top_channels(limit=10)


def test_fetch_top_channels_retries_timeout(monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client")
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "token")
    monkeypatch.setattr(update_channels.time, "sleep", lambda _: None)

    timeout_exc = requests.exceptions.Timeout("timed out")

    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "data": [{"user_login": "StreamerA"}],
        "pagination": {},
    }

    session = MagicMock()
    session.get.side_effect = [timeout_exc, response]

    channels = update_channels.fetch_top_channels(limit=1, max_retries=2, session=session)

    assert channels == ["streamera"]
    assert session.get.call_count == 2


def test_fetch_top_channels_raises_after_retries(monkeypatch):
    monkeypatch.setenv("TWITCH_CLIENT_ID", "client")
    monkeypatch.setenv("TWITCH_OAUTH_TOKEN", "token")
    monkeypatch.setattr(update_channels.time, "sleep", lambda _: None)

    session = MagicMock()
    session.get.side_effect = requests.exceptions.Timeout("timed out")

    with pytest.raises(ChannelDiscoveryError, match="Failed to fetch channels"):
        update_channels.fetch_top_channels(limit=1, max_retries=1, session=session)


def test_update_channel_list_writes_channels_file(monkeypatch, tmp_path):
    out_file = tmp_path / "channels.txt"
    monkeypatch.setattr(update_channels, "CHANNEL_FILE", str(out_file))
    monkeypatch.setattr(update_channels, "fetch_top_channels", lambda limit=5000: ["a", "b"])

    channels = update_channels.update_channel_list(limit=2)

    assert channels == ["a", "b"]
    assert out_file.read_text(encoding="utf-8").strip().splitlines() == ["a", "b"]


def test_update_channel_list_raises_on_empty(monkeypatch):
    monkeypatch.setattr(update_channels, "fetch_top_channels", lambda limit=5000: [])

    with pytest.raises(ChannelDiscoveryError, match="No channels returned"):
        update_channels.update_channel_list(limit=10)
