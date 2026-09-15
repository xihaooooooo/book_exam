from __future__ import annotations

import shutil
import stat
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile, ZipInfo


class MinerUArchiveError(RuntimeError):
    """Raised when a MinerU result archive is invalid or unsafe."""


def extract_mineru_results(book_directory: Path) -> tuple[Path, ...]:
    """Extract all downloaded MinerU ZIP files in filename order."""
    book_directory = book_directory.resolve()
    archives = sorted((book_directory / "mineru-results").glob("*.zip"))
    if not archives:
        raise MinerUArchiveError("No MinerU result archives were found")

    extracted_root = book_directory / "mineru-extracted"
    return tuple(
        extract_mineru_archive(archive, extracted_root / archive.stem)
        for archive in archives
    )


def extract_mineru_archive(archive_path: Path, destination: Path) -> Path:
    """Atomically extract a MinerU ZIP without allowing path traversal."""
    archive_path = archive_path.resolve()
    destination = destination.resolve()
    if not archive_path.is_file():
        raise MinerUArchiveError("MinerU result archive is missing")
    if destination.exists():
        raise MinerUArchiveError("MinerU result directory already exists")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{destination.name}.{uuid4().hex}.extracting"
    staging.mkdir()
    try:
        with ZipFile(archive_path) as archive:
            members = archive.infolist()
            for member in members:
                _validate_member(member, staging)
            archive.extractall(staging, members)
        staging.replace(destination)
    except MinerUArchiveError:
        raise
    except BadZipFile as error:
        raise MinerUArchiveError("MinerU result is not a valid ZIP archive") from error
    except Exception as error:
        raise MinerUArchiveError(f"Unable to extract MinerU result: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return destination


def _validate_member(member: ZipInfo, staging: Path) -> None:
    target = (staging / member.filename).resolve()
    if not target.is_relative_to(staging):
        raise MinerUArchiveError("MinerU archive contains an unsafe path")
    unix_mode = member.external_attr >> 16
    if stat.S_ISLNK(unix_mode):
        raise MinerUArchiveError("MinerU archive contains a symbolic link")
