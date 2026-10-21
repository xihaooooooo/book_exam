from __future__ import annotations

import platform
import sqlite3
import struct
from pathlib import Path

_SIMPLE_EXTENSION_PATH = (
    Path(__file__).parent
    / "vendor"
    / "simple"
    / "v0.7.1"
    / "windows-x64"
    / "simple.dll"
)


class SimpleExtensionError(RuntimeError):
    """Raised when the bundled SQLite simple extension cannot be loaded."""


def load_simple_extension(connection: sqlite3.Connection) -> None:
    """Load the bundled simple v0.7.1 extension into one SQLite connection."""
    _check_supported_runtime()
    if not callable(getattr(connection, "enable_load_extension", None)):
        raise SimpleExtensionError(
            "SQLite extension loading is unavailable in this Python build"
        )

    extension_path = _SIMPLE_EXTENSION_PATH.resolve()
    if not extension_path.is_file():
        raise SimpleExtensionError(
            f"Bundled simple v0.7.1 extension is missing: {extension_path}"
        )

    failure: Exception | None = None
    try:
        connection.enable_load_extension(True)
        connection.load_extension(str(extension_path))
    except Exception as error:
        failure = error
    finally:
        try:
            connection.enable_load_extension(False)
        except Exception as error:
            if failure is None:
                failure = error
            else:
                failure = SimpleExtensionError(
                    f"{failure}; also failed to disable extension loading: {error}"
                )

    if failure is not None:
        raise SimpleExtensionError(
            f"Unable to load bundled simple v0.7.1 extension: {failure}"
        ) from failure


def _check_supported_runtime() -> None:
    if platform.system() != "Windows":
        raise SimpleExtensionError(
            "Bundled simple v0.7.1 is available only for 64-bit Windows"
        )
    if struct.calcsize("P") != 8 or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise SimpleExtensionError(
            "Bundled simple v0.7.1 is available only for 64-bit x86 Windows"
        )
