import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_records import REQUIRED_SECTIONS, validate_records


class RecordsValidationTests(unittest.TestCase):
    def _write_snapshot(self, root: Path, *, omit: str | None = None) -> None:
        for section in REQUIRED_SECTIONS:
            if section == omit:
                continue
            (root / f"{section}.jsonl").write_text(
                "# HEX-PRIVATE-SERVER Records v1\n"
                + json.dumps(
                    '{"m_Guid":"00000000-0000-0000-0000-000000000000"}'
                )
                + "\n",
                encoding="utf-8",
            )

    def test_complete_snapshot_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_snapshot(root)
            errors, counts = validate_records(root)
            self.assertEqual(errors, [])
            self.assertEqual(set(counts), set(REQUIRED_SECTIONS))
            self.assertTrue(all(count == 1 for count in counts.values()))

    def test_missing_section_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_snapshot(root, omit="EncounterDeck")
            errors, counts = validate_records(root)
            self.assertIn("missing: EncounterDeck.jsonl", errors)
            self.assertNotIn("EncounterDeck", counts)

    def test_missing_header_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_snapshot(root)
            path = root / "CardTemplate.jsonl"
            path.write_text(
                json.dumps(
                    '{"m_Guid":"00000000-0000-0000-0000-000000000000"}'
                ) + "\n",
                encoding="utf-8",
            )
            errors, counts = validate_records(root)
            self.assertTrue(
                any("CardTemplate.jsonl: missing required first-line header" in x
                    for x in errors)
            )

    def test_invalid_json_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_snapshot(root)
            (root / "CardTemplate.jsonl").write_text(
                "# HEX-PRIVATE-SERVER Records v1\n{not valid json}\n",
                encoding="utf-8",
            )
            errors, counts = validate_records(root)
            self.assertTrue(
                any("CardTemplate.jsonl:2: invalid JSON" in x for x in errors)
            )


if __name__ == "__main__":
    unittest.main()