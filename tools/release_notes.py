#!/usr/bin/env python3
"""Wypisuje opis wskazanej wersji z CHANGELOG.md; brak wpisu blokuje wydanie."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent


def release_notes(changelog, version):
    version = version.removeprefix("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Nieprawidłowa wersja: {version}")
    heading = re.search(rf"^## {re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}\s*$",
                        changelog, re.MULTILINE)
    if heading is None:
        raise ValueError(f"Brak wpisu w CHANGELOG.md dla wersji {version}")
    body = re.split(r"^## ", changelog[heading.end():], maxsplit=1, flags=re.MULTILINE)[0].strip()
    if not body:
        raise ValueError(f"Pusty wpis w CHANGELOG.md dla wersji {version}")
    return body + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", nargs="?", help="wersja lub tag; domyślnie wersja marketplace")
    args = parser.parse_args()
    version = args.version or json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["plugins"][0]["version"]
    try:
        print(release_notes((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version), end="")
    except ValueError as exc:
        parser.exit(1, f"BŁĄD: {exc}\n")


if __name__ == "__main__":
    main()
