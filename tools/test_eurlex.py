#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eurlex.py pure functions (no network). Run: python3 tools/test_eurlex.py"""
import argparse
import contextlib
import io
import sys
import importlib.util
import pathlib
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eurlex", ROOT / "plugins/prawo-eu-eurlex/skills/prawo-eu-eurlex/scripts/eurlex.py")
eurlex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eurlex)


class TestCelexNorm(unittest.TestCase):
    def test_formy_celex(self):
        cases = [
            (["32016R0679"], "32016R0679"),
            (["celex:32016R0679"], "32016R0679"),
            (["CELEX: 32024R1689"], "32024R1689"),
            (["02016R0679-20160504"], "02016R0679-20160504"),
            (["32016R0679R(02)"], "32016R0679R(02)"),
            (["12012E/TXT"], "12012E/TXT"),
            (["12012P/TXT"], "12012P/TXT"),
            (["12012E016"], "12012E016"),
            (["32002L0058"], "32002L0058"),
        ]
        for sig, want in cases:
            self.assertEqual(eurlex.celex_norm(sig), want, f"celex: {sig}")

    def test_formy_eli(self):
        cases = [
            (["reg/2016/679"], "32016R0679"),
            (["http://data.europa.eu/eli/reg/2016/679/oj"], "32016R0679"),
            (["dir/2022/2555"], "32022L2555"),
            (["dec/2010/87"], "32010D0087"),
        ]
        for sig, want in cases:
            self.assertEqual(eurlex.celex_norm(sig), want, f"eli: {sig}")

    def test_bledny_celex_konczy_program(self):
        with self.assertRaises(SystemExit):
            eurlex.celex_norm(["zupełnie błędny identyfikator"])
        with self.assertRaises(SystemExit):
            eurlex.celex_norm(["Dz.U. 2024 poz. 18"])  # sygnatura polska → to skill prawo-pl-eli


class TestLang(unittest.TestCase):
    def test_mapowanie(self):
        self.assertEqual(eurlex._lang("pl"), "POL")
        self.assertEqual(eurlex._lang("pol"), "POL")
        self.assertEqual(eurlex._lang("en"), "ENG")
        self.assertEqual(eurlex._lang(None), "POL")
        self.assertEqual(eurlex._lang("HUN"), "HUN")  # kod 3-literowy spoza mapy przechodzi

    def test_nieznany_konczy_program(self):
        with self.assertRaises(SystemExit):
            eurlex._lang("xx")


class TestHtmlToText(unittest.TestCase):
    def test_normalizes_nbsp(self):
        self.assertEqual(eurlex.html_to_text("<p>Artykuł\xa028</p>"), "Artykuł 28")

    def test_strips_script(self):
        t = eurlex.html_to_text("<p>A</p><script>var x=1;</script><p>B</p>")
        self.assertIn("A", t)
        self.assertNotIn("var x", t)


class TestFragmenty(unittest.TestCase):
    TXT = ("ROZDZIAŁ I\n\nArtykuł 1\nPrzedmiot.\n\n"
           "Artykuł 2\n1. Zakres; odesłanie do art. 1 i Artykuł 28 nie na początku linii.\n\n"
           "Artykuł 28\nPodmiot przetwarzający.\n\n"
           "Artykuł 281\nPrzepis o dłuższym numerze.\n")

    def _frag(self, fraza):
        spans = eurlex._fragmenty(self.TXT, fraza)
        return [self.TXT[s:e] for s, e in spans]

    def test_artykul_po_naglowku_nie_po_odeslaniu(self):
        frags = self._frag("art. 28")
        self.assertEqual(len(frags), 1)
        self.assertIn("Podmiot przetwarzający", frags[0])
        self.assertNotIn("Zakres", frags[0])

    def test_artykul_nie_lapie_dluzszego_numeru(self):
        frags = self._frag("artykuł 2")
        self.assertEqual(len(frags), 1)
        self.assertIn("Zakres", frags[0])
        self.assertNotIn("dłuższym", frags[0])

    def test_fraza_pelnotekstowa(self):
        frags = self._frag("podmiot przetwarzający")
        self.assertEqual(len(frags), 1)
        self.assertTrue(frags[0].startswith("Artykuł 28"))

    def test_brak_trafien(self):
        self.assertEqual(eurlex._fragmenty(self.TXT, "nie ma takiej frazy"), [])


