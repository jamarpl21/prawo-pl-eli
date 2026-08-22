#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for uodo.py pure functions (no network). Run: python3 tools/test_uodo.py"""
import sys
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "uodo", ROOT / "plugins/prawo-pl-uodo/skills/prawo-pl-uodo/scripts/uodo.py")
uodo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uodo)


class TestRefid(unittest.TestCase):
    def test_sygnatura_dkn(self):
        self.assertEqual(uodo._refid("DKN.5131.9.2025"), "urn:ndoc:gov:pl:uodo:2025:dkn_5131_9")

    def test_sygnatura_dke(self):
        self.assertEqual(uodo._refid("DKE.561.1.2026"), "urn:ndoc:gov:pl:uodo:2026:dke_561_1")

    def test_diakrytyki_transliterowane(self):
        # URN-y portalu są w ASCII: ZSOŚS → zsoss (zweryfikowane na żywym API)
        self.assertEqual(uodo._refid("ZSOŚS.440.259.2019"), "urn:ndoc:gov:pl:uodo:2019:zsoss_440_259")

    def test_urn_przechodzi(self):
        urn = "urn:ndoc:gov:pl:uodo:2025:dkn_5131_9"
        self.assertEqual(uodo._refid(urn), urn)
        self.assertEqual(uodo._refid(urn.upper()), urn)

    def test_bez_roku_exits(self):
        with self.assertRaises(SystemExit):
            uodo._refid("DKN")


class TestTimespan(unittest.TestCase):
    def test_pusty(self):
        self.assertEqual(uodo._timespan(None, None), ",")

    def test_od(self):
        self.assertEqual(uodo._timespan("2026-01-01", None), "2026-01-01,")

    def test_od_do(self):
        self.assertEqual(uodo._timespan("2026-01-01", "2026-06-30"), "2026-01-01,2026-06-30")


class TestPl(unittest.TestCase):
    def test_dict_pl(self):
        self.assertEqual(uodo._pl({"pl": "tytuł"}), "tytuł")

    def test_dict_bez_pl(self):
        self.assertEqual(uodo._pl({"en": "title"}), "title")

    def test_string(self):
        self.assertEqual(uodo._pl("zwykły"), "zwykły")

    def test_none(self):
        self.assertEqual(uodo._pl(None), "")


class TestDaty(unittest.TestCase):
    def test_announcement_publication(self):
        item = {"dates": [
            {"date": "2025-08-07", "use": "announcement"},
            {"date": "2025-08-27", "use": "publication"},
            {"date": "2025-09-11", "use": "validation"},
        ]}
        d = uodo._daty(item)
        self.assertEqual(d["announcement"], "2025-08-07")
        self.assertEqual(d["publication"], "2025-08-27")
        self.assertEqual(d["validation"], "2025-09-11")

    def test_brak_dat(self):
        self.assertEqual(uodo._daty({}), {})


class TestFragmenty(unittest.TestCase):
    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "KARA" + ("Y" * 50)
        spans = uodo._fragmenty(txt, "kara")
        self.assertEqual(len(spans), 1)
        self.assertIn("KARA", txt[spans[0][0]:spans[0][1]])

    def test_brak_trafien(self):
        self.assertEqual(uodo._fragmenty("dowolny tekst", "nie ma"), [])


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, uodo.cmd_szukaj
        uodo.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            uodo.main()
        finally:
            sys.argv, uodo.cmd_szukaj = oryg_argv, oryg_cmd
        return zlapane

    def test_flaga_po_komendzie(self):
        self.assertTrue(self._parsuj(self.ARGV + ["--json"])["json"])

    def test_flaga_przed_komenda(self):
        self.assertTrue(self._parsuj(["--json"] + self.ARGV)["json"])

    def test_bez_flagi(self):
        self.assertFalse(self._parsuj(self.ARGV)["json"])

    def test_strict_po_komendzie(self):
        self.assertTrue(self._parsuj(self.ARGV + ["--strict"])["strict"])

    def test_strict_przed_komenda(self):
        self.assertTrue(self._parsuj(["--strict"] + self.ARGV)["strict"])

    def test_bez_strict(self):
        self.assertFalse(self._parsuj(self.ARGV)["strict"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
