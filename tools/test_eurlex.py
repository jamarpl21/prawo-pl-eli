#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eurlex.py pure functions (no network). Run: python3 tools/test_eurlex.py"""
import argparse
import json
import re
import contextlib
import io
import sys
import importlib.util
import os
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eurlex", ROOT / "plugins/prawo-eu-eurlex/skills/prawo-eu-eurlex/scripts/eurlex.py")
eurlex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eurlex)


class _SiecZablokowana(BaseException):
    """Test dotknął sieci. BaseException, bo _http łapie Exception i ponawia z time.sleep."""


_straz_sieci = mock.patch.object(eurlex._opener, "open",
                                 side_effect=_SiecZablokowana("test próbował połączyć się z siecią"))


def setUpModule():
    _straz_sieci.start()


def tearDownModule():
    _straz_sieci.stop()


class TestEmptyConsolidations(unittest.TestCase):
    def test_no_consolidation_does_not_claim_no_amendments(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_konsolidacje", return_value=[]), \
                contextlib.redirect_stdout(out):
            eurlex.cmd_skonsolidowany(argparse.Namespace(celex=["32016R0679"], json=False))
        self.assertNotIn("akt nie był zmieniany", out.getvalue())
        self.assertIn("nie potwierdza braku zmian", out.getvalue())
        self.assertIn("odniesienia 32016R0679", out.getvalue())

    def test_empty_json_remains_a_list(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_konsolidacje", return_value=[]), \
                contextlib.redirect_stdout(out):
            eurlex.cmd_skonsolidowany(argparse.Namespace(celex=["32016R0679"], json=True))
        self.assertEqual(json.loads(out.getvalue()), [])


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

    def test_odeslanie_na_poczatku_linii_nie_jest_naglowkiem(self):
        # AI Act art. 113 lit. c (EN): „Article 6(1) and the corresponding obligations … shall apply
        # from 2 August 2027." — linia zaczyna się od „Article 6", ale nagłówkiem nie jest
        txt = ("Article 113\nEntry into force and application\n(c)\n"
               "Article 6(1) and the corresponding obligations in this Regulation shall apply from 2 August 2027.\n"
               "Done at Brussels, 13 June 2024.\n")
        spans = eurlex._fragmenty(txt, "art. 113")
        frag = txt[spans[0][0]:spans[0][1]]
        self.assertIn("2 August 2027", frag)
        self.assertNotIn("Done at", frag)
        self.assertEqual(eurlex._fragmenty(txt, "art. 6"), [])  # odesłanie to nie trafienie w art. 6


class TestGraniceOstatniegoArtykulu(unittest.TestCase):
    """Fragment OSTATNIEGO artykułu nie może ciągnąć za sobą podpisów i przypisów końcowych
    (RODO art. 99: 21 przypisów, AI Act art. 113: 58) — audyt D04/C17."""

    XHTML = ("<div><p>Artykuł 98</p><p>Przepis przedostatni.</p></div>"
             "<div id=\"art_99\"><p class=\"oj-ti-art\">Artykuł 99</p>"
             "<p class=\"oj-normal\">1. Niniejsze rozporządzenie wchodzi w życie.</p></div>"
             "<div class=\"oj-final\"><p class=\"oj-normal\">Niniejsze rozporządzenie wiąże w całości.</p>"
             "<p class=\"oj-normal\">Sporządzono w Brukseli dnia 27 kwietnia 2016 r.</p>"
             "<div class=\"oj-signatory\"><p class=\"oj-signatory\">W imieniu Parlamentu Europejskiego</p>"
             "<p class=\"oj-signatory\">M. SCHULZ</p></div></div>"
             "<hr class=\"oj-note\"/><p class=\"oj-note\">(1) Dz.U. C 229 z 31.7.2012, s. 90.</p>"
             "<p class=\"oj-note\">(2) Stanowisko Parlamentu Europejskiego z dnia 12 marca 2014 r.</p>")

    def test_xhtml_aktu_bazowego_daje_znak_granicy_przed_podpisami_i_przypisami(self):
        txt = eurlex.html_to_text(self.XHTML)
        self.assertIn(eurlex.GRANICA, txt)
        spans = eurlex._fragmenty(txt, "art. 99")
        self.assertEqual(len(spans), 1)
        frag = eurlex._bez_granic(txt[spans[0][0]:spans[0][1]])
        self.assertIn("wchodzi w życie", frag)
        self.assertIn("wiąże w całości", frag)  # formuła końcowa zostaje przy ostatnim artykule
        for zbedne in ("Sporządzono", "W imieniu", "SCHULZ", "Dz.U. C 229", "Stanowisko"):
            self.assertNotIn(zbedne, frag)
        self.assertNotIn(eurlex.GRANICA, frag)

    def test_xhtml_wersji_skonsolidowanej_przypisy_klasa_footnote(self):
        xhtml = ("<p class=\"title-article-norm\">Artykuł 99</p><p class=\"norm\">Treść.</p>"
                 "<hr class=\"separator-short\"/>"
                 "<p class=\"footnote\">(1) Dyrektywa (UE) 2015/1535 (Dz.U. L 241 z 17.9.2015, s. 1).</p>")
        txt = eurlex.html_to_text(xhtml)
        spans = eurlex._fragmenty(txt, "art. 99")
        frag = eurlex._bez_granic(txt[spans[0][0]:spans[0][1]])
        self.assertIn("Treść.", frag)
        self.assertNotIn("2015/1535", frag)

    def test_granice_tekstowe_bez_znacznikow(self):
        # zapas, gdyby XHTML nie miał klas: „Sporządzono w", „W imieniu …", „(1) Dz.U." kończą jednostkę
        for ogon in ("Sporządzono w Brukseli dnia 1 maja 2020 r.\nX\n",
                     "W imieniu Rady\nJ. KOWALSKI\n",
                     "Done at Brussels, 13 June 2024.\n",
                     "For the European Parliament\nThe President\n",
                     "(1) Dz.U. C 229 z 31.7.2012, s. 90.\n(2) Dz.U. L 1.\n",
                     "(1) OJ C 229, 31.7.2012, p. 90.\n"):
            with self.subTest(ogon=ogon):
                txt = "Artykuł 5\nTreść piąta.\n\n" + ogon
                spans = eurlex._fragmenty(txt, "art. 5")
                frag = txt[spans[0][0]:spans[0][1]]
                self.assertIn("Treść piąta", frag)
                self.assertNotIn(ogon.splitlines()[0], frag)

    def test_punkt_aktu_zmieniajacego_po_angielsku_nie_jest_granica(self):
        # w aktach zmieniających (EN) punkty to „(1) Article 5 is replaced…" — to NIE przypis
        txt = "Article 1\nRegulation X is amended as follows:\n(1) Article 5 is replaced by the following;\n(2) Article 6 is deleted.\n\nArticle 2\nEntry into force.\n"
        spans = eurlex._fragmenty(txt, "art. 1")
        frag = txt[spans[0][0]:spans[0][1]]
        self.assertIn("Article 6 is deleted", frag)
        self.assertNotIn("Entry into force", frag)

    def test_fraza_z_przypisu_daje_sam_przypis(self):
        txt = eurlex.html_to_text(self.XHTML)
        spans = eurlex._fragmenty(txt, "Dz.U. C 229")
        frag = eurlex._bez_granic(txt[spans[0][0]:spans[0][1]]).strip()
        self.assertTrue(frag.startswith("(1)"), frag)
        self.assertNotIn("Artykuł 99", frag)

    def test_cmd_tekst_nie_wypisuje_znaku_granicy(self):
        out = io.StringIO()
        for fragment in ("art. 99", None):
            with self.subTest(fragment=fragment):
                out = io.StringIO()
                args = argparse.Namespace(celex=["32016R0679"], jezyk="pol", json=False,
                                          strict=False, pdf=None, fragment=fragment)
                with mock.patch.object(eurlex, "_http", return_value=(self.XHTML.encode(), "text/html")), \
                        mock.patch.object(eurlex, "_konsolidacje", return_value=[]), \
                        mock.patch.object(eurlex, "_sprostowania", return_value=[]), \
                        contextlib.redirect_stdout(out):
                    eurlex.cmd_tekst(args)
                self.assertNotIn(eurlex.GRANICA, out.getvalue())
                self.assertIn("wchodzi w życie", out.getvalue())
                if fragment:
                    self.assertNotIn("SCHULZ", out.getvalue())
                else:
                    self.assertIn("SCHULZ", out.getvalue())  # pełny tekst: nic nie ginie


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
                mock.patch.object(eurlex, "_sprostowania", return_value=[]), \
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
                mock.patch.object(eurlex, "_sprostowania", return_value=[]), \
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
                mock.patch.object(eurlex, "_sprostowania", return_value=[]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "32016R0679", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(SystemExit, "tekst 02016R0679-20160504"):
                eurlex.main()
        self.assertEqual(out.getvalue(), "")


def _lit(v):
    return {"type": "literal", "value": v}


def _wiersz(**kw):
    return {k: _lit(v) for k, v in kw.items()}


TYP = "http://publications.europa.eu/resource/authority/resource-type/"


class _FakeCellar:
    """Podmiana _sparql rozpoznająca zapytania po CELEX-ie w literale i po właściwości.

    meta: {celex: [wiersze]}; zmiany: {celex: [celexy aktów zmieniających]};
    sprost: {celex: [wiersze sprostowań]} — brak klucza = []."""

    def __init__(self, meta, zmiany=None, awaria_zmian=False, sprost=None):
        self.meta, self.zmiany, self.awaria_zmian = meta, zmiany or {}, awaria_zmian
        self.sprost = sprost or {}
        self.zapytania = []

    def __call__(self, q, soft=False):
        self.zapytania.append(q)
        m = re.search(r'resource_legal_id_celex "([^"]+)"', q)
        celex = m.group(1) if m else None
        if "resource_legal_amends_resource_legal ?w" in q and "BIND" not in q:
            if self.awaria_zmian:
                raise eurlex.VerificationUnknown("timeout")
            return [_wiersz(c2=c) for c in self.zmiany.get(celex, [])]
        if "resource_legal_corrects_resource_legal ?w" in q and "BIND" not in q:
            return self.sprost.get(celex, [])
        return self.meta.get(celex, [])


class TestSzukajZeroTrafien(unittest.TestCase):
    """SKILL.md: zero trafień = komunikat + kod wyjścia ≠ 0 TAKŻE z --json (audyt D06/C18)."""

    def test_zero_z_json_i_bez(self):
        for argv in (["szukaj", "dane osobowe", "--typ", "REG"],
                     ["szukaj", "dane osobowe", "--typ", "REG", "--json"],
                     ["--json", "szukaj", "dane osobowe"]):
            with self.subTest(argv=argv):
                out = io.StringIO()
                with mock.patch.object(eurlex, "_sparql", return_value=[]), \
                        mock.patch.object(sys, "argv", ["eurlex.py", *argv]), \
                        contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        eurlex.main()
                self.assertNotEqual(caught.exception.code, 0)
                self.assertIn("To NIE dowód", str(caught.exception.code))
                self.assertEqual(out.getvalue(), "")  # żadnego „[]"


class TestMetaWersjaSkonsolidowana(unittest.TestCase):
    """meta na CELEX-ie skonsolidowanym: data „stan na" osobno, daty z AKTU BAZOWEGO (audyt D03/C16)."""

    KONS = "02016R0679-20160504"
    BAZA = "32016R0679"
    META = {
        KONS: [_wiersz(type=TYP + "CONS_TEXT", date="2016-05-04", eiv="2016-05-04",
                       kons_data="2016-05-04", baza=BAZA, sklad=BAZA, title="Tytuł RODO"),
               _wiersz(type=TYP + "CONS_TEXT", date="2016-05-04", eiv="2016-05-04",
                       kons_data="2016-05-04", baza=BAZA, sklad=BAZA + "R(01)", title="Tytuł RODO")],
        BAZA: [_wiersz(type=TYP + "REG", date="2016-04-27", eiv="2016-05-24", inf="1",
                       eli="http://data.europa.eu/eli/reg/2016/679/oj", title="Tytuł RODO"),
               _wiersz(type=TYP + "REG", date="2016-04-27", eiv="2018-05-25", inf="1",
                       eli="http://data.europa.eu/eli/reg/2016/679/oj", title="Tytuł RODO")],
    }

    def _meta(self, celex, strict=False, zmiany=None, kons=None, json_=False, awaria=False):
        out = io.StringIO()
        fake = _FakeCellar(self.META, zmiany, awaria_zmian=awaria)
        argv = ["eurlex.py", "meta", celex] + (["--strict"] if strict else []) + (["--json"] if json_ else [])
        with mock.patch.object(eurlex, "_sparql", fake), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=kons if kons is not None else [self.KONS]), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            eurlex.main()
        return out.getvalue()

    def test_stan_na_i_daty_aktu_bazowego(self):
        out = self._meta(self.KONS)
        self.assertIn("Stan na (konsolidacja): 2016-05-04", out)
        self.assertIn("Akt bazowy: CELEX 32016R0679", out)
        self.assertIn("Data aktu: 2016-04-27", out)
        self.assertIn("Wejście w życie / stosowanie: 2016-05-24, 2018-05-25", out)
        self.assertIn("Uwzględnia: 32016R0679, 32016R0679R(01)", out)
        self.assertIn("Status:  OBOWIĄZUJE", out)
        self.assertNotIn("Data aktu: 2016-05-04", out)
        self.assertNotIn("WEJŚCIE W ŻYCIE / STOSOWANIE", out)

    def test_json_ma_akt_bazowy_i_ostrzezenia(self):
        d = json.loads(self._meta(self.KONS, json_=True))
        self.assertTrue(d["wersja_skonsolidowana"])
        self.assertEqual(d["akt_bazowy"]["celex"], self.BAZA)
        self.assertEqual(d["akt_bazowy"]["meta"]["eiv"], ["2016-05-24", "2018-05-25"])
        self.assertEqual(d["zmieniajace"], [])
        self.assertTrue(any("DOKUMENTACYJNY" in w for w in d["ostrzezenia"]))

    def test_akt_bazowy_bez_zmian_nie_ma_ostrzezenia_o_zmianach(self):
        out = self._meta(self.BAZA)
        self.assertIn("Wejście w życie / stosowanie: 2016-05-24, 2018-05-25", out)
        self.assertNotIn("był zmieniany", out)

    def test_awaria_kontroli_zmian_bez_strict_ostrzega(self):
        out = self._meta(self.BAZA, awaria=True)
        self.assertIn("nie udało się zweryfikować, czy akt 32016R0679 był zmieniany", out)

    def test_awaria_kontroli_zmian_w_strict_blokuje(self):
        with self.assertRaises((SystemExit, eurlex.VerificationUnknown)):
            self._meta(self.BAZA, strict=True, awaria=True)


AI_BAZA, AI_KONS, AI_ZM = "32024R1689", "02024R1689-20260727", "32026R1744"


class TestMetaAktZmieniany(unittest.TestCase):
    """Daty stosowania aktu bazowego po nowelizacji mogą być nieaktualne (AI Act art. 113 po
    32026R1744) — ostrzeżenie zawsze, w --strict blokada aktu bazowego (audyt D02/C08)."""

    BAZA, KONS, ZM = AI_BAZA, AI_KONS, AI_ZM
    META = {
        AI_BAZA: [_wiersz(type=TYP + "REG", date="2024-06-13", eiv=d, inf="1", title="AI Act")
                  for d in ("2024-08-01", "2025-02-02", "2025-08-02", "2026-08-02", "2027-08-02")],
        AI_KONS: [_wiersz(type=TYP + "CONS_TEXT", date="2026-07-27", eiv="2026-07-27",
                          kons_data="2026-07-27", baza=AI_BAZA, sklad=s, title="AI Act")
                  for s in (AI_BAZA, AI_ZM)],
        "02024R1689-20240712": [_wiersz(type=TYP + "CONS_TEXT", date="2024-07-12", eiv="2024-07-12",
                                        kons_data="2024-07-12", baza=AI_BAZA, sklad=AI_BAZA, title="AI Act")],
    }

    def _meta(self, celex, strict=False, zmiany=None, kons=None):
        out = io.StringIO()
        fake = _FakeCellar(self.META, zmiany if zmiany is not None else {self.BAZA: [self.ZM]})
        argv = ["eurlex.py", "meta", celex] + (["--strict"] if strict else [])
        with mock.patch.object(eurlex, "_sparql", fake), \
                mock.patch.object(eurlex, "_konsolidacje",
                                  return_value=kons if kons is not None else [self.KONS, "02024R1689-20240712"]), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            eurlex.main()
        return out.getvalue()

    def test_bez_strict_ostrzezenie_wskazuje_akt_zmieniajacy_i_najnowsza_konsolidacje(self):
        out = self._meta(self.BAZA)
        self.assertIn("2027-08-02", out)
        self.assertRegex(out, r"UWAGA: akt 32024R1689 był zmieniany \(32026R1744\).*mogą być nieaktualne")
        self.assertIn("najnowszej wersji skonsolidowanej 02024R1689-20260727", out)

    def test_strict_blokuje_akt_bazowy_po_nowelizacji_bez_stdout(self):
        with self.assertRaises(SystemExit) as caught:
            self._meta(self.BAZA, strict=True)
        msg = str(caught.exception.code)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("32026R1744", msg)
        self.assertIn("meta 02024R1689-20260727", msg)
        self.assertIn("art. o stosowaniu", msg)

    def test_strict_same_sprostowania_nie_blokuja(self):
        out = self._meta(self.BAZA, strict=True, zmiany={})
        self.assertIn("Akt: CELEX 32024R1689", out)
        self.assertNotIn("był zmieniany", out)

    def test_strict_najnowsza_konsolidacja_przechodzi_z_ostrzezeniem(self):
        out = self._meta(self.KONS, strict=True)
        self.assertIn("Stan na (konsolidacja): 2026-07-27", out)
        self.assertIn("Uwzględnia: 32024R1689, 32026R1744", out)
        self.assertIn("był zmieniany (32026R1744)", out)

    def test_strict_starsza_konsolidacja_blokuje(self):
        with self.assertRaisesRegex(SystemExit, "nowsza wersja skonsolidowana: 02024R1689-20260727"):
            self._meta("02024R1689-20240712", strict=True)


class TestMetaDyrektywaIEtykiety(unittest.TestCase):
    """Dyrektywa: termin(y) transpozycji; jedna data = „Wejście w życie", nie łączona etykieta
    (audyt D05/C10). Tytuł bez ucięcia na 300 znakach (D08/C22)."""

    TYTUL = "Dyrektywa " + "bardzo długa nazwa " * 25 + "(Tekst mający znaczenie dla EOG)"

    def _meta(self, celex, rows):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_sparql", _FakeCellar({celex: rows})), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=[]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "meta", celex]), \
                contextlib.redirect_stdout(out):
            eurlex.main()
        return out.getvalue()

    def test_dyrektywa_dwa_terminy_transpozycji(self):
        rows = [_wiersz(type=TYP + "DIR", date="2019-10-23", eiv="2019-12-16", inf="1", trans=t,
                        title="Dyrektywa 2019/1937") for t in ("2021-12-17", "2023-12-17")]
        out = self._meta("32019L1937", rows)
        self.assertIn("Wejście w życie: 2019-12-16", out)
        self.assertIn("Termin transpozycji: 2021-12-17, 2023-12-17", out)
        self.assertIn("prawo-pl-eli", out)
        self.assertNotIn("STOSOWANIE", out)

    def test_dyrektywa_bez_terminu_w_cellar_mowi_to_wprost(self):
        out = self._meta("31999L0001", [_wiersz(type=TYP + "DIR", date="1999-01-01", eiv="1999-02-01", inf="0")])
        self.assertIn("Termin transpozycji: brak w CELLAR", out)

    def test_rozporzadzenie_jedna_data_to_wejscie_w_zycie(self):
        out = self._meta("32020R0001", [_wiersz(type=TYP + "REG", date="2020-01-01", eiv="2020-01-21", inf="1")])
        self.assertIn("Wejście w życie: 2020-01-21", out)
        self.assertNotIn("stosowanie", out.lower().split("tekst:")[0].split("wejście w życie:")[1].split("\n")[0])

    def test_tytul_w_calosci(self):
        assert len(self.TYTUL) > 300
        out = self._meta("32019L1937", [_wiersz(type=TYP + "DIR", date="2019-10-23", title=self.TYTUL)])
        self.assertEqual(re.sub(r"\s+", " ", out.split("Tytuł:")[1].split("Typ:")[0]).strip(), self.TYTUL)


class TestOdniesieniaUchylenia(unittest.TestCase):
    """Relacje uchylenia w obie strony + jasny status NIE OBOWIĄZUJE (audyt D01/C06)."""

    def _odn(self, celex, rows):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_sparql", return_value=rows), \
                mock.patch.object(sys, "argv", ["eurlex.py", "odniesienia", celex]), \
                contextlib.redirect_stdout(out):
            eurlex.main()
        return out.getvalue()

    def test_zapytanie_pyta_o_uchylenia_w_obie_strony(self):
        fake = _FakeCellar({})
        with mock.patch.object(eurlex, "_sparql", fake), \
                mock.patch.object(sys, "argv", ["eurlex.py", "odniesienia", "31995L0046"]), \
                contextlib.redirect_stdout(io.StringIO()):
            eurlex.main()
        q = fake.zapytania[0]
        self.assertIn("?x cdm:resource_legal_repeals_resource_legal ?w", q)
        self.assertIn("?w cdm:resource_legal_repeals_resource_legal ?o", q)
        self.assertIn("resource_legal_implicitly_repeals_resource_legal", q)

    def test_uchylony_przez_rodo(self):
        rows = [_wiersz(kier=eurlex._KIERUNKI[0], c2="32016R0679", inf="0", eov="2018-05-24"),
                _wiersz(kier=eurlex._KIERUNKI[2], c2="32003R1882", inf="0", eov="2018-05-24")]
        out = self._odn("31995L0046", rows)
        self.assertIn("AKT UCHYLONY przez 32016R0679 (koniec obowiązywania: 2018-05-24) — NIE OBOWIĄZUJE", out)
        self.assertIn("## UCHYLONY PRZEZ", out)
        self.assertNotIn("aktualny stan czytaj z wersji skonsolidowanej", out)

    def test_rodo_uchyla_95_46(self):
        rows = [_wiersz(kier=eurlex._KIERUNKI[4], c2="31995L0046", inf="1", eov="9999-12-31"),
                _wiersz(kier=eurlex._KIERUNKI[5], c2="32003R1882", inf="1", eov="9999-12-31"),
                _wiersz(kier=eurlex._KIERUNKI[3], c2="32016R0679R(01)", inf="1", eov="9999-12-31")]
        out = self._odn("32016R0679", rows)
        self.assertIn("## Uchyla (akty uchylone przez ten akt)  (1)\n  - 31995L0046", out)
        self.assertIn("## Uchyla w sposób dorozumiany", out)
        self.assertNotIn("NIE OBOWIĄZUJE", out)


class TestTekst404Konsolidacji(unittest.TestCase):
    """404 na wersji skonsolidowanej z listy skonsolidowany: CELLAR nie serwuje wersji zastąpionej —
    nie każ „sprawdzać numeru CELEX" (audyt D07/C21)."""

    def _tekst(self, celex, kons):
        args = argparse.Namespace(celex=[celex], jezyk="pol", json=False, strict=False, pdf=None,
                                  fragment="art. 5")
        blad = SystemExit(f"BŁĄD: nie znaleziono zasobu (404): https://publications.europa.eu/resource/celex/{celex}\n"
                          "Sprawdź numer CELEX")
        with mock.patch.object(eurlex, "_http", side_effect=blad), \
                mock.patch.object(eurlex, "_konsolidacje", **kons), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                eurlex.cmd_tekst(args)
        return str(caught.exception.code)

    def test_wersja_zastapiona(self):
        msg = self._tekst("02024R1689-20240712", dict(return_value=["02024R1689-20260727", "02024R1689-20240712"]))
        self.assertIn("nie udostępnia już tekstu wersji skonsolidowanej 02024R1689-20240712", msg)
        self.assertIn("Najnowsza wersja: 02024R1689-20260727", msg)
        self.assertNotIn("Sprawdź numer CELEX", msg)

    def test_najnowsza_wersja_bez_tekstu_w_jezyku(self):
        msg = self._tekst("02024R1689-20260727", dict(return_value=["02024R1689-20260727"]))
        self.assertIn("najnowszej wersji skonsolidowanej 02024R1689-20260727 w języku pol", msg)

    def test_nieznana_wersja(self):
        msg = self._tekst("02024R1689-20990101", dict(return_value=["02024R1689-20260727"]))
        self.assertIn("nie zna wersji skonsolidowanej 02024R1689-20990101", msg)

    def test_awaria_listy_wersji_nie_udaje_pewnosci(self):
        msg = self._tekst("02024R1689-20240712", dict(side_effect=eurlex.VerificationUnknown("timeout")))
        self.assertIn("nie udało się zweryfikować", msg)

    def test_akt_bazowy_404_nieznany_celex_kaze_sprawdzic_numer(self):
        # 404 i brak pracy w metadanych CELLAR (SPARQL: zero wierszy) — dopiero wtedy „sprawdź numer CELEX"
        args = argparse.Namespace(celex=["39999R9999"], jezyk="pol", json=False, strict=False, pdf=None, fragment=None)
        with mock.patch.object(eurlex, "_http", side_effect=SystemExit("BŁĄD: nie znaleziono zasobu (404): x")), \
                mock.patch.object(eurlex, "_sparql", return_value=[]):
            with self.assertRaisesRegex(SystemExit, "nie znaleziono aktu 39999R9999.*Sprawdź numer CELEX"):
                eurlex.cmd_tekst(args)


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


# --- Sprostowania (żywy test 2026-10-05: tekst 32016R0679 --fragment "art. 4" drukował „informacje"
# zamiast „wszelkie informacje" bez słowa o sprostowaniu 32016R0679R(02)) ------------------------------

RODO, RODO_KONS = "32016R0679", "02016R0679-20160504"
# wiersze SPARQL odtworzone z odpowiedzi CELLAR (corrects_resource_legal + język POL + skład konsolidacji);
# R(01) nie ma wersji polskiej, więc w zapytaniu z językiem POL go nie ma
SPROST_RODO_WIERSZE = [_wiersz(c2="32016R0679R(02)", date="2018-05-23", kc=RODO_KONS),
                       _wiersz(c2="32016R0679R(03)", date="2021-03-04", kc=RODO_KONS)]
# fragmenty tekstów sprostowań z CELLAR (XHTML; nagłówki miejsc jak w Dz.U.)
SPROST_R02 = ("<p>Sprostowanie do rozporządzenia Parlamentu Europejskiego i Rady (UE) 2016/679</p>"
              "<p>Strona 14, motyw 71, zdania piąte i szóste:</p><p>zamiast: …</p>"
              "<p>Strona 33, art. 4 ust. 1:</p><p>zamiast:</p>"
              "<p>„dane osobowe” oznaczają informacje o zidentyfikowanej …</p><p>powinno być:</p>"
              "<p>„dane osobowe” oznaczają wszelkie informacje o zidentyfikowanej …</p>"
              "<p>Strona 37, art. 6 ust. 4 lit. c):</p><p>zamiast: …</p>"
              "<p>Strona 39, art. 10:</p><p>zamiast:</p><p>„Artykuł 10</p>"
              "<p>Przetwarzanie danych osobowych dotyczących wyroków skazujących i naruszeń prawa</p>"
              "<p>Strona 62, art. 47, tytuł:</p><p>zamiast: …</p>"
              "<p>Strona 74, art. 64 ust. 6, 7 i 8:</p><p>zamiast: …</p>").encode()
SPROST_R03 = ("<p>Sprostowanie do rozporządzenia (UE) 2016/679</p><p>Strona 81, art. 82 ust. 2:</p>"
              "<p>zamiast:</p><p>… zgodnymi z prawem instrukcjami administratora …</p>"
              "<p>powinno być:</p><p>… zgodnymi z prawem poleceniami administratora …</p>").encode()
RODO_XHTML = ("<p class=\"oj-ti-art\">Artykuł 4</p><p>Definicje</p>"
              "<p>1) „dane osobowe” oznaczają informacje o zidentyfikowanej osobie;</p>"
              "<p class=\"oj-ti-art\">Artykuł 5</p><p>Zasady</p>"
              "<p class=\"oj-ti-art\">Artykuł 82</p><p>2. … zgodnymi z prawem instrukcjami administratora …</p>").encode()


def _http_cellar(teksty):
    """Podmiana _http: URL kończący się (po zakodowaniu) danym CELEX-em → bajty; reszta → 404."""
    wywolania = []

    def fake(url, data=None, headers=None, timeout=60):
        wywolania.append((url, headers))
        for celex, tresc in teksty.items():
            if eurlex.urllib.parse.unquote(url).endswith("/" + celex):
                return tresc, "application/xhtml+xml"
        raise SystemExit(f"BŁĄD: nie znaleziono zasobu (404): {url}\nSprawdź numer CELEX")
    fake.wywolania = wywolania
    return fake


class TestSprostowania(unittest.TestCase):
    TEKSTY = {RODO: RODO_XHTML, "32016R0679R(02)": SPROST_R02, "32016R0679R(03)": SPROST_R03}

    def _tekst(self, celex, fragment=None, strict=False, sprost=SPROST_RODO_WIERSZE, kons=(RODO_KONS,),
               pdf=None):
        out = io.StringIO()
        fake_sparql = mock.Mock(return_value=sprost)
        argv = ["eurlex.py", "tekst", celex] + (["--fragment", fragment] if fragment else []) \
            + (["--strict"] if strict else []) + (["--pdf", pdf] if pdf else [])
        with mock.patch.object(eurlex, "_http", _http_cellar(self.TEKSTY)), \
                mock.patch.object(eurlex, "_sparql", fake_sparql), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=list(kons)), \
                mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            eurlex.main()
        return out.getvalue(), fake_sparql

    def test_zakres_z_naglowkow_sprostowania(self):
        z = eurlex._zakres_z_tekstu(eurlex.html_to_text(SPROST_R02.decode()))
        self.assertEqual(z["art"], ["4", "6", "10", "47", "64"])
        self.assertEqual(z["inne"], ["motywy"])
        self.assertEqual(eurlex._zakres_z_tekstu("Page 33, Article 4(1):\nfor: …\n")["art"], ["4"])
        self.assertEqual(eurlex._zakres_z_tekstu("Artykuł 10\nTreść bez nagłówka miejsca.\n"),
                         {"art": [], "inne": []})

    def test_sprostowania_z_sparql(self):
        fake = mock.Mock(return_value=SPROST_RODO_WIERSZE + [_wiersz(date="2016-04-27")])
        with mock.patch.object(eurlex, "_sparql", fake):
            wynik = eurlex._sprostowania(RODO_KONS, "POL")  # z wersji skonsolidowanej → akt bazowy
        q = fake.call_args.args[0]
        self.assertIn('"32016R0679"^^', q)
        self.assertIn("resource_legal_corrects_resource_legal ?w", q)
        self.assertIn("language/POL>", q)
        self.assertEqual([s["celex"] for s in wynik], ["32016R0679R(02)", "32016R0679R(03)"])
        self.assertEqual(wynik[0], {"celex": "32016R0679R(02)", "data": "2018-05-23",
                                    "konsolidacje": [RODO_KONS]})

    def test_sprostowanie_samo_nie_ma_sprostowan(self):
        with mock.patch.object(eurlex, "_sparql", side_effect=AssertionError("bez zapytania")):
            self.assertEqual(eurlex._sprostowania("32016R0679R(02)", "POL"), [])

    def test_tekst_bazowy_art_4_ostrzega_i_wskazuje_wersje_sprostowana(self):
        out, _ = self._tekst(RODO, fragment="art. 4")
        self.assertIn("UWAGA: art. 4 SPROSTOWANO (32016R0679R(02))", out)
        self.assertIn("NIESPROSTOWANE", out)
        self.assertIn(f'tekst {RODO_KONS} --jezyk pol --fragment "art. 4"', out)
        self.assertIn("32016R0679R(02) (2018-05-23): art. 4, 6, 10, 47, 64; motywy", out)
        self.assertIn("32016R0679R(03) (2021-03-04): art. 82", out)
        self.assertIn("oznaczają informacje", out)  # treść główna nadal jest drukowana
        self.assertLess(out.index("SPROSTOWANO"), out.index("Artykuł 4"))

    def test_tekst_bazowy_art_82_wskazuje_R03(self):
        out, _ = self._tekst(RODO, fragment="art. 82")
        self.assertIn("UWAGA: art. 82 SPROSTOWANO (32016R0679R(03))", out)

    def test_artykul_bez_sprostowania_tylko_ogolne_ostrzezenie(self):
        out, _ = self._tekst(RODO, fragment="art. 5")
        self.assertNotIn("art. 5 SPROSTOWANO", out)
        self.assertIn("akt ma SPROSTOWANIA w języku pol", out)

    def test_brak_tekstu_sprostowania_nie_blokuje_listy(self):
        self.TEKSTY = {RODO: RODO_XHTML}
        out, _ = self._tekst(RODO, fragment="art. 4")
        self.assertIn("32016R0679R(02) (2018-05-23): zakres nieustalony", out)

    def test_strict_blokuje_niesprostowany_tekst_bazowy(self):
        with self.assertRaises(SystemExit) as caught:
            self._tekst(RODO, fragment="art. 4", strict=True)
        msg = str(caught.exception.code)
        self.assertIn("ma sprostowania w języku pol (32016R0679R(02) z 2018-05-23", msg)
        self.assertIn(f"tekst {RODO_KONS}", msg)
        self.assertNotIn("akt bazowy + zmiany", msg)

    def test_strict_bez_stdout(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_http", _http_cellar(self.TEKSTY)), \
                mock.patch.object(eurlex, "_sparql", return_value=SPROST_RODO_WIERSZE), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=[]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "--strict", "tekst", RODO]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                eurlex.main()
        self.assertEqual(out.getvalue(), "")

    def test_strict_awaria_kontroli_sprostowan_blokuje(self):
        with mock.patch.object(eurlex, "_sparql", side_effect=eurlex.VerificationUnknown("timeout")):
            with self.assertRaises(eurlex.VerificationUnknown):
                eurlex._kontrole_tresci(RODO, "POL", strict=True)

    def test_bez_strict_awaria_kontroli_sprostowan_ostrzega(self):
        with mock.patch.object(eurlex, "_sparql", side_effect=eurlex.VerificationUnknown("timeout")), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=[]):
            out = eurlex._kontrole_tresci(RODO, "POL")
        self.assertTrue(any("nie udało się zweryfikować, czy akt 32016R0679 ma sprostowania" in w for w in out))

    def test_wersja_skonsolidowana_ze_sprostowaniem_bez_ostrzezenia_i_przechodzi_strict(self):
        teksty = dict(self.TEKSTY)
        teksty[RODO_KONS] = RODO_XHTML.replace("informacje".encode(), "wszelkie informacje".encode())
        self.TEKSTY = teksty
        out, _ = self._tekst(RODO_KONS, fragment="art. 4", strict=True)
        self.assertIn("wszelkie informacje", out)
        self.assertNotIn("akt ma SPROSTOWANIA", out)
        self.assertNotIn("nie figuruje", out)
        self.assertIn("SPROSTOWANIAMI i aktami zmieniającymi", out)  # nowa formuła urzędowego cytatu

    def test_wersja_skonsolidowana_bez_sprostowania_ostrzega(self):
        teksty = dict(self.TEKSTY)
        teksty[RODO_KONS] = RODO_XHTML
        self.TEKSTY = teksty
        wiersze = [_wiersz(c2="32016R0679R(04)", date="2027-01-01")]
        out, _ = self._tekst(RODO_KONS, sprost=wiersze)
        self.assertIn("sprostowanie 32016R0679R(04) (2027-01-01) w języku pol nie figuruje w składzie", out)

    def test_pdf_aktu_bazowego_tez_ostrzega(self):
        teksty = dict(self.TEKSTY)
        teksty["DOC_1"] = b"%PDF-1.4 rodo"
        self.TEKSTY = teksty
        wiersze = SPROST_RODO_WIERSZE + [_wiersz(l="POL", mtype="pdfa1a",
                                                  man="http://publications.europa.eu/resource/cellar/3e48.0018.01",
                                                  item="http://publications.europa.eu/resource/cellar/3e48.0018.01/DOC_1")]
        with tempfile.TemporaryDirectory() as d:
            sciezka = os.path.join(d, "rodo.pdf")
            out, _ = self._tekst(RODO, sprost=wiersze, pdf=sciezka)
            self.assertEqual(pathlib.Path(sciezka).read_bytes(), b"%PDF-1.4 rodo")
        self.assertIn("akt ma SPROSTOWANIA w języku pol", out)

    def test_meta_wymienia_sprostowania_i_nie_blokuje_w_strict(self):
        meta = {RODO: TestMetaWersjaSkonsolidowana.META[RODO]}
        fake = _FakeCellar(meta, sprost={RODO: SPROST_RODO_WIERSZE})
        for argv in (["meta", RODO], ["--strict", "meta", RODO]):
            with self.subTest(argv=argv):
                out = io.StringIO()
                with mock.patch.object(eurlex, "_sparql", fake), \
                        mock.patch.object(eurlex, "_konsolidacje", return_value=[RODO_KONS]), \
                        mock.patch.object(sys, "argv", ["eurlex.py", *argv]), contextlib.redirect_stdout(out):
                    eurlex.main()
                self.assertIn("akt ma SPROSTOWANIA w języku pol: 32016R0679R(02) (2018-05-23), "
                              "32016R0679R(03) (2021-03-04)", out.getvalue())
                self.assertIn(f"brzmienie poprawione: tekst {RODO_KONS}", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(eurlex, "_sparql", fake), \
                mock.patch.object(eurlex, "_konsolidacje", return_value=[RODO_KONS]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "meta", RODO, "--json"]), \
                contextlib.redirect_stdout(out):
            eurlex.main()
        self.assertEqual([s["celex"] for s in json.loads(out.getvalue())["sprostowania"]],
                         ["32016R0679R(02)", "32016R0679R(03)"])

    def test_wskazuje_najnowsza_konsolidacje_a_nie_zastapiona_wymieniajaca_sprostowanie(self):
        # AI Act (deu): R(01) wymienia tylko 02024R1689-20240712 (zastąpiona, 404 w CELLAR);
        # 02024R1689-20260727 jest kumulatywna, ale sprostowań w metadanych nie powtarza
        wiersze = [_wiersz(c2="32024R1689R(01)", date="2025-10-09", kc="02024R1689-20240712")]
        kons = ["02024R1689-20260727", "02024R1689-20240712"]
        self.TEKSTY = {AI_BAZA: RODO_XHTML, "32024R1689R(01)": b"<p>Seite 52, Artikel 3 Nummer 1:</p>"}
        out, _ = self._tekst(AI_BAZA, fragment="art. 4", sprost=wiersze, kons=kons)
        self.assertIn("Brzmienie po sprostowaniu: tekst 02024R1689-20260727", out)
        self.assertIn("32024R1689R(01) (2025-10-09): art. 3", out)
        with self.assertRaises(SystemExit) as caught:
            self._tekst(AI_BAZA, sprost=wiersze, kons=kons, strict=True)
        self.assertIn("do analizy: tekst 02024R1689-20260727", str(caught.exception.code))
        self.assertNotIn("tekst 02024R1689-20240712", str(caught.exception.code))

    def test_komunikaty_nie_kaza_cytowac_samego_aktu_bazowego_i_zmian(self):
        out = io.StringIO()
        with mock.patch.object(eurlex, "_konsolidacje", return_value=[RODO_KONS]), \
                contextlib.redirect_stdout(out):
            eurlex.cmd_skonsolidowany(argparse.Namespace(celex=[RODO], json=False))
        self.assertNotIn("akt bazowy + zmiany", out.getvalue())
        self.assertIn("SPROSTOWANIAMI", out.getvalue())
        self.assertNotIn("akt bazowy + zmiany", "\n".join(eurlex._ostrzezenia_konsolidacja(RODO_KONS, kons=[RODO_KONS])))


# --- Starsze akty: tylko manifestacja „html" i PDF pod DOC_2 (żywy test 2026-10-05: tekst i --pdf
# 31995L0046 / 32002L0058 kończyły się 404 „Sprawdź numer CELEX", choć akty istnieją) -------------------

CELLAR_ITEM = "http://publications.europa.eu/resource/cellar/"
# 32002L0058 — wiersze _manifestacje (wszystkie języki) odtworzone z SPARQL CELLAR
EPRIV_MANIF = [
    _wiersz(l="POL", mtype="html", man=CELLAR_ITEM + "cb5af945.0018.01", item=CELLAR_ITEM + "cb5af945.0018.01/DOC_1"),
    _wiersz(l="POL", mtype="pdf", man=CELLAR_ITEM + "cb5af945.0018.02", item=CELLAR_ITEM + "cb5af945.0018.02/DOC_2"),
    _wiersz(l="POL", mtype="print", man=CELLAR_ITEM + "cb5af945.0018.03"),
    _wiersz(l="ENG", mtype="html", man=CELLAR_ITEM + "cb5af945.0004.01", item=CELLAR_ITEM + "cb5af945.0004.01/DOC_1"),
    _wiersz(l="ENG", mtype="pdf", man=CELLAR_ITEM + "cb5af945.0004.02", item=CELLAR_ITEM + "cb5af945.0004.02/DOC_1"),
]
EPRIV_HTML = ("<html><head><title>EUR-Lex - 32002L0058 - PL</title></head><body>"
              "<p>Artykuł 5</p><p>Poufność komunikacji</p><p>1. Państwa Członkowskie zapewniają …</p>"
              "<p>Artykuł 6</p><p>Dane o ruchu</p></body></html>").encode()


class TestStareAkty(unittest.TestCase):

    def test_negocjacja_dopuszcza_html(self):
        fake = mock.Mock(return_value=(EPRIV_HTML, "text/html;charset=UTF-8"))
        with mock.patch.object(eurlex, "_http", fake):
            self.assertEqual(eurlex._pobierz_tekst("32002L0058", "pol"), EPRIV_HTML)
        akcept = fake.call_args.kwargs["headers"]["Accept"]
        self.assertIn("application/xhtml+xml", akcept)
        self.assertIn("text/html", akcept)

    def test_404_negocjacji_tekst_z_elementu_manifestacji_html(self):
        teksty = {"cb5af945.0018.01/DOC_1": EPRIV_HTML}

        def fake_http(url, data=None, headers=None, timeout=60):
            for k, v in teksty.items():
                if url.endswith(k):
                    return v, "text/html"
            raise SystemExit(f"BŁĄD: nie znaleziono zasobu (404): {url}")
        out = io.StringIO()
        with mock.patch.object(eurlex, "_http", side_effect=fake_http), \
                mock.patch.object(eurlex, "_sparql", return_value=EPRIV_MANIF), \
                mock.patch.object(eurlex, "_kontrole_tresci", return_value=[]), \
                mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "32002L0058", "--fragment", "art. 5"]), \
                contextlib.redirect_stdout(out):
            eurlex.main()
        self.assertIn("Poufność komunikacji", out.getvalue())
        self.assertNotIn("EUR-Lex - 32002L0058", out.getvalue())  # <title> nie trafia do tekstu
        self.assertNotIn("Dane o ruchu", out.getvalue())

    def _404(self, celex, rows, jezyk="pol"):
        args = argparse.Namespace(celex=[celex], jezyk=jezyk, json=False, strict=False, pdf=None, fragment=None)
        with mock.patch.object(eurlex, "_http", side_effect=SystemExit("BŁĄD: nie znaleziono zasobu (404): x")), \
                mock.patch.object(eurlex, "_sparql", return_value=rows), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                eurlex.cmd_tekst(args)
        return str(caught.exception.code)

    def test_404_akt_istnieje_tylko_pdf_w_jezyku(self):
        # bez pdftotext w PATH: dotychczasowy komunikat z odesłaniem do --pdf (+ podpowiedź instalacji)
        rows = [r for r in EPRIV_MANIF if not (r["l"]["value"] == "POL" and r["mtype"]["value"] == "html")]
        with mock.patch.object(eurlex.shutil, "which", return_value=None):
            msg = self._404("32002L0058", rows)
        self.assertIn("akt 32002L0058 istnieje w CELLAR", msg)
        self.assertIn("--pdf", msg)
        self.assertIn("tekst HTML jest w: eng", msg)
        self.assertIn("pdftotext", msg)
        self.assertNotIn("Sprawdź numer CELEX", msg)

    def test_404_akt_istnieje_brak_jezyka(self):
        msg = self._404("32002L0058", [r for r in EPRIV_MANIF if r["l"]["value"] == "ENG"])
        self.assertIn("nie ma tekstu w języku pol", msg)
        self.assertIn("eng", msg)
        self.assertNotIn("Sprawdź numer CELEX", msg)

    def test_404_awaria_metadanych_nie_twierdzi_ze_celex_zly(self):
        args = argparse.Namespace(celex=["32002L0058"], jezyk="pol", json=False, strict=False, pdf=None, fragment=None)
        with mock.patch.object(eurlex, "_http", side_effect=SystemExit("BŁĄD: nie znaleziono zasobu (404): x")), \
                mock.patch.object(eurlex, "_sparql", side_effect=eurlex.VerificationUnknown("timeout")):
            with self.assertRaises(SystemExit) as caught:
                eurlex.cmd_tekst(args)
        self.assertIn("meta 32002L0058", str(caught.exception.code))
        self.assertNotIn("Sprawdź numer CELEX", str(caught.exception.code))

    def test_pdf_url_z_elementu_manifestacji_nie_doc_1(self):
        przypadki = [
            ("32002L0058", "POL", EPRIV_MANIF, CELLAR_ITEM + "cb5af945.0018.02/DOC_2"),
            # wersja skonsolidowana e-Privacy: pdfa1a pod DOC_2 (stary kod: …/DOC_1 → 404)
            ("02002L0058-20091219", "POL",
             [_wiersz(l="POL", mtype="pdfa1a", man=CELLAR_ITEM + "6def8269.0017.04",
                      item=CELLAR_ITEM + "6def8269.0017.04/DOC_2"),
              _wiersz(l="POL", mtype="xhtml", man=CELLAR_ITEM + "6def8269.0017.03",
                      item=CELLAR_ITEM + "6def8269.0017.03/DOC_3")],
             CELLAR_ITEM + "6def8269.0017.04/DOC_2"),
            # RODO: pdfa1a pod DOC_1 (bez zmian)
            ("32016R0679", "POL",
             [_wiersz(l="POL", mtype="pdfa1a", man=CELLAR_ITEM + "3e485e15.0018.01",
                      item=CELLAR_ITEM + "3e485e15.0018.01/DOC_1"),
              _wiersz(l="POL", mtype="fmx4", man=CELLAR_ITEM + "3e485e15.0018.02",
                      item=CELLAR_ITEM + "3e485e15.0018.02/DOC_2")],
             CELLAR_ITEM + "3e485e15.0018.01/DOC_1"),
        ]
        for celex, lang, rows, want in przypadki:
            with self.subTest(celex=celex):
                fake = mock.Mock(return_value=[r for r in rows if r["l"]["value"] == lang])
                with mock.patch.object(eurlex, "_sparql", fake):
                    self.assertEqual(eurlex._pdf_url(celex, lang), (want, []))
                self.assertIn("item_belongs_to_manifestation", fake.call_args.args[0])

    def test_pdf_preferuje_pdfa_i_zglasza_pozostale_pliki(self):
        rows = [_wiersz(l="POL", mtype="pdf", man=CELLAR_ITEM + "a.01", item=CELLAR_ITEM + "a.01/DOC_1"),
                _wiersz(l="POL", mtype="pdfa2a", man=CELLAR_ITEM + "a.04", item=CELLAR_ITEM + "a.04/DOC_10"),
                _wiersz(l="POL", mtype="pdfa2a", man=CELLAR_ITEM + "a.04", item=CELLAR_ITEM + "a.04/DOC_9")]
        with mock.patch.object(eurlex, "_sparql", return_value=rows):
            self.assertEqual(eurlex._pdf_url("X", "POL"),
                             (CELLAR_ITEM + "a.04/DOC_9", [CELLAR_ITEM + "a.04/DOC_10"]))

    def test_pdf_brak_manifestacji(self):
        with mock.patch.object(eurlex, "_sparql", return_value=[r for r in EPRIV_MANIF if r["mtype"]["value"] == "html"]):
            self.assertEqual(eurlex._pdf_url("32002L0058", "POL"), (None, []))

    def test_cmd_pdf_zapisuje_doc_2_po_https(self):
        fake_http = mock.Mock(return_value=(b"%PDF-1.4 e-privacy", "application/pdf"))
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d:
            sciezka = os.path.join(d, "ep.pdf")
            with mock.patch.object(eurlex, "_http", fake_http), \
                    mock.patch.object(eurlex, "_sparql",
                                      return_value=[r for r in EPRIV_MANIF if r["l"]["value"] == "POL"]), \
                    mock.patch.object(eurlex, "_kontrole_tresci", return_value=[]), \
                    mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "32002L0058", "--pdf", sciezka]), \
                    contextlib.redirect_stdout(out):
                eurlex.main()
            self.assertEqual(pathlib.Path(sciezka).read_bytes(), b"%PDF-1.4 e-privacy")
        self.assertEqual(fake_http.call_args.args[0], CELLAR_ITEM + "cb5af945.0018.02/DOC_2")
        self.assertIn("źródło: https://publications.europa.eu/resource/cellar/cb5af945.0018.02/DOC_2", out.getvalue())

    def test_cmd_pdf_odrzuca_odpowiedz_bez_sygnatury_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            sciezka = os.path.join(d, "ep.pdf")
            with mock.patch.object(eurlex, "_http", return_value=(b"<html>blad</html>", "text/html")), \
                    mock.patch.object(eurlex, "_sparql",
                                      return_value=[r for r in EPRIV_MANIF if r["l"]["value"] == "POL"]), \
                    mock.patch.object(eurlex, "_kontrole_tresci", return_value=[]), \
                    mock.patch.object(sys, "argv", ["eurlex.py", "tekst", "32002L0058", "--pdf", sciezka]), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(SystemExit, "nie zwrócił pliku PDF"):
                    eurlex.main()
            self.assertFalse(os.path.exists(sciezka))


