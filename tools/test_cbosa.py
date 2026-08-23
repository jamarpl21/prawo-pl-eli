#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for cbosa.py pure functions (no network). Run: python3 tools/test_cbosa.py"""
import os
import io
import json
import sys
import importlib.util
import pathlib
import ssl
import unittest
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
        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value=html), \
                mock.patch.object(sys, "argv", ["cbosa.py"] + argv), redirect_stdout(out):
            try:
                cbosa.main()
            except SystemExit as e:
                return out.getvalue(), e.code
        return out.getvalue(), 0

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
            self.assertNotEqual(kod, 0)
            self.assertIn("orzeczenie nieprawomocne — tryb strict nie zwraca treści, której aktualność "
                          "nie jest potwierdzona; bez --strict dostaniesz tekst z ostrzeżeniem", str(kod))
            self.assertIn("[8889489BE0]", str(kod))  # odsyła do wyroku NSA w tej sprawie
            self.assertNotIn("ponownie", str(kod))   # to nie awaria przejściowa

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
        self.assertIn("CBOSA nie podaje prawomocności", str(kod))
        self.assertIn("bez --strict", str(kod))
        out, kod = self._uruchom(TestOrzeczenie.HTML, ["orzeczenie", "8889489BE0", "--strict"])
        self.assertEqual(out, "")
        self.assertIn("nie znaleziono oznaczenia", str(kod))

    def test_strict_nadal_sprawdza_tls(self):
        def fake_fetch(path, data=None):
            cbosa._transport_tls_verified = False
            return self.PRAWOMOCNE

        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", side_effect=fake_fetch), \
                mock.patch.object(sys, "argv", ["cbosa.py", "orzeczenie", "8889489BE0", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                cbosa.main()
        cbosa._transport_tls_verified = True
        self.assertEqual(out.getvalue(), "")
        self.assertIn("TLS", str(caught.exception.code))


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

    def test_http_410_na_doc_to_zweryfikowany_brak(self):
        # CBOSA odpowiada 410 (Gone) dla nieistniejącego doc_id — to potwierdzone
        # "nie ma", nie awaria; komunikat nie może odsyłać w pętlę "spróbuj ponownie"
        err = urllib.error.HTTPError("https://orzeczenia.nsa.gov.pl/doc/DEADBEEF",
                                     410, "Gone", {}, None)
        opener = mock.Mock()
        opener.open.side_effect = err
        cbosa._ostatnie[0] = 0.0
        with mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(cbosa.time, "sleep"):
            with self.assertRaisesRegex(SystemExit, "Nie znaleziono orzeczenia") as caught:
                cbosa._fetch("/doc/DEADBEEF")
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
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_t12_json_strict_nie_emituje_wyniku_gdy_kontrola_nie_przeszla(self):
        out = io.StringIO()
        with mock.patch.object(cbosa, "_szukaj", side_effect=cbosa.VerificationUnknown("timeout")), \
                mock.patch.object(sys, "argv", ["cbosa.py", "szukaj", "RODO", "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit):
                cbosa.main()
        self.assertEqual(out.getvalue(), "")

        out = io.StringIO()
        with mock.patch.object(cbosa, "_fetch", return_value="<html><body>Błąd</body></html>"), \
                mock.patch.object(sys, "argv", ["cbosa.py", "orzeczenie", "DEADBEEF", "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit):
                cbosa.main()
        self.assertEqual(out.getvalue(), "")

        out = io.StringIO()
        with mock.patch.object(cbosa, "_szukaj", return_value=(0, [], "Nie znaleziono")), \
                mock.patch.object(sys, "argv", ["cbosa.py", "sygnatura", "II", "FSK", "1/24",
                                                       "--json", "--strict"]), \
                redirect_stdout(out):
            with self.assertRaises(SystemExit):
                cbosa.main()
        self.assertEqual(out.getvalue(), "")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
