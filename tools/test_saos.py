#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for saos.py pure functions (no network). Run: python3 tools/test_saos.py"""
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
    "saos", ROOT / "plugins/prawo-pl-saos/skills/prawo-pl-saos/scripts/saos.py")
saos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(saos)


class TestCourtType(unittest.TestCase):
    def test_aliases(self):
        cases = [
            ("SN", "SUPREME"), ("sn", "SUPREME"),
            ("TK", "CONSTITUTIONAL_TRIBUNAL"), ("trybunał", "CONSTITUTIONAL_TRIBUNAL"),
            ("powszechne", "COMMON"), ("SA", "COMMON"), ("SO", "COMMON"), ("SR", "COMMON"),
            ("admin", "ADMINISTRATIVE"), ("NSA", "ADMINISTRATIVE"), ("WSA", "ADMINISTRATIVE"),
            ("KIO", "NATIONAL_APPEAL_CHAMBER"),
            ("SUPREME", "SUPREME"),  # forma kanoniczna też przechodzi
        ]
        for inp, want in cases:
            self.assertEqual(saos._court_type(inp), want, f"sąd: {inp!r}")

    def test_none_passes_through(self):
        self.assertIsNone(saos._court_type(None))

    def test_invalid_exits(self):
        with self.assertRaises(SystemExit):
            saos._court_type("kosmiczny")


class TestJType(unittest.TestCase):
    def test_aliases(self):
        cases = [
            ("wyrok", "SENTENCE"), ("postanowienie", "DECISION"), ("uchwala", "RESOLUTION"),
            ("uchwała", "RESOLUTION"), ("zarzadzenie", "REGULATION"), ("uzasadnienie", "REASONS"),
            ("SENTENCE", "SENTENCE"),
        ]
        for inp, want in cases:
            self.assertEqual(saos._jtype(inp), want, f"typ: {inp!r}")

    def test_none_passes_through(self):
        self.assertIsNone(saos._jtype(None))

    def test_invalid_exits(self):
        with self.assertRaises(SystemExit):
            saos._jtype("apelacja")


class TestHtmlToText(unittest.TestCase):
    def test_strips_em_highlight(self):
        # snippety SAOS podświetlają trafienie znacznikiem <em>
        t = saos.html_to_text("…trudno przyjąć, by <em>rękojmia</em> wiary publicznej…")
        self.assertIn("rękojmia", t)
        self.assertNotIn("<em>", t)

    def test_strips_script(self):
        t = saos.html_to_text("<p>A</p><script>var x=1;</script><p>B</p>")
        self.assertIn("A", t)
        self.assertIn("B", t)
        self.assertNotIn("var x", t)

    def test_normalizes_nbsp(self):
        self.assertEqual(saos.html_to_text("<p>art.\xa0299</p>"), "art. 299")

    def test_collapses_blank_lines(self):
        t = saos.html_to_text("<div><p>A</p><p></p><p></p><p>B</p></div>")
        self.assertNotIn("\n\n\n", t)


class TestFragmenty(unittest.TestCase):
    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "FRAZA" + ("Y" * 50)
        spans = saos._fragmenty(txt, "fraza")
        self.assertEqual(len(spans), 1)
        wycinek = txt[spans[0][0]:spans[0][1]]
        self.assertIn("FRAZA", wycinek)

    def test_dwa_odlegle_trafienia_dwa_okna(self):
        txt = "AAA cel " + ("Z" * 2000) + " cel BBB"
        spans = saos._fragmenty(txt, "cel")
        self.assertEqual(len(spans), 2)

    def test_bliskie_trafienia_scalone(self):
        txt = "start cel oraz cel koniec"  # oba trafienia w jednym oknie
        spans = saos._fragmenty(txt, "cel")
        self.assertEqual(len(spans), 1)

    def test_brak_trafien(self):
        self.assertEqual(saos._fragmenty("dowolny tekst", "nie ma takiej frazy"), [])

    def test_pusta_fraza(self):
        self.assertEqual(saos._fragmenty("cokolwiek", "   "), [])

    def test_limit_okien(self):
        txt = (" cel " + "Q" * 2000) * 10
        spans = saos._fragmenty(txt, "cel", maks=3)
        self.assertLessEqual(len(spans), 3)

    def test_okno_dosuwane_do_granicy_zdania(self):  # D10: nie ucinamy w pół słowa
        przed = "Pierwsze zdanie o niczym. " * 12
        txt = (przed + "Drugie zdanie zaczyna się tu i mówi o rękojmi za wady. Trzecie zdanie kończy. "
               + "Czwarte zdanie jest dalej. " * 12)
        (s, e), = saos._fragmenty(txt, "rękojmi", okno=60)
        self.assertTrue(txt[s:].startswith("Drugie zdanie"), txt[s:s + 30])
        self.assertTrue(txt[:e].endswith("."), txt[e - 30:e])  # koniec okna = koniec zdania
        self.assertTrue(txt[e:].startswith(" Czwarte") or txt[e:].startswith(" Trzecie"), txt[e:e + 20])

    def test_okno_bez_zdan_dosuwane_do_slowa(self):
        txt = "alfa " * 100 + "cel " + "beta " * 100
        (s, e), = saos._fragmenty(txt, "cel", okno=30)
        self.assertTrue(s == 0 or txt[s - 1] == " ", repr(txt[s - 3:s + 3]))
        self.assertTrue(e == len(txt) or txt[e] == " " or txt[e - 1] == " ", repr(txt[e - 3:e + 3]))


