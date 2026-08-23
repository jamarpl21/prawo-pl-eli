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


def _lit(v):
    return {"type": "literal", "value": v}


def _wiersz(**kw):
    return {k: _lit(v) for k, v in kw.items()}


TYP = "http://publications.europa.eu/resource/authority/resource-type/"


class _FakeCellar:
    """Podmiana _sparql rozpoznająca zapytania po CELEX-ie w literale i po właściwości.

    meta: {celex: [wiersze]}; zmiany: {celex: [celexy aktów zmieniających]} — brak klucza = []."""

    def __init__(self, meta, zmiany=None, awaria_zmian=False):
        self.meta, self.zmiany, self.awaria_zmian = meta, zmiany or {}, awaria_zmian
        self.zapytania = []

    def __call__(self, q, soft=False):
        self.zapytania.append(q)
        m = re.search(r'resource_legal_id_celex "([^"]+)"', q)
        celex = m.group(1) if m else None
        if "resource_legal_amends_resource_legal ?w" in q and "BIND" not in q:
            if self.awaria_zmian:
                raise eurlex.VerificationUnknown("timeout")
            return [_wiersz(c2=c) for c in self.zmiany.get(celex, [])]
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

    def test_akt_bazowy_404_zostaje_generyczny(self):
        args = argparse.Namespace(celex=["39999R9999"], jezyk="pol", json=False, strict=False, pdf=None, fragment=None)
        with mock.patch.object(eurlex, "_http", side_effect=SystemExit("BŁĄD: nie znaleziono zasobu (404): x")):
            with self.assertRaisesRegex(SystemExit, "nie znaleziono zasobu"):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