class TestKonsolidacje(unittest.TestCase):
    """_konsolidacje buduje prefiks i filtruje wyniki SPARQL (podmieniamy _sparql)."""

    def _z_fake_sparql(self, rows, celex):
        zapytania = []

        def fake_sparql(q, soft=False):
            zapytania.append(q)
            return rows
        orig = eurlex._sparql
        eurlex._sparql = fake_sparql
        try:
            return eurlex._konsolidacje(celex), zapytania
        finally:
            eurlex._sparql = orig

    def test_prefiks_z_aktu_bazowego(self):
        rows = [{"celex": {"value": "02016R0679-20160504"}}]
        wynik, zapytania = self._z_fake_sparql(rows, "32016R0679")
        self.assertEqual(wynik, ["02016R0679-20160504"])
        self.assertIn('"02016R0679-"', zapytania[0])

    def test_prefiks_z_wersji_skonsolidowanej(self):
        _, zapytania = self._z_fake_sparql([], "02006L0112-20240101")
        self.assertIn('"02006L0112-"', zapytania[0])

    def test_odfiltrowuje_celexy_bez_daty(self):
        rows = [{"celex": {"value": "02016R0679-20160504"}},
                {"celex": {"value": "02016R0679(01)"}}]
        wynik, _ = self._z_fake_sparql(rows, "32016R0679")
        self.assertEqual(wynik, ["02016R0679-20160504"])


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj", "fraza"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, eurlex.cmd_szukaj
        eurlex.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            eurlex.main()
        finally:
            sys.argv, eurlex.cmd_szukaj = oryg_argv, oryg_cmd
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


