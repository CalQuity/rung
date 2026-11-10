"""One failure contract, exercised against a corrected and deliberately faulty save."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from store import FaultyStore, Store


class FailurePathTests(unittest.TestCase):
    def make_store(self, implementation: type[Store] = Store) -> Store:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "value.txt"
        path.write_text("old")
        return implementation(path)

    def assert_rejected_write_preserves_state(self, store: Store) -> None:
        before_memory = store.value
        before_file = store.path.read_text()
        failure = PermissionError("injected rejection before writing")
        with patch.object(store, "_write", side_effect=failure) as write:
            with self.assertRaises(PermissionError) as caught:
                store.save("new")
            write.assert_called_once_with("new")
            self.assertIs(caught.exception, failure)
        self.assertEqual(store.path.read_text(), before_file, "stored data changed after rejected write")
        self.assertEqual(store.value, before_memory, "cache changed after rejected write")

    def test_initial_state(self) -> None:
        store = self.make_store()
        self.assertEqual(store.value, "old")
        self.assertEqual(store.path.read_text(), "old")

    def test_successful_save(self) -> None:
        # Happy-path tests alone cannot distinguish the implementations.
        for implementation in (Store, FaultyStore):
            with self.subTest(implementation=implementation.__name__):
                store = self.make_store(implementation)
                store.save("new")
                self.assertEqual(store.value, "new")
                self.assertEqual(store.path.read_text(), "new")

    def test_rejected_write_preserves_state(self) -> None:
        self.assert_rejected_write_preserves_state(self.make_store())

    def test_faulty_save_is_detected(self) -> None:
        # Catch only the expected contract violation, not an arbitrary exception.
        with self.assertRaisesRegex(AssertionError, "cache changed after rejected write"):
            self.assert_rejected_write_preserves_state(self.make_store(FaultyStore))

    def test_can_save_after_rejected_write(self) -> None:
        store = self.make_store()
        self.assert_rejected_write_preserves_state(store)
        store.save("recovered")
        self.assertEqual(store.value, "recovered")
        self.assertEqual(store.path.read_text(), "recovered")


if __name__ == "__main__":
    unittest.main()
