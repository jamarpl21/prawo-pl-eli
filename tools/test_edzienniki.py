#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for edzienniki.py pure functions (no network). Run: python3 tools/test_edzienniki.py"""
import contextlib
import io
import ssl
import sys
import importlib.util
import pathlib
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "edzienniki", ROOT / "plugins/prawo-pl-edzienniki/skills/prawo-pl-edzienniki/scripts/edzienniki.py")
edz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(edz)


class TestWoj(unittest.TestCase):
    def test_kod(self):
        kod, nazwa, host, pub = edz._woj("DS")
        self.assertEqual((kod, host, pub), ("DS", "edzienniki.duw.pl", "POL_WOJ_DS"))

    def test_kod_male_litery(self):
        self.assertEqual(edz._woj("ds")[0], "DS")

    def test_nazwa_z_diakrytykami(self):
        self.assertEqual(edz._woj("dolnośląskie")[0], "DS")
        self.assertEqual(edz._woj("łódzkie")[0], "LD")

    def test_nazwa_bez_diakrytykow(self):
        self.assertEqual(edz._woj("dolnoslaskie")[0], "DS")
        self.assertEqual(edz._woj("lodzkie")[0], "LD")
        self.assertEqual(edz._woj("swietokrzyskie")[0], "SK")

    def test_prefiks_nazwy(self):
        self.assertEqual(edz._woj("mazow")[0], "MZ")

    def test_prefiks_niejednoznaczny_wymienia_kandydatow(self):
        # D9: 'MA' pasuje do małopolskie i mazowieckie — błąd z listą, nie cichy wybór pierwszego
        with self.assertRaises(SystemExit) as cm:
            edz._woj("MA")
        self.assertIn("MP=małopolskie", str(cm.exception))
        self.assertIn("MZ=mazowieckie", str(cm.exception))

    def test_wszystkie_16(self):
        self.assertEqual(len(edz.WOJEWODZTWA), 16)
        for kod, (nazwa, host, pub) in edz.WOJEWODZTWA.items():
            self.assertEqual(pub, f"POL_WOJ_{kod}")
            self.assertTrue(host)

    def test_nieznane_exits(self):
        with self.assertRaises(SystemExit):
            edz._woj("pomorze-gdanskie-x")

    def test_brak_exits(self):
        with self.assertRaises(SystemExit):
            edz._woj(None)


class TestNorm(unittest.TestCase):
    def test_pascal_case(self):
        # 4 hosty (starsze wdrożenia ABC PRO) zwracają PascalCase — normalizacja do lowercase
        d = edz._norm({"Items": [{"Title": "Uchwała", "Pos": 5092, "Year": 2026}],
                       "TotalCount": 5092})
        self.assertEqual(d["totalcount"], 5092)
        self.assertEqual(d["items"][0]["title"], "Uchwała")

    def test_camel_case(self):
        d = edz._norm({"items": [], "totalCount": 3301})
        self.assertEqual(d["totalcount"], 3301)

    def test_zagniezdzenie_i_listy(self):
        d = edz._norm([{"A": {"B": [1, 2]}}])
        self.assertEqual(d, [{"a": {"b": [1, 2]}}])


class TestData(unittest.TestCase):
    def test_sentinel(self):
        self.assertIsNone(edz._data("0001-01-01T00:00:00"))

    def test_none(self):
        self.assertIsNone(edz._data(None))

    def test_iso_z_godzina(self):
        self.assertEqual(edz._data("2026-07-10T00:00:00"), "2026-07-10")


class TestDaty(unittest.TestCase):
    """D1: semantyka pól RÓŻNI SIĘ między endpointami — akt: announcementDate=data aktu,
    promulgation=ogłoszenie; lista rocznika: odwrotnie."""
    AKT = {"announcementdate": "2026-08-11T00:00:00", "promulgation": "2026-08-13T10:46:01.443"}
    LISTA = {"promulgation": "2026-08-11T00:00:00", "announcementdate": "2026-08-13T10:46:01.443"}

    def test_rekord_aktu(self):
        self.assertEqual(edz._daty(self.AKT), ("2026-08-11", "2026-08-13"))

    def test_lista_rocznika_odwrotnie(self):
        self.assertEqual(edz._daty(self.LISTA, lista=True), ("2026-08-11", "2026-08-13"))

    def test_ogloszenie_nie_poprzedza_aktu(self):
        # host o odwrotnej konwencji: ogłoszenie < data aktu → zamiana z powrotem
        self.assertEqual(edz._daty(self.LISTA), ("2026-08-11", "2026-08-13"))

    def test_sentinel(self):
        self.assertEqual(edz._daty({"announcementdate": "2026-08-11T00:00:00",
                                    "promulgation": "0001-01-01T00:00:00"}), ("2026-08-11", None))


