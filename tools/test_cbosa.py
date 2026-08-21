#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for cbosa.py pure functions (no network). Run: python3 tools/test_cbosa.py"""
import os
import sys
import importlib.util
import pathlib
import ssl
import unittest
import urllib.error
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


class TestZgodaNaObnizenieTls(unittest.TestCase):
    """Downgrade TLS wymaga jawnej zgody: tresc CBOSA trafia do cytatow w pismach."""

    def setUp(self):
        cbosa._ostatnie[0] = 0.0
        self._ctx = cbosa._ssl_ctx

    def tearDown(self):
        cbosa._ssl_ctx = self._ctx

    def _padnij_na_certyfikacie(self):
        blad = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
        opener = mock.Mock()
        opener.open.side_effect = blad
        return mock.patch.object(cbosa.urllib.request, "build_opener", return_value=opener)

    def test_bez_zgody_konczy_sie_unknown(self):
        srodowisko = {k: v for k, v in os.environ.items() if k != "CBOSA_INSECURE_TLS"}
        with mock.patch.dict(os.environ, srodowisko, clear=True), self._padnij_na_certyfikacie():
            with self.assertRaises(cbosa.VerificationUnknown) as ctx:
                cbosa._fetch("/doc/testowy")
        komunikat = str(ctx.exception)
        self.assertIn("certyfikat", komunikat.lower())
        self.assertIn("CBOSA_INSECURE_TLS", komunikat)

    def test_bez_zgody_nie_dotyka_kontekstu_ssl(self):
        srodowisko = {k: v for k, v in os.environ.items() if k != "CBOSA_INSECURE_TLS"}
        with mock.patch.dict(os.environ, srodowisko, clear=True), self._padnij_na_certyfikacie():
            with self.assertRaises(cbosa.VerificationUnknown):
                cbosa._fetch("/doc/testowy")
        self.assertNotEqual(cbosa._ssl_ctx.verify_mode, ssl.CERT_NONE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