def _strona_pdf(naglowek, lewy, prawy, pod=(), szer=62):
    """Strona jak z `pdftotext -layout` Dz.Urz. UE: nagłówek, dwa łamy obok siebie, wiersze pod nimi."""
    wiersze = list(naglowek)
    for i in range(max(len(lewy), len(prawy))):
        l = lewy[i] if i < len(lewy) else ""
        p = prawy[i] if i < len(prawy) else ""
        wiersze.append((l.ljust(szer) + p).rstrip())
    return "\n".join(wiersze + list(pod))


# Wycinek układu PDF wydania specjalnego (32004R0883, pol): nagłówek wydania specjalnego, CELEX nad aktem,
# nagłówek pierwotnego Dz.Urz., motywy i przypis w lewym łamie, artykuły w prawym; na stronie 2 — dalszy
# ciąg art. 1 z przeniesieniem wyrazu, art. 2 w prawym łamie i przypisy w dwóch łamach w jednym wierszu.
PDF_STRONA_1 = _strona_pdf(
    ["72                   PL                     Dziennik Urzędowy Unii Europejskiej                05/t. 5",
     "",
     "32004R0883",
     "",
     "30.4.2004                       DZIENNIK URZĘDOWY UNII EUROPEJSKIEJ                       L 166/1",
     "",
     "                 ROZPORZĄDZENIE PARLAMENTU EUROPEJSKIEGO I RADY (WE) nr 883/2004",
     "                              z dnia 29 kwietnia 2004 r.",
     ""],
    ["PARLAMENT EUROPEJSKI I RADA UNII EUROPEJSKIEJ,",
     "",
     "uwzględniając Traktat ustanawiający Wspólnotę",
     "Europejską (1),",
     "",
     "(1)    Zasady mające na celu koordynację w zakresie",
     "       zabezpieczenia społecznego wpisują się w ramy",
     "       swobodnego przepływu osób.",
     "",
     "(2)    Traktat nie przewiduje podejmowania przez",
     "       władze inne niż określone w art. 308 działań.",
     "",
     "(1) Dz.U. C 38 z 12.2.1999, str. 10."],
    ["                    Artykuł 1",
     "",
     "                    Definicje",
     "",
     "Do celów stosowania niniejszego rozporządzenia:",
     "",
     "a) określenie „praca najemna” oznacza wszelką",
     "   pracę lub sytuację równoważną, traktowaną jako",
     "   taką do celów stosowania ustawodawstwa w za-"],
    pod=["", "                                                                  73"])