class TestAscii(unittest.TestCase):
    def test_diakrytyki(self):
        self.assertEqual(edz._ascii("Zagospodarowania Przestrzennego ŚĄŻ"),
                         "zagospodarowania przestrzennego saz")


class TestHtmlToText(unittest.TestCase):
    def test_akapity(self):
        t = edz.html_to_text("<p>§ 1. Uchwala się.</p><p>§ 2. Wykonanie.</p>")
        self.assertIn("§ 1. Uchwala się.", t)
        self.assertIn("\n", t)

    def test_nbsp(self):
        self.assertEqual(edz.html_to_text("<p>art.\xa05</p>"), "art. 5")

    def test_sup_inline(self):
        # D10: <sup> w linii, cyfra jako indeks górny — bez łamania wiersza
        self.assertEqual(edz.html_to_text("<p>1,00 zł od 1 m<sup>2</sup> powierzchni</p>"),
                         "1,00 zł od 1 m² powierzchni")

    def test_osobny_akapit_z_cyfra_indeksu(self):
        # D10: serwer emituje indeks jako osobny <p>2 </p> PRZED linią z „m"
        t = edz.html_to_text("<p>a w tym czasie nie </p><p>2 </p><p>zakończono budowy - 3,40 zł od 1 m powierzchni, </p>")
        self.assertIn("od 1 m² powierzchni,", t)
        self.assertNotIn("\n2\n", t)


class TestCzyscPdf(unittest.TestCase):
    """D3: tekst z pdftotext -layout — nagłówek dziennika, nagłówki stron i stopki usunięte,
    zawinięte linie scalone, jednostki na początku linii."""
    SUROWY = (
        "                     DZIENNIK URZĘDOWY\n"
        "                            WOJEWÓDZTWA MAŁOPOLSKIEGO\n\n"
        "                                   Kraków, dnia 16 grudnia 2025 r.                   Podpisany przez:\n"
        "                                                                                     Jan Kowalski\n"
        "                                                                                     Data: 16.12.2025 15:32:19\n"
        "                                                Poz. 7877\n\n"
        "                                     UCHWAŁA NR XII/112/2025\n\n"
        "   Na podstawie art. 18 ust. 2 pkt 8 ustawy z dnia 8 marca 1990 r. o samorządzie gminnym (t.j.\n"
        "Dz.U. z 2025r. poz. 1153 ze zm.) uchwala się co następuje:\n"
        "   § 1. Określa się wysokość stawek podatku od nieruchomości:\n"
        " 1) od gruntów:\n"
        "   a) związanych z prowadzeniem działalności gospodarczej bez względu na sposób zakwalifikowania\n"
        "      w ewidencji gruntów i budynków – 1,00 zł od 1 m² powierzchni,\n"
        "Id: 9E7C6D3A-1234-5678-ABCD-0123456789AB. Podpisany                                   Strona 1\n"
        "\x0cDziennik Urzędowy Województwa Małopolskiego       –2–                                             Poz. 7877\n\n"
        " 3) od budowli – 2% ich wartości określonej na podstawie art.4 ust. 1 pkt 3 ustawy o podatkach\n"
        "    i opłatach lokalnych.\n"
        "   § 2. Wykonanie uchwały powierza się Burmistrzowi.\n"
        "Strona 2\n"
        "\x0c")

    def test_czyszczenie_i_scalanie(self):
        txt, strony, naglowek = edz._czysc_pdf(self.SUROWY)
        self.assertEqual(strony, 2)
        self.assertEqual(naglowek, "Kraków, dnia 16 grudnia 2025 r., poz. 7877")
        for smiec in ("DZIENNIK URZĘDOWY", "Podpisany przez", "Data: 16.12", "Poz. 7877", "–2–", "Id: 9E7C", "Strona 1", "Strona 2"):
            self.assertNotIn(smiec, txt, smiec)
        self.assertIn("o samorządzie gminnym (t.j. Dz.U. z 2025r. poz. 1153 ze zm.) uchwala się", txt)
        self.assertIn("bez względu na sposób zakwalifikowania w ewidencji gruntów i budynków – 1,00 zł od 1 m² powierzchni,", txt)
        self.assertIn("ustawy o podatkach i opłatach lokalnych.", txt)
        linie = txt.split("\n")
        self.assertIn("§ 1. Określa się wysokość stawek podatku od nieruchomości:", linie)
        self.assertIn("§ 2. Wykonanie uchwały powierza się Burmistrzowi.", linie)
        self.assertTrue(any(l.startswith("1) od gruntów") for l in linie))

    def test_bez_naglowka_dziennika(self):
        txt, strony, naglowek = edz._czysc_pdf("   § 1. Tekst.\n   § 2. Koniec.\n\x0c")
        self.assertEqual((strony, naglowek), (1, None))
        self.assertEqual(txt, "§ 1. Tekst.\n§ 2. Koniec.")