class TestCourtLabel(unittest.TestCase):
    def test_common_court_name_and_division(self):
        it = {"division": {"name": "I Wydział Cywilny",
                           "court": {"name": "Sąd Apelacyjny w Krakowie"}}}
        self.assertEqual(saos._court_label(it), "Sąd Apelacyjny w Krakowie, I Wydział Cywilny")

    def test_supreme_chambers_list(self):  # kształt z /search
        it = {"division": {"name": "Wydział III", "chambers": [{"name": "Izba Cywilna"}]}}
        self.assertEqual(saos._court_label(it), "Izba Cywilna")

    def test_supreme_chamber_single(self):  # kształt z /judgments/{id}
        it = {"division": {"chamber": {"name": "Izba Karna"}}}
        self.assertEqual(saos._court_label(it), "Izba Karna")

    def test_fallback_division_name(self):
        self.assertEqual(saos._court_label({"division": {"name": "Wydział X"}}), "Wydział X")

    def test_empty(self):
        self.assertEqual(saos._court_label({}), "")


class TestCaseNumbers(unittest.TestCase):
    def test_joins_case_numbers(self):
        it = {"courtCases": [{"caseNumber": "III CSK 203/09"}, {"caseNumber": "I CSK 70/07"}]}
        self.assertEqual(saos._case_numbers(it), "III CSK 203/09, I CSK 70/07")

    def test_empty(self):
        self.assertEqual(saos._case_numbers({}), "")


class TestForma(unittest.TestCase):
    def test_string_form_from_search(self):  # /search zwraca string
        self.assertEqual(saos._forma({"judgmentType": "SENTENCE", "judgmentForm": "wyrok SN"}),
                         "wyrok (wyrok SN)")
        self.assertEqual(saos._forma({"judgmentForm": "wyrok SN"}), "wyrok SN")

    def test_dict_form_from_detail(self):  # /judgments/{id} zwraca obiekt {'name': …}
        self.assertEqual(saos._forma({"judgmentForm": {"name": "wyrok SN"}}), "wyrok SN")

    def test_fallback_to_judgment_type(self):  # surowy enum API → polska nazwa (D09)
        self.assertEqual(saos._forma({"judgmentType": "SENTENCE"}), "wyrok")

    def test_enum_po_polsku_dla_kazdego_sadu(self):
        for enum, pl in (("SENTENCE", "wyrok"), ("DECISION", "postanowienie"), ("RESOLUTION", "uchwała"),
                         ("REGULATION", "zarządzenie"), ("REASONS", "uzasadnienie")):
            self.assertEqual(saos._forma({"judgmentType": enum}), pl, enum)
        self.assertNotIn("SENTENCE", saos._forma({"judgmentType": "SENTENCE", "courtType": "COMMON"}))

    def test_forma_sn_dopisana_gdy_dokladniejsza(self):
        self.assertEqual(saos._forma({"judgmentType": "RESOLUTION",
                                      "judgmentForm": "uchwała składu 7 sędziów SN"}),
                         "uchwała (uchwała składu 7 sędziów SN)")
        self.assertEqual(saos._forma({"judgmentType": "SENTENCE", "judgmentForm": "wyrok"}), "wyrok")

    def test_empty(self):
        self.assertEqual(saos._forma({}), "")