PDF_STRONA_2 = _strona_pdf(
    ["05/t. 5            PL                    Dziennik Urzędowy Unii Europejskiej                    73",
     ""],
    ["   kresie zabezpieczenia społecznego;",
     "",
     "b) określenie „pobyt” oznacza pobyt czasowy (2);",
     "",
     "",
     "c) określenie „zamieszkanie” oznacza miejsce,",
     "   w którym osoba zwykle przebywa;",
     "",
     "",
     "(2) Dz.U. L 149 z 5.7.1971, str. 2.      (3) Dz.U. L 1 z 1.1.2000, str. 1."],
    ["                    Artykuł 2",
     "",
     "               Zakres podmiotowy",
     "",
     "1. Niniejsze rozporządzenie stosuje się do oby-",
     "wateli Państwa Członkowskiego (3).",
     "",
     "2. Ponadto niniejsze rozporządzenie stosuje się do",
     "osób pozostałych przy życiu."])
PDF_LAYOUT = PDF_STRONA_1 + "\f" + PDF_STRONA_2 + "\f"


class TestTekstZPdf(unittest.TestCase):
    """Akt w CELLAR tylko jako PDF (bez HTML/XHTML w danym języku): tekst przez pdftotext -layout."""

    def test_naglowki_stron_i_numer_celex_usuniete(self):
        txt, puste, stron = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        self.assertEqual((puste, stron), (0, 2))
        self.assertNotIn("Dziennik Urzędowy", txt)
        self.assertNotIn("DZIENNIK URZĘDOWY", txt)
        self.assertNotRegex(txt, r"(?m)^32004R0883$")
        self.assertNotRegex(txt, r"(?m)^\s*73\s*$")

    def test_naglowek_sprostowania_tez_usuniety(self):
        linie = ["32004R0883R(06)", "", "L 166/1   DZIENNIK URZĘDOWY UNII EUROPEJSKIEJ   30.4.2004", "Treść"]
        self.assertEqual([l for l in eurlex._pdf_bez_naglowka(linie) if l.strip()], ["Treść"])

    def test_lamy_rozdzielone_lewy_przed_prawym(self):
        txt, _, _ = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        self.assertLess(txt.index("(2) Traktat nie przewiduje"), txt.index("Artykuł 1"))
        self.assertLess(txt.index("Artykuł 1"), txt.index("b) określenie „pobyt”"))
        self.assertLess(txt.index("c) określenie „zamieszkanie”"), txt.index("Artykuł 2"))
        # motyw nie miesza się z artykułem z prawego łamu w jednym wierszu
        self.assertNotRegex(txt, r"koordynację.*Artykuł")

    def test_sklejanie_wierszy_i_dzielonych_wyrazow(self):
        txt, _, _ = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        self.assertIn("(1) Zasady mające na celu koordynację w zakresie zabezpieczenia społecznego wpisują "
                      "się w ramy swobodnego przepływu osób.", txt)
        # przeniesienie przez stronę: „w za-” (strona 1, prawy łam) + „kresie” (strona 2, lewy łam)
        self.assertIn("taką do celów stosowania ustawodawstwa w zakresie zabezpieczenia społecznego;", txt)
        self.assertIn("1. Niniejsze rozporządzenie stosuje się do obywateli Państwa Członkowskiego (3).", txt)
        self.assertIn("uwzględniając Traktat ustanawiający Wspólnotę Europejską (1),", txt)

    def test_naglowek_artykulu_i_tytul_w_osobnych_liniach(self):
        txt, _, _ = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        self.assertRegex(txt, r"(?m)^Artykuł 1\nDefinicje\nDo celów stosowania")
        self.assertRegex(txt, r"(?m)^Artykuł 2\nZakres podmiotowy\n1\. Niniejsze")

    def test_przypisy_na_koncu_za_granica(self):
        txt, _, _ = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        przypisy = txt.index(eurlex.GRANICA)
        for nr in ("(1) Dz.U. C 38", "(2) Dz.U. L 149", "(3) Dz.U. L 1 z"):
            self.assertGreater(txt.index(nr), przypisy, nr)
        # przypisy w dwóch łamach jednego wiersza: najpierw lewy, potem prawy
        self.assertLess(txt.index("(2) Dz.U. L 149"), txt.index("(3) Dz.U. L 1 z"))

    def test_fragment_artykulu_bez_przypisow_i_sasiada(self):
        txt, _, _ = eurlex.pdf_do_tekstu(PDF_LAYOUT)
        spans = eurlex._fragmenty(txt, "art. 1")
        self.assertEqual(len(spans), 1)
        frag = txt[spans[0][0]:spans[0][1]]
        self.assertIn("c) określenie „zamieszkanie”", frag)
        self.assertNotIn("Dz.U.", frag)
        self.assertNotIn("Zakres podmiotowy", frag)

    def test_strona_jednolamowa_bez_zmian(self):
        strona = ["Artykuł 3", "", "Niniejsze rozporządzenie wchodzi w życie dwudziestego dnia po jego "
                  "opublikowaniu w Dzienniku Urzędowym Unii Europejskiej."] * 4
        tresc, przypisy = eurlex._pdf_lamy(strona)
        self.assertEqual([l.lstrip(eurlex._PDF_SRODEK) for l in tresc], strona)
        self.assertEqual(przypisy, [])

    def test_pdftotext_layout_wola_program_z_layout_i_sprzata(self):
        sciezki = []

        def fake_run(cmd, capture_output, timeout):
            sciezki.append(cmd[-2])
            self.assertEqual(cmd[:4], ["pdftotext", "-layout", "-enc", "UTF-8"])
            self.assertTrue(os.path.exists(cmd[-2]))
            return mock.Mock(returncode=0, stdout="tekst\f".encode())
        with mock.patch.object(eurlex.subprocess, "run", side_effect=fake_run):
            self.assertEqual(eurlex._pdftotext_layout(b"%PDF-1.4"), "tekst\f")
        self.assertFalse(os.path.exists(sciezki[0]))
        with mock.patch.object(eurlex.subprocess, "run", return_value=mock.Mock(returncode=1, stdout=b"")):
            self.assertEqual(eurlex._pdftotext_layout(b"%PDF-1.4"), "")

    ZRODLO = "https://publications.europa.eu/resource/cellar/21eb3af6.0018.01/DOC_2"

    def _tekst(self, fragment=None, strict=False, kontrole=None, layout=PDF_LAYOUT, which="/usr/bin/pdftotext"):
        args = argparse.Namespace(celex=["32004R0883"], jezyk="pol", json=False, strict=strict, pdf=None,
                                  fragment=fragment)
        brak = eurlex._TylkoPdf("BŁĄD: akt 32004R0883 istnieje w CELLAR, ale w języku pol nie ma wersji "
                                "HTML/XHTML (formaty: pdf, print) — pobierz urzędowy PDF: tekst 32004R0883 "
                                "--jezyk pol --pdf plik.pdf.")
        out = io.StringIO()
        pobierz_pdf = mock.Mock(return_value=(b"%PDF-1.4", self.ZRODLO, []))
        kontrole = kontrole if kontrole is not None else mock.Mock(return_value=[])
        with mock.patch.object(eurlex, "_pobierz_tekst", side_effect=brak), \
                mock.patch.object(eurlex.shutil, "which", return_value=which), \
                mock.patch.object(eurlex, "_pobierz_pdf", pobierz_pdf), \
                mock.patch.object(eurlex, "_pdftotext_layout", return_value=layout), \
                mock.patch.object(eurlex, "_kontrole_tresci", kontrole), \
                contextlib.redirect_stdout(out):
            try:
                eurlex.cmd_tekst(args)
            except SystemExit as e:
                return out.getvalue(), e, pobierz_pdf, kontrole
        return out.getvalue(), None, pobierz_pdf, kontrole

    def test_cmd_tekst_akt_tylko_pdf_czyta_pdf(self):
        out, blad, _, _ = self._tekst(fragment="art. 2")
        self.assertIsNone(blad)
        self.assertIn("tekst z urzędowego PDF przez pdftotext -layout", out.splitlines()[0])
        self.assertIn(f"EURLEX_TEXT_SOURCE_PDF={self.ZRODLO}", out)
        self.assertIn("WYEKSTRAHOWANY z urzędowego PDF", out)
        self.assertIn("--pdf plik.pdf", out)
        self.assertIn("Zakres podmiotowy", out)
        self.assertNotIn("Definicje", out)
        self.assertNotIn(eurlex.GRANICA, out)

    def test_cmd_tekst_pelny_tekst_z_pdf_bez_znaku_granicy(self):
        out, blad, _, _ = self._tekst()
        self.assertIsNone(blad)
        self.assertIn("Przypisy (z dołu stron PDF):", out)
        self.assertNotIn(eurlex.GRANICA, out)

    def test_strict_przepuszcza_tekst_z_wlasnego_pdf(self):
        out, blad, _, kontrole = self._tekst(fragment="art. 1", strict=True)
        self.assertIsNone(blad)
        self.assertIn("Definicje", out)
        self.assertTrue(kontrole.call_args.args[2])  # kontrole treści w trybie strict nadal działają

    def test_strict_sprostowanie_nadal_blokuje_przed_wydrukiem(self):
        blokada = mock.Mock(side_effect=SystemExit("BŁĄD: akt 32004R0883 ma sprostowania w języku pol"))
        out, blad, _, _ = self._tekst(strict=True, kontrole=blokada)
        self.assertIn("sprostowania", str(blad.code))
        self.assertEqual(out, "")

    def test_bez_pdftotext_dotychczasowy_komunikat(self):
        out, blad, pobierz_pdf, _ = self._tekst(which=None)
        self.assertIn("--pdf plik.pdf", str(blad.code))
        self.assertIn("pdftotext", str(blad.code))
        pobierz_pdf.assert_not_called()
        self.assertEqual(out, "")

    def test_pdf_bez_warstwy_tekstowej(self):
        out, blad, _, _ = self._tekst(layout="")
        self.assertIn("pdftotext nie zwrócił z niego tekstu", str(blad.code))
        self.assertIn("--pdf plik.pdf", str(blad.code))
        self.assertEqual(out, "")

    def test_zakres_sprostowania_traktuje_tylko_pdf_jak_brak_tekstu(self):
        with mock.patch.object(eurlex, "_pobierz_tekst", side_effect=eurlex._TylkoPdf("tylko PDF")):
            self.assertIsNone(eurlex._zakres_sprostowania("32004R0883R(06)", "pol"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