class TestOcenaTekstu(unittest.TestCase):
    """D4/D5: wykrywanie tekstu niezweryfikowanego (U+FFFD, brak §/Art., text.html = 1. strona)."""

    def test_pdf_poprawny_bez_uwag(self):
        self.assertEqual(edz._ocena_tekstu("§ 1. Tekst.\n§ 2. Koniec.", "pdf"), ([], []))

    def test_fffd_blokuje(self):
        ostrz, blok = edz._ocena_tekstu("§ 1. Tekst \ufffd\ufffd.", "pdf")
        self.assertTrue(any("U+FFFD" in b for b in blok))

    def test_html_zawsze_niezweryfikowany(self):
        ostrz, blok = edz._ocena_tekstu("§ 1. Tekst.\n§ 2. Koniec.", "html")
        self.assertTrue(any("PIERWSZĄ STRONĘ" in b for b in blok))

    def test_pdf_narracyjny_tylko_ostrzega(self):
        # rozstrzygnięcie nadzorcze bez § — poprawny tekst z PDF, strict NIE blokuje
        txt = "ROZSTRZYGNIĘCIE NADZORCZE\n" + "Stwierdza się nieważność uchwały. " * 20
        ostrz, blok = edz._ocena_tekstu(txt, "pdf")
        self.assertEqual(blok, [])
        self.assertTrue(ostrz)

    def test_pdf_krotki_bez_jednostek_blokuje(self):
        ostrz, blok = edz._ocena_tekstu("skan", "pdf")
        self.assertTrue(blok)


class TestStronicuj(unittest.TestCase):
    # regresja BUG 2026-07-23 (PM/Rumia): paginacja MUSI działać na liście przefiltrowanych
    # trafień i strony 1..N muszą pokrywać cały policzony zbiór (bez fałszywych negatywów)
    def test_strony_pokrywaja_caly_zbior(self):
        trafienia = list(range(43))  # 43 trafienia jak w zgłoszeniu
        _, _, strony = edz._stronicuj(trafienia, 10, 1)
        self.assertEqual(strony, 5)
        zebrane = []
        for s in range(1, strony + 1):
            okno, start, _ = edz._stronicuj(trafienia, 10, s)
            self.assertEqual(start, (s - 1) * 10)
            zebrane.extend(okno)
        self.assertEqual(zebrane, trafienia)

    def test_okno_rowne_limitowi(self):
        okno, start, strony = edz._stronicuj(list(range(43)), 50, 1)
        self.assertEqual((len(okno), start, strony), (43, 0, 1))

    def test_ostatnia_strona_czesciowa(self):
        okno, _, _ = edz._stronicuj(list(range(43)), 10, 5)
        self.assertEqual(okno, [40, 41, 42])

    def test_strona_poza_zakresem_pusta(self):
        okno, _, strony = edz._stronicuj(list(range(43)), 10, 6)
        self.assertEqual((okno, strony), ([], 5))

    def test_pusty_zbior(self):
        self.assertEqual(edz._stronicuj([], 10, 1), ([], 0, 1))


class TestFragmenty(unittest.TestCase):
    TEKST = ("Preambuła.\n"
             "§ 1. Określa się stawki:\n1) od gruntów – 1,00 zł;\n2) od budowli – 2% wartości.\n"
             "§ 2. Traci moc uchwała.\n"
             "§ 10. Inny paragraf.\n"
             "Załącznik do uchwały\n"
             "§ 1. 1. Żłobek nosi nazwę.\n2. Siedziba.\n"
             "§ 2. Koniec statutu.\n")

    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "PLAN" + ("Y" * 50)
        spans = edz._fragmenty(txt, "plan")
        self.assertEqual(len(spans), 1)
        self.assertIn("PLAN", txt[spans[0][0]:spans[0][1]])

    def test_brak_trafien(self):
        self.assertEqual(edz._fragmenty("dowolny tekst", "nie ma"), [])

    def test_cala_jednostka_do_nastepnego_paragrafu(self):
        # D7: „§ 1" = CAŁY § 1 (do następnego §), nie okno 600 znaków; oba wystąpienia (uchwała + załącznik)
        spans = edz._fragmenty(self.TEKST, "§ 1")
        self.assertEqual(len(spans), 2)
        pierwszy = self.TEKST[spans[0][0]:spans[0][1]]
        self.assertTrue(pierwszy.startswith("§ 1. Określa się"))
        self.assertIn("2) od budowli – 2% wartości.", pierwszy)
        self.assertNotIn("§ 2.", pierwszy)
        drugi = self.TEKST[spans[1][0]:spans[1][1]]
        self.assertTrue(drugi.startswith("§ 1. 1. Żłobek"))
        self.assertIn("2. Siedziba.", drugi)
        self.assertNotIn("Koniec statutu", drugi)

    def test_paragraf_1_nie_lapie_10(self):
        spans = edz._fragmenty(self.TEKST, "§ 10")
        self.assertEqual(len(spans), 1)
        self.assertTrue(self.TEKST[spans[0][0]:spans[0][1]].startswith("§ 10. Inny"))
        self.assertNotIn("Załącznik", self.TEKST[spans[0][0]:spans[0][1]])

    def test_ostatni_paragraf_konczy_sie_na_zalaczniku(self):
        spans = edz._fragmenty(self.TEKST, "§ 10.")
        self.assertEqual(self.TEKST[spans[0][0]:spans[0][1]].strip(), "§ 10. Inny paragraf.")

    def test_artykul(self):
        t = "Art. 1. Pierwszy.\nArt. 2. Drugi.\n"
        spans = edz._fragmenty(t, "art. 2")
        self.assertEqual(t[spans[0][0]:spans[0][1]].strip(), "Art. 2. Drugi.")

    def test_okno_rozszerzone_do_granic_linii(self):
        # D7: okno nigdy nie tnie w pół słowa — rozszerzane do granic linii
        t = "linia pierwsza\n" + ("słowo " * 200) + "FRAZA " + ("dalej " * 200) + "\nlinia ostatnia"
        spans = edz._fragmenty(t, "fraza")
        s, e = spans[0]
        self.assertEqual(s, len("linia pierwsza\n"))
        self.assertEqual(t[e - 6:e], "dalej ")
        self.assertNotIn("linia", t[s:e])


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj", "--woj", "mazowieckie"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, edz.cmd_szukaj
        edz.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            edz.main()
        finally:
            sys.argv, edz.cmd_szukaj = oryg_argv, oryg_cmd
        return zlapane

    def test_flaga_po_komendzie(self):
        self.assertTrue(self._parsuj(self.ARGV + ["--json"])["json"])

    def test_flaga_przed_komenda(self):
        self.assertTrue(self._parsuj(["--json"] + self.ARGV)["json"])

    def test_bez_flagi(self):
        self.assertFalse(self._parsuj(self.ARGV)["json"])


class TestStrict(unittest.TestCase):
    """--strict: rocznik bez listy aktów = wynik NIEKOMPLETNY → blokada; domyślnie głośne ostrzeżenie.
    Zero trafień kończy się komunikatem także z --json."""
    ROCZNIK = {"items": [{"pos": 10, "title": "Uchwała w sprawie statutu gminy X", "year": 2026}],
               "totalcount": 1}

    def _fake_get(self, host, path, params=None, raw=False):
        if path.endswith("/2026"):
            return self.ROCZNIK
        return None  # rocznik 2025 → nieoczekiwana odpowiedź

    def _uruchom(self, argv):
        out = io.StringIO()
        with mock.patch.object(edz, "_get", side_effect=self._fake_get), \
                mock.patch.object(edz, "_roczniki", return_value={"years": [2026, 2025]}), \
                mock.patch.object(sys, "argv", ["edzienniki.py", *argv]), \
                contextlib.redirect_stdout(out):
            try:
                edz.main()
            except SystemExit as e:
                return out.getvalue(), e
        return out.getvalue(), None

    def test_strict_blokuje_gdy_rocznik_pominiety_i_stdout_jest_pusty(self):
        out, e = self._uruchom(["szukaj", "statut", "--woj", "DS", "--strict"])
        self.assertIsNotNone(e)
        self.assertIn("rocznika 2025", str(e))
        self.assertEqual(out, "")

    def test_domyslnie_ostrzega_o_pominietym_roczniku(self):
        out, e = self._uruchom(["szukaj", "statut", "--woj", "DS"])
        self.assertIsNone(e)
        self.assertIn("2025 POMINIĘTE", out)
        self.assertIn("statutu gminy X", out)

    def test_json_zero_trafien_konczy_sie_komunikatem(self):
        out, e = self._uruchom(["szukaj", "zzqq", "--woj", "DS", "--rok", "2026", "--json"])
        self.assertIsNotNone(e)
        self.assertIn("Brak wyników", str(e))
        self.assertEqual(out, "")

    def test_strict_po_komendzie_i_przed(self):
        for argv in (["szukaj", "statut", "--woj", "DS", "--rok", "2026", "--strict"],
                     ["--strict", "szukaj", "statut", "--woj", "DS", "--rok", "2026"]):
            out, e = self._uruchom(argv)
            self.assertIsNone(e, argv)
            self.assertIn("statutu gminy X", out)


def _uruchom(argv, **patches):
    """main() z podmienionymi funkcjami sieciowymi → (stdout, SystemExit|None)."""
    out = io.StringIO()
    with contextlib.ExitStack() as st:
        for nazwa, wartosc in patches.items():
            st.enter_context(mock.patch.object(edz, nazwa, **wartosc))
        st.enter_context(mock.patch.object(sys, "argv", ["edzienniki.py", *argv]))
        st.enter_context(contextlib.redirect_stdout(out))
        try:
            edz.main()
        except SystemExit as e:
            return out.getvalue(), e
    return out.getvalue(), None


