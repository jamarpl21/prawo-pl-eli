#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for cbosa.py pure functions (no network). Run: python3 tools/test_cbosa.py"""
import os
import io
import json
import sys
import importlib.util
import http.client
import pathlib
import socket
import ssl
import unittest
import contextlib
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

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


class TestData(unittest.TestCase):
    def test_pelna_data_bez_zmian(self):
        self.assertEqual(cbosa._data("2024-01-31"), "2024-01-31")

    def test_pusta(self):
        self.assertEqual(cbosa._data(None), "")
        self.assertEqual(cbosa._data(""), "")

    def test_skroty_uzupelniane_zaleznie_od_konca(self):
        self.assertEqual(cbosa._data("2024"), "2024-01-01")
        self.assertEqual(cbosa._data("2024", koniec=True), "2024-12-31")
        self.assertEqual(cbosa._data("2024-02"), "2024-02-01")
        self.assertEqual(cbosa._data("2024-02", koniec=True), "2024-02-29")  # rok przestępny
        self.assertEqual(cbosa._data("2023-02", koniec=True), "2023-02-28")

    def test_separatory_i_zera(self):
        self.assertEqual(cbosa._data("2024.1.5"), "2024-01-05")

    def test_zly_format_exits(self):
        # regresja: CBOSA odpowiada wtedy stroną błędu, nie do odróżnienia od przeciążenia
        for zla in ("31-12-2024", "2024-13-01", "2024-02-30", "wczoraj", "01/2024"):
            with self.assertRaises(SystemExit, msg=f"data: {zla!r}"):
                cbosa._data(zla)


class TestFormularz(unittest.TestCase):
    """Mapowanie argumentów CLI na pola formularza CBOSA."""

    class _Args:
        fraza = sygnatura = sad = rodzaj = symbol = sedzia = od = do = None

    def _form(self, **kw):
        a = self._Args()
        for k, v in kw.items():
            setattr(a, k, v)
        return cbosa._formularz(a)

    def test_samo_od_dostaje_gorna_granice(self):
        # regresja: CBOSA na samo `odDaty` (puste `doDaty`) zwraca ZERO wyników
        f = self._form(od="2024-01-01")
        self.assertEqual(f["odDaty"], "2024-01-01")
        self.assertEqual(f["doDaty"], cbosa.DO_OTWARTE)

    def test_samo_do_bez_zmian(self):
        f = self._form(do="2020-12-31")  # ta strona zakresu działa w CBOSA otwarta
        self.assertEqual(f["odDaty"], "")
        self.assertEqual(f["doDaty"], "2020-12-31")

    def test_oba_konce_zachowane(self):
        f = self._form(od="2024", do="2025")
        self.assertEqual((f["odDaty"], f["doDaty"]), ("2024-01-01", "2025-12-31"))

    def test_bez_dat_puste(self):
        f = self._form(fraza="RODO")
        self.assertEqual((f["odDaty"], f["doDaty"]), ("", ""))
        self.assertEqual(f["wszystkieSlowa"], "RODO")


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

    # strona CBOSA bez trafień: formularz z komunikatem, bez licznika „Znaleziono N"
    HTML_ZERO = """<form name="QueryForm" method="post" action="/cbo/search">
      <div class="warning"> Nie znaleziono orzeczeń spełniających podany warunek! </div>
      <div class="forma"><TABLE id="qt"></TABLE></div></form>"""
    HTML_ZLA_DATA = """<form name="QueryForm" method="post" action="/cbo/search">
      <div class="warning"> Niepoprawny format daty, podaj RRRR-MM-DD! </div>
      <div class="forma"><TABLE id="qt"></TABLE></div></form>"""

    def test_total_i_pozycje(self):
        total, poz, _ = cbosa._wyniki(self.HTML)
        self.assertEqual(total, 1878)
        self.assertEqual(len(poz), 2)

    def test_glowny_wynik(self):
        _, poz, _ = cbosa._wyniki(self.HTML)
        glowny = poz[0]
        self.assertEqual(glowny["doc_id"], "8889489BE0")
        self.assertFalse(glowny["powiazane"])
        self.assertIn("II FSK 2870/18", glowny["opis"])
        self.assertIn("6112", glowny["snippet"])

    def test_powiazane_oznaczone(self):
        _, poz, _ = cbosa._wyniki(self.HTML)
        self.assertTrue(poz[1]["powiazane"])
        self.assertEqual(poz[1]["doc_id"], "109C7D1883")

    def test_brak_trafien_to_zweryfikowane_zero(self):
        # regresja: bez tego każde puste wyszukiwanie wyglądało jak awaria serwera
        total, poz, komunikat = cbosa._wyniki(self.HTML_ZERO)
        self.assertEqual(total, 0)
        self.assertEqual(poz, [])
        self.assertIn("Nie znaleziono orzeczeń", komunikat)

    def test_blad_formularza_zwraca_komunikat(self):
        total, poz, komunikat = cbosa._wyniki(self.HTML_ZLA_DATA)
        self.assertIsNone(total)  # to nie zero — zapytanie w ogóle nie zostało wykonane
        self.assertEqual(poz, [])
        self.assertIn("Niepoprawny format daty", komunikat)

    def test_strona_z_wynikami_bez_komunikatu(self):
        self.assertEqual(cbosa._wyniki(self.HTML)[2], "")

    def test_pusty_html(self):
        # Strona bez rozpoznanego licznika/listy jest teraz UNKNOWN, nie cichym
        # sentinelem (None, [], "") - patch found/verified_absent/unknown.
        with self.assertRaises(cbosa.VerificationUnknown):
            cbosa._wyniki("<html><body>Brak wyników</body></html>")


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


