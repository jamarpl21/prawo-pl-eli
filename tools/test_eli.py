#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eli.py pure functions (no network). Run: python3 tools/test_eli.py"""
import argparse
import contextlib
import io
import sys
import importlib.util
import pathlib
import re
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eli", ROOT / "plugins/prawo-pl-eli/skills/prawo-pl-eli/scripts/eli.py")
eli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eli)


class TestActPath(unittest.TestCase):
    def test_signature_forms(self):
        cases = [
            (["DU", "2024", "18"], "/acts/DU/2024/18"),
            (["DU/2024/18"], "/acts/DU/2024/18"),
            (["Dz.U. 2024 poz. 18"], "/acts/DU/2024/18"),
            (["Dz.U. 1997 nr 78 poz. 483"], "/acts/DU/1997/483"),
            (["WDU20240000018"], "/acts/WDU20240000018"),
            (["wdu20240000018"], "/acts/WDU20240000018"),
            (["MP", "2023", "1"], "/acts/MP/2023/1"),
            (["M.P. 2023 poz. 1"], "/acts/MP/2023/1"),
        ]
        for sig, want in cases:
            self.assertEqual(eli.act_path(sig)[0], want, f"sygnatura: {sig}")

    def test_invalid_signature_exits(self):
        with self.assertRaises(SystemExit):
            eli.act_path(["zupełnie błędna sygnatura"])


class TestHtmlToText(unittest.TestCase):
    def test_strips_script_and_style(self):
        t = eli.html_to_text("<p>A</p><script>var x=1;</script><style>.c{}</style><p>B</p>")
        self.assertIn("A", t)
        self.assertIn("B", t)
        self.assertNotIn("var x", t)
        self.assertNotIn(".c{", t)

    def test_normalizes_nbsp(self):
        # API ELI używa NBSP: "Art.\xa0299." musi być wyszukiwalne jako "Art. 299."
        self.assertEqual(eli.html_to_text("<p>Art.\xa0299.</p>"), "Art. 299.")

    def test_collapses_blank_lines(self):
        t = eli.html_to_text("<div><p>A</p><p></p><p></p><p>B</p></div>")
        self.assertNotIn("\n\n\n", t)

    def test_superscript_gets_space(self):
        # Indeks górny w <sup> musi zostać rozdzielony spacją, żeby art. 21¹
        # ("Art. 21 1.") był odróżnialny od art. 211 ("Art. 211.").
        self.assertEqual(eli.html_to_text("<p>Art.\xa021<sup>1</sup>.</p>"), "Art. 21 1.")
        self.assertEqual(eli.html_to_text("<p>Art.\xa0211.</p>"), "Art. 211.")

    # treść przypisu (dymek) API wstawia inline w środek przepisu
    HTML_PRZYPIS = ('<h3><b>Art.\xa066c<a class="gloss-link tooltip" href="#gloss-0:6:"><sup>6)</sup>'
                    '<span class="tooltip-text"><span class="pro-gloss-inner">Dodany przez art. 3 pkt 2'
                    ' ustawy z dnia 9 marca 2023 r.</span></span></a>.</b></h3>'
                    '<p>Kto uporczywie nie stosuje się do obowiązków.</p>')

    def test_przypis_w_osobnej_linii_z_etykieta(self):
        t = eli.html_to_text(self.HTML_PRZYPIS)
        # komentarz redakcyjny ≠ norma: własna linia z etykietą i numerem odsyłacza,
        # a numer odsyłacza („6)") NIE wchodzi w nagłówek artykułu — ten kończy się kropką
        self.assertIn("Art. 66c.\n[przypis 6)] Dodany przez", t)
        self.assertTrue(t.startswith("Art. 66c."))
        self.assertNotIn("66c 6)", t)
        self.assertEqual(eli._hity_naglowka(t, "art. 66c"), [0])

    def test_przypis_nie_udaje_naglowka_jednostki(self):
        # Przypis potrafi zaczynać się od „Art. 598…"/„Tytuł działu…" (7 razy w k.p.c.);
        # na początku linii udawałby granicę jednostki i tnąłby fragment w losowym miejscu.
        html = self.HTML_PRZYPIS.replace("Dodany przez art. 3 pkt 2", "Art. 598 16 w związku z art.")
        t = eli.html_to_text(html)
        self.assertNotIn("\nArt. 598", t)
        self.assertIn("[przypis 6)] Art. 598", t)

    # D07 (audyt 2026-08): cyfra odsyłacza do przypisu wchodziła w numer jednostki —
    # „2 1)" (pkt 2 czytało się jak pkt 21), „a 2)", „§ 1 12)" (bez kropki), „(uchylony) 5)"
    HTML_PKT = ('<h3 CLASS="pro-align-padding-right">2<A class="gloss-link tooltip" href="#gloss-0:1:">'
                '<sup>1)</sup><span class="tooltip-text"><span class="pro-gloss-inner">Ze zmianą wprowadzoną'
                ' przez § 1 pkt 1 lit. a rozporządzenia.</span></span></A>)</h3>'
                '<div class="unit-inner"><div CLASS="pro-text">„pomieszczeniach” - rozumie się szatnie;</div></div>'
                '<h3 CLASS="pro-align-padding-right">a<A class="gloss-link tooltip" href="#gloss-0:2:"><sup>2)</sup>'
                '<span class="tooltip-text"><span class="pro-gloss-inner">W brzmieniu ustalonym.</span></span></A>)</h3>'
                '<div CLASS="pro-text">łączny czas przebywania.</div>'
                '<h3 CLASS="pro-padding-right"><B CLASS="b">§&nbsp;1<A class="gloss-link tooltip" href="#gloss-0:12:">'
                '<sup>12)</sup><span class="tooltip-text"><span class="pro-gloss-inner">Uznany za niezgodny z Konstytucją'
                ' (Dz. U. poz. 739).</span></span></A>.</B></h3>'
                '<div CLASS="pro-text">Jeżeli egzekucja przeciwko spółce okaże się bezskuteczna.</div>'
                '<div CLASS="pro-text">§&nbsp;2. (uchylony)<A class="gloss-link tooltip" href="#gloss-0:5:"><sup>5)</sup>'
                '<span class="tooltip-text"><span class="pro-gloss-inner">Przez art. 1 ustawy.</span></span></A></div>')

    def test_numer_odsylacza_nie_wchodzi_w_numer_jednostki(self):
        t = eli.html_to_text(self.HTML_PKT)
        self.assertIn("2)\n[przypis 1)] Ze zmianą wprowadzoną przez § 1 pkt 1 lit. a rozporządzenia.\n", t)
        self.assertNotIn("2 1)", t)
        self.assertIn("a)\n[przypis 2)] W brzmieniu ustalonym.", t)
        self.assertNotIn("a 2)", t)
        self.assertIn("§ 1.\n[przypis 12)] Uznany za niezgodny", t)
        self.assertNotIn("§ 1 12)", t)
        self.assertIn("§ 2. (uchylony)\n[przypis 5)] Przez art. 1 ustawy.", t)
        self.assertNotIn("..", t)          # kropka artykułu nie dubluje się z kropką przypisu
        self.assertIn("\nJeżeli egzekucja", t)  # treść normy nienaruszona

    def test_indeks_gorny_artykulu_nadal_odspacjowany(self):
        # reguła „spacja przed <sup>" dla indeksu górnego artykułu zostaje (art. 449¹ ≠ art. 4491)
        t = eli.html_to_text('<h3>Art.\xa0449<sup>1</sup>.</h3><p>Treść.</p><h3>Art.\xa04491.</h3>')
        self.assertIn("Art. 449 1.", t)
        self.assertIn("Art. 4491.", t)
        self.assertEqual(len(eli._hity_naglowka(t, "art. 449(1)")), 1)
        self.assertEqual(len(eli._hity_naglowka(t, "art. 4491")), 1)

    def test_przypis_w_srodku_akapitu_wychodzi_za_akapit(self):
        # odsyłacz w środku zdania: przypis czeka do końca bloku, norma zostaje w jednym kawałku
        html = ('<div CLASS="pro-text">Strony wyrażą na to zgodę.<A class="gloss-link tooltip" href="#g">'
                '<sup>20)</sup><span class="tooltip-text">Zdanie trzecie dodane przez art. 1.</span></A>'
                ' Zdanie trzecie.</div><div>Art. 2.</div>')
        t = eli.html_to_text(html)
        self.assertIn("Strony wyrażą na to zgodę. Zdanie trzecie.\n[przypis 20)] Zdanie trzecie dodane przez art. 1.\nArt. 2.", t)


class TestFragmenty(unittest.TestCase):
    # Tekst po konwersji HTML→tekst: art. 21¹ (indeks górny w <sup>) renderuje się
    # jako "Art. 21 1." (patrz _Stripper), a art. 211 jako "Art. 211." — odróżnialne.
    TXT = ("Tytuł I\n\nArt. 1.\nPierwszy przepis.\n\n"
           "Art. 2.\n§ 1. Drugi przepis o spółce.\n§ 2. Odesłanie, o którym mowa w art. 1.\n\n"
           "Art. 21.\nDwudziesty pierwszy przepis.\n\n"
           "Art. 21 1.\nPrzepis z indeksem górnym (art. 21 ze zn. 1).\n\n"
           "Art. 211.\nDwieście jedenasty przepis.\n")

    def _frag(self, fraza):
        spans = eli._fragmenty(self.TXT, fraza)
        return [self.TXT[s:e] for s, e in spans]

    def test_artykul_po_naglowku_nie_po_odeslaniu(self):
        frags = self._frag("art. 2")
        self.assertEqual(len(frags), 1)
        self.assertIn("Drugi przepis", frags[0])
        self.assertNotIn("Pierwszy", frags[0])
        self.assertNotIn("Dwudziesty", frags[0])  # "Art. 21." to inny artykuł

    def test_artykul_nie_lapie_dluzszego_numeru(self):
        frags = self._frag("art. 21")
        self.assertEqual(len(frags), 1)
        self.assertIn("Dwudziesty pierwszy", frags[0])
        self.assertNotIn("indeksem górnym", frags[0])  # "Art. 21 1." to inny artykuł
        self.assertNotIn("Dwieście", frags[0])          # "Art. 211." to inny artykuł

    def test_artykul_z_indeksem_gornym(self):
        # "art. 21(1)" oraz "art. 21¹" trafiają w "Art. 21 1." (indeks górny),
        # a NIE w "Art. 21." ani "Art. 211.".
        for fraza in ("art. 21(1)", "art. 21¹"):
            frags = self._frag(fraza)
            self.assertEqual(len(frags), 1, fraza)
            self.assertIn("indeksem górnym", frags[0], fraza)
            self.assertNotIn("Dwudziesty pierwszy", frags[0], fraza)
            self.assertNotIn("Dwieście", frags[0], fraza)

    def test_rozroznia_indeks_gorny_od_pelnego_numeru(self):
        # "art. 211" trafia TYLKO w art. 211, nie w art. 21¹ (to był bug).
        frags = self._frag("art. 211")
        self.assertEqual(len(frags), 1)
        self.assertIn("Dwieście jedenasty", frags[0])
        self.assertNotIn("indeksem górnym", frags[0])

    def test_fraza_pelnotekstowa_docieta_do_artykulu(self):
        frags = self._frag("o którym mowa")
        self.assertEqual(len(frags), 1)
        self.assertTrue(frags[0].startswith("Art. 2."))

    def test_brak_trafien(self):
        self.assertEqual(eli._fragmenty(self.TXT, "nie ma takiej frazy"), [])

    def test_pusta_fraza(self):
        self.assertEqual(eli._fragmenty(self.TXT, "   "), [])


class TestFragmentyZPrzypisem(unittest.TestCase):
    """Nagłówek z ODSYŁACZEM DO PRZYPISU (art. 66c Kodeksu wykroczeń — zgłoszenie z 2026-07-26).

    W tekście jednolitym każdy niedawno dodany lub zmieniony przepis ma przy numerze odsyłacz
    („Art. 66c 6)Dodany przez…"), a kropka artykułu stoi dopiero za treścią przypisu. Wymaganie
    kropki tuż po numerze dawało fałszywy negatyw tam, gdzie prawo jest najświeższe.
    """

    TXT = ("Art. 66b 4)W brzmieniu ustalonym przez art. 2 ustawy z dnia 13 stycznia 2023 r..\n"
           "Kto zawiadamia o niebezpieczeństwie.\n\n"
           "Art. 66c 6)Dodany przez art. 3 pkt 2 ustawy z dnia 9 marca 2023 r..\n"
           "Kto uporczywie nie stosuje się do obowiązków, podlega karze ograniczenia wolności.\n\n"
           "Art. 66.\nKto ze złośliwości wywołuje niepotrzebną czynność.\n\n"
           "Art. 107 10)W tym brzmieniu obowiązuje do dnia wejścia w życie zmiany..\n"
           "Kto w celu dokuczenia innej osobie złośliwie wprowadza ją w błąd.\n\n"
           "Art. 446 1 7)Zdanie drugie utraciło moc..\nPrzepis z indeksem górnym i przypisem.\n\n"
           "Art. 446 1 a.\nPrzepis z indeksem górnym i literą (art. 446 ze zn. 1a).\n\n"
           "Art. 669 101)W brzmieniu ustalonym przez art. 1..\nTreść z przypisem 101.\n")

    def _frag(self, fraza):
        return [self.TXT[s:e] for s, e in eli._fragmenty(self.TXT, fraza)]

    def test_sufiks_literowy_z_przypisem(self):
        # To był zgłoszony bug: --fragment "art. 66c" zwracało „nie znaleziono".
        frags = self._frag("art. 66c")
        self.assertEqual(len(frags), 1)
        self.assertIn("uporczywie", frags[0])
        self.assertNotIn("zawiadamia", frags[0])      # art. 66b to inny artykuł
        self.assertNotIn("złośliwości", frags[0])     # art. 66 to inny artykuł

    def test_goly_numer_z_przypisem(self):
        frags = self._frag("art. 107")
        self.assertEqual(len(frags), 1)
        self.assertIn("dokuczenia", frags[0])

    def test_indeks_gorny_z_przypisem(self):
        frags = self._frag("art. 446(1)")
        self.assertEqual(len(frags), 1)
        self.assertIn("indeksem górnym i przypisem", frags[0])
        self.assertNotIn("i literą", frags[0])        # art. 446 ze zn. 1a to inny artykuł

    def test_indeks_gorny_z_litera_rozdzielone_spacja(self):
        # "Art. 446 1 a." — indeks górny i litera bywają w tekście rozdzielone
        frags = self._frag("art. 446(1a)")
        self.assertEqual(len(frags), 1)
        self.assertIn("i literą", frags[0])

    def test_przypis_nie_udaje_indeksu_gornego(self):
        # "art. 669¹" NIE może trafić w "Art. 669 101)" (art. 669 z przypisem 101)
        self.assertEqual(eli._hity_naglowka(self.TXT, "art. 669(1)"), [])
        frags = self._frag("art. 669")
        self.assertEqual(len(frags), 1)
        self.assertIn("przypisem 101", frags[0])

    def test_goly_numer_nie_lapie_sufiksu_literowego(self):
        frags = self._frag("art. 66")
        self.assertEqual(len(frags), 1)
        self.assertIn("złośliwości", frags[0])

    def test_brak_naglowka_wraca_do_szukania_pelnotekstowego(self):
        # Fałszywy negatyw jest gorszy niż odesłanie: bez nagłówka pokazujemy trafienia w treści.
        self.assertEqual(eli._hity_naglowka(self.TXT, "art. 2"), [])
        frags = self._frag("art. 2")
        self.assertEqual(len(frags), 1)
        self.assertIn("art. 2 ustawy z dnia 13 stycznia 2023", frags[0])

    def test_wielka_litera_w_zapytaniu(self):
        # ze zgłoszenia: „Art. 66c" i „art. 66c" muszą dawać ten sam wynik
        self.assertEqual(eli._fragmenty(self.TXT, "Art. 66c"), eli._fragmenty(self.TXT, "art. 66c"))

    def test_warianty_myslnika(self):
        # myślnik w akcie (U+2012) vs. zwykły "-" w zapytaniu — nie może to być fałszywy negatyw
        txt = "Art. 5.\nProgram korekcyjno‒edukacyjny dla sprawców.\n"
        self.assertEqual(len(eli._fragmenty(txt, "korekcyjno-edukacyjny")), 1)

    def test_inwariant_spojnosci(self):
        """§3.2 zgłoszenia: fraza widoczna w `tekst <akt>` NIGDY nie może dać „nie znaleziono"."""
        for i in range(0, len(self.TXT) - 12):
            fraza = self.TXT[i:i + 12 + (i % 25)].strip()
            if len(fraza) >= 8:
                self.assertTrue(eli._fragmenty(self.TXT, fraza), f"brak trafienia dla {fraza!r}")


class TestTjZTekstem(unittest.TestCase):
    """Fallback, gdy API zwraca 200 i 0 bajtów dla text.html świeżego tekstu jednolitego."""

    REFS_TJ = {"Tekst jednolity dla aktu": [
        {"act": {"ELI": "DU/1964/296", "displayAddress": "Dz.U. 1964 nr 43 poz. 296"}}]}
    BASE_REFS = {"Inf. o tekście jednolitym": [
        {"act": {"ELI": "DU/2026/468", "displayAddress": "Dz.U. 2026 poz. 468"}},
        {"act": {"ELI": "DU/2024/1568", "displayAddress": "Dz.U. 2024 poz. 1568"}},
    ]}

    def _z_fake_get(self, odpowiedzi, path, refs):
        def fake_get(p, params=None, soft=False):
            return odpowiedzi.get(p)
        orig = eli._get
        eli._get = fake_get
        try:
            return eli._tj_z_tekstem(path, refs)
        finally:
            eli._get = orig

    def test_akt_jest_tj_bez_html_bierze_poprzedni_tj(self):
        wynik = self._z_fake_get({
            "/acts/DU/1964/296/references": self.BASE_REFS,
            "/acts/DU/2024/1568/text.html": "<p>Art. 1. Treść.</p>",
        }, "/acts/DU/2026/468", self.REFS_TJ)
        self.assertIsNotNone(wynik)
        act, txt, pominiete = wynik
        self.assertEqual(act["ELI"], "DU/2024/1568")
        self.assertIn("Art. 1.", txt)
        self.assertEqual(pominiete, [])

    def test_pomija_tj_z_pustym_html(self):
        # najnowszy kandydat też bez HTML — idzie dalej po liście
        refs_bazowe = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2026/468"}},
            {"act": {"ELI": "DU/2024/1568"}},
            {"act": {"ELI": "DU/2023/1550"}},
        ]}
        wynik = self._z_fake_get({
            "/acts/DU/2026/468/text.html": "",
            "/acts/DU/2024/1568/text.html": "",
            "/acts/DU/2023/1550/text.html": "<p>Art. 1.</p>",
        }, "/acts/DU/1964/296", refs_bazowe)
        self.assertEqual(wynik[0]["ELI"], "DU/2023/1550")
        # pominięte t.j. z pustym HTML są zwracane — nagłówek ma je wymienić (k.c.: 2025/1071)
        self.assertEqual(wynik[2], ["DU/2026/468", "DU/2024/1568"])

    def test_brak_kandydatow_zwraca_none(self):
        self.assertIsNone(self._z_fake_get({}, "/acts/DU/2026/468", {}))

    def test_nie_zwraca_aktu_biezacego(self):
        # jedyny t.j. na liście to akt bieżący — fallback nie może zwrócić jego samego
        wynik = self._z_fake_get({
            "/acts/DU/1964/296/references": {"Inf. o tekście jednolitym": [
                {"act": {"ELI": "DU/2026/468"}}]},
        }, "/acts/DU/2026/468", self.REFS_TJ)
        self.assertIsNone(wynik)


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj", "fraza"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, eli.cmd_szukaj
        eli.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            eli.main()
        finally:
            sys.argv, eli.cmd_szukaj = oryg_argv, oryg_cmd
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


class _Response:
    def __init__(self, body, content_type="application/json"):
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class EliVerificationContractTests(unittest.TestCase):
    """found/verified_absent/unknown - blad transportu nie moze wygladac jak potwierdzony brak."""

    def test_malformed_references_block_strict_and_warn_otherwise(self):
        for refs in (None, [], "<html>Service unavailable</html>",
                     {"error": "Service unavailable"}, {"relacja": [None]}):
            for strict in (False, True):
                with self.subTest(refs=refs, strict=strict):
                    args = argparse.Namespace(sygnatura=["DU", "2024", "18"],
                                              strict=strict, pdf=None, fragment=None)
                    out = io.StringIO()
                    with mock.patch.object(eli, "_get", side_effect=[refs, "<p>Treść testowa</p>", {}]), \
                            contextlib.redirect_stdout(out):
                        if strict:
                            with self.assertRaises(eli.VerificationUnknown):
                                eli.cmd_tekst(args)
                            self.assertEqual(out.getvalue(), "")
                        else:
                            eli.cmd_tekst(args)
                            self.assertIn("nie udało się zweryfikować aktualności", out.getvalue())
                            self.assertIn("Treść testowa", out.getvalue())

    def test_json_error_is_not_act_text(self):
        for body in ({"error": "Service unavailable"}, [], None):
            for strict in (False, True):
                with self.subTest(body=body, strict=strict):
                    args = argparse.Namespace(sygnatura=["DU", "2024", "18"],
                                              strict=strict, pdf=None, fragment=None)
                    out = io.StringIO()
                    with mock.patch.object(eli, "_get", side_effect=[{}, body]), \
                            contextlib.redirect_stdout(out):
                        with self.assertRaisesRegex(SystemExit, "zamiast tekstu HTML"):
                            eli.cmd_tekst(args)
                    self.assertEqual(out.getvalue(), "")

    def test_soft_transport_error_is_unknown(self):
        with mock.patch.object(eli._opener, "open", side_effect=urllib.error.URLError("offline")), \
                mock.patch.object(eli.time, "sleep"):
            with self.assertRaises(eli.VerificationUnknown):
                eli._get("/acts/test/references", soft=True)

    def test_successful_empty_json_is_verified_absent(self):
        response = _Response(b"[]")
        with mock.patch.object(eli._opener, "open", return_value=response):
            self.assertEqual(eli._get("/acts/search", soft=True), [])

    def test_soft_404_is_verified_absent(self):
        # API ELI odpowiada 404 wyłącznie dla nieistniejącego zasobu — to zweryfikowany
        # brak (None), nie awaria; "spróbuj ponownie" byłoby tu fałszywym komunikatem
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(eli._opener, "open", side_effect=err), \
                mock.patch.object(eli.time, "sleep"):
            self.assertIsNone(eli._get("/acts/DU/2024/999999/references", soft=True))

    def test_tekst_dostarczony_mimo_awarii_odniesien(self):
        # awaria POBOCZNEGO /references nie odbiera tekstu — tekst + GŁOŚNE ostrzeżenie
        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                raise eli.VerificationUnknown("timeout")
            return "<html><body><p>Art. 1. Treść przepisu.</p></body></html>"
        args = argparse.Namespace(sygnatura=["DU", "2024", "18"], json=False,
                                  pdf=None, fragment=None)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                contextlib.redirect_stdout(out):
            eli.cmd_tekst(args)
        self.assertIn("Art. 1. Treść przepisu.", out.getvalue())
        self.assertIn("nie udało się zweryfikować aktualności", out.getvalue())

    def test_strict_blokuje_tekst_przy_awarii_odniesien(self):
        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                raise eli.VerificationUnknown("timeout")
            return "<html><body><p>Art. 1. Treść przepisu.</p></body></html>"
        args = argparse.Namespace(sygnatura=["DU", "2024", "18"], json=False,
                                  strict=True, pdf=None, fragment=None)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(eli.VerificationUnknown, "timeout"):
                eli.cmd_tekst(args)
        self.assertNotIn("Treść przepisu", out.getvalue())

    def test_strict_nie_wypisuje_tj_przed_kontrola_aktualnosci(self):
        refs = {"Tekst jednolity dla aktu": [
            {"act": {"ELI": "DU/1964/296", "displayAddress": "Dz.U. 1964 poz. 296"}}]}

        def fake_get(path, params=None, soft=False):
            if path == "/acts/DU/2024/1568/references":
                return refs
            raise eli.VerificationUnknown("timeout")

        args = argparse.Namespace(sygnatura=["DU", "2024", "1568"], json=False, strict=True)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(eli.VerificationUnknown, "timeout"):
                eli.cmd_tj(args)
        self.assertEqual(out.getvalue(), "")

    def test_fallback_pomija_kandydata_z_awaria(self):
        # awaria transportu na JEDNYM kandydacie t.j. nie zabija pętli zapasowej
        refs = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2023/100", "displayAddress": "Dz.U. 2023 poz. 100"}},
            {"act": {"ELI": "DU/2020/50", "displayAddress": "Dz.U. 2020 poz. 50"}}]}
        def fake_get(path, params=None, soft=False):
            if "2023/100" in path:
                raise eli.VerificationUnknown("zapora odrzuciła żądanie")
            return "<html><body><p>Art. 1. Tekst z 2020 r.</p></body></html>"
        with mock.patch.object(eli, "_get", side_effect=fake_get):
            act, txt, _ = eli._tj_z_tekstem("/acts/DU/2024/18", refs)
        self.assertEqual(act["ELI"], "DU/2020/50")
        self.assertIn("Tekst z 2020", txt)

    def test_fallback_unknown_gdy_zaden_kandydat_nie_dal_tekstu(self):
        # gdy część kandydatów padła, a żaden nie dał tekstu — UNKNOWN, nie "braku t.j."
        refs = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2023/100"}}, {"act": {"ELI": "DU/2020/50"}}]}
        def fake_get(path, params=None, soft=False):
            if "2023/100" in path:
                raise eli.VerificationUnknown("timeout")
            return ""
        with mock.patch.object(eli, "_get", side_effect=fake_get):
            with self.assertRaises(eli.VerificationUnknown):
                eli._tj_z_tekstem("/acts/DU/2024/18", refs)

    def test_domyslnie_starszy_tj_ma_maszynowy_znacznik(self):
        refs = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2024/1568", "displayAddress": "Dz.U. 2024 poz. 1568"}}]}

        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                return refs
            if path == "/acts/DU/2026/468/text.html":
                return ""
            if path == "/acts/DU/2024/1568/text.html":
                return "<p>Art. 1. Starsza treść.</p>"
            if path in ("/acts/DU/2026/468", "/acts/DU/2024/1568"):
                return {"texts": [], "legalStatusDate": "2024-10-01"}
            raise AssertionError(path)

        args = argparse.Namespace(sygnatura=["DU", "2026", "468"], json=False,
                                  strict=False, pdf=None, fragment=None)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=False), \
                contextlib.redirect_stdout(out):
            eli.cmd_tekst(args)
        self.assertIn("ELI_TEXT_SOURCE_FALLBACK=DU/2024/1568", out.getvalue())
        self.assertIn("NIEAKTUALNE BRZMIENIE MOŻLIWE", out.getvalue())
        self.assertIn("Art. 1. Starsza treść.", out.getvalue())

    def test_strict_blokuje_starszy_tj(self):
        refs = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2024/1568", "displayAddress": "Dz.U. 2024 poz. 1568"}}]}

        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                return refs
            if path == "/acts/DU/2026/468/text.html":
                return ""
            if path == "/acts/DU/2024/1568/text.html":
                return "<p>Art. 1. Starsza treść.</p>"
            if path == "/acts/DU/2026/468":
                return {"texts": [{"type": "T", "fileName": "D20260468Lj.pdf"}]}
            raise AssertionError(path)

        args = argparse.Namespace(sygnatura=["DU", "2026", "468"], json=False,
                                  strict=True, pdf=None, fragment=None)
        out = io.StringIO()
        # brak pdftotext → kompletności zastępczego tekstu nie da się zweryfikować → strict blokuje
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=False), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(SystemExit, "brak programu pdftotext.*strict blokuje zastępczy"):
                eli.cmd_tekst(args)
        self.assertEqual(out.getvalue(), "")

    def test_t01_strict_blokuje_poprawnie_wykryty_nowszy_tj_i_stdout_jest_pusty(self):
        refs = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2026/500", "displayAddress": "Dz.U. 2026 poz. 500"}}]}

        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                return refs
            return "<p>Art. 1. Starsza, ale niepusta treść.</p>"

        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2024", "18", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eli.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")

    def test_t02_json_strict_nie_emituje_danych_gdy_kontrola_nie_przeszla(self):
        komendy = [
            ["szukaj", "kodeks"],
            ["meta", "DU", "2024", "18"],
            ["tekst", "DU", "2024", "18"],
            ["struktura", "DU", "2024", "18"],
            ["odniesienia", "DU", "2024", "18"],
            ["tj", "DU", "2024", "18"],
        ]
        for komenda in komendy:
            with self.subTest(komenda=komenda):
                out = io.StringIO()
                with mock.patch.object(eli, "_get", side_effect=eli.VerificationUnknown("timeout")), \
                        mock.patch.object(sys, "argv", ["eli.py", *komenda, "--json", "--strict"]), \
                        contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        eli.main()
                self.assertNotEqual(caught.exception.code, 0)
                self.assertEqual(out.getvalue(), "")

    def test_t02b_tj_json_strict_kontroluje_nowszy_tj_przed_emisja(self):
        refs_tj = {"Tekst jednolity dla aktu": [
            {"act": {"ELI": "DU/1964/296", "displayAddress": "Dz.U. 1964 poz. 296"}}]}
        refs_bazowe = {"Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2026/500", "displayAddress": "Dz.U. 2026 poz. 500"}},
            {"act": {"ELI": "DU/2024/1568", "displayAddress": "Dz.U. 2024 poz. 1568"}},
        ]}

        def fake_get(path, params=None, soft=False):
            if path == "/acts/DU/2024/1568/references":
                return refs_tj
            if path == "/acts/DU/1964/296/references":
                return refs_bazowe
            raise AssertionError(path)

        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(sys, "argv", ["eli.py", "tj", "DU", "2024", "1568",
                                                       "--json", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eli.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")


class TestT03PrzekierowaniaHttps(unittest.TestCase):
    def test_host_tresci_jest_podnoszony_a_obcy_host_odrzucany(self):
        req = eli.urllib.request.Request("https://api.sejm.gov.pl/eli/acts/DU/2024/18")
        handler = eli._PrzekierowaniaHttps()
        nowy = handler.redirect_request(
            req, None, 302, "Found", {}, "http://api.sejm.gov.pl/eli/acts/DU/2024/18/text.pdf")
        self.assertEqual(nowy.full_url,
                         "https://api.sejm.gov.pl/eli/acts/DU/2024/18/text.pdf")
        with self.assertRaisesRegex(urllib.error.URLError, "niezaufany host"):
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.test/akt.pdf")


class TestT04RecznaInstalacjaSkilli(unittest.TestCase):
    WYDANE = ("prawo-pl-eli", "prawo-pl-edzienniki", "prawo-eu-eurlex", "prawo-pl-saos",
              "prawo-pl-cbosa", "prawo-pl-uodo", "prawo-pl-rejestr-umow")

    def test_wszystkie_skille_maja_lokalny_fallback_bez_sieci_i_find(self):
        # helper wyłącznie z bieżącego pakietu: ścieżka podstawiana przez Claude Code
        # (${CLAUDE_PLUGIN_ROOT}) albo katalog tego SKILL.md — bez find po dysku i bez curl z main.
        # Trzeci krok (piaskownica, 2.0.1) pobiera DOKŁADNIE tę wersję z tagu i odmawia uruchomienia,
        # gdy suma SHA-256 nie zgadza się z zapisaną w SKILL.md — dlatego suma w SKILL.md musi być
        # sumą pliku silnika z tego samego pakietu, a tag = wersja z frontmattera.
        import hashlib
        for plugin in self.WYDANE:
            skill = ROOT / f"plugins/{plugin}/skills/{plugin}/SKILL.md"
            text = skill.read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertIn(f'="${{CLAUDE_PLUGIN_ROOT}}/skills/{plugin}/scripts/', text)
                self.assertIn('="<katalog skilla>/scripts/', text)
                self.assertIn("nie pobieraj go z żadnego innego źródła niż przypięty tag", text)
                self.assertIn("Nie szukaj helpera przez `find`", text)
                self.assertNotIn("curl -fsSL", text)
                self.assertNotIn('find "$HOME', text)
                self.assertNotIn("/prawo-pl-eli/main/", text)
                self.assertIn("`--strict`", text)
                wersja = re.search(r"^version:\s*(\S+)\s*$", text, re.M).group(1)
                m = re.search(r'WERSJA, SHA256 = "([^"]+)", "([0-9a-f]{64})"', text)
                self.assertIsNotNone(m, "brak przypiętego kroku 3) Piaskownica")
                self.assertEqual(m.group(1), wersja, "tag pobierania ≠ wersja skilla")
                silnik = re.search(r'/v\{WERSJA\}/plugins/' + re.escape(plugin)
                                   + r'/skills/' + re.escape(plugin) + r'/scripts/([a-z]+\.py)"', text)
                self.assertIsNotNone(silnik, "URL pobierania nie wskazuje tagu v{WERSJA} tego skilla")
                plik = ROOT / f"plugins/{plugin}/skills/{plugin}/scripts/{silnik.group(1)}"
                self.assertEqual(m.group(2), hashlib.sha256(plik.read_bytes()).hexdigest(),
                                 "suma w SKILL.md ≠ suma silnika w pakiecie")


class TestT11NowszyTjNaStarszymTj(unittest.TestCase):
    """Akt, który SAM jest (starszym) t.j., nie ma listy t.j. we własnych odniesieniach —
    aktualność trzeba sprawdzić na akcie bazowym, inaczej przestarzały t.j. wygląda jak aktualny."""
    REFS_TJ = {"Tekst jednolity dla aktu": [
        {"act": {"ELI": "DU/1964/93", "displayAddress": "Dz.U. 1964 nr 16 poz. 93"}}]}
    REFS_BAZOWE = {"Inf. o tekście jednolitym": [
        {"act": {"ELI": "DU/2024/1061", "displayAddress": "Dz.U. 2024 poz. 1061"}},
        {"act": {"ELI": "DU/2026/795", "displayAddress": "Dz.U. 2026 poz. 795"}}]}

    def _fake_get(self, path, params=None, soft=False):
        if path == "/acts/DU/2024/1061/references" or path == "/acts/DU/2026/795/references":
            return self.REFS_TJ
        if path == "/acts/DU/1964/93/references":
            return self.REFS_BAZOWE
        if path in ("/acts/DU/2024/1061", "/acts/DU/2026/795"):
            return {"legalStatusDate": "2026-05-19", "textHTML": True}
        if path.endswith("/text.html"):
            return "<p>Art. 1. Kodeks niniejszy reguluje stosunki cywilnoprawne.</p>"
        raise AssertionError(path)

    def test_strict_blokuje_tekst_starszego_tj_i_stdout_jest_pusty(self):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=self._fake_get), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2024", "1061", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(SystemExit, "nowszy tekst jednolity: Dz.U. 2026 poz. 795"):
                eli.main()
        self.assertEqual(out.getvalue(), "")

    def test_domyslnie_tekst_starszego_tj_ma_ostrzezenie_o_nowszym(self):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=self._fake_get), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2024", "1061"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        self.assertIn("NIEAKTUALNY tekst jednolity", out.getvalue())
        self.assertIn("Dz.U. 2026 poz. 795", out.getvalue())
        self.assertIn("tekst DU 2026 795", out.getvalue())
        self.assertIn("Art. 1.", out.getvalue())

    def test_najnowszy_tj_przechodzi_w_strict_bez_ostrzezenia(self):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=self._fake_get), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2026", "795", "--strict"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        self.assertNotIn("NIEAKTUALNY", out.getvalue())
        self.assertIn("Art. 1.", out.getvalue())

    def test_awaria_kontroli_na_akcie_bazowym_strict_blokuje_a_domyslnie_ostrzega(self):
        def fake(path, params=None, soft=False):
            if path == "/acts/DU/1964/93/references":
                raise eli.VerificationUnknown("timeout")
            return self._fake_get(path, params, soft)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2024", "1061", "--strict"]), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eli.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2024", "1061"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        self.assertIn("nie udało się sprawdzić, czy istnieje nowszy tekst jednolity", out.getvalue())
        self.assertIn("Art. 1.", out.getvalue())


class TestAudyt2026DatyIMeta(unittest.TestCase):
    """D02/D03: „Ogłoszono" musi być datą publikacji w Dz.U. (promulgation), nie datą aktu
    (announcementDate = „z dnia" w tytule); `comments` (rozłożone wejście w życie) nie może zniknąć."""

    META = {"title": "Ustawa z dnia 14 czerwca 2024 r. o ochronie sygnalistów", "displayAddress": "Dz.U. 2024 poz. 928",
            "type": "Ustawa", "status": "obowiązujący", "inForce": "IN_FORCE", "announcementDate": "2024-06-14",
            "promulgation": "2024-06-24", "entryIntoForce": "2024-09-25", "legalStatusDate": None,
            "comments": "art. 5 ust. 4, art. 25 ust. 1 pkt 8 oraz przepisy rozdziału 4 wchodzą w życie z dniem 25 grudnia 2024 r.",
            "ELI": "DU/2024/928", "texts": [], "textHTML": True}

    def _meta(self, meta):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", return_value=meta), contextlib.redirect_stdout(out):
            eli.cmd_meta(argparse.Namespace(sygnatura=["DU", "2024", "928"], json=False))
        return out.getvalue()

    def test_ogloszono_to_promulgation_a_data_aktu_osobno(self):
        out = self._meta(self.META)
        self.assertRegex(out, r"Ogłoszono: 2024-06-24")
        self.assertRegex(out, r"Data aktu: 2024-06-14")
        self.assertNotRegex(out, r"Ogłoszono: 2024-06-14")
        self.assertIn("WEJŚCIE W ŻYCIE: 2024-09-25", out)

    def test_uwagi_z_api_wypisane_doslownie(self):
        self.assertIn("Uwagi:   art. 5 ust. 4, art. 25 ust. 1 pkt 8 oraz przepisy rozdziału 4 wchodzą w życie "
                      "z dniem 25 grudnia 2024 r.", self._meta(self.META))

    def test_stan_prawny_tylko_gdy_jest_i_brak_promulgation(self):
        tj = dict(self.META, legalStatusDate="2026-05-19", promulgation=None, textHTML=False, comments=None)
        out = self._meta(tj)
        self.assertIn("Stan prawny na: 2026-05-19", out)
        self.assertIn("Ogłoszono: — (brak w API)", out)
        self.assertNotIn("Uwagi:", out)
        self.assertIn("BRAK w API (textHTML=false)", out)
        self.assertNotIn("Stan prawny na", self._meta(self.META))


class TestAudyt2026Szukaj(unittest.TestCase):
    """D04: „Znaleziono" = totalCount z API, nie rozmiar strony; zero trafień = exit ≠ 0 także z --json."""

    ODP = {"count": 2, "totalCount": 58, "offset": 0,
           "items": [{"address": "WDU20260001046", "ELI": "DU/2026/1046", "title": "Ustawa A", "status": "obowiązujący"},
                     {"address": "WDU20260000473", "ELI": "DU/2026/473", "title": "Ustawa B", "status": "obowiązujący"}]}

    def _szukaj(self, odp, **kw):
        args = dict(fraza="Kodeks pracy", typ="Ustawa", rok=None, wyd=None, haslo=None, obowiazujace=False,
                    limit=2, offset=0, json=False)
        args.update(kw)
        out = io.StringIO()
        with mock.patch.object(eli, "_get", return_value=odp), contextlib.redirect_stdout(out):
            eli.cmd_szukaj(argparse.Namespace(**args))
        return out.getvalue()

    def test_total_count_i_instrukcja_stronicowania(self):
        out = self._szukaj(self.ODP)
        self.assertIn("Znaleziono: 58 (pokazuję 2, offset 0)", out)
        self.assertIn("pozostało 56", out)
        self.assertIn("--offset 2 --limit 2", out)

    def test_komplet_bez_ostrzezenia(self):
        out = self._szukaj(dict(self.ODP, totalCount=2))
        self.assertIn("Znaleziono: 2 (pokazuję 2, offset 0)", out)
        self.assertNotIn("NIE wszystkie", out)

    def test_zero_trafien_konczy_sie_bledem_takze_z_json(self):
        pusty = {"count": 0, "totalCount": 0, "offset": 0, "items": []}
        for js in (False, True):
            with self.subTest(json=js):
                out = io.StringIO()
                with mock.patch.object(eli, "_get", return_value=pusty), contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        eli.cmd_szukaj(argparse.Namespace(fraza="xyz", typ=None, rok=None, wyd=None, haslo=None,
                                                          obowiazujace=False, limit=10, offset=0, json=js))
                self.assertNotEqual(caught.exception.code, 0)
                self.assertIn("Brak wyników", str(caught.exception.code))
                self.assertEqual(out.getvalue(), "")


class TestAudyt2026Struktura(unittest.TestCase):
    """D08: 404 na /struct to zweryfikowany brak → komunikat „Brak struktury", nie surowy błąd HTTP."""

    def test_404_daje_komunikat_o_braku_struktury(self):
        def fake_get(path, params=None, soft=False):
            self.assertTrue(soft, "struct musi być pobierany soft (404 = brak, nie awaria)")
            return None
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(SystemExit, "Brak struktury dla tego aktu \(DU 2026 poz. 694\)"):
                eli.cmd_struktura(argparse.Namespace(sygnatura=["DU", "2026", "694"], json=False, filtr=None, poziom=None))
        self.assertEqual(out.getvalue(), "")


class TestAudyt2026NowelizacjePoTj(unittest.TestCase):
    """D05: lista „Nowelizacje po tekście jednolitym" uzupełniana o „Akty zmieniające" aktu bazowego
    po legalStatusDate t.j., bez duplikatów, z jednoznacznymi etykietami dat."""

    REFS_TJ = {
        "Tekst jednolity dla aktu": [{"act": {"ELI": "DU/1964/296", "displayAddress": "Dz.U. 1964 nr 43 poz. 296"}}],
        "Nowelizacje po tekście jednolitym": [
            {"act": {"ELI": "DU/2026/830", "displayAddress": "Dz.U. 2026 poz. 830", "title": "Ustawa o ochronie debaty",
                     "announcementDate": "2026-05-29", "promulgation": "2026-06-23"}},
            {"act": {"ELI": "DU/2026/473", "displayAddress": "Dz.U. 2026 poz. 473", "title": "Ustawa o PIP",
                     "announcementDate": "2026-03-11", "promulgation": "2026-04-07"}, "date": "2026-03-11"},
        ]}
    REFS_BAZOWE = {"Akty zmieniające": [
        {"act": {"ELI": "DU/2026/1046", "displayAddress": "Dz.U. 2026 poz. 1046", "title": "Ustawa o zmianie k.p. i k.p.c.",
                 "announcementDate": "2026-06-19", "promulgation": "2026-08-04"}, "date": "2026-11-05"},
        {"act": {"ELI": "DU/2026/1003", "displayAddress": "Dz.U. 2026 poz. 1003", "title": "Ustawa o systemach AI",
                 "announcementDate": "2026-07-03", "promulgation": "2026-07-27"}, "date": "2026-10-28"},
        {"act": {"ELI": "DU/2026/830", "displayAddress": "Dz.U. 2026 poz. 830", "title": "Ustawa o ochronie debaty",
                 "announcementDate": "2026-05-29", "promulgation": "2026-06-23"}, "date": "2026-07-08"},
        {"act": {"ELI": "DU/2026/473", "displayAddress": "Dz.U. 2026 poz. 473", "title": "Ustawa o PIP",
                 "announcementDate": "2026-03-11", "promulgation": "2026-04-07"}, "date": "2026-07-08"},
        {"act": {"ELI": "DU/2025/1500", "displayAddress": "Dz.U. 2025 poz. 1500", "title": "Stara nowelizacja",
                 "announcementDate": "2025-11-01", "promulgation": "2025-11-20"}, "date": "2026-01-01"},
    ]}
    META_TJ = {"ELI": "DU/2026/468", "legalStatusDate": "2026-03-25", "promulgation": "2026-04-07"}

    def _fake_get(self, path, params=None, soft=False):
        if path == "/acts/DU/2026/468":
            return self.META_TJ
        if path == "/acts/DU/1964/296/references":
            return self.REFS_BAZOWE
        raise AssertionError(path)

    def test_uzupelnia_z_aktu_bazowego_i_deduplikuje(self):
        with mock.patch.object(eli, "_get", side_effect=self._fake_get):
            linie, uwagi = eli._nowelizacje_po_tj(self.REFS_TJ, "/acts/DU/2026/468")
        self.assertEqual(uwagi, [])
        self.assertIn("odnotowano zmiany (4)", linie[0])
        self.assertIn("stan prawny na 2026-03-25", linie[0])
        pozycje = [l for l in linie[1:] if l.startswith("  - ")]
        self.assertEqual([l.split()[1:4] for l in pozycje],
                         [["Dz.U.", "2026", "poz."]] * 4)
        self.assertEqual(len(pozycje), 4)
        self.assertIn("Dz.U. 2026 poz. 1046", "\n".join(pozycje))      # z aktu bazowego (brakowało)
        self.assertIn("Dz.U. 2026 poz. 1003", "\n".join(pozycje))
        self.assertNotIn("Dz.U. 2025 poz. 1500", "\n".join(pozycje))   # w całości przed stanem prawnym t.j.
        self.assertEqual("\n".join(pozycje).count("poz. 830"), 1)       # bez duplikatu
        linia_830 = next(l for l in pozycje if "poz. 830" in l)
        self.assertIn("data aktu 2026-05-29", linia_830)
        self.assertIn("ogłoszono 2026-06-23", linia_830)
        self.assertIn("wejście w życie zmiany 2026-07-08", linia_830)
        self.assertNotIn("(data 2026", linia_830)                       # stara, dwuznaczna etykieta

    def test_awaria_aktu_bazowego_strict_blokuje_a_domyslnie_ostrzega(self):
        def fake(path, params=None, soft=False):
            if path == "/acts/DU/1964/296/references":
                raise eli.VerificationUnknown("timeout")
            if path in ("/acts/DU/2026/830", "/acts/DU/2026/473"):
                return {"entryIntoForce": None}       # dopytanie o wejście w życie bez daty
            return self._fake_get(path, params, soft)
        with mock.patch.object(eli, "_get", side_effect=fake):
            with self.assertRaises(eli.VerificationUnknown):
                eli._nowelizacje_po_tj(self.REFS_TJ, "/acts/DU/2026/468", strict=True)
            linie, uwagi = eli._nowelizacje_po_tj(self.REFS_TJ, "/acts/DU/2026/468", strict=False)
        self.assertTrue(uwagi and "NIEPEŁNA" in uwagi[0])
        self.assertIn("odnotowano zmiany (2)", linie[0])
        self.assertIn("wejście w życie aktu — sprawdź: meta DU 2026 830", linie[1])

    def test_dopytanie_o_wejscie_w_zycie_jest_ograniczone(self):
        # pozycje tylko z listy t.j. bez daty wejścia w życie → meta aktu, ale nie więcej niż _MAKS_DOPYTAN
        wlasne = [{"act": {"ELI": f"DU/2026/{n}", "displayAddress": f"Dz.U. 2026 poz. {n}", "title": "x",
                           "announcementDate": "2026-01-01", "promulgation": "2026-01-02"}} for n in range(1, 15)]
        refs = {"Tekst jednolity dla aktu": self.REFS_TJ["Tekst jednolity dla aktu"],
                "Nowelizacje po tekście jednolitym": wlasne}
        zapytania = []
        def fake(path, params=None, soft=False):
            zapytania.append(path)
            if path == "/acts/DU/2026/468":
                return self.META_TJ
            if path == "/acts/DU/1964/296/references":
                return {"Akty zmieniające": []}
            return {"entryIntoForce": "2026-09-01", "comments": "art. 5 wchodzi w życie z dniem 1 stycznia 2027 r."}
        with mock.patch.object(eli, "_get", side_effect=fake):
            linie, uwagi = eli._nowelizacje_po_tj(refs, "/acts/DU/2026/468")
        self.assertEqual(sum(1 for p in zapytania if p.startswith("/acts/DU/2026/") and p != "/acts/DU/2026/468"),
                         eli._MAKS_DOPYTAN)
        self.assertIn("wejście w życie aktu 2026-09-01", linie[1])
        self.assertIn("uwagi: art. 5 wchodzi w życie", linie[2])
        self.assertTrue(any("sprawdź: meta DU 2026" in l for l in linie))

    def test_fmt_ref_etykiety_dat_zalezne_od_kategorii(self):
        ref = {"act": {"ELI": "DU/2026/830", "displayAddress": "Dz.U. 2026 poz. 830", "title": "T",
                       "announcementDate": "2026-05-29", "promulgation": "2026-06-23"}, "date": "2026-07-08"}
        self.assertIn("wejście w życie zmiany 2026-07-08", eli._fmt_ref(ref, "Akty zmieniające"))
        ref2 = dict(ref, date="2026-05-29")
        linia = eli._fmt_ref(ref2, "Nowelizacje po tekście jednolitym")
        self.assertIn("data aktu 2026-05-29", linia)
        self.assertEqual(linia.count("2026-05-29"), 1)
        self.assertIn("data wg API 2026-07-08", eli._fmt_ref(ref, "Orzeczenie TK"))
        self.assertNotIn("(data 2026", eli._fmt_ref(ref, "Akty zmieniające"))


PDF_LAYOUT = (
    "     ©Kancelaria Sejmu                                                        s. 1/3\n"
    "\n"
    "                                        Dz. U. 2026 poz. 795\n"
    "\n"
    "                                   O B W IE S Z CZ E N I E\n"
    "                                      z dnia 27 maja 2026 r.\n"
    "     w sprawie ogłoszenia jednolitego tekstu ustawy – Kodeks cywilny\n"
    "     1. Na podstawie art. 16 ust. 1 ogłasza się jednolity tekst ustawy, z uwzględnieniem stanu prawnego\n"
    "na dzień 19 maja 2026 r. Tekst nie obejmuje art. 3 ustawy, który stanowi:\n"
    "     „Art. 3. Ustawa wchodzi w życie po upływie miesiąca od dnia ogłoszenia.”\n"
    "                                                                                     2026-06-22\n"
    "\f"
    "Dziennik Ustaw                               – 2 –                                   Poz. 795\n"
    "\n"
    "                                 Załącznik do obwieszczenia Marszałka Sejmu\n"
    "\n"
    "                                             US T AW A\n"
    "                                      z dnia 23 kwietnia 1964 r.\n"
    "                                          Kodeks cywilny\n"
    "      Art. 3. Ustawa nie ma mocy wstecznej, chyba że to wynika z jej brzmienia lub celu.\n"
    "      Art. 68[1]. § 1. W stosunkach między przedsiębiorcami odpowiedź na ofertę z zastrzeżeniem nie-\n"
    "zmieniających istotnie treści oferty poczytuje się za jej przyjęcie.\n"
    "      Art. 187. § 1.3) Rzecz znaleziona, która nie zostanie odebrana w ciągu 6 miesięcy od dnia\n"
    "doręczenia wezwania, staje się własnością znalazcy.\n"
    "      § 2. (uchylony)5)\n"
    "\n"
    "3)   W brzmieniu ustalonym przez art. 2 ustawy z dnia 23 stycznia 2026 r. (Dz. U. poz. 184), która\n"
    "     weszła w życie z dniem 19 maja 2026 r.\n"
    "5)\n"
    "     Uchylony przez art. 1 ustawy.\n"
    "                                                                                     2026-06-22\n"
    "\f"
    "Dziennik Ustaw                               – 3 –                                   Poz. 795\n"
    "\n"
    "      Art. 385[3]. W razie wątpliwości uważa się, że niedozwolonymi są te, które w szczególności:\n"
    "1)   wyłączają lub ograniczają odpowiedzialność względem konsumenta za szkody na osobie;\n"
    "2)   wyłączają lub istotnie ograniczają odpowiedzialność za niewykonanie lub nienależyte wyko-\n"
    "     nanie zobowiązania;\n"
    "\n"
    "                                                           DZIAŁ IV\n"
    "                                                        Współwłasność\n"
    "      Art. 195. Własność tej samej rzeczy może przysługiwać niepodzielnie kilku osobom (współwłasność).\n"
    "                                                                                     2026-06-22\n"
    "\f")


class TestAudyt2026PdfLayout(unittest.TestCase):
    """D01/D06: tekst z urzędowego PDF (pdftotext -layout) — nagłówki/stopki usunięte, wiersze sklejone,
    odsyłacze do przypisów poza numeracją, przypisy z dołu strony pod akapitem, jednostki na początku linii."""

    def setUp(self):
        self.t = eli.pdf_layout_do_tekstu(PDF_LAYOUT)

    def test_naglowki_i_stopki_usuniete(self):
        for smiec in ("Kancelaria Sejmu", "s. 1/3", "– 2 –", "2026-06-22", "Dziennik Ustaw"):
            self.assertNotIn(smiec, self.t, smiec)

    def test_wiersze_sklejone_i_dzielenie_wyrazow(self):
        self.assertIn("z zastrzeżeniem niezmieniających istotnie treści oferty", self.t)   # „nie-" + „zmieniających"
        self.assertIn("nienależyte wykonanie zobowiązania;", self.t)
        self.assertIn("w ciągu 6 miesięcy od dnia doręczenia wezwania, staje się", self.t)  # zwykła kontynuacja

    def test_odsylacz_do_przypisu_poza_numeracja_a_tresc_pod_akapitem(self):
        self.assertIn("Art. 187. § 1. Rzecz znaleziona", self.t)
        self.assertNotIn("§ 1.3)", self.t)
        self.assertIn("\n[przypis 3)] W brzmieniu ustalonym przez art. 2 ustawy z dnia 23 stycznia 2026 r. "
                      "(Dz. U. poz. 184), która weszła w życie z dniem 19 maja 2026 r.\n", self.t)
        self.assertIn("§ 2. (uchylony)\n[przypis 5)] Uchylony przez art. 1 ustawy.", self.t)
        self.assertNotIn("(uchylony)5)", self.t)

    def test_indeks_gorny_jak_w_html_i_fragmenty(self):
        self.assertIn("Art. 68 1. § 1.", self.t)
        frag = [self.t[s:e] for s, e in eli._fragmenty(self.t, "art. 68(1)")]
        self.assertEqual(len(frag), 1)
        self.assertIn("niezmieniających", frag[0])
        frag = [self.t[s:e] for s, e in eli._fragmenty(self.t, "art. 187")]
        self.assertEqual(len(frag), 1)
        self.assertIn("6 miesięcy", frag[0])
        self.assertIn("[przypis 3)]", frag[0])
        self.assertNotIn("Art. 195", frag[0])

    def test_obwieszczenie_oznaczone_i_nie_udaje_artykulu(self):
        # „Art. 3." cytowany w obwieszczeniu NIE może trafić jako nagłówek artykułu k.c.
        frag = [self.t[s:e] for s, e in eli._fragmenty(self.t, "art. 3")]
        self.assertEqual(len(frag), 1)
        self.assertIn("mocy wstecznej", frag[0])
        self.assertIn("» „Art. 3. Ustawa wchodzi w życie", self.t)
        self.assertIn("NIE są treścią aktu", self.t)
        self.assertIn("OBWIESZCZENIE", self.t)     # rozstrzelony tytuł złączony
        self.assertIn("\nUSTAWA\n", self.t)

    def test_pkt_i_naglowki_dzialu(self):
        self.assertIn("\n1) wyłączają lub ograniczają", self.t)
        self.assertIn("\n2) wyłączają lub istotnie", self.t)
        self.assertIn("\nDZIAŁ IV\n\nWspółwłasność\n", self.t)
        self.assertTrue(any(b == self.t.index("DZIAŁ IV") for b in
                            [m.start() for m in eli.re.finditer(eli._GRANICE, self.t)]))

    def test_pdftotext_awaria_daje_pusty_tekst(self):
        with mock.patch.object(eli.subprocess, "run", side_effect=OSError("brak")):
            self.assertEqual(eli.pdf_do_tekstu_layout(b"%PDF-1.4"), "")
        with mock.patch.object(eli.subprocess, "run", return_value=mock.Mock(returncode=1, stdout=b"")):
            self.assertEqual(eli.pdf_do_tekstu_layout(b"%PDF-1.4"), "")


def _bbox_stron_jak(raw):
    """Podmiana `_pdftotext`: `-bbox` daje tyle stron (bez słów), ile ma `raw` z `-layout`. Bez współrzędnych `tekst`
    ostrzega, a --strict blokuje (TestBezWspolrzednych202611b) — testy z samym `pdf_do_tekstu_layout` dostają je tutaj."""
    stron = raw.count("\f") if raw.endswith("\f") else raw.count("\f") + 1
    return lambda args, pdf_bytes: _bbox([(595.0, 842.0, [])] * stron) if "-bbox" in args else ""


class TestAudyt2026TekstZPdf(unittest.TestCase):
    """D01/D06/strict: przy textHTML=false `tekst` czyta WŁASNY PDF aktu (strict przepuszcza);
    gdy pdftotext nie działa — strict blokuje, a domyślnie starszy t.j. z pełnym ostrzeżeniem."""

    REFS_TJ = {"Tekst jednolity dla aktu": [{"act": {"ELI": "DU/1964/93", "displayAddress": "Dz.U. 1964 nr 16 poz. 93"}}]}
    REFS_BAZOWE = {
        "Inf. o tekście jednolitym": [
            {"act": {"ELI": "DU/2026/795", "displayAddress": "Dz.U. 2026 poz. 795"}},
            {"act": {"ELI": "DU/2025/1071", "displayAddress": "Dz.U. 2025 poz. 1071"}},
            {"act": {"ELI": "DU/2024/1061", "displayAddress": "Dz.U. 2024 poz. 1061"}}],
        "Akty zmieniające": [
            {"act": {"ELI": "DU/2026/184", "displayAddress": "Dz.U. 2026 poz. 184", "title": "Ustawa o rzeczach znalezionych",
                     "announcementDate": "2026-01-23", "promulgation": "2026-02-18"}, "date": "2026-05-19"},
            {"act": {"ELI": "DU/2026/507", "displayAddress": "Dz.U. 2026 poz. 507", "title": "Ustawa o CEIDG",
                     "announcementDate": "2026-03-13", "promulgation": "2026-04-13"}, "date": "2028-11-01"},
            {"act": {"ELI": "DU/2023/1", "displayAddress": "Dz.U. 2023 poz. 1", "title": "Stara",
                     "announcementDate": "2023-01-01", "promulgation": "2023-01-02"}, "date": "2023-02-01"}]}
    META = {"DU/2026/795": {"ELI": "DU/2026/795", "legalStatusDate": "2026-05-19", "textHTML": False, "textPDF": True,
                            "displayAddress": "Dz.U. 2026 poz. 795",
                            "texts": [{"fileName": "D20260795.pdf", "type": "O"}, {"fileName": "D20260795L.pdf", "type": "T"}]},
            "DU/2025/1071": {"ELI": "DU/2025/1071", "legalStatusDate": "2025-07-24"},
            "DU/2024/1061": {"ELI": "DU/2024/1061", "legalStatusDate": "2024-06-19"}}
    HTML = {"DU/2026/795": "", "DU/2025/1071": "", "DU/2024/1061": "<p>Art. 187.</p><p>§ 1. Rzecz znaleziona w ciągu roku.</p>"}

    def _fake_get(self, path, params=None, soft=False):
        m = eli.re.match(r"^/acts/(DU/\d+/\d+)(/references|/text\.html)?$", path)
        eli_id, co = m.group(1), m.group(2)
        if co == "/references":
            return self.REFS_BAZOWE if eli_id == "DU/1964/93" else self.REFS_TJ
        if co == "/text.html":
            return self.HTML[eli_id]
        return self.META[eli_id]

    def _tekst(self, argv, pdftotext=True, raw=PDF_LAYOUT):
        out = io.StringIO()
        pobrane = []
        with mock.patch.object(eli, "_get", side_effect=self._fake_get), \
                mock.patch.object(eli, "_get_bytes", side_effect=lambda url, soft=False: pobrane.append(url) or b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=pdftotext), \
                mock.patch.object(eli, "pdf_do_tekstu_layout", return_value=raw), \
                mock.patch.object(eli, "_pdftotext", side_effect=_bbox_stron_jak(raw)), \
                mock.patch.object(sys, "argv", ["eli.py"] + argv), \
                contextlib.redirect_stdout(out):
            eli.main()
        return out.getvalue(), pobrane

    def test_pdf_wlasnego_aktu_zamiast_starszego_tj_i_strict_przepuszcza(self):
        out, pobrane = self._tekst(["tekst", "DU", "2026", "795", "--fragment", "art. 187", "--strict"])
        self.assertEqual(pobrane, ["https://api.sejm.gov.pl/eli/acts/DU/2026/795/text/T/D20260795L.pdf"])
        self.assertIn("(tekst z urzędowego PDF przez pdftotext", out.replace("— tekst (z urzędowego", "(tekst z urzędowego"))
        self.assertIn("ELI_TEXT_SOURCE_PDF=https://api.sejm.gov.pl/eli/acts/DU/2026/795/text/T/D20260795L.pdf", out)
        self.assertIn("w ciągu 6 miesięcy", out)
        self.assertNotIn("w ciągu roku", out)                   # stare brzmienie z 2024/1061 NIE wchodzi
        self.assertNotIn("NIEAKTUALNE", out)
        self.assertNotIn("ELI_TEXT_SOURCE_FALLBACK", out)
        self.assertIn("odnotowano zmiany (1)", out)               # 2026/507 wchodzi w życie po stanie prawnym
        self.assertIn("Dz.U. 2026 poz. 507", out)

    def test_bez_pdftotext_starszy_tj_z_pelnym_ostrzezeniem_i_zmianami_inline(self):
        out, pobrane = self._tekst(["tekst", "DU", "2026", "795", "--fragment", "art. 187"], pdftotext=False)
        self.assertEqual(pobrane, [])
        self.assertIn("NIEAKTUALNE BRZMIENIE MOŻLIWE", out.split("\n")[0])
        self.assertIn("ELI_TEXT_SOURCE_FALLBACK=DU/2024/1061", out)
        self.assertIn("Pominięto t.j. z pustym text.html: DU/2025/1071", out)
        self.assertIn("brak programu pdftotext", out)
        self.assertIn("po 2024-06-19 (2) — NIE ma ich w poniższym tekście", out)
        self.assertIn("Dz.U. 2026 poz. 184", out)
        self.assertIn("wejście w życie zmiany 2026-05-19", out)
        self.assertNotIn("Dz.U. 2023 poz. 1 ", out)
        self.assertNotIn("odniesienia DU 2024 1061", out)        # martwa instrukcja usunięta
        self.assertNotIn("opóźnieniem", out)
        self.assertIn("w ciągu roku", out)

    def test_strict_blokuje_gdy_pdftotext_zawodzi(self):
        for pdftotext, raw, blad in ((False, PDF_LAYOUT, "brak programu pdftotext"),
                                     (True, "", "pdftotext nie zwrócił tekstu")):
            with self.subTest(blad=blad):
                with self.assertRaises(SystemExit) as caught:
                    self._tekst(["tekst", "DU", "2026", "795", "--strict"], pdftotext=pdftotext, raw=raw)
                self.assertNotEqual(caught.exception.code, 0)
                self.assertIn(blad, str(caught.exception.code))
                self.assertIn("strict blokuje zastępczy", str(caught.exception.code))

    def test_konstytucja_bez_html_i_bez_pdftotext_komunikat_bez_opoznienia(self):
        meta = {"DU/1997/483": {"ELI": "DU/1997/483", "textHTML": False,
                                "texts": [{"fileName": "D19970483Lj.pdf", "type": "U"}]}}
        def fake(path, params=None, soft=False):
            if path.endswith("/references"):
                return {"Podstawa prawna": []}
            if path.endswith("/text.html"):
                return ""
            return meta["DU/1997/483"]
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=False), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                eli.cmd_tekst(argparse.Namespace(sygnatura=["DU", "1997", "483"], json=False, strict=False,
                                                 pdf=None, fragment="Art. 45"))
        self.assertNotIn("opóźnieniem", str(caught.exception.code))
        self.assertIn("textHTML=false", str(caught.exception.code))
        self.assertIn("pdftotext", str(caught.exception.code))
        self.assertEqual(out.getvalue(), "")

    def test_konstytucja_przez_pdf(self):
        raw = ("©Kancelaria Sejmu                                                       s. 1/2\n\n"
               "      Art. 45. 1. Każdy ma prawo do sprawiedliwego i jawnego rozpatrzenia sprawy\n"
               "bez nieuzasadnionej zwłoki przez właściwy, niezależny, bezstronny i niezawisły sąd.\n"
               "      2. Wyłączenie jawności rozprawy może nastąpić ze względu na moralność.\n"
               "      Art. 46. Przepadek rzeczy może nastąpić tylko w przypadkach określonych w ustawie.\n"
               "                                                                        2015-01-07\n\f")
        meta = {"ELI": "DU/1997/483", "textHTML": False, "texts": [{"fileName": "D19970483Lj.pdf", "type": "U"}]}
        def fake(path, params=None, soft=False):
            if path.endswith("/references"):
                return {"Podstawa prawna": []}
            if path.endswith("/text.html"):
                return ""
            return meta
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), \
                mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_layout", return_value=raw), \
                mock.patch.object(eli, "_pdftotext", side_effect=_bbox_stron_jak(raw)), \
                contextlib.redirect_stdout(out):
            eli.cmd_tekst(argparse.Namespace(sygnatura=["DU", "1997", "483"], json=False, strict=True,
                                             pdf=None, fragment="Art. 45"))
        self.assertIn("Art. 45. 1. Każdy ma prawo do sprawiedliwego i jawnego rozpatrzenia sprawy bez "
                      "nieuzasadnionej zwłoki przez właściwy, niezależny, bezstronny i niezawisły sąd.\n"
                      "2. Wyłączenie jawności", out.getvalue())
        self.assertNotIn("Art. 46", out.getvalue())
        self.assertIn("/text/U/D19970483Lj.pdf", out.getvalue())


def _dwa_lamy(wiersze):
    """[(lewy łam, prawy łam)] → wiersze jak z `pdftotext -layout` (łamy obok siebie, rynna od kol. 52)."""
    return "\n".join(("    " + l).ljust(56) + p if p else "    " + l for l, p in wiersze) + "\n"


# Zeszyt Dz.U. 2003 Nr 105 (skrócony): koniec poz. 990, poz. 991 w dwóch łamach, początek poz. 992.
# Litery jak z pdftotext dla fontów „…PL" z lat 2000–2009 (kody Mac CE opisane jako Mac Roman).
PDF_ZESZYT_2003 = (
    "Dziennik Ustaw Nr 105                         — 7006 —                            Poz. 990 i 991\n"
    "\n"
    "Za∏àczniki:\n"
    "1. Potwierdzenie wp∏aty sk∏adki rocznej do Funduszu ˚eglugi Âródlàdowej przez wnioskodawc´ za dany rok.\n"
    "\n"
    "                                                  991\n"
    "                                    ROZPORZÑDZENIE MINISTRA KULTURY\n"
    "\n"
    "                                         z dnia 2 czerwca 2003 r.\n"
    "   w sprawie okreÊlenia kategorii urzàdzeƒ i noÊników s∏u˝àcych do utrwalania utworów oraz op∏at\n"
    "\n"
    + _dwa_lamy([
        ("Na podstawie art. 20 ust. 5 ustawy z dnia", "§ 1. Rozporzàdzenie okreÊla:"),
        ("4 lutego 1994 r. o prawie autorskim i pra-", "1) kategorie urzàdzeƒ i czystych noÊników"),
        ("wach pokrewnych (Dz. U. z 2000 r. Nr 80,", "   s∏u˝àcych do utrwalania utworów;"),
        ("poz. 904) zarzàdza si´, co nast´puje:", "2) wysokoÊç op∏at od urzàdzeƒ i noÊników,"),
        ("", "   o których mowa w pkt 1."),
    ])
    + "\f"
    "Dziennik Ustaw Nr 105                         — 7007 —                            Poz. 991 i 992\n"
    "\n"
    + _dwa_lamy([
        ("§ 2. Op∏ata od magnetofonów wynosi 3 %", "§ 3. Rozporzàdzenie wchodzi w ˝ycie po"),
        ("ceny sprzeda˝y.", "up∏ywie 14 dni od dnia og∏oszenia."),
        ("", ""),
        ("", "Minister Kultury: W. Dàbrowski"),
    ])
    + "\n"
    "                                                  992\n"
    "                                    ROZPORZÑDZENIE MINISTRA ZDROWIA\n"
    "\n"
    + _dwa_lamy([
        ("Na podstawie art. 7 ustawy o Êrodkach", "§ 2. Rozporzàdzenie wchodzi w ˝ycie"),
        ("farmaceutycznych zarzàdza si´, co nast´puje:", "z dniem og∏oszenia."),
        ("§ 1. Wykaz leków stanowi za∏àcznik.", ""),
    ]))


class TestPdfZeszytDzU2000_2011(unittest.TestCase):
    """PDF ogłoszonego aktu Dz.U./M.P. 2000–2011 to strony zeszytu w dwóch łamach (z sąsiednimi aktami);
    w 2000–2009 polskie litery w kodach Mac CE opisanych jako Mac Roman."""

    def setUp(self):
        self.t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_2003, 2003, 991))

    def test_polskie_litery(self):
        self.assertIn("ROZPORZĄDZENIE MINISTRA KULTURY", self.t)
        self.assertIn("w sprawie określenia kategorii urządzeń i nośników służących", self.t)
        for zle in ("∏", "à", "Ê", "´", "˝", "ƒ"):
            self.assertNotIn(zle, self.t, zle)

    def test_lamy_po_kolei(self):
        self.assertIn("Na podstawie art. 20 ust. 5 ustawy z dnia 4 lutego 1994 r. o prawie autorskim i prawach "
                      "pokrewnych (Dz. U. z 2000 r. Nr 80, poz. 904) zarządza się, co następuje:", self.t)
        self.assertIn("\n§ 1. Rozporządzenie określa:\n1) kategorie urządzeń i czystych nośników służących do "
                      "utrwalania utworów;\n2) wysokość opłat od urządzeń i nośników, o których mowa w pkt 1.", self.t)
        self.assertLess(self.t.index("§ 2. Opłata"), self.t.index("§ 3. Rozporządzenie"))

    def test_tylko_ten_akt_bez_naglowkow_zeszytu(self):
        self.assertTrue(self.t.startswith("991\n"), self.t[:40])
        for obce in ("Załączniki", "Funduszu Żeglugi", "992", "MINISTRA ZDROWIA", "Wykaz leków",
                     "Dziennik Ustaw", "— 7007 —"):
            self.assertNotIn(obce, self.t, obce)
        self.assertIn("Minister Kultury: W. Dąbrowski", self.t)

    def test_fragment_po_polskiej_frazie(self):
        frag = [self.t[s:e] for s, e in eli._fragmenty(self.t, "§ 3")]
        self.assertEqual(len(frag), 1)
        self.assertIn("wchodzi w życie po upływie 14 dni od dnia ogłoszenia", frag[0])

    def test_znak_wodny_2010_2011_i_bez_tabeli_liter(self):
        raw = PDF_ZESZYT_2003.replace("\n\n                                         z dnia",
                                      "\n                    .go\n                                         z dnia")
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 2010, 991))
        self.assertNotIn(".go", t)
        # fonty Mac CE poznaje się po treści, nie po roku (DU 2010 poz. 1 ma je mimo roku)
        self.assertIn("ROZPORZĄDZENIE", t)
        self.assertNotIn("MINISTRA ZDROWIA", t)

    def test_tabela_liter_nie_rusza_strony_z_polskimi_literami(self):
        # strona z poprawnymi polskimi literami i francuskim „à" (tekst umowy) — bez zmian
        strona = "Umowa sporządzona w Paryżu, à Paris, le 2 juin 2003 — „Załącznik”\n" * 3
        self.assertEqual(eli.pdf_zeszyt_do_aktu(strona, 2003, None).strip(), strona.strip())

    def test_bez_numeru_pozycji_tekst_caly(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_2003, 2003, 995))
        self.assertIn("Załączniki", t)
        self.assertIn("MINISTRA ZDROWIA", t)
        self.assertIn("prawach pokrewnych", t)

    def test_strona_w_jednym_lamie_bez_zmian(self):
        for strona in PDF_LAYOUT.split("\f"):
            self.assertEqual(eli._pdf_lamy(strona), strona)

    def test_tekst_z_pdf_tylko_dla_ogloszonego_aktu_1990_2011(self):
        def tekst(meta):
            with mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                    mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                    mock.patch.object(eli, "pdf_do_tekstu_layout", return_value=PDF_ZESZYT_2003):
                return eli._tekst_z_pdf("/acts/DU/%s/%s" % (meta["year"], meta["pos"]), "", meta)[0]
        o = {"publisher": "DU", "year": 2003, "pos": 991, "texts": [{"fileName": "D20030991.pdf", "type": "O"}]}
        for ten in (o, dict(o, publisher="MP")):
            with self.subTest(meta=ten):
                self.assertIn("ROZPORZĄDZENIE MINISTRA KULTURY", tekst(ten))
                self.assertNotIn("MINISTRA ZDROWIA", tekst(ten))
        for inny in (dict(o, year=2012), dict(o, year=1989),
                     dict(o, texts=[{"fileName": "D20030991Lj.pdf", "type": "U"}])):
            with self.subTest(meta=inny):
                self.assertIn("MINISTRA ZDROWIA", tekst(inny))   # bez zmian: cały tekst jak dotąd


def _bbox(strony):
    """[(szer, wys, [(x0, y0, słowo[, x1[, y1]])])] → HTML jak z `pdftotext -bbox` (domyślnie x1 = x0 + 6 pt
    na znak, y1 = y0 + 11)."""
    out = ['<doc>']
    for w, h, slowa in strony:
        out.append(f'<page width="{w}" height="{h}">')
        for x0, y0, t, *reszta in slowa:
            x1 = reszta[0] if reszta else x0 + 6 * len(t)
            y1 = reszta[1] if len(reszta) > 1 else y0 + 11
            out.append(f'<word xMin="{x0}" yMin="{y0}" xMax="{x1}" yMax="{y1}">{t}</word>')
        out.append("</page>")
    return "\n".join(out + ["</doc>"])


def _wiersz(y, tekst, x0=71.0, x1=480.0):
    """Wiersz treści justowany do prawej krawędzi `x1` (ostatnie słowo kończy się dokładnie na x1); szerokość
    znaku dobrana tak, żeby wiersz się zmieścił (najwyżej 6 pt), odstępy między słowami równe."""
    slowa = tekst.split()
    znaki = sum(len(t) for t in slowa)
    cw = min(6.0, (x1 - x0 - 4 * (len(slowa) - 1)) / znaki)
    odstep = (x1 - x0 - cw * znaki) / max(1, len(slowa) - 1)
    out, x = [], x0
    for t in slowa:
        out.append((round(x, 2), y, t, round(x + cw * len(t), 2)))
        x += cw * len(t) + odstep
    return out


# Strona tekstu ujednoliconego k.p.c. (skrót): art. 461 § 1¹ — brzmienie obecne w [ ], przyszłe w < >,
# notka na prawym marginesie (x ≥ 497) obok brzmienia przyszłego. Indeks górny „[1]" — osobne słowo tuż za „1".
STRONA_U = (595.32, 841.92, [
    (70.9, 28.6, "©Kancelaria"), (118.9, 28.6, "Sejmu"), (453.4, 28.6, "s."), (461.5, 28.6, "41/571")]
    + _wiersz(100, "Art. 461. § 1. Powództwo w sprawie z zakresu prawa pracy można wytoczyć bądź przed")
    + _wiersz(112, "sąd ogólnie właściwy dla pozwanego.")
    + _wiersz(124, "[§ 1[1]. Do właściwości sądów rejonowych należą sprawy z zakresu prawa pracy.]")
    + _wiersz(136, "<§ 1[1]. Do właściwości sądów rejonowych, bez względu na charakter sprawy,")
    + _wiersz(148, "należą sprawy z zakresu prawa pracy oraz mobbingu.>")
    + _wiersz(160, "§ 2. W sprawach z zakresu ubezpieczeń społecznych właściwy jest sąd.")
    + [(497.5, 138, "Nowe"), (527.5, 138, "brzmienie"), (497.5, 150, "§"), (505.0, 150, "1"), (511.0, 148, "[1]"),
       (531.0, 150, "w"), (541.0, 150, "art."), (565.0, 150, "461"), (497.5, 162, "wejdzie"), (541.0, 162, "w"),
       (551.0, 162, "życie"), (497.5, 174, "z"), (507.0, 174, "dn."), (527.0, 174, "5.11.2026"), (581.0, 174, "r."),
       (497.5, 186, "(Dz."), (527.0, 186, "U."), (543.0, 186, "z"), (553.0, 186, "2026"), (581.0, 186, "r."),
       (497.5, 198, "poz."), (527.0, 198, "1046).")]
    + [(440.0, 820.0, "2026-08-18")])
# Ta sama strona z `pdftotext -layout`, przycięta do szerokości treści (bez notki)
STRONA_U_LAYOUT = (
    "©Kancelaria Sejmu                                                             s. 41/571\n\n\n"
    "      Art. 461. § 1. Powództwo w sprawie z zakresu prawa pracy można wytoczyć bądź przed\n"
    "sąd ogólnie właściwy dla pozwanego.\n"
    "      [§ 1[1]. Do właściwości sądów rejonowych należą sprawy z zakresu prawa pracy.]\n"
    "      <§ 1[1]. Do właściwości sądów rejonowych, bez względu na charakter sprawy,\n"
    "należą sprawy z zakresu prawa pracy oraz mobbingu.>\n"
    "      § 2. W sprawach z zakresu ubezpieczeń społecznych właściwy jest sąd.\n")


class TestTekstUjednolicony(unittest.TestCase):
    """Audyt 2026-10: PDF typu U (tekst ujednolicony Kancelarii Sejmu) zawiera późniejsze zmiany, także
    te, które jeszcze nie weszły w życie ([obecne] <przyszłe> + notka na marginesie); helper sklejał
    notkę z treścią przepisu, gubił „Opracowano na podstawie…" i podpisywał tekst datą stanu prawnego t.j."""

    def test_notki_z_bbox_i_przypiecie_do_wiersza(self):
        strona1 = (595.32, 841.92, [(497.5, 77.6, "Opracowano"), (562.1, 77.6, "na"), (497.5, 89.2, "podstawie:"),
                                    (560.9, 89.2, "t.j."), (497.5, 100.7, "Dz."), (516.7, 100.7, "U."),
                                    (530.0, 100.7, "z"), (540.0, 100.7, "2026"), (566.0, 100.7, "r."),
                                    (497.5, 112.2, "poz."), (520.0, 112.2, "468,"), (545.0, 112.2, "1046.")]
                   + _wiersz(150, "Kodeks postępowania cywilnego normuje postępowanie sądowe"))
        prog, notki, info = eli._pdf_notki_z_bbox(eli._pdf_slowa(_bbox([strona1, STRONA_U])))
        self.assertTrue(480 < prog < 497)
        self.assertEqual(info["podstawa"], "Opracowano na podstawie: t.j. Dz. U. z 2026 r. poz. 468, 1046.")
        self.assertEqual(info["data_wydruku"], "2026-08-18")
        [(k, [notka])] = notki[1].items()
        self.assertEqual(notka, "Nowe brzmienie § 1[1] w art. 461 wejdzie w życie z dn. 5.11.2026 r. "
                                "(Dz. U. z 2026 r. poz. 1046).")
        self.assertEqual(k, 5)          # wiersz „<§ 1[1]. …" (5. niepusty wiersz strony, licząc nagłówek)

    def test_strona_z_tabela_za_progiem_nie_jest_przycinana(self):
        tabela = (595.32, 841.92, _wiersz(100, "Tabela stawek opłat obowiązujących w danym roku")
                  + [(500.0, 112, "1,5"), (530.0, 112, "3,0")])
        _, notki, _ = eli._pdf_notki_z_bbox(eli._pdf_slowa(_bbox([tabela])))
        self.assertEqual(notki, {})

    def _tekst(self):
        info = {"podstawa": "Opracowano na podstawie: t.j. Dz. U. z 2026 r. poz. 468, 1046."}
        return eli.pdf_layout_do_tekstu(STRONA_U_LAYOUT, info, {0: {5: [
            "Nowe brzmienie § 1[1] w art. 461 wejdzie w życie z dn. 5.11.2026 r. (Dz. U. z 2026 r. poz. 1046)."]}})

    def test_brzmienia_jako_osobne_akapity_a_notka_pod_brzmieniem_przyszlym(self):
        t = self._tekst()
        self.assertIn("\n[§ 1 1. Do właściwości sądów rejonowych należą sprawy z zakresu prawa pracy.]\n", t)
        self.assertIn("\n<§ 1 1. Do właściwości sądów rejonowych, bez względu na charakter sprawy, należą sprawy "
                      "z zakresu prawa pracy oraz mobbingu.>\n[margines: Nowe brzmienie § 1 1 w art. 461 wejdzie w życie "
                      "z dn. 5.11.2026 r. (Dz. U. z 2026 r. poz. 1046).]\n§ 2.", t)
        self.assertNotIn("wejdzie w życie z dn. 5.11.2026 r. (Dz. U. z 2026 r. poz. 1046). należą", t)

    def test_notka_bez_bbox_rozpoznana_po_odstepie_i_frazie(self):
        raw = ("      <§ 2. Adwokat lub radca prawny wnosi pisma w sposób wskazany\n"
               "                                                                                          § 2 w art. 125[1]\n"
               "Rzeczypospolitej Polskiej lub prokurator wnosi w sposób wskazany w § 1:                   wejdzie w życie z\n"
               "                                                                                          dn. 1.03.2027 r.\n"
               "1)    zgłoszenie się do udziału w sprawie;\n")
        t = eli.pdf_layout_do_tekstu(raw)
        self.assertIn("[margines: § 2 w art. 125 1 wejdzie w życie z dn. 1.03.2027 r.]", t)
        self.assertIn("prokurator wnosi w sposób wskazany w § 1:", t)
        self.assertNotIn("§ 1: § 2 w art.", t)

    def test_naglowek_artykulu_w_nawiasie(self):
        t = "Art. 477 6. Treść.\n\n<Art. 477 6a. Nowy artykuł.>\n[margines: Dodany art. 477 6a wejdzie w życie z dn. 5.11.2026 r.]\n\n[Art. 32. Stare.]"
        self.assertEqual(len(eli._hity_naglowka(t, "art. 477(6a)")), 1)
        self.assertEqual(len(eli._hity_naglowka(t, "art. 32")), 1)
        [(s, e)] = eli._fragmenty(t, "art. 477(6a)")
        self.assertIn("[margines: Dodany art. 477 6a", t[s:e])

    def test_daty_wejscia_w_zycie(self):
        self.assertEqual(eli._data_wejscia("Nowe brzmienie wejdzie w życie z dn. 5.11.2026 r. (Dz. U. poz. 1046)."), "2026-11-05")
        self.assertEqual(eli._data_wejscia("[przypis 13)] Dodany przez art. 2 ustawy (Dz. U. poz. 507), która wejdzie w życie "
                                           "z dniem 14 października 2026 r.; wejdzie w życie z dniem 1 listopada 2028 r."),
                         "2028-11-01")
        self.assertEqual(eli._data_wejscia("[przypis 3)] W brzmieniu ustalonym przez art. 1, która weszła w życie z dniem 1 lipca 2024 r."), "")

    def test_ostrzezenie_przyszle_zalezy_od_dnia(self):
        t = self._tekst()
        przed = "\n".join(eli._ostrzezenie_przyszle(t, True, dzis="2026-10-05"))
        self.assertIn("JESZCZE NIE OBOWIĄZUJĄ (dziś 2026-10-05): 1", przed)
        self.assertIn("od 2026-11-05: Nowe brzmienie § 1 1 w art. 461", przed)
        self.assertIn("obowiązuje brzmienie w nawiasie kwadratowym", przed)
        po = "\n".join(eli._ostrzezenie_przyszle(t, True, dzis="2026-11-05"))
        self.assertNotIn("JESZCZE NIE", po)
        self.assertIn("weszły już w życie zmiany (1)", po)
        self.assertIn("brzmienie w [ … ] jest NIEAKTUALNE", po)
        kc = "§ 3. Zawarcie umowy spółki w systemie.\n[przypis 13)] Dodany przez art. 2 ustawy, która wejdzie w życie z dniem 1 listopada 2028 r."
        self.assertIn("przepis z przypisem 13) wejdzie w życie z dniem 1 listopada 2028 r.",
                      "\n".join(eli._ostrzezenie_przyszle(kc, False, dzis="2026-10-05")))
        self.assertEqual(eli._ostrzezenie_przyszle(kc, False, dzis="2028-11-01"), [])

    def test_otwarte_bloki_dzial_i_zgubiony_nawias(self):
        t = ("<DZIAŁ IVFA\n[margines: Dodany dział IVFA wejdzie w życie z dn. 28.10.2026 r.]\nPostępowanie w sprawach SI\n\n"
             "Art. 479 88a. Sąd okręgowy.\n\nArt. 479 88b. Odwołanie.")
        [(otw, notki)] = eli._otwarte_bloki(t, t.index("Art. 479 88b"))
        self.assertEqual(otw, "<DZIAŁ IVFA")
        self.assertEqual(notki, ["[margines: Dodany dział IVFA wejdzie w życie z dn. 28.10.2026 r.]"])
        # „<§ 2 1." bez „>" (zgubiony w źródle) zamyka następny paragraf; blok „[…]" zamknięty nawiasem
        t2 = "Art. 9. § 1. A.\n[§ 2 1. Stare.]\n<§ 2 1. Nowe bez nawiasu.\n§ 3. Dalej.\nArt. 10. Kolejny."
        self.assertEqual(eli._otwarte_bloki(t2, t2.index("§ 3.")), [])
        self.assertEqual(eli._otwarte_bloki(t2, t2.index("Art. 10.")), [])

    def test_podstawa_i_oznaczenie_list_nowelizacji(self):
        self.assertEqual(eli._podstawa_ujednolicenia("Opracowano na podstawie: t.j. Dz. U. z 2025 r. poz. 383, 1818, 1872 "
                                                     "oraz z 2026 r. poz. 902, 988."),
                         {(2025, 383), (2025, 1818), (2025, 1872), (2026, 902), (2026, 988)})
        self.assertEqual(eli._podstawa_ujednolicenia("Opracowano na podstawie: Dz. U. z 1997 r. Nr 78, poz. 483, "
                                                     "z 2001 r. Nr 28, poz. 319."), {(1997, 483), (2001, 319)})
        linie = ["UWAGA: po tym tekście jednolitym (stan prawny na 2026-03-25) odnotowano zmiany (2) — sprawdź:",
                 "  - Dz.U. 2026 poz. 1046  Ustawa o zmianie k.p.  (wejście w życie zmiany 2026-11-05)",
                 "  - Dz.U. 2026 poz. 26  Ustawa o s.u.s.  (wejście w życie zmiany 2026-04-13)"]
        out = eli._oznacz_uwzglednione(linie, {(2026, 468), (2026, 1046)})
        self.assertIn("[UWZGLĘDNIONA w tym tekście ujednoliconym — NIE nakładaj ponownie]", out[1])
        self.assertIn("poza listą „Opracowano na podstawie”", out[2])
        self.assertEqual(eli._oznacz_uwzglednione(linie, set()), linie)

    def test_tekst_u_od_poczatku_do_konca(self):
        meta = {"ELI": "DU/2026/468", "legalStatusDate": "2026-03-25", "textHTML": False, "displayAddress": "Dz.U. 2026 poz. 468",
                "entryIntoForce": None, "texts": [{"fileName": "D20260468L.pdf", "type": "T"},
                                                  {"fileName": "D20260468Lj.pdf", "type": "U"}]}

        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                return {}
            if path.endswith("/text.html"):
                return ""
            return meta
        info = {"podstawa": "Opracowano na podstawie: t.j. Dz. U. z 2026 r. poz. 468, 1046.", "data_wydruku": "2026-08-18"}
        notki = {0: {5: ["Nowe brzmienie § 1[1] w art. 461 wejdzie w życie z dn. 5.11.2026 r. (Dz. U. z 2026 r. poz. 1046)."]}}
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_z_notkami", return_value=(STRONA_U_LAYOUT, notki, info)), \
                mock.patch.object(eli, "_dzis", return_value="2026-10-05"), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2026", "468", "--fragment", "art. 461", "--strict"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        out = out.getvalue()
        self.assertIn("/text/U/D20260468Lj.pdf", out)
        self.assertIn("TEKST UJEDNOLICONY Kancelarii Sejmu — nieurzędowy, wydruk z 2026-08-18", out)
        self.assertIn("To t.j. ze stanem prawnym na 2026-03-25 PLUS późniejsze zmiany z tej listy", out)
        self.assertNotIn("(stan prawny na 2026-03-25). Sklejanie", out)
        self.assertIn("od 2026-11-05: Nowe brzmienie § 1 1 w art. 461", out)
        self.assertLess(out.index("JESZCZE NIE OBOWIĄZUJĄ"), out.index("Art. 461."))


def _notka(x0, y0, wiersze, dy=11.5, cw=4.6):
    """Notka z marginesu: wiersze tekstu od lewego brzegu `x0`, co `dy` pt (wysokość słowa 11 pt jak w PDF U)."""
    out = []
    for i, w in enumerate(wiersze):
        x = x0
        for t in w.split():
            out.append((round(x, 2), round(y0 + i * dy, 2), t, round(x + cw * len(t), 2)))
            x += cw * len(t) + 3.4
    return out


def _strona_u(*grupy):
    """Strona PDF U: nagłówek, 30 wierszy treści justowanej do 468 pt (co 20.7 pt) + słowa `grupy`."""
    tresc = [(70.9, 28.6, "©Kancelaria", 118.0), (121.0, 28.6, "Sejmu", 150.0), (439.1, 28.6, "s.", 444.9),
             (447.2, 28.6, "13/40", 467.8)]
    for i in range(30):
        tresc += _wiersz(80 + 20.7 * i, f"wiersz {i + 1} treści przepisu justowany do prawej krawędzi kolumny", 56.6, 468.0)
    return (595.32, 841.92, tresc + [s for g in grupy for s in g])


class TestPrzegladTekstowU(unittest.TestCase):
    """Przegląd 186 tekstów ujednoliconych (PDF U, 2026-10): poprawnie oddanych było 72% notek z marginesu, a w 44
    aktach kawałki notek wpadały w treść przepisów (liczba mnoga „wejdą w życie”, notka pod 0,93 wysokości strony,
    data wydruku w notce, notki nachodzące na siebie, próg zaburzony tabelą, notka przy innym artykule)."""

    def test_liczba_mnoga_i_warianty_frazy_oraz_daty_bez_dn(self):
        for t, data in (("Dodane pkt 6 i 7 w art. 2 wejdą w życie z dn. 1.11.2028 r. (Dz. U. poz. 507).", "2028-11-01"),
                        ("Zmiana w ust. 3 w art. 5 wejdzie w życie z 1.01.2027 r. (Dz. U. z 2025 r. poz. 1705).", "2027-01-01"),
                        ("Przepis uchylający pkt 4 w art. 73 wejdzie w życie 1.09.2026 r. (Dz. U. poz. 781).", "2026-09-01"),
                        ("Dodany ust. 2 w art. 9 wejdziei w życie z dn. 1.03.2027 r.", "2027-03-01"),
                        ("Utraci moc z dniem 1 lipca 2026 r. (Dz. U. z 2023 r. poz. 1890).", "2026-07-01"),
                        ("Przepisy uchylające art. 47a i art. 47b utracą moc z dniem 2 stycznia 2029 r.", "2029-01-02"),
                        ("Pkt 4 i 5 w art. 6 wchodzą w życie z dniem 1 stycznia 2027 r.", "2027-01-01")):
            with self.subTest(t=t):
                self.assertTrue(eli._PDF_NOTKA.search(t))
                self.assertEqual(eli._data_wejscia(t), data)

    def test_termin_okreslony_w_komunikacie_jest_przyszly_bez_daty(self):
        t = ("Art. 42c. Treść.\n[margines: Dodane art. 42c– 42w wejdą w życie z dniem określonym w komunikacie, o którym "
             "mowa w art. 33 ustawy z dnia 15 maja 2024 r. (Dz. U. poz. 854).]\n"
             "[margines: Wejdzie w życie po upływie 7 dni od dnia ogłoszenia w Dzienniku Ustaw]")
        self.assertEqual(eli._termin_bez_daty(t.split("\n")[1]), "komunikat")
        self.assertEqual(eli._termin_bez_daty(t.split("\n")[2]), "nieznany")
        self.assertEqual(eli._termin_bez_daty("[przypis 3)] W brzmieniu ustalonym przez art. 1, która weszła w życie z dniem 1 lipca 2024 r."), "")
        # czas przeszły („utracił moc" — przypis o wyroku TK) to ani przyszła data, ani termin nieznany
        for przeszly in ("[przypis 10)] Utracił moc z dniem 26 stycznia 2024 r. na podstawie wyroku Trybunału Konstytucyjnego.",
                         "[przypis 3)] Ustawa utraciła moc na podstawie art. 54 ust. 1 pkt 3 ustawy z dnia 26 lipca 1991 r."):
            self.assertEqual((eli._data_wejscia(przeszly), eli._termin_bez_daty(przeszly)), ("", ""))
            self.assertEqual(eli._zmiany_w_tekscie(przeszly, "2026-10-05"), ([], []))
        przyszle, minione = eli._zmiany_w_tekscie(t, "2026-10-05")
        self.assertEqual([d for d, _ in przyszle], ["komunikat", "nieznany"])
        out = "\n".join(eli._ostrzezenie_przyszle(t, True, dzis="2026-10-05"))
        self.assertIn("JESZCZE NIE OBOWIĄZUJĄ (dziś 2026-10-05): 2 (w tym 2 bez daty wejścia w życie)", out)
        self.assertIn("  - termin wejścia w życie nieznany — określi komunikat: Dodane art. 42c", out)

    def test_ostrzezenie_zbiorcze_obejmuje_notki_bez_daty_takze_przy_dlugiej_liscie(self):
        t = "\n".join([f"[margines: Zmiana w art. {i} wejdzie w życie z dn. 1.0{1 + i % 9}.2027 r.]" for i in range(20)]
                      + ["[margines: Zmiana w art. 99 wejdzie w życie z dniem określonym w komunikacie, o którym mowa w art. 1.]"])
        out = eli._ostrzezenie_przyszle(t, True, dzis="2026-10-05", maks=5)
        self.assertIn(": 21 (w tym 1 bez daty", out[0])
        self.assertTrue(out[1].startswith("  - termin wejścia w życie nieznany — określi komunikat: Zmiana w art. 99"))

    def test_notki_nachodzace_na_siebie_maja_rozne_brzegi(self):
        # DU 2026 622 s. 357: druga notka przesunięta w lewo (x0 484,4), zaczyna się 3 pt pod końcem pierwszej
        pierwsza = _notka(492.7, 604.6, ["Zmiana (usunięcie", "wyrazu) w § 3 w", "art. 297 wejdzie", "w życie z dn.",
                                         "1.10.2026 r. (Dz.", "U. z 2026 r. poz.", "846)."])
        druga = _notka(484.4, 688.1, ["Nowe brzmienie", "§ 4 w art. 297", "wejdzie w życie z", "dn. 1.01.2027 r.",
                                      "(Dz. U. z 2026 r.", "poz. 1098)."])
        _, notki, _ = eli._pdf_notki_z_bbox(eli._pdf_slowa(_bbox([_strona_u(pierwsza, druga)])))
        wszystkie = [n for v in notki[0].values() for n in v]
        self.assertEqual(wszystkie, [
            "Zmiana (usunięcie wyrazu) w § 3 w art. 297 wejdzie w życie z dn. 1.10.2026 r. (Dz. U. z 2026 r. poz. 846).",
            "Nowe brzmienie § 4 w art. 297 wejdzie w życie z dn. 1.01.2027 r. (Dz. U. z 2026 r. poz. 1098)."])

    def test_ogon_notki_bez_frazy_i_notka_przy_dole_strony_obok_daty_wydruku(self):
        # DU 2026 880 s. 52: „(ust. 1 i ust. 2 pkt 1 wszedł w życie)" 17 pt pod notką; DU 2026 30 s. 13: notka sięga
        # 831 pt (> 0,93 wysokości), a data wydruku „2026-06-17" stoi w stopce tuż obok
        notka = _notka(492.5, 660.1, ["Dodany rozdział", "8a (art. 56a–56f)", "wejdzie w życie z", "dn. 2.04.2027 r.",
                                      "(Dz. U. z 2025 r.", "poz. 1669)."])
        ogon = _notka(492.5, 734.9, ["(ust. 1 i ust. 2 pkt", "1 wszedł w życie)"])
        dol = _notka(492.5, 765.5, ["Przepis uchylający", "art. 9 wejdzie w", "życie z dn.", "1.11.2028 r. (Dz.", "U. z 2026 r. poz.",
                                    "507)."], dy=11.0)
        strona = _strona_u(notka, ogon, dol, [(425.7, 796.6, "2026-06-17", 467.8)])
        _, notki, info = eli._pdf_notki_z_bbox(eli._pdf_slowa(_bbox([strona])))
        wszystkie = [n for v in notki[0].values() for n in v]
        self.assertEqual(wszystkie, [
            "Dodany rozdział 8a (art. 56a–56f) wejdzie w życie z dn. 2.04.2027 r. (Dz. U. z 2025 r. poz. 1669). "
            "(ust. 1 i ust. 2 pkt 1 wszedł w życie)",
            "Przepis uchylający art. 9 wejdzie w życie z dn. 1.11.2028 r. (Dz. U. z 2026 r. poz. 507)."])
        self.assertEqual(info["data_wydruku"], "2026-06-17")

    def test_notka_przechodzaca_na_nastepna_strone(self):
        koniec = _notka(492.5, 760.0, ["Dodany art. 42x", "wejdzie w życie z", "dniem określonym w", "komunikacie, o którym"])
        dalej = _notka(492.5, 54.1, ["mowa w art. 33", "ustawy (Dz. U. poz. 854)."])
        _, notki, _ = eli._pdf_notki_z_bbox(eli._pdf_slowa(_bbox([_strona_u(koniec), _strona_u(dalej)])))
        self.assertEqual([n for v in notki[0].values() for n in v],
                         ["Dodany art. 42x wejdzie w życie z dniem określonym w komunikacie, o którym mowa w art. 33 "
                          "ustawy (Dz. U. poz. 854)."])
        self.assertEqual(notki[1], {})      # strona z samym dokończeniem notki też jest przycinana (bez wycieku)

    def test_krawedz_tekstu_nie_przesuwa_sie_przez_tabele(self):
        # DU 2025 1154: kolumna liczb kończąca się na 518 pt w wielu wierszach dawała próg 518 (notki wpadały w treść)
        tabela = [w for i in range(40) for w in ((56.6, 300 + 11 * i, "Lp.", 70.0), (120.0, 300 + 11 * i, "stawka", 150.0),
                                                 (300.0, 300 + 11 * i, "zł", 310.0), (490.0, 300 + 11 * i, "12,50", 518.0))]
        strony = eli._pdf_slowa(_bbox([_strona_u(), (595.32, 841.92, tabela)]))
        self.assertEqual(eli._pdf_prawa_krawedz(strony), 468)

    def test_notka_tuz_za_krawedzia_tekstu_i_strona_przycinana_przed_nia(self):
        # DU 2026 75: treść do 481,8 pt, notka od 482,4 pt — przycięcie na 482 pt nie gubi ani treści, ani notki
        tresc = [w for i in range(30) for w in _wiersz(80 + 20.7 * i, "Rzeczypospolitej Polskiej od co najmniej stu lat", 56.6, 481.8)]
        notka = _notka(482.4, 685.8, ["Dodany pkt 2a w", "ust. 2 w art. 2", "wejdzie w życie z", "dn. 16.12.2026 r."])
        prog, marg, _ = eli._pdf_margines(eli._pdf_slowa(_bbox([(595.32, 841.92, tresc + notka)])))
        self.assertTrue(481.8 < prog < 482.4)
        przyciecie, strona = marg[0]
        self.assertEqual(przyciecie, 482)
        self.assertEqual([n for v in strona.values() for n in v],
                         ["Dodany pkt 2a w ust. 2 w art. 2 wejdzie w życie z dn. 16.12.2026 r."])

    def test_notka_przy_naglowku_strony_trafia_do_pierwszego_wiersza_tresci(self):
        # DU 2025 1431 s. 147: notka na wysokości nagłówka (k = 1) trafiała do pustego wiersza i znikała
        raw = ("©Kancelaria Sejmu                                                   s. 147/446\n\n\n"
               "3)    [minister właściwy do spraw wewnętrznych] <Komendant Główny Policji> – w przypadku lotniska.\n"
               "      2. Organy, o których mowa w ust. 1, opiniują plany.\n")
        t = eli.pdf_layout_do_tekstu(raw, {}, {0: {1: ["Zmiana w pkt 3 w ust. 1 w art. 87[7] wejdzie w życie z dn. "
                                                       "1.10.2026 r. (Dz. U. z 2026 r. poz. 864)."]}})
        self.assertIn("<Komendant Główny Policji> – w przypadku lotniska.\n[margines: Zmiana w pkt 3 w ust. 1 w art. 87 7 "
                      "wejdzie w życie z dn. 1.10.2026 r. (Dz. U. z 2026 r. poz. 864).]", t)

    def test_notka_przypieta_do_artykulu_ktorego_dotyczy(self):
        raw = ("©Kancelaria Sejmu                                                   s. 30/200\n"
               "      Art. 100. § 1. Sąd orzeka postanowieniem.\n"
               "      § 2. Sąd uzasadnia postanowienie.\n"
               "      <Art. 100a. § 1. Nowy przepis o posiedzeniu.>\n"
               "      Art. 101. § 1. Dalszy przepis.\n"
               "      <Rozdział 6a\n"
               "      Art. 102. Kolejny.\n")
        notki = {0: {3: ["Dodany art. 100a wejdzie w życie z dn. 1.10.2029 r. (Dz. U. z 2020 r. poz. 2320)."],
                     5: ["Zmiana w § 2 w art. 100 wejdzie w życie z dniem określonym w komunikacie, o którym mowa w art. 101 "
                         "ustawy z dnia 5 maja 2024 r."],
                     7: ["Dodany rozdział 6a (art. 102–102c) wejdzie w życie z dn. 1.11.2028 r."]}}
        t = eli.pdf_layout_do_tekstu(raw, {}, notki)
        # „Dodany art. 100a" stał przy § 2 art. 100 — trafia pod nagłówek art. 100a
        self.assertIn("<Art. 100a. § 1. Nowy przepis o posiedzeniu.>\n[margines: Dodany art. 100a", t)
        # art. 101 po frazie „wejdzie w życie" to przepis INNEJ ustawy — liczy się „§ 2 w art. 100"
        self.assertIn("§ 2. Sąd uzasadnia postanowienie.\n[margines: Zmiana w § 2 w art. 100", t)
        self.assertIn("<Rozdział 6a\n[margines: Dodany rozdział 6a", t)

    def test_podstawa_z_kropka_w_srodku_listy_i_bez_z_przed_rokiem(self):
        self.assertEqual(eli._podstawa_ujednolicenia("Opracowano na podstawie: t.j. Dz. U. z 2026 r. poz. 13, 426. 737, 912."),
                         {(2026, 13), (2026, 426), (2026, 737), (2026, 912)})
        self.assertEqual(eli._podstawa_ujednolicenia("Opracowano na podstawie: t.j. Dz. U. z 2025 r. poz. 89, 619, 621. 1794, "
                                                     "z 2026 r. poz. 507."),
                         {(2025, 89), (2025, 619), (2025, 621), (2025, 1794), (2026, 507)})
        self.assertEqual(eli._podstawa_ujednolicenia("Opracowano na podstawie: t.j. Dz. U. z 1959 r. Nr 14, poz. 78, z 1961 r. "
                                                     "Nr 32, poz. 161, 1971 r. Nr 27, poz. 250."),
                         {(1959, 78), (1961, 161), (1971, 250)})

    def test_indeks_gorny_sklejony_z_numerem_artykulu(self):
        # DU 2025 1459: -layout daje „Art. 131.", a to art. 13¹ — indeks górny jest mniejszy i podniesiony
        slowa = [(96.5, 250.0, "Art.", 117.4, 263.3), (120.4, 250.0, "13", 132.4, 263.3), (132.4, 248.9, "1", 136.4, 257.8),
                 (136.5, 250.0, ".", 139.5, 263.3), (142.5, 250.0, "1.", 151.5, 263.3), (154.7, 250.0, "W", 166.0, 263.3),
                 (96.5, 280.0, "Art.", 117.4, 293.3), (120.4, 280.0, "131.", 139.5, 293.3), (142.5, 280.0, "Dalej.", 170.0, 293.3)]
        [(_, _, ws)] = eli._pdf_slowa(_bbox([(595.32, 841.92, slowa)]))
        self.assertTrue(eli._pdf_indeks_gorny(ws[1], ws[2]))
        self.assertFalse(eli._pdf_indeks_gorny(ws[0], ws[1]))
        strona = "      Art. 131. 1. W razie rozwiązania stosunku pracy.\n      Art. 131. Dalej.\n"
        oznaczona = eli._pdf_oznacz_indeksy(strona, ws)
        self.assertEqual(oznaczona, "      Art. 13[1]. 1. W razie rozwiązania stosunku pracy.\n      Art. 131. Dalej.\n")
        t = eli.pdf_layout_do_tekstu(oznaczona)
        self.assertEqual(len(eli._hity_naglowka(t, "art. 13^1")), 1)
        self.assertEqual(len(eli._hity_naglowka(t, "art. 131")), 1)
        self.assertIn("Art. 13 1. 1. W razie", t)
        # litera przed indeksem („art. 6b³", DU 2026 884)
        self.assertEqual(len(eli._hity_naglowka("Art. 6b. A.\n\nArt. 6b 3. B.", "art. 6b(3)")), 1)
        # w notce z marginesu indeks górny też jest oznaczony
        self.assertEqual(eli._pdf_tekst_wiersza(ws[:4]), "Art. 13[1].")

    def test_struktura_podkreslnik_jako_indeks_gorny(self):
        # k.s.h. DU 2024 18: /struct ma „Art. 7_1." — to art. 7¹; --fragment "art. 7_1" dawał „Nie znaleziono"
        t = "Art. 7. (uchylony)\n\nArt. 7 1. § 1. Jeżeli przepis ustawy tak stanowi.\n\nArt. 71. § 1. Sąd rejestrowy."
        self.assertEqual(eli._hity_naglowka(t, "art. 7_1"), [t.index("Art. 7 1.")])
        self.assertEqual(eli._hity_naglowka(t, "Art. 7_1."), [t.index("Art. 7 1.")])
        out = io.StringIO()
        with mock.patch.object(eli, "_get", return_value=[{"id": "arti_7_1", "type": "arti", "title": "Art. 7_1."}]), \
                contextlib.redirect_stdout(out):
            eli.cmd_struktura(argparse.Namespace(sygnatura=["DU", "2024", "18"], json=False, filtr=None, poziom=None))
        self.assertIn('arti_7_1  [arti]  Art. 7_1.  (--fragment "art. 7(1)")', out.getvalue())

    def test_rozstrzelony_tytul_dlugi_z_granicami_wyrazow_z_bbox(self):
        # MP 2025 726: „M IN I S TR A F IN AN SÓ W I G O SPO DA RK I 1)" — odstęp liter ≈1 pt, między wyrazami ≈4,4 pt
        kawalki = [(187.0, "M", 196.4), (197.4, "IN", 209.4), (210.5, "I", 214.4), (215.4, "S", 220.9), (222.0, "TR", 236.7),
                   (237.8, "A", 245.0), (249.4, "F", 255.5), (256.6, "IN", 268.6), (269.7, "AN", 285.0), (286.1, "SÓ", 300.3),
                   (301.4, "W", 311.4), (315.9, "I", 319.8), (324.2, "G", 331.9), (332.9, "O", 340.7), (341.8, "SPO", 363.1),
                   (364.2, "DA", 379.6), (380.7, "RK", 396.5), (397.7, "I", 401.5)]
        slowa = [(x0, 256.5, t, x1, 267.5) for x0, t, x1 in kawalki] + [(402.9, 257.5, "1)", 408.3, 263.3)]
        slowa += [(150.0, 300.0, "USTAWA", 190.0, 311.0), (194.0, 300.0, "O", 200.0, 311.0), (204.0, 300.0, "PODATKU", 250.0, 311.0),
                  (254.0, 300.0, "I", 257.0, 311.0), (261.0, 300.0, "OPŁATACH", 310.0, 311.0)]
        [(_, _, ws)] = eli._pdf_slowa(_bbox([(595.32, 841.92, slowa)]))
        strona = ("                                      M IN I S TR A F IN AN SÓ W I G O SPO DA RK I 1)\n"
                  "                          USTAWA O PODATKU I OPŁATACH\n")
        self.assertEqual(eli._pdf_rozstrzelone(strona, ws),
                         "                                      MINISTRA FINANSÓW I GOSPODARKI1)\n"
                         "                          USTAWA O PODATKU I OPŁATACH\n")

    def test_przypis_przy_naglowku_rozdzialu_obejmuje_jego_artykuly(self):
        # k.wyb. (PDF T): przypis przy „Rozdział 11b" — rozdział wejdzie w życie 18.02.2027, art. 103e nie ma własnego
        txt = ("Art. 103c. Stary przepis.\n\nRozdział 11b\n[przypis 4)] Rozdział dodany przez art. 1 pkt 1 ustawy z dnia "
               "23 stycznia 2026 r. (Dz. U. poz. 178), która wejdzie w życie z dniem 18 lutego 2027 r.\n\nPortal poparcia\n\n"
               "Art. 103d. § 1. Wyborcom udostępnia się usługę.\n\nArt. 103e. Minister określi wzór.\n\nRozdział 12\n\n"
               "Art. 104. Dalej.")
        [(naglowek, przy)] = eli._naglowki_nadrzedne(txt, txt.index("Art. 103e."))
        self.assertEqual(naglowek, "Rozdział 11b")
        self.assertTrue(przy[0].startswith("[przypis 4)] Rozdział dodany"))
        self.assertEqual(eli._naglowki_nadrzedne(txt, txt.index("Art. 104.")), [])
        meta = {"ELI": "DU/2026/1261", "legalStatusDate": "2026-08-25", "textHTML": False, "displayAddress": "Dz.U. 2026 poz. 1261",
                "entryIntoForce": None, "texts": [{"fileName": "D20261261.pdf", "type": "T"}]}

        def fake_get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else meta
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "_tekst_z_pdf", return_value=(txt, "https://api.sejm.gov.pl/x.pdf", "")), \
                mock.patch.object(eli, "_dzis", return_value="2026-10-05"), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2026", "1261", "--fragment", "art. 103e"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        out = out.getvalue()
        self.assertIn("UWAGA: ten fragment leży w jednostce „Rozdział 11b”", out)
        self.assertIn("od 2027-02-18: przepis z przypisem 4) wejdzie w życie z dniem 18 lutego 2027 r.", out)

    def test_punkt_w_brzmieniu_przyszlym_to_nie_odsylacz_do_przypisu(self):
        # „<6) udostępnianie…" (DU 2026 30): „<" przed numerem punktu — punkt 6, a nie przypis 6
        t = eli.pdf_layout_do_tekstu("      Art. 5. 1. Wpisowi podlegają:\n<6) udostępnianie informacji o spółce cywilnej;>\n")
        self.assertIn("<6) udostępnianie informacji", t)
        self.assertNotIn("[przypis 6)]", t)

    def test_przypis_z_kilkoma_akapitami_nie_wpada_w_tresc(self):
        # k.s.h. art. 459–460: przypis 10) ma akapit z adnotacją o wyroku TK — po pustym wierszu, wcięty
        raw = ("        Art. 460.10) § 1. Do dnia złożenia wniosku rozwiązaniu może zapobiec uchwała.\n"
               "        § 2. Przepisu § 1 nie stosuje się w przypadku orzeczenia sądowego.\n\n\n\n"
               "10)\n"
               "      Utracił moc z dniem 26 stycznia 2024 r. na podstawie wyroku Trybunału Konstytucyjnego.\n\n"
               "      Wyrok Trybunału Konstytucyjnego został opublikowany w Dz. U. z adnotacją.\n")
        t = eli.pdf_layout_do_tekstu(raw)
        self.assertTrue(t.endswith("\n§ 2. Przepisu § 1 nie stosuje się w przypadku orzeczenia sądowego."), t[-200:])
        self.assertIn("[przypis 10)] Utracił moc z dniem 26 stycznia 2024 r. na podstawie wyroku Trybunału Konstytucyjnego. "
                      "Wyrok Trybunału Konstytucyjnego został opublikowany w Dz. U. z adnotacją.", t)


def _dwa_szerokie_lamy(wiersze):
    """Jak `_dwa_lamy`, ale z szeroką rynną (lewy łam do kol. ~50, prawy od kol. 64) — jak w zeszytach 2000–2011."""
    return "\n".join(("    " + l).ljust(64) + p if p else "    " + l for l, p in wiersze) + "\n"


class TestZeszytPrzypisyIPodpisWLamie(unittest.TestCase):
    """Zeszyty Dz.U. 1990–2011: przypisy z dołu lewego łamu trafiały między łamy (w środek treści), a podpis
    dosunięty do prawej krawędzi lewego łamu przy pustym prawym łamie zamykał blok i przestawiał kolejność."""

    STRONA = ("Dziennik Ustaw Nr 105                         — 7006 —                            Poz. 991\n\n"
              "                                                  991\n"
              "                                    ROZPORZĄDZENIE MINISTRA KULTURY1)\n\n"
              "                                         z dnia 2 czerwca 2003 r.\n\n"
              + _dwa_szerokie_lamy([
                  ("Na podstawie art. 20 ust. 5 ustawy z dnia 4 lutego", "§ 1. Rozporządzenie określa kategorie urządzeń"),
                  ("1994 r. o prawie autorskim i prawach pokrewnych", "i nośników służących do utrwalania utworów."),
                  ("zarządza się, co następuje:", "§ 2. Rozporządzenie wchodzi w życie po upływie"),
                  ("", "14 dni od dnia ogłoszenia."),
                  ("———————", ""),
                  ("1) Minister Kultury kieruje działem administracji", ""),
                  ("   rządowej — kultura i ochrona dziedzictwa.", "")]))

    def test_przypisy_z_dolu_lewego_lamu(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(self.STRONA, 2003, 991))
        self.assertIn("ROZPORZĄDZENIE MINISTRA KULTURY\n[przypis 1)] Minister Kultury kieruje działem administracji rządowej "
                      "— kultura i ochrona dziedzictwa.", t)
        self.assertIn("zarządza się, co następuje:\n§ 1. Rozporządzenie określa", t)
        self.assertNotIn("———", t)
        self.assertNotIn("nie odnaleziono", t)

    def test_podpis_przy_prawej_krawedzi_lewego_lamu_nie_zamyka_bloku(self):
        # układ jak w PDF_ZESZYT_2003 (wąska rynna): krótki podpis „W. Dąbrowski" dosunięty do prawej krawędzi lewego
        # łamu zaczyna się dalej niż 0,35 szerokości strony — wcześniej uznany za wyśrodkowany, zamykał blok
        strona = ("                                                  991\n"
                  "                                    ROZPORZĄDZENIE MINISTRA KULTURY\n\n"
                  + _dwa_lamy([
                      ("§ 1. Rozporządzenie określa wysokość opłat od", "Załącznik do rozporządzenia Ministra"),
                      ("urządzeń i czystych nośników, które służą do", "Kultury z dnia 2 czerwca 2003 r."),
                      ("utrwalania utworów. § 2. Rozporządzenie wcho-", "1. Magnetofony — 3 %"),
                      ("dzi w życie z dniem ogłoszenia.", "2. Kserokopiarki — 1 %"),
                      ("Minister Kultury:".rjust(44), ""),
                      ("W. Dąbrowski".rjust(44), ""),
                      ("", "3. Skanery — 1 %"),
                      ("", "4. Nagrywarki — 1 %")]))
        t = eli._pdf_lamy(strona)
        # podpis zostaje na końcu lewego łamu, przed załącznikiem z prawego — a nie w środku listy z prawego łamu
        self.assertLess(t.index("W. Dąbrowski"), t.index("Załącznik do rozporządzenia"))
        self.assertLess(t.index("2. Kserokopiarki"), t.index("3. Skanery"))
        # wyśrodkowany numer pozycji i tytuł nadal zamykają blok (zostają nad łamami)
        self.assertLess(t.index("ROZPORZĄDZENIE MINISTRA KULTURY"), t.index("§ 1. Rozporządzenie"))


class TestMetaVacatioCytatUchylenie(unittest.TestCase):
    """Audyt 2026-10: akt w vacatio legis miał tylko „Status: obowiązujący"; brak urzędowej formy cytatu
    („Dz. U. z 2001 r. Nr 112, poz. 1198"); akt uchylony bez wskazania aktu uchylającego."""

    META = {"title": "Ustawa z dnia 19 czerwca 2026 r. o zmianie ustawy - Kodeks pracy", "displayAddress": "Dz.U. 2026 poz. 1046",
            "type": "Ustawa", "status": "obowiązujący", "inForce": "IN_FORCE", "announcementDate": "2026-06-19",
            "promulgation": "2026-08-04", "entryIntoForce": "2026-11-05", "ELI": "DU/2026/1046", "texts": [], "textHTML": True}

    def _meta(self, meta, refs=None, dzis="2026-10-05"):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=lambda p, *a, **k: refs if p.endswith("/references") else meta), \
                mock.patch.object(eli, "_dzis", return_value=dzis), contextlib.redirect_stdout(out):
            eli.cmd_meta(argparse.Namespace(sygnatura=["DU", "2026", "1046"], json=False))
        return out.getvalue()

    def test_vacatio_legis(self):
        self.assertIn("akt jeszcze NIE WSZEDŁ W ŻYCIE — wejście w życie 2026-11-05 (dziś 2026-10-05)", self._meta(self.META))
        self.assertNotIn("NIE WSZEDŁ", self._meta(self.META, dzis="2026-11-05"))

    def test_jednostki_wchodzace_pozniej_z_uwag(self):
        m = dict(self.META, entryIntoForce="2026-08-11",
                 comments="1) art. 125 ust. 4 wchodzi w życie z dniem 28 lipca 2026 r.; 2) art. 8-18 wchodzą w życie 28 października 2026 r.")
        out = self._meta(m)
        self.assertIn("część przepisów wchodzi w życie PÓŹNIEJ (2026-10-28)", out)
        self.assertNotIn("2026-07-28)", out)

    def test_cytat_urzedowy(self):
        self.assertEqual(eli._cytat({"displayAddress": "Dz.U. 2001 nr 112 poz. 1198"}), "Dz. U. z 2001 r. Nr 112, poz. 1198")
        self.assertEqual(eli._cytat({"displayAddress": "Dz.U. 2024 poz. 18"}), "Dz. U. z 2024 r. poz. 18")
        self.assertEqual(eli._cytat({"displayAddress": "M.P. 2004 nr 5 poz. 100"}), "M.P. z 2004 r. Nr 5, poz. 100")
        self.assertIn("Cytat:   Dz. U. z 2026 r. poz. 1046", self._meta(self.META))

    def test_uchylony_przez(self):
        m = dict(self.META, status="uchylony", inForce="NOT_IN_FORCE", entryIntoForce="1998-04-30",
                 displayAddress="Dz.U. 1997 nr 133 poz. 883")
        refs = {"Akty uchylające": [{"act": {"ELI": "DU/2018/1000", "displayAddress": "Dz.U. 2018 poz. 1000",
                                             "title": "Ustawa z dnia 10 maja 2018 r. o ochronie danych osobowych"},
                                     "date": "2018-05-25"}]}
        out = self._meta(m, refs)
        self.assertIn("Uchylony przez: Dz.U. 2018 poz. 1000  Ustawa z dnia 10 maja 2018 r. o ochronie danych osobowych", out)
        self.assertIn("API nie wskazuje aktu uchylającego", self._meta(m, {}))


class TestZeszytStopkaIZnakWodny(unittest.TestCase):
    """Audyt 2026-10 (PR #14 — ciąg dalszy): stopka wydawnicza zeszytu w tekście ostatniego aktu, resztki
    znaku wodnego www.rcl.gov.pl („ov.pl", „w. ww" przed nagłówkiem), sam numer strony „— 3052 —"."""

    def test_stopka_zeszytu_pod_lamami_usunieta(self):
        raw = (_dwa_lamy([("§ 1. Ustala się stawki.", "§ 2. Wchodzi w życie z dniem ogłoszenia."),
                          ("", "Minister: R. Sikorski")] + [("", "")] * 6)
               + "Egzemplarze bieżące oraz archiwalne można nabywać:\n"
               "         — w Centrum Usług Wspólnych\n"
               "                       Wydawca: Kancelaria Prezesa Rady Ministrów\n"
               "Zam. 1423/W/C/2011                    ISSN 0867-3411                     Cena 4,30 zł\n")
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 2011, None))
        self.assertIn("Minister: R. Sikorski", t)
        for smiec in ("Egzemplarze", "Wydawca", "ISSN", "Cena 4,30"):
            self.assertNotIn(smiec, t)

    def test_znak_wodny_i_numer_strony(self):
        raw = ("w. ww Dziennik Ustaw Nr 1          — 3 —          Poz. 1\n\n"
               "      § 1. Treść przepisu.\n"
               "                        ov.pl\n"
               "                                   — 3052 —\n"
               "      § 2. Dalsza treść.\n")
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 2010, None))
        self.assertEqual(t, "§ 1. Treść przepisu.\n§ 2. Dalsza treść.")

    def test_tabela_nie_jest_rozrywana_na_lamy(self):
        wiersze = [("    4   Radio z magnetofonem i odtwarzaczem płyt CD      1,5", ""),
                   ("    5   Radio z magnetofonem                              1,5", "")]
        strona = "\n".join(("Lp.   Typ urządzenia lub nośnika                     Wysokość opłaty w %",) + tuple(
            l for l, _ in wiersze) * 5)
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(strona, 2003, None))
        self.assertIn("4 Radio z magnetofonem i odtwarzaczem płyt CD 1,5", t)


# Zeszyt Dz.U. 1998 Nr 55 (prawdziwy wynik `pdftotext -layout`, skrócony spis treści): winieta i spis treści,
# poz. 351 w dwóch łamach, nagłówek strony z myślnikami ASCII („-   2134 -"), początek poz. 352.
PDF_ZESZYT_1998 = "\n".join((
    '                         DZIENNIK USTAW',
    '                         RZECZYPOSPOLITEJ POLSKIEJ',
    '                                      Warszawa, dnia 6 maja 1998 r.                                            Nr 55',
    'TREŚĆ:',
    'Poz.:',
    '                                                USTAWA',
    '351 -    z dnia 20 marca 1998 r. o zmianie ustawy o rewaloryzacji emerytur i rent, o zasadach usta -',
    '         lania emerytur i rent oraz o zmianie niektórych ustaw                                       2133',
    '',
    '                           WYROK TRYBUNAŁU KONSTYTUCYJNEGO',
    '363 -    z dnia 8 kwietnia 1998 r. sygn . akt K. 10/97                                              . 2156',
    '',
    '',
    '',
    '                                                             351',
    '                                                          USTAWA',
    '',
    '                                                   z dnia 20 marca 1998 r.',
    '',
    '              o zmianie ustawy o rewaloryzacji emerytur i rent, o zasadach ustalania emerytur i rent',
    '                                       oraz o zmianie niektórych ustaw.',
    '',
    '    Art. 1. W ustawie z dnia 17 października 1991 r. o re -       poz. 636 i Nr 147, poz. 687 oraz z 1997 r. Nr 30, poz. 164,',
    'waloryzacji emerytur i rent, o zasadach ustalania eme-            Nr 106, poz. 676, Nr 111 , poz. 725 i Nr 141, poz. 943)',
    'rytur i rent oraz o zmian ie niektórych ustaw (Dz. U.             wprowadza się następuj ące zmian y :',
    'Nr 104, poz. 450, z 1992 r. Nr 21, poz. 84, z 1993 r. Nr 127,',
    '                                                                   1) wart. 2 w ust. 1 pkt 3a otrzymuje brzmienie:',
    'poz. 583 i Nr 129, poz. 602, z 1994 r. Nr 84, poz. 385,',
    'z 1995 r. Nr 4, poz. 17, Nr 95, poz. 473 i Nr 138, poz. 681,          ,,3a) okresy   działalności      kombatanckiej,   działal\xad',
    'Z 1996 r. Nr 87, poz. 395, Nr 100, poz. 461 , Nr 136,                       ności równorzędnej z tą działalnośc ią, a także',
    '\x0cDziennik Ustaw Nr 55                               -   2134 -                                         Poz. 351 i 352',
    '',
    '           okresy zaliczane do okresów tej działalności        Art. 2. 1. Ponowne ustalenie wysokości świadcze\xad',
    '           oraz okresy podlegania represjom wojennym       nia, przy uwzględnieniu nowych okresów składko\xad',
    '           i okresu powojennego, określone w przepi-       wych, następuje na wniosek uprawnionego.',
    '           sach o kombatantach oraz niektórych oso-            2. Podwyższone świadczenia wraz z należnymi od-',
    '           bach będących ofiarami represji wojennych       setkami wypłaca się za okres od dnia 11 września',
    '           i okresu powojennego,";                         1997 r., jeżeli wniosek w tej sprawie został zgłoszony',
    ' 2) wart. 4 w ust. 1 skreśla się pkt 1;                    w ciągu 2 lat od dnia wejścia w życie ustawy.',
    '                                                                Art. 3. Ustawa wchodzi w życie z dniem ogłoszenia.',
    ' 3) wart. 6 w ust. 4 skreśla się wyrazy "z zastrzeżeniem',
    '    art. 4 ust. 2".                                          Prezydent Rzeczypospolitej Polskiej: A. Kwaśniewski',
    '',
    '',
    '                                                       352',
    '                                 ROZPORZĄDZENIE PREZESA RADY MINISTRÓW',
    '',
    '                                            z dnia 30 kwietnia 1998 r.',
    '',
    'w sprawie organizacji pracy, składu oraz zasad wynagradzania członków komisji rozpatrującej wnioski o wy-',
    '                       rażenie zgody na zatrudnienie osób, które pełniły funkcje publiczne.',
    '',
    '',
    '    Na podstawie art. 7 ust. 6 ustawy z dnia 21 sierp-         § 6. 1. Przewodniczący komisji zapewnia sprawne',
    'nia 1997 r. o ograniczeniu prowadzenia działalności go-    i zgodne z prawem funkcjonowanie komisji, w szcze-',
    'spodarczej przez osoby pełniące funkcje publiczne          gólności:',
    '(Dz. U. Nr 106, poz. 679) zarządza się, co następuje:',
    '                                                             1) wyznacza skład do rozpatrzenia danej sprawy,',
    '   §   1. Rozporządzenie określa organizację pracy              w tym przewodniczącego składu,',
    'i skład komisji rozpatrującej wnioski o wyrażenie zgo-',
    'dy na zatrudnienie u przedsiębiorcy osób, przed upły\xad        2) informuje Prezesa Rady Ministrów o okolicznoś\xad',
    'wem roku od zaprzestania zajmowania przez nie stano-            ciach uzasadniających odwołanie członka komisji,',
    'wiska lub pełnienia funkcji publicznej, oraz wnioski        3) składa Prezesowi Rady Ministrów coroczne spra -',
    'o wydanie opinii, czy działalność gospodarcza małżon\xad          wozdanie z prac komisji,',
)) + "\n"

# Zeszyt Dz.U. 1992 Nr 60, strona 1 (prawdziwy wynik `pdftotext -layout`, skrócony spis treści): spis treści
# na całą szerokość nad łamami, lewy łam poz. 302 wchodzi w rynnę („Europejs-       1991 r."), poz. 303 obok.
PDF_ZESZYT_1992 = "\n".join((
    '                          DZIENNIK USTAW',
    '                         RZECZYPOSPOLITEJ POLSKIEJ',
    '                                     Warszawa, dnia 13 sierpnia 1992 r.                                           Nr 60',
    'TRE$Ć:',
    'Poz.:',
    '                                             USTAWY:',
    '302 -   z dnia 4 lipca 1992 r. o ratyfikacji Układu Europejskiego ustanawiającego stowarzyszenie między',
    '        Rzecząpospolitą Polską a Wspólnotami Europejskimi i ich Państwami Członkowskimi, sporządzone-',
    '        go w Brukseli dnia 16 grudnia 1991 r. .                                                         1101',
    '303 -   z dnia 30 lipca 1992 r. o ustanowieniu $więta Wojska Polskiego .                                   1101',
    '',
    '',
    '',
    '',
    '                                                               302',
    '                                                           USTAWA',
    '',
    '                                                      z dnia 4 lipca 1992 r',
    '',
    'o ratyfikacji Układu Europejskiego ustanawiającego stowarzyszenie między Rzecząpospolitą Polską a Wspól-',
    ' notami Europejskimi i ich Państwami Członkowskimi, sporządzonego w Brukseli dnia 16 grudnia 1991 r.',
    '',
    '     Art. 1. Wyraża się zgodę na dokonanie przez Prezy-            Członkowskimi, sporządzonego w Brukseli dnia 16 grudnia',
    'denta Rzeczypospolitej Polskiej ratyfikacji Układu Europejs-       1991 r.',
    'kiego ustanawiającego stowarzyszenie między Rzecząpos\xad',
    'politą Polską a Wspólnotami Europejskimi i ich Państwami                Art. 2. Ustawa wchodzi w życie z dniem ogłoszenia.',
    '',
    '                                                                                 Prezydent Rzeczypospolitej Polskiej: L. Wałęsa',
    '',
    '',
    '',
    '                                                               303',
    '                                                           USTAWA',
    '',
    '                                                     z dnia 30 lipca 1992 r.',
    '',
    '                                       o ustanowieniu $więta Wojska Polskiego.',
    '',
    '    Art. 1. Dzień 15 sierpnia ustanawia się Świętem Wojs-              3. Sposób obchodzenia w Siłach Zbrojnych Święta',
    'ka Polskiego.                                                      Wojska Polskiego oraz innych świąt wojskowych określa',
    '                                                                   Minister Obrony Narodowej, a w jednostkach wojskowych',
    '    Art. 2. 1. W rodzajach Sił Zbrojnych, wojsk i służb,           podporządkowanych Ministrowi Spraw Wewnętrznych',
    'w jednostkach wojskowych i ich związkaćh organizacyjnych           - Minister Obrony Narodowej w porozumieniu z Ministrem',
    'mogą być obchodzone również inne święta wojskowe,                  Spraw Wewnętrznych.',
    'ustanowione dla nich przez Ministra Obrony Narodowej,',
    'a w stosunku do jednostek wojskowych podporządkowa\xad                    Art. 3. Traci moc dekret z dnia 7 października 1950 r.',
    'nych Ministrowi Spraw Wewnętrznych - przez Ministra                o ustanowieniu dnia 12 października Dniem Wojska Pol-',
    'Spraw Wewnętrznych.                                                skiego (Dz. U. Nr 45, poz. 411).',
    '    2. Obchody Święta Wojska Polskiego i innych świąt                   Art. 4. Ustawa wchodzi w życie z dniem ogłoszenia.',
    'wojskowych organizuje się w formie uroczystej, ustalonej',
    'w ceremoniale wojskowym.                                                         Prezydent Rzeczypospolitej Polskiej: L. Wałęsa',
)) + "\n"


class TestPdfZeszytLata90(unittest.TestCase):
    """Zeszyty Dz.U./M.P. 1990–1999: skan z warstwą tekstową z OCR — nagłówki z myślnikami ASCII albo zniekształcone,
    spis treści na pierwszej stronie, łamy przesunięte względem rynny, okruchy OCR przy numerze pozycji."""

    def test_akt_z_zeszytu_1998_bez_naglowka_ascii_i_sasiada(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_1998, 1998, 351))
        self.assertTrue(t.startswith("351\n"), t[:40])
        for obce in ("Dziennik Ustaw", "2134", "TREŚĆ", "352", "ROZPORZĄDZENIE PREZESA", "Na podstawie art. 7"):
            self.assertNotIn(obce, t, obce)
        # nagłówek strony nie wpada w środek akapitu, który przechodzi na następną stronę
        self.assertIn("działalnośc ią, a także okresy zaliczane do okresów tej działalności", t)
        poz = [t.index(f) for f in ("Art. 1. W ustawie", "2) wart. 4", "3) wart. 6", "Art. 2. 1. Ponowne",
                                     "2. Podwyższone", "Art. 3. Ustawa wchodzi", "A. Kwaśniewski")]
        self.assertEqual(poz, sorted(poz))

    def test_sasiedni_akt_z_tego_samego_zeszytu(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_1998, 1998, 352))
        self.assertTrue(t.startswith("352\n"), t[:40])
        self.assertNotIn("Kwaśniewski", t)
        self.assertLess(t.index("§ 1. Rozporządzenie określa"), t.index("§ 6. 1. Przewodniczący"))

    def test_spis_tresci_nie_zaslania_rynny_a_lam_przesuniety_przez_ocr(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_1992, 1992, 302))
        self.assertIn("Art. 1. Wyraża się zgodę na dokonanie przez Prezydenta Rzeczypospolitej Polskiej ratyfikacji "
                      "Układu Europejskiego ustanawiającego stowarzyszenie między Rzecząpospolitą Polską a Wspólnotami "
                      "Europejskimi i ich Państwami Członkowskimi, sporządzonego w Brukseli dnia 16 grudnia 1991 r.", t)
        for obce in ("TRE$Ć", "303", "Święta Wojska", "1101"):
            self.assertNotIn(obce, t, obce)
        # luz rynny tylko dla lat 90. — dla 2000–2011 łamy dzielone jak dotąd
        self.assertEqual(eli._pdf_lamy(PDF_ZESZYT_2003.split("\f")[0], luz=0),
                         eli._pdf_lamy(PDF_ZESZYT_2003.split("\f")[0]))

    def test_naglowki_stron_znieksztalcone_przez_ocr(self):
        for naglowek in ("Dziennik Ustaw Nr 24                  319                  Poz. 141 i 142",
                         "Dzienr.!k Ustaw Nr 119              -    1655 -           Poz. 517",
                         "Dzienn ik Ustaw Nr 98 ~ 3089 ~ Poz. 602",
                         "Monitor Polski Nr 65 .. 834 Poz. 578, 579 i 580",
                         "Dziennik Ustaw Nr 119 - 1619 ' Paz, 513, 514 i 515",
                         "  •\n\nDziennik Ustaw Nr 7                 -     80          Poz. 25 i 26",
                         "\f                     -    2186    -                     Poz. 428 i 429\nDziennik Ustaw Nr 85"):
            with self.subTest(naglowek=naglowek):
                strona = naglowek + "\n\n   § 1. Treść przepisu (Monitor Polski Nr 41, poz. 264).\n"
                self.assertEqual(eli._pdf_bez_naglowkow_stron(strona).strip("\f\n "),
                                 "§ 1. Treść przepisu (Monitor Polski Nr 41, poz. 264).")
        # wiersz treści na górze strony zostaje, także cytat „Dziennik Ustaw Nr …" bez „Poz."
        for tresc in ("Dziennik Ustaw Nr 5 ogłasza się w terminie", "   § 2. Dalsza treść.", "1"):
            self.assertEqual(eli._pdf_bez_naglowkow_stron(tresc + "\n"), tresc + "\n")

    def test_numer_pozycji_z_okruchami_ocr_i_sygnatura(self):
        raw = ("                    463 .\n            USTAWA\n   Art. 1. Treść.\n"
               "                    464\n            Rej . 184/94\n   Inny akt.\n")
        self.assertEqual(eli._pdf_wytnij_akt(raw, 463).split("\n")[-1], "   Art. 1. Treść.")
        raw = "                    346\nSygn. akt W. 7/96\n   UCHWAŁA\n   Treść.\n   347\n   OBWIESZCZENIE\n"
        self.assertNotIn("OBWIESZCZENIE", eli._pdf_wytnij_akt(raw, 346))

    def test_zle_odczytana_cyfra_numeru_nastepnej_pozycji(self):
        raw = ("     499\n   USTAWA\n   Art. 1. Treść.\n"
               "     600\n   ROZPORZĄDZENIE RADY MINISTRÓW\n   § 1. Cudzy akt.\n")
        self.assertNotIn("Cudzy akt", eli._pdf_wytnij_akt(raw, 499, ocr=True))
        self.assertIn("Cudzy akt", eli._pdf_wytnij_akt(raw, 499))     # 2000–2011: numer musi się zgadzać

    def test_ogloszenia_wydawcy_przed_stopka_lat_90(self):
        raw = (_dwa_lamy([("§ 1. Ustala się stawki.", "§ 2. Wchodzi w życie z dniem ogłoszenia."),
                          ("", "Minister Finansów: G. Kołodko")] + [("", "")] * 6)
               + "Pojedyncze' egzemplarze Dziennika Ustaw i Monitora Polskiego można nabywać za gotówkę:\n"
               "w Warszawie: - w punkcie sprzedaży Urzędu Rady Ministrów, al. I Armii Wojska Polskiego 2/4\n"
               "Egzemplarze b i eżące o raz z lat u b ieg łych m ożna nabyw ać na podstawie zamówienia\n"
               "                       Wydawca: Urząd Rady Ministrów\n"
               "Zam. 1423                    ISSN 0867-3411                     Cena 4000 zł\n")
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 1993, None))
        self.assertIn("Minister Finansów: G. Kołodko", t)
        for smiec in ("Pojedyncze", "Egzemplarze", "Wydawca", "ISSN"):
            self.assertNotIn(smiec, t)
        # 2000–2011: tylko dotychczasowe frazy stopki
        self.assertIn("Pojedyncze", eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 2003, None)))

    def test_tekst_z_pdf_lat_90_oznacza_ocr(self):
        meta = {"publisher": "DU", "year": 1998, "pos": 351, "texts": [{"fileName": "D19980351.pdf", "type": "O"}]}
        info = {}
        with mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_layout", return_value=PDF_ZESZYT_1998):
            t = eli._tekst_z_pdf("/acts/DU/1998/351", "", meta, info)[0]
        self.assertTrue(t.startswith("351\n"), t[:40])
        self.assertNotIn("ROZPORZĄDZENIE PREZESA", t)
        self.assertTrue(info.get("ocr"))
        self.assertNotIn("puste_strony", info)


class TestOcrSkanow(unittest.TestCase):
    """Punkt 6 (2026-10): strony bez warstwy tekstowej — OCR (tesseract), gdy jest; tekst oznaczony jako
    niepewny, --strict go blokuje; długie skany tylko z --ocr."""

    META = {"ELI": "DU/1992/413", "year": 1992, "pos": 413, "publisher": "DU", "textHTML": False,
            "displayAddress": "Dz.U. 1992 nr 80 poz. 413", "texts": [{"fileName": "D19920413.pdf", "type": "O"}]}
    OCR = ("413\nROZPORZĄDZENIE MINISTRA SPRAWIEDLIWOŚCI\nz dnia 23 października 1992 r.\n"
           "§ 1. W rozporządzeniu wprowadza się zmiany.\n§ 2. Rozporządzenie wchodzi w życie z dniem ogłoszenia.\n")

    def _tekst(self, argv, stron=1):
        def fake_get(path, params=None, soft=False):
            if path.endswith("/references"):
                return {}
            if path.endswith("/text.html"):
                return ""
            return self.META
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_z_notkami", return_value=("\f" * stron, {}, {})), \
                mock.patch.object(eli, "ocr_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_ocr_stron", side_effect=lambda data, nr: {i: self.OCR for i in nr}) as ocr, \
                mock.patch.object(sys, "argv", ["eli.py"] + argv), contextlib.redirect_stdout(out):
            try:
                eli.main()
            except SystemExit as e:
                return out.getvalue(), str(e), ocr
        return out.getvalue(), "", ocr

    def test_krotki_skan_odczytany_i_oznaczony(self):
        out, err, ocr = self._tekst(["tekst", "DU", "1992", "413"])
        self.assertEqual(err, "")
        self.assertIn("UWAGA — OCR: 1 z 1 stron", out)
        self.assertIn("§ 2. Rozporządzenie wchodzi w życie z dniem ogłoszenia.", out)

    def test_strict_blokuje_tekst_z_ocr(self):
        out, err, _ = self._tekst(["--strict", "tekst", "DU", "1992", "413"])
        self.assertIn("BŁĄD (strict)", err)
        self.assertNotIn("ROZPORZĄDZENIE", out)

    def test_dlugi_skan_tylko_z_flaga_ocr(self):
        out, err, ocr = self._tekst(["tekst", "DU", "1992", "413"], stron=eli._OCR_AUTO_MAKS + 5)
        ocr.assert_not_called()
        self.assertIn("--ocr (ok.", err + out)
        out, err, ocr = self._tekst(["tekst", "DU", "1992", "413", "--ocr"], stron=eli._OCR_AUTO_MAKS + 5)
        ocr.assert_called_once()
        self.assertIn(f"UWAGA — OCR: {eli._OCR_AUTO_MAKS + 5} z {eli._OCR_AUTO_MAKS + 5} stron", out)

    def test_bez_ocr(self):
        _, err, ocr = self._tekst(["tekst", "DU", "1992", "413", "--bez-ocr"])
        ocr.assert_not_called()
        self.assertIn("nie zwrócił tekstu", err)

    def test_paragraf_zamiast_dolara(self):
        with mock.patch.object(eli.subprocess, "run", side_effect=[
                mock.Mock(), mock.Mock(stdout="1) $ 2 otrzymuje brzmienie: „$2. 1. Sąd”".encode())]), \
                mock.patch.object(eli, "_ocr_katalog", return_value="/nonexistent/x"), \
                mock.patch("builtins.open", side_effect=OSError), mock.patch.object(eli.os, "makedirs", side_effect=OSError):
            self.assertEqual(eli._ocr_strona("/tmp/x.pdf", 1, "k"), "1) § 2 otrzymuje brzmienie: „§2. 1. Sąd”")


class TestOcrParagrafOsiem(unittest.TestCase):
    """tesseract czyta „§" jako „8" (DU 1998 poz. 875: 20 z 21 paragrafów to „8 N."; DU 1992 poz. 316: „81."–„84.").
    Zamiana tylko na stronach z OCR i tylko, gdy potwierdza ją ciąg numeracji; poprawki po odczycie z pamięci."""

    STRONA_875 = ("8 1. 1. Ławników do sądów wojskowych wybiera\nsię spośród żołnierzy.\n\n"
                  "8 2. Prezesi właściwych sądów wojskowych, co naj-\nmniej na 3 miesiące.\n\n"
                  "8 3. Dowódca garnizonu ustala liczbę ławników\n\n"
                  "8 7. 1. Prawo zgłaszania kandydatów na ławników\no których mowa w 8 1 ust. 2.\n\n"
                  "8 8. Przed przystąpieniem do wyboru kandydat po-\n\n"
                  "8 11. Zebranie dokonuje wyboru ławników w dro-\n\n"
                  "przeprowadza się stosując zasady określone w 8 7—13.\n\n"
                  "8 12. Rozporządzenie wchodzi w życie po upływie\n")

    def popraw(self, strony, ocr_idx=None):
        return eli._ocr_popraw_paragrafy(strony, range(len(strony)) if ocr_idx is None else ocr_idx)

    def test_rozstrzelony_paragraf_w_ciagu_numeracji(self):
        s = self.popraw([eli._ocr_popraw_strone(self.STRONA_875)])[0]
        for n in (1, 2, 3, 7, 8, 11, 12):
            self.assertRegex(s, rf"(?m)^§ {n}\. ")
        self.assertIn("o których mowa w § 1 ust. 2.", s)
        self.assertIn("określone w § 7—13.", s)
        self.assertNotRegex(s, r"(?m)^8 ")
        self.assertEqual(self.popraw([s])[0], s)

    def test_fragment_paragrafu_z_tekstu_ocr(self):
        s = self.popraw([eli._ocr_popraw_strone(self.STRONA_875)])[0]
        self.assertTrue(eli._hity_paragrafu(s, "§ 11"))

    def test_sklejony_ciag_od_poczatku_aktu_do_paragrafu_na_nastepnej_stronie(self):
        # DU 1992 poz. 316: skan strony 1 (OCR), § 5 na stronie 2 z warstwą tekstową
        ocr = ("zarządza się,\nco następuje:\n\n81. Wspomaganie nauczania historii, geografii i języka\n\n"
               "1) kierowanie nauczycieli do pracy za granicą,\n\n82. 1. Do pracy za granicą w celu, o którym mowa\n\n"
               "3. Na czas skierowania nauczyciele otrzymują urlop.\n\n83. 1. O skierowanie do pracy za granicą mogą\n\n"
               "84. 1. Jednostka kierująca zawiera z nauczycielem\n")
        tekst = "    § 5. Nauczycielowi przysługuje dodatek.\n    § 6. Rozporządzenie wchodzi w życie."
        wynik = self.popraw([ocr, tekst], [0])
        for n in (1, 2, 3, 4):
            self.assertRegex(wynik[0], rf"(?m)^§{n}\. ")
        self.assertIn("3. Na czas skierowania", wynik[0])
        self.assertEqual(wynik[1], tekst)
        # bez § 5 za ciągiem (np. strona 2 niedostępna) — ciąg 81.–84. nie ma ramy z obu stron: bez zmian
        self.assertEqual(self.popraw([ocr], [0])[0], ocr)

    def test_sklejony_miedzy_rozstrzelonymi_i_idempotencja(self):
        # DU 1996 poz. 457: „8 4. 1.", „85. 1.", „8 6. 1."
        s = ("8 3. Pracownikowi zatrudnionemu w niepełnym\n\n8 4. 1. Ustala się:\n\n"
             "85. 1. Pracownikowi zatrudnionemu na stanowi-\n\n8 6. 1. Pracownikowi zatrudnionemu w zakładzie,\n")
        raz = self.popraw([s])
        self.assertEqual(raz[0], "§ 3. Pracownikowi zatrudnionemu w niepełnym\n\n§ 4. 1. Ustala się:\n\n"
                                 "§5. 1. Pracownikowi zatrudnionemu na stanowi-\n\n§ 6. 1. Pracownikowi zatrudnionemu w zakładzie,\n")
        self.assertEqual(self.popraw(raz), raz)

    def test_wyliczenie_81_89_w_paragrafie_bez_zmian(self):
        # punkty 80.–89. wykazu w § 2: przed nimi § 2, za nimi § 3 — nie są paragrafami
        s = ("§ 2. Wykaz obejmuje:\n" + "".join(f"8{k}. Wyrób numer {k}\n" for k in range(0, 10))
             + "90. Wyrób numer dziewięćdziesiąty\n§ 3. Rozporządzenie wchodzi w życie.\n")
        self.assertEqual(self.popraw([s])[0], s)
        # to samo bez żadnego pewnego nagłówka wokół
        s = "".join(f"8{k}. Pozycja wykazu {k}\n" for k in range(1, 6))
        self.assertEqual(self.popraw([s])[0], s)

    def test_punkt_osiem_i_wiersz_tabeli_bez_zmian(self):
        s = ("§ 3. Ustala się stawki:\n1. Pierwsza.\n8. Ósma pozycja wyliczenia.\n"
             "Lp. Nazwa Stawka\n1 2 3\n7 1. Owies 12,50\n8 1. Pszenica 15,00\n"
             "§ 4. Rozporządzenie wchodzi w życie.\n")
        self.assertEqual(self.popraw([s])[0], s)
        # wiersz tabeli z numerem sąsiedniego paragrafu: obok jest już pewny § z tym samym numerem
        for wiersz in ("8 3. Pszenica 15,00", "8 4. Pszenica 15,00"):
            s = f"§ 3. Ustala się stawki:\nLp. Nazwa Stawka\n{wiersz}\n§ 4. Rozporządzenie wchodzi w życie.\n"
            self.assertEqual(self.popraw([s])[0], s)

    def test_strona_z_warstwa_tekstowa_bez_zmian(self):
        s = "8 2. Prezesi sądów\n8 3. Dowódca garnizonu\n"
        self.assertEqual(self.popraw([s], [])[0], s)
        self.assertEqual(self.popraw([s, s], [1]), [s, eli._ocr_popraw_paragrafy([s], [0])[0]])

    def test_odeslania_w_srodku_wiersza(self):
        f = eli._ocr_popraw_strone
        self.assertEqual(f("o których mowa w 8 1 ust. 2."), "o których mowa w § 1 ust. 2.")
        self.assertEqual(f("głosowania określone w\n8 11—14."), "głosowania określone w\n§ 11—14.")
        self.assertEqual(f("zgodnie z 8 4 pkt 2"), "zgodnie z § 4 pkt 2")
        self.assertEqual(f("stosuje się do 8 3 ust. 1 i do 8 5—7"), "stosuje się do § 3 ust. 1 i do § 5—7")
        self.assertEqual(f("ustalonego zgodnie z 8 16."), "ustalonego zgodnie z § 16.")
        self.assertEqual(f("sił określonych w 8 14, a ponadto"), "sił określonych w § 14, a ponadto")
        self.assertEqual(f("z zastrzeżeniem\n8 14, wszystkie"), "z zastrzeżeniem\n§ 14, wszystkie")
        self.assertEqual(f("Na podstawie art. 23715 8 1 Kodeksu pracy"), "Na podstawie art. 23715 § 1 Kodeksu pracy")
        self.assertEqual(f("art. 228 8 1 Kodeksu pracy"), "art. 228 § 1 Kodeksu pracy")
        for bez_zmian in ("kara od 8 500 zł do 10 000 zł", "z 8 500 zł", "w 8 egzemplarzach", "w 8-10 dni",
                          "z dnia 8 1 marca 1998 r.", "do 8 1998 r.", "art. 8 ust. 1", "w 8 10 przypadkach",
                          "wynosi 8 1/2 godziny", "w 8 ust. 2", "w 8 12. 1998", "art. 8 1 ustawy",
                          "wyznacza się na niedzielę dnia 8 3. Rozporządzenie",
                          "od 7 00 do 8 00.", "od godz. 7 do 8 30, a w soboty", "na wysokości w 8 14,5 m",
                          "z 8 05, a następnie",
                          # odesłanie zawinięte na początek wiersza wyglądałoby jak nagłówek „§ 16."
                          "ustalonego zgodnie z\n8 16.\n"):
            self.assertEqual(f(bez_zmian), bez_zmian)
        s = f("w $ 3 i w 8 7—13, a także $2.")
        self.assertEqual(s, "w § 3 i w § 7—13, a także §2.")
        self.assertEqual(f(s), s)

    def test_poprawki_takze_dla_wpisu_z_pamieci_podrecznej(self):
        # pamięć trzyma surowy wynik tesseracta; poprawki — po odczycie z niej, bez wywołania tesseracta
        import tempfile
        pdf = b"%PDF-1.3 skan"
        klucz = eli.hashlib.sha256(pdf).hexdigest()[:16]
        surowy = "zarządza się, co następuje:\n\n$ 1. Ilekroć w rozporządzeniu\n\n8 2. Dowódca garnizonu\n\n" \
                 "8 3. Rozporządzenie wchodzi w życie z dniem ogłoszenia, o którym mowa w 8 2 ust. 1.\n"
        meta = {"ELI": "DU/1998/875", "year": 1998, "pos": None, "publisher": "DU", "textHTML": False,
                "texts": [{"fileName": "D19980875.pdf", "type": "T"}]}
        with tempfile.TemporaryDirectory() as tmp:
            with open(eli.os.path.join(tmp, f"{klucz}-1-{eli._OCR_DPI}.txt"), "w", encoding="utf-8") as f:
                f.write(surowy)
            with mock.patch.object(eli, "_ocr_katalog", return_value=tmp), \
                    mock.patch.object(eli.subprocess, "run", side_effect=AssertionError("tesseract nie powinien ruszyć")), \
                    mock.patch.object(eli, "_get_bytes", return_value=pdf), \
                    mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                    mock.patch.object(eli, "ocr_dostepny", return_value=True), \
                    mock.patch.object(eli, "pdf_do_tekstu_z_notkami", return_value=("\f", {}, {})):
                self.assertIn("§ 1. Ilekroć", eli.pdf_ocr_stron(pdf, [0])[0])
                info = {}
                txt, _, err = eli._tekst_z_pdf("/acts/DU/1998/875", "DU 1998 poz. 875", meta, info)
            with open(eli.os.path.join(tmp, f"{klucz}-1-{eli._OCR_DPI}.txt"), encoding="utf-8") as f:
                self.assertEqual(f.read(), surowy)       # wpis w pamięci bez zmian
        self.assertEqual(err, "")
        self.assertEqual(info.get("ocr_strony"), (1, 1))
        for n in (1, 2, 3):
            self.assertRegex(txt, rf"(?m)^§ {n}\. ")
        self.assertIn("o którym mowa w § 2 ust. 1.", txt)


class TestAudyt2026Cache(unittest.TestCase):
    def test_get_bez_parametrow_jest_cache_owany_a_z_parametrami_nie(self):
        eli._CACHE.clear()
        wywolania = []
        def fake(url, soft):
            wywolania.append(url)
            return {"ok": len(wywolania)}
        with mock.patch.object(eli, "_pobierz", side_effect=fake):
            self.assertEqual(eli._get("/acts/DU/2024/18"), {"ok": 1})
            self.assertEqual(eli._get("/acts/DU/2024/18"), {"ok": 1})
            eli._get("/acts/search", {"title": "x"})
            eli._get("/acts/search", {"title": "x"})
        self.assertEqual(len(wywolania), 3)
        eli._CACHE.clear()


# --- Próba losowa 2026-10 (fix 2.1.1): fixture z prawdziwych danych ---
# DU 2010 poz. 1128, s. 1 (`pdftotext -layout`): przypisy 1–3 na dole LEWEGO łamu, bez kreski w warstwie tekstowej
PDF_ZESZYT_2010_S1 = "\n".join((
    'Dziennik Ustaw Nr 166                                   — 12378 —                                             Poz. 1128',
    '',
    '',
    '                                                            1128',
    '                  ROZPORZĄDZENIE MINISTRA SPRAW WEWNĘTRZNYCH I ADMINISTRACJI1)',
    '',
    '                                                 z dnia 7 września 2010 r.',
    '                                                                                            l',
    '                                            .go',
    '     w sprawie wymagań, jakim powinna odpowiadać ochrona wartości pieniężnych przechowywanych',
    '                i transportowanych przez przedsiębiorców i inne jednostki organizacyjne',
    '',
    '    Na podstawie art. 6 ust. 2 ustawy z dnia 22 sierp-              pospolitej Polskiej „Monitor Polski”, na podstawie',
    'nia 1997 r. o ochronie osób i mienia (Dz. U. z 2005 r.              art. 20 pkt 2 ustawy z dnia 17 grudnia 1998 r.',
    'Nr 145, poz. 1221, z późn. zm.2)) zarządza się, co nastę-           o emeryturach i rentach z Funduszu Ubezpieczeń',
    'puje:                                                               Społecznych (Dz. U. z 2009 r. Nr 153, poz. 1227',
    '',
    '',
    '                                               v.p',
    '                                                                    oraz z 2010 r. Nr 40, poz. 224 i Nr 134, poz. 903);',
    '    § 1. Użyte w rozporządzeniu określenia oznaczają:',
    '                                                                  6) klasa odporności na włamanie — przyjęte we właś-',
    ' 1) ustawa — ustawę z dnia 22 sierpnia 1997 r.                       ciwej Polskiej Normie klasyfikacyjne oznaczenie',
    '    o ochronie osób i mienia;                                        odporności pomieszczeń lub urządzeń na włama-',
    '                                                                     nie;',
    ' 2) wartości pieniężne:',
    '    a) krajowe i zagraniczne znaki pieniężne,                     7) posterunki doraźne — posterunki wystawiane',
    '                                                                     w miejscu, które wymaga ochrony natychmiasto-',
    '    b) czeki, z wyjątkiem czeków zakreślonych, skaso-                wej i tymczasowej;',
    '       wanych lub opatrzonych indosem pełnomocni-',
    '       czym, zawierającym wzmiankę „wartość do in-                8) konwojent — osobę posiadającą licencję pracow-',
    '       kasa”, „należność do inkasa” lub inną o podob-                nika ochrony fizycznej, ochraniającą transport',
    '       nym charakterze,                                              wartości pieniężnych, wyposażoną w broń palną',
    '    c) weksle, z wyjątkiem weksli opatrzonych indo-                  bojową, środki ochrony osobistej oraz środki łącz-',
    '       sem pełnomocniczym zawierającym wzmiankę                      ności;',
    '       „wartość do inkasa” lub inną o podobnym cha-',
    '       rakterze,                                                  9) środki łączności wewnętrznej — środki służące do',
    '                                                                     nawiązywania łączności w obrębie chronionego',
    '    d) inne dokumenty zastępujące w obrocie gotów-                   obiektu lub między członkami konwoju;',
    '',
    '',
    '     w.',
    '       kę,',
    '    e) złoto, srebro i wyroby z tych metali, kamienie            10) środki łączności zewnętrznej — środki służące do',
    '       szlachetne i perły, a także platynę i inne metale             nawiązywania łączności z podmiotami zewnętrz-',
    '       z grupy platynowców, z wyjątkiem przedmiotów                  nymi;',
    '       będących muzealiami w rozumieniu przepisów',
    '',
    '',
    '       rcl',
    '       ustawy z dnia 21 listopada 1996 r. o muzeach              11) środki ochrony osobistej — wyposażenie kulo-',
    '       (Dz. U. z 1997 r. Nr 5, poz. 24, z późn. zm.3));              odporne składające się co najmniej z kamizelki',
    '                                                                     i hełmu;',
    ' 3) transport wartości pieniężnych — przewożenie lub',
    '    przenoszenie wartości pieniężnych poza obiektami             12) pojemnik specjalistyczny — pojemnik służący do',
    '    przedsiębiorcy lub innej jednostki organizacyjnej;               transportu wartości pieniężnych, wyposażony',
    '                                                                     w urządzenia utrudniające jego zabór, wykorzystu-',
    ' 4) konwojowany transport wartości pieniężnych —                     jące działanie paralizatora elektrycznego, alarmu',
    '    przewożenie lub przenoszenie wartości pienięż-                   akustycznego, sygnału dymnego lub uszkadzające',
    '    nych ochranianych przez konwojentów poza obiek-                  zawartość pojemnika z zachowaniem możliwości',
    '    tami przedsiębiorcy lub innej jednostki organiza-                identyfikacji papierowych wartości pieniężnych;',
    '    cyjnej;',
    '                                                                 13) pojemnik bezpieczny — pojemnik wykonany ze',
    ' 5) jednostka obliczeniowa — 120-krotność przecięt-                  stopu metali używany w celu ochrony zawartości',
    '    nego wynagrodzenia w poprzednim kwartale,                        przed włamaniem;',
    '    ogłaszanego przez Prezesa Głównego Urzędu Sta-',
    '                                                                 14) osoba transportująca — osobę przenoszącą lub',
    '',
    '',
    'ww',
    '    tystycznego w Dzienniku Urzędowym Rzeczy-',
    '                                                                     przewożącą wartości pieniężne;',
    '1) Minister Spraw Wewnętrznych i Administracji kieruje dzia-',
    '                                                                 15) bankowozy — pojazdy samochodowe przeznaczo-',
    '   łem administracji rządowej — sprawy wewnętrzne, na                ne do transportu wartości pieniężnych, oznaczone',
    '   podstawie § 1 ust. 2 pkt 3 rozporządzenia Prezesa Rady            w zależności od konstrukcji i zabezpieczenia tech-',
    '   Ministrów z dnia 16 listopada 2007 r. w sprawie szczegóło-        nicznego jako typy: A, B i C;',
    '   wego zakresu działania Ministra Spraw Wewnętrznych',
    '   i Administracji (Dz. U. Nr 216, poz. 1604).                   16) pojazdy ubezpieczające — pojazdy samochodowe',
    '2) Zmiany tekstu jednolitego wymienionej ustawy zostały',
    '   ogłoszone w Dz. U. z 2006 r. Nr 104, poz. 708, z 2008 r.',
    '                                                                     przeznaczone do przewożenia konwojentów ochra-',
    '   Nr 171, poz. 1055 i Nr 180, poz. 1112, z 2009 r. Nr 98,           niających pojazdy samochodowe przewożące war-',
    '   poz. 817 oraz z 2010 r. Nr 47, poz. 278.                          tości pieniężne;',
    '3) Zmiany wymienionej ustawy zostały ogłoszone w Dz. U.',
    '   z 1998 r. Nr 106, poz. 668, z 2002 r. Nr 113, poz. 984,       17) pomieszczenie — wydzieloną część budynku',
    '   z 2003 r. Nr 162, poz. 1568, z 2005 r. Nr 64, poz. 565 oraz       o określonej klasie odporności na włamanie, słu-',
    '   z 2007 r. Nr 136, poz. 956.                                       żącą do przechowywania wartości pieniężnych;',
)) + "\n"
# DU 2010 poz. 1128: koniec zał. 6 (s. 13), strona-reklama wydawcy (s. 14, OCR tesseract), stopka z ISSN (s. 15)
PDF_1128_KONIEC = "\n".join((
    '    2. Urządzenia elektroniczne i elektryczne zamonto-     PN-S 76021 (rodzaje i zakres badań według punk-',
    'wane w pojeździe powinny spełniać, w zakresie kom-         tu 4.1 .b tablica 2 kolumna 4 tej normy).',
    'patybilności elektromagnetycznej, wymagania dyrek-',
    'tywy Rady nr 72/245/EWG z dnia 20 czerwca 1972 r.              4. Koła pojazdu powinny być wykonane z wkładka-',
    'w sprawie zbliżenia ustawodawstw Państw Członkow-          mi masywowymi lub w innej konstrukcji umożliwiają-',
    'skich odnoszących się do tłumienia zakłóceń radio-         cej po przebiciu opony dalszą jazdę przez 15 km',
    'elektrycznych wywoływanych przez silniki z zapłonem        z prędkością 50 (+/–5) km/godz.',
    '',
    '',
    '',
    '',
    '                                           v.p',
    'iskrowym stosowane w pojazdach silnikowych',
    '(Dz. Urz. UE Polskie wydanie specjalne, rozdz. 13, t. 1,       5. W pojazdach ubezpieczających nie dopuszcza',
    'str. 226) lub regulaminu nr 10 Europejskiej Komisji        się stosowania instalacji gazowej.',
    '',
    '',
    '',
    '',
    '    w.rcl',
    'ww',
    '',
))
OCR_1128_REKLAMA = "\n".join((
    '— 12391 —',
    '',
    'CENTRUM OBSŁUGI KANCELARII PREZESA RADY MINISTRÓW',
    'WYDZIAŁ WYDAWNICTW I POLIGRAFII',
    '',
    'proponuje',
    '',
    'ZAŁĄCZNIK DO REGULAMINU MIĘDZYNARODOWEGO',
    'PRZEWOZU KOLEJAMI TOWARÓW NIEBEZPIECZNYCH (RID),',
    'STANOWIĄCEGO ZAŁĄCZNIK C€ DO KONWENCJI',
    'O MIĘDZYNARODOWYM PRZEWOZIE KOLEJAMI (COTIF)',
    '',
    'DZIENNIK USTAW',
    '',
    'Załącznik do Dziennika Ustaw Nr 167, poz. 1318',
    'z dnia 8 października 2009 r.',
    '',
    'kierowany do wszystkich uczestniczących',
    'w przewozie kolejami towarów niebezpiecznych',
    '',
    'określa:',
    '% towary niebezpieczne, które nie są dopuszczone do przewozu międzynarodowego,',
    '',
    '% towary niebezpieczne, które są dopuszczone do przewozu międzynarodowego',
    'oraz przypisane do nich warunki dotyczące w szczególności:',
    '',
    '+ klasyfikacji towarów,',
    '',
    '+ używania opakowań,',
    '',
    '+ używania cystern,',
    '',
    '+ procedur wysyłkowych,',
    '',
    '* przepisów z zakresu konstrukcji, badania i dopuszczania opakowań i cystern,',
    '',
    '+ używania jednostek transportowych.',
    '',
    'Cena brutto I tomu — 105,00 zł (w tym 7 % VAT)',
    'WERSJA POLSKA',
    '',
    'Cena brutto II tomu — 113,40 zł (wtym 7 % vAT)',
    'WERSJA ANGIELSKA',
    '',
    'Dodatkowe informacje: tel. 22 694-67-52, 800 287 581',
    'Adres e-mailowy: wydawnictwa © cokprm.gov.pl',
    'Faks 22 694-60-48',
    '',
    'Pełna oferta: www.wydawnictwa.cokprm.gov.pl',
    '',
    '',
))
PDF_1128_STOPKA = "\n".join((
    '                                                               — 12392 —',
    '',
    '',
    '',
    '',
    '                                                                                                            l',
    '                                                 .go',
    '                                                    v.p',
    '     w.rcl',
    'ww                                 Wydawca: Kancelaria Prezesa Rady Ministrów',
    '          Redakcja: Rządowe Centrum Legislacji — Departament Dziennika Ustaw i Monitora Polskiego',
    '                             al. J. Ch. Szucha 2/4, 00-582 Warszawa, tel. 22 622-66-56',
    ' Skład, druk i kolportaż: Centrum Obsługi Kancelarii Prezesa Rady Ministrów — Wydział Wydawnictw i Poligraﬁi,',
    '                    ul. Powsińska 69/71, 02-903 Warszawa, tel. 22 694-67-52; faks 22 694-60-48',
    '                             Bezpłatna infolinia: 800 287 581 (czynna w godz. 730–1530)',
    '                                          www.wydawnictwa.cokprm.gov.pl',
    '                                        e-mail: wydawnictwa@cokprm.gov.pl',
    '',
    '     Tłoczono z polecenia Prezesa Rady Ministrów w Centrum Obsługi Kancelarii Prezesa Rady Ministrów — Wydział Wydawnictw i Poligraﬁi,',
    '                                                    ul. Powsińska 69/71, 02-903 Warszawa',
    '',
    'Zam. 2489/W/C/2010                                          ISSN 0867-3411                          Cena 2,80 zł (w tym 7 % VAT)',
    '',
))
# DU 2026 poz. 1245 (k.p., PDF T) po `pdf_layout_do_tekstu`: obwieszczenie Marszałka Sejmu (skrócone po pkt 2.2)
TJ_KP_OBWIESZCZENIE = "\n".join((
    '[obwieszczenie Marszałka Sejmu sprzed załącznika — wiersze ze znakiem » NIE są treścią aktu]',
    '',
    '» Dz. U. 2026 poz. 1245',
    '',
    '» OBWIESZCZENIE',
    '',
    '» MARSZAŁKA SEJMU RZECZYPOSPOLITEJ POLSKIEJ',
    '',
    '» z dnia 1 września 2026 r.',
    '',
    '» w sprawie ogłoszenia jednolitego tekstu ustawy – Kodeks pracy',
    '» 1. Na podstawie art. 16 ust. 1 ustawy z dnia 20 lipca 2000 r. o ogłaszaniu aktów normatywnych i niektórych innych aktów prawnych (Dz. U. z 2019 r. poz. 1461 oraz z 2026 r. poz. 912) ogłasza się w załączniku do niniejszego obwieszczenia jednolity tekst ustawy z dnia 26 czerwca 1974 r. – Kodeks pracy (Dz. U. z 2025 r. poz. 277), z uwzględnieniem stanu prawnego na dzień 27 sierpnia 2026 r. oraz zmian wprowadzonych:',
    '» 1) ustawą z dnia 4 czerwca 2025 r. o zmianie ustawy – Kodeks pracy (Dz. U. poz. 807);',
    '» 2) ustawą z dnia 26 września 2025 r. o zmianie ustawy – Kodeks pracy oraz niektórych innych ustaw (Dz. U. poz. 1423);',
    '» 3) ustawą z dnia 5 listopada 2025 r. o układach zbiorowych pracy i porozumieniach zbiorowych (Dz. U. poz. 1661);',
    '» 4) ustawą z dnia 4 grudnia 2025 r. o zmianie ustawy – Kodeks pracy oraz ustawy o zakładowym funduszu świadczeń socjalnych (Dz. U. z 2026 r. poz. 25);',
    '» 5) ustawą z dnia 11 marca 2026 r. o zmianie ustawy o Państwowej Inspekcji Pracy oraz niektórych innych ustaw (Dz. U. poz. 473);',
    '» 6) ustawą z dnia 19 czerwca 2026 r. o zmianie ustawy – Kodeks pracy oraz ustawy – Kodeks postępowania cywilnego (Dz. U. poz. 1046).',
    '» 2. Podany w załączniku do niniejszego obwieszczenia tekst jednolity ustawy nie obejmuje:',
    '» 1) art. 2 ustawy z dnia 4 czerwca 2025 r. o zmianie ustawy – Kodeks pracy (Dz. U. poz. 807), który stanowi:',
    '» „Art. 2. Ustawa wchodzi w życie po upływie 6 miesięcy od dnia ogłoszenia.”;',
    '» 2) art. 5–10 ustawy z dnia 26 września 2025 r. o zmianie ustawy – Kodeks pracy oraz niektórych innych ustaw (Dz. U. poz. 1423), które stanowią:',
    'Załącznik do obwieszczenia Marszałka Sejmu Rzeczypospolitej',
))

# DU 2017 poz. 1692 (text.html → tekst, skrócone): paragrafy w rozporządzeniu bez artykułów
HTML_TXT_1692 = ("Na podstawie art. 237 15 § 1 ustawy z dnia 26 czerwca 1974 r. – Kodeks pracy zarządza się, co następuje:\n\n"
                 "§ 3.\n\nW pomieszczeniach inwentarskich:\n\n3)\n\nzapewnia się oświetlenie umożliwiające bezpieczne "
                 "wykonywanie pracy przy obsłudze\nzwierząt;\n\n"
                 "§ 4.\n\n1.\n\nW nowo budowanych lub przebudowywanych pomieszczeniach inwentarskich stosuje się urządzenia\n"
                 "techniczne ułatwiające pracę przy dowozie i dozowaniu pasz, usuwaniu odchodów zwierzęcych\noraz pojeniu zwierząt.\n\n"
                 "2.\n\nW pomieszczeniach, o których mowa w ust. 1, niedopuszczalne jest umieszczanie progów\n"
                 "w otworach drzwiowych, na ciągach komunikacyjnych i w przejściach.\n\n"
                 "§ 5.\n\nW pomieszczeniach inwentarskich przeznaczonych dla świń stosuje się kojce skonstruowane\n"
                 "w sposób umożliwiający:\n\n")
# DU 2024 poz. 1600 (text.html → tekst, skrócone): rozporządzenie zmieniające cytuje „§ 7." w cudzysłowie
HTML_TXT_1600 = ("§ 1.\n\nW rozporządzeniu Ministra Zdrowia z dnia 11 września 2006 r. w sprawie środków odurzających\n"
                 "(Dz. U. z 2024 r. poz. 373) wprowadza się następujące zmiany:\n\n1)\n\n§ 7 otrzymuje brzmienie:\n\n„\n\n"
                 "§ 7.\n\nRecepta na preparat zawierający środek odurzający jest wystawiana po uprzednim zbadaniu.\n\n”\n\n;\n\n"
                 "§ 2.\n\nRozporządzenie wchodzi w życie po upływie 7 dni od dnia ogłoszenia.\n")
# DU 2023 poz. 2809 (k.r.o., text.html → tekst): paragrafy w artykułach
HTML_TXT_KRO = ("Art. 24.\n\nMałżonkowie rozstrzygają wspólnie o istotnych sprawach rodziny.\n\n"
                "Art. 25.\n\n§ 1.\n\nO nazwisku, które każdy z małżonków będzie nosił po zawarciu małżeństwa, decyduje\n"
                "jego oświadczenie.\n\n§ 2.\n\nMałżonkowie mogą nosić wspólne nazwisko będące dotychczasowym nazwiskiem jednego z\nnich.\n\n"
                "§ 3.\n\nNazwisko utworzone w wyniku połączenia\nnie może składać się z więcej niż dwóch członów.\n\n"
                "Art. 26.\n\n§ 1.\n\nNazwisko dziecka określa się w oświadczeniach małżonków.\n\n§ 2.\n\nDziecko nosi nazwisko ojca.\n\n")
# DU 2026 poz. 1245 (k.p., PDF T): dwa brzmienia art. 94³; data wejścia w życie jest tylko w przypisie 6 (art. 18³a)
TJ_KP_PRZYPISY = ("Art. 18 3a. § 1. Pracownicy powinni być równo traktowani.\n"
                  "[przypis 5)] W tym brzmieniu obowiązuje do wejścia w życie zmiany, o której mowa w odnośniku 6.\n"
                  "§ 2. Równe traktowanie w zatrudnieniu oznacza niedyskryminowanie.\n"
                  "[przypis 6)] W brzmieniu ustalonym przez art. 1 pkt 1 lit. a ustawy z dnia 19 czerwca 2026 r. o zmianie ustawy – "
                  "Kodeks pracy oraz ustawy – Kodeks postępowania cywilnego (Dz. U. poz. 1046), która wejdzie w życie z dniem 5 "
                  "listopada 2026 r.\n\n"
                  "Art. 94 3. § 1. Pracodawca jest obowiązany przeciwdziałać mobbingowi.\n"
                  "[przypis 28)] W tym brzmieniu obowiązuje do wejścia w życie zmiany, o której mowa w odnośniku 29.\n"
                  "§ 2. Mobbing oznacza działania lub zachowania dotyczące pracownika.\n\n"
                  "Art. 94 3. § 1. Pracodawca jest obowiązany systematycznie przeciwdziałać mobbingowi.\n"
                  "[przypis 29)] W brzmieniu ustalonym przez art. 1 pkt 5 ustawy, o której mowa w odnośniku 6.\n"
                  "§ 2. Mobbing oznacza zachowania polegające na uporczywym nękaniu pracownika.\n\n"
                  "Art. 94 4. Pracodawca jest obowiązany zapewniać bezpieczne i higieniczne warunki pracy.\n")
# DU 2023 poz. 2809 (k.r.o., text.html → tekst): obwieszczenie t.j. z cytatami bez roku i formułą „ogłoszonych przed dniem"
HTML_TXT_KRO_OBWIESZCZENIE = (
    "Treść obwieszczenia\n\n1.\n\nNa podstawie art. 16 ust. 1 zdanie pierwsze ustawy z dnia 20 lipca 2000 r. o ogłaszaniu aktów "
    "normatywnych\ni niektórych innych aktów prawnych\n(Dz. U. z 2019 r. poz. 1461) ogłasza się w załączniku do niniejszego "
    "obwieszczenia jednolity tekst ustawy z dnia 25 lutego 1964 r. - Kodeks rodzinny i opiekuńczy\n(Dz. U. z 2020 r. poz. 1359), "
    "z uwzględnieniem zmian wprowadzonych:\n\n1)\n\nustawą z dnia 7 października 2022 r. o zmianie ustawy o wspieraniu rodziny "
    "i systemie\npieczy zastępczej oraz niektórych innych ustaw\n(Dz. U. poz. 2140 i 2243),\n\n2)\n\nustawą z dnia 7 lipca 2023 r. "
    "o ogólnoeuropejskim indywidualnym produkcie emerytalnym\n(Dz. U. poz. 1843),\n\n"
    "oraz zmian wynikających z przepisów ogłoszonych przed dniem 6 grudnia 2023 r.\n\n2.\n\nPodany w załączniku do niniejszego "
    "obwieszczenia tekst jednolity ustawy nie obejmuje:\n\n1)\n\nart. 36 i art. 45 ustawy z dnia 7 października 2022 r. o zmianie "
    "ustawy o wspieraniu\nrodziny i systemie pieczy zastępczej oraz niektórych innych ustaw\n(Dz. U. poz. 2140 i 2243), które "
    "stanowią:\n\n„\n\nArt. 36.\n\nDo spraw w przedmiocie określenia okresu osobistej styczności stosuje się przepisy dotychczasowe.\n\n”\n\n")


class TestProbaLosowa202610(unittest.TestCase):
    """Pomiar na losowej próbie (2026-10, fix 2.1.1): § jako jednostka fragmentu, reklama wydawcy w zeszycie, obwieszczenie
    t.j. i łańcuchy przypisów, indeks górny w zeszycie, przypisy łamu bez kreski, HTML <h1>/gloss-section/„Pokaż całość",
    powtórzony dywiz, --strict przy stronach bez warstwy tekstowej."""

    # 1. --fragment "§ N"
    def test_paragraf_ciety_na_granicach_paragrafow(self):
        [(s, e)] = eli._fragmenty(HTML_TXT_1692, "§ 4")
        frag = HTML_TXT_1692[s:e]
        self.assertTrue(frag.startswith("§ 4.\n"), frag[:30])
        self.assertIn("w otworach drzwiowych", frag)
        self.assertNotIn("oświetlenie", frag)     # koniec § 3 (dawniej start „ię oświetlenie…")
        self.assertNotIn("§ 5.", frag)

    def test_paragraf_z_indeksem_gornym(self):
        txt = "§ 2.\n\nTreść drugiego.\n\n§ 2 1.\n\nTreść paragrafu 2¹.\n\n§ 21.\n\nTreść dwudziestego pierwszego.\n"
        for fraza in ("§ 2¹", "§ 2(1)", "§2[1]"):
            with self.subTest(fraza=fraza):
                [(s, e)] = eli._fragmenty(txt, fraza)
                self.assertEqual(txt[s:e].strip(), "§ 2 1.\n\nTreść paragrafu 2¹.")
        [(s, e)] = eli._fragmenty(txt, "§ 2")
        self.assertEqual(txt[s:e].strip(), "§ 2.\n\nTreść drugiego.")

    def test_paragraf_cytowany_w_akcie_zmieniajacym_nie_jest_granica(self):
        [(s, e)] = eli._fragmenty(HTML_TXT_1600, "§ 1")
        frag = HTML_TXT_1600[s:e]
        self.assertIn("Recepta na preparat", frag)          # cytowany „§ 7." nie przerywa § 1
        self.assertNotIn("wchodzi w życie po upływie 7 dni", frag)
        [(s, e)] = eli._fragmenty(HTML_TXT_1600, "§ 2")
        self.assertIn("wchodzi w życie po upływie 7 dni", HTML_TXT_1600[s:e])

    def test_paragraf_w_artykule_niejednoznaczny_i_z_artykulem(self):
        self.assertEqual(len(eli._hity_paragrafu(HTML_TXT_KRO, "§ 1")), 2)
        spans = eli._fragmenty(HTML_TXT_KRO, "§ 1")
        self.assertEqual([eli._artykul_nad(HTML_TXT_KRO, s) for s, _ in spans], ["Art. 25.", "Art. 26."])
        [(s, e)] = eli._fragmenty(HTML_TXT_KRO, "art. 25 § 2")
        self.assertEqual(HTML_TXT_KRO[s:e].strip(), "§ 2.\n\nMałżonkowie mogą nosić wspólne nazwisko będące dotychczasowym "
                                                     "nazwiskiem jednego z\nnich.")
        # tekst z PDF: „Art. 25. § 1. …" w jednym wierszu — § 1 trafia w wiersz artykułu
        pdf = "Art. 25. § 1. O nazwisku decyduje oświadczenie.\n§ 2. Wspólne nazwisko.\nArt. 26. § 1. Nazwisko dziecka.\n"
        [(s, e)] = eli._fragmenty(pdf, "art. 25 § 1")
        self.assertEqual(pdf[s:e], "Art. 25. § 1. O nazwisku decyduje oświadczenie.\n")

    def test_paragraf_wypis_z_artykulem_i_ostrzezeniem(self):
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=lambda p, params=None, soft=False:
                               {} if p.endswith("/references") else "<p>x</p>" if p.endswith("text.html") else {}), \
                mock.patch.object(eli, "html_to_text", return_value=HTML_TXT_KRO), \
                mock.patch.object(sys, "argv", ["eli.py", "tekst", "DU", "2023", "2809", "--fragment", "§ 1"]), \
                contextlib.redirect_stdout(out):
            eli.main()
        out = out.getvalue()
        self.assertIn("NIEJEDNOZNACZNE", out)
        self.assertIn("[§ 1 — w: Art. 25.]\n§ 1.", out)
        self.assertIn("[§ 1 — w: Art. 26.]\n§ 1.", out)

    # 2. reklama wydawcy w zeszycie
    def test_strona_reklamowa_z_ocr_i_z_warstwy_tekstowej(self):
        self.assertTrue(eli._pdf_strona_reklamowa(OCR_1128_REKLAMA))
        self.assertFalse(eli._pdf_strona_reklamowa(PDF_1128_KONIEC))
        self.assertFalse(eli._pdf_strona_reklamowa(PDF_1128_STOPKA))     # stopka: „Centrum Obsługi…" zwykłą pisownią
        raw = PDF_1128_KONIEC + "\f" + OCR_1128_REKLAMA + "\f" + PDF_1128_STOPKA + "\f"
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(raw, 2010, None))
        self.assertTrue(t.endswith("5. W pojazdach ubezpieczających nie dopuszcza się stosowania instalacji gazowej."), t[-200:])
        for smiec in ("CENTRUM", "RID", "Pełna oferta", "Cena brutto", "ISSN", "ww"):
            self.assertNotIn(smiec, t)
        self.assertIn("50 (+/–5) km/godz.", t)       # „–5)" to liczba w nawiasie, nie odsyłacz do przypisu 5

    def test_ocr_strony_reklamowej_nie_jest_tekstem_aktu(self):
        meta = {"ELI": "DU/2010/1128", "year": 2010, "pos": None, "publisher": "DU", "textHTML": False,
                "texts": [{"fileName": "D20101128.pdf", "type": "O"}]}
        raw = PDF_1128_KONIEC + "\f\f" + PDF_1128_STOPKA + "\f"
        with mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_z_notkami", return_value=(raw, {}, {})), \
                mock.patch.object(eli, "ocr_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_ocr_stron", return_value={1: OCR_1128_REKLAMA}):
            info = {}
            t, _, err = eli._tekst_z_pdf("/acts/DU/2010/1128", "DU 2010 poz. 1128", meta, info)
        self.assertEqual(err, "")
        self.assertNotIn("ocr_strony", info)      # jedyna strona z OCR to reklama — tekst aktu nie pochodzi z OCR
        self.assertEqual(info.get("reklama_strony"), 1)
        self.assertNotIn("CENTRUM", t)

    # 3. obwieszczenie t.j. i przypisy odsyłające do innych przypisów
    def test_obwieszczenie_pdf_t_zmiany_uwzglednione(self):
        uwzgl, nieobj, przed = eli._obwieszczenie_tj(TJ_KP_OBWIESZCZENIE)
        self.assertEqual(uwzgl, {(2025, 807), (2025, 1423), (2025, 1661), (2026, 25), (2026, 473), (2026, 1046)})
        self.assertEqual((nieobj, przed), (set(), ""))
        linie = ["UWAGA: po tym tekście jednolitym (stan prawny na 2026-08-27) odnotowano zmiany (2) — sprawdź:",
                 "  - Dz.U. 2026 poz. 1046  Ustawa z dnia 19 czerwca 2026 r. o zmianie ustawy - Kodeks pracy  (data aktu "
                 "2026-06-19, ogłoszono 2026-08-04, wejście w życie zmiany 2026-11-05)",
                 "  - Dz.U. 2026 poz. 1300  Ustawa późniejsza  (data aktu 2026-09-10, ogłoszono 2026-09-20, wejście w życie zmiany 2027-01-01)"]
        out = eli._oznacz_obwieszczenie(linie, uwzgl, nieobj, przed, dzis="2026-10-05")
        self.assertIn("[UWZGLĘDNIONA w tym t.j.", out[1])
        self.assertIn("wchodzi w życie 2026-11-05 — do tego dnia obowiązuje dotychczasowe brzmienie", out[1])
        self.assertNotIn("[", out[2].split(")", 2)[-1])           # nie z obwieszczenia — bez oznaczenia
        self.assertIn("wg obwieszczenia Marszałka Sejmu", out[0])

    def test_obwieszczenie_html_rok_z_daty_aktu_i_formula_ogloszonych_przed(self):
        uwzgl, nieobj, przed = eli._obwieszczenie_tj(HTML_TXT_KRO_OBWIESZCZENIE)
        self.assertEqual(uwzgl, {(2022, 2140), (2022, 2243), (2023, 1843)})
        self.assertEqual((nieobj, przed), (set(), "2023-12-06"))    # pkt 2 wymienia przepisy, nie zmiany
        out = eli._oznacz_obwieszczenie(["  - Dz.U. 2023 poz. 2600  Ustawa  (ogłoszono 2023-11-30, wejście w życie zmiany 2024-01-01)"],
                                        uwzgl, nieobj, przed, dzis="2026-10-05")
        self.assertIn("UWZGLĘDNIONA", out[0])
        self.assertEqual(eli._obwieszczenie_tj("Art. 1. Treść aktu bez obwieszczenia."), (set(), set(), ""))

    def test_obwieszczenie_zmiany_nieobjete(self):
        txt = ("1. Na podstawie art. 16 ust. 1 ustawy ogłasza się w załączniku do niniejszego obwieszczenia jednolity tekst ustawy "
               "z dnia 6 czerwca 1997 r. (Dz. U. z 2024 r. poz. 17), z uwzględnieniem zmian wprowadzonych ustawą z dnia "
               "5 marca 2025 r. (Dz. U. poz. 300).\n2. Podany w załączniku tekst jednolity ustawy nie obejmuje:\n"
               "1) zmian wprowadzonych ustawą z dnia 9 maja 2025 r. (Dz. U. poz. 700), które wejdą w życie z dniem 1 stycznia 2027 r.;\n"
               "2) art. 5 ustawy z dnia 5 marca 2025 r. (Dz. U. poz. 300), który stanowi: …\n")
        uwzgl, nieobj, _ = eli._obwieszczenie_tj(txt)
        self.assertEqual((uwzgl, nieobj), ({(2025, 300)}, {(2025, 700)}))
        out = eli._oznacz_obwieszczenie(["  - Dz.U. 2025 poz. 700  Ustawa  (ogłoszono 2025-05-20)"], uwzgl, nieobj)
        self.assertIn("[NIE objęta tym t.j.", out[0])
        # bez pkt 2: lista kończy się przed załącznikiem — cytaty z treści aktu nie są „uwzględnionymi zmianami"
        bez_pkt2 = txt.split("\n2. ")[0] + "\n» Marszałek Sejmu: W. Czarzasty\nZałącznik do obwieszczenia\n" \
            "Art. 1. Przepisy ustawy z dnia 1 lipca 2020 r. (Dz. U. z 2020 r. poz. 5) stosuje się odpowiednio.\n"
        self.assertEqual(eli._obwieszczenie_tj(bez_pkt2)[0], {(2025, 300)})

    def test_przypis_odsylajacy_do_innego_przypisu_daje_date(self):
        txt = TJ_KP_PRZYPISY
        spans = eli._fragmenty(txt, "art. 94(3)")
        self.assertEqual(len(spans), 2)
        frag = "\n".join(txt[s:e] for s, e in spans)
        dociagniete, analiza, ostrz = eli._przypisy_fragmentu(frag, txt, dzis="2026-10-05")
        self.assertEqual([l[:14] for l in dociagniete], ["[przypis 6)] W"])
        self.assertEqual(len(ostrz), 1)
        self.assertIn("brzmienie z przypisem 28) obowiązuje tylko DO wejścia w życie zmiany (2026-11-05; przypis 29) → 6))", ostrz[0])
        self.assertIn("dziś jeszcze obowiązuje", ostrz[0])
        przyszle, _ = eli._zmiany_w_tekscie("\n".join(analiza), "2026-10-05")
        self.assertEqual([(d, o[:39]) for d, o in przyszle], [("2026-11-05", "przepis z przypisem 29) wejdzie w życie")])
        # po wejściu zmiany w życie stare brzmienie już nie obowiązuje
        _, _, ostrz = eli._przypisy_fragmentu(frag, txt, dzis="2026-12-01")
        self.assertIn("już weszła w życie, to brzmienie NIE obowiązuje", ostrz[0])

    def test_tresc_przypisu_z_dalszej_strony_pdf(self):
        # DU 2026 poz. 1245 (k.p., PDF T): przypis 46 („Obecnie minister właściwy do spraw pracy…") ma treść tylko na
        # stronie pierwszego odesłania, a t.j. odsyła do niego jeszcze na wielu dalszych stronach (23 → 7 braków)
        raw = ("Art. 237 1. § 2. Minister właściwy do spraw pracy46) określi, w drodze rozporządzenia, szczegółowe zasady.\n\n"
               "46) Obecnie minister właściwy do spraw pracy, na podstawie art. 4 ust. 1, art. 5 pkt 16 oraz art. 21 ustawy\n"
               "    z dnia 4 września 1997 r. o działach administracji rządowej.\n"
               "\fArt. 237 2. Pracodawca jest obowiązany zapewnić środki.\n"
               "\fArt. 237 3. § 2. Minister właściwy do spraw pracy46) określi, w drodze rozporządzenia, szczegółowe zasady.\n")
        t = eli.pdf_layout_do_tekstu(raw)
        self.assertEqual(t.count("[przypis 46)] Obecnie minister właściwy do spraw pracy"), 2, t)
        self.assertNotIn("nie odnaleziono", t)

    # 4. indeks górny w zeszycie 2000–2011
    def test_indeks_gorny_w_zeszycie_w_miejscu(self):
        # DU 2008 poz. 288, s. 1: słowa z `pdftotext -bbox` („222" + podniesione „1") i ten wiersz z `-layout`
        slowa = [(134.7674, 406.7197, 150.9244, 416.3097, "art."), (158.8684, 406.7197, 175.9464, 416.3097, "222"),
                 (176.1389, 405.2372, 180.3089, 412.4297, "1"), (188.2532, 406.7197, 193.8132, 416.3097, "§"),
                 (201.7562, 406.7197, 207.3162, 416.3097, "3")]
        wiersz = "   Na podstawie art. 2221 § 3 ustawy z dnia                                     1) drobnoustroje komórkowe, w tym zmo-"
        out = eli._pdf_oznacz_indeksy(wiersz, slowa, w_miejscu=True)
        self.assertEqual(out, wiersz.replace("art. 2221", "art. 222¹"))
        self.assertEqual(len(out), len(wiersz))          # łamy zostają na miejscu
        self.assertIn("art. 222 1 § 3", eli.pdf_layout_do_tekstu(out))

    def test_zeszyt_dostaje_indeksy_a_ocr_lat_90_nie(self):
        bbox = _bbox([(595, 842, [(134.7674, 406.7197, "art.", 150.9244, 416.3097), (158.8684, 406.7197, "222", 175.9464, 416.3097),
                                  (176.1389, 405.2372, "1", 180.3089, 412.4297), (188.2532, 406.7197, "§", 193.8132, 416.3097)])])
        layout = "Na podstawie art. 2221 § 3 ustawy\n\f"
        with mock.patch.object(eli, "_pdftotext", side_effect=lambda args, data: bbox if "-bbox" in args else layout):
            self.assertIn("art. 222¹ §", eli.pdf_do_tekstu_z_notkami(b"%PDF", zeszyt=True)[0])
            self.assertIn("art. 2221 §", eli.pdf_do_tekstu_z_notkami(b"%PDF", zeszyt=True, indeksy=False)[0])

    # 5. przypisy na dole łamu bez kreski
    def test_przypisy_lewego_lamu_bez_kreski(self):
        t = eli.pdf_layout_do_tekstu(eli.pdf_zeszyt_do_aktu(PDF_ZESZYT_2010_S1, 2010, 1128))
        self.assertIn("[przypis 1)] Minister Spraw Wewnętrznych i Administracji kieruje działem administracji rządowej", t)
        self.assertIn("[przypis 3)] Zmiany wymienionej ustawy zostały ogłoszone w Dz. U. z 1998 r.", t)
        self.assertNotIn("nie odnaleziono", t)
        self.assertIn("w Dzienniku Urzędowym Rzeczypospolitej Polskiej „Monitor Polski”, na podstawie art. 20 pkt 2", t)

    # 6–7. HTML: <h1>, gloss-section, „Pokaż całość"
    def test_html_naglowek_h1_rozdzielony(self):
        html = ('<h1><span class="head-type">Rozporządzenie Ministra Zdrowia</span><span class="head-date">z dnia 29 '
                'października 2024&nbsp;r.</span><span class="head-title">zmieniające rozporządzenie w sprawie środków '
                'odurzających</span></h1><div id="show-all" class="show-all hidden"><button id="show-all-button">Pokaż '
                'całość</button></div><script src="/act.js"></script>')
        self.assertEqual(eli.html_to_text(html), "Rozporządzenie Ministra Zdrowia\nz dnia 29 października 2024 r.\n"
                                                 "zmieniające rozporządzenie w sprawie środków odurzających")

    def test_html_przypis_z_gloss_section_tylko_raz(self):
        # DU 2014 poz. 141, § 29 (skrócone): przypis 3 w odsyłaczu i drugi raz w bloku gloss-section
        html = ('<h3 CLASS="pro-none"><B CLASS="b">§&nbsp;29.</B></h3><div class="unit-inner"><div data-template="xText" '
                'CLASS="pro-text">Rozporządzenie wchodzi w życie z dniem 1 lutego 2014&nbsp;r.<A class="gloss-link tooltip" '
                'href="#gloss-0:3:"><sup>3)</sup><span class="tooltip-text"><span class="pro-gloss-inner">Niniejsze '
                'rozporządzenie było poprzedzone rozporządzeniem Rady Ministrów z dnia 30\n marca 2004&nbsp;r.</span></span>'
                '</A></div></div><div class="gloss-section"><DIV CLASS="gloss" ID="gloss-0:3:"><div>3)</div><div><span '
                'class="pro-gloss-inner">Niniejsze rozporządzenie było poprzedzone rozporządzeniem Rady Ministrów z dnia 30\n'
                ' marca 2004&nbsp;r.</span></div></DIV><DIV CLASS="gloss" ID="gloss-0:9:"><div>9)</div><div>Przypis bez '
                'odsyłacza w treści.</div></DIV></div>')
        t = eli.html_to_text(html)
        self.assertEqual(t.count("Niniejsze rozporządzenie"), 1, t)
        self.assertNotIn("\n3)\n", t)                  # „3)" z bloku nie udaje punktu § 29
        self.assertIn("[przypis 9)] Przypis bez odsyłacza w treści.", t)
        [(s, e)] = eli._fragmenty(t, "§ 29")
        self.assertIn("[przypis 3)] Niniejsze rozporządzenie", t[s:e])

    # 8. powtórzony dywiz
    def test_powtorzony_dywiz_przy_lamaniu(self):
        self.assertEqual(eli._doklej("w Europie Środkowo-", "-Wschodniej. Przed Niemcami"),
                         "w Europie Środkowo-Wschodniej. Przed Niemcami")
        self.assertEqual(eli._doklej("zabez-", "pieczenia"), "zabezpieczenia")
        self.assertEqual(eli._doklej("ust. 1 i 2 –", "– tiret"), "ust. 1 i 2 – – tiret")   # półpauzy bez zmian

    # 9. --strict a strony bez warstwy tekstowej
    def _tekst_pdf(self, argv, raw, ocr=None):
        meta = {"ELI": "DU/2010/20", "year": 2010, "pos": None, "publisher": "DU", "textHTML": False,
                "displayAddress": "Dz.U. 2010 nr 3 poz. 20", "texts": [{"fileName": "D20100020.pdf", "type": "O"}]}

        def fake_get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else meta
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake_get), \
                mock.patch.object(eli, "_get_bytes", return_value=b"%PDF"), \
                mock.patch.object(eli, "pdftotext_dostepny", return_value=True), \
                mock.patch.object(eli, "pdf_do_tekstu_z_notkami", return_value=(raw, {}, {})), \
                mock.patch.object(eli, "ocr_dostepny", return_value=ocr is not None), \
                mock.patch.object(eli, "pdf_ocr_stron", side_effect=lambda data, nr: {i: ocr for i in nr}), \
                mock.patch.object(sys, "argv", ["eli.py"] + argv), contextlib.redirect_stdout(out):
            try:
                eli.main()
            except SystemExit as e:
                return out.getvalue(), str(e)
        return out.getvalue(), ""

    def test_strict_blokuje_strony_bez_warstwy_tekstowej(self):
        raw = PDF_1128_KONIEC + "\f\f" + PDF_1128_STOPKA + "\f"
        out, err = self._tekst_pdf(["tekst", "DU", "2010", "20", "--bez-ocr", "--strict"], raw)
        self.assertIn("BŁĄD (strict): 1 z 3 stron PDF", err)
        self.assertIn("NIEKOMPLETNY", err)
        self.assertNotIn("instalacji gazowej", out)
        out, err = self._tekst_pdf(["tekst", "DU", "2010", "20", "--bez-ocr"], raw)
        self.assertEqual(err, "")
        self.assertIn("1 z 3 stron tego PDF nie ma warstwy tekstowej", out)
        # z OCR: pusta strona to reklama wydawcy — po jej usunięciu tekst jest kompletny, więc strict przepuszcza
        out, err = self._tekst_pdf(["--strict", "tekst", "DU", "2010", "20"], raw, ocr=OCR_1128_REKLAMA)
        self.assertEqual(err, "")
        self.assertNotIn("UWAGA — OCR", out)
        self.assertIn("instalacji gazowej.", out)




# ---------------------------------------------------------------------------------------------
# 2026-11: kody wyjścia 0/1/2, analiza imienna zmian po stanie tekstu, wyroki TK, daty
# ---------------------------------------------------------------------------------------------
def _kod(argv, fake_get=None, **patche):
    """Uruchamia eli.kod_wyjscia(argv) z podmienionym _get; zwraca (kod, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    stos = contextlib.ExitStack()
    with stos:
        if fake_get is not None:
            stos.enter_context(mock.patch.object(eli, "_get", side_effect=fake_get))
        for nazwa, wartosc in patche.items():
            stos.enter_context(mock.patch.object(eli, nazwa, wartosc))
        stos.enter_context(mock.patch.object(sys, "argv", ["eli.py"]))
        stos.enter_context(contextlib.redirect_stdout(out))
        stos.enter_context(contextlib.redirect_stderr(err))
        kod = eli.kod_wyjscia(argv)
    return kod, out.getvalue(), err.getvalue()


class TestKodyWyjscia(unittest.TestCase):
    """0 = wynik; 1 = ZWERYFIKOWANY brak (zero trafień, frazy nie ma w tekście); 2 = wszystko inne
    (jak orzeczenia_ms.py). Dotąd sys.exit("…") dawało 1 także przy awarii."""

    PUSTE = {"count": 0, "totalCount": 0, "offset": 0, "items": []}

    def test_zero_trafien_to_1(self):
        kod, out, err = _kod(["szukaj", "zzz-nie-ma"], lambda path, params=None, soft=False: self.PUSTE)
        self.assertEqual(kod, 1)
        self.assertIn("Brak wyników", err)
        self.assertIn("NIE dowodzi", err)
        self.assertEqual(out, "")

    def test_offset_za_koncem_to_2_nie_1(self):
        odp = {"count": 0, "totalCount": 154, "offset": 1000, "items": []}
        kod, _, err = _kod(["szukaj", "Kodeks karny", "--offset", "1000"], lambda path, params=None, soft=False: odp)
        self.assertEqual(kod, 2)
        self.assertIn("za końcem listy", err)

    def test_zla_sygnatura_i_blad_http_to_2(self):
        kod, _, err = _kod(["meta", "xx"])
        self.assertEqual(kod, 2)
        self.assertIn("Nie rozpoznano sygnatury", err)

        def http(path, params=None, soft=False):
            sys.exit(f"BŁĄD HTTP 404: {eli.BASE}{path}")
        kod, _, err = _kod(["meta", "DU", "2026", "99999"], http)
        self.assertEqual(kod, 2)
        self.assertIn("BŁĄD HTTP 404", err)

    def test_verification_unknown_i_nieprzewidziany_wyjatek_to_2(self):
        def unknown(path, params=None, soft=False):
            raise eli.VerificationUnknown("timeout")
        kod, _, err = _kod(["meta", "DU", "2026", "1"], unknown)
        self.assertEqual(kod, 2)
        self.assertIn("nie udało się zweryfikować", err)

        def wyjatek(path, params=None, soft=False):
            raise KeyError("x")
        kod, _, err = _kod(["meta", "DU", "2026", "1"], wyjatek)
        self.assertEqual(kod, 2)
        self.assertIn("nieoczekiwany błąd", err)
        self.assertNotIn("Traceback", err)

    def test_blad_argumentow_to_2_a_wynik_to_0(self):
        kod, _, _ = _kod([])
        self.assertEqual(kod, 2)
        meta = {"title": "Ustawa", "displayAddress": "Dz.U. 2026 poz. 1", "status": "obowiązujący",
                "entryIntoForce": "2026-01-02", "type": "Ustawa"}
        kod, out, _ = _kod(["meta", "DU", "2026", "1"],
                           lambda path, params=None, soft=False: {} if path.endswith("/references") else meta)
        self.assertEqual(kod, 0)
        self.assertIn("Dz.U. 2026 poz. 1", out)

    def test_brak_frazy_w_tekscie_to_1_z_zastrzezeniem(self):
        def fake(path, params=None, soft=False):
            if path.endswith("/references"):
                return {}
            if path.endswith("/text.html"):
                return "<p>Art. 1. Ustawa reguluje.</p>"
            return {"entryIntoForce": "2020-01-01"}
        kod, _, err = _kod(["tekst", "DU", "2020", "1", "--fragment", "art. 99"], fake)
        self.assertEqual(kod, 1)
        self.assertIn("NIE dowodzi, że przepisu nie ma", err)

    def test_struktura_filtr_bez_trafien_to_1_brak_aktu_to_2(self):
        drzewo = [{"id": "art_1", "type": "art", "title": "Art. 1."}]
        kod, _, err = _kod(["struktura", "DU", "2020", "1", "--filtr", "Art. 999"],
                           lambda path, params=None, soft=False: drzewo)
        self.assertEqual(kod, 1)
        self.assertIn("nie pasuje do filtra", err)
        kod, _, err = _kod(["struktura", "DU", "2020", "1"],
                           lambda path, params=None, soft=False: None if path.endswith("/struct") else {"ELI": "DU/2020/1"})
        self.assertEqual(kod, 1)
        kod, _, err = _kod(["struktura", "DU", "2020", "1"], lambda path, params=None, soft=False: None)
        self.assertEqual(kod, 2)
        self.assertIn("nie zwraca też jego metadanych", err)

    def test_adres_isap_tylko_11_cyfr(self):
        # „DU 2026 99999" to nie adres ISAP (WDU202699999) — dotąd tak go czytano
        self.assertEqual(eli.act_path(["DU", "2026", "99999"])[0], "/acts/DU/2026/99999")
        self.assertEqual(eli.act_path(["WDU20240000018"])[0], "/acts/WDU20240000018")


class TestSzukajFiltrHasloAktBazowy(unittest.TestCase):
    """Zerowe wyniki i --haslo (audyt 2026-08, „eli × zero-results")."""

    def _ns(self, **kw):
        a = dict(fraza=None, typ=None, rok=None, wyd=None, haslo=None, obowiazujace=False, limit=10, offset=0, json=False)
        a.update(kw)
        return argparse.Namespace(**a)

    def test_sam_filtr_wydawcy_i_roku_jest_dozwolony(self):
        wolania = []

        def fake(path, params=None, soft=False):
            wolania.append(params)
            return {"totalCount": 1, "items": [{"address": "WMP20260001139", "ELI": "MP/2026/1139", "title": "Komunikat"}]}
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), contextlib.redirect_stdout(out):
            eli.cmd_szukaj(self._ns(wyd="MP", rok="2026"))
        self.assertIn("Znaleziono: 1", out.getvalue())
        self.assertEqual(wolania[0]["publisher"], "MP")
        self.assertIsNone(wolania[0]["title"])

    def test_haslo_spoza_slownika_to_zly_argument_z_podpowiedzia(self):
        def fake(path, params=None, soft=False):
            return ["Sygnaliści", "sygnaliści", "Trybunał Konstytucyjny"] if path == "/keywords" else \
                {"totalCount": 0, "items": []}
        with mock.patch.object(eli, "_get", side_effect=fake):
            with self.assertRaises(SystemExit) as caught:
                eli.cmd_szukaj(self._ns(haslo="sygnalisci"))
        self.assertNotIsInstance(caught.exception, eli.BrakWynikow)
        self.assertIn("nie ma w słowniku", str(caught.exception.code))
        self.assertIn("sygnaliści", str(caught.exception.code))

    def test_haslo_ze_slownika_bez_trafien_to_brak_wynikow(self):
        def fake(path, params=None, soft=False):
            return ["sygnaliści"] if path == "/keywords" else {"totalCount": 0, "items": []}
        with mock.patch.object(eli, "_get", side_effect=fake):
            with self.assertRaises(eli.BrakWynikow):
                eli.cmd_szukaj(self._ns(haslo="Sygnaliści", rok="2020"))

    def test_akt_bazowy_spoza_strony_jest_wskazany(self):
        # 2026-11-13: „Kodeks karny" --typ Ustawa — 101 trafień, k.k. dopiero na pozycji 92
        strona = [{"address": f"WDU2026000{n:04d}", "ELI": f"DU/2026/{n}", "title": "Ustawa o zmianie ustawy - Kodeks karny"}
                  for n in range(1, 11)]
        wszystkie = strona + [{"address": "WDU19970880553", "ELI": "DU/1997/553", "status": "akt posiada tekst jednolity",
                               "title": "Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny."},
                              {"address": "WDU19970900557", "ELI": "DU/1997/557",
                               "title": "Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny wykonawczy."}]

        def fake(path, params=None, soft=False):
            return {"totalCount": 12, "items": strona if params["limit"] == 10 else wszystkie}
        out = io.StringIO()
        with mock.patch.object(eli, "_get", side_effect=fake), contextlib.redirect_stdout(out):
            eli.cmd_szukaj(self._ns(fraza="Kodeks karny", typ="Ustawa"))
        self.assertIn("pozycja 11 z 12", out.getvalue())
        self.assertIn("DU/1997/553", out.getvalue())
        self.assertNotIn("DU/1997/557", out.getvalue())          # k.k.w. ma inny tytuł


# treść aktów zmieniających i wyroków TK (wycinki z urzędowych tekstów pobranych 2026-11-13)
ZM_KPK = ("Dz. U. 2026 poz. 901\nUSTAWA\nz dnia 11 czerwca 2026 r.\n"
          "Art. 1. W ustawie z dnia 6 czerwca 1997 r. – Kodeks postępowania karnego (Dz. U. z 2026 r. poz. 490, 421, 638, "
          "760 i 882) w art. 87 dodaje się § 4 i 5 w brzmieniu:\n„§ 4. Zażalenie na postanowienie o odmowie, o której mowa "
          "w § 3, rozpoznaje sąd rejonowy.\n§ 5. Postanowienie prokuratora, o którym mowa w art. 99, staje się wykonalne.”.\n"
          "Art. 2. W ustawie z dnia 10 września 1999 r. – Kodeks karny skarbowy (Dz. U. z 2025 r. poz. 633) w art. 122 "
          "w § 1 w pkt 1 po wyrazach „art. 87 § 3” dodaje się wyrazy „i 5”.\n"
          "Art. 3. Do rozpoznania zażaleń, o których mowa w art. 87 § 3 ustawy zmienianej w art. 1, stosuje się przepisy "
          "nowe.\nArt. 5. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.\n")
ZM_RADCY = ("Art. 1. W ustawie z dnia 6 lipca 1982 r. o radcach prawnych (Dz. U. z 2024 r. poz. 499, z 2025 r. poz. 1172 "
            "oraz z 2026 r. poz. 370) wprowadza się następujące zmiany:\n1) w art. 5a w ust. 2 wyrazy „a” zastępuje się "
            "wyrazami „b”;\n2) art. 5b i art. 5c otrzymują brzmienie:\n„Art. 5b. Obowiązek zachowania tajemnicy, o której "
            "mowa w art. 3 ust. 3–6, nie ustaje.\nArt. 5c. 1. Okres przechowywania danych wynosi 10 lat.”;\n3) po art. 5c "
            "dodaje się art. 5d w brzmieniu:\n„Art. 5d. Administratorem jest izba.”;\n4) w art. 22 2 ust. 1 otrzymuje "
            "brzmienie:\n„1. Treść.”;\n5) uchyla się art. 30–32.\nArt. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia "
            "ogłoszenia.\n")
ZM_PLANOWANIE_I_KC = ("Art. 1. W ustawie z dnia 7 stycznia 1993 r. o planowaniu rodziny, ochronie płodu ludzkiego i warunkach "
                      "dopuszczalności przerywania ciąży (Dz. U. Nr 17, poz. 78 i z 1995 r. Nr 66, poz. 334) wprowadza się "
                      "następujące zmiany:\n1) w art. 9 wyrazy „x” zastępuje się wyrazami „y”.\n"
                      "Art. 2. W ustawie z dnia 23 kwietnia 1964 r. - Kodeks cywilny (Dz. U. Nr 16, poz. 93, z 1971 r. Nr 27, "
                      "poz. 252, z 1993 r. Nr 17, poz. 78) wprowadza się następujące zmiany:\n1) w art. 8 skreśla się § 2;\n"
                      "2) w art. 446 1 po kropce dodaje się nowe zdanie.\nArt. 3. Ustawa wchodzi w życie po upływie 14 dni.\n")
BAZA_KPK = {"ELI": "DU/1997/555", "title": "Ustawa z dnia 6 czerwca 1997 r. - Kodeks postępowania karnego.",
            "announcementDate": "1997-06-06"}
BAZA_KK = {"ELI": "DU/1997/553", "title": "Ustawa z dnia 6 czerwca 1997 r. - Kodeks karny.", "announcementDate": "1997-06-06"}
BAZA_RADCY = {"ELI": "DU/1982/145", "title": "Ustawa z dnia 6 lipca 1982 r. o radcach prawnych.", "announcementDate": "1982-07-06"}
BAZA_PLAN = {"ELI": "DU/1993/78", "announcementDate": "1993-01-07",
             "title": "Ustawa z dnia 7 stycznia 1993 r. o planowaniu rodziny, ochronie płodu ludzkiego i warunkach "
                      "dopuszczalności przerywania ciąży."}
BAZA_KC = {"ELI": "DU/1964/93", "title": "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny.", "announcementDate": "1964-04-23"}


class TestZmianyWAkcieZmieniajacym(unittest.TestCase):
    """Które artykuły aktu bazowego zmienia akt zmieniający — z jego treści (zweryfikowane 2026-11-13 na 26 parach
    akt zmieniający → akt bazowy z lat 1995–2026: 124 z 124 artykułów zgodnie z ręcznym odczytem)."""

    def test_tylko_przepis_zmieniajacy_ten_akt_i_bez_cytatow(self):
        z = eli._zmiany_w_akcie(ZM_KPK, {(1997, 555), (2026, 490)}, BAZA_KPK)
        self.assertEqual(z["klucze"], {"87"})          # nie 99 (w cytacie), nie 122 (k.k.s.), nie art. 3 (przejściowy)
        self.assertEqual(z["przepisy"], ["art. 1"])
        self.assertEqual(z["blad"], "")
        self.assertIn("14 dni", z["wejscie"])
        z = eli._zmiany_w_akcie(ZM_KPK, {(1999, 930), (2025, 633)}, {"ELI": "DU/1999/930"})
        self.assertEqual(z["klucze"], {"122"})

    def test_dodane_zmienione_uchylone_indeks_gorny_zakres(self):
        z = eli._zmiany_w_akcie(ZM_RADCY, {(1982, 145), (2024, 499)}, BAZA_RADCY)
        self.assertEqual(z["klucze"], {"5a", "5b", "5c", "5d", "22^2", "30", "31", "32"})   # „po art. 5c" to tylko miejsce

    def test_pierwsza_pozycja_cytatu_a_nie_nowelizacje(self):
        # DU 1996/646: art. 2 zmienia k.c., a jego cytat wymienia też 1993 Nr 17 poz. 78 (= ustawa o planowaniu rodziny)
        z = eli._zmiany_w_akcie(ZM_PLANOWANIE_I_KC, {(1993, 78)}, BAZA_PLAN)
        self.assertEqual((z["przepisy"], z["klucze"]), (["art. 1"], {"9"}))
        z = eli._zmiany_w_akcie(ZM_PLANOWANIE_I_KC, {(1964, 93)}, BAZA_KC)
        self.assertEqual((z["przepisy"], z["klucze"]), (["art. 2"], {"8", "446^1"}))

    def test_bez_cytatu_tytul_z_granica_wyrazu(self):
        kkw = ("Art. 1. W ustawie z dnia 6 czerwca 1997 r. – Kodeks karny wykonawczy wprowadza się następujące zmiany:\n"
               "1) w art. 5 uchyla się § 2;\nArt. 2. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.\n")
        self.assertEqual(eli._zmiany_w_akcie(kkw, set(), BAZA_KK)["klucze"], set())
        self.assertTrue(eli._zmiany_w_akcie(kkw, set(), BAZA_KK)["blad"])
        kk = kkw.replace("Kodeks karny wykonawczy wprowadza się następujące zmiany:\n1)", "Kodeks karny")
        self.assertEqual(eli._zmiany_w_akcie(kk, set(), BAZA_KK)["klucze"], {"5"})

    def test_cala_ustawa_i_niedomkniety_cudzyslow_to_niepewnosc(self):
        cala = ZM_KPK.replace("w art. 87 dodaje się § 4 i 5 w brzmieniu:", "w całej ustawie wyrazy „x” zastępuje się "
                              "wyrazami „y”, a w art. 87 dodaje się § 4 i 5 w brzmieniu:")
        self.assertTrue(eli._zmiany_w_akcie(cala, {(2026, 490)}, BAZA_KPK)["calosc"])
        zgubiony = "„Wstęp bez zamknięcia " + "x " * 120000 + "\n" + ZM_KPK.replace("„", "").replace("”", "")
        self.assertTrue(eli._zmiany_w_akcie(zgubiony, {(2026, 490)}, BAZA_KPK)["blad"])


SENT_K_9_04 = ("Wyrok Trybunału Konstytucyjnego z dnia 15 marca 2005 r. orzeka: I Art. 179 ustawy z dnia 23 kwietnia 1964 r. "
               "- Kodeks cywilny (Dz. U. Nr 16, poz. 93, ze zm.): a) jest zgodny z art. 16 i art. 167 ust. 4 Konstytucji "
               "Rzeczypospolitej Polskiej oraz nie jest niezgodny z art. 31 ust. 3 i art. 32 Konstytucji, b) jest niezgodny "
               "z art. 2 i art. 165 Konstytucji. II Art. 179 ustawy powołanej w pkt I sentencji traci moc obowiązującą "
               "z dniem 15 lipca 2006 r. Poświadczono za zgodność z oryginałem.")
SENT_K_29_23 = ("orzeka: 1. Art. 26 ust. 4 ustawy z dnia 29 grudnia 1992 r. o radiofonii i telewizji (Dz. U. z 2022 r. poz. "
                "1722) w zakresie, w jakim pomija literalne wskazanie art. 459-478 ustawy z dnia 15 września 2000 r. - "
                "Kodeks spółek handlowych (Dz. U. z 2024 r. poz. 18), jest niezgodny z art. 2 Konstytucji. 2. Art. 459-478 "
                "ustawy - Kodeks spółek handlowych o rozwiązaniu i likwidacji spółki akcyjnej, rozumiane w ten sposób, że "
                "obejmują także jednostki publicznej radiofonii, są niezgodne z art. 2 Konstytucji. Ponadto postanawia: "
                "umorzyć postępowanie.")
SENT_P_13_16 = ("orzeka: Art. 691 § 5 ustawy z dnia 23 kwietnia 1964 r. - Kodeks cywilny (Dz. U. z 2017 r. poz. 459, ze zm.) "
                "w zakresie, w jakim wyłącza prawo dziecka, jest zgodny z art. 32 ust. 1 Konstytucji. Ponadto postanawia:")
SENT_P_46_09 = ("orzeka: Art. 2 ustawy z dnia 16 lutego 2007 r. o zmianie ustawy - Kodeks cywilny (Dz. U. Nr 80, poz. 538) "
                "jest zgodny z art. 2 Konstytucji. Poświadczono")
SENT_SK_14_21 = ("orzeka: I. Art. 1a ust. 1 pkt 2 ustawy z dnia 12 stycznia 1991 r. o podatkach i opłatach lokalnych (Dz. U. "
                 "z 2023 r. poz. 70) jest niezgodny z art. 84 i art. 217 Konstytucji. II. Przepis wymieniony w części I traci "
                 "moc obowiązującą po upływie 18 (osiemnastu) miesięcy od dnia ogłoszenia wyroku. Ponadto postanawia:")
SENT_SK_109_20 = ("orzeka: I § 31 rozporządzenia Ministra Pracy i Polityki Społecznej z dnia 11 października 2011 r. w sprawie "
                  "postępowania o świadczenia emerytalno-rentowe (Dz. U. Nr 237, poz. 1412) jest niezgodny z art. 67 ust. 1 "
                  "Konstytucji. II Przepis wskazany w części I traci moc obowiązującą po upływie 12 miesięcy. Ponadto postanawia:")


class TestSentencjaTk(unittest.TestCase):
    """Sentencja wyroku TK → artykuły, zakres, rozstrzygnięcie, akt (26 wyroków z lat 1997–2024 sprawdzone 2026-11-13;
    wszystkie wyroki TK w Dz.U. mają text.html)."""

    CELE_KC = {(1964, 93), (2017, 459), (2026, 795)}

    def test_niezgodny_z_jednym_wzorcem_to_utrata_mocy_mimo_zgodnosci_z_innymi(self):
        pkt = eli._sentencja_tk(SENT_K_9_04, self.CELE_KC, BAZA_KC)
        self.assertEqual(pkt[0]["klucze"], {"179"})
        self.assertIn("niezgodny", pkt[0]["werdykty"])
        self.assertFalse(pkt[0]["zakresowy"])
        self.assertEqual(pkt[0]["akt"], "ten")
        self.assertEqual(pkt[1]["akt"], "ten")              # „ustawy powołanej w pkt I"

    def test_wyrok_interpretacyjny_jest_zakresowy(self):
        pkt = eli._sentencja_tk(SENT_K_29_23, {(2000, 1037), (2024, 18)},
                                {"title": "Ustawa z dnia 15 września 2000 r. - Kodeks spółek handlowych.",
                                 "announcementDate": "2000-09-15"})
        ksh = [x for x in pkt if "459" in x["klucze"]]
        self.assertTrue(ksh and all(x["zakresowy"] for x in ksh))
        self.assertEqual(pkt[-1]["akt"], "ten")

    def test_zgodny_i_inny_akt(self):
        self.assertEqual(eli._sentencja_tk(SENT_P_13_16, self.CELE_KC, BAZA_KC)[0]["werdykty"], {"zgodny"})
        self.assertEqual(eli._sentencja_tk(SENT_P_46_09, self.CELE_KC, BAZA_KC)[0]["akt"], "inny")

    def test_czesc_i_wyroku_to_nie_zakres_a_paragraf_rozporzadzenia(self):
        pkt = eli._sentencja_tk(SENT_SK_14_21, {(1991, 31), (2023, 70)}, {})
        self.assertFalse(pkt[0]["zakresowy"])
        self.assertTrue(pkt[0]["czesc"])
        pkt = eli._sentencja_tk(SENT_SK_109_20, {(2011, 1412)}, {})
        self.assertEqual(pkt[0]["paragrafy"], {"31"})
        self.assertFalse(pkt[0]["zakresowy"])

    def test_brak_sentencji(self):
        self.assertIsNone(eli._sentencja_tk("Obwieszczenie bez rozstrzygnięcia", set(), {}))


def _refs_tj(base_eli="DU/1982/145"):
    return {"Tekst jednolity dla aktu": [{"act": {"ELI": base_eli, "displayAddress": "Dz.U. 1982 nr 19 poz. 145"}}]}


class TestAnalizaImienna(unittest.TestCase):
    """Przepis zmieniony po stanie t.j.: ostrzeżenie imienne i blokada --strict (radcy prawni DU 2024/499 art. 5b,
    zmiana DU 2026/731 od 2026-06-18 — dotąd --strict dawał kod 0 i stare brzmienie)."""

    HTML_TJ = ("<p>Treść obwieszczenia</p><p>1. ogłasza się w załączniku jednolity tekst ustawy z uwzględnieniem zmian "
               "wprowadzonych: 1) ustawą (Dz. U. poz. 10) oraz zmian wynikających z przepisów ogłoszonych przed dniem "
               "14 marca 2024 r.</p><p>Art. 5b.</p><p>Obowiązek zachowania tajemnicy, o której mowa w art. 3 ust. 4-6, "
               "nie ustaje.</p><p>Art. 6.</p><p>Radca prawny wykonuje zawód.</p>")

    def _fake(self, zmiany, teksty, base_refs_extra=None, meta_tj=None):
        base_refs = {"Inf. o tekście jednolitym": [{"act": {"ELI": "DU/2024/499", "displayAddress": "Dz.U. 2024 poz. 499"}}],
                     "Akty zmieniające": zmiany}
        base_refs.update(base_refs_extra or {})

        def fake(path, params=None, soft=False):
            if path == "/acts/DU/2024/499/references":
                return _refs_tj()
            if path == "/acts/DU/1982/145/references":
                return base_refs
            if path == "/acts/DU/2024/499":
                return meta_tj or {"legalStatusDate": "2024-03-14", "textHTML": True}
            if path == "/acts/DU/2024/499/text.html":
                return self.HTML_TJ
            if path == "/acts/DU/1982/145":
                return BAZA_RADCY
            m = re.match(r"^/acts/(DU/\d+/\d+)(/text\.html)?$", path)
            if m and m.group(2):
                return teksty.get(m.group(1), "")
            if m:
                return {"ELI": m.group(1), "textHTML": True}
            raise AssertionError(path)
        return fake

    ZM_731 = [{"act": {"ELI": "DU/2026/731", "displayAddress": "Dz.U. 2026 poz. 731", "type": "Ustawa",
                       "title": "Ustawa z dnia 15 maja 2026 r. o zmianie ustawy o radcach prawnych",
                       "announcementDate": "2026-05-15", "promulgation": "2026-06-03"}, "date": "2026-06-18"}]
    TEKSTY = {"DU/2026/731": "<p>" + ZM_RADCY.replace("\n", "</p><p>") + "</p>"}

    def _uruchom(self, argv, zmiany=None, teksty=None, **kw):
        return _kod(argv, self._fake(self.ZM_731 if zmiany is None else zmiany, self.TEKSTY if teksty is None else teksty,
                                     **kw), _dzis=lambda: "2026-11-13")

    def test_zmieniony_artykul_ostrzezenie_imienne_i_blokada_strict(self):
        kod, out, err = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5b"])
        self.assertEqual(kod, 0)
        self.assertIn("TEN PRZEPIS ZMIENIONO po stanie tego tekstu: art. 5b zmienia Dz.U. 2026 poz. 731", out)
        self.assertIn("wejście w życie zmiany 2026-06-18", out)
        self.assertIn("Obowiązek zachowania tajemnicy", out)
        kod, out, err = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"])
        self.assertEqual(kod, 2)
        self.assertEqual(out, "")
        self.assertIn("art. 5b: zmiana Dz.U. 2026 poz. 731", err)

    def test_niezmieniony_artykul_przechodzi_z_informacja(self):
        kod, out, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 6"])
        self.assertEqual(kod, 0)
        self.assertIn("nie znalazłem zmiany art. 6", out)

    def test_caly_tekst_strict_blokuje_gdy_zmiana_w_zyciu_nieujeta(self):
        kod, _, err = self._uruchom(["--strict", "tekst", "DU", "2024", "499"])
        self.assertEqual(kod, 2)
        self.assertIn("NIE MA jej w tym tekście", err)

    def test_zmiana_przyszla_bez_blokady(self):
        przyszla = [dict(self.ZM_731[0], date="2027-01-01")]
        kod, out, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=przyszla)
        self.assertEqual(kod, 0)
        self.assertIn("ZMIENIA Dz.U. 2026 poz. 731", out)
        self.assertIn("z dniem 2027-01-01", out)

    def test_ujeta_w_obwieszczeniu_albo_przypisie_nie_jest_zmiana_po_tj(self):
        ujeta = [dict(self.ZM_731[0], act=dict(self.ZM_731[0]["act"], promulgation="2024-03-01"), date="2024-03-20")]
        kod, out, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=ujeta)
        self.assertEqual(kod, 0)
        self.assertNotIn("TEN PRZEPIS ZMIENIONO", out)

    def test_nieudane_pobranie_aktu_zmieniajacego_to_niepewnosc(self):
        kod, out, _ = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5b"], teksty={})
        self.assertEqual(kod, 0)
        self.assertIn("nie ustaliłem, czy te zmiany po stanie tekstu dotyczą tego fragmentu", out)
        kod, _, err = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], teksty={})
        self.assertEqual(kod, 2)

    def test_artykul_dodany_po_stanie_tekstu_to_nie_brak(self):
        kod, _, err = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5d"])
        self.assertEqual(kod, 2)
        self.assertIn("To NIE jest brak przepisu", err)
        self.assertIn("art. 5d zmienia Dz.U. 2026 poz. 731", err)

    WYROK = {"act": {"ELI": "DU/2025/5", "displayAddress": "Dz.U. 2025 poz. 5", "type": "Orzeczenie",
                     "title": "Wyrok Trybunału Konstytucyjnego z dnia 7 stycznia 2025 r. sygn. akt K 1/25",
                     "announcementDate": "2025-01-07", "promulgation": "2025-01-10"}}

    def _tk(self, sentencja, skutek):
        teksty = {"DU/2025/5": "<p>orzeka: Art. 5b ustawy z dnia 6 lipca 1982 r. o radcach prawnych (Dz. U. z 2024 r. "
                               "poz. 499) " + sentencja + " Poświadczono</p>"}
        return [dict(self.WYROK, date=skutek)], teksty, {"Orzeczenie TK": [self.WYROK]}

    def test_wyrok_tk_po_tj_utrata_mocy_blokuje_strict(self):
        zm, teksty, extra = self._tk("jest niezgodny z art. 2 Konstytucji.", "2025-01-10")
        kod, out, _ = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm, teksty=teksty,
                                    base_refs_extra=extra)
        self.assertIn("WYROKI TK OGŁOSZONE PO STANIE TEGO TEKSTU", out)
        self.assertIn("PRZEPIS UTRACIŁ MOC (wyrok TK Dz.U. 2025 poz. 5 (K 1/25), skutek od 2025-01-10): art. 5b", out)
        kod, _, err = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm,
                                    teksty=teksty, base_refs_extra=extra)
        self.assertEqual(kod, 2)
        self.assertIn("utrata mocy po wyroku TK", err)

    def test_wyrok_tk_mieszany_zgodny_i_niezgodny_to_utrata_mocy(self):
        # jak K 9/04 (art. 179 k.c.): „a) jest zgodny z … b) jest niezgodny z …" — przepis utracił moc w całości
        zm, teksty, extra = self._tk(": a) jest zgodny z art. 16 Konstytucji, b) jest niezgodny z art. 2 Konstytucji.",
                                     "2025-01-10")
        kod, _, err = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm,
                                    teksty=teksty, base_refs_extra=extra)
        self.assertEqual(kod, 2)
        self.assertIn("utrata mocy po wyroku TK", err)

    def test_wyrok_tk_ze_skutkiem_wg_api_bez_punktu_o_tym_akcie_to_niepewnosc(self):
        # jak SK 34/08 przy k.c.: API zapisuje skutek dla aktu, a sentencja dotyczy art. 5 ustawy zmieniającej
        zm, _, extra = self._tk("", "2025-01-10")
        teksty = {"DU/2025/5": "<p>orzeka: Art. 5 ustawy z dnia 17 czerwca 2004 r. o zmianie ustawy o radcach prawnych "
                               "(Dz. U. Nr 162, poz. 1692) jest niezgodny z art. 2 Konstytucji. Poświadczono</p>"}
        kod, out, _ = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm, teksty=teksty,
                                    base_refs_extra=extra)
        self.assertIn("sentencja nie wskazuje go wprost", out)
        self.assertNotIn("nie wymienia art. 5b", out)
        kod, _, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm,
                                  teksty=teksty, base_refs_extra=extra)
        self.assertEqual(kod, 2)

    def test_rozne_terminy_punktow_nie_twierdzi_sprzed(self):
        # DU 2026/1290: „…z wyjątkiem art. 1 pkt 16, 41 i 42, które wchodzą w życie z dniem 1 stycznia 2027 r."
        teksty = {"DU/2026/731": self.TEKSTY["DU/2026/731"].replace(
            "Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.",
            "Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia, z wyjątkiem art. 1 pkt 2, który wchodzi "
            "w życie z dniem 1 stycznia 2027 r.")}
        kod, out, _ = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 5b"], teksty=teksty)
        self.assertIn("data API dla aktu zmieniającego; dla poszczególnych punktów terminy są RÓŻNE", out)
        self.assertNotIn("SPRZED", out)
        kod, _, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], teksty=teksty)
        self.assertEqual(kod, 2)

    def test_uchylenie_rozdzialu_to_niepewnosc(self):
        z = eli._zmiany_w_akcie(ZM_RADCY.replace("5) uchyla się art. 30–32.", "5) uchyla się rozdział 3."),
                                {(2024, 499)}, BAZA_RADCY)
        self.assertTrue(z["calosc"])
        teksty = {"DU/2026/731": "<p>Art. 1. W ustawie z dnia 6 lipca 1982 r. o radcach prawnych (Dz. U. z 2024 r. "
                                 "poz. 499) uchyla się rozdział 3.</p><p>Art. 2. Ustawa wchodzi w życie po upływie "
                                 "14 dni od dnia ogłoszenia.</p>"}
        kod, out, _ = self._uruchom(["tekst", "DU", "2024", "499", "--fragment", "art. 6"], teksty=teksty)
        self.assertIn("uchylenie albo nowe brzmienie całego rozdziału", out)
        self.assertNotIn("nie znalazłem zmiany art. 6", out)

    def test_wyrok_tk_zakresowy_tylko_ostrzega(self):
        zm, teksty, extra = self._tk("w zakresie, w jakim dotyczy organu nadzorczego, jest niezgodny z art. 2 "
                                     "Konstytucji.", "2025-01-10")
        kod, out, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm,
                                    teksty=teksty, base_refs_extra=extra)
        self.assertEqual(kod, 0)
        self.assertIn("WYROK TK ZAKRESOWY", out)

    def test_wyrok_tk_z_odroczona_utrata_mocy_ostrzega_bez_blokady(self):
        # żywego przypadku nie ma (0 wyroków TK w Dz.U. 2025–2026) — data „dziś" ustalona w teście
        zm, teksty, extra = self._tk("jest niezgodny z art. 2 Konstytucji. II. Przepis wymieniony w części I traci moc "
                                     "obowiązującą po upływie 12 miesięcy.", "2027-01-10")
        kod, out, _ = self._uruchom(["--strict", "tekst", "DU", "2024", "499", "--fragment", "art. 5b"], zmiany=zm,
                                    teksty=teksty, base_refs_extra=extra)
        self.assertEqual(kod, 0)
        self.assertIn("utrata mocy ODROCZONA do 2027-01-10", out)

    def test_akt_bez_tj_lista_zmian_i_brzmienie_pierwotne(self):
        refs = {"Akty zmieniające": self.ZM_731}

        def fake(path, params=None, soft=False):
            if path == "/acts/DU/1982/145/references":
                return refs
            if path == "/acts/DU/1982/145/text.html":
                return "<p>Art. 5b.</p><p>Stare brzmienie.</p>"
            if path == "/acts/DU/1982/145":
                return BAZA_RADCY
            if path == "/acts/DU/2026/731/text.html":
                return self.TEKSTY["DU/2026/731"]
            raise AssertionError(path)
        kod, out, _ = _kod(["tekst", "DU", "1982", "145", "--fragment", "art. 5b"], fake, _dzis=lambda: "2026-11-13")
        self.assertIn("ten akt NIE MA tekstu jednolitego", out)
        self.assertIn("TEN PRZEPIS ZMIENIONO", out)


class TestDatyObwieszczeniaWejsciaTk(unittest.TestCase):
    def test_stan_z_obwieszczenia_obie_formuly(self):
        self.assertEqual(eli._stan_z_obwieszczenia("» z uwzględnieniem stanu prawnego na dzień 24 marca 2026 r. oraz"),
                         ("stan", "2026-03-24"))
        self.assertEqual(eli._stan_z_obwieszczenia("zmian wynikających z przepisów ogłoszonych przed dniem 5 grudnia "
                                                   "2023 r."), ("przed", "2023-12-05"))
        self.assertEqual(eli._stan_z_obwieszczenia("z uwzględnieniem zmian wprowadzonych: 1) rozporządzeniem"), ("", ""))

    def test_rozbieznosc_z_api_ostrzega_i_poszerza_liste(self):
        # k.p.c. DU 2026/468: API 2026-03-25, obwieszczenie „24 marca 2026 r." (sprawdzone 2026-11-13)
        base_refs = {"Akty zmieniające": [{"act": {"ELI": "DU/2026/400", "displayAddress": "Dz.U. 2026 poz. 400",
                                                   "promulgation": "2026-03-24"}, "date": "2026-04-08"}]}
        wynik = {"stan": "2026-03-25", "baza": {"ELI": "DU/1964/296"}, "pozycje": []}
        with mock.patch.object(eli, "_get", return_value=base_refs):
            linie = eli._sprawdz_stan_obwieszczenia("stanu prawnego na dzień 24 marca 2026 r.",
                                                    {"legalStatusDate": "2026-03-25"}, wynik)
        self.assertIn("2026-03-24", linie[0])
        self.assertIn("2026-03-25", linie[0])
        self.assertEqual(wynik["stan"], "2026-03-24")
        self.assertEqual([p["act"]["ELI"] for p in wynik["pozycje"]], ["DU/2026/400"])

    def test_granica_daty_stanu_prawnego(self):
        refs = {"Akty zmieniające": [
            {"act": {"ELI": "DU/1/1", "promulgation": "2023-12-05"}, "date": "2024-01-01"},   # ogłoszony w dniu X
            {"act": {"ELI": "DU/1/2", "promulgation": "2023-11-01"}, "date": "2023-12-05"},   # w życie w dniu X
            {"act": {"ELI": "DU/1/3", "promulgation": "2023-11-01"}, "date": "2023-12-06"}]}
        with mock.patch.object(eli, "_get", return_value=refs):
            eli_po = [r["act"]["ELI"] for r in eli._zmiany_bazowe_po("DU/9/9", "2023-12-05")]
        self.assertEqual(eli_po, ["DU/1/1", "DU/1/3"])

    def test_puste_wejscie_w_zycie(self):
        warunek = {"type": "Rozporządzenie", "status": "obowiązujący", "entryIntoForce": None,
                   "comments": "Rozporządzenie wchodzi w życie z dniem określonym w komunikacie ministra"}
        self.assertIn("termin zależy od zdarzenia", eli._jeszcze_nie_obowiazuje(warunek, "2026-11-13")[0])
        bez_uwag = {"type": "Rozporządzenie", "entryIntoForce": None}
        self.assertIn("--fragment \"wchodzi w życie\"", eli._jeszcze_nie_obowiazuje(bez_uwag, "2026-11-13")[0])
        self.assertEqual(eli._jeszcze_nie_obowiazuje({"type": "Obwieszczenie"}, "2026-11-13"), [])

    def test_wyrok_tk_w_przypisie_fragmentu(self):
        frag = ("2)\n(utracił moc)\n[przypis 12)] Z dniem 27 stycznia 2021 r. na podstawie wyroku Trybunału "
                "Konstytucyjnego z dnia 22 października 2020 r. sygn. akt K 1/20 (Dz. U. z 2021 r. poz. 175).")
        linie = eli._tk_w_przypisach(frag)
        self.assertIn("WYROK TK W PRZYPISIE", linie[0])
        self.assertIn("K 1/20", linie[1])
        # tekst przepisu (nie przypis) o Trybunale nie jest ostrzeżeniem
        self.assertEqual(eli._tk_w_przypisach("Prezes Trybunału Konstytucyjnego w zakresie swoich zadań"), [])
        self.assertIn("(utracił moc)", eli._tk_w_przypisach("Art. 179. (utracił moc)")[0])

    def test_lista_tk_meta_ze_skutkiem(self):
        refs = {"Orzeczenie TK": [{"act": {"ELI": "DU/2021/175", "displayAddress": "Dz.U. 2021 poz. 175",
                                           "title": "Wyrok Trybunału Konstytucyjnego z dnia 22 października 2020 r. sygn. akt K 1/20",
                                           "promulgation": "2021-01-27"}}],
                "Akty zmieniające": [{"act": {"ELI": "DU/2021/175", "type": "Orzeczenie",
                                              "title": "Wyrok Trybunału Konstytucyjnego"}, "date": "2021-01-27"}]}
        linie = eli._linie_tk_meta(refs)
        self.assertIn("K 1/20", linie[1])
        self.assertIn("skutek dla tego aktu od 2021-01-27", linie[1])


class TestOdsylaczeZeWspolrzednych202611(unittest.TestCase):
    """Odsyłacze do przypisów w PDF rozpoznawane po współrzędnych słów (`pdftotext -bbox`): indeks górny = osobne, niższe
    i podniesione słowo „N)". Współrzędne skopiowane 13.11.2026 z urzędowych PDF (wartości z `-bbox`)."""

    # k.c. DU 2026/795 s. 21: „(utracił moc)" + podniesione „2)"; numer przypisu „2)" na dole strony (bez słowa z lewej)
    KC_SLOWA = [(143.483960, 645.675640, 163.941800, 656.701360, "moc)"),
                (163.940000, 645.296320, 169.337840, 652.469680, "2)"),
                (51.0, 772.23, 55.998, 778.872, "2)"), (65.04, 772.557, 70.539, 782.52, "Z")]
    KC_STRONA = ("      Art. 178. (uchylony)\n\n"
                 "                                                              Rozdział III\n"
                 "                                             Inne wypadki nabycia i utraty własności\n"
                 "                                2)\n"
                 "      Art. 179. (utracił moc)\n"
                 "      Art. 180. Właściciel może wyzbyć się własności rzeczy ruchomej przez to, że w tym zamiarze rzecz porzuci.\n"
                 "      Art. 182. § 1. Rój pszczół staje się niczyim, jeżeli właściciel nie odszukał go przed upływem trzech dni.\n"
                 "      § 2. Jeżeli rój osiadł w cudzym ulu niezajętym, właściciel może domagać się wydania roju za zwrotem kosztów.\n"
                 "\n\n2)\n"
                 "     Z dniem 15 lipca 2006 r. na podstawie wyroku Trybunału Konstytucyjnego z dnia 15 marca 2005 r. sygn. akt "
                 "K 9/04 (Dz. U. poz. 462).\n")

    def _tekst(self, strona, slowa):
        return eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))

    def test_kc_przypis_tk_przy_art_179_a_nie_przy_art_182(self):
        przed = eli.pdf_layout_do_tekstu(self.KC_STRONA)       # bez współrzędnych — dawny błąd
        self.assertIn("kosztów. 2) Z dniem 15 lipca 2006", przed)
        t = self._tekst(self.KC_STRONA, self.KC_SLOWA)
        art179 = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 179"))
        art182 = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 182"))
        self.assertIn("Art. 179. (utracił moc)\n[przypis 2)] Z dniem 15 lipca 2006 r.", art179)
        self.assertIn("K 9/04", art179)
        self.assertNotIn("K 9/04", art182)
        self.assertTrue(art182.rstrip().endswith("za zwrotem kosztów."), art182)
        self.assertNotIn("\n2)\n", t)

    def test_kp_samotny_odsylacz_za_numerem_punktu(self):
        # k.p. DU 2026/1245 s. 11, art. 22¹ § 1: „14)" stoi tuż za „6)" (bbox), a `-layout` drukuje je wiersz wyżej
        slowa = [(51.0, 630.555640, 59.356680, 641.581360, "6)"), (59.28, 630.176320, 67.917840, 637.349680, "14)"),
                 (72.024, 630.555640, 106.475640, 641.581360, "przebieg"),
                 (51.0, 612.675640, 59.356680, 623.701360, "5)"), (72.024, 612.675640, 121.415640, 623.701360, "kwalifikacje")]
        strona = ("4)          wykształcenie;\n5)          kwalifikacje zawodowe;\n      14)\n"
                  "6)          przebieg dotychczasowego zatrudnienia.\n"
                  "      § 2. Pracodawca żąda podania danych osobowych.\n\n\n"
                  "14)\n        W brzmieniu ustalonym przez art. 1 pkt 2 ustawy, o której mowa w odnośniku 10.\n")
        t = self._tekst(strona, slowa)
        self.assertIn("5) kwalifikacje zawodowe;\n6) przebieg dotychczasowego zatrudnienia.\n[przypis 14)] W brzmieniu", t)
        self.assertNotIn("zawodowe; 14)", t)

    def test_cyfry_przypisu_nie_zabieraja_cyfr_liczby(self):
        # DU 2025/1016 s. 9 („0,01" + „2)") i DU 2026/218 s. 13 („152,61" + „3)"): dotąd „0," i „152,"
        slowa = [(465.5023, 507.28802, 481.833877, 519.851489, "0,01"), (481.9178, 507.41228, 487.097394, 515.787926, "2)"),
                 (82.224, 469.018, 115.224, 482.302, "152,61"), (115.22, 467.98636, 121.97732, 476.88664, "3)"),
                 (129.38, 469.018, 137.996, 482.302, "zł")]
        strona = ("1)     152,613) zł za 1000 l benzyn silnikowych;\n"
                  "      Materiały paszowe i mieszanki paszowe,                 0,012)\n")
        przed = eli.pdf_layout_do_tekstu(strona)
        self.assertIn("152, zł", przed)
        t = self._tekst(strona, slowa)
        self.assertIn("1) 152,61 zł za 1000 l", t)
        self.assertIn("paszowe, 0,01", t)
        self.assertIn("[przypis 3)]", t)
        self.assertIn("[przypis 2)]", t)
        self.assertNotIn("[przypis 613)]", t)
        self.assertNotIn("[przypis 012)]", t)

    def test_cyfry_w_jednym_slowie_to_tekst(self):
        # DU 2026/1391 s. 15 („SpO2),") i DU 2025/1016 s. 5 („(E561)"): jedno słowo bbox, jedna wysokość — nie odsyłacz
        slowa = [(278.4446, 710.0701, 307.0546, 723.5401, "SpO2),"), (177.3, 686.0, 220.0, 698.6, "(E561)")]
        strona = "– wysycenie hemoglobiny tlenem (saturacja, SpO2),\n      Wermikulit (E561)       3000\n"
        self.assertIn("(saturacja, SpO,", eli.pdf_layout_do_tekstu(strona))        # dawny błąd
        t = self._tekst(strona, slowa)
        self.assertIn("(saturacja, SpO2),", t)
        self.assertIn("Wermikulit (E561) 3000", t)
        self.assertNotIn("[przypis", t)

    def test_dwa_odsylacze_obok_siebie(self):
        # k.p.c. DU 2026/468 s. 14, art. 49 § 1: „§ 1." „6)" „7)" — drugi stoi za pierwszym; dotąd „7) Niezależnie…"
        slowa = [(147.728, 338.798, 156.728, 352.082, "1."), (156.86, 337.76636, 163.61732, 346.66664, "6)"),
                 (167.06, 337.76636, 173.81732, 346.66664, "7)"), (177.14, 338.798, 233.78, 352.082, "Niezależnie")]
        strona = ("       Art. 49. § 1.6) 7) Niezależnie od przyczyn wymienionych w art. 48, sąd wyłącza sędziego.\n\n\n"
                  "6)\n     Utracił moc z dniem 9 czerwca 2020 r. (P 13/19).\n7)\n     Utracił moc z dniem 28 lutego 2022 r. (P 10/19).\n")
        self.assertIn("7) Niezależnie", eli.pdf_layout_do_tekstu(strona))      # dawny błąd
        t = self._tekst(strona, slowa)
        self.assertIn("Art. 49. § 1. Niezależnie od przyczyn", t)
        self.assertIn("[przypis 6)] Utracił moc z dniem 9 czerwca 2020 r.", t)
        self.assertIn("[przypis 7)] Utracił moc z dniem 28 lutego 2022 r.", t)

    def test_blok_przypisow_z_dokonczeniem_z_poprzedniej_strony(self):
        # k.p.c. DU 2026/468 s. 13–14: przypis 5 kończy się na s. 14 nad „6)", „7)"; dotąd całość szła do art. 50 § 2
        s13_slowa = [(300.0, 400.0, 360.0, 412.0, "niższej;"), (360.2, 399.5, 365.4, 406.7, "5)")]
        s13 = ("5)    w sprawach, w których w instancji niższej brał udział;5)\n\n\n"
               "5)\n     Utracił moc z dniem 30 lipca 2004 r. w zakresie, w jakim ogranicza wyłączenie do spraw, w których brał\n")
        s14_slowa = [(147.728, 338.798, 156.728, 352.082, "1."), (156.86, 337.76636, 163.61732, 346.66664, "6)"),
                     (167.06, 337.76636, 173.81732, 346.66664, "7)"), (177.14, 338.798, 233.78, 352.082, "Niezależnie")]
        s14 = ("       Art. 49. § 1.6) 7) Niezależnie od przyczyn wymienionych w art. 48, sąd wyłącza sędziego.\n"
               "       Art. 50. § 2. Strona powinna uprawdopodobnić, że przyczyna wyłączenia stała się jej znana.\n\n\n"
               "     udział w instancji bezpośrednio niższej (SK 19/02).\n"
               "6)\n     Utracił moc z dniem 9 czerwca 2020 r. (P 13/19).\n7)\n     Utracił moc z dniem 28 lutego 2022 r. (P 10/19).\n")
        s13_ozn = eli._pdf_oznacz_odsylacze(s13, [(250.0, 400.0, 299.0, 412.0, "niższej"),
                                                  (299.1, 399.5, 304.4, 406.7, "brał"), (304.5, 399.5, 309.4, 406.7, "udział;")])
        t = eli.pdf_layout_do_tekstu(s13_ozn + "\f" + eli._pdf_oznacz_odsylacze(s14, s14_slowa))
        art50 = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 50"))
        self.assertTrue(art50.rstrip().endswith("stała się jej znana."), art50)
        self.assertIn("[przypis 6)] Utracił moc z dniem 9 czerwca 2020 r.", t)
        self.assertIn("brał udział w instancji bezpośrednio niższej (SK 19/02).", t)

    def test_bez_wspolrzednych_dawna_regula(self):
        # zeszyty 1990–2011 i OCR: bez słów bbox odsyłacz rozpoznaje `_PDF_ODSYLACZ` — wynik jak dotąd
        strona = "§ 1.3) Minister ustala opłatę.\n\n3) Dodany przez § 1 pkt 1 rozporządzenia.\n"
        self.assertEqual(eli._pdf_oznacz_odsylacze(strona, []), strona)
        self.assertIn("[przypis 3)] Dodany przez", eli.pdf_layout_do_tekstu(strona))

    def test_cytowany_przypis_w_obwieszczeniu_zostaje(self):
        # DU 2025/151 s. 1: obwieszczenie cytuje przypis „„2) Tekst Programu…" — po cudzysłowie to nie odsyłacz
        slowa = [(100.0, 300.0, 104.0, 312.0, "„"), (104.1, 299.5, 109.3, 306.7, "2)"), (112.0, 300.0, 140.0, 312.0, "Tekst")]
        self.assertEqual(eli._pdf_odsylacze_bbox(slowa), [])

    def test_punkt_z_odsylaczem_to_nowy_akapit(self):
        # k.p. DU 2026/1245 art. 94 pkt 2b: „2b)25) przeciwdziałać…" dotąd doklejało się do pkt 2a
        strona = ("2a)  organizować pracę w ustalonym z góry tempie;\n"
                  "2b)25) przeciwdziałać dyskryminacji w zatrudnieniu;\n\n\n"
                  "25) W brzmieniu ustalonym przez art. 1 pkt 3 ustawy, o której mowa w odnośniku 6.\n")
        t = eli.pdf_layout_do_tekstu(strona)
        self.assertIn("tempie;\n2b) przeciwdziałać dyskryminacji", t)
        self.assertIn("[przypis 25)] W brzmieniu", t)

    def test_przypis_w_zalaczniku_nie_z_poczatku_aktu(self):
        # DU 2026/1391 s. 42: „Grupa krwi i czynnik Rh1)" w formularzu załącznika dostawał przypis 1 aktu ze s. 1
        raw = ("§ 1. Rozporządzenie określa wzór.1)\n\n1) Minister Zdrowia kieruje działem administracji rządowej – zdrowie.\n"
               "\f§ 2. Rozporządzenie wchodzi w życie po upływie 14 dni od dnia ogłoszenia.\n"
               "\f                    Załącznik nr 1\n\n7. Grupa krwi i czynnik Rh1)\n")
        t = eli.pdf_layout_do_tekstu(raw)
        self.assertEqual(t.count("Minister Zdrowia kieruje"), 1, t)
        self.assertIn("Rh\n[przypis 1)] (treści przypisu nie odnaleziono", t)
        # w treści aktu (przed załącznikiem) odsyłacz powtórzony na dalszej stronie dostaje przypis jak dotąd
        t = eli.pdf_layout_do_tekstu(raw.replace("Załącznik nr 1", "§ 3. Minister"))
        self.assertEqual(t.count("Minister Zdrowia kieruje"), 2, t)


class TestFrazaZIndeksemLiterowym202611(unittest.TestCase):
    def test_litera_indeksu_gornego_w_zapisie_unicode(self):
        # k.p.c. art. 130¹ᵃ, k.p. art. 18³ᵉ — dotąd „Nie znaleziono frazy"
        txt = ("Art. 130 1. § 1. (utracił moc)\n\nArt. 130 1a. (uchylony)\n\nArt. 130 2. § 1. Pismo wniesione przez adwokata.\n\n"
               "Art. 18 3e. § 1. Skorzystanie przez pracownika z uprawnień.\n\nArt. 18 3f. § 1. W postępowaniach.")
        for fraza, poczatek in (("art. 130¹ᵃ", "Art. 130 1a. (uchylony)"), ("art. 130(1a)", "Art. 130 1a. (uchylony)"),
                                ("art. 130_1a", "Art. 130 1a. (uchylony)"), ("art. 18³ᵉ", "Art. 18 3e. § 1."),
                                ("art. 130¹", "Art. 130 1. § 1.")):
            spans = eli._fragmenty(txt, fraza)
            self.assertEqual(len(spans), 1, fraza)
            self.assertTrue(txt[spans[0][0]:spans[0][1]].startswith(poczatek), fraza)
            self.assertNotIn("Art. 130 2.", txt[spans[0][0]:spans[0][1]], fraza)
        self.assertEqual(eli._klucz_frazy("art. 130¹ᵃ"), "130^1a")
        # HTML: „Art. 130<sup>1</sup><sup>a</sup>." → „Art. 130 1 a."
        self.assertEqual(len(eli._fragmenty("Art. 130 1 a.\n\n§ 1.\n\nTreść.\n\nArt. 130 2.\n\nX", "art. 130¹ᵃ")), 1)


class TestPdfNieNadpisuje202611(unittest.TestCase):
    def test_istniejacy_plik_odmowa_przed_pobraniem(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            plik = pathlib.Path(d) / "akt.pdf"
            plik.write_bytes(b"MOJ DOKUMENT")
            pobrane = []
            kod, out, err = _kod(["tekst", "DU", "2026", "795", "--pdf", str(plik)],
                                 lambda path, params=None, soft=False: pobrane.append(path) or {},
                                 _get_bytes=lambda *a, **k: pobrane.append("pdf") or b"%PDF")
            self.assertEqual(kod, 2)
            self.assertIn("już istnieje", err)
            self.assertIn("nie nadpisuję", err)
            self.assertEqual(pobrane, [])                     # nic nie pobrano
            self.assertEqual(plik.read_bytes(), b"MOJ DOKUMENT")

    def test_nowy_plik_zapisany_wylacznie(self):
        import tempfile
        meta = {"texts": [{"type": "T", "fileName": "D20260795L.pdf"}]}

        def get(path, params=None, soft=False):
            return {} if path.endswith("/references") else meta
        with tempfile.TemporaryDirectory() as d:
            plik = pathlib.Path(d) / "nowy.pdf"
            with mock.patch.object(eli, "_nowszy_tj", return_value=None), \
                    mock.patch.object(eli, "_ostrzezenia", return_value=[]):
                kod, out, err = _kod(["tekst", "DU", "2026", "795", "--pdf", str(plik)], get,
                                     _get_bytes=lambda *a, **k: b"%PDF-1.7")
            self.assertEqual(kod, 0, err)
            self.assertEqual(plik.read_bytes(), b"%PDF-1.7")
            # plik powstał w trakcie pobierania — tryb „xb" też odmawia
            with mock.patch.object(eli, "_nowszy_tj", return_value=None), \
                    mock.patch.object(eli, "_ostrzezenia", return_value=[]), \
                    mock.patch.object(eli.os.path, "lexists", return_value=False):
                kod, out, err = _kod(["tekst", "DU", "2026", "795", "--pdf", str(plik)], get,
                                     _get_bytes=lambda *a, **k: b"INNY")
            self.assertEqual(kod, 2)
            self.assertIn("już istnieje", err)
            self.assertEqual(plik.read_bytes(), b"%PDF-1.7")


class TestHtmlBrakiINumeracja202611(unittest.TestCase):
    def test_naglowek_spoza_numeracji(self):
        # k.p.c. t.j. DU 2024/1568 (HTML i /struct): art. 15 ma nagłówek „Art. 1522."
        txt = "Art. 14.\n\n(uchylony)\n\nPrzepis wstępny\n\nArt. 1522.\n\n§ 1.\n\nSąd właściwy…\n\nArt. 16.\n\n§ 1. Sąd rejonowy"
        self.assertEqual(eli._fragmenty(txt, "art. 15")[:0], [])
        self.assertEqual(eli._hity_naglowka(txt, "art. 15"), [])
        self.assertEqual(eli._naglowek_poza_numeracja(txt, "art. 15"), "Art. 1522.")
        # zwykły brak (artykuł uchylony z numeracji, art. 14¹ za art. 14) — bez podpowiedzi
        self.assertEqual(eli._naglowek_poza_numeracja("Art. 14.\n\nX\n\nArt. 14 1.\n\nY\n\nArt. 16.\n\nZ", "art. 15"), "")
        self.assertEqual(eli._naglowek_poza_numeracja("Art. 14.\n\nX\n\nArt. 16.\n\nZ", "art. 15"), "")

    def test_patrz_oryginal_ostrzega_a_strict_blokuje(self):
        # t.j. DU 2023/2190: załącznik nr 1 (diety za granicą) w HTML API to tylko odsyłacz „patrz oryginał"
        html = ("<p>§ 1. Rozporządzenie określa wysokość diet.</p><h2>Załącznik nr 1 - Wysokość diety</h2>"
                "<div><a href='https://api.sejm.gov.pl/eli/acts//text.pdf'>patrz oryginał</a></div>")

        def get(path, params=None, soft=False):
            if path.endswith("/text.html"):
                return html
            return {} if path.endswith("/references") else {"texts": []}
        with mock.patch.object(eli, "_ostrzezenia", return_value=[]), mock.patch.object(eli, "_nowszy_tj", return_value=None):
            kod, out, err = _kod(["tekst", "DU", "2023", "2190", "--fragment", "Załącznik nr 1"], get)
            self.assertEqual(kod, 0, err)
            self.assertIn("1× zamiast treści stoi odsyłacz API „patrz oryginał”", out)
            kod, out, err = _kod(["tekst", "DU", "2023", "2190", "--fragment", "§ 1"], get)
            self.assertNotIn("patrz oryginał", out)
            kod, out, err = _kod(["--strict", "tekst", "DU", "2023", "2190"], get)
            self.assertEqual(kod, 2)
            self.assertIn("NIEKOMPLETNY", err)

    def test_bom_przed_naglowkiem_strony(self):
        # DU 2025/1016 s. 10: „\ufeff Dziennik Ustaw – 10 – Poz. 1016" wchodziło w treść tabeli
        raw = "– olejów i tłuszczów 0,05\n\f\ufeff Dziennik Ustaw – 10 – Poz. 1016\n\n5 DDT (suma izomerów)\n"
        t = eli.pdf_layout_do_tekstu(raw)
        self.assertNotIn("Dziennik Ustaw", t)
        self.assertIn("5 DDT", t)


class TestOcrParagrafWarianty202611(unittest.TestCase):
    """Nagłówki „§" odczytane przez OCR ze śmieciem przed numerem, z przecinkiem albo z „3"/„5" zamiast „§" — zamiana tylko,
    gdy numer N potwierdzają oba sąsiednie pewne nagłówki (§ N−1 tuż przed, § N+1 tuż za). Wiersze z surowego OCR."""

    def _popraw(self, strona):
        return eli._ocr_popraw_paragrafy([strona], [0])[0]

    def test_warianty_potwierdzone_z_obu_stron(self):
        # DU 2000 poz. 313 s. 1 i 4, DU 1998 poz. 875 s. 2, DU 1993 poz. 58 s. 2 (obraz strony: „§ 2.", „§ 12.")
        przyklady = [("§ 1. Rozporządzenie określa:\n5 2, Ilekroć w rozporządzeniu jest mowa o:\n§ 3. 1. Pracodawca jest",
                      "§ 2. Ilekroć w rozporządzeniu"),
                     ("§ 15. Wartości sił\n3 16. Dopuszczalne jest ręczne przetaczanie\n§ 17. 1. Przenoszenie", "§ 16. Dopuszczalne"),
                     ("§ 11. Zebranie dokonuje wyboru\n„8 12, 1. Głosowanie tajne odbywa się\n§ 13. 1. Na każdego", "§ 12. 1. Głosowanie"),
                     ("§16. 1. Opalanie tusz\n. 817. 1. Urządzenie do mechanicznego\n§18. Wysokość stosów", "§ 17. 1. Urządzenie")]
        for strona, oczekiwane in przyklady:
            wynik = self._popraw(strona)
            self.assertIn(oczekiwane, wynik, strona)
            self.assertEqual(wynik, self._popraw(wynik))      # idempotentne

    def test_bez_potwierdzenia_bez_zmian(self):
        for strona in ("§ 1. Rozporządzenie określa:\n5 2, Ilekroć w rozporządzeniu\n§ 4. Pracodawca",   # brak § 3 za nim
                       "§ 3. Ustala się stawki:\n3 5. Pszenica 15,00\n§ 4. Stawki obowiązują",      # wiersz tabeli w § 3
                       "Art. 5. Przepis\n„8 12, 1. Głosowanie\n§ 13. Na każdego",                # przed nim artykuł, nie § 11
                       "§ 2. Treść\n3 3. Wpisy w rejestrze\n§ 4. Dalej"):                           # „3 3." bez śmiecia i z „3" — N=3
            wynik = self._popraw(strona)
            if strona.startswith("§ 2. Treść"):
                self.assertIn("§ 3. Wpisy", wynik)            # oba sąsiedzi potwierdzają — to jest § 3
            else:
                self.assertEqual(wynik, strona)


class TestPrzypisyBezUtraty202611(unittest.TestCase):
    def test_przypis_bez_odsylacza_nie_znika(self):
        # DU 2025/609 (formularz): przypis rozpoznany pod tekstem strony, do którego żaden odsyłacz nie trafił
        raw = ("§ 1. Wniosek zawiera dane.\n\n"
               "9) Załączany dokument należy zaznaczyć X. Dodany przez § 1 pkt 1 rozporządzenia.\n")
        t = eli.pdf_layout_do_tekstu(raw)
        # etykieta mówi, że odsyłacza nie ustalono (202611b) — dotąd „[przypis 9)] …", jak przypis tego akapitu
        self.assertIn("[przypis 9) — bez odsyłacza w treści tej strony; nie przypisuj go do sąsiedniego przepisu] "
                      "Załączany dokument należy zaznaczyć X.", t)

    def test_blok_po_odstepie_nie_bierze_tresci(self):
        # wcięty wiersz wielką literą (podpis) albo nagłówek jednostki pod odstępem to treść, nie dokończenie przypisu
        slowa = [(100.0, 300.0, 140.0, 312.0, "ogłoszenia."), (140.1, 299.5, 145.3, 306.7, "1)")]
        for srodek in ("              Minister Zdrowia: A. Nowak\n", "      § 5. Rozporządzenie wchodzi w życie.\n"):
            strona = ("§ 4. Termin liczy się od dnia ogłoszenia.1)\n\n\n" + srodek +
                      "1)\n     Minister Zdrowia kieruje działem administracji rządowej – zdrowie.\n")
            t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
            self.assertIn(srodek.strip()[:20], t)


class TestDwaOdsylaczeSzerokaPrzerwa202611b(unittest.TestCase):
    """C: drugi odsyłacz za pierwszym przy szerszej przerwie (wiersz justowany). Współrzędne z `-bbox` k.p.c. DU 2026/468 s. 1."""

    ART1_SLOWA = [(96.50, 304.00, 117.42, 317.28, "Art."), (120.38, 304.00, 129.38, 317.28, "1."),
                  (129.50, 302.97, 136.26, 311.87, "2)"), (142.10, 302.97, 148.86, 311.87, "3)"),
                  (150.86, 304.00, 187.45, 317.28, "Kodeks"), (194.77, 304.00, 260.77, 317.28, "postępowania")]
    ART1 = ("       Art. 1.2) 3) Kodeks postępowania cywilnego normuje postępowanie sądowe w sprawach cywilnych.\n"
            "       Art. 2. § 1. Do rozpoznawania spraw cywilnych powołane są sądy powszechne.\n\n\n"
            "2)\n     Utracił moc z dniem 17 lipca 2000 r. na podstawie wyroku Trybunału Konstytucyjnego sygn. akt SK. 12/99.\n"
            "3)\n     Utracił moc z dniem 11 września 2017 r. na podstawie wyroku Trybunału Konstytucyjnego sygn. akt K 10/17.\n")

    def test_kpc_art_1_dwa_odsylacze_przerwa_5_84_pt(self):
        self.assertEqual(eli._pdf_odsylacze_bbox(self.ART1_SLOWA), [("1.", "2", False), ("2)", "3", True)])
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(self.ART1, self.ART1_SLOWA))
        art1 = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 1"))
        art2 = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 2"))
        self.assertIn("Art. 1. Kodeks postępowania cywilnego normuje", art1)
        self.assertNotIn("3) Kodeks", t)
        self.assertIn("[przypis 2)] Utracił moc z dniem 17 lipca 2000 r.", art1)
        self.assertIn("[przypis 3)] Utracił moc z dniem 11 września 2017 r.", art1)
        self.assertNotIn("K 10/17", art2)
        self.assertNotIn("bez odsyłacza", t)

    def test_nie_kazde_N_obok_odsylacza_to_drugi_odsylacz(self):
        # „1)" zwykłą czcionką tuż za odsyłaczem (góra prawie ta sama) i „7)" tej samej wysokości 14 pt dalej — tekst
        slowa = [(147.728, 338.798, 156.728, 352.082, "1."), (156.86, 337.76636, 163.61732, 346.66664, "6)"),
                 (166.0, 338.798, 175.0, 352.082, "1)"), (190.0, 337.76636, 196.75, 346.66664, "7)")]
        self.assertEqual(eli._pdf_odsylacze_bbox(slowa), [("1.", "6", False)])


class TestPunktObwieszczeniaToNiePrzypis202611b(unittest.TestCase):
    """A: punkt wyliczenia obwieszczenia Marszałka Sejmu na dole strony („5) art. 48 ustawy … który stanowi: „Art. 48. Ustawa
    wchodzi w życie…”") — DU 2026/1473 s. 3, DU 2026/795, 1315, 1342, 1395. Układ `-layout` jak w PDF."""

    RAW = ("                                     OBWIESZCZENIE\n"
           "                              MARSZAŁKA SEJMU RZECZYPOSPOLITEJ POLSKIEJ\n"
           "                    w sprawie ogłoszenia jednolitego tekstu ustawy o Państwowej Straży Pożarnej\n\n"
           "     1. Na podstawie art. 16 ust. 1 ustawy ogłasza się w załączniku do niniejszego obwieszczenia jednolity\n"
           "tekst ustawy z dnia 24 sierpnia 1991 r. o Państwowej Straży Pożarnej.\n"
           "     2. Podany w załączniku do niniejszego obwieszczenia tekst jednolity ustawy nie obejmuje:\n"
           "1)   art. 12 ustawy z dnia 12 września 2025 r. o zmianie niektórych ustaw (Dz. U. poz. 1366), który stanowi:\n"
           "          „Art. 12. Ustawa wchodzi w życie z dniem następującym po dniu ogłoszenia.”;\n\n"
           "2)   art. 48 ustawy z dnia 31 lipca 2026 r. o zmianie ustawy o opiece nad dziećmi w wieku do lat 3 (Dz. U.\n"
           "     poz. 1123), który stanowi:\n\n"
           "          „Art. 48. Ustawa wchodzi w życie z dniem 1 stycznia 2028 r.”.\n\n"
           "                                                         Marszałek Sejmu: W. Czarzasty\n"
           "\fZałącznik do obwieszczenia Marszałka Sejmu Rzeczypospolitej Polskiej z dnia 4 września 2026 r.\n\n"
           "                                        USTAWA\n"
           "      Art. 1. Ustawa reguluje ochronę przeciwpożarową.4)\n\n\n"
           "4)   Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2025 r. poz. 1.\n")

    def test_punkt_wyliczenia_zostaje_w_obwieszczeniu(self):
        t = eli.pdf_layout_do_tekstu(self.RAW)
        self.assertIn("» 2) art. 48 ustawy z dnia 31 lipca 2026 r.", t)
        self.assertIn("» „Art. 48. Ustawa wchodzi w życie z dniem 1 stycznia 2028 r.”.", t)
        self.assertNotIn("[przypis 2)]", t)
        # prawdziwy przypis pod „Załącznik do obwieszczenia" działa jak dotąd
        self.assertIn("Art. 1. Ustawa reguluje ochronę przeciwpożarową.\n[przypis 4)] Zmiany tekstu jednolitego", t)
        # i nie karmi ostrzeżenia o przepisach aktu, które jeszcze nie obowiązują
        self.assertEqual(eli._ostrzezenie_przyszle(t, False, dzis="2026-11-13"), [])

    def test_bez_obwieszczenia_regula_bloku_przypisow_bez_zmian(self):
        # ta sama strona bez „Załącznik do obwieszczenia" — reguła bloku przypisów jak dotąd (punkt nie jest chroniony)
        raw = self.RAW.split("\f")[0]
        self.assertIn("[przypis 2) — bez odsyłacza w treści tej strony; nie przypisuj go do sąsiedniego przepisu] art. 48 "
                      "ustawy", eli.pdf_layout_do_tekstu(raw))

    def test_potwierdzony_odsylacz_albo_wielka_litera_to_przypis(self):
        blok = [(0, "2) art. 48 ustawy z dnia 31 lipca 2026 r.", [])]
        self.assertTrue(eli._pdf_punkt_obwieszczenia(blok, ["2"], "tekst obwieszczenia"))
        self.assertFalse(eli._pdf_punkt_obwieszczenia(blok, ["2"], "tekst" + eli._ODS_POTW + "2) obwieszczenia"))
        self.assertFalse(eli._pdf_punkt_obwieszczenia([(0, "1) Niniejsze obwieszczenie dotyczy…", [])], ["1"], "x"))


class TestPrzypisBezOdsylacza202611b(unittest.TestCase):
    """B: przypis bez dopasowanego odsyłacza ma etykietę „nie wiadomo, której jednostki", nie rozcina wyliczenia, a ostrzeżenia
    o przypisach fragmentu nie biorą go za przypis tej jednostki (k.p.c. DU 2026/468 przypis 3 przy art. 2; DU 2026/1473 przypis 15)."""

    RAW = ("      Art. 2. § 1.2) Sąd orzeka w sprawach:\n"
           "1)    rodzinnych;\n"
           "2)    opiekuńczych;\n\n\n"
           "2)    W brzmieniu ustalonym przez art. 1 ustawy z dnia 9 marca 2026 r. (Dz. U. poz. 400).\n"
           "3)    Utracił moc z dniem 11 września 2017 r. na podstawie wyroku Trybunału Konstytucyjnego z dnia 11 września\n"
           "      2017 r. sygn. akt K 10/17 (Dz. U. poz. 1727).\n"
           "\f3)    pracy;\n"
           "4)    ubezpieczeń społecznych.\n"
           "      § 2. Sprawy rozpoznaje sąd powszechny.\n"
           "      Art. 3. Strony obowiązane są dawać wyjaśnienia.\n")

    def test_etykieta_i_miejsce_za_wyliczeniem(self):
        t = eli.pdf_layout_do_tekstu(self.RAW)
        etykieta = "[przypis 3) — bez odsyłacza w treści tej strony; nie przypisuj go do sąsiedniego przepisu] Utracił moc"
        self.assertIn("2) opiekuńczych;\n3) pracy;\n4) ubezpieczeń społecznych.\n" + etykieta, t)
        self.assertIn("K 10/17 (Dz. U. poz. 1727).\n§ 2. Sprawy rozpoznaje", t)
        self.assertNotIn("\n[przypis 3)]", t)

    def test_ostrzezenie_tk_osobno_z_zastrzezeniem(self):
        t = eli.pdf_layout_do_tekstu(self.RAW)
        uwagi = eli._tk_w_przypisach("\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 2")))
        tekst = "\n".join(uwagi)
        self.assertNotIn("WYROK TK W PRZYPISIE TEGO FRAGMENTU", tekst)
        self.assertIn("NIEPRZYPISANYM DO JEDNOSTKI", tekst)
        self.assertIn("K 10/17", tekst)         # informacja o wyroku nie ginie
        # przypis dopasowany do odsyłacza — jak dotąd
        self.assertIn("WYROK TK W PRZYPISIE TEGO FRAGMENTU",
                      "\n".join(eli._tk_w_przypisach("Art. 1. X\n[przypis 3)] Utracił moc na podstawie wyroku Trybunału "
                                                     "Konstytucyjnego sygn. akt K 10/17.")))

    def test_przepisy_przyszle_naglowek_rozdzialu_i_odnosniki(self):
        bez = eli._przypis_bez_odsylacza("5", "Wejdzie w życie z dniem 1 stycznia 2028 r.")
        przyszle, _ = eli._zmiany_w_tekscie("Art. 1. X\n" + bez, dzis="2026-11-13")
        self.assertEqual(len(przyszle), 1)
        self.assertTrue(przyszle[0][1].startswith("przypis nieprzypisany do jednostki 5)"), przyszle)
        self.assertNotIn("przepis z przypisem", przyszle[0][1])
        uwagi = "\n".join(eli._ostrzezenie_przyszle("Art. 1. X\n" + bez, False, dzis="2026-11-13"))
        self.assertIn("NIE wiadomo, którego przepisu dotyczy", uwagi)
        self.assertIn("od 2028-01-01: przypis nieprzypisany do jednostki 5)", uwagi)
        # sam przypis bez odsyłacza nie daje listy przepisów, które jeszcze nie obowiązują, ani zdania o przepisie „przy nim"
        for tekst_ujednolicony in (False, True):
            uwagi = "\n".join(eli._ostrzezenie_przyszle("Art. 1. X\n" + bez, tekst_ujednolicony, dzis="2026-11-13"))
            self.assertNotIn("PRZEPISY, KTÓRE JESZCZE NIE OBOWIĄZUJĄ", uwagi)
            self.assertNotIn("przy którym stoi taki przypis, jeszcze NIE obowiązuje", uwagi)
            self.assertNotIn("Na dziś obowiązuje brzmienie", uwagi)
        # obok przypisu dopasowanego — oba bloki osobno, licznik tylko dopasowanych
        uwagi = eli._ostrzezenie_przyszle("Art. 1. X\n[przypis 4)] Wejdzie w życie z dniem 1 marca 2027 r.\n" + bez, False,
                                          dzis="2026-11-13")
        self.assertIn("UWAGA — PRZEPISY, KTÓRE JESZCZE NIE OBOWIĄZUJĄ (dziś 2026-11-13): 1", uwagi)
        # pod nagłówkiem rozdziału nie jest przypisem tego rozdziału
        txt = "Rozdział 8\n\nZakwaterowanie\n" + bez + "\n\nArt. 74. Strażakowi przysługuje.\n\nArt. 75. Dalej."
        self.assertEqual(eli._naglowki_nadrzedne(txt, txt.index("Art. 75.")), [])
        # treść znajduje odesłanie „…w odnośniku 15", ale fragment nie bierze go za swój przypis
        txt = ("Art. 1. X\n[przypis 6)] W brzmieniu ustalonym przez ustawę, o której mowa w odnośniku 15.\n"
               + eli._przypis_bez_odsylacza("15", "Ustawa wejdzie w życie z dniem 1 marca 2027 r."))
        self.assertEqual(eli._przypisy_aktu(txt)["15"], "Ustawa wejdzie w życie z dniem 1 marca 2027 r.")
        self.assertEqual(eli._przypisy_aktu("[przypis 15)] A\n" + eli._przypis_bez_odsylacza("15", "B"))["15"], "A")

    def test_przypis_nieprzypisany_ostrzega_a_strict_blokuje(self):
        # 202611c: dotąd --strict przepuszczał przypis bez odsyłacza (kod 0) — a jego odsyłacz mógł zostać w treści jako „N)"
        t = eli.pdf_layout_do_tekstu(self.RAW)
        meta = {"ELI": "DU/2026/468", "legalStatusDate": "2026-03-25", "textHTML": False,
                "displayAddress": "Dz.U. 2026 poz. 468", "texts": [{"fileName": "D20260468L.pdf", "type": "T"}]}

        def get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else meta
        patche = dict(_tekst_z_pdf=lambda *a, **k: (t, "https://api.sejm.gov.pl/x.pdf", ""), _dzis=lambda: "2026-11-13")
        kod, out, err = _kod(["tekst", "DU", "2026", "468", "--fragment", "art. 2"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertIn("NIEPRZYPISANYM DO JEDNOSTKI", out)
        self.assertNotIn("W PRZYPISIE TEGO FRAGMENTU", out)
        self.assertIn("UWAGA: w tym fragmencie są miejsca odczytane z PDF niepewnie (1)", out)
        self.assertIn("przypis 3) bez odsyłacza (art. 2) — odsyłacz mógł zostać w treści jako „3)”", out)
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "468", "--fragment", "art. 2"], get, **patche)
        self.assertEqual(kod, 2)
        self.assertIn("BŁĄD (strict)", err)
        self.assertEqual(out, "")
        # fragment bez takiego miejsca — strict przepuszcza
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "468", "--fragment", "art. 3"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertNotIn("odczytane z PDF niepewnie", out)


class TestBezWspolrzednych202611b(unittest.TestCase):
    """D: `pdftotext -bbox` nie dał stron — dotąd po cichu działała dawna reguła kształtu („152,61" + „3)" → „152,")."""

    RAW = ("Art. 37m. 1. Stawka opłaty paliwowej wynosi:\n"
           "1)     152,613) zł za 1000 l benzyn silnikowych;\n\n\n"
           "3)     Aktualne stawki opłaty paliwowej ogłasza minister.\n\f")
    SLOWA = [(82.224, 469.018, "152,61", 115.224, 482.302), (115.22, 467.98636, "3)", 121.97732, 476.88664),
             (129.38, 469.018, "zł", 137.996, 482.302)]

    def _pdftotext(self, bbox):
        return lambda args, pdf_bytes: (bbox if "-bbox" in args else self.RAW if "-layout" in args else "")

    def test_pusty_bbox_oznacza_brak_wspolrzednych(self):
        with mock.patch.object(eli, "_pdftotext", side_effect=self._pdftotext("")):
            raw, notki, info = eli.pdf_do_tekstu_z_notkami(b"%PDF")
        self.assertEqual(info.get("bez_wspolrzednych"), (0, 1))
        self.assertIn("152, zł", eli.pdf_layout_do_tekstu(raw))      # dawna reguła — dlatego ostrzeżenie
        # zeszyt 2000–2011 (indeksy z bbox) — też; 1990–1999 (OCR, indeksy=False) współrzędnych nie używa
        with mock.patch.object(eli, "_pdftotext", side_effect=self._pdftotext("")):
            self.assertIn("bez_wspolrzednych", eli.pdf_do_tekstu_z_notkami(b"%PDF", zeszyt=True)[2])
            self.assertEqual(eli.pdf_do_tekstu_z_notkami(b"%PDF", zeszyt=True, indeksy=False)[2], {})

    def test_bbox_z_ta_sama_liczba_stron_bez_flagi(self):
        with mock.patch.object(eli, "_pdftotext", side_effect=self._pdftotext(_bbox([(595.0, 842.0, self.SLOWA)]))):
            raw, notki, info = eli.pdf_do_tekstu_z_notkami(b"%PDF")
        self.assertNotIn("bez_wspolrzednych", info)
        self.assertIn("1) 152,61 zł za 1000 l", eli.pdf_layout_do_tekstu(raw))
        # inna liczba stron niż w `-layout` — oznaczanie pominięte i flaga
        with mock.patch.object(eli, "_pdftotext", side_effect=self._pdftotext(_bbox([(595.0, 842.0, self.SLOWA)] * 2))):
            self.assertEqual(eli.pdf_do_tekstu_z_notkami(b"%PDF")[2].get("bez_wspolrzednych"), (2, 1))

    def test_tekst_ostrzega_a_strict_blokuje(self):
        meta = {"ELI": "DU/2026/218", "legalStatusDate": "2026-02-19", "textHTML": False,
                "displayAddress": "Dz.U. 2026 poz. 218", "texts": [{"fileName": "D20260218L.pdf", "type": "T"}]}

        def get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else meta
        patche = dict(_get_bytes=lambda *a, **k: b"%PDF", pdftotext_dostepny=lambda: True,
                      _pdftotext=self._pdftotext(""), _nowszy_tj=lambda *a, **k: None, _ostrzezenia=lambda *a, **k: [])
        kod, out, err = _kod(["tekst", "DU", "2026", "218", "--fragment", "art. 37m"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertIn("UWAGA: pdftotext -bbox nie zwrócił współrzędnych słów", out)
        self.assertIn("liczby stojące tuż przed przypisem mogą być obcięte", out)
        self.assertIn("--pdf plik.pdf", out)
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "218", "--fragment", "art. 37m"], get, **patche)
        self.assertEqual(kod, 2)
        self.assertIn("BŁĄD (strict)", err)
        self.assertIn("NIE zostały potwierdzone współrzędnymi", err)
        self.assertEqual(out, "")
        # ze współrzędnymi — bez ostrzeżenia, strict przepuszcza
        patche["_pdftotext"] = self._pdftotext(_bbox([(595.0, 842.0, self.SLOWA)]))
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "218", "--fragment", "art. 37m"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertNotIn("współrzędnych słów", out)
        self.assertIn("1) 152,61 zł za 1000 l", out)


class TestPrzypisyStronyPdf202611c(unittest.TestCase):
    """Wada 1: blok przypisów u dołu strony nierozpoznany, treść przypisów zostaje w przepisie. Współrzędne słów skopiowane
    13.11.2026 z `pdftotext -bbox` urzędowych PDF; układ `-layout` jak w PDF (skrócone wiersze treści)."""

    # k.p. DU 2026/1245 s. 95: „Art. 302[1].62)" — w bbox „302" „[1]" „." „62)"; „." to osobne słowo, w `-layout` bez spacji
    KP_SLOWA = [(82.39392, 88.46564, 97.1148, 99.49136, "zm."), (97.104, 88.06632, 105.73784, 95.23968, "60)"),
                (92.064, 138.74564, 109.53384, 149.77136, "302."), (109.46, 138.36632, 118.09784, 145.53968, "61)"),
                (92.064, 208.70564, 107.124, 219.73136, "302"), (107.18, 208.32632, 114.73784, 215.49968, "[1]"),
                (114.74, 208.70564, 117.23, 219.73136, "."), (117.14, 208.32632, 125.77784, 215.49968, "62)"),
                (128.3, 208.70564, 133.28, 219.73136, "§"),
                (51.0, 728.194, 58.998, 734.836, "60)"), (51.0, 750.27, 58.998, 756.912, "61)"),
                (51.0, 772.23, 58.998, 778.872, "62)")]
    KP_STRONA = ("     Art. 301. § 1. Szczególne uprawnienia normują przepisy ustawy o obronie Ojczyzny (Dz. U. z 2025 r. poz. 825,\n"
                 "z późn. zm.60)).\n"
                 "     Art. 302.61) Do okresu zatrudnienia wlicza się okres służby w Policji.\n"
                 "     Art. 302[1].62) § 1. Do okresu zatrudnienia wlicza się okresy prowadzenia działalności.\n"
                 "      § 2. Do okresu zatrudnienia wlicza się okresy:\n"
                 "1)    wykonywania przez osobę fizyczną umowy zlecenia,\n"
                 "2)    wykonywania przez osobę fizyczną umowy agencyjnej.\n"
                 "     § 7. Do okresu zatrudnienia wlicza się udokumentowany okres wykonywania pracy zarobkowej na innej podstawie niż\n"
                 "stosunek pracy przebyty za granicą.\n\n\n\n\n"
                 "60) Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2025 r. poz. 1014 i 1080.\n"
                 "61) W brzmieniu ustalonym przez art. 1 pkt 1 ustawy z dnia 26 września 2025 r. o zmianie ustawy – Kodeks pracy\n\n"
                 "    (Dz. U. poz. 1423), która weszła w życie z dniem 1 stycznia 2026 r.\n"
                 "62)\n"
                 "    Dodany przez art. 1 pkt 2 ustawy, o której mowa w odnośniku 61.\n")

    def test_kp_art_302_1_przypisy_60_62_pod_tekstem_strony(self):
        przed = eli.pdf_layout_do_tekstu(self.KP_STRONA)        # bez potwierdzenia „62)" — dawny wynik
        self.assertIn("\n60) Zmiany tekstu jednolitego", przed)
        self.assertEqual(eli._pdf_odsylacze_bbox_pelne(self.KP_SLOWA)[2], (".", "62", False, True, True))
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(self.KP_STRONA, self.KP_SLOWA))
        self.assertNotIn("nie odnaleziono", t)
        self.assertIn("z późn. zm.).\n[przypis 60)] Zmiany tekstu jednolitego", t)
        self.assertIn("Art. 302 1. § 1. Do okresu zatrudnienia wlicza się okresy prowadzenia działalności.\n"
                      "[przypis 62)] Dodany przez art. 1 pkt 2", t)
        self.assertIn("[przypis 61)] W brzmieniu ustalonym przez art. 1 pkt 1 ustawy z dnia 26 września 2025 r. o zmianie "
                      "ustawy – Kodeks pracy (Dz. U. poz. 1423)", t)
        art = "\n".join(t[s:e] for s, e in eli._fragmenty(t, "art. 302¹"))
        self.assertTrue(art.rstrip().endswith("przebyty za granicą."), art)
        # punkty wyliczenia zostają
        self.assertEqual(len(re.findall(r"(?m)^\d+\) ", t)), 2)

    def test_nawias_za_odsylaczem_i_kropka_bez_sklejenia(self):
        # DU 2026/55 s. 2: „zm." „1)" „)" „2)" — drugi odsyłacz stoi za „)" sklejonym z pierwszym
        slowa = [(279.01424, 255.74564, 292.53992, 266.77136, "zm."), (292.49, 255.36632, 297.88784, 262.53968, "1)"),
                 (297.89, 255.74564, 301.20668, 266.77136, ")"), (301.25, 255.36632, 306.64784, 262.53968, "2)"),
                 (309.17, 255.74564, 316.36112, 266.77136, "w")]
        wynik = eli._pdf_oznacz_odsylacze("Prawo telekomunikacyjne (Dz. U. poz. 852, z późn. zm.1))2) w zezwolenie\n", slowa)
        self.assertEqual(wynik.count(eli._ODS_POTW), 2)
        # „." NIESKLEJONE w bbox ze słowem przed nim — bez spacji przed sobą w tekście nie jest szukane
        self.assertEqual(eli._pdf_odsylacze_bbox_pelne([(100.0, 300.0, 110.0, 311.0, "a"), (115.0, 300.0, 117.5, 311.0, "."),
                                                        (117.6, 299.6, 123.0, 306.8, "5)")])[0][3], False)

    def test_przypis_z_numerem_rzymskim(self):
        # k.k.s. DU 1999/930 s. 1 (tekst ujednolicony): „1)I)" — dotąd blok szedł do art. 1 § 3 z punktami 1) i 2)
        strona = ("                                    Kodeks karny skarbowy1)\n\n"
                  "         Art. 1. § 3. Nie popełnia przestępstwa skarbowego sprawca czynu zabronionego.\n\n\n"
                  "1)I)\n"
                  "       Niniejsza ustawa w zakresie swojej regulacji:\n"
                  "       1) realizuje postanowienia dyrektywy (UE) 2017/1371;\n"
                  "       2) wdraża dyrektywę Rady (UE) 2022/2523.\n"
                  "  I)\n"
                  "       Odnośnik do tytułu ustawy w brzmieniu ustalonym przez art. 142 pkt 1 ustawy z dnia 6 listopada 2024 r.\n")
        t = eli.pdf_layout_do_tekstu(strona)
        self.assertIn("Kodeks karny skarbowy\n[przypis 1)] Niniejsza ustawa w zakresie swojej regulacji: 1) realizuje", t)
        self.assertIn("I) Odnośnik do tytułu ustawy", t)
        self.assertTrue(t.rstrip().endswith("sprawca czynu zabronionego."), t)
        self.assertNotIn("\n1) realizuje", t)

    def test_blok_przypisow_bez_pustego_wiersza(self):
        # DU 2013/714 s. 1: „1)" tuż pod ust. 3, bez odstępu; dotąd przypis 1 szedł do ust. 3
        slowa = [(60.0, 120.0, 75.0, 131.0, "zm."), (75.2, 119.5, 80.6, 126.7, "1)")]
        strona = ("    Art. 1. W ustawie o zasadach prowadzenia polityki rozwoju (Dz. U. z 2009 r. Nr 84, poz. 712,\n"
                  "z późn. zm.1)) wprowadza się następujące zmiany:\n"
                  "      1) projekt nie uzyskał minimum punktowego;\n\n"
                  "          3. Wyczerpanie środków nie może stanowić wyłącz-\n"
                  "      nej przesłanki wniesienia protestu.\n"
                  "1)\n"
                  "     Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2009 r. Nr 157, poz. 1241.\n")
        przed = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, []))
        self.assertIn("protestu. 1) Zmiany tekstu", przed)
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
        self.assertIn("wyłącznej przesłanki wniesienia protestu.", t)
        self.assertIn("[przypis 1)] Zmiany tekstu jednolitego", t)
        self.assertNotIn("protestu. 1)", t)
        # „nie psuj": punkt wyliczenia „1) …" z wciętym ciągiem dalszym na dole strony, bez frazy przypisu — zostaje treścią
        punkt = strona.split("1)\n     Zmiany")[0] + "1)    projekt podlega ponownej ocenie,\n      jeżeli wniesiono protest.\n"
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(punkt, slowa))
        self.assertIn("\n1) projekt podlega ponownej ocenie, jeżeli wniesiono protest.", t)

    def test_dwa_przypisy_bez_pustego_wiersza(self):
        # blok bez odstępu z DWOMA przypisami — oba wychodzą z treści, nie tylko ostatni
        slowa = [(60.0, 120.0, 75.0, 131.0, "zm."), (75.2, 119.5, 80.6, 126.7, "1)"),
                 (60.0, 160.0, 75.0, 171.0, "pracy"), (75.2, 159.5, 80.6, 166.7, "2)")]
        strona = ("    Art. 1. W ustawie o zasadach polityki rozwoju (Dz. U. z 2009 r. Nr 84, poz. 712, z późn. zm.1)) wprowadza się:\n"
                  "          3. Minister właściwy do spraw pracy2) ogłasza wykaz.\n"
                  "1)\n"
                  "     Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2009 r. Nr 157, poz. 1241.\n"
                  "2)\n"
                  "     Obecnie minister właściwy do spraw pracy.\n")
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
        self.assertIn("[przypis 1)] Zmiany tekstu jednolitego", t)
        self.assertIn("[przypis 2)] Obecnie minister", t)
        self.assertIn("3. Minister właściwy do spraw pracy ogłasza wykaz.\n", t)

    def test_numer_przypisu_rozbity_na_dwa_wiersze(self):
        # DU 2014/900 s. 1: „1" ⏎ „ )     Niniejszą…", odsyłacze „ustaw 1), 2)" ze spacją w `-layout`
        slowa = [(293.13792, 184.60788, 318.07776, 193.57188, "ustaw"), (318.72, 182.65416, 325.73784, 188.48616, "1),"),
                 (327.96048, 182.65416, 333.35832, 188.48616, "2)"), (66.95988, 210.64176, 84.44964, 219.60576, "Art."),
                 (51.0, 737.982, 54.0, 743.382, "1"), (54.0, 739.893, 56.997, 747.993, ")"),
                 (51.0, 760.0, 54.0, 765.4, "2"), (54.0, 761.9, 56.997, 770.0, ")")]
        strona = ("                                                   innych ustaw 1), 2)\n\n"
                  "    Art. 1. W ustawie z dnia 16 lutego 2007 r. o zapasach ropy naftowej wprowadza się następujące zmiany:\n\n"
                  "1)      w art. 2 pkt 2 otrzymuje brzmienie;\n\n"
                  "1\n )     Niniejszą ustawą zmienia się ustawy: ustawę z dnia 10 kwietnia 1997 r. – Prawo energetyczne.\n"
                  "2\n )     Niniejsza ustawa dokonuje w zakresie swojej regulacji wdrożenia dyrektywy Rady 2009/119/WE.\n")
        przed = eli.pdf_layout_do_tekstu(strona)
        self.assertIn("1 ) Niniejszą ustawą", przed)
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
        self.assertIn("innych ustaw\n[przypis 1)] Niniejszą ustawą zmienia się ustawy", t)
        self.assertIn("[przypis 2)] Niniejsza ustawa dokonuje", t)
        self.assertIn("\n1) w art. 2 pkt 2 otrzymuje brzmienie;", t)       # punkt wyliczenia zostaje
        self.assertNotIn("1 )", t)

    def test_dokonczenie_przypisu_na_stronie_bez_odsylaczy(self):
        # DU 2019/1500 s. 2–3: przypis 1 urywa się „…z dniem 25 maja 2018", dokończenie „r. Przepisy…" na s. 3 szło do ust. 4b
        s2 = ("      3. Minister jest administratorem danych (Dz. U. z 2016 r. poz. 922)1).>\n\n"
              "1)\n     Ustawa utraciła moc z dniem 25 maja 2018 r. z wyjątkiem art. 1, która weszła w życie z dniem 25 maja 2018\n")
        s3 = ("       4b. Centralna Informacja udostępnia bezpłatnie, w ogólnodostępnych sieciach\n"
              "teleinformatycznych, listę podmiotów, wobec których w dziale 6 rejestru\n\n\n"
              "     r. Przepisy ustawy wymienione w zdaniu pierwszym utraciły moc z dniem 6 lutego 2019 r.\n"
              "\f"
              "przedsiębiorców wpisano informację o ogłoszeniu upadłości.\n")
        slowa2 = [(300.0, 400.0, 320.0, 411.0, "922)"), (320.1, 399.5, 330.0, 406.7, "1).>")]
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(s2, [(300.0, 400.0, 320.0, 411.0, "922)"),
                                                                    (320.1, 399.5, 325.0, 406.7, "1)")]) + "\f" + s3)
        self.assertIn("w dziale 6 rejestru przedsiębiorców wpisano informację", t)
        self.assertIn("z dniem 25 maja 2018 r. Przepisy ustawy wymienione w zdaniu pierwszym utraciły moc", t)
        # „nie psuj": przypis poprzedniej strony zakończony kropką — wcięty blok małą literą zostaje w treści
        s2_kropka = s2.replace("25 maja 2018\n", "25 maja 2018 r.\n")
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(s2_kropka, [(300.0, 400.0, 320.0, 411.0, "922)"),
                                                                           (320.1, 399.5, 325.0, 406.7, "1)")]) + "\f" + s3)
        self.assertIn("rejestru r. Przepisy ustawy", t)
        self.assertNotIn(slowa2[0][4] + "1)", t)

    def test_dokonczenie_od_liczby_tylko_po_urwanym_przypisie(self):
        # k.p. DU 2018/917 s. 85–86: dokończenie przypisu 8 zaczyna się od „1566, 1909…"; dotąd przypis 9 „nie odnaleziony"
        s85 = ("      Art. 175. Minister Pracy i Polityki Socjalnej8) określi zasady.\n\n\n"
               "8)\n     Obecnie minister właściwy do spraw pracy (Dz. U. z 2017 r. poz. 888, 1086,\n")
        s86 = ("1)     ubezpieczonej – matce dziecka (Dz. U. z 2017 r. poz. 1778, z późn. zm.9));\n"
               "2)     ubezpieczonym – ojcu dziecka;\n\n\n\n"
               "     1566, 1909, 2180 i 2494 oraz z 2018 r. poz. 138 i 379), która weszła w życie z dniem 1 kwietnia\n"
               "     1999 r.\n"
               "9)\n     Zmiany tekstu jednolitego wymienionej ustawy zostały ogłoszone w Dz. U. z 2018 r. poz. 106.\n")
        o85 = eli._pdf_oznacz_odsylacze(s85, [(200.0, 100.0, 240.0, 111.0, "Socjalnej"), (240.1, 99.5, 245.0, 106.7, "8)")])
        o86 = eli._pdf_oznacz_odsylacze(s86, [(200.0, 100.0, 214.0, 111.0, "zm."), (214.1, 99.5, 219.0, 106.7, "9)")])
        t = eli.pdf_layout_do_tekstu(o85 + "\f" + o86)
        self.assertIn("[przypis 8)] Obecnie minister właściwy do spraw pracy (Dz. U. z 2017 r. poz. 888, 1086, 1566, 1909", t)
        self.assertIn("[przypis 9)] Zmiany tekstu jednolitego", t)
        self.assertIn("2) ubezpieczonym – ojcu dziecka;\n", t + "\n")
        self.assertNotIn("nie odnaleziono", t)
        # „nie psuj": wcięty ustęp „2. …" pod odstępem to treść, nie dokończenie urwanego przypisu
        o86_ust = o86.replace("     1566, 1909, 2180 i 2494 oraz z 2018 r. poz. 138 i 379), która weszła w życie z dniem 1 kwietnia\n"
                              "     1999 r.\n", "     2. Przepis § 1 stosuje się odpowiednio.\n")
        self.assertNotEqual(o86_ust, o86)
        t = eli.pdf_layout_do_tekstu(o85 + "\f" + o86_ust)
        self.assertIn("\n2. Przepis § 1 stosuje się odpowiednio.", t)
        self.assertIn("poz. 888, 1086,\n", t)


class TestOdsylaczZeSpacja202611c(unittest.TestCase):
    """Wada 2: odsyłacz, który w PDF stoi tuż za słowem, a `-layout` oddziela go spacją, zostawał w tekście jako „N)"."""

    # DU 2026/1473 s. 51: „Pożarnej" kończy się na 415,66, „15)" zaczyna się na 415,99 (mniejsze, podniesione)
    SLOWA = [(377.5592, 307.37564, 415.6562, 318.40136, "Pożarnej"), (415.99, 306.99632, 424.62784, 314.16968, "15)"),
             (51.0, 761.19, 58.998, 767.832, "15)"), (65.04, 761.5, 90.0, 771.0, "Tytuł")]
    STRONA = ("                                                       Rozdział 8\n"
              "                                  Zakwaterowanie strażaków Państwowej Straży Pożarnej 15)\n"
              "      Art. 74. 1. Strażakowi przysługuje prawo do zakwaterowania.\n"
              "15)  oświadczenie strażaka o skorzystaniu z pomocy finansowej;\n\n\n"
              "15)   Tytuł rozdziału w brzmieniu ustalonym przez art. 4 pkt 3 ustawy, o której mowa w odnośniku 7.\n")

    def test_tytul_rozdzialu_z_odsylaczem_po_spacji(self):
        przed = eli.pdf_layout_do_tekstu(self.STRONA)
        self.assertIn("Pożarnej 15)", przed)
        self.assertIn("bez odsyłacza", przed)
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(self.STRONA, self.SLOWA))
        self.assertIn("Zakwaterowanie strażaków Państwowej Straży Pożarnej\n[przypis 15)] Tytuł rozdziału", t)
        self.assertNotIn("bez odsyłacza", t)
        self.assertIn("\n15) oświadczenie strażaka o skorzystaniu", t)     # punkt wyliczenia zostaje

    def test_bez_numeru_przypisu_mniejsza_czcionka_spacja_zostaje(self):
        # numer „15)" na dole tej samej wielkości co tekst (punkt wyliczenia, nie przypis) — „15)" w tytule zostaje
        slowa = self.SLOWA[:2] + [(51.0, 761.19, 58.998, 772.2, "15)")]
        self.assertFalse(eli._pdf_odsylacze_bbox_pelne(slowa)[0][4])
        wynik = eli._pdf_oznacz_odsylacze(self.STRONA, slowa)
        self.assertIn("Pożarnej 15)", wynik)
        self.assertNotIn(eli._ODS_POTW, wynik)

    def test_dwa_odsylacze_z_przecinkiem(self):
        # k.p.c. DU 2021/1805 s. 1: „Art." „1." „1)," „2)" — dotąd „Art. 1., 2) Kodeks…" i przypis 2 „bez odsyłacza"
        slowa = [(106.1, 303.758, 115.1, 317.042, "1."), (115.22, 302.72636, 123.95, 311.62664, "1),"),
                 (129.5, 302.72636, 136.25732, 311.62664, "2)"), (139.0, 303.758, 180.0, 317.042, "Kodeks"),
                 (56.64, 627.08416, 62.03784, 632.91616, "1)"), (56.64, 673.19416, 62.03784, 679.02616, "2)")]
        strona = ("       Art. 1.1), 2) Kodeks postępowania cywilnego normuje postępowanie sądowe.\n\n\n"
                  "1)\n     Utracił moc z dniem 17 lipca 2000 r. (SK. 12/99).\n"
                  "2)\n     Utracił moc z dniem 11 września 2017 r. (K 10/17).\n")
        przed = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, []))
        self.assertIn("Art. 1., 2) Kodeks", przed)
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
        self.assertIn("Art. 1. Kodeks postępowania cywilnego normuje postępowanie sądowe.\n[przypis 1)] Utracił moc z dniem "
                      "17 lipca 2000 r. (SK. 12/99).\n[przypis 2)] Utracił moc z dniem 11 września 2017 r.", t)
        self.assertNotIn("bez odsyłacza", t)

    def test_kwoty_i_kody_bez_zmian(self):
        # „nie psuj": cyfry liczby przed odsyłaczem (DU 2026/218, DU 2025/1016) — jak w TestOdsylaczeZeWspolrzednych202611
        slowa = [(82.224, 469.018, 115.224, 482.302, "152,61"), (115.22, 467.98636, 121.97732, 476.88664, "3)"),
                 (129.38, 469.018, 137.996, 482.302, "zł"), (51.0, 772.23, 55.998, 778.872, "3)")]
        strona = "1)     152,613) zł za 1000 l benzyn silnikowych;\n\n\n3)     Aktualne stawki ogłasza minister.\n"
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa))
        self.assertIn("1) 152,61 zł za 1000 l", t)
        self.assertIn("[przypis 3)] Aktualne stawki", t)


class TestStopkaZData202611c(unittest.TestCase):
    """Wada 3: data wydruku w stopce tekstu ujednoliconego w starym zapisie („18.01.2021") zostawała w treści każdej strony
    (DU 2019/1500, DU 2020/920 i 9 innych PDF typu U wygenerowanych 04.2018–01.2022)."""

    RAW = ("©Kancelaria Sejmu                                                               s. 1/3\n\n"
           "      Art. 2. 3. Minister określi sposób współpracy sądów rejestrowych i wójtów w tych sprawach,\n\n\n"
           "                                                                            18.01.2021\n"
           "\f©Kancelaria Sejmu                                                                                  s. 2/3\n\n"
           "w szczególności wskazując sposób postępowania sądów.\n"
           "       Art. 3. Rejestr obejmuje podmioty wpisane do Rejestru z dnia 18.01.2021 r.\n"
           "       Art. 3a. Minister utrzymuje system teleinformatyczny (Dz. U. z 2016 r. poz. 922)1).\n\n"
           "1)\n     Zmiana wymienionego rozporządzenia została ogłoszona w Dz. Urz. UE L 28 z 04.02.2016, str. 18.\n"
           "                                                                                               18.01.2021\n"
           "\f©Kancelaria Sejmu                                                                          s. 3/3\n\n"
           "       Art. 4. Ustawa wchodzi w życie z dniem ogłoszenia.\n\n\n"
           "                                                                                       18.01.2021\n")

    def test_data_stopki_usunieta_i_zdanie_sklejone(self):
        info = {}
        t = eli.pdf_layout_do_tekstu(self.RAW, info)
        self.assertNotIn("\n18.01.2021", t)
        self.assertIn("współpracy sądów rejestrowych i wójtów w tych sprawach, w szczególności wskazując", t)
        self.assertIn("[przypis 1)] Zmiana wymienionego rozporządzenia została ogłoszona w Dz. Urz. UE L 28 z 04.02.2016, "
                      "str. 18.\n", t + "\n")
        self.assertEqual(info.get("data_wydruku"), "2021-01-18")
        self.assertNotIn("niepewne_miejsca", info)

    def test_prawdziwa_data_w_tresci_zostaje(self):
        t = eli.pdf_layout_do_tekstu(self.RAW)
        self.assertIn("z dnia 18.01.2021 r.", t)
        # data na końcu tylko 1 z 3 stron — to nie stopka; zostaje i daje ostrzeżenie (samodzielna data)
        raw = self.RAW.replace("                                                                                               18.01.2021\n", "") \
                      .replace("                                                                                       18.01.2021\n", "")
        info = {}
        t = eli.pdf_layout_do_tekstu(raw, info)
        self.assertIn("\n18.01.2021\n", t)
        self.assertEqual(info.get("niepewne_miejsca"), [("data", "18.01.2021", 1)])
        self.assertIsNone(eli._pdf_data_stopki(raw.split("\f")))

    def test_akt_jednostronicowy_data_zostaje_z_ostrzezeniem(self):
        # DU 2020/2374: data pod podpisem Prezydenta — bez powtórzenia na innych stronach to nie jest potwierdzona stopka
        strona = "      Art. 2. Ustawa wchodzi w życie z dniem 1 stycznia 2021 r.\n\n        Prezydent: A. Duda\n\n" \
                 + " " * 112 + "04.01.2021\n"
        self.assertIsNone(eli._pdf_data_stopki([strona]))
        info = {}
        t = eli.pdf_layout_do_tekstu(strona, info)
        self.assertIn("\n04.01.2021", t)
        self.assertEqual(info["niepewne_miejsca"], [("data", "04.01.2021", 1)])


class TestNiepewneMiejscaPdf202611c(unittest.TestCase):
    """Przypis bez treści, przypis bez odsyłacza i samodzielna data w tekście z PDF — dotąd tylko etykieta w tekście, bez
    ostrzeżenia; teraz UWAGA ze stroną PDF i jednostką, a --strict blokuje (kod 2), z --fragment — tylko w tym fragmencie."""

    RAW = ("      Art. 1. Ustawa reguluje ochronę danych.7)\n"
           "      Art. 2. Przepis ogólny.\n"
           "\f      Art. 3. Dalszy przepis.\n\n\n"
           "                                                                            2026-11-12\n\n"
           "                                                                                Zmiana\n"
           "\f      Art. 4. Ustawa wchodzi w życie z dniem ogłoszenia.\n\n\n"
           "9)    Dodany przez art. 1 ustawy z dnia 1 marca 2020 r. o zmianie ustawy.\n")

    def _kod(self, argv):
        def tekst_z_pdf(path, label, meta, info=None, ocr=None):
            return eli.pdf_layout_do_tekstu(self.RAW, info), "https://api.sejm.gov.pl/x.pdf", ""

        def get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else {
                "ELI": "DU/2026/1", "textHTML": False, "displayAddress": "Dz.U. 2026 poz. 1",
                "texts": [{"fileName": "D20260001L.pdf", "type": "T"}]}
        return _kod(argv, get, _tekst_z_pdf=tekst_z_pdf, _nowszy_tj=lambda *a, **k: None,
                    _ostrzezenia=lambda *a, **k: [], _dzis=lambda: "2026-11-13")

    def test_lista_miejsc_ze_stronami(self):
        info = {}
        t = eli.pdf_layout_do_tekstu(self.RAW, info)
        self.assertEqual(info["niepewne_miejsca"], [("brak_tresci", "7", 1), ("data", "2026-11-12", 2),
                                                    ("bez_odsylacza", "9", 3)])
        opisy = eli._niepewne_miejsca_pdf(t, None, info["niepewne_miejsca"])
        self.assertEqual(len(opisy), 3)
        self.assertTrue(opisy[0].startswith("przypis 7) bez treści (s. 1 PDF, art. 1)"), opisy)
        self.assertTrue(opisy[1].startswith("samodzielna data „2026-11-12” (s. 2 PDF, art. 3)"), opisy)
        self.assertTrue(opisy[2].startswith("przypis 9) bez odsyłacza (s. 3 PDF, art. 4)"), opisy)
        self.assertEqual(eli._niepewne_miejsca_pdf(t, eli._fragmenty(t, "art. 2"), info["niepewne_miejsca"]), [])
        self.assertEqual(eli._niepewne_miejsca_pdf(t, [], info["niepewne_miejsca"]), [])

    def test_tekst_ostrzega_strict_blokuje_tylko_we_fragmencie(self):
        kod, out, err = self._kod(["tekst", "DU", "2026", "1"])
        self.assertEqual(kod, 0, err)
        self.assertIn("UWAGA: w tym tekście są miejsca odczytane z PDF niepewnie (3)", out)
        self.assertIn("przypis 7) bez treści (s. 1 PDF, art. 1) — treści nie znaleziono pod tekstem strony", out)
        kod, out, err = self._kod(["--strict", "tekst", "DU", "2026", "1", "--fragment", "art. 3"])
        self.assertEqual(kod, 2)
        self.assertIn("samodzielna data „2026-11-12” (s. 2 PDF, art. 3)", err)
        self.assertEqual(out, "")
        kod, out, err = self._kod(["--strict", "tekst", "DU", "2026", "1", "--fragment", "art. 2"])
        self.assertEqual(kod, 0, err)
        self.assertNotIn("niepewnie", out)

    def test_przypis_wklejony_w_inny_artykul(self):
        # k.p. s. 95 bez współrzędnych (dawny wynik): zaślepki stoją przy art. 301 i 302, a treść przypisów — w art. 302¹ § 7;
        # ostrzeżenie dla fragmentu art. 302¹ musi wskazać wklejone „60) Zmiany tekstu…"
        self.RAW = TestPrzypisyStronyPdf202611c.KP_STRONA
        kod, out, err = self._kod(["tekst", "DU", "2026", "1", "--fragment", "art. 302¹"])
        self.assertEqual(kod, 0, err)
        self.assertIn("„60) …” w treści przepisu (s. 1 PDF, art. 302¹)", out)      # indeks górny jak w nagłówku (202611d)
        self.assertIn("„61) …” w treści przepisu", out)
        self.assertLess(out.index("ELI_TEXT_SOURCE_PDF="), out.index("odczytane z PDF niepewnie"))
        kod, out, err = self._kod(["--strict", "tekst", "DU", "2026", "1", "--fragment", "art. 302¹"])
        self.assertEqual(kod, 2)
        # punkt wyliczenia małą literą nie jest przypisem
        self.assertEqual(list(eli._PDF_PRZYPIS_W_TRESCI.finditer("1) w brzmieniu ustalonym przez ustawę; 2) dodany przez")), [])

    def test_tekst_bez_takich_miejsc_bez_ostrzezenia(self):
        self.RAW = "      Art. 1. Ustawa reguluje ochronę danych.\n      Art. 2. Przepis ogólny.\n"
        kod, out, err = self._kod(["--strict", "tekst", "DU", "2026", "1"])
        self.assertEqual(kod, 0, err)
        self.assertNotIn("niepewnie", out)


def _slowa_wiersza(y, x0, tekst, wys=11.0, cw=5.0):
    """Słowa jednego wiersza bbox (x0, y0, x1, y1, słowo) — szerokość znaku `cw`, wysokość `wys`."""
    out, x = [], x0
    for t in tekst.split():
        out.append((x, y, x + cw * len(t), y + wys, t))
        x += cw * len(t) + 3.0
    return out


class TestUkrytaWarstwa202611d(unittest.TestCase):
    """DU 2025/615 — strona tabeli to osadzony PDF z własnymi przypisami i nagłówkami, przycięty ramką; `pdftotext` daje obie
    kopie (widoczną i ukrytą), więc blok przypisów nie był rozpoznawany (28 przypisów w treści tabel). Strony z tekstem obróconym
    o 90° nie miały odsyłaczy potwierdzonych współrzędnymi („3.4" + „21)" → „3." i „przypis 421)")."""

    # s. 10: ukryta kopia przypisu 9 (13,7 pt, y = 757) nad widocznym (12,1 pt, y = 783); odsyłacz „9)" przy „45"
    SLOWA = (_slowa_wiersza(652.8, 57.98, "45", 13.8) + [(70.85, 652.46, 77.95, 661.34, "9)")]
             + _slowa_wiersza(652.8, 90.06, "Lakierowanie zębów 1/4", 13.8)
             + [(51.02, 757.05, 57.22, 765.81, "9)"), (53.85, 757.0, 59.43, 765.83, "9)")]
             + _slowa_wiersza(756.85, 68.18, "Ze zmianą wprowadzoną przez § 1 pkt 3", 13.74)
             + [(51.02, 783.23, 55.9, 791.11, "9)")]
             + _slowa_wiersza(782.92, 68.03, "Ze zmianą wprowadzoną przez § 1 pkt 3", 12.12))
    STRONA = ("      459)   Lakierowanie zębów 1/4                     23.1006              Świadczenie dotyczy wszystkich zębów\n\n\n"
              " 9)\n9)\n       Ze zmianą wprowadzoną przez § 1 pkt 3\n\n"
              "9)\n       Ze zmianą wprowadzoną przez § 1 pkt 3\n")

    def test_ukryta_kopia_przypisu_usunieta(self):
        przed = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(self.STRONA, self.SLOWA))
        self.assertIn("9) 9) Ze zmianą", przed)          # dawny wynik: przypis w treści tabeli
        strona, inne = eli._pdf_bez_ukrytych_przypisow(self.STRONA, self.SLOWA)
        self.assertEqual(inne, [])
        self.assertEqual(strona.count("Ze zmianą wprowadzoną"), 1)
        info = {}
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, self.SLOWA), info)
        self.assertIn("45 Lakierowanie zębów 1/4 23.1006 Świadczenie dotyczy wszystkich zębów\n"
                      "[przypis 9)] Ze zmianą wprowadzoną przez § 1 pkt 3", t)
        self.assertNotIn("niepewne_miejsca", info)

    def test_powtorzony_punkt_tabeli_tej_samej_wielkosci_zostaje(self):
        # s. 88: „3) sprawdzenie toru oddychania;" w dwóch wierszach tabeli — oba widoczne (ta sama wysokość), nic nie znika
        slowa = (_slowa_wiersza(186.4, 356.3, "3) sprawdzenie toru oddychania;", 13.8)
                 + _slowa_wiersza(715.8, 356.3, "3) sprawdzenie toru oddychania;", 13.8))
        self.assertEqual(eli._pdf_ukryta_warstwa(slowa), ([], []))
        strona = "      3) sprawdzenie toru oddychania;\n\n      Lp. 2\n      3) sprawdzenie toru oddychania;\n"
        self.assertEqual(eli._pdf_bez_ukrytych_przypisow(strona, slowa)[0], strona)
        # VAT DU 2017/1221 s. 310: to samo hasło w poz. 73 i 74, inna wysokość wiersza, ten sam początek — nie warstwa
        vat = (_slowa_wiersza(205, 240.0, "przeznaczone na nieodpłatne lub odpłatne", 13.3)
               + _slowa_wiersza(413, 240.0, "przeznaczone na nieodpłatne lub odpłatne", 10.8))
        self.assertEqual(eli._pdf_ukryta_warstwa(vat), ([], []))

    def test_naglowek_zdublowany_daje_ostrzezenie(self):
        # s. 63: „Załącznik nr 5" widoczny (12,5 pt, x = 488) i ukryty (16,8 pt, x = 436)
        slowa = _slowa_wiersza(70.5, 488.2, "Załącznik nr 5", 12.5) + _slowa_wiersza(83.3, 436.5, "Załącznik nr 5", 16.8)
        strona = "                                  Załącznik nr 5\n                             Załącznik nr 5\n"
        wynik, inne = eli._pdf_bez_ukrytych_przypisow(strona, slowa)
        self.assertEqual(wynik, strona)                   # nagłówka nie usuwamy — tylko ostrzeżenie
        self.assertEqual(inne, ["Załącznik nr 5"])
        info = {}
        t = eli.pdf_layout_do_tekstu("      Art. 1. Tekst.\n\f" + strona + "      Lp.  Nazwa\n", info,
                                     strony_flagi={1: [("warstwa", "Załącznik nr 5")]})
        self.assertEqual([o[:4] for o in info["niepewne_obszary"]], [("warstwa", "Załącznik nr 5", 2, 2)])
        opisy = eli._niepewne_miejsca_pdf(t, None, None, info["niepewne_obszary"])
        self.assertTrue(opisy[0].startswith("strona z ukrytą warstwą tekstu (s. 2 PDF"), opisy)
        self.assertEqual(eli._niepewne_miejsca_pdf(t, eli._fragmenty(t, "art. 1"), None, info["niepewne_obszary"]), [])

    def test_strona_obrocona_odsylacz_potwierdzony(self):
        # s. 40: tekst czytany od dołu do góry; „3.4" (x 476–493, y 767–783) i odsyłacz „21)" (x 478–487, y 756–767)
        slowa = [(476.36, 767.16, 493.29, 782.98, "3.4"), (478.03, 755.78, 487.21, 767.4, "21)"),
                 (476.36, 684.45, 493.29, 746.29, "Wstrząsarka"), (476.36, 640.0, 493.29, 680.0, "do"),
                 (535.2, 784.49, 543.08, 792.28, "21)"), (534.89, 765.78, 547.01, 775.28, "Ze")]
        slowa += [(300.0 + 20 * k, 100.0, 317.0 + 20 * k, 160.0, "słowo") for k in range(10)]
        w, h, obrocone = eli._pdf_obroc(595.276, 841.89, slowa)
        self.assertEqual((w, h), (841.89, 595.276))
        self.assertEqual(eli._pdf_odsylacze_bbox(slowa), [])        # pionowe współrzędne — dawniej brak odsyłacza
        self.assertIn(("3.4", "21", False), eli._pdf_odsylacze_bbox(obrocone))
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(
            "      3.421) Wstrząsarka do\n\n\n21)\n       Ze zmianą wprowadzoną przez § 1 pkt 5.\n", obrocone))
        self.assertIn("3.4 Wstrząsarka do\n[przypis 21)] Ze zmianą wprowadzoną", t)

    def test_strona_pozioma_bez_zmian(self):
        slowa = [(56.0 + 30 * k, 100.0 + 12 * (k % 3), 80.0 + 30 * k, 111.0 + 12 * (k % 3), "wyraz") for k in range(12)]
        self.assertIs(eli._pdf_obroc(595.0, 842.0, slowa)[2], slowa)
        obr = {}
        eli._pdf_slowa(_bbox([(595.0, 842.0, [(56.0, 100.0, "Art."), (90.0, 100.0, "1.")])]), obr)
        self.assertEqual(obr, {})

    def test_strona_obrocona_czytana_z_gory_na_dol(self):
        # obrót w drugą stronę (w próbie nie wystąpił): wiersz biegnie w dół strony, góra liter po prawej (większe x)
        slowa = [(476.0, 60.0, 493.0, 76.0, "3.4"), (482.0, 76.2, 491.0, 87.8, "21)"), (476.0, 92.0, 493.0, 150.0, "Wstrząsarka"),
                 (476.0, 154.0, 493.0, 170.0, "do"), (476.0, 174.0, 493.0, 230.0, "materiałów"),
                 (476.0, 234.0, 493.0, 310.0, "kapsułkowanych")]
        slowa += [(300.0 + 20 * k, 200.0, 317.0 + 20 * k, 260.0, "słowo") for k in range(10)]
        w, h, obrocone = eli._pdf_obroc(595.0, 842.0, slowa)
        self.assertEqual((w, h), (842.0, 595.0))
        self.assertEqual([s[4] for s in sorted(obrocone[:4])], ["3.4", "21)", "Wstrząsarka", "do"])   # kolejność czytania
        self.assertIn(("3.4", "21", False), eli._pdf_odsylacze_bbox(obrocone))

    def test_naglowek_poziomy_strony_obroconej(self):
        slowa = [(51.0, 46.14, 87.68, 59.61, "Dziennik"), (90.0, 46.14, 120.0, 59.61, "Ustaw"),
                 (509.0, 46.14, 526.5, 59.61, "Poz."), (529.3, 46.14, 544.3, 59.61, "615"),
                 (51.0, 28.19, 53.5, 41.66, "﻿"), (476.0, 120.0, 493.0, 700.0, "treść")]
        self.assertEqual(eli._pdf_naglowek_poziomy(841.89, slowa), 59.61)
        # słowo treści sięgające nagłówka — strona zostaje w całości
        self.assertIsNone(eli._pdf_naglowek_poziomy(841.89, slowa + [(300.0, 50.0, 317.0, 300.0, "długi")]))


class TestTabeleNiepewne202611d(unittest.TestCase):
    """tabela, w której w jednym wierszu zawija się kilka komórek — `-layout` przeplata ich słowa; teraz obszar niepewny z
    numerami stron PDF (UWAGA, --strict blokuje; z --fragment tylko gdy fragment zachodzi na tabelę)."""

    # VAT DU 2026/1263 s. 414 (układ `-layout`)
    VAT = ("    9     ex 12               Nasiona i owoce oleiste; ziarna, nasiona i owoce różne\n"
           "   10a    bez względu         Nawozy, środki poprawiające właściwości gleby, stymulatory\n"
           "          na CN               produkty pofermentacyjne, nawozowe produkty mikrobiologiczne\n"
           "   10b    bez względu         Środki ochrony roślin, o których mowa w ustawie z dnia 8 marca\n"
           "          na CN               2013 r. o środkach ochrony roślin\n")
    # DU 1998/1012 (OCR): zawinięta nazwa, wartości w pierwszym wierszu — „…na- szt. 4 2 1 czyń"
    NORMY = ("     1    Ręcznik                         szt.     6     2     2\n"
             "     2    Ścierka do wycierania na-       szt.     4     2     1\n"
             "          czyń\n"
             "     3    Koc                             szt.     2     4     2\n")

    def test_przemieszane_komorki_wykryte(self):
        for tabela in (self.VAT, self.NORMY):
            linie = tabela.split("\n")
            self.assertTrue(eli._pdf_tabela_niepewna(linie), tabela)
        self.assertIn("13 bez względu Wyroby medyczne medycznych", eli.pdf_layout_do_tekstu(
            "   13    bez względu   Wyroby medyczne\n         na CN         medycznych\n").replace("na CN ", ""))

    def test_tabela_czytana_dobrze_bez_alarmu(self):
        przyklady = [
            # zawinięta tylko ostatnia kolumna (VAT „poz. | PKWiU | nazwa")
            "   73    ex 58.13.10.0    Gazety drukowane lub na nośnikach (dyskach,\n"
            "                          taśmach i innych) – wyłącznie gazety drukowane –\n"
            "   74    ex 58.14.1       Czasopisma i pozostałe periodyki drukowane lub na\n"
            "                          nośnikach (dyskach, taśmach i innych) – wyłącznie\n"
            "   75    59.20.31.0       Nuty w formie drukowanej\n",
            # wartość w wierszu ciągu nazwy (DU 1998/895)
            "     I    Sposób zastosowania azbestu\n"
            "          l.        Powierzchnia pokryta masą natryskową\n"
            "                    z azbestem (torkret)........ .. .. .....            25\n"
            "          2.        Tynk zawierający azbest... .. .... .........        20\n"
            "          3.        Lekkie płyty izolacyjne z azbestem\n"
            "                    (gęstość objętościowa <1 000 kg/m3 ...........      15\n",
            # objaśnienie symboli wzoru (DU 2014/900)
            "          ZOr       –       minimalną wielkość zapasów obowiązkowych ropy naftowej lub paliw w zakresie ro-\n"
            "          py naftowej wyrażoną w jednostkach wagowych,\n"
            "          P–       wielkość przywozu ropy naftowej i ilość ropy naftowej wprowadzonej na rynek krajowy\n"
            "          z zapasów obowiązkowych w wyniku ich interwencyjnego uwolnienia w poprzednim roku kalendarzo-\n"
            "          G        –       ilość ropy naftowej wykorzystanej do produkcji paliw w poprzednim roku kalenda-\n"
            "          rzowym, wyrażoną w jednostkach wagowych,\n",
            # rozstrzelona proza (k.p.c. DU 2026/468 s. 166) i wyliczenie z odstępem po numerze (k.p. s. 13)
            "      § 4. W sprawach     o rozwiązanie      przysposobienia     prokurator       wytaczając\n"
            "powództwo pozywa przysposabiającego oraz przysposobionego.\n\n"
            "      Art. 454[1]. § 1.   Powództwo       wzajemne       o ustalenie    lub      zaprzeczenie\n"
            "macierzyństwa,      o ustalenie   lub    zaprzeczenie    ojcostwa,     a także    o ustalenie\n",
            "  1)   daty, godziny i minuty oraz miejsca przeprowadzenia badania;\n"
            "  2)   wyniku badania;\n  3)   danych osobowych pracownika:\n"
            "       a)   imienia i nazwiska,\n       b) numeru PESEL,\n       c)   daty urodzenia, płci, wzrostu,\n",
        ]
        for p in przyklady:
            self.assertEqual(eli._pdf_tabela_niepewna(p.split("\n")), set(), p)

    def test_obszar_fragment_i_strict(self):
        raw = ("      Art. 1. Stawki określa załącznik.\n      Art. 2. Rozporządzenie wchodzi w życie z dniem ogłoszenia.\n"
               "\f                                                          Załącznik\n" + self.VAT)
        info = {}
        t = eli.pdf_layout_do_tekstu(raw, info)
        self.assertEqual(t, eli.pdf_layout_do_tekstu(raw))         # znaczniki obszarów nie trafiają do tekstu
        self.assertFalse(eli._ZNACZNIKI.search(t))
        [(rodzaj, _, od, do, s, e)] = info["niepewne_obszary"]
        self.assertEqual((rodzaj, od, do), ("tabela", 2, 2))
        self.assertIn("bez względu", t[s:e])
        self.assertEqual(eli._niepewne_miejsca_pdf(t, eli._fragmenty(t, "art. 2"), None, info["niepewne_obszary"]), [])

        def tekst_z_pdf(path, label, meta, info=None, ocr=None):
            return eli.pdf_layout_do_tekstu(raw, info), "https://api.sejm.gov.pl/x.pdf", ""

        def get(path, params=None, soft=False):
            return {} if path.endswith("/references") else "" if path.endswith("/text.html") else {
                "ELI": "DU/2026/1", "textHTML": False, "displayAddress": "Dz.U. 2026 poz. 1",
                "texts": [{"fileName": "D20260001L.pdf", "type": "O"}]}
        patche = dict(_tekst_z_pdf=tekst_z_pdf, _nowszy_tj=lambda *a, **k: None, _ostrzezenia=lambda *a, **k: [],
                      _dzis=lambda: "2026-11-13")
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "1"], get, **patche)
        self.assertEqual(kod, 2)
        self.assertIn("tabela (s. 2 PDF, załącznik) — komórki zawinięte", err)
        kod, out, err = _kod(["--strict", "tekst", "DU", "2026", "1", "--fragment", "art. 2"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertNotIn("niepewnie", out)
        kod, out, err = _kod(["tekst", "DU", "2026", "1"], get, **patche)
        self.assertEqual(kod, 0, err)
        self.assertIn("UWAGA: w tym tekście są miejsca odczytane z PDF niepewnie (1)", out)

    def test_data_w_komorce_tabeli_bez_alarmu_daty(self):
        raw = ("      § 1. Ustala się terminy wypłat:\n"
               "      Lp.    Rodzaj świadczenia                       Termin wypłaty\n"
               "      1      Dodatek energetyczny                     31.03.2026\n"
               "      2      Dodatek osłonowy dla gospodarstw\n"
               "                                                      30.06.2026\n"
               "             domowych jednoosobowych\n"
               "      3      Dodatek węglowy                          30.09.2026\n"
               "      § 2. Rozporządzenie wchodzi w życie z dniem ogłoszenia.\n")
        info = {}
        eli.pdf_layout_do_tekstu(raw, info)
        self.assertNotIn("niepewne_miejsca", info)                # dotąd: „samodzielna data 30.06.2026"
        self.assertEqual([o[0] for o in info["niepewne_obszary"]], ["tabela"])
        # data na dole strony (stopka niepotwierdzona) — alarm zostaje
        info = {}
        eli.pdf_layout_do_tekstu("      Art. 1. Ustawa wchodzi w życie.\n\n" + " " * 70 + "04.01.2021\n", info)
        self.assertEqual(info["niepewne_miejsca"], [("data", "04.01.2021", 1)])


class TestNumeracjaOcr202611d(unittest.TestCase):
    """DU 1994/73 (OCR, dwa łamy) — okruch OCR w środku strony zamykał blok łamów (§ 46–48 przed § 40–45), nagłówek strony
    rozbity przez tesseracta wchodził w treść, „586." to § 86; luki i odwrócenia numeracji — ostrzeżenie."""

    def test_luki_i_odwrocenia(self):
        t = ("".join(f"§ {n}. Treść.\n" for n in range(1, 9)) + "§9. 1. W pomieszczeniach. § '0. 1. Podłoże.\n§ 11 . Pracownicy.\n"
             + "".join(f"§ {n}. Treść.\n" for n in range(12, 39))
             + "§ 39.1. Jest niedopuszczalne:\n§ 46. Do obsługi.\n§ 47. Pojemniki.\n§ 40: Chlorownie.\n§ 41 . Pomieszczenia.\n")
        opisy = [o for o, _, _ in eli._luki_numeracji(t)]
        self.assertEqual(opisy, ["§ 10 brak w odczycie (między § 9 a § 11)",
                                 "§ 40–45 brak w odczycie (między § 39 a § 46)",
                                 "§ 40 po § 47 — kolejność jednostek w odczycie"])
        s, e = eli._luki_numeracji(t)[0][1:]
        self.assertTrue(t[s:].startswith("§9. 1.") and t[s:e].endswith("§ 11 . Pracownicy."))
        self.assertEqual([o for o, _, _ in eli._luki_numeracji("Na podstawie art. 5:\n§ 3. Rozporządzenie wchodzi.\n§ 1. Określa.\n")],
                         ["§ 3 jako pierwszy paragraf — § 1–2 brak w odczycie"])

    def test_bez_falszywych_alarmow(self):
        t = ("Art. 1. W ustawie wprowadza się zmiany:\n1) art. 5 otrzymuje brzmienie:\n„Art. 5. § 1. Nowe.\n"
             "§ 2. Dalej.\nArt. 6. Także.\nArt. 9. Cytat bez otwarcia w wierszu.”;\n"     # cytat wieloakapitowy
             "Art. 2. § 1. Przepis.\n§ 2. Dalej.\n§ 2a. Dodany.\n§ 3. Koniec.\n"           # § w artykule, litera
             "Art. 3. Uchyla się.\nArt. 4–6. (uchylone)\nArt. 7. Wchodzi w życie.\n"        # zakres uchylonych
             "Załącznik\n§ 1. Regulamin.\n§ 2. Dalej.\n")                                    # § od nowa w załączniku
        self.assertEqual(eli._luki_numeracji(t), [])
        # odesłanie przeniesione do nowego wiersza („…stosując zasady określone w ⏎ § 11—14." — DU 1998/875) to nie nagłówek
        t = "".join(f"§ {n}. Treść.\n" for n in range(1, 18)) + "§ 11—14.\n§ 18. Dane.\n"
        self.assertEqual(eli._luki_numeracji(t), [])
        # akt zmieniający z OCR: cudzysłów odczytany jako „,," / `"` — maska cytatów nie działa, akt nie jest sprawdzany (DU 1995/504)
        t = ('Art. 1. W ustawie wprowadza się zmiany:\n1) art. 33 otrzymuje brzmienie:\n"Art. 33. Nadzór.\n'
             '2) dodaje się art. 34b w brzmieniu:\n,,Art. 34b. Organ.\nArt. 2. Ustawa wchodzi w życie.\n')
        self.assertEqual(eli._luki_numeracji(t), [])

    def test_okruch_ocr_nie_zamyka_bloku_lamow(self):
        # DU 1994/73 s. 4 (układ `-layout`): łamy po ok. 55 znaków, okruch „'. :" w lewym łamie, daleko od marginesu
        lewy = ["§ 38. 1. Ścieki z obiektów stosujących chlor powinny", "być odprowadzone po ich zneutralizowaniu.",
                "§ 39.1. Jest niedopuszczalne:", "1) magazynowanie i transportowanie razem z chlorem",
                "2) neutralizowanie chloru amoniakiem,", "3) pobieranie chloru z pojemnika, w którym ciśnienie",
                "spadło do 100 kPa przy temperaturze powyżej + 16°C", "§ 40. Chlorownie oraz magazyny, w których stosuje się",
                "§ 41. Pomieszczenia magazynu podchlorynu sodowego"]
        prawy = ["§ 46. Do obsługi i konserwacji chloratorów na pod-", "chloryn sodowy dopuszcza się obsługę dwuosobową,",
                 "§ 47. Pojemniki z podchlorynem sodowym należy skła-", "dać w odległości nie mniejszej niż 1 m od grzejników.",
                 "§ 48. Pojemniki z podchlorynem sodowym nie mogą", "być magazynowane i transportowane razem z materiałami",
                 "§ 49. 1. Magazyny podchlorynu sodowego, w których", "przechowuje się pojemniki większe niż 0,2 m3, powinny",
                 "§ 50. 1. Pomieszczenia do magazynowania wapna"]
        wiersze = [f"     {l:<59}{p}" for l, p in zip(lewy, prawy)]
        wiersze.insert(5, " " * 49 + "'. :")
        strona = "\n".join(wiersze)
        przed = eli._pdf_lamy(strona.replace("'. :", "'. X"), luz=8)     # wiersz z literą — jak dotąd zamyka blok
        self.assertLess(przed.index("§ 46"), przed.index("§ 40"))
        po = eli._pdf_lamy(strona, luz=8)
        self.assertLess(po.index("§ 41"), po.index("§ 46"))
        self.assertNotIn("'. :", po)
        self.assertEqual(eli._pdf_lamy(strona), eli._pdf_lamy(strona, luz=0))     # bez OCR — bez zmian
        self.assertIn("'. :", eli._pdf_lamy(strona))

    def test_naglowek_rozbity_przez_ocr(self):
        raw = "Dziennik Ustaw Nr 21 —\n\n274 —\n\nPoz. 73\n\nRozdział 12\n\n§87. 1. W pomieszczeniu."
        wynik = eli._pdf_bez_naglowkow_stron(raw)
        self.assertNotIn("Dziennik", wynik)
        self.assertIn("Rozdział 12", wynik)
        # zwykły wiersz zaczynający się od „Dziennik Ustaw" w treści (bez numeru pozycji w trzech wierszach) zostaje
        tresc = "Dziennik Ustaw publikuje się w formie elektronicznej\nna stronie urzędowej.\nDalej."
        self.assertEqual(eli._pdf_bez_naglowkow_stron(tresc), tresc)

    def test_paragraf_sklejony_z_piatka(self):
        strona = "§ 85. 1. Praca w pomieszczeniach.\nRozdział 12\n586. 1. Kwas siarkowy powinien być\n§87. 1. W pomieszczeniu"
        self.assertIn("§ 86. 1. Kwas siarkowy", eli._ocr_popraw_paragrafy([strona], [0])[0])
        for s in ("§ 2. Treść\n317. Wartość graniczna\n§ 4. Dalej",              # bez § 16 i § 18 — to nie § 17
                  "§ 85. Praca.\n586. 1. Kwas siarkowy\n§ 88. Dalej"):          # za nim nie § 87
            self.assertEqual(eli._ocr_popraw_paragrafy([s], [0])[0], s)

    def test_strony_zeszytu_od_poczatku_aktu(self):
        raw = "\f".join(["Inny akt\n\n12\n\nROZPORZĄDZENIE\n", "§ 1. Tekst.7)\n§ 2. Dalej.\n",
                         "§ 3. Koniec.\n\n13\n\nOBWIESZCZENIE\n"])
        start = []
        wyciety = eli._pdf_wytnij_akt(raw, 12, start=start)
        self.assertEqual(start, [0])
        info = {}
        eli.pdf_layout_do_tekstu(wyciety.split("\f", 1)[1], info, strona0=1)
        self.assertEqual(info["niepewne_miejsca"], [("brak_tresci", "7", 2)])


class TestDrobne202611d(unittest.TestCase):
    """Indeks górny w ostrzeżeniu, urwana notka pod datą wydruku, trzy odsyłacze z przecinkami, strona z notką nieprzycięta."""

    def test_indeks_gorny_w_ostrzezeniu(self):
        t = "Art. 302 1. § 1. Tekst.\n[przypis 62)] (treści przypisu nie odnaleziono na tej stronie PDF)\n"
        self.assertEqual(eli._jednostka_w_miejscu(t, t.index("[przypis")), "art. 302¹")
        t = "Art. 13 1a. Tekst.\nArt. 65a 65b. Wyliczenie.\n"
        self.assertEqual(eli._jednostka_w_miejscu(t, t.index("Tekst")), "art. 13¹ᵃ")
        self.assertEqual(eli._jednostka_w_miejscu(t, t.index("Wyliczenie")), "art. 65a")

    def test_urwana_notka_pod_data_wydruku(self):
        # DU 2026/1263 s. 25: data wydruku, pod nią początek notki urwanej na krawędzi strony
        strona1 = ("8)    towary mają być czasowo używane przez tego podatnika na terytorium kraju,\n"
                   "      niedłużej jednak niż przez 24 miesiące, pod warunkiem że import takich towarów\n\n\n"
                   + " " * 78 + "2026-11-12\n\n" + " " * 92 + "Zmiana\n" + " " * 92 + "(skreślenie\n"
                   + " " * 92 + "wyrazów) w pkt 9\n")
        strona2 = ("      z terytorium państwa trzeciego na terytorium kraju;\n      Art. 13. Przepis.\n\n\n"
                   + " " * 78 + "2026-11-12\n")
        strona3 = "      Art. 14. Dalej.\n\n\n" + " " * 78 + "2026-11-12\n"     # data wydruku potwierdzona na innych stronach
        info = {}
        t = eli.pdf_layout_do_tekstu(strona1 + "\f" + strona2 + "\f" + strona3, info)
        self.assertIn("pod warunkiem że import takich towarów z terytorium państwa trzeciego", t)
        self.assertNotIn("2026-11-12", t)
        self.assertIn("[margines: Zmiana (skreślenie wyrazów) w pkt 9 … (notka urwana na dole strony 1 PDF", t)
        self.assertNotIn("niepewne_miejsca", info)
        self.assertEqual([o[:4] for o in info["niepewne_obszary"]],
                         [("notka", "Zmiana (skreślenie wyrazów) w pkt 9", 1, 1)])
        # inna data niż data wydruku z pozostałych stron — bez zmian, zostaje alarm daty
        info = {}
        t = eli.pdf_layout_do_tekstu(strona1 + "\f" + strona2.replace("2026-11-12", "2026-11-13") + "\f"
                                     + strona3.replace("2026-11-12", "2026-11-13"), info)
        self.assertIn("\n2026-11-12\n", t)
        self.assertEqual(info["niepewne_miejsca"], [("data", "2026-11-12", 1)])
        # pod datą zwykła treść od lewego marginesu — to nie notka
        self.assertEqual(eli._pdf_notka_pod_data("Tekst\n" + " " * 70 + "2026-11-12\nDalszy tekst od marginesu\n",
                                                 "2026-11-12")[1], None)

    def test_trzy_odsylacze_z_przecinkami(self):
        slowa = [(106.1, 303.758, 115.1, 317.042, "1."), (115.22, 302.72636, 123.95, 311.62664, "1),"),
                 (129.5, 302.72636, 138.25, 311.62664, "2),"), (143.0, 302.72636, 149.75, 311.62664, "3)"),
                 (153.0, 303.758, 190.0, 317.042, "Kodeks"),
                 (56.64, 627.08, 62.04, 632.92, "1)"), (56.64, 673.19, 62.04, 679.03, "2)"), (56.64, 700.19, 62.04, 706.03, "3)")]
        strona = ("       Art. 1.1), 2), 3) Kodeks postępowania cywilnego normuje postępowanie sądowe.\n\n\n"
                  "1)\n     Utracił moc z dniem 17 lipca 2000 r. (SK. 12/99).\n"
                  "2)\n     Utracił moc z dniem 11 września 2017 r. (K 10/17).\n"
                  "3)\n     Utracił moc z dniem 1 stycznia 2020 r. (K 1/19).\n")
        info = {}
        t = eli.pdf_layout_do_tekstu(eli._pdf_oznacz_odsylacze(strona, slowa), info)
        self.assertIn("Art. 1. Kodeks postępowania cywilnego normuje postępowanie sądowe.\n[przypis 1)] Utracił moc z dniem "
                      "17 lipca 2000 r. (SK. 12/99).\n[przypis 2)] Utracił moc z dniem 11 września 2017 r. (K 10/17).\n"
                      "[przypis 3)] Utracił moc z dniem 1 stycznia 2020 r.", t)
        self.assertNotIn("niepewne_miejsca", info)
        # dwa odsyłacze — jak dotąd (TestOdsylaczZeSpacja202611c)
        self.assertEqual([x[1] for x in eli._pdf_odsylacze_bbox(slowa[:2] + slowa[3:])], ["1"])

    def test_strona_z_notka_nieprzycieta_oznaczona(self):
        # notka za krawędzią tekstu, a obok niej słowo treści sięgające za miejsce przycięcia (przypis szerszy niż kolumna)
        tresc = [w for i in range(30) for w in _wiersz(80 + 20.7 * i, "Rzeczypospolitej Polskiej od co najmniej stu lat", 56.6,
                                                         468.0)]
        notka = _notka(497.5, 300.0, ["Nowe brzmienie", "poz. 15 w zał. nr 3", "wejdzie w życie z", "dn. 1.01.2027 r."])
        obce = [(470.0, 700.0, "UE", 520.0), (523.0, 700.0, "L", 530.0), (533.0, 700.0, "48", 545.0)]
        _, marg, info = eli._pdf_margines(eli._pdf_slowa(_bbox([(595.32, 841.92, tresc + notka + obce)])))
        self.assertEqual(marg, {})
        self.assertEqual(info["nieprzyciete"], [(0, "Nowe brzmienie poz. 15 w zał.")])
        _, marg, info = eli._pdf_margines(eli._pdf_slowa(_bbox([(595.32, 841.92, tresc + notka)])))
        self.assertIn(0, marg)
        self.assertNotIn("nieprzyciete", info)


if __name__ == "__main__":
    unittest.main(verbosity=2)