class TestListaRocznika(unittest.TestCase):
    """D2/D5/D11: lista rocznika bez magicznego limit=100000; niepełna lista = głośne ostrzeżenie,
    w strict blokada; nagłówek mówi prawdę o faktycznie przeszukanych rocznikach; status etykietowany."""

    def _item(self, pos, status="obowiązujący"):
        return {"pos": pos, "year": 2026, "title": f"Uchwała nr {pos} Rady Gminy Ryjewo w sprawie planu",
                "type": "Uchwała", "status": status,
                "promulgation": "2026-08-12T00:00:00", "announcementdate": "2026-08-20T13:59:37.743"}

    def test_bez_parametru_limit_i_pelna_lista(self):
        wywolania = []

        def fake_get(host, path, params=None, raw=False):
            wywolania.append((path, params))
            return {"items": [self._item(1), self._item(2)], "totalcount": 2}
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026", "--strict"],
                          _get={"side_effect": fake_get})
        self.assertIsNone(e)
        self.assertEqual(wywolania[0], ("/acts/POL_WOJ_PM/2026", None))
        self.assertFalse(any(p and p.get("limit") == 100000 for _, p in wywolania))
        self.assertIn("2026: 2/2", out)
        self.assertIn("data aktu: 2026-08-12  · ogłoszono: 2026-08-20", out)

    def test_niepelna_lista_ponawia_z_innym_limitem(self):
        wywolania = []

        def fake_get(host, path, params=None, raw=False):
            wywolania.append((path, params))
            if params:  # ponowienie z limitem → pełna lista
                return {"items": [self._item(1), self._item(2), self._item(3)], "totalcount": 3}
            return {"items": [self._item(1), self._item(2)], "totalcount": 3}
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026", "--strict"],
                          _get={"side_effect": fake_get})
        self.assertIsNone(e)
        self.assertEqual(wywolania[1][1], {"limit": 503})
        self.assertIn("[2026/3]", out)
        self.assertNotIn("NIEPEŁNA", out)

    def test_nadal_niepelna_ostrzega_a_strict_blokuje(self):
        def fake_get(host, path, params=None, raw=False):
            if path.count("/") == 4:
                return None  # rekord aktu (weryfikacja statusu w strict) — nieistotne tutaj
            return {"items": [self._item(1), self._item(2)], "totalcount": 3330}
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026"], _get={"side_effect": fake_get})
        self.assertIsNone(e)
        self.assertIn("NIEPEŁNA", out)
        self.assertIn("brakuje 3328 NAJNOWSZYCH", out)
        self.assertIn("2026: 2/3330 (pobrano 2 — NIEPEŁNA)", out)
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026", "--strict"],
                          _get={"side_effect": fake_get})
        self.assertIsNotNone(e)
        self.assertIn("NIEPEŁNA", str(e))
        self.assertEqual(out, "")

    def test_json_niepelna_lista_ma_flage(self):
        import json

        def fake_get(host, path, params=None, raw=False):
            return {"items": [self._item(1)], "totalcount": 5}
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026", "--json"], _get={"side_effect": fake_get})
        self.assertIsNone(e)
        d = json.loads(out)
        self.assertEqual(d["roczniki_niepelne"], {"2026": {"pobrano": 1, "aktow": 5}})
        self.assertFalse(d["roczniki"]["2026"]["pelna"])

    def test_naglowek_mowi_ktore_roczniki_przeszukano(self):
        # D11: bez frazy okno strony wypełnia 2026 → nagłówek NIE twierdzi „2024–2026"
        def fake_get(host, path, params=None, raw=False):
            return {"items": [self._item(i) for i in range(1, 6)], "totalcount": 5}
        out, e = _uruchom(["szukaj", "--woj", "DS", "--limit", "2"], _get={"side_effect": fake_get},
                          _roczniki={"return_value": {"years": [2024, 2025, 2026]}})
        self.assertIsNone(e)
        self.assertIn("przeszukane roczniki: 2026 ", out)
        self.assertNotIn("2024–2026", out)
        self.assertIn("roczniki 2025, 2024 NIE przeszukane", out)
        self.assertIn("z 5 trafień (roczniki 2026)", out)

    def test_strict_weryfikuje_status_w_rekordzie_aktu(self):
        # C09: lista podaje „obowiązujący", rekord aktu „Stwierdzono nieważność aktu" → pokazujemy prawdę
        def fake_get(host, path, params=None, raw=False):
            if path.endswith("/2026/3104"):
                return {"title": "Uchwała", "status": "Stwierdzono nieważność aktu"}
            return {"items": [self._item(3104)], "totalcount": 1}
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026", "--strict"],
                          _get={"side_effect": fake_get})
        self.assertIsNone(e)
        self.assertIn("status (zweryfikowany w rekordzie aktu): Stwierdzono nieważność aktu  "
                      "[lista rocznika podawała: obowiązujący]", out)
        out, e = _uruchom(["szukaj", "Ryjewo", "--woj", "PM", "--rok", "2026"], _get={"side_effect": fake_get})
        self.assertIn("status (wg listy rocznika): obowiązujący", out)


