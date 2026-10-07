"""Retry and backoff behaviour of the VOD chat download."""

import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from vod_collector import VODChatDownloader


class TestVODDownloadRetry:
    """Tests that VODChatDownloader retries on transient failures."""

    @pytest.fixture
    def downloader(self):
        return VODChatDownloader(cli_path="TwitchDownloaderCLI")

    def _failed_result(self, returncode=1, stderr="download error"):
        r = MagicMock()
        r.returncode = returncode
        r.stderr = stderr
        return r

    def _success_result(self):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        return r

    def test_succeeds_on_first_attempt(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        with patch("subprocess.run", return_value=self._success_result()) as mock_run, \
             patch("time.sleep"):
            result = downloader.download_vod_chat("123456", output)
        assert result is True
        assert mock_run.call_count == 1

    def test_retries_on_nonzero_exit(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        max_attempts = len(VODChatDownloader._DOWNLOAD_RETRY_DELAYS) + 1
        with patch("subprocess.run", return_value=self._failed_result()) as mock_run, \
             patch("time.sleep"):
            result = downloader.download_vod_chat("123456", output)
        assert result is False
        assert mock_run.call_count == max_attempts

    def test_retries_on_timeout(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        max_attempts = len(VODChatDownloader._DOWNLOAD_RETRY_DELAYS) + 1
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=300)) as mock_run, \
             patch("time.sleep"):
            result = downloader.download_vod_chat("123456", output)
        assert result is False
        assert mock_run.call_count == max_attempts

    def test_succeeds_on_second_attempt(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        with patch("subprocess.run", side_effect=[
            self._failed_result(),
            self._success_result(),
        ]) as mock_run, patch("time.sleep"):
            result = downloader.download_vod_chat("123456", output)
        assert result is True
        assert mock_run.call_count == 2

    def test_succeeds_on_third_attempt(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        with patch("subprocess.run", side_effect=[
            self._failed_result(),
            subprocess.TimeoutExpired(cmd=[], timeout=300),
            self._success_result(),
        ]) as mock_run, patch("time.sleep"):
            result = downloader.download_vod_chat("123456", output)
        assert result is True
        assert mock_run.call_count == 3

    def test_no_retry_on_file_not_found(self, downloader, tmp_path):
        """FileNotFoundError (CLI not installed) must not be retried."""
        output = str(tmp_path / "chat.json")
        with patch("subprocess.run", side_effect=FileNotFoundError("CLI not found")) as mock_run, \
             patch("time.sleep") as mock_sleep:
            result = downloader.download_vod_chat("123456", output)
        assert result is False
        assert mock_run.call_count == 1
        mock_sleep.assert_not_called()

    def test_sleep_delays_between_retries(self, downloader, tmp_path):
        output = str(tmp_path / "chat.json")
        expected_delays = VODChatDownloader._DOWNLOAD_RETRY_DELAYS
        with patch("subprocess.run", return_value=self._failed_result()), \
             patch("time.sleep") as mock_sleep:
            downloader.download_vod_chat("123456", output)

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        assert sleep_calls == list(expected_delays)

    def test_logs_permanent_failure(self, downloader, tmp_path, caplog):
        """Should log a clear permanent failure message after exhausting retries."""
        output = str(tmp_path / "chat.json")
        with patch("subprocess.run", return_value=self._failed_result()), \
             patch("time.sleep"), \
             caplog.at_level(logging.ERROR, logger="vod_collector"):
            downloader.download_vod_chat("999999", output)

        error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
        assert any("999999" in msg for msg in error_messages)