class TestFetchPonowienia(unittest.TestCase):
    """Krótkie okna, w których CBOSA ucina połączenia, muszą przeżyć kilka ponowień."""

    def _bez_sieci(self, awarie):
        """Podmienia opener: pierwsze `awarie` prób pada, kolejna zwraca stronę. Zwraca licznik."""
        licznik = {"proby": 0, "spanie": []}

        class _Odpowiedz:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def read(self_inner):
                return b"<html>OK</html>"

        class _Opener:
            def open(self_inner, req, timeout=None):
                licznik["proby"] += 1
                if licznik["proby"] <= awarie:
                    raise OSError("Remote end closed connection without response")
                return _Odpowiedz()

        return licznik, _Opener()

    def _uruchom(self, awarie):
        licznik, opener = self._bez_sieci(awarie)
        oryg_opener, oryg_sleep = cbosa.urllib.request.build_opener, cbosa.time.sleep
        cbosa.urllib.request.build_opener = lambda *a, **k: opener
        cbosa.time.sleep = lambda s: licznik["spanie"].append(s)
        try:
            return licznik, cbosa._fetch("/cbo/search", data={"submit": "Szukaj"})
        finally:
            cbosa.urllib.request.build_opener, cbosa.time.sleep = oryg_opener, oryg_sleep

    def test_przezywa_kilkunastosekundowe_okno(self):
        licznik, html = self._uruchom(awarie=4)  # cztery zerwane połączenia z rzędu
        self.assertIn("OK", html)
        self.assertEqual(licznik["proby"], 5)
        self.assertGreaterEqual(sum(s for s in licznik["spanie"] if s > 0.5), 26)

    def test_trwala_awaria_konczy_bledem(self):
        # Trwala awaria transportu to teraz UNKNOWN (nie SystemExit) na poziomie
        # _fetch - main() dopiero mapuje to na polski komunikat i exit (patch
        # found/verified_absent/unknown, patrz VerificationUnknown ponizej).
        with self.assertRaises(cbosa.VerificationUnknown):
            self._uruchom(awarie=99)


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

    # audyt D3: okna cięte w środku słowa („kupno i sprz", „aluty tradycyjne") bez znacznika

    @staticmethod
    def _tekst(przed=40, po=120):
        slowa_przed = " ".join(f"wyraz{i:03d}" for i in range(przed))
        slowa_po = " ".join(f"slowo{i:03d}" for i in range(po))
        return f"{slowa_przed} INTERPRETACJA indywidualna {slowa_po}."

    def test_brzegi_na_granicy_wyrazu(self):
        txt = self._tekst()
        (s, e), = cbosa._fragmenty(txt, "interpretacja")
        self.assertGreater(s, 0)
        self.assertLess(e, len(txt))
        self.assertTrue(txt[s - 1].isspace(), f"początek w środku słowa: {txt[s - 5:s + 5]!r}")
        self.assertTrue(txt[e].isspace(), f"koniec w środku słowa: {txt[e - 5:e + 5]!r}")
        self.assertIn("INTERPRETACJA", txt[s:e])

    def test_brzegi_wola_granice_zdania(self):
        zdania = " ".join(f"Zdanie numer {i} ma kilka wyrazów i kończy się kropką." for i in range(6))
        txt = zdania + " Tu pada fraza KRYPTOWALUTA i dalej tekst. " + \
            " ".join(f"Dalsze zdanie {i} o niczym w szczególności." for i in range(30))
        (s, e), = cbosa._fragmenty(txt, "kryptowaluta")
        self.assertTrue(txt[s].isupper(), f"okno nie zaczyna się od zdania: {txt[s:s + 20]!r}")
        self.assertEqual(txt[e - 2], ".", f"okno nie kończy się zdaniem: {txt[e - 20:e]!r}")

    def test_skrot_nie_konczy_zdania(self):
        # „art.", „2018 r." — kropka po skrócie nie jest granicą zdania
        txt = "Wyrokiem z 6 czerwca 2018 r. sąd oddalił skargę na podstawie art. 151 p.p.s.a. Skarżący wniósł skargę.\nNaczelny Sąd"
        granice = cbosa._granice_zdan(txt, 0, len(txt))
        self.assertEqual([txt[g:g + 8] for g in granice], ["Skarżący", "Naczelny"])

    def test_fraza_zostaje_w_oknie_po_scaleniu(self):
        txt = self._tekst(po=5) + " " + self._tekst(przed=5, po=200)
        spans = cbosa._fragmenty(txt, "interpretacja")
        self.assertEqual(len(spans), 1)  # dwa bliskie trafienia → jedno okno
        s, e = spans[0]
        self.assertEqual(txt[s:e].upper().count("INTERPRETACJA"), 2)

    def test_wydruk_ma_znaczniki_uciecia(self):
        html = TestOrzeczenie.HTML.replace(
            "<P>1. Wyrokiem z 6 czerwca 2018 r.&nbsp;sąd oddalił skargę.</P>",
            "<P>" + self._tekst(przed=60, po=200) + "</P>")
        args = mock.Mock(doc_id="8889489BE0", json=False, strict=False, fragment="interpretacja")
        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value=html), redirect_stdout(out):
            cbosa.cmd_orzeczenie(args)
        okno = out.getvalue().split("## Uzasadnienie", 1)[1].split("\n")[1]
        self.assertTrue(okno.startswith("…"), okno[:40])
        self.assertTrue(okno.endswith(" …"), okno[-40:])
        self.assertRegex(okno, r"^…wyraz\d{3} ")  # pełne słowo tuż za znacznikiem
        self.assertRegex(okno, r" slowo\d{3} …$")
        self.assertIn("„…” oznacza ucięcie", out.getvalue())

    def test_okno_od_poczatku_bez_znacznika(self):
        html = TestOrzeczenie.HTML.replace("6 czerwca 2018 r.", "6 czerwca 2018 r. interpretacja")
        args = mock.Mock(doc_id="8889489BE0", json=False, strict=False, fragment="interpretacja")
        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value=html), redirect_stdout(out):
            cbosa.cmd_orzeczenie(args)
        okno = out.getvalue().split("## Uzasadnienie", 1)[1].split("\n")[1]
        self.assertFalse(okno.startswith("…"), okno)  # okno zaczyna się na początku tekstu
        self.assertFalse(okno.endswith("…"), okno)    # i sięga jego końca — nic nie ucięto


