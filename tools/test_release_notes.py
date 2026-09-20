"""Testy wyboru opisu wydania, bez sieci."""
import unittest
from release_notes import release_notes


class ReleaseNotesTests(unittest.TestCase):
    CHANGELOG = "# Historia\n\n## Niewydane\n\n- Przyszłe zmiany\n\n## 2.0.3 — 2026-09-20\n\n- Kontakt\n\n## 2.0.2 — 2026-09-20\n\n- Poprawki\n"

    def test_extracts_only_requested_version(self):
        self.assertEqual(release_notes(self.CHANGELOG, "v2.0.3"), "- Kontakt\n")
        self.assertEqual(release_notes(self.CHANGELOG, "2.0.2"), "- Poprawki\n")

    def test_missing_version_fails(self):
        with self.assertRaisesRegex(ValueError, "Brak wpisu"):
            release_notes(self.CHANGELOG, "2.0.4")

    def test_empty_section_fails(self):
        with self.assertRaisesRegex(ValueError, "Pusty wpis"):
            release_notes("## 2.0.3 — 2026-09-20\n\n## 2.0.2 — 2026-09-20\n- Poprawki\n", "2.0.3")

    def test_invalid_version_fails(self):
        with self.assertRaises(ValueError):
            release_notes(self.CHANGELOG, "2.*")


if __name__ == "__main__":
    unittest.main()
