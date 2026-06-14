#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for saos.py pure functions (no network). Run: python3 tools/test_saos.py"""
import importlib.util
import pathlib
import unittest

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
        self.assertEqual(saos._forma({"judgmentForm": "wyrok SN"}), "wyrok SN")

    def test_dict_form_from_detail(self):  # /judgments/{id} zwraca obiekt {'name': …}
        self.assertEqual(saos._forma({"judgmentForm": {"name": "wyrok SN"}}), "wyrok SN")

    def test_fallback_to_judgment_type(self):
        self.assertEqual(saos._forma({"judgmentType": "SENTENCE"}), "SENTENCE")

    def test_empty(self):
        self.assertEqual(saos._forma({}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