class TestPrawomocnosc(unittest.TestCase):
    """Audyt D1/D2: CBOSA oznacza każde orzeczenie „orzeczenie prawomocne / nieprawomocne" w komórce
    „Data orzeczenia" (zagnieżdżona tabela) — silnik gubił flagę, a --strict przepuszczał wyrok WSA
    uchylony przez NSA. Fragmenty HTML = żywe strony /doc/109C7D1883, /doc/8889489BE0 (2026-08)."""

    @staticmethod
    def _html(data, kursywa, sygnatura="I SA/Bk 226/18 - Wyrok WSA w Białymstoku z 2018-06-06",
              powiazane=True):
        pow_ = ('<tr class="niezaznaczona"><td class="info-list-label"><table class="noborder-tab"><tr>'
                '<td class="lista-label">Sygn. powiązane</td></tr></table></td><td class="info-list-value">'
                '<a href="/doc/8889489BE0">II FSK 2870/18 - Wyrok NSA z 2021-02-10</a></td></tr>'
                if powiazane else "")
        return f"""<TITLE>{sygnatura}</TITLE>
        <table class="info-list">
        <tr class="niezaznaczona"><td class="info-list-label"><table class="noborder-tab"><tr>
          <td class="lista-label">Data orzeczenia</td></tr></table></td>
          <td class="info-list-value"><table cellspacing=0 cellpadding=0 style="width: 100%">
            <tr><td>{data}</td>
            <td style="width: 50%; text-align: right; padding-right: 5px; font-style: italic;">{kursywa}</td>
            </tr></table></td></tr>
        <tr class="niezaznaczona"><td class="info-list-label"><table class="noborder-tab"><tr>
          <td class="lista-label">Sąd</td></tr></table></td>
          <td class="info-list-value">Wojewódzki Sąd Administracyjny w Białymstoku</td></tr>
        {pow_}
        <tr class="niezaznaczona"><td class="info-list-label"><table class="noborder-tab"><tr>
          <td class="lista-label">Treść wyniku</td></tr></table></td>
          <td class="info-list-value">Oddalono skargę</td></tr></table>
        <td class="info-list-label-uzasadnienie"><div class="lista-label">Uzasadnienie</div>
          <span class="info-list-value-uzasadnienie"><P>Sąd uznał, że skarga jest nieprawomocna
          w ocenie strony, ale to tekst uzasadnienia, nie oznaczenie.</P></span></td>"""

    NIEPRAWOMOCNE = _html.__func__("2018-06-06", "orzeczenie nieprawomocne")
    PRAWOMOCNE = _html.__func__("2021-02-10", "orzeczenie prawomocne",
                                sygnatura="II FSK 2870/18 - Wyrok NSA z 2021-02-10")
    # żywa strona /doc/1A8B8B8130 (postanowienie o wstrzymaniu wykonania, 2026-08-19): pole jest, ale puste
    PUSTE_POLE = _html.__func__("2026-08-19", "&nbsp;",
                                sygnatura="VI SA/Wa 707/26 - Postanowienie WSA w Warszawie z 2026-08-19",
                                powiazane=False)

    def _uruchom(self, html, argv):
        """→ (stdout, kod wyjścia); stderr (tam idą komunikaty, odkąd kody wyjścia są wspólne z saos.py) w self.err."""
        out, err = io.StringIO(), io.StringIO()
        kod = 0
        with mock.patch.object(cbosa, "_fetch", return_value=html), \
                mock.patch.object(sys, "argv", ["cbosa.py"] + argv), redirect_stdout(out), \
                redirect_stderr(err):
            try:
                cbosa.main()
            except SystemExit as e:
                kod = e.code
        self.err = err.getvalue()
        return out.getvalue(), kod

    def test_parser_nieprawomocne_i_czysta_data(self):
        d = cbosa._orzeczenie(self.NIEPRAWOMOCNE, "109C7D1883")
        self.assertIs(d["prawomocne"], False)
        self.assertEqual(d["prawomocnosc"], "orzeczenie nieprawomocne")
        self.assertEqual(d["metadane"]["Data orzeczenia"], "2018-06-06")  # data bez śmieci z kursywy
        self.assertEqual(d["metadane"]["Treść wyniku"], "Oddalono skargę")

    def test_parser_prawomocne(self):
        d = cbosa._orzeczenie(self.PRAWOMOCNE, "8889489BE0")
        self.assertIs(d["prawomocne"], True)
        self.assertEqual(d["prawomocnosc"], "orzeczenie prawomocne")

    def test_parser_puste_pole_to_none_nie_prawomocne(self):
        d = cbosa._orzeczenie(self.PUSTE_POLE, "1A8B8B8130")
        self.assertIsNone(d["prawomocne"])
        self.assertEqual(d["prawomocnosc"], "")  # pole jest, CBOSA nic nie wpisała

    def test_parser_brak_pola_to_none(self):
        d = cbosa._orzeczenie(TestOrzeczenie.HTML, "X")  # stary płaski układ bez kursywy
        self.assertIsNone(d["prawomocne"])
        self.assertIsNone(d["prawomocnosc"])

    def test_slowo_w_uzasadnieniu_nie_jest_oznaczeniem(self):
        html = self.PUSTE_POLE.replace("skarga jest nieprawomocna", "orzeczenie nieprawomocne w ocenie")
        self.assertIsNone(cbosa._orzeczenie(html, "X")["prawomocne"])

    def test_tekst_nieprawomocne_linia_po_dacie_i_glosna_uwaga(self):
        out, kod = self._uruchom(self.NIEPRAWOMOCNE, ["orzeczenie", "109C7D1883"])
        self.assertEqual(kod, 0)
        linie = out.splitlines()
        i = next(n for n, l in enumerate(linie) if l.startswith("  Data orzeczenia: 2018-06-06"))
        self.assertEqual(linie[i + 1], "  Prawomocność: NIEPRAWOMOCNE (oznaczenie CBOSA „orzeczenie nieprawomocne”) — zob. UWAGA wyżej")
        self.assertTrue(out.startswith("UWAGA: I SA/Bk 226/18 jest oznaczone w CBOSA jako ORZECZENIE NIEPRAWOMOCNE"))
        self.assertIn("uchylone", out)
        self.assertIn("II FSK 2870/18 - Wyrok NSA z 2021-02-10 → orzeczenie 8889489BE0", out)  # dokąd skoczyć

    def test_tekst_prawomocne_bez_uwagi(self):
        out, kod = self._uruchom(self.PRAWOMOCNE, ["orzeczenie", "8889489BE0"])
        self.assertEqual(kod, 0)
        self.assertIn("  Data orzeczenia: 2021-02-10\n  Prawomocność: prawomocne (oznaczenie CBOSA „orzeczenie prawomocne”)\n", out)
        self.assertNotIn("UWAGA", out)

    def test_tekst_puste_pole_ma_adnotacje(self):
        out, kod = self._uruchom(self.PUSTE_POLE, ["orzeczenie", "1A8B8B8130"])
        self.assertEqual(kod, 0)
        self.assertIn("Prawomocność: nieznana — CBOSA nie podaje", out)
        self.assertIn("UWAGA: VI SA/Wa 707/26 — CBOSA nie podaje prawomocności", out)

    def test_json_ma_pole_prawomocne(self):
        out, _ = self._uruchom(self.NIEPRAWOMOCNE, ["orzeczenie", "109C7D1883", "--json"])
        d = json.loads(out)
        self.assertIs(d["prawomocne"], False)
        self.assertEqual(d["prawomocnosc"], "orzeczenie nieprawomocne")
        self.assertIn("NIEPRAWOMOCNE", d["uwaga"])
        self.assertTrue(d["transport_tls_verified"])
        out, _ = self._uruchom(self.PRAWOMOCNE, ["--json", "orzeczenie", "8889489BE0"])
        d = json.loads(out)
        self.assertIs(d["prawomocne"], True)
        self.assertNotIn("uwaga", d)
        out, _ = self._uruchom(self.PUSTE_POLE, ["orzeczenie", "1A8B8B8130", "--json"])
        self.assertIsNone(json.loads(out)["prawomocne"])

    def test_strict_blokuje_nieprawomocne_przed_emisja(self):
        for argv in (["orzeczenie", "109C7D1883", "--strict"],
                     ["--strict", "orzeczenie", "109C7D1883"],
                     ["orzeczenie", "109C7D1883", "--json", "--strict"]):
            out, kod = self._uruchom(self.NIEPRAWOMOCNE, argv)
            self.assertEqual(out, "", argv)  # nic na stdout — także z --json
            self.assertEqual(kod, 2)
            self.assertIn("orzeczenie nieprawomocne — tryb strict nie zwraca treści, której aktualność "
                          "nie jest potwierdzona; bez --strict dostaniesz tekst z ostrzeżeniem", self.err)
            self.assertIn("[8889489BE0]", self.err)  # odsyła do wyroku NSA w tej sprawie
            self.assertNotIn("ponownie", self.err)   # to nie awaria przejściowa

    def test_strict_przepuszcza_prawomocne(self):
        out, kod = self._uruchom(self.PRAWOMOCNE, ["orzeczenie", "8889489BE0", "--strict"])
        self.assertEqual(kod, 0)
        self.assertIn("Prawomocność: prawomocne", out)
        out, kod = self._uruchom(self.PRAWOMOCNE, ["orzeczenie", "8889489BE0", "--strict", "--json"])
        self.assertEqual(kod, 0)
        self.assertIs(json.loads(out)["prawomocne"], True)

    def test_strict_blokuje_brak_oznaczenia(self):
        out, kod = self._uruchom(self.PUSTE_POLE, ["orzeczenie", "1A8B8B8130", "--strict"])
        self.assertEqual(out, "")
        self.assertEqual(kod, 2)
        self.assertIn("CBOSA nie podaje prawomocności", self.err)
        self.assertIn("bez --strict", self.err)
        out, kod = self._uruchom(TestOrzeczenie.HTML, ["orzeczenie", "8889489BE0", "--strict"])
        self.assertEqual(out, "")
        self.assertEqual(kod, 2)
        self.assertIn("nie znaleziono oznaczenia", self.err)

    def test_strict_nadal_sprawdza_tls(self):
        def fake_fetch(path, data=None):
            cbosa._transport_tls_verified = False
            return self.PRAWOMOCNE

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cbosa, "_fetch", side_effect=fake_fetch), \
                mock.patch.object(sys, "argv", ["cbosa.py", "orzeczenie", "8889489BE0", "--strict"]), \
                redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        cbosa._transport_tls_verified = True
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("TLS", err.getvalue())


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj", "fraza"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, cbosa.cmd_szukaj
        cbosa.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            cbosa.main()
        finally:
            sys.argv, cbosa.cmd_szukaj = oryg_argv, oryg_cmd
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


