#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for rejestrumow.py pure functions (no network). Run: python3 tools/test_rejestrumow.py"""
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
