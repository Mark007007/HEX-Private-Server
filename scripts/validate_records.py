#!/usr/bin/env python3
"""Validate a client-derived HEX Records snapshot before using it locally."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


REQUIRED_SECTIONS = (
    "AbilityEffectConditionTemplate",
    "AbilityEffectTemplate",
    "AbilityTargetTemplate",
    "AbilityTemplate",
    "CardCounterTemplate",
    "CardTemplate",
    "ChampionClassData",
    "ChampionTalentData",
    "ChampionTemplate",
    "ConversationTemplate",
    "DeckTemplate",
    "EncounterDeck",
    "InventoryItemData",
    "QuestTemplate",
    "SceneData",
)


def validate_records(root: str | Path) -> tuple[list[str], dict[str, int]]:
    path = Path(root).expanduser().resolve()
    errors: list[str] = []
    counts: dict[str, int] = {}

    if not path.is_dir():
        return [f"Records directory does not exist: {path}"], counts

    for section in REQUIRED_SECTIONS:
        record_path = path / f"{section}.jsonl"
        if not record_path.is_file():
            errors.append(f"missing: {record_path.name}")
            continue

        count = 0
        try:
            with record_path.open("r", encoding="utf-8", errors="strict") as handle:
                lines = handle.readlines()
            if not lines or lines[0].rstrip("\n") != "# HEX-PRIVATE-SERVER Records v1":
                errors.append(
                    f"{record_path.name}: missing required first-line header {header!r}"
                )
            for lineno, line in enumerate(lines[1:], 2):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(
                        f"{record_path.name}:{lineno}: invalid JSON: {exc.msg}"
                    )
                    continue
                count += 1
        except UnicodeError as exc:
            errors.append(f"{record_path.name}: invalid UTF-8: {exc}")
            continue
        except OSError as exc:
            errors.append(f"{record_path.name}: read failure: {exc}")
            continue

        if count == 0:
            errors.append(f"empty: {record_path.name}")
        counts[section] = count

    return errors, counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the 15 client-derived HEX Records sections."
    )
    parser.add_argument(
        "records_dir",
        nargs="?",
        default=os.environ.get(
            "HEX_RECORDS",
            str(Path(__file__).resolve().parents[1] / "hex-server" / "Records"),
        ),
    )
    args = parser.parse_args()

    errors, counts = validate_records(args.records_dir)
    for section in REQUIRED_SECTIONS:
        filename = f"{section}.jsonl"
        if section in counts:
            print(f"{filename:40s} {counts[section]:7d} records")
    if errors:
        print("\nRecords validation FAILED:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        f"\nRecords validation PASS: {len(REQUIRED_SECTIONS)} sections, "
        f"{sum(counts.values())} non-empty JSONL records."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())