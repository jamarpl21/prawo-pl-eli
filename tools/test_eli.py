#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eli.py pure functions (no network). Run: python3 tools/test_eli.py"""
import argparse
import contextlib
import io
import sys
import importlib.util
import pathlib
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
        # (${CLAUDE_PLUGIN_ROOT}) albo katalog tego SKILL.md — bez find po dysku i bez curl z main
        for plugin in self.WYDANE:
            skill = ROOT / f"plugins/{plugin}/skills/{plugin}/SKILL.md"
            text = skill.read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                self.assertIn(f'="${{CLAUDE_PLUGIN_ROOT}}/skills/{plugin}/scripts/', text)
                self.assertIn('="<katalog skilla>/scripts/', text)
                self.assertIn("Nie pobieraj helpera z sieci", text)
                self.assertIn("nie szukaj go przez `find`", text)
                self.assertNotIn("curl -fsSL", text)
                self.assertNotIn('find "$HOME', text)
                self.assertIn("`--strict`", text)


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
                contextlib.redirect_stdout(out):
            eli.cmd_tekst(argparse.Namespace(sygnatura=["DU", "1997", "483"], json=False, strict=True,
                                             pdf=None, fragment="Art. 45"))
        self.assertIn("Art. 45. 1. Każdy ma prawo do sprawiedliwego i jawnego rozpatrzenia sprawy bez "
                      "nieuzasadnionej zwłoki przez właściwy, niezależny, bezstronny i niezawisły sąd.\n"
                      "2. Wyłączenie jawności", out.getvalue())
        self.assertNotIn("Art. 46", out.getvalue())
        self.assertIn("/text/U/D19970483Lj.pdf", out.getvalue())


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



if __name__ == "__main__":
    unittest.main(verbosity=2)