class TestZasiegZbiorow(unittest.TestCase):
    """SAOS przestał zasilać SN/TK/KIO — bez ostrzeżenia zero trafień udaje brak orzecznictwa.
    Granica jest znana do DNIA (D01): SN 2016-06-22, TK 2015-12-09, KIO 2018-09-06."""

    def test_granice_do_dnia(self):
        self.assertEqual(saos.ZASIEG["SUPREME"][0], "2016-06-22")
        self.assertEqual(saos.ZASIEG["CONSTITUTIONAL_TRIBUNAL"][0], "2015-12-09")
        self.assertEqual(saos.ZASIEG["NATIONAL_APPEAL_CHAMBER"][0], "2018-09-06")

    def test_sn_ma_granice(self):
        k = saos._ostrzezenie_zasiegu("SUPREME")
        self.assertIn("22.06.2016", k)
        self.assertNotIn("na 2016 r.", k)
        self.assertIn("Sąd Najwyższy", k)
        self.assertIn("nie ma górnej granicy", k)

    def test_zakres_poza_zbiorem_dopisuje_powod(self):
        k = saos._ostrzezenie_zasiegu("NATIONAL_APPEAL_CHAMBER", "2021-01-01")
        self.assertIn("06.09.2018", k)
        self.assertIn("POZA zbiorem", k)

    def test_zakres_w_tym_samym_roku_po_granicy_jest_poza_zbiorem(self):  # D01: Q4-2018 KIO
        k = saos._ostrzezenie_zasiegu("NATIONAL_APPEAL_CHAMBER", "2018-10-01", "2018-12-31")
        self.assertIn("POZA zbiorem", k)
        k = saos._ostrzezenie_zasiegu("SUPREME", "2016-07-01", "2016-12-31")
        self.assertIn("POZA zbiorem", k)

    def test_zakres_siegajacy_poza_granice_ostrzega(self):
        k = saos._ostrzezenie_zasiegu("SUPREME", "2016-01-01", "2016-12-31")
        self.assertNotIn("POZA zbiorem", k)
        self.assertIn("po 22.06.2016 SAOS nie ma NIC", k)

    def test_zakres_w_zbiorze_bez_dopisku(self):
        k = saos._ostrzezenie_zasiegu("CONSTITUTIONAL_TRIBUNAL", "2010-01-01", "2015-12-09")
        self.assertNotIn("POZA zbiorem", k)
        self.assertNotIn("nie ma NIC", k)

    def test_granica_potwierdzona_na_zywo_w_komunikacie(self):
        k = saos._ostrzezenie_zasiegu("SUPREME", None, None, "2016-06-22", True)
        self.assertIn("potwierdzone na żywo", k)

    def test_sady_powszechne_bez_ostrzezenia(self):
        self.assertIsNone(saos._ostrzezenie_zasiegu("COMMON"))
        self.assertIsNone(saos._ostrzezenie_zasiegu(None))


class TestNormaData(unittest.TestCase):
    def test_rok_i_miesiac(self):
        self.assertEqual(saos._norma_data("2016"), "2016-01-01")
        self.assertEqual(saos._norma_data("2016", koniec=True), "2016-12-31")
        self.assertEqual(saos._norma_data("2016-02", koniec=True), "2016-02-29")
        self.assertEqual(saos._norma_data("2015-02", koniec=True), "2015-02-28")
        self.assertEqual(saos._norma_data("2018-09-06"), "2018-09-06")
        self.assertIsNone(saos._norma_data(None))

    def test_zly_format_konczy_bledem(self):
        with self.assertRaisesRegex(SystemExit, "RRRR-MM-DD"):
            saos._norma_data("06.09.2018")


class TestGranicaNaZywo(unittest.TestCase):
    """Granica zbioru jest samoweryfikująca: tanie zapytanie o najnowsze orzeczenie (cache w procesie)."""

    def setUp(self):
        saos._granice_na_zywo.clear()

    def test_potwierdzenie_na_zywo_i_cache(self):
        odp = {"items": [{"id": 245360, "judgmentDate": "2016-06-22"}], "info": {"totalResults": 38081}}
        with mock.patch.object(saos, "_get", return_value=odp) as get:
            self.assertEqual(saos._granica_zbioru("SUPREME"), ("2016-06-22", True))
            self.assertEqual(saos._granica_zbioru("SUPREME"), ("2016-06-22", True))
        get.assert_called_once()
        self.assertTrue(get.call_args.kwargs.get("soft"))
        self.assertEqual(get.call_args.args[1]["pageSize"], 1)

    def test_wznowione_zasilanie_przesuwa_granice(self):
        odp = {"items": [{"id": 1, "judgmentDate": "2024-03-01"}], "info": {"totalResults": 1}}
        with mock.patch.object(saos, "_get", return_value=odp):
            self.assertEqual(saos._granica_zbioru("NATIONAL_APPEAL_CHAMBER"), ("2024-03-01", True))

    def test_awaria_transportu_zostawia_znana_granice(self):
        with mock.patch.object(saos, "_get", side_effect=saos.VerificationUnknown("przerwa")):
            self.assertEqual(saos._granica_zbioru("CONSTITUTIONAL_TRIBUNAL"), ("2015-12-09", False))


class TestIndeksyGorne(unittest.TestCase):
    """D04: <sup> w tekstach sądów powszechnych ma być indeksem górnym w jednej linii, nie łamaniem."""

    def test_sup_z_artefaktem_saos(self):
        html = ('Stosownie do treści <a href="x">art. 556<sup>\n<!-- -->1</sup> § 1 k.c.</a> wada fizyczna '
                'polega')
        self.assertEqual(saos.html_to_text(html), "Stosownie do treści art. 556¹ § 1 k.c. wada fizyczna polega")

    def test_sup_wielocyfrowy_i_spacja_koncowa(self):
        self.assertEqual(saos.html_to_text("art. 479<sup>45</sup> § 1"), "art. 479⁴⁵ § 1")
        self.assertEqual(saos.html_to_text("art. 556<sup>\n<!-- --> 5 </sup>k.c."), "art. 556⁵ k.c.")
        self.assertEqual(saos.html_to_text("2461 cm<sup>\n<!-- -->3</sup> i moc"), "2461 cm³ i moc")

    def test_artefakt_nowej_linii_w_em(self):
        self.assertEqual(saos.html_to_text("możliwe <em>\n<!-- -->(art. 561<sup>\n<!-- --> 2</sup> § 2 k.c.)</em>"),
                         "możliwe (art. 561² § 2 k.c.)")

    def test_akapity_nadal_lamane(self):
        self.assertEqual(saos.html_to_text("<p>A</p><p>B</p>"), "A\nB")


class TestWpisPrzepisu(unittest.TestCase):
    """D11: uszkodzone wpisy z referencedRegulations są oznaczane, czyste nie."""

    def _w(self, text):
        return saos._wpis_przepisu({"text": text})

    def test_sklejony_numer(self):
        self.assertIn("uszkodzony", self._w("Kodeks postępowania cywilnego (… art. 479(45) § 1-3, art. 4793647945)"))

    def test_nr_0(self):
        self.assertIn("„Nr 0”", self._w("Obwieszczenie … (Dz. U. z 2015 r. Nr 0 poz. 184 - art. 24 ust. 2)"))

    def test_sklejone_slowa(self):
        self.assertIn("sklejone słowa", self._w("Konstytucja (… art. 180 ust. 2oraz, art. 2oraz)"))
        self.assertIn("sklejone słowa", self._w("Konstytucja (… art. 194 ust. atakże)"))

    def test_art_n(self):
        self.assertIn("„art. n”", self._w("Ustawa (… art. 6; art. n)"))

    def test_czysty_wpis_bez_oznaczenia(self):
        t = "Ustawa z dnia 23 kwietnia 1964 r. - Kodeks cywilny (Dz. U. z 1964 r. Nr 16 poz. 93 - art. 417, art. 417(1) § 1, art. 442(1))"
        self.assertEqual(self._w({"text": t}["text"]), t)


class TestZrodlaUrzedowe(unittest.TestCase):
    """D07: link z SAOS dla SN/TK/KIO jest martwy — pokazujemy działający wzorzec / wyszukiwarkę."""

    def test_sn_wzorzec_pdf(self):
        linie = saos._zrodla_urzedowe({"courtType": "SUPREME", "courtCases": [{"caseNumber": "II KK 56/16"}],
                                       "source": {"judgmentUrl": "http://www.sn.pl/orzecznictwo/SitePages/Baza_orzeczen"}})
        self.assertIn("https://www.sn.pl/sites/orzecznictwo/Orzeczenia3/II%20KK%2056-16.pdf", linie[0])
        self.assertIn("sprawdź — wzorzec adresu", linie[0])
        self.assertIn("nie służy do weryfikacji", linie[1])
        self.assertFalse(any(l.startswith("  Źródło oryginalne") for l in linie))

    def test_tk_i_kio_wyszukiwarki(self):
        tk = saos._zrodla_urzedowe({"courtType": "CONSTITUTIONAL_TRIBUNAL", "courtCases": [{"caseNumber": "K 35/15"}]})
        self.assertIn("ipo.trybunal.gov.pl", tk[0]); self.assertIn("K 35/15", tk[0])
        kio = saos._zrodla_urzedowe({"courtType": "NATIONAL_APPEAL_CHAMBER", "courtCases": [{"caseNumber": "KIO 1564/18"}]})
        self.assertIn("orzeczenia.uzp.gov.pl", kio[0]); self.assertIn("KIO 1564/18", kio[0])

    def test_sad_powszechny_zachowuje_link(self):
        url = "https://apiorzeczenia.wroclaw.sa.gov.pl/ncourt-api/judgement/details?id=1"
        linie = saos._zrodla_urzedowe({"courtType": "COMMON", "courtCases": [{"caseNumber": "I C 374/25"}],
                                       "source": {"judgmentUrl": url}})
        self.assertEqual(linie, [f"  Źródło oryginalne: {url}"])


