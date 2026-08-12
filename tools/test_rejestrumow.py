#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for rejestrumow.py pure functions (no network). Run: python3 tools/test_rejestrumow.py"""
import sys
import importlib.util
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rejestrumow", ROOT / "plugins/prawo-pl-rejestr-umow/skills/prawo-pl-rejestr-umow/scripts/rejestrumow.py")
rejestrumow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rejestrumow)


def ns(**kw):
    """Namespace jak z argparse dla komendy szukaj (wszystkie filtry puste, chyba że podane)."""
    base = dict(fraza=None, jsfp=None, regon=None, nip=None,
                wykonawca=None, wykonawca_nip=None, wykonawca_regon=None,
                woj=None, powiat=None, gmina=None, miejscowosc=None,
                status=None, od=None, do=None, pub_od=None, pub_do=None,
                wartosc_od=None, wartosc_do=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestFiltry(unittest.TestCase):
    def test_puste(self):
        self.assertEqual(rejestrumow._filtry(ns()), {})

    def test_fraza_do_przedmiotu(self):
        self.assertEqual(rejestrumow._filtry(ns(fraza="remont drogi")),
                         {"menuGlowne": {"przedmiotUmowy": "remont drogi"}})

    def test_sekcje_rozdzielone(self):
        body = rejestrumow._filtry(ns(jsfp="gmina", woj="dolnośląskie", wykonawca="KAMA"))
        self.assertEqual(body, {"menuGlowne": {"nazwa": "gmina"},
                                "jsfp": {"wojewodztwo": "dolnośląskie"},
                                "inneStronyUmowy": {"nazwa": "KAMA"}})

    def test_daty_i_wartosci(self):
        body = rejestrumow._filtry(ns(od="2026-07-01", pub_do="2026-07-23", wartosc_od="100 000,50"))
        self.assertEqual(body["menuGlowne"], {"dataZawarciaOd": "2026-07-01",
                                              "dataPublikacjiDo": "2026-07-23",
                                              "wartoscOd": 100000.50})

    def test_zla_data_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._filtry(ns(od="01.07.2026"))  # API wymaga RRRR-MM-DD


class TestLiczba(unittest.TestCase):
    def test_spacje_i_przecinek(self):
        self.assertEqual(rejestrumow._liczba("1 000 000,50"), 1000000.50)

    def test_kropka(self):
        self.assertEqual(rejestrumow._liczba("49999.99"), 49999.99)

    def test_zla_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._liczba("sto złotych")


class TestKwota(unittest.TestCase):
    def test_format_polski(self):
        # separator tysięcy = spacja niełamliwa, przecinek dziesiętny
        self.assertEqual(rejestrumow._kwota(2733508), "2 733 508,00 zł")

    def test_grosze(self):
        self.assertEqual(rejestrumow._kwota(1188.31), "1 188,31 zł")

    def test_none(self):
        self.assertEqual(rejestrumow._kwota(None), "—")


class TestUuid(unittest.TestCase):
    def test_poprawny(self):
        self.assertTrue(rejestrumow._uuid_ok("0002c775-2526-484f-9b93-5a60e2b934c4"))

    def test_wielkie_litery(self):
        self.assertTrue(rejestrumow._uuid_ok("0002C775-2526-484F-9B93-5A60E2B934C4"))

    def test_niepoprawne(self):
        # API zwraca 500 (nie 404) na złe id — walidacja przed wysłaniem jest konieczna
        for zly in ("xyz", "", None, "0002c775-2526-484f-9b93", "0002c775252648 f9b935a60e2b934c4"):
            self.assertFalse(rejestrumow._uuid_ok(zly))


class TestData(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(rejestrumow._data("2026-07-01"), "2026-07-01")

    def test_format_polski_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._data("23.07.2026")


class TestPelne(unittest.TestCase):
    def test_usuwa_puste(self):
        self.assertEqual(rejestrumow._pelne({"a": 1, "b": None, "c": "", "d": 0}),
                         {"a": 1, "d": 0})


class TestLimit(unittest.TestCase):
    """API tnie stronę do 50 — nagłówek i numeracja stron muszą mówić o REALNEJ wielkości."""

    def test_obciete_do_50(self):
        self.assertEqual(rejestrumow._limit(100), 50)
        self.assertEqual(rejestrumow._limit(50), 50)

    def test_male_wartosci_bez_zmian(self):
        self.assertEqual(rejestrumow._limit(10), 10)
        self.assertEqual(rejestrumow._limit(1), 1)

    def test_zero_i_ujemne_podnoszone(self):
        self.assertEqual(rejestrumow._limit(0), 1)
        self.assertEqual(rejestrumow._limit(-5), 1)


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, rejestrumow.cmd_szukaj
        rejestrumow.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            rejestrumow.main()
        finally:
            sys.argv, rejestrumow.cmd_szukaj = oryg_argv, oryg_cmd
        return zlapane

    def test_flaga_po_komendzie(self):
        self.assertTrue(self._parsuj(self.ARGV + ["--json"])["json"])

    def test_flaga_przed_komenda(self):
        self.assertTrue(self._parsuj(["--json"] + self.ARGV)["json"])

    def test_bez_flagi(self):
        self.assertFalse(self._parsuj(self.ARGV)["json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
