import sqlite3
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from book_exam.search import SimpleExtensionError, load_simple_extension
from book_exam.search import simple_extension


class SimpleExtensionTests(unittest.TestCase):
    def test_loads_bundled_absolute_path_and_disables_loading(self) -> None:
        calls: list[tuple[object, ...]] = []

        class Connection:
            def enable_load_extension(self, enabled: bool) -> None:
                calls.append(("enable", enabled))

            def load_extension(self, path: str) -> None:
                calls.append(("load", path))

        expected_path = (
            Path(simple_extension.__file__).parent
            / "vendor"
            / "simple"
            / "v0.7.1"
            / "windows-x64"
            / "simple.dll"
        ).resolve()

        with self._supported_runtime():
            load_simple_extension(Connection())  # type: ignore[arg-type]

        self.assertEqual(
            calls,
            [("enable", True), ("load", str(expected_path)), ("enable", False)],
        )

    def test_disables_extension_loading_after_load_failure(self) -> None:
        calls: list[tuple[object, ...]] = []

        class Connection:
            def enable_load_extension(self, enabled: bool) -> None:
                calls.append(("enable", enabled))

            def load_extension(self, path: str) -> None:
                calls.append(("load", path))
                raise sqlite3.OperationalError("load rejected")

        with self.assertRaisesRegex(SimpleExtensionError, "load rejected"):
            with self._supported_runtime():
                load_simple_extension(Connection())  # type: ignore[arg-type]

        self.assertEqual(calls[0], ("enable", True))
        self.assertEqual(calls[-1], ("enable", False))

    def test_rejects_runtime_without_extension_loading(self) -> None:
        with self.assertRaisesRegex(SimpleExtensionError, "unavailable"):
            with self._supported_runtime():
                load_simple_extension(object())  # type: ignore[arg-type]

    def test_rejects_unsupported_platform(self) -> None:
        with patch.object(simple_extension.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(SimpleExtensionError, "only for 64-bit Windows"):
                load_simple_extension(object())  # type: ignore[arg-type]

    def test_rejects_unsupported_architecture_and_process_width(self) -> None:
        with (
            patch.object(simple_extension.platform, "system", return_value="Windows"),
            patch.object(simple_extension.platform, "machine", return_value="ARM64"),
        ):
            with self.assertRaisesRegex(SimpleExtensionError, "64-bit x86 Windows"):
                load_simple_extension(object())  # type: ignore[arg-type]

        with (
            patch.object(simple_extension.platform, "system", return_value="Windows"),
            patch.object(simple_extension.platform, "machine", return_value="AMD64"),
            patch.object(simple_extension.struct, "calcsize", return_value=4),
        ):
            with self.assertRaisesRegex(SimpleExtensionError, "64-bit x86 Windows"):
                load_simple_extension(object())  # type: ignore[arg-type]

    def test_rejects_missing_bundled_resource(self) -> None:
        missing = simple_extension._SIMPLE_EXTENSION_PATH.with_name("missing.dll")
        with patch.object(simple_extension, "_SIMPLE_EXTENSION_PATH", missing):
            with self.assertRaisesRegex(SimpleExtensionError, "extension is missing"):
                with self._supported_runtime():
                    load_simple_extension(_ExtensionCapableConnection())

    def _supported_runtime(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            patch.object(simple_extension.platform, "system", return_value="Windows")
        )
        stack.enter_context(
            patch.object(simple_extension.platform, "machine", return_value="AMD64")
        )
        stack.enter_context(patch.object(simple_extension.struct, "calcsize", return_value=8))
        return stack


class _ExtensionCapableConnection:
    def enable_load_extension(self, enabled: bool) -> None:
        pass

    def load_extension(self, path: str) -> None:
        pass
