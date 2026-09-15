from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

_MAX_PAGES = 200


class PdfChunkingError(RuntimeError):
    """Raised when a stored textbook cannot be split into PDF chunks."""


@dataclass(frozen=True, slots=True)
class PdfChunk:
    path: Path
    start_page: int
    end_page: int
    data_id: str


class PdfChunker:
    """Splits a stored textbook into atomically published, page-limited PDFs."""

    def chunk(self, book_directory: Path) -> tuple[PdfChunk, ...]:
        book_directory = book_directory.resolve()
        source_path = book_directory / "source.pdf"
        destination = book_directory / "mineru-chunks"
        staging = book_directory / f".{uuid4().hex}.chunking"

        if not source_path.is_file():
            raise PdfChunkingError(f"Stored PDF is missing: {source_path}")

        chunks: list[PdfChunk] = []
        try:
            reader = PdfReader(source_path)
            if reader.is_encrypted:
                raise PdfChunkingError("Encrypted PDFs are not supported")
            if not reader.pages:
                raise PdfChunkingError("Stored PDF contains no pages")

            staging.mkdir()
            for first_index in range(0, len(reader.pages), _MAX_PAGES):
                start_page = first_index + 1
                end_page = min(first_index + _MAX_PAGES, len(reader.pages))
                data_id = f"chunk-{start_page:04d}-{end_page:04d}"
                filename = f"{data_id}.pdf"

                writer = PdfWriter()
                for page_index in range(first_index, end_page):
                    writer.add_page(reader.pages[page_index])
                writer.write(staging / filename)
                chunks.append(
                    PdfChunk(
                        path=destination / filename,
                        start_page=start_page,
                        end_page=end_page,
                        data_id=data_id,
                    )
                )

            self._publish(staging, destination)
        except PdfChunkingError:
            raise
        except Exception as error:
            raise PdfChunkingError(f"Unable to split PDF: {error}") from error
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        return tuple(chunks)

    @staticmethod
    def _publish(staging: Path, destination: Path) -> None:
        if destination.exists():
            raise PdfChunkingError(f"Chunk destination already exists: {destination}")
        staging.replace(destination)
