#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eli.py pure functions (no network). Run: python3 tools/test_eli.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eli", ROOT / "plugins/prawo-pl-eli/skills/prawo-pl-eli/scripts/eli.py")
eli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eli)


class TestActPath(unittest.TestCase):
    def test_signature_forms(self):
        cases = [
            (["DU", "2024", "18"], "/acts/DU/2024/18"),
            (["DU/2024/18"], "/acts/DU/2024/18"),
            (["Dz.U. 2024 poz. 18"], "/acts/DU/2024/18"),
            (["Dz.U. 1997 nr 78 poz. 483"], "/acts/DU/1997/483"),
            (["WDU20240000018"], "/acts/WDU20240000018"),
            (["wdu20240000018"], "/acts/WDU20240000018"),
            (["MP", "2023", "1"], "/acts/MP/2023/1"),
            (["M.P. 2023 poz. 1"], "/acts/MP/2023/1"),
        ]
        for sig, want in cases:
            self.assertEqual(eli.act_path(sig)[0], want, f"sygnatura: {sig}")

    def test_invalid_signature_exits(self):
        with self.assertRaises(SystemExit):
            eli.act_path(["zupełnie błędna sygnatura"])


class TestHtmlToText(unittest.TestCase):
    def test_strips_script_and_style(self):
        t = eli.html_to_text("<p>A</p><script>var x=1;</script><style>.c{}</style><p>B</p>")
        self.assertIn("A", t)
        self.assertIn("B", t)
        self.assertNotIn("var x", t)
        self.assertNotIn(".c{", t)

    def test_normalizes_nbsp(self):
        # API ELI używa NBSP: "Art.\xa0299." musi być wyszukiwalne jako "Art. 299."
        self.assertEqual(eli.html_to_text("<p>Art.\xa0299.</p>"), "Art. 299.")

    def test_collapses_blank_lines(self):
        t = eli.html_to_text("<div><p>A</p><p></p><p></p><p>B</p></div>")
        self.assertNotIn("\n\n\n", t)


class TestFragmenty(unittest.TestCase):
    TXT = ("Tytuł I\n\nArt. 1.\nPierwszy przepis.\n\n"
           "Art. 2.\n§ 1. Drugi przepis o spółce.\n§ 2. Odesłanie, o którym mowa w art. 1.\n\n"
           "Art. 21.\nDwudziesty pierwszy przepis.\n\n"
           "Art. 211.\nPrzepis z indeksem (art. 21 ze zn. 1).\n")

    def _frag(self, fraza):
        spans = eli._fragmenty(self.TXT, fraza)
        return [self.TXT[s:e] for s, e in spans]

    def test_artykul_po_naglowku_nie_po_odeslaniu(self):
        frags = self._frag("art. 2")
        self.assertEqual(len(frags), 1)
        self.assertIn("Drugi przepis", frags[0])
        self.assertNotIn("Pierwszy", frags[0])
        self.assertNotIn("Dwudziesty", frags[0])  # "Art. 21." to inny artykuł

    def test_artykul_nie_lapie_dluzszego_numeru(self):
        frags = self._frag("art. 21")
        self.assertEqual(len(frags), 1)
        self.assertIn("Dwudziesty pierwszy", frags[0])
        self.assertNotIn("indeksem", frags[0])  # "Art. 211." to inny artykuł

    def test_fraza_pelnotekstowa_docieta_do_artykulu(self):
        frags = self._frag("o którym mowa")
        self.assertEqual(len(frags), 1)
        self.assertTrue(frags[0].startswith("Art. 2."))

    def test_brak_trafien(self):
        self.assertEqual(eli._fragmenty(self.TXT, "nie ma takiej frazy"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
