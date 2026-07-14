#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for cbosa.py pure functions (no network). Run: python3 tools/test_cbosa.py"""
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "cbosa", ROOT / "plugins/prawo-pl-cbosa/skills/prawo-pl-cbosa/scripts/cbosa.py")
cbosa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbosa)


class TestSad(unittest.TestCase):
    def test_nsa(self):
        self.assertEqual(cbosa._sad("NSA"), "Naczelny Sąd Administracyjny")
        self.assertEqual(cbosa._sad("nsa"), "Naczelny Sąd Administracyjny")

    def test_wsa_miasta(self):
        cases = [
            ("WSA Warszawa", "Wojewódzki Sąd Administracyjny w Warszawie"),
            ("wsa warszawa", "Wojewódzki Sąd Administracyjny w Warszawie"),
            ("WSA Wrocław", "Wojewódzki Sąd Administracyjny we Wrocławiu"),
            ("wsa wroclaw", "Wojewódzki Sąd Administracyjny we Wrocławiu"),
            ("WSA w Krakowie", "Wojewódzki Sąd Administracyjny w Krakowie"),  # forma z „w"
            ("Łódź", "Wojewódzki Sąd Administracyjny w Łodzi"),
            ("gorzów wlkp.", "Wojewódzki Sąd Administracyjny w Gorzowie Wlkp."),
        ]
        for inp, want in cases:
            self.assertEqual(cbosa._sad(inp), want, f"sąd: {inp!r}")

    def test_pelna_nazwa_przechodzi(self):
        # pełna nazwa z formularza CBOSA (też historyczne ośrodki zamiejscowe) idzie bez zmian
        for nazwa in ("Naczelny Sąd Administracyjny",
                      "Wojewódzki Sąd Administracyjny w Gliwicach",
                      "NSA oz. w Katowicach"):
            self.assertEqual(cbosa._sad(nazwa), nazwa)

    def test_domyslny(self):
        self.assertEqual(cbosa._sad(None), "dowolny")
        self.assertEqual(cbosa._sad("dowolny"), "dowolny")

    def test_nieznany_exits(self):
        with self.assertRaises(SystemExit):
            cbosa._sad("WSA Pcim")


class TestRodzaj(unittest.TestCase):
    def test_aliasy(self):
        for inp, want in [("wyrok", "Wyrok"), ("POSTANOWIENIE", "Postanowienie"),
                          ("uchwala", "Uchwała"), ("uchwała", "Uchwała")]:
            self.assertEqual(cbosa._rodzaj(inp), want)

    def test_domyslny(self):
        self.assertEqual(cbosa._rodzaj(None), "dowolny")

    def test_nieznany_exits(self):
        with self.assertRaises(SystemExit):
            cbosa._rodzaj("apelacja")


class TestWyniki(unittest.TestCase):
    # zminiaturyzowana struktura żywej strony wyników CBOSA (2026-07)
    HTML = """
    <table class="top-linki"><tr><td>Znaleziono 1878 orzeczeń, Str. 1 z 188</td></tr></table>
    <table class="info-list">
      <tr class="niezaznaczona"><td class="info-list-value " style="font-size: 12pt; border: none;">
        <a href="/doc/8889489BE0"  >\n II FSK 2870/18 - Wyrok NSA z 2021-02-10 </a>
        <a href="/cbo/pow/8889489BE0"><img src="/img/copy.png"/></a></td></tr>
      <tr class="niezaznaczona"><td class="info-list-value " style="font-size: 10pt; border: none;">
        uchylono zaskarżony wyrok... 6112 Podatek dochodowy<BR/></td></tr>
      <tr><td><span class="powiazane"><a href="/doc/109C7D1883"  >
        I SA/Bk 226/18 - Wyrok WSA w Białymstoku z 2018-06-06<br/></a></span></td></tr>
    </table>"""

    def test_total_i_pozycje(self):
        total, poz = cbosa._wyniki(self.HTML)
        self.assertEqual(total, 1878)
        self.assertEqual(len(poz), 2)

    def test_glowny_wynik(self):
        _, poz = cbosa._wyniki(self.HTML)
        glowny = poz[0]
        self.assertEqual(glowny["doc_id"], "8889489BE0")
        self.assertFalse(glowny["powiazane"])
        self.assertIn("II FSK 2870/18", glowny["opis"])
        self.assertIn("6112", glowny["snippet"])

    def test_powiazane_oznaczone(self):
        _, poz = cbosa._wyniki(self.HTML)
        self.assertTrue(poz[1]["powiazane"])
        self.assertEqual(poz[1]["doc_id"], "109C7D1883")

    def test_pusty_html(self):
        total, poz = cbosa._wyniki("<html><body>Brak wyników</body></html>")
        self.assertIsNone(total)
        self.assertEqual(poz, [])