class TestSygnaturaZero(unittest.TestCase):
    """D02: sygnatura z rocznika granicy nie może dostać „nie ma" — tylko „może być późniejsze"."""

    def test_sn_z_rocznika_granicy(self):
        msg = "\n".join(saos._wyjasnienie_zera_sygnatury("III CZP 81/16"))
        self.assertIn("może być późniejsze niż koniec zbioru 22.06.2016", msg)
        self.assertNotIn("NIE MA", msg)
        self.assertNotIn("Trybunał", msg)

    def test_kio_z_rocznika_granicy(self):
        msg = "\n".join(saos._wyjasnienie_zera_sygnatury("KIO 2577/18"))
        self.assertIn("może być późniejsze niż koniec zbioru 06.09.2018", msg)

    def test_kio_po_granicy(self):
        msg = "\n".join(saos._wyjasnienie_zera_sygnatury("KIO 12/19"))
        self.assertIn("NIE MA z założenia", msg)

    def test_tk_przed_granica(self):
        msg = "\n".join(saos._wyjasnienie_zera_sygnatury("K 1/10"))
        self.assertIn("mieści się w zbiorze", msg)

    def test_nierozpoznana_sygnatura_nie_twierdzi_ze_nie_ma(self):
        msg = "\n".join(saos._wyjasnienie_zera_sygnatury("I C 374/25"))
        self.assertIn("sąd powszechny", msg)
        self.assertIn("jeśli to Sąd Najwyższy", msg)

    def test_cmd_sygnatura_zero_uzywa_granicy_do_dnia(self):
        args = argparse.Namespace(sygnatura=["III", "CZP", "81/16"], json=False)
        with mock.patch.object(saos, "_get", return_value={"items": [], "info": {"totalResults": 0}}):
            with self.assertRaises(SystemExit) as caught:
                saos.cmd_sygnatura(args)
        self.assertIn("może być późniejsze niż koniec zbioru 22.06.2016", str(caught.exception))
        self.assertNotIn("na 2016 r.", str(caught.exception))


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj", "fraza"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, saos.cmd_szukaj
        saos.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            saos.main()
        finally:
            sys.argv, saos.cmd_szukaj = oryg_argv, oryg_cmd
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
    headers = {"Content-Type": "application/json"}

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


