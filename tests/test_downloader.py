# tests/test_downloader.py
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from tracker.ingest.downloader import (
    download_psi_archive,
    get_data_path,
    is_archive_stale,
)


class TestGetDataPath:
    """Test data path resolution."""

    def test_returns_path_object(self):
        path = get_data_path()
        assert isinstance(path, Path)

    def test_default_is_data_raw(self):
        path = get_data_path()
        assert path.name == 'raw'
        assert path.parent.name == 'data'


class TestIsArchiveStale:
    """Test archive freshness detection."""

    def test_nonexistent_file_is_stale(self, tmp_path):
        path = tmp_path / 'archive.zip'
        assert is_archive_stale(path, max_age_days=7) is True

    def test_fresh_file_is_not_stale(self, tmp_path):
        path = tmp_path / 'archive.zip'
        path.touch()  # Creates file with current timestamp
        assert is_archive_stale(path, max_age_days=7) is False

    def test_old_file_is_stale(self, tmp_path):
        import os
        import time
        path = tmp_path / 'archive.zip'
        path.touch()
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 24 * 60 * 60)
        os.utime(path, (old_time, old_time))
        assert is_archive_stale(path, max_age_days=7) is True


class TestDownloadPsiArchive:
    """Test PSI archive download."""

    @patch('tracker.ingest.downloader.requests.get')
    def test_downloads_to_correct_path(self, mock_get, tmp_path):
        """Verify download saves to expected location."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda chunk_size: [b'PK\x03\x04test']
        mock_get.return_value = mock_response

        result = download_psi_archive(tmp_path)

        assert result.exists()
        assert result.name == 'archive.zip'
        mock_get.assert_called_once()

    @patch('tracker.ingest.downloader.requests.get')
    def test_skips_download_if_fresh(self, mock_get, tmp_path):
        """Should not download if file exists and is fresh."""
        archive = tmp_path / 'archive.zip'
        archive.write_bytes(b'existing')

        result = download_psi_archive(tmp_path, force=False)

        assert result == archive
        mock_get.assert_not_called()

    @patch('tracker.ingest.downloader.requests.get')
    def test_force_redownload(self, mock_get, tmp_path):
        """Force flag should download even if file exists."""
        archive = tmp_path / 'archive.zip'
        archive.write_bytes(b'old')

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_content = lambda chunk_size: [b'new']
        mock_get.return_value = mock_response

        result = download_psi_archive(tmp_path, force=True)

        assert result.exists()
        mock_get.assert_called_once()

    @patch('tracker.ingest.downloader.requests.get')
    def test_handles_download_error(self, mock_get, tmp_path):
        """Should raise on HTTP error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = Exception("Server error")
        mock_get.return_value = mock_response

        with pytest.raises(Exception, match="Server error"):
            download_psi_archive(tmp_path, force=True)