class EurlexVerificationContractTests(unittest.TestCase):
    """found/verified_absent/unknown - blad transportu nie moze wygladac jak potwierdzony brak."""

    def test_soft_transport_error_is_unknown(self):
        with mock.patch.object(eurlex, "_http", side_effect=SystemExit("BŁĄD sieci")):
            with self.assertRaises(eurlex.VerificationUnknown):
                eurlex._sparql("SELECT * WHERE {}", soft=True)

    def test_successful_zero_consolidations_is_verified_absent(self):
        with mock.patch.object(eurlex, "_sparql", return_value=[]):
            self.assertEqual(eurlex._konsolidacje("32016R0679"), [])

    def test_command_reports_unknown_consolidations(self):
        args = argparse.Namespace(celex=["32016R0679"], json=False)
        with mock.patch.object(eurlex, "_konsolidacje",
                               side_effect=eurlex.VerificationUnknown("timeout")):
            with self.assertRaisesRegex(SystemExit, "nie udało się zweryfikować.*Spróbuj ponownie"):
                eurlex.cmd_skonsolidowany(args)

    def test_ostrzezenia_przy_tekscie_nie_blokuja_tresci(self):
        # przy tekście/meta konsolidacje to informacja POBOCZNA — awaria SPARQL daje
        # głośne ostrzeżenie zamiast odebrać użytkownikowi treść główną
        with mock.patch.object(eurlex, "_konsolidacje",
                               side_effect=eurlex.VerificationUnknown("timeout")):
            out = eurlex._ostrzezenia_konsolidacja("32016R0679")
        self.assertEqual(len(out), 1)
        self.assertIn("nie udało się zweryfikować", out[0])
        self.assertIn("skonsolidowany 32016R0679", out[0])

    def test_strict_blokuje_wynik_przy_awarii_kontroli_konsolidacji(self):
        with mock.patch.object(eurlex, "_konsolidacje",
                               side_effect=eurlex.VerificationUnknown("timeout")):
            with self.assertRaisesRegex(eurlex.VerificationUnknown, "timeout"):
                eurlex._ostrzezenia_konsolidacja("32016R0679", strict=True)

    def test_strict_nie_wypisuje_tekstu_przed_kontrola_konsolidacji(self):
        args = argparse.Namespace(celex=["32016R0679"], jezyk="pol", json=False,
                                  strict=True, pdf=None, fragment=None)
        out = io.StringIO()
        with mock.patch.object(eurlex, "_http", return_value=(b"<p>Artykul 1. Tresc.</p>", "text/html")), \
                mock.patch.object(eurlex, "_konsolidacje",
                                  side_effect=eurlex.VerificationUnknown("timeout")), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(eurlex.VerificationUnknown, "timeout"):
                eurlex.cmd_tekst(args)
        self.assertEqual(out.getvalue(), "")

    def test_t05_strict_blokuje_poprawnie_wykryta_nowsza_konsolidacje(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_http",
                               return_value=(b"<p>Artykul 1. Starsza tresc.</p>", "text/html")), \
                mock.patch.object(eurlex, "_konsolidacje",
                                  return_value=["02016R0679-20250504", "02016R0679-20160504"]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "02016R0679-20160504", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eurlex.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_t06_json_strict_nie_emituje_danych_gdy_kontrola_nie_przeszla(self):
        komendy = [
            ["szukaj", "dane"],
            ["meta", "32016R0679"],
            ["tekst", "32016R0679"],
            ["skonsolidowany", "32016R0679"],
            ["odniesienia", "32016R0679"],
        ]
        for komenda in komendy:
            with self.subTest(komenda=komenda):
                out = io.StringIO()
                with mock.patch.object(eurlex, "_http",
                                       return_value=(b"<p>Artykul 1. Tresc.</p>", "text/html")), \
                        mock.patch.object(eurlex, "_sparql",
                                          side_effect=eurlex.VerificationUnknown("timeout")), \
                        mock.patch.object(sys, "argv",
                                          ["eurlex.py", *komenda, "--json", "--strict"]), \
                        contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        eurlex.main()
                self.assertNotEqual(caught.exception.code, 0)
                self.assertEqual(out.getvalue(), "")


class TestT12StrictMetaAktuBazowego(unittest.TestCase):
    """Metadane aktu bazowego nie są nieaktualne przez to, że istnieje konsolidacja — strict
    blokuje tam tylko AWARIĘ kontroli; treść (tekst) aktu bazowego z konsolidacjami blokuje."""
    WIERSZ = [{"type": {"value": "http://publications.europa.eu/resource/authority/resource-type/REG"},
               "date": {"value": "2016-04-27"}, "inf": {"value": "2016-05-24"},
               "title": {"value": "Rozporządzenie 2016/679"}}]

    def test_meta_strict_z_konsolidacja_przechodzi_z_ostrzezeniem(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_sparql", return_value=self.WIERSZ), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=["02016R0679-20160504"]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "meta", "32016R0679", "--strict"]), \
                contextlib.redirect_stdout(out):
            eurlex.main()
        self.assertIn("Akt: CELEX 32016R0679", out.getvalue())
        self.assertIn("użyj najnowszej: 02016R0679-20160504", out.getvalue())

    def test_meta_strict_awaria_kontroli_blokuje_bez_stdout(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_sparql", return_value=self.WIERSZ), \
                mock.patch.object(eurlex, "_konsolidacje",
                                  side_effect=eurlex.VerificationUnknown("timeout")), \
                mock.patch.object(sys, "argv", ["eurlex.py", "meta", "32016R0679", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eurlex.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_tekst_aktu_bazowego_z_konsolidacja_strict_blokuje_i_wskazuje_wersje(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_http", return_value=(b"<p>Artykul 1.</p>", "text/html")), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=["02016R0679-20160504"]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "32016R0679", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(SystemExit, "tekst 02016R0679-20160504"):
                eurlex.main()
        self.assertEqual(out.getvalue(), "")


class TestT07WymusHttps(unittest.TestCase):
    """CELLAR nazywa zasoby przez http:// URI, ale pobierac mamy po https."""

    def test_dziewiec_przypadkow_url(self):
        cases = [
            ("http://publications.europa.eu/resource/celex/X?x=1#f",
             "https://publications.europa.eu/resource/celex/X?x=1#f"),
            ("http://notreally-europa.eu/resource/celex/X",
             "http://notreally-europa.eu/resource/celex/X"),
            ("http://publications.europa.eu@notreally-europa.eu/resource/celex/X",
             "http://publications.europa.eu@notreally-europa.eu/resource/celex/X"),
            ("http://PUBLICATIONS.EUROPA.EU/resource/celex/X",
             "https://PUBLICATIONS.EUROPA.EU/resource/celex/X"),
            ("HTTP://publications.europa.eu/resource/celex/X",
             "https://publications.europa.eu/resource/celex/X"),
            ("http://publications.europa.eu./resource/celex/X",
             "https://publications.europa.eu./resource/celex/X"),
            ("http://publications.europa.eu:443/resource/celex/X",
             "https://publications.europa.eu:443/resource/celex/X"),
            ("http://publications.europa.eu:8080/resource/celex/X",
             "https://publications.europa.eu:8080/resource/celex/X"),
            ("http://api.publications.europa.eu/resource/celex/X",
             "https://api.publications.europa.eu/resource/celex/X"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(eurlex._wymus_https(url), expected)

    def test_nie_daje_sie_nabrac_na_nadhosta(self):
        url = "http://publications.europa.eu.evil.example/resource/celex/X"
        self.assertEqual(eurlex._wymus_https(url), url)

    def test_data_europa_i_poddomena_sa_na_bialej_liscie(self):
        cases = [
            ("http://data.europa.eu/eli/reg/2016/679/oj",
             "https://data.europa.eu/eli/reg/2016/679/oj"),
            ("http://api.data.europa.eu/eli/reg/2016/679/oj",
             "https://api.data.europa.eu/eli/reg/2016/679/oj"),
        ]
        for url, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(eurlex._wymus_https(url), expected)

    def test_http_przekazuje_request_z_podniesionym_url(self):
        odpowiedz = mock.MagicMock()
        odpowiedz.__enter__.return_value = odpowiedz
        odpowiedz.read.return_value = b"OK"
        odpowiedz.headers.get.return_value = "text/plain"
        with mock.patch.object(eurlex._opener, "open", return_value=odpowiedz) as otworz:
            eurlex._http("http://publications.europa.eu/resource/celex/X")
        request = otworz.call_args.args[0]
        self.assertEqual(request.full_url, "https://publications.europa.eu/resource/celex/X")

    def test_przekierowanie_303_na_http_cellar_jest_podnoszone(self):
        # CELLAR odpowiada 303 z Location http:// nawet na żądanie https — bez podniesienia
        # schematu w celu przekierowania treść i tak popłynęłaby czystym HTTP.
        req = eurlex.urllib.request.Request("https://publications.europa.eu/resource/celex/X")
        nowy = eurlex._PrzekierowaniaHttps().redirect_request(
            req, None, 303, "See Other", {},
            "http://publications.europa.eu/resource/cellar/abc.0018.03/DOC_1")
        self.assertEqual(nowy.full_url,
                         "https://publications.europa.eu/resource/cellar/abc.0018.03/DOC_1")

    def test_przekierowanie_303_na_http_data_europa_jest_podnoszone(self):
        req = eurlex.urllib.request.Request("https://publications.europa.eu/resource/celex/X")
        nowy = eurlex._PrzekierowaniaHttps().redirect_request(
            req, None, 303, "See Other", {}, "http://data.europa.eu/eli/reg/2016/679/oj")
        self.assertEqual(nowy.full_url, "https://data.europa.eu/eli/reg/2016/679/oj")

    def test_przekierowanie_na_obcy_host_po_http_jest_odrzucone(self):
        req = eurlex.urllib.request.Request("https://publications.europa.eu/resource/celex/X")
        with self.assertRaisesRegex(eurlex.urllib.error.URLError,
                                    "odrzucono przekierowanie.*niezaufany host"):
            eurlex._PrzekierowaniaHttps().redirect_request(
                req, None, 303, "See Other", {}, "http://example.test/legal-content")

    def test_uri_przestrzeni_nazw_zostaja_http(self):
        # Zmiana ich postaci zerwalaby dopasowanie w zapytaniach SPARQL.
        for stala in (eurlex.CDM, eurlex.LANG_AUTH, eurlex.TYPE_AUTH, eurlex.XSD_STR):
            self.assertTrue(stala.startswith("http://"), stala)


if __name__ == "__main__":
    unittest.main(verbosity=2)
