#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for edzienniki.py pure functions (no network). Run: python3 tools/test_edzienniki.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "edzienniki", ROOT / "plugins/prawo-pl-edzienniki/skills/prawo-pl-edzienniki/scripts/edzienniki.py")
edz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edz)


class TestWoj(unittest.TestCase):
    def test_kod(self):
        kod, nazwa, host, pub = edz._woj("DS")
        self.assertEqual((kod, host, pub), ("DS", "edzienniki.duw.pl", "POL_WOJ_DS"))

    def test_kod_male_litery(self):
        self.assertEqual(edz._woj("ds")[0], "DS")

    def test_nazwa_z_diakrytykami(self):
        self.assertEqual(edz._woj("dolnośląskie")[0], "DS")
        self.assertEqual(edz._woj("łódzkie")[0], "LD")

    def test_nazwa_bez_diakrytykow(self):
        self.assertEqual(edz._woj("dolnoslaskie")[0], "DS")
        self.assertEqual(edz._woj("lodzkie")[0], "LD")
        self.assertEqual(edz._woj("swietokrzyskie")[0], "SK")

    def test_prefiks_nazwy(self):
        self.assertEqual(edz._woj("mazow")[0], "MZ")

    def test_wszystkie_16(self):
        self.assertEqual(len(edz.WOJEWODZTWA), 16)
        for kod, (nazwa, host, pub) in edz.WOJEWODZTWA.items():
            self.assertEqual(pub, f"POL_WOJ_{kod}")
            self.assertTrue(host)

    def test_nieznane_exits(self):
        with self.assertRaises(SystemExit):
            edz._woj("pomorze-gdanskie-x")

    def test_brak_exits(self):
        with self.assertRaises(SystemExit):
            edz._woj(None)


class TestNorm(unittest.TestCase):
    def test_pascal_case(self):
        # 4 hosty (starsze wdrożenia ABC PRO) zwracają PascalCase — normalizacja do lowercase
        d = edz._norm({"Items": [{"Title": "Uchwała", "Pos": 5092, "Year": 2026}],
                       "TotalCount": 5092})
        self.assertEqual(d["totalcount"], 5092)
        self.assertEqual(d["items"][0]["title"], "Uchwała")

    def test_camel_case(self):
        d = edz._norm({"items": [], "totalCount": 3301})
        self.assertEqual(d["totalcount"], 3301)

    def test_zagniezdzenie_i_listy(self):
        d = edz._norm([{"A": {"B": [1, 2]}}])
        self.assertEqual(d, [{"a": {"b": [1, 2]}}])


class TestData(unittest.TestCase):
    def test_sentinel(self):
        self.assertIsNone(edz._data("0001-01-01T00:00:00"))

    def test_none(self):
        self.assertIsNone(edz._data(None))

    def test_iso_z_godzina(self):
        self.assertEqual(edz._data("2026-07-10T00:00:00"), "2026-07-10")


class TestAscii(unittest.TestCase):
    def test_diakrytyki(self):
        self.assertEqual(edz._ascii("Zagospodarowania Przestrzennego ŚĄŻ"),
                         "zagospodarowania przestrzennego saz")


class TestHtmlToText(unittest.TestCase):
    def test_akapity(self):
        t = edz.html_to_text("<p>§ 1. Uchwala się.</p><p>§ 2. Wykonanie.</p>")
        self.assertIn("§ 1. Uchwala się.", t)
        self.assertIn("\n", t)

    def test_nbsp(self):
        self.assertEqual(edz.html_to_text("<p>art.\xa05</p>"), "art. 5")


class TestStronicuj(unittest.TestCase):
    # regresja BUG 2026-07-23 (PM/Rumia): paginacja MUSI działać na liście przefiltrowanych
    # trafień i strony 1..N muszą pokrywać cały policzony zbiór (bez fałszywych negatywów)
    def test_strony_pokrywaja_caly_zbior(self):
        trafienia = list(range(43))  # 43 trafienia jak w zgłoszeniu
        _, _, strony = edz._stronicuj(trafienia, 10, 1)
        self.assertEqual(strony, 5)
        zebrane = []
        for s in range(1, strony + 1):
            okno, start, _ = edz._stronicuj(trafienia, 10, s)
            self.assertEqual(start, (s - 1) * 10)
            zebrane.extend(okno)
        self.assertEqual(zebrane, trafienia)

    def test_okno_rowne_limitowi(self):
        okno, start, strony = edz._stronicuj(list(range(43)), 50, 1)
        self.assertEqual((len(okno), start, strony), (43, 0, 1))

    def test_ostatnia_strona_czesciowa(self):
        okno, _, _ = edz._stronicuj(list(range(43)), 10, 5)
        self.assertEqual(okno, [40, 41, 42])

    def test_strona_poza_zakresem_pusta(self):
        okno, _, strony = edz._stronicuj(list(range(43)), 10, 6)
        self.assertEqual((okno, strony), ([], 5))

    def test_pusty_zbior(self):
        self.assertEqual(edz._stronicuj([], 10, 1), ([], 0, 1))


class TestFragmenty(unittest.TestCase):
    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "PLAN" + ("Y" * 50)
        spans = edz._fragmenty(txt, "plan")
        self.assertEqual(len(spans), 1)
        self.assertIn("PLAN", txt[spans[0][0]:spans[0][1]])

    def test_brak_trafien(self):
        self.assertEqual(edz._fragmenty("dowolny tekst", "nie ma"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