class SaosVerificationContractTests(unittest.TestCase):
    """found/verified_absent/unknown - blad transportu nie moze wygladac jak potwierdzony brak."""

    def test_limit_czasu_90s_i_timeout_to_blad_sieci(self):  # API bywa wolne (40–60 s na /search)
        import socket
        with mock.patch.object(saos._opener, "open", side_effect=socket.timeout("timed out")) as op, \
                mock.patch.object(saos.time, "sleep"):
            with self.assertRaisesRegex(SystemExit, "BŁĄD sieci"):
                saos._get("/search/judgments", {"courtType": "SUPREME"})
        self.assertEqual(op.call_args.kwargs.get("timeout"), 90)

    def test_przerwa_techniczna_html_to_unknown(self):
        html = _Response(b"<!DOCTYPE html><html><head><title>Przerwa techniczna</title></head></html>")
        with mock.patch.object(saos._opener, "open", return_value=html), mock.patch.object(saos.time, "sleep"):
            with self.assertRaisesRegex(saos.VerificationUnknown, "przerw"):
                saos._get("/search/judgments", soft=True)
            with self.assertRaisesRegex(SystemExit, "przerwę techniczną"):
                saos._get("/search/judgments")

    def test_soft_transport_error_is_unknown(self):
        with mock.patch.object(saos._opener, "open", side_effect=urllib.error.URLError("offline")), \
                mock.patch.object(saos.time, "sleep"):
            with self.assertRaises(saos.VerificationUnknown):
                saos._get("/search/judgments", soft=True)

    def test_successful_zero_results_is_verified_absent(self):
        response = _Response(b'{"items": [], "info": {"totalResults": 0}}')
        with mock.patch.object(saos._opener, "open", return_value=response):
            result = saos._get("/search/judgments", soft=True)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["info"]["totalResults"], 0)

    def test_malformed_api_response_is_not_no_results(self):
        args = argparse.Namespace(sygnatura=["III", "CSK", "203/09"], json=False)
        with mock.patch.object(saos, "_get", return_value={"message": "maintenance"}):
            with self.assertRaisesRegex(SystemExit, "nie udało się zweryfikować") as caught:
                saos.cmd_sygnatura(args)
        self.assertNotIn("Nie znaleziono orzeczenia", str(caught.exception))

    def setUp(self):
        saos._granice_na_zywo.clear()

    def test_t08_strict_blokuje_zakres_poza_zamknietym_zbiorem_i_stdout_jest_pusty(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv",
                               ["saos.py", "szukaj", "--sad", "SN", "--od", "2024-01-01", "--strict"]), \
                mock.patch.object(saos, "_get") as get, contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                saos.main()
        self.assertNotEqual(caught.exception.code, 0)
        self.assertEqual(out.getvalue(), "")
        # jedyne zapytanie to miękkie potwierdzenie granicy zbioru — nie właściwe wyszukiwanie
        get.assert_called_once()
        self.assertTrue(get.call_args.kwargs.get("soft"))

    def test_zero_trafien_szukaj_konczy_bledem_takze_z_json(self):  # D06 / C17
        odp = {"items": [], "info": {"totalResults": 0}}
        for argv in (["szukaj", "--sad", "KIO", "--od", "2019-01-01", "--json"],
                     ["szukaj", "--sad", "SN", "--od", "2017-01-01"],
                     ["szukaj", "rękojmia", "--sad", "powszechne", "--json"]):
            with self.subTest(argv=argv):
                saos._granice_na_zywo.clear()
                out = io.StringIO()
                with mock.patch.object(saos, "_get", return_value=odp), \
                        mock.patch.object(sys, "argv", ["saos.py", *argv]), contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        saos.main()
                self.assertNotEqual(caught.exception.code, 0)
                self.assertEqual(out.getvalue(), "")
                msg = str(caught.exception)
                self.assertIn("Brak trafień", msg)
                if "--sad" in argv and argv[argv.index("--sad") + 1] in ("KIO", "SN"):
                    self.assertIn("POZA zbiorem", msg)
                    self.assertRegex(msg, r"06\.09\.2018|22\.06\.2016")

    def test_json_z_wynikami_ostrzega_na_stderr(self):
        odp = {"items": [{"id": 1, "judgmentDate": "2016-06-22", "courtType": "SUPREME", "courtCases": []}],
               "info": {"totalResults": 1}}
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(saos, "_get", return_value=odp), \
                mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "SN", "--json"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            saos.main()
        self.assertIn('"totalResults": 1', out.getvalue())
        self.assertIn("22.06.2016", err.getvalue())

    def test_przepis_luzne_dopasowanie_ostrzezenie(self):  # D08
        odp = {"items": [{"id": 1, "judgmentDate": "2016-01-01", "courtType": "SUPREME", "courtCases": [],
                          "judgmentType": "SENTENCE"}], "info": {"totalResults": 810}}
        out = io.StringIO()
        with mock.patch.object(saos, "_get", return_value=odp), \
                mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "SN", "--przepis", "art. 415",
                                                "--do", "2016-06-22"]), contextlib.redirect_stdout(out):
            saos.main()
        self.assertIn("LUŹNE dopasowanie", out.getvalue())
        self.assertIn("Kodeks cywilny art. 415", out.getvalue())
        self.assertIn("· wyrok", out.getvalue())

    def test_t09_json_strict_nie_emituje_wyniku_gdy_kontrola_nie_przeszla(self):
        przypadki = [
            (["szukaj", "--sad", "SN", "--od", "2024-01-01"], None),
            (["orzeczenie", "123"], {"message": "maintenance"}),
            (["sygnatura", "III", "CSK", "203/09"],
             {"items": [], "info": {"totalResults": 0}}),
        ]
        for komenda, odpowiedz in przypadki:
            with self.subTest(komenda=komenda):
                out = io.StringIO()
                with mock.patch.object(saos, "_get", return_value=odpowiedz), \
                        mock.patch.object(sys, "argv", ["saos.py", *komenda, "--json", "--strict"]), \
                        contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as caught:
                        saos.main()
                self.assertNotEqual(caught.exception.code, 0)
                self.assertEqual(out.getvalue(), "")


