from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BookContext:
    """Identifies the stored textbook that textbook tools operate on."""

    book_id: str
    database_path: Path
