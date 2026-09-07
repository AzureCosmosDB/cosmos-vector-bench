from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.add_sessionids import RollingSessionAssigner, main, preprocess_jsonl


class RollingSessionAssignerTests(unittest.TestCase):
    def test_large_assignment_respects_rolling_bounds_and_adjacency(self) -> None:
        assigner = RollingSessionAssigner(10, 100, 1_000, 42)
        assignments = [assigner.next() for _ in range(250_000)]
        counts = Counter(assignments)

        self.assertTrue(all(left != right for left, right in zip(assignments, assignments[1:])))
        self.assertLessEqual(max(counts.values()), 100)
        self.assertLessEqual(sum(count < 10 for count in counts.values()), assigner.incomplete_sessions)
        self.assertLessEqual(assigner.incomplete_sessions, 1_000)
        self.assertEqual(len(counts), assigner.sessions_created)
        self.assertEqual(assigner.sessions_created, assigner.completed_sessions + assigner.incomplete_sessions)

    def test_slots_are_created_lazily(self) -> None:
        assigner = RollingSessionAssigner(10, 100, 1_000, 42)
        assignments = [assigner.next() for _ in range(5)]

        self.assertEqual(5, assigner.sessions_created)
        self.assertEqual(5, assigner.incomplete_sessions)
        self.assertEqual(5, len(set(assignments)))

    def test_invalid_options_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RollingSessionAssigner(0, 100, 1_000, 42)
        with self.assertRaises(ValueError):
            RollingSessionAssigner(101, 100, 1_000, 42)
        with self.assertRaises(ValueError):
            RollingSessionAssigner(10, 100, 1, 42)


class PreprocessJsonlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_input(self, count: int, *, existing_sessionids: bool = False) -> Path:
        path = self.root / "input.json"
        with path.open("wt", encoding="utf-8", newline="\n") as stream:
            for index in range(count):
                document = {"docid": f"doc-{index}", "position": index, "nested": {"value": index}}
                if existing_sessionids:
                    document["sessionid"] = "old-session"
                json.dump(document, stream)
                stream.write("\n")
                if index == 1:
                    stream.write("\n")
        return path

    @staticmethod
    def _read(path: Path) -> list[dict]:
        with path.open("rt", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def test_preprocessing_preserves_order_and_overwrites_existing_values(self) -> None:
        input_path = self._write_input(5_000, existing_sessionids=True)
        output_path = self.root / "output.json"

        summary = preprocess_jsonl(input_path, output_path, session_pool_size=100)
        original = self._read(input_path)
        processed = self._read(output_path)
        counts = Counter(document["sessionid"] for document in processed)

        self.assertEqual(5_000, summary["documents_processed"])
        self.assertEqual(5_000, summary["existing_sessionids_overwritten"])
        self.assertEqual(100, summary["session_pool_size"])
        self.assertEqual(list(range(5_000)), [document["position"] for document in processed])
        self.assertEqual([document["nested"] for document in original], [document["nested"] for document in processed])
        self.assertTrue(all(left["sessionid"] != right["sessionid"] for left, right in zip(processed, processed[1:])))
        self.assertLessEqual(max(counts.values()), 100)
        self.assertEqual(summary["sessions_created"], summary["completed_sessions"] + summary["incomplete_sessions"])
        self.assertLessEqual(summary["incomplete_sessions"], 100)

    def test_default_seed_produces_byte_identical_output(self) -> None:
        input_path = self._write_input(2_000)
        first = self.root / "first.json"
        second = self.root / "second.json"

        preprocess_jsonl(input_path, first)
        preprocess_jsonl(input_path, second)

        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_different_seed_changes_assignments(self) -> None:
        input_path = self._write_input(2_000)
        first = self.root / "first.json"
        second = self.root / "second.json"

        preprocess_jsonl(input_path, first, seed=42)
        preprocess_jsonl(input_path, second, seed=43)

        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_empty_input_creates_empty_output(self) -> None:
        input_path = self._write_input(0)
        output_path = self.root / "output.json"

        summary = preprocess_jsonl(input_path, output_path)

        self.assertEqual(0, summary["sessions_created"])
        self.assertEqual(0, summary["incomplete_sessions"])
        self.assertEqual(b"", output_path.read_bytes())

    def test_existing_output_requires_force(self) -> None:
        input_path = self._write_input(20)
        output_path = self.root / "output.json"
        output_path.write_text("existing", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            preprocess_jsonl(input_path, output_path)

        preprocess_jsonl(input_path, output_path, force=True)
        self.assertEqual(20, len(self._read(output_path)))

    def test_progress_reports_one_pass_counts_and_completion(self) -> None:
        input_path = self._write_input(5, existing_sessionids=True)
        output_path = self.root / "output.json"
        messages: list[str] = []

        preprocess_jsonl(input_path, output_path, progress=messages.append, progress_every=2)

        self.assertTrue(any("Processed 2 JSON documents" in message for message in messages))
        self.assertTrue(any("replaced 4 existing session IDs" in message for message in messages))
        self.assertFalse(any("Inspected" in message or "%" in message for message in messages))
        self.assertIn("added 5 session ID assignments", messages[-1])
        self.assertFalse(any("unique session" in message for message in messages))

    def test_input_and_output_must_differ(self) -> None:
        input_path = self._write_input(20)
        with self.assertRaises(ValueError):
            preprocess_jsonl(input_path, input_path, force=True)

    def test_invalid_record_does_not_leave_output_or_temp_file(self) -> None:
        input_path = self.root / "input.json"
        input_path.write_text('{"docid":"one"}\nnot-json\n', encoding="utf-8")
        output_path = self.root / "output.json"

        with self.assertRaisesRegex(ValueError, "line 2"):
            preprocess_jsonl(input_path, output_path)

        self.assertFalse(output_path.exists())
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_non_object_record_is_rejected(self) -> None:
        input_path = self.root / "input.json"
        input_path.write_text("[]\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "expected an object"):
            preprocess_jsonl(input_path, self.root / "output.json")

    def test_invalid_pool_size_is_rejected(self) -> None:
        input_path = self._write_input(20)
        with self.assertRaisesRegex(ValueError, "session_pool_size"):
            preprocess_jsonl(input_path, self.root / "output.json", session_pool_size=1)

    def test_cli_returns_failure_without_replacing_existing_output(self) -> None:
        input_path = self._write_input(20)
        output_path = self.root / "output.json"
        output_path.write_text("existing", encoding="utf-8")

        exit_code = main(["--input", str(input_path), "--output", str(output_path)])

        self.assertEqual(1, exit_code)
        self.assertEqual("existing", output_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