class TestT13StrictZasiegKomunikat(unittest.TestCase):
    """Blokada zakresu jest deterministyczna — komunikat nie może sugerować ponowienia,
    tylko jak zawęzić zakres (--do) albo gdzie szukać nowszych orzeczeń."""

    NAJNOWSZE = {"SUPREME": "2016-06-22", "CONSTITUTIONAL_TRIBUNAL": "2015-12-09",
                 "NATIONAL_APPEAL_CHAMBER": "2018-09-06"}

    def setUp(self):
        saos._granice_na_zywo.clear()

    def _odp_granicy(self, path, params=None, soft=False):
        """Atrapa API: tylko miękkie zapytanie o najnowsze orzeczenie (pageSize=1) jest dozwolone."""
        self.assertTrue(soft)
        self.assertEqual(params["pageSize"], 1)
        return {"items": [{"id": 1, "judgmentDate": self.NAJNOWSZE[params["courtType"]]}],
                "info": {"totalResults": 1}}

    def _blokada(self, argv):
        saos._granice_na_zywo.clear()
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["saos.py", *argv, "--strict"]), \
                mock.patch.object(saos, "_get", side_effect=self._odp_granicy) as get, \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                saos.main()
        get.assert_called_once()
        self.assertEqual(out.getvalue(), "")
        return str(caught.exception)

    def test_bez_gornej_granicy_podpowiada_do(self):
        msg = self._blokada(["szukaj", "--sad", "SN", "--przepis", "art. 415"])
        self.assertIn("--do 2016-06-22", msg)
        self.assertIn("22.06.2016", msg)
        self.assertIn("granica potwierdzona na żywo", msg)
        self.assertNotIn("ponownie", msg)

    def test_zakres_od_po_koncu_zbioru_wskazuje_portal(self):
        msg = self._blokada(["szukaj", "--sad", "TK", "--od", "2024-01-01"])
        self.assertIn("zaczyna się 01.01.2024", msg)
        self.assertIn("ipo.trybunal.gov.pl", msg)
        self.assertNotIn("ponownie", msg)

    def test_ten_sam_rocznik_po_granicy_jest_blokowany(self):  # D01 / C03
        msg = self._blokada(["szukaj", "--sad", "SN", "--od", "2016-07-01", "--do", "2016-12-31"])
        self.assertIn("zaczyna się 01.07.2016", msg)
        msg = self._blokada(["szukaj", "--sad", "KIO", "--od", "2018-10-01", "--do", "2018-12-31"])
        self.assertIn("06.09.2018", msg)
        msg = self._blokada(["szukaj", "--sad", "SN", "--od", "2016-01-01", "--do", "2016-12-31"])
        self.assertIn("sięga 31.12.2016", msg)
        self.assertIn("--do 2016-06-22", msg)

    def test_blokada_bez_potwierdzenia_na_zywo(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "SN", "--strict"]), \
                mock.patch.object(saos, "_get", side_effect=saos.VerificationUnknown("przerwa")), \
                contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                saos.main()
        self.assertIn("nie udało się potwierdzić granicy na żywo", str(caught.exception))
        self.assertIn("--do 2016-06-22", str(caught.exception))
        self.assertEqual(out.getvalue(), "")

    def test_wznowione_zasilanie_odblokowuje_zakres(self):
        """Gdyby SAOS znów zasilał SN, granica na żywo przesuwa się i zakres w niej nie jest blokowany."""
        odpowiedzi = iter([
            {"items": [{"id": 1, "judgmentDate": "2025-01-15"}], "info": {"totalResults": 1}},
            {"items": [{"id": 2, "judgmentDate": "2024-06-01", "courtType": "SUPREME", "courtCases": [],
                        "judgmentType": "SENTENCE"}], "info": {"totalResults": 1}},
        ])
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "SN", "--od", "2024-01-01",
                                             "--do", "2024-12-31", "--strict"]), \
                mock.patch.object(saos, "_get", side_effect=lambda *a, **k: next(odpowiedzi)), \
                contextlib.redirect_stdout(out):
            saos.main()
        self.assertIn("Znaleziono: 1", out.getvalue())
        self.assertIn("15.01.2025", out.getvalue())
        self.assertNotIn("POZA zbiorem", out.getvalue())

    def test_wynik_nowszy_niz_granica_przesuwa_granice(self):
        """Sam wynik (sort po dacie malejąco) dowodzi wznowienia zasilania — ostrzeżenie nie może
        twierdzić, że zbiór kończy się wcześniej niż pokazane orzeczenie."""
        odp = {"items": [{"id": 9, "judgmentDate": "2021-03-03", "courtType": "NATIONAL_APPEAL_CHAMBER",
                          "courtCases": [{"caseNumber": "KIO 1/21"}], "judgmentType": "SENTENCE"}],
               "info": {"totalResults": 1}}
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "KIO", "--od", "2021-01-01",
                                             "--do", "2021-12-31"]), \
                mock.patch.object(saos, "_get", return_value=odp), contextlib.redirect_stdout(out):
            saos.main()
        self.assertIn("03.03.2021", out.getvalue())
        self.assertNotIn("POZA zbiorem", out.getvalue())
        self.assertNotIn("06.09.2018", out.getvalue())

    def test_zakres_w_granicach_zbioru_przechodzi(self):
        """Zakres mieszczący się w zbiorze nie jest blokowany przez strict (bez zapytania o granicę);
        zero trafień to zwykły komunikat „Brak trafień", nie blokada zbioru."""
        out = io.StringIO()
        odp = {"items": [], "info": {"totalResults": 0}}
        with mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "KIO",
                                             "--od", "2017-01-01", "--do", "2018-09-06", "--strict"]), \
                mock.patch.object(saos, "_get", return_value=odp) as get, contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                saos.main()
        self.assertNotIn("BŁĄD: zbiór", str(caught.exception))
        self.assertIn("Brak trafień", str(caught.exception))
        self.assertEqual(get.call_args_list[0].args[0], "/search/judgments")
        self.assertFalse(get.call_args_list[0].kwargs.get("soft"))
        self.assertEqual(out.getvalue(), "")

    def test_zakres_z_wynikami_w_granicach_zbioru(self):
        out = io.StringIO()
        odp = {"items": [{"id": 354889, "judgmentDate": "2018-08-27", "courtType": "NATIONAL_APPEAL_CHAMBER",
                          "courtCases": [{"caseNumber": "KIO 1564/18"}], "judgmentType": "SENTENCE"}],
               "info": {"totalResults": 1}}
        with mock.patch.object(sys, "argv", ["saos.py", "szukaj", "--sad", "KIO",
                                             "--od", "2018-08-01", "--do", "2018-09-06", "--strict"]), \
                mock.patch.object(saos, "_get", return_value=odp) as get, contextlib.redirect_stdout(out):
            saos.main()
        get.assert_called_once()
        self.assertIn("Znaleziono: 1", out.getvalue())
        self.assertIn("· wyrok", out.getvalue())
        self.assertNotIn("SENTENCE", out.getvalue())


