# src/tracker/ingest/downloader.py
"""Download NSW Property Sales Information from nswpropertysalesdata.com."""

import logging
import time
from pathlib import Path
from typing import Optional, List

import requests

logger = logging.getLogger(__name__)

# Pre-cleaned CSV archive from nswpropertysalesdata.com
# Contains 6 years of NSW property sales data, updated daily
PSI_ARCHIVE_URL = "https://nswpropertysalesdata.com/data/archive.zip"

# Default download location
DEFAULT_DATA_DIR = Path("data/raw")


def get_data_path(base_dir: Optional[Path] = None) -> Path:
    """
    Get the data directory path, creating it if necessary.

    Args:
        base_dir: Optional override for base directory

    Returns:
        Path to the data/raw directory
    """
    if base_dir:
        path = Path(base_dir)
    else:
        path = DEFAULT_DATA_DIR

    path.mkdir(parents=True, exist_ok=True)
    return path


def is_archive_stale(
    archive_path: Path,
    max_age_days: int = 7
) -> bool:
    """
    Check if the archive file is stale (older than max_age_days).

    Args:
        archive_path: Path to the archive file
        max_age_days: Maximum acceptable age in days

    Returns:
        True if file doesn't exist or is older than max_age_days
    """
    if not archive_path.exists():
        return True

    mtime = archive_path.stat().st_mtime
    age_seconds = time.time() - mtime
    age_days = age_seconds / (24 * 60 * 60)

    return age_days > max_age_days


def download_psi_archive(
    dest_dir: Path,
    force: bool = False,
    max_age_days: int = 7,
    timeout: int = 120,
) -> Path:
    """
    Download the NSW PSI archive from nswpropertysalesdata.com.

    Args:
        dest_dir: Directory to save the archive
        force: If True, download even if fresh file exists
        max_age_days: Skip download if file is newer than this
        timeout: Request timeout in seconds

    Returns:
        Path to the downloaded archive

    Raises:
        Exception: On download failure
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / "archive.zip"

    # Skip download if fresh and not forced
    if not force and archive_path.exists():
        if not is_archive_stale(archive_path, max_age_days):
            logger.info(f"Archive is fresh (< {max_age_days} days), skipping download")
            return archive_path

    logger.info(f"Downloading PSI archive from {PSI_ARCHIVE_URL}")

    response = requests.get(
        PSI_ARCHIVE_URL,
        stream=True,
        timeout=timeout,
        headers={
            "User-Agent": "PropertyTracker/1.0 (personal use)"
        }
    )
    response.raise_for_status()

    # Write to temp file then rename for atomicity
    temp_path = archive_path.with_suffix('.tmp')
    with open(temp_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    temp_path.rename(archive_path)
    logger.info(f"Downloaded {archive_path.stat().st_size / 1024 / 1024:.1f} MB to {archive_path}")

    return archive_path


def extract_archive(
    archive_path: Path,
    dest_dir: Optional[Path] = None
) -> List[Path]:
    """
    Extract the ZIP archive to destination directory.

    Args:
        archive_path: Path to the ZIP archive
        dest_dir: Destination directory (defaults to archive's parent)

    Returns:
        List of extracted file paths
    """
    import zipfile

    if dest_dir is None:
        dest_dir = archive_path.parent

    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(archive_path, 'r') as zf:
        zf.extractall(dest_dir)
        extracted = [dest_dir / name for name in zf.namelist()]

    logger.info(f"Extracted {len(extracted)} files to {dest_dir}")
    return extracted
