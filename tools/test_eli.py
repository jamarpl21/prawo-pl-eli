#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eli.py pure functions (no network). Run: python3 tools/test_eli.py"""
import importlib.util
import pathlib
import unittest

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
        self.assertIn("Art. 66c 6)\n[przypis] Dodany przez", t)   # komentarz redakcyjny ≠ norma
        self.assertTrue(t.startswith("Art. 66c 6)"))

    def test_przypis_nie_udaje_naglowka_jednostki(self):
        # Przypis potrafi zaczynać się od „Art. 598…"/„Tytuł działu…" (7 razy w k.p.c.);
        # na początku linii udawałby granicę jednostki i tnąłby fragment w losowym miejscu.
        html = self.HTML_PRZYPIS.replace("Dodany przez art. 3 pkt 2", "Art. 598 16 w związku z art.")
        t = eli.html_to_text(html)
        self.assertNotIn("\nArt. 598", t)
        self.assertIn("[przypis] Art. 598", t)


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
        act, txt = wynik
        self.assertEqual(act["ELI"], "DU/2024/1568")
        self.assertIn("Art. 1.", txt)

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

    def test_brak_kandydatow_zwraca_none(self):
        self.assertIsNone(self._z_fake_get({}, "/acts/DU/2026/468", {}))

    def test_nie_zwraca_aktu_biezacego(self):
        # jedyny t.j. na liście to akt bieżący — fallback nie może zwrócić jego samego
        wynik = self._z_fake_get({
            "/acts/DU/1964/296/references": {"Inf. o tekście jednolitym": [
                {"act": {"ELI": "DU/2026/468"}}]},
        }, "/acts/DU/2026/468", self.REFS_TJ)
        self.assertIsNone(wynik)


if __name__ == "__main__":
    unittest.main(verbosity=2)