class TestTekst(unittest.TestCase):
    """D3/D4/D5/D7: tekst z PDF przez pdftotext (gdy dostępny), inaczej text.html z głośnym ostrzeżeniem;
    strict blokuje text.html i tekst uszkodzony, NIE blokuje poprawnego tekstu z PDF."""
    HTML = b"<html><p>\xc2\xa7 1. Pierwsza strona.</p><p>\xc2\xa7 2. Koniec strony 1.</p></html>"
    PDF_SUROWY = ("                     DZIENNIK URZĘDOWY\n   WOJEWÓDZTWA X\n"
                  "   Wrocław, dnia 13 sierpnia 2026 r.\n   Poz. 3654\n\n"
                  "   § 1. Pierwsza strona.\n   § 2. Koniec strony 1.\n"
                  "\x0cDziennik Urzędowy Województwa X  –2–  Poz. 3654\n"
                  "   § 3. Druga strona: od budowli – 2% ich\nwartości.\n\x0c")

    def _get(self, host, path, params=None, raw=False):
        if path.endswith("text.pdf"):
            return b"%PDF-1.4 ..."
        if path.endswith("text.html"):
            return self.HTML
        raise AssertionError(path)

    def test_pdf_przez_pdftotext(self):
        out, e = _uruchom(["tekst", "DS", "2026", "3654", "--strict"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": "/usr/bin/pdftotext"},
                          _pdftotext={"return_value": self.PDF_SUROWY})
        self.assertIsNone(e)
        self.assertIn("(tekst z urzędowego PDF przez pdftotext, 2 str.", out)
        self.assertIn("nagłówek dziennika: Wrocław, dnia 13 sierpnia 2026 r., poz. 3654", out)
        self.assertIn("§ 3. Druga strona: od budowli – 2% ich wartości.", out)
        self.assertNotIn("UWAGA", out)

    def test_fragment_cala_jednostka_z_pdf(self):
        out, e = _uruchom(["tekst", "DS", "2026", "3654", "--fragment", "§ 3"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": "/usr/bin/pdftotext"},
                          _pdftotext={"return_value": self.PDF_SUROWY})
        self.assertIsNone(e)
        self.assertIn("§ 3. Druga strona: od budowli – 2% ich wartości.", out)
        self.assertIn("jednostka § 3: 1 wystąpień", out)

    def test_bez_pdftotext_html_z_glosnym_ostrzezeniem(self):
        out, e = _uruchom(["tekst", "DS", "2026", "3654"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": None})
        self.assertIsNone(e)
        self.assertIn("(tekst z text.html", out)
        self.assertIn("UWAGA: text.html zawiera zwykle tylko PIERWSZĄ STRONĘ aktu — to NIE jest pełny tekst; "
                      "pobierz PDF: tekst DS 2026 3654 --pdf", out)
        self.assertIn("zalecane: zainstaluj poppler", out)

    def test_bez_pdftotext_strict_blokuje(self):
        out, e = _uruchom(["--strict", "tekst", "DS", "2026", "3654"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": None})
        self.assertIsNotNone(e)
        self.assertIn("PIERWSZĄ STRONĘ", str(e))
        self.assertEqual(out, "")

    def test_html_nie_znaleziono_frazy_nie_jest_definitywne(self):
        out, e = _uruchom(["tekst", "DS", "2026", "3654", "--fragment", "budowli"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": None})
        self.assertIsNotNone(e)
        self.assertIn("TYLKO 1. strona aktu", str(e))

    def test_uszkodzony_html_fffd(self):
        html = "<p>\ufffd\ufffd\ufffd</p><p>Tworzy się jednostkę onazwie Klub</p>".encode()
        out, e = _uruchom(["tekst", "PL", "2026", "3155"], _get={"return_value": html},
                          _pdftotext_dostepny={"return_value": None})
        self.assertIsNone(e)
        self.assertIn("3 znaków zastępczych U+FFFD", out)
        self.assertIn("brak oznaczeń jednostek", out)
        out, e = _uruchom(["tekst", "PL", "2026", "3155", "--strict"], _get={"return_value": html},
                          _pdftotext_dostepny={"return_value": None})
        self.assertIsNotNone(e)
        self.assertIn("U+FFFD", str(e))

    def test_json_ma_zrodlo_i_uwagi(self):
        import json
        out, e = _uruchom(["tekst", "DS", "2026", "3654", "--json"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": None})
        d = json.loads(out)
        self.assertEqual(d["zrodlo"], "html")
        self.assertTrue(any("PIERWSZĄ STRONĘ" in x for x in d["niezweryfikowany"]))

    def test_pdftotext_pada_to_html(self):
        out, e = _uruchom(["tekst", "DS", "2026", "3654"], _get={"side_effect": self._get},
                          _pdftotext_dostepny={"return_value": "/usr/bin/pdftotext"},
                          _pdftotext={"return_value": None})
        self.assertIsNone(e)
        self.assertIn("pdftotext nie przetworzył PDF", out)
        self.assertIn("(tekst z text.html", out)


class TestAkt(unittest.TestCase):
    """D1/D8: „Data aktu" vs „Ogłoszony" z właściwych pól; powiązania z rejestru dziennika (best-effort)."""
    ELI = {"title": "Uchwała Nr XII/112/2025 Rady Miejskiej w Koszycach z dnia 15 grudnia 2025 r.",
           "type": "Uchwała", "releasedby": ["Rada Gminy Koszyce"], "status": "obowiązujący",
           "announcementdate": "2025-12-15T00:00:00", "promulgation": "2025-12-16T14:57:54.347",
           "displayaddress": "DZ. URZ. WOJ. 2025.7877", "texthtml": True, "textpdf": True}
    REJESTR = {"actdate": "2025-12-15T00:00:00", "publicationdate": "2025-12-16T14:57:54.347",
               "actstatus": {"isinvalid": False, "ispartialinvalid": False, "description": ""},
               "actrelations": [{"relationtype": "JestSprostowaniemDla", "description": "Ma sprostowanie",
                                 "legalactsrelated": [{"year": 2026, "position": 446, "legalacttype": "Obwieszczenie",
                                                       "actdate": "2026-01-26T00:00:00", "casenumber": None,
                                                       "description": "DZ. URZ. WOJ. 2026.446"}]}]}

    def test_daty_i_powiazania(self):
        out, e = _uruchom(["akt", "MP", "2025", "7877"], _get={"return_value": dict(self.ELI)},
                          _get_rejestr={"return_value": self.REJESTR})
        self.assertIsNone(e)
        self.assertIn("Data aktu: 2025-12-15   Ogłoszony (publikacja w dzienniku): 2025-12-16", out)
        self.assertIn("Ma sprostowanie: Obwieszczenie z 2026-01-26 → DZ. URZ. WOJ. 2026.446  [rok 2026 poz. 446]", out)

    def test_rejestr_niedostepny_tylko_ostrzega_takze_w_strict(self):
        out, e = _uruchom(["--strict", "akt", "MP", "2025", "7877"], _get={"return_value": dict(self.ELI)},
                          _get_rejestr={"return_value": None})
        self.assertIsNone(e)
        self.assertIn("Powiązania: nie udało się pobrać rejestru dziennika", out)

    def test_status_z_rejestru_gdy_inny(self):
        rej = dict(self.REJESTR, actstatus={"isinvalid": True, "ispartialinvalid": True,
                                             "description": "Stwierdzono częściową nieważność"}, actrelations=[])
        out, e = _uruchom(["akt", "MP", "2025", "7877"], _get={"return_value": dict(self.ELI)},
                          _get_rejestr={"return_value": rej})
        self.assertIn("Status (rejestr dziennika): Stwierdzono częściową nieważność", out)
        self.assertIn("częściowa nieważność", out)
        self.assertIn("Powiązania: brak w rejestrze dziennika", out)

    def test_json_zawiera_rejestr(self):
        import json
        out, e = _uruchom(["akt", "MP", "2025", "7877", "--json"], _get={"return_value": dict(self.ELI)},
                          _get_rejestr={"return_value": self.REJESTR})
        d = json.loads(out)
        self.assertEqual(d["_rejestr_dziennika"]["actrelations"][0]["description"], "Ma sprostowanie")


class TestTls(unittest.TestCase):
    """D6: niepełny łańcuch certyfikatów → dociągnięcie pośredniego przez AIA i ponowienie z PEŁNĄ
    weryfikacją; gdy się nie uda — komunikat o łańcuchu (nie o geoblokadzie), nigdy CERT_NONE dla treści."""

    def _blad_cert(self):
        return urllib.error.URLError(ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate"))

    def test_aia_urls_z_der(self):
        url = b"http://certumdvtlsg2r39ca.repository.certum.pl/certumdvtlsg2r39ca.cer"
        der = b"\x30\x82" + edz._OID_CA_ISSUERS + b"\x86" + bytes([len(url)]) + url + b"\x30\x1f\x06\x03"
        self.assertEqual(edz._aia_urls(der), [url.decode()])

    def test_aia_urls_dluga_forma_dlugosci(self):
        url = b"http://x.example/" + b"a" * 130 + b".crt"
        der = edz._OID_CA_ISSUERS + b"\x86\x81" + bytes([len(url)]) + url
        self.assertEqual(edz._aia_urls(der), [url.decode()])

    def test_aia_urls_pomija_ocsp_i_crl(self):
        der = (b"\x06\x08\x2b\x06\x01\x05\x05\x07\x30\x01\x86\x10http://ocsp.x.pl/"
               + edz._OID_CA_ISSUERS + b"\x86\x12http://x.pl/ca.crl")
        self.assertEqual(edz._aia_urls(der), [])

    def test_pobrany_posredni_nie_jest_kotwica_zaufania(self):
        # adres pośredniego pochodzi z NIEZWERYFIKOWANEGO liścia — gdyby pobrany certyfikat był
        # kotwicą (PARTIAL_CHAIN), atakujący w tranzycie podstawiłby własny „pośredni"; łańcuch
        # musi kończyć się na samopodpisanym korzeniu z domyślnego magazynu
        url = "http://ca.example/posredni.cer"
        der = edz._OID_CA_ISSUERS + b"\x86" + bytes([len(url)]) + url.encode()
        pem = edz._der_do_pem(b"\x30\x03\x02\x01\x01")
        zaladowane = []
        ctx = ssl.create_default_context()
        with mock.patch.object(edz, "_lisc_der", return_value=der), \
                mock.patch.object(edz, "_SSL_CTX", None), \
                mock.patch.object(edz.ssl, "create_default_context", return_value=ctx), \
                mock.patch.object(ctx, "load_verify_locations", side_effect=lambda cadata: zaladowane.append(cadata)), \
                mock.patch.object(edz.urllib.request, "urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = pem.encode()
            wynik = edz._ctx_z_aia("edziennik.example")
        self.assertIs(wynik, ctx)
        self.assertEqual(zaladowane, [pem])
        self.assertFalse(ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_przekierowanie_http_na_hoscie_dziennika_jest_podnoszone_a_obce_odrzucane(self):
        req = edz.urllib.request.Request("https://edzienniki.duw.pl/api/eli/acts/POL_WOJ_DS/2026/1")
        h = edz._PrzekierowaniaHttps()
        nowy = h.redirect_request(req, None, 302, "Found", {}, "http://edzienniki.duw.pl/api/eli/acts/POL_WOJ_DS/2026/1/text.pdf")
        self.assertEqual(nowy.full_url, "https://edzienniki.duw.pl/api/eli/acts/POL_WOJ_DS/2026/1/text.pdf")
        with self.assertRaisesRegex(edz.urllib.error.URLError, "niezaufany host"):
            h.redirect_request(req, None, 302, "Found", {}, "http://example.test/akt.pdf")

    def test_der_do_pem(self):
        pem = edz._der_do_pem(b"\x30\x03\x02\x01\x01")
        self.assertTrue(pem.startswith("-----BEGIN CERTIFICATE-----\nMAMCAQE=\n-----END CERTIFICATE-----"))
        self.assertEqual(edz._der_do_pem(b"-----BEGIN CERTIFICATE-----\nX\n-----END CERTIFICATE-----\n").count("BEGIN"), 1)

    def test_ponowienie_z_pelna_weryfikacja(self):
        ctx = ssl.create_default_context()
        odp = mock.MagicMock()
        odp.__enter__.return_value.read.return_value = b"[]"
        otworz = mock.Mock(side_effect=[self._blad_cert(), odp])
        with mock.patch.object(edz, "_otworz", otworz), \
                mock.patch.object(edz, "_ctx_z_aia", return_value=ctx), \
                mock.patch.object(edz, "_SSL_CTX", None):
            dane = edz._fetch("https://edziennik.malopolska.uw.gov.pl/api/eli/acts", "edziennik.malopolska.uw.gov.pl")
        self.assertEqual(dane, b"[]")
        self.assertEqual(otworz.call_count, 2)
        self.assertIs(otworz.call_args_list[1].args[2], ctx)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_brak_posredniego_komunikat_o_lancuchu(self):
        otworz = mock.Mock(side_effect=self._blad_cert())
        with mock.patch.object(edz, "_otworz", otworz), \
                mock.patch.object(edz, "_ctx_z_aia", return_value=None), \
                mock.patch.object(edz, "_SSL_CTX", None), mock.patch.object(edz.time, "sleep"):
            with self.assertRaises(SystemExit) as cm:
                edz._fetch("https://dzienniki.luw.pl/api/eli/acts", "dzienniki.luw.pl")
        self.assertIn("niepełny łańcuch certyfikatów po stronie serwera", str(cm.exception))
        self.assertNotIn("spoza PL", str(cm.exception))
        self.assertEqual(otworz.call_count, 1)

    def test_soft_zwraca_none(self):
        urlopen = mock.Mock(side_effect=self._blad_cert())
        with mock.patch.object(edz.urllib.request, "urlopen", urlopen), \
                mock.patch.object(edz, "_ctx_z_aia", return_value=None), mock.patch.object(edz, "_SSL_CTX", None):
            self.assertIsNone(edz._fetch("https://dzienniki.luw.pl/api/legalact", "dzienniki.luw.pl", soft=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