class TestOrzeczenie(unittest.TestCase):
    HTML = """
    <TITLE>II FSK 2870/18 - Wyrok NSA z 2021-02-10</TITLE>
    <table><tr><td class="lista-label"><td class="info-list-label">Data orzeczenia</td>
      <td class="info-list-value">2021-02-10</td></tr>
    <tr><td class="info-list-label">Sąd</td><td class="info-list-value">Naczelny Sąd Administracyjny</td></tr>
    <tr><td class="info-list-label">Sygn. powiązane</td>
      <td class="info-list-value"><a href="/doc/109C7D1883">I SA/Bk 226/18 - Wyrok WSA</a></td></tr></table>
    <td class="info-list-label-uzasadnienie"><div class="lista-label">Sentencja</div>
      <span class="info-list-value-uzasadnienie"><P>Naczelny Sąd Administracyjny uchyla wyrok.</P></span></td>
    <td class="info-list-label-uzasadnienie"><div class="lista-label">Uzasadnienie</div>
      <span class="info-list-value-uzasadnienie"><P>1. Wyrokiem z 6 czerwca 2018 r.&nbsp;sąd oddalił skargę.</P></span></td>
    """

    def test_tytul_i_sygnatura(self):
        d = cbosa._orzeczenie(self.HTML, "8889489BE0")
        self.assertEqual(d["sygnatura"], "II FSK 2870/18")
        self.assertEqual(d["doc_id"], "8889489BE0")

    def test_metadane(self):
        d = cbosa._orzeczenie(self.HTML, "X")
        self.assertEqual(d["metadane"]["Data orzeczenia"], "2021-02-10")
        self.assertEqual(d["metadane"]["Sąd"], "Naczelny Sąd Administracyjny")

    def test_powiazane_doc_id(self):
        d = cbosa._orzeczenie(self.HTML, "X")
        self.assertEqual(d["powiazane"][0]["doc_id"], "109C7D1883")

    def test_sekcje(self):
        d = cbosa._orzeczenie(self.HTML, "X")
        self.assertIn("uchyla wyrok", d["sentencja"])
        self.assertIn("oddalił skargę", d["uzasadnienie"])
        self.assertNotIn("<P>", d["uzasadnienie"])
        self.assertNotIn("\xa0", d["uzasadnienie"])  # NBSP znormalizowany


class TestText(unittest.TestCase):
    def test_akapity_i_encje(self):
        t = cbosa._text("<P>art.&nbsp;116</P><BR/>dalej &amp; koniec")
        self.assertIn("art. 116", t)
        self.assertIn("dalej & koniec", t)
        self.assertNotIn("<", t)


class TestFragmenty(unittest.TestCase):
    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "FRAZA" + ("Y" * 50)
        spans = cbosa._fragmenty(txt, "fraza")
        self.assertEqual(len(spans), 1)
        self.assertIn("FRAZA", txt[spans[0][0]:spans[0][1]])

    def test_brak_trafien(self):
        self.assertEqual(cbosa._fragmenty("dowolny tekst", "nie ma"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