class TestOrzeczenieWyjscie(unittest.TestCase):
    """cmd_orzeczenie: źródła, nota o indeksach górnych, etykieta listy przepisów, okna z „…"."""

    def _uruchom(self, data, argv_extra=()):
        out = io.StringIO()
        with mock.patch.object(saos, "_get", return_value={"data": data}), \
                mock.patch.object(sys, "argv", ["saos.py", "orzeczenie", str(data["id"]), *argv_extra]), \
                contextlib.redirect_stdout(out):
            saos.main()
        return out.getvalue()

    SN = {"id": 245229, "courtType": "SUPREME", "judgmentType": "SENTENCE", "judgmentDate": "2016-05-06",
          "courtCases": [{"caseNumber": "I CSK 364/15"}], "judgmentForm": {"name": "wyrok SN"},
          "source": {"judgmentUrl": "http://www.sn.pl/orzecznictwo/SitePages/Baza_orzeczen"},
          "referencedRegulations": [{"text": "Kodeks cywilny (Dz. U. z 1964 r. Nr 16 poz. 93 - art. 417(1))"},
                                    {"text": "Kodeks postępowania cywilnego (… art. 4793647945)"}],
          "textContent": "<p>Sąd zważył, że art. 4171 k.c. ma zastosowanie.</p>"}
    SP = {"id": 549939, "courtType": "COMMON", "judgmentType": "SENTENCE", "judgmentDate": "2026-06-24",
          "courtCases": [{"caseNumber": "I C 374/25"}],
          "source": {"judgmentUrl": "https://apiorzeczenia.wroclaw.sa.gov.pl/ncourt-api/judgement/details?id=1"},
          "textContent": ("<p>" + "Zdanie wstępu bez trafienia, które ma wypełnić okno. " * 20
                          + "</p><p>Pierwsze zdanie akapitu. Stosownie do treści "
                          "art. 556<sup>\n<!-- -->1</sup> § 1 k.c. wada fizyczna polega na niezgodności z umową. "
                          "Kolejne zdanie o rękojmi za wady fizyczne. Ostatnie zdanie akapitu.</p>"
                          "<p>" + "Inny akapit bez trafienia. " * 40 + "</p>")}

    def test_sn_nota_o_indeksach_i_wzorzec_sn(self):
        out = self._uruchom(self.SN)
        self.assertIn("SAOS spłaszcza indeksy górne", out)
        self.assertIn("I%20CSK%20364-15.pdf", out)
        self.assertIn("nie służy do weryfikacji", out)
        self.assertIn("lista z SAOS", out)
        self.assertIn("bywa niepełna", out)
        self.assertIn("wpis SAOS prawdopodobnie uszkodzony", out)
        self.assertIn("typ: wyrok (wyrok SN)", out)

    def test_sad_powszechny_indeks_gorny_inline_i_bez_noty(self):
        out = self._uruchom(self.SP)
        self.assertIn("art. 556¹ § 1 k.c.", out)
        self.assertNotIn("spłaszcza", out)
        self.assertIn("Źródło oryginalne: https://apiorzeczenia", out)
        self.assertIn("typ: wyrok", out)

    def test_fragment_granice_zdan_i_wielokropki(self):  # D10
        out = self._uruchom(self.SP, ["--fragment", "rękojmi"])
        tresc = out.split("## Treść uzasadnienia")[1]
        self.assertRegex(tresc, r"\n…[A-ZĄĆĘŁŃÓŚŹŻ]")  # okno zaczyna się od początku zdania, z „…"
        self.assertNotIn("\nerki", tresc)
        self.assertIn("…\n", tresc)


class TestT10PrzekierowaniaHttps(unittest.TestCase):
    def test_host_tresci_jest_podnoszony_a_obcy_host_odrzucany(self):
        req = saos.urllib.request.Request("https://www.saos.org.pl/api/search/judgments")
        handler = saos._PrzekierowaniaHttps()
        nowy = handler.redirect_request(
            req, None, 302, "Found", {}, "http://www.saos.org.pl/api/judgments/123")
        self.assertEqual(nowy.full_url, "https://www.saos.org.pl/api/judgments/123")
        with self.assertRaisesRegex(urllib.error.URLError, "niezaufany host"):
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.test/judgment")


if __name__ == "__main__":
    unittest.main(verbosity=2)