class CbosaVerificationContractTests(unittest.TestCase):
    """found/verified_absent/unknown - blad transportu nie moze wygladac jak potwierdzony brak."""

    def test_transport_error_is_unknown(self):
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError("offline")
        cbosa._ostatnie[0] = 0.0
        with mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(cbosa.time, "sleep"):
            with self.assertRaises(cbosa.VerificationUnknown):
                cbosa._fetch("/cbo/search")

    def test_no_results_warning_is_verified_absent(self):
        html = '<div class="warning">Nie znaleziono orzeczeń spełniających kryteria</div>'
        total, results, message = cbosa._wyniki(html)
        self.assertEqual(total, 0)
        self.assertEqual(results, [])
        self.assertIn("Nie znaleziono", message)

    def test_unrecognized_error_page_is_unknown(self):
        with self.assertRaises(cbosa.VerificationUnknown):
            cbosa._wyniki("<html><title>Service unavailable</title></html>")

    def test_http_410_na_doc_to_blad_identyfikatora(self):
        # CBOSA odpowiada 410 (Gone) dla nieistniejącego doc_id — to zły identyfikator, nie awaria;
        # komunikat nie może odsyłać w pętlę "spróbuj ponownie" (kod wyjścia 2: TestKodyWyjscia)
        err = urllib.error.HTTPError("https://orzeczenia.nsa.gov.pl/doc/DEADBEEF",
                                     410, "Gone", {}, None)
        opener = mock.Mock()
        opener.open.side_effect = err
        cbosa._ostatnie[0] = 0.0
        with mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(cbosa.time, "sleep"):
            with self.assertRaisesRegex(SystemExit, "nie ma orzeczenia o id 'DEADBEEF'") as caught:
                cbosa._fetch("/doc/DEADBEEF")
        self.assertNotIsInstance(caught.exception, cbosa.BrakWynikow)
        self.assertNotIn("ponownie", str(caught.exception))

    def test_http_410_na_wyszukiwarce_to_nadal_unknown(self):
        # 410/404 ma sens "brak dokumentu" tylko na /doc/{id}; na wyszukiwarce to awaria
        err = urllib.error.HTTPError("https://orzeczenia.nsa.gov.pl/cbo/search",
                                     410, "Gone", {}, None)
        opener = mock.Mock()
        opener.open.side_effect = err
        cbosa._ostatnie[0] = 0.0
        with mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(cbosa.time, "sleep"):
            with self.assertRaises(cbosa.VerificationUnknown):
                cbosa._fetch("/cbo/search")

    def test_t11_strict_blokuje_niezweryfikowany_transport_i_stdout_jest_pusty(self):
        def fake_fetch(path, data=None):
            cbosa._transport_tls_verified = False
            return TestOrzeczenie.HTML

        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", side_effect=fake_fetch), \
                mock.patch.object(sys, "argv", ["cbosa.py", "orzeczenie", "8889489BE0", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(out.getvalue(), "")

    def test_t12_json_strict_nie_emituje_wyniku_gdy_kontrola_nie_przeszla(self):
        out = io.StringIO()
        with mock.patch.object(cbosa, "_szukaj", side_effect=cbosa.VerificationUnknown("timeout")), \
                mock.patch.object(sys, "argv", ["cbosa.py", "szukaj", "RODO", "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(caught.exception.code, 2)

        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value="<html><body>Błąd</body></html>"), \
                mock.patch.object(sys, "argv", ["cbosa.py", "orzeczenie", "DEADBEEF", "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(caught.exception.code, 2)

        out = io.StringIO()
        with mock.patch.object(cbosa, "_szukaj", return_value=(0, [], "Nie znaleziono")), \
                mock.patch.object(sys, "argv", ["cbosa.py", "sygnatura", "II", "FSK", "1/24",
                                                       "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(caught.exception.code, 1)  # rozpoznane zero przy zweryfikowanym TLS


class TestT13PrzekierowaniaHttps(unittest.TestCase):
    def test_host_tresci_jest_podnoszony_a_obcy_host_odrzucany(self):
        req = cbosa.urllib.request.Request("https://orzeczenia.nsa.gov.pl/doc/8889489BE0")
        handler = cbosa._PrzekierowaniaHttps()
        nowy = handler.redirect_request(
            req, None, 302, "Found", {}, "http://orzeczenia.nsa.gov.pl/doc/8889489BE0")
        self.assertEqual(nowy.full_url, "https://orzeczenia.nsa.gov.pl/doc/8889489BE0")
        with self.assertRaisesRegex(urllib.error.URLError, "niezaufany host"):
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.test/doc/1")


class TestZgodaNaObnizenieTls(unittest.TestCase):
    """Downgrade TLS wymaga jawnej zgody: tresc CBOSA trafia do cytatow w pismach."""

    def setUp(self):
        cbosa._ostatnie[0] = 0.0
        cbosa._transport_tls_verified = True

    def tearDown(self):
        cbosa._transport_tls_verified = True

    class _Odpowiedz:
        def __init__(self, body=b"<html>OK</html>"):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self.body

    def _transport(self, zachowanie):
        """Buduje opener zależny od kontekstu HTTPS i zapisuje kontekst każdej próby."""
        proby = []

        def build_opener(*handlers):
            https = next(h for h in handlers if isinstance(h, cbosa.urllib.request.HTTPSHandler))
            context = https._context

            class _Opener:
                def open(self, req, timeout=None):
                    proby.append(context)
                    return zachowanie(len(proby), context)

            return _Opener()

        return proby, mock.patch.object(cbosa.urllib.request, "build_opener",
                                        side_effect=build_opener)

    @staticmethod
    def _blad_certyfikatu():
        return urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))

    def test_bez_zgody_konczy_sie_unknown(self):
        srodowisko = {k: v for k, v in os.environ.items() if k != "CBOSA_INSECURE_TLS"}
        proby, transport = self._transport(lambda nr, ctx: (_ for _ in ()).throw(
            self._blad_certyfikatu()))
        with mock.patch.dict(os.environ, srodowisko, clear=True), transport:
            with self.assertRaises(cbosa.VerificationUnknown) as ctx:
                cbosa._fetch("/doc/testowy")
        komunikat = str(ctx.exception)
        self.assertIn("certyfikat", komunikat.lower())
        self.assertIn("CBOSA_INSECURE_TLS", komunikat)
        self.assertEqual(len(proby), 1)

    def test_bez_zgody_nie_dotyka_kontekstu_ssl(self):
        srodowisko = {k: v for k, v in os.environ.items() if k != "CBOSA_INSECURE_TLS"}
        proby, transport = self._transport(lambda nr, ctx: (_ for _ in ()).throw(
            self._blad_certyfikatu()))
        with mock.patch.dict(os.environ, srodowisko, clear=True), transport:
            with self.assertRaises(cbosa.VerificationUnknown):
                cbosa._fetch("/doc/testowy")
        self.assertTrue(proby)
        self.assertTrue(all(ctx.verify_mode != ssl.CERT_NONE for ctx in proby))

    def test_opt_in_przelacza_kontekst_i_realnie_ponawia(self):
        def zachowanie(nr, context):
            if context.verify_mode != ssl.CERT_NONE:
                raise self._blad_certyfikatu()
            return self._Odpowiedz()

        proby, transport = self._transport(zachowanie)
        with mock.patch.dict(os.environ, {"CBOSA_INSECURE_TLS": "1"}, clear=True), \
                transport, mock.patch.object(cbosa.time, "sleep"), redirect_stderr(io.StringIO()):
            html = cbosa._fetch("/doc/testowy")
        self.assertIn("OK", html)
        self.assertEqual(len(proby), 2)
        self.assertNotEqual(proby[0].verify_mode, ssl.CERT_NONE)
        self.assertEqual(proby[1].verify_mode, ssl.CERT_NONE)
        self.assertIsNot(proby[0], proby[1])
        self.assertFalse(cbosa._transport_tls_verified)

    def test_blad_certyfikatu_w_ostatnim_obiegu_ma_dodatkowa_probe(self):
        def zachowanie(nr, context):
            if nr < 5:
                raise OSError("Remote end closed connection without response")
            if nr == 5:
                raise self._blad_certyfikatu()
            return self._Odpowiedz()

        proby, transport = self._transport(zachowanie)
        with mock.patch.dict(os.environ, {"CBOSA_INSECURE_TLS": "1"}, clear=True), \
                transport, mock.patch.object(cbosa.time, "sleep"), redirect_stderr(io.StringIO()):
            html = cbosa._fetch("/doc/testowy")
        self.assertIn("OK", html)
        self.assertEqual(len(proby), 6)
        self.assertTrue(all(ctx.verify_mode != ssl.CERT_NONE for ctx in proby[:5]))
        self.assertEqual(proby[5].verify_mode, ssl.CERT_NONE)

    def test_json_niesie_znacznik_nieuwierzytelnionego_transportu(self):
        body = TestOrzeczenie.HTML.encode("utf-8")

        def zachowanie(nr, context):
            if context.verify_mode != ssl.CERT_NONE:
                raise self._blad_certyfikatu()
            return self._Odpowiedz(body)

        proby, transport = self._transport(zachowanie)
        args = mock.Mock(doc_id="8889489BE0", json=True, strict=False, fragment=None)
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"CBOSA_INSECURE_TLS": "1"}, clear=True), \
                transport, mock.patch.object(cbosa.time, "sleep"), \
                redirect_stderr(io.StringIO()), redirect_stdout(stdout):
            cbosa.cmd_orzeczenie(args)
        wynik = json.loads(stdout.getvalue())
        self.assertFalse(wynik["transport_tls_verified"])
        self.assertEqual(len(proby), 2)

    def test_tekst_ma_adnotacje_przy_tresci(self):
        cbosa._transport_tls_verified = False
        args = mock.Mock(doc_id="8889489BE0", json=False, strict=False, fragment=None)
        stdout = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value=TestOrzeczenie.HTML), \
                redirect_stdout(stdout):
            cbosa.cmd_orzeczenie(args)
        wynik = stdout.getvalue()
        self.assertIn("transport TLS nie został zweryfikowany", wynik)
        self.assertLess(wynik.index("transport TLS"), wynik.index("# II FSK"))


class TestOrganIAnonimizacja(unittest.TestCase):
    """Regresja 2.1.1 (łańcuch UODO → CBOSA): `szukaj "DKN.5131.1.2025"` dawało „Brak wyników
    (zweryfikowane zero)”, choć WSA (II SA/Wa 837/25) uchylił pkt 2 tej decyzji — CBOSA anonimizuje
    numer decyzji („z dnia [...] marca 2025 r. nr [...]”). Wyrok znajduje dopiero --organ."""

    # strona CBOSA bez trafień — kształt z żywej odpowiedzi 2026-10-05
    HTML_ZERO = TestWyniki.HTML_ZERO

    def _uruchom(self, argv, html):
        """→ (stdout, stderr, atrapa _fetch); kod wyjścia w self.kod (komunikat idzie na stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value=html) as f, \
                mock.patch.object(sys, "argv", ["cbosa.py"] + argv), redirect_stdout(out), \
                redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.kod = caught.exception.code
        return out.getvalue(), err.getvalue(), f

    def test_znak_organu(self):
        for znak in ("DKN.5131.1.2025", "ZSPR.421.2.2019", "DS.523.1.2024", "ZSOŚS.421.25.2019"):
            self.assertEqual(cbosa._znak_organu(znak), znak)
        self.assertEqual(cbosa._znak_organu(None, "decyzja DKN.5131.1.2025 kara"), "DKN.5131.1.2025")
        for nie in ("II SA/Wa 837/25", "III OSK 377/23", "administracyjnej kary", "Dz.U. 2019", ""):
            self.assertIsNone(cbosa._znak_organu(nie), nie)

    def test_zero_dla_znaku_organu_to_nie_zweryfikowane_zero(self):
        out, msg, _ = self._uruchom(["szukaj", "DKN.5131.1.2025"], self.HTML_ZERO)
        self.assertEqual(out, "")
        self.assertEqual(self.kod, 2)  # CHANGELOG 2.2.0: to celowo NIE jest zweryfikowane zero
        self.assertNotIn("zweryfikowane zero", msg)
        self.assertIn("NIE dowodzi braku wyroku", msg)
        self.assertIn("nr [...]", msg)
        self.assertIn('--organ UODO --symbol 647 --sad "WSA Warszawa"', msg)
        self.assertIn("--od 2025-01-01", msg)

    def test_zero_dla_sygnatury_ze_znakiem_organu(self):
        _, msg, _ = self._uruchom(["sygnatura", "ZSPR.421.2.2019"], self.HTML_ZERO)
        self.assertEqual(self.kod, 2)
        self.assertIn("anonimizuje", msg)
        self.assertNotIn("Sygnatury sądów administracyjnych mają formę", msg)

    def test_zwykla_fraza_nadal_zweryfikowane_zero(self):
        _, msg, _ = self._uruchom(["szukaj", "jakaś fraza"], self.HTML_ZERO)
        self.assertEqual(self.kod, 1)
        self.assertIn("zweryfikowane zero", msg)

    def test_organ_trafia_do_formularza(self):
        _, _, f = self._uruchom(["szukaj", "--organ", "UODO", "--od", "2025-03-17"], self.HTML_ZERO)
        form = f.call_args.kwargs["data"]
        self.assertEqual(form["rodzaj_organu"], "Ochrony Danych Osobowych")
        self.assertEqual(form["odDaty"], "2025-03-17")

    def test_alias_i_pelna_nazwa(self):
        self.assertEqual(cbosa._organ("uodo"), "Ochrony Danych Osobowych")
        self.assertEqual(cbosa._organ("GIODO"), "Ochrony Danych Osobowych")
        self.assertEqual(cbosa._organ(" Prezes  Urzędu Ochrony Konkurencji i Konsumentów "),
                         "Prezes Urzędu Ochrony Konkurencji i Konsumentów")
        self.assertEqual(cbosa._organ(None), "")
        self.assertEqual(TestFormularz()._form(fraza="RODO")["rodzaj_organu"], "")

    def test_samo_organ_jest_kryterium(self):
        _, msg, _ = self._uruchom(["szukaj", "--organ", "UODO"], self.HTML_ZERO)
        self.assertNotIn("Podaj kryterium", msg)


class TestDiagnozaBleduSieci(unittest.TestCase):
    """Błąd sieci ≠ „serwer CBOSA ucina połączenia": proxy piaskownicy, brak DNS/sieci, awaria
    serwera i timeout dostają osobną diagnozę i poradę (dawniej każdy był „winą CBOSA")."""

    def _fetch(self, blad, proxy=None):
        opener = mock.Mock()
        opener.open.side_effect = blad
        spanie = []
        cbosa._ostatnie[0] = 0.0
        with mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(cbosa.urllib.request, "getproxies", return_value=proxy or {}), \
                mock.patch.object(cbosa.time, "sleep", side_effect=spanie.append):
            with self.assertRaises(cbosa.VerificationUnknown) as caught:
                cbosa._fetch("/cbo/search", data={"submit": "Szukaj"})
        return caught.exception, opener.open.call_count, sum(s for s in spanie if s > 0.5)

    def test_proxy_odrzuca_tunel_to_blokada_srodowiska_bez_ponawiania(self):
        e, proby, czekanie = self._fetch(urllib.error.URLError(
            OSError("Tunnel connection failed: 403 Forbidden")))
        self.assertIn("blokada środowiska", str(e))
        self.assertNotIn("ucina", str(e))
        self.assertIn("Ponawianie nic nie da", e.porada)
        self.assertEqual(proby, 1)
        self.assertEqual(czekanie, 0)

    def test_brak_dns_to_brak_sieci_z_jedna_krotka_proba(self):
        e, proby, czekanie = self._fetch(urllib.error.URLError(
            socket.gaierror(8, "nodename nor servname provided, or not known")))
        self.assertIn("brak sieci w tym środowisku", str(e))
        self.assertNotIn("ucina", str(e))
        self.assertIn("nie serwera CBOSA", e.porada)
        self.assertEqual(proby, 2)
        self.assertLess(czekanie, 5)

    def test_polaczenie_odrzucone_bez_proxy_to_brak_sieci(self):
        e, _, _ = self._fetch(urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")))
        self.assertIn("brak sieci", str(e))

    def test_polaczenie_odrzucone_przy_proxy_to_srodowisko(self):
        # przy ustawionym HTTPS_PROXY łączymy się z proxy, nie z CBOSA
        e, proby, _ = self._fetch(urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
                                  proxy={"https": "http://127.0.0.1:9"})
        self.assertIn("blokada środowiska", str(e))
        self.assertEqual(proby, 1)

    def test_zerwane_polaczenie_to_awaria_serwera_po_26_s(self):
        for blad in (http.client.RemoteDisconnected("Remote end closed connection without response"),
                     urllib.error.URLError(ConnectionResetError(54, "Connection reset by peer")),
                     urllib.error.URLError(BrokenPipeError(32, "Broken pipe")),
                     urllib.error.URLError(ssl.SSLEOFError(8, "EOF occurred in violation of protocol"))):
            with self.subTest(blad=repr(blad)):
                e, proby, czekanie = self._fetch(blad)
                self.assertIn("awaria po stronie serwera CBOSA", str(e))
                self.assertIn("kilka–kilkanaście minut", e.porada)
                self.assertRegex(e.porada, r"sprawdzono o \d\d:\d\d")
                self.assertEqual(proby, 5)
                self.assertGreaterEqual(czekanie, 26)

    def test_http_5xx_to_awaria_serwera(self):
        err = urllib.error.HTTPError("https://orzeczenia.nsa.gov.pl/cbo/search", 503,
                                     "Service Unavailable", {}, None)
        e, proby, czekanie = self._fetch(err)
        self.assertIn("awaria po stronie serwera CBOSA", str(e))
        self.assertIn("HTTP 503", str(e))
        self.assertIn("21:00", e.porada)
        self.assertEqual(proby, 5)
        self.assertGreaterEqual(czekanie, 26)

    def test_timeout_opisany_uczciwie(self):
        e, proby, _ = self._fetch(urllib.error.URLError(socket.timeout("timed out")))
        self.assertIn("serwer CBOSA odpowiada wolno albo łącze jest niestabilne", str(e))
        self.assertEqual(proby, 5)

    def test_main_podaje_porade_zamiast_ogolnika(self):
        blad = cbosa.VerificationUnknown("blokada środowiska", porada="PORADA-X")
        err = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", side_effect=blad), \
                mock.patch.object(sys, "argv", ["cbosa.py", "szukaj", "RODO"]), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("PORADA-X", err.getvalue())
        self.assertNotIn("za chwilę", err.getvalue())

    def test_main_bez_porady_zostaje_ogolnik(self):
        err = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", side_effect=cbosa.VerificationUnknown("x")), \
                mock.patch.object(sys, "argv", ["cbosa.py", "szukaj", "RODO"]), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("Spróbuj ponownie za chwilę", err.getvalue())


class TestBladWyszukiwarki(unittest.TestCase):
    """2026-10-05: w czasie awarii CBOSA wyszukiwarka zwraca „Błąd przy szukaniu orzeczeń" — to błąd
    serwera (UNKNOWN z poradą ponowienia), nie „CBOSA odrzuciło zapytanie"."""

    def test_blad_przy_szukaniu_to_awaria_serwera(self):
        strona = '<html><body><div class="warning">Błąd przy szukaniu orzeczeń</div></body></html>'
        with self.assertRaises(cbosa.VerificationUnknown) as ctx:
            cbosa._wyniki(strona)
        self.assertIn("Błąd przy szukaniu orzeczeń", str(ctx.exception))
        self.assertIn("ponów to samo zapytanie", ctx.exception.porada)

    def test_niepoprawna_data_nadal_odrzucona(self):
        strona = '<html><body><div class="warning">Niepoprawny format daty</div></body></html>'
        self.assertEqual(cbosa._wyniki(strona), (None, [], "Niepoprawny format daty"))


def _uruchom_main(argv, fetch=None, opener_blad=None, env=None):
    """cbosa.main() offline → (kod wyjścia, stdout, stderr); kod 0, gdy main() wrócił normalnie.

    fetch: atrapa _fetch — str (stała strona), lista stron / funkcja (side_effect) albo wyjątek.
    opener_blad: zamiast atrapy _fetch prawdziwy _fetch z openerem rzucającym ten wyjątek (ścieżka
    przez _ponow / _blad_transportu); sleep i proxy podmienione, więc bez czekania i bez sieci."""
    out, err = io.StringIO(), io.StringIO()
    latki = [mock.patch.object(sys, "argv", ["cbosa.py", *argv]), mock.patch.object(cbosa.time, "sleep"),
             redirect_stdout(out), redirect_stderr(err)]
    if fetch is not None:
        kw = {"return_value": fetch} if isinstance(fetch, str) else {"side_effect": fetch}
        latki.append(mock.patch.object(cbosa, "_fetch", **kw))
    if opener_blad is not None:
        opener = mock.Mock()
        opener.open.side_effect = opener_blad
        latki += [mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener),
                  mock.patch.object(cbosa.urllib.request, "getproxies", return_value={})]
    if env is not None:
        latki.append(mock.patch.dict(os.environ, env, clear=True))
    cbosa._ostatnie[0] = 0.0
    kod = 0
    with contextlib.ExitStack() as stos:
        for latka in latki:
            stos.enter_context(latka)
        try:
            cbosa.main()
        except SystemExit as e:
            kod = e.code
    cbosa._transport_tls_verified = True
    return kod, out.getvalue(), err.getvalue()


class TestKodyWyjscia(unittest.TestCase):
    """0 = wynik, 1 = ZWERYFIKOWANY brak, 2 = wszystko inne (jak saos.py i uodo.py). Do 2.4.0 włącznie każde
    sys.exit("tekst") w cbosa.py dawało 1 — awaria serwera („Błąd przy szukaniu orzeczeń”), zły
    argument, nieistniejący doc_id i blokada --strict wyglądały jak „nie ma takiego orzeczenia”."""

    WYNIKI = TestWyniki.HTML
    ZERO = TestWyniki.HTML_ZERO
    ZLA_DATA = TestWyniki.HTML_ZLA_DATA
    PRAWOMOCNE = TestPrawomocnosc.PRAWOMOCNE
    NIEPRAWOMOCNE = TestPrawomocnosc.NIEPRAWOMOCNE
    PUSTE_POLE = TestPrawomocnosc.PUSTE_POLE
    # strona wyników z licznikiem, ale bez żadnej pozycji (np. --strona za końcem, zmiana układu listy)
    LICZNIK_BEZ_POZYCJI = '<table class="top-linki"><tr><td>Znaleziono 2 orzeczeń, Str. 3 z 1</td></tr></table>'
    # strona /doc/{id} bez sekcji „Uzasadnienie" (prawomocne, sama sentencja)
    BEZ_UZASADNIENIA = PRAWOMOCNE[:PRAWOMOCNE.index('<td class="info-list-label-uzasadnienie">')] + \
        '<td class="info-list-label-uzasadnienie"><div class="lista-label">Sentencja</div>' \
        '<span class="info-list-value-uzasadnienie"><P>NSA oddala skargę kasacyjną.</P></span></td>'

    def _kod(self, oczekiwany, argv, **kw):
        kod, out, err = _uruchom_main(argv, **kw)
        self.assertEqual(kod, oczekiwany, f"{argv}: stderr={err!r}")
        if oczekiwany != 0:
            self.assertEqual(out, "", f"{argv}: przy kodzie {kod} stdout musi być pusty")
            self.assertTrue(err.strip(), f"{argv}: komunikat musi trafić na stderr")
        return out, err

    # ── 0: jest wynik ──
    def test_wynik_to_0(self):
        for argv, strona in ((["szukaj", "RODO"], self.WYNIKI), (["szukaj", "RODO", "--json"], self.WYNIKI),
                             (["sygnatura", "II", "FSK", "2870/18"], self.WYNIKI),
                             (["--json", "sygnatura", "II FSK 2870/18"], self.WYNIKI),
                             (["orzeczenie", "8889489BE0"], self.PRAWOMOCNE),
                             (["orzeczenie", "8889489BE0", "--json"], self.PRAWOMOCNE),
                             (["orzeczenie", "8889489BE0", "--fragment", "tekst uzasadnienia"], self.PRAWOMOCNE),
                             (["orzeczenie", "8889489BE0"], self.BEZ_UZASADNIENIA)):
            with self.subTest(argv=argv):
                out, _ = self._kod(0, argv, fetch=strona)
                self.assertTrue(out.strip())
        out, _ = self._kod(0, ["sygnatura", "II FSK 2870/18", "--json"], fetch=self.WYNIKI)
        self.assertEqual(json.loads(out)["wyniki"][0]["doc_id"], "8889489BE0")

    # ── 1: zweryfikowany brak ──
    def test_zero_szukaj_i_sygnatura_to_1_takze_z_json(self):
        for argv in (["szukaj", "zzz-nie-ma"], ["szukaj", "zzz-nie-ma", "--json"],
                     ["--json", "szukaj", "--sad", "NSA", "--od", "2025-01-01"],
                     ["szukaj", "zzz-nie-ma", "--strona", "2"],
                     ["sygnatura", "II", "FSK", "99999/18"], ["sygnatura", "II FSK 99999/18", "--json"],
                     ["sygnatura", "II FSK 99999/18", "--strict"]):
            with self.subTest(argv=argv):
                _, err = self._kod(1, argv, fetch=self.ZERO)
                self.assertRegex(err, r"Brak wyników \(zweryfikowane zero\)|Nie znaleziono orzeczenia o sygnaturze")

    def test_brak_frazy_w_uzasadnieniu_to_1(self):
        _, err = self._kod(1, ["orzeczenie", "109C7D1883", "--fragment", "zzz-nie-ma"], fetch=self.NIEPRAWOMOCNE)
        self.assertIn("Nie znaleziono frazy 'zzz-nie-ma' w uzasadnieniu", err)

    def test_json_z_fragmentem_zwraca_pelny_json_jak_saos(self):
        # --json ignoruje --fragment (tak samo w saos.py): pełny zrzut, kod 0
        out, _ = self._kod(0, ["orzeczenie", "8889489BE0", "--json", "--fragment", "zzz-nie-ma"],
                           fetch=self.PRAWOMOCNE)
        self.assertIn("uzasadnienie", json.loads(out))

    def test_zero_przy_niezweryfikowanym_tls_to_1_z_adnotacja_a_strict_2(self):
        def strona_bez_tls(path, data=None):
            cbosa._transport_tls_verified = False
            return self.ZERO
        _, err = self._kod(1, ["szukaj", "zzz-nie-ma"], fetch=strona_bez_tls)
        self.assertIn("transport TLS nie został zweryfikowany", err)
        _, err = self._kod(2, ["szukaj", "zzz-nie-ma", "--strict"], fetch=strona_bez_tls)
        self.assertIn("tryb strict odrzuca wynik pobrany bez weryfikacji certyfikatu TLS", err)

    # ── 2: błędy wejścia ──
    def test_zle_argumenty_to_2(self):
        for argv, fraza in ((["szukaj", "--sad", "kosmos"], "Nieznany sąd"),
                            (["szukaj", "--rodzaj", "apelacja"], "Nieznany rodzaj"),
                            (["szukaj", "--od", "2024-13-01"], "Nieprawidłowa data"),
                            (["szukaj", "--od", "1.1.24"], "Nieprawidłowa data"),
                            (["szukaj"], "Podaj kryterium"),
                            (["orzeczenie", "nie-hex!"], "Nieprawidłowy doc_id")):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=self.WYNIKI)
                self.assertIn(fraza, err)

    def test_argparse_to_2_a_version_0(self):
        self._kod(2, ["szukaj", "x", "--strona", "dwa"])
        self._kod(2, ["nieznana-komenda"])
        out, _ = self._kod(0, ["--version"])
        self.assertIn(cbosa.__version__, out)

    # ── 2: strona CBOSA, która nie potwierdza braku ──
    def test_odrzucone_zapytanie_to_2(self):
        for argv in (["szukaj", "x"], ["sygnatura", "II FSK 1/18"]):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=self.ZLA_DATA)
                self.assertIn("CBOSA odrzuciło zapytanie", err)

    def test_blad_przy_szukaniu_i_strona_bez_licznika_to_2(self):
        blad = '<html><body><div class="warning">Błąd przy szukaniu orzeczeń</div></body></html>'
        for argv in (["szukaj", "x"], ["sygnatura", "II FSK 1/18"], ["szukaj", "x", "--strona", "2"]):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=blad)
                self.assertIn("ponów to samo zapytanie", err)
                self.assertIn("To NIE jest brak wyników", err)
                _, err = self._kod(2, argv, fetch="<html><body>Service unavailable</body></html>")
                self.assertIn("bez licznika i listy wyników", err)

    def test_licznik_bez_pozycji_to_2(self):
        # --strona za końcem wyników: dalsza strona pusta albo z „Nie znaleziono” — licznik z pierwszej
        for dalsza in (self.LICZNIK_BEZ_POZYCJI, self.ZERO):
            with self.subTest(dalsza=dalsza[:40]):
                _, err = self._kod(2, ["szukaj", "RODO", "--strona", "500"], fetch=[self.WYNIKI, dalsza])
                self.assertIn("NIE jest zweryfikowany brak", err)
                self.assertIn("1878", err)
        _, err = self._kod(2, ["szukaj", "RODO"], fetch=self.LICZNIK_BEZ_POZYCJI)
        self.assertIn("NIE jest zweryfikowany brak", err)
        _, err = self._kod(2, ["sygnatura", "II FSK 1/18"], fetch=self.LICZNIK_BEZ_POZYCJI)
        self.assertIn("NIE jest zweryfikowany brak", err)

    def test_numer_decyzji_organu_to_2_nie_zweryfikowane_zero(self):
        for argv in (["szukaj", "DKN.5131.1.2025"], ["szukaj", "kara DKN.5131.1.2025", "--json"],
                     ["sygnatura", "ZSPR.421.2.2019"]):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=self.ZERO)
                self.assertIn("NIE dowodzi braku wyroku", err)

    def test_orzeczenie_bledy_to_2(self):
        _, err = self._kod(2, ["orzeczenie", "DEADBEEF"], fetch="<html><body>Błąd</body></html>")
        self.assertIn("nie udało się rozpoznać strony orzeczenia", err)
        _, err = self._kod(2, ["orzeczenie", "8889489BE0", "--fragment", "skarga"], fetch=self.BEZ_UZASADNIENIA)
        self.assertIn("To NIE jest brak frazy", err)

    def test_strict_to_2(self):
        for argv, strona, fraza in ((["orzeczenie", "109C7D1883", "--strict"], self.NIEPRAWOMOCNE, "nieprawomocne"),
                                    (["orzeczenie", "109C7D1883", "--strict", "--json"], self.NIEPRAWOMOCNE, "nieprawomocne"),
                                    (["orzeczenie", "1A8B8B8130", "--strict"], self.PUSTE_POLE, "nie podaje prawomocności")):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=strona)
                self.assertIn(fraza, err)

        def bez_tls(path, data=None):
            cbosa._transport_tls_verified = False
            return self.WYNIKI
        for argv in (["szukaj", "RODO", "--strict"], ["sygnatura", "II FSK 2870/18", "--strict", "--json"]):
            with self.subTest(argv=argv):
                _, err = self._kod(2, argv, fetch=bez_tls)
                self.assertIn("bez weryfikacji certyfikatu TLS", err)

    def test_nieprzewidziany_wyjatek_to_2(self):
        _, err = self._kod(2, ["szukaj", "RODO"], fetch=KeyError("opis"))
        self.assertIn("nieoczekiwany błąd helpera cbosa.py (KeyError('opis'))", err)
        self.assertIn("To NIE jest brak wyników", err)

    # ── 2: transport — prawdziwy _fetch, każda diagnoza _blad_transportu ──
    def test_bledy_transportu_to_2(self):
        przypadki = [
            (urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")), "blokada środowiska"),
            (urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")), "brak sieci"),
            (http.client.RemoteDisconnected("Remote end closed connection without response"), "awaria po stronie serwera"),
            (urllib.error.URLError(socket.timeout("timed out")), "przekroczony czas oczekiwania"),
            (urllib.error.URLError(ValueError("coś innego")), "błąd sieci"),
            (urllib.error.HTTPError(cbosa.BASE + "/cbo/search", 503, "Service Unavailable", {}, None), "HTTP 503"),
            (urllib.error.HTTPError(cbosa.BASE + "/cbo/search", 403, "Forbidden", {}, None), "HTTP 403"),
        ]
        for blad, fraza in przypadki:
            for argv in (["szukaj", "RODO"], ["sygnatura", "II FSK 2870/18", "--json"], ["orzeczenie", "8889489BE0"]):
                with self.subTest(blad=repr(blad), argv=argv):
                    _, err = self._kod(2, argv, opener_blad=blad)
                    self.assertIn(fraza, err)
                    self.assertIn("To NIE jest brak wyników", err)

    def test_nieistniejacy_doc_id_to_2(self):
        gone = urllib.error.HTTPError(cbosa.BASE + "/doc/DEADBEEF", 410, "Gone", {}, None)
        _, err = self._kod(2, ["orzeczenie", "DEADBEEF"], opener_blad=gone)
        self.assertIn("nie ma orzeczenia o id 'DEADBEEF'", err)
        self.assertNotIn("ponownie", err)

    def test_blad_certyfikatu_bez_zgody_to_2(self):
        blad = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        bez_zgody = {k: v for k, v in os.environ.items() if k != "CBOSA_INSECURE_TLS"}
        _, err = self._kod(2, ["szukaj", "RODO"], opener_blad=blad, env=bez_zgody)
        self.assertIn("CBOSA_INSECURE_TLS=1", err)


def _lista(total, opisy):
    """Strona wyników CBOSA w kształcie TestWyniki.HTML: licznik + pozycje (doc_id, opis)."""
    wiersze = "".join(
        f'<tr class="niezaznaczona"><td class="info-list-value " style="font-size: 12pt; border: none;">'
        f'<a href="/doc/{doc}"  >{opis}</a></td></tr>'
        f'<tr class="niezaznaczona"><td class="info-list-value " style="font-size: 10pt; border: none;">'
        f'Oddalono skargę...</td></tr>' for doc, opis in opisy)
    return (f'<table class="top-linki"><tr><td>Znaleziono {total} orzeczeń, Str. 1 z {-(-total // 10)}</td></tr>'
            f'</table><table class="info-list">{wiersze}</table>')


class TestKolizjeSygnatur(unittest.TestCase):
    """Audyt 13.11.2026 (raport, „signature collisions”): CBOSA dopasowuje pole sygnatury po członach —
    `sygnatura "SA/Wa 1/20"` dało na żywo 8 różnych spraw z wydziałów I–VIII WSA w Warszawie, a
    „II SA/Wa 2559/19” i „OSK 150/04” — po dwa orzeczenia tej samej sprawy. Silnik pokazywał
    wszystkie, ale wskazywał pierwsze jako „Pełna treść” bez słowa, że to różne sprawy."""

    OSIEM = _lista(8, [("216497BE0A", "VII SA/Wa 1/20 - Wyrok WSA w Warszawie z 2020-10-06"),
                       ("35CDBFABDE", "II SA/Wa 1/20 - Postanowienie WSA w Warszawie z 2020-10-01"),
                       ("809FA03771", "V SA/Wa 1/20 - Wyrok WSA w Warszawie z 2020-02-05")])
    DWA = _lista(2, [("2F881CED73", "II SA/Wa 2559/19 - Wyrok WSA w Warszawie z 2020-09-03"),
                     ("6E19266905", "II SA/Wa 2559/19 - Postanowienie WSA w Warszawie z 2020-02-13")])
    JEDEN = _lista(1, [("30EDD316DA", "II SA/Wa 1030/19 - Wyrok WSA w Warszawie z 2019-12-11")])
    DUZO = _lista(37, [(f"ABCDEF{i:04d}", f"I OSK {i}/20 - Wyrok NSA z 2021-01-01") for i in range(1, 11)])

    def test_rozne_sprawy_ostrzezenie_i_bez_wskazania_pierwszej(self):
        kod, out, _ = _uruchom_main(["sygnatura", "SA/Wa", "1/20"], fetch=self.OSIEM)
        self.assertEqual(kod, 0)
        self.assertIn("trafienia mają różne sygnatury (3), czyli dotyczą różnych spraw: VII SA/Wa 1/20, "
                      "II SA/Wa 1/20, V SA/Wa 1/20", out)
        self.assertIn("pokazano 3 z 8 trafień", out)
        self.assertNotIn("Pełna treść: orzeczenie 216497BE0A", out)
        self.assertIn("Pełna treść: orzeczenie <doc_id> — wybierz z listy", out)

    def test_ta_sama_sygnatura_kilka_orzeczen(self):
        kod, out, _ = _uruchom_main(["sygnatura", "II SA/Wa 2559/19"], fetch=self.DWA)
        self.assertEqual(kod, 0)
        self.assertIn("ma w CBOSA kilka orzeczeń (2), np. wyrok i postanowienie", out)
        self.assertNotIn("różne sygnatury", out)
        self.assertIn("[6E19266905]  II SA/Wa 2559/19 - Postanowienie", out)  # oba widoczne

    def test_jedno_trafienie_bez_uwag(self):
        kod, out, _ = _uruchom_main(["sygnatura", "ii  sa/wa 1030/19"], fetch=self.JEDEN)  # wielkość liter, spacje
        self.assertEqual(kod, 0)
        self.assertNotIn("UWAGA", out)
        self.assertIn("Pełna treść: orzeczenie 30EDD316DA", out)

    def test_ponad_dziesiec_trafien_podaje_kolejna_strone(self):
        kod, out, _ = _uruchom_main(["sygnatura", "OSK"], fetch=self.DUZO)
        self.assertEqual(kod, 0)
        self.assertIn('pokazano 10 z 37 trafień (pierwsza strona wyników CBOSA). Kolejne: szukaj --sygnatura "OSK" --strona 2', out)

    def test_fragment_sygnatury_z_jednym_trafieniem_nie_udaje_roznych_spraw(self):
        jedno = _lista(1, [("8889489BE0", "II FSK 2870/18 - Wyrok NSA z 2021-02-10")])
        kod, out, _ = _uruchom_main(["sygnatura", "FSK 2870/18"], fetch=jedno)
        self.assertEqual(kod, 0)
        self.assertNotIn("różne sygnatury", out)
        self.assertIn("CBOSA dopasowała zapytanie 'FSK 2870/18' do sygnatury 'II FSK 2870/18'", out)
        self.assertIn("Pełna treść: orzeczenie 8889489BE0", out)

    def test_json_uwagi_na_stderr_stdout_czysty(self):
        kod, out, err = _uruchom_main(["sygnatura", "SA/Wa 1/20", "--json"], fetch=self.OSIEM)
        self.assertEqual(kod, 0)
        self.assertEqual(len(json.loads(out)["wyniki"]), 3)
        self.assertIn("różne sygnatury", err)


class TestPrawomocnyWyrokWsaZWyrokiemNsa(unittest.TestCase):
    """Audyt 13.11.2026: II SA/Wa 1341/22 ma w CBOSA „orzeczenie prawomocne”, choć NSA (III OSK 3311/23)
    uchylił ten wyrok i decyzję; II SA/Wa 2559/19 i II SA/Wa 863/23 w tej samej sytuacji są
    „nieprawomocne”. Wyrok WSA z powiązanym wyrokiem NSA dostaje więc uwagę także przy „prawomocne”."""

    WSA = TestPrawomocnosc._html("2022-10-28", "orzeczenie prawomocne",
                                 sygnatura="II SA/Wa 1341/22 - Wyrok WSA w Warszawie z 2022-10-28")

    def test_tekst_i_json_maja_uwage(self):
        kod, out, _ = _uruchom_main(["orzeczenie", "AE3EE2C6FD"], fetch=self.WSA)
        self.assertEqual(kod, 0)
        self.assertTrue(out.startswith("UWAGA: CBOSA wiąże z orzeczeniem II SA/Wa 1341/22 wyrok NSA "
                                       "(II FSK 2870/18 - Wyrok NSA z 2021-02-10 → orzeczenie 8889489BE0)"))
        self.assertIn("NIE wyklucza, że NSA je uchylił", out)
        self.assertIn("Prawomocność: prawomocne (oznaczenie CBOSA „orzeczenie prawomocne”) — zob. UWAGA wyżej", out)
        kod, out, _ = _uruchom_main(["orzeczenie", "AE3EE2C6FD", "--json"], fetch=self.WSA)
        d = json.loads(out)
        self.assertIs(d["prawomocne"], True)
        self.assertIn("NIE wyklucza", d["uwaga"])

    def test_strict_nadal_przepuszcza(self):
        # --strict blokuje tylko „nieprawomocne” i brak oznaczenia; tę uwagę opisuje SKILL.md
        kod, out, _ = _uruchom_main(["orzeczenie", "AE3EE2C6FD", "--strict"], fetch=self.WSA)
        self.assertEqual(kod, 0)
        self.assertIn("Tryb --strict tego nie sprawdza", out)

    def test_bez_uwagi_dla_nsa_oz_sprzed_2004(self):
        # „SA/Sz 833/03” to sygnatura NSA oz. w Szczecinie (sprzed reformy), nie WSA
        nsa_oz = TestPrawomocnosc._html("2003-12-17", "orzeczenie prawomocne",
                                        sygnatura="SA/Sz 833/03 - Wyrok NSA oz. w Szczecinie z 2003-12-17")
        _, out, _ = _uruchom_main(["orzeczenie", "2D8D134608"], fetch=nsa_oz)
        self.assertNotIn("UWAGA", out)

    def test_bez_uwagi_dla_nsa_i_wsa_bez_wyroku_nsa(self):
        _, out, _ = _uruchom_main(["orzeczenie", "8889489BE0"], fetch=TestPrawomocnosc.PRAWOMOCNE)
        self.assertNotIn("UWAGA", out)  # sam wyrok NSA
        bez = TestPrawomocnosc._html("2022-10-28", "orzeczenie prawomocne", powiazane=False,
                                     sygnatura="II SA/Wa 1341/22 - Wyrok WSA w Warszawie z 2022-10-28")
        _, out, _ = _uruchom_main(["orzeczenie", "AE3EE2C6FD"], fetch=bez)
        self.assertNotIn("UWAGA", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
