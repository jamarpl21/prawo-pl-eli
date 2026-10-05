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

    def setUp(self):
        # tekst odpytuje rejestr dziennika (powiązania) — w tych testach akt bez powiązań, bez sieci
        p = mock.patch.object(edz, "_get_rejestr", return_value={
            "actstatus": {"isinvalid": False, "ispartialinvalid": False, "description": ""}, "actrelations": []})
        p.start()
        self.addCleanup(p.stop)

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
        otworz = mock.Mock(side_effect=self._blad_cert())
        with mock.patch.object(edz, "_otworz", otworz), \
                mock.patch.object(edz, "_ctx_z_aia", return_value=None), mock.patch.object(edz, "_SSL_CTX", None):
            self.assertIsNone(edz._fetch("https://dzienniki.luw.pl/api/legalact", "dzienniki.luw.pl", soft=True))


# --- 2.1.1: powiązania w `tekst`, tytuły rozstrzygnięć, cytat, wejście w życie, pozycje techniczne,
# nagłówek dziennika, diagnoza TLS, filtr Akamai na MZ. Odpowiedzi odtworzone z prawdziwych (2026-10-05).

def _rejestr(pascal):
    """Rejestr dziennika jak po _get_rejestr (klucze znormalizowane do lowercase)."""
    return edz._norm(pascal)


_BRAK = {"IsInvalid": False, "IsPartialInvalid": False, "Description": "", "ShowCheckDateMsg": False}
# DS 2026/584 — uchwała Rady Gminy Rudna; DS 2026/1099 — rozstrzygnięcie nadzorcze (nieważność w części)
REJ_DS584 = _rejestr({
    "Title": "Uchwała nr XXI/142/2026 Rady Gminy Rudna z dnia 21 stycznia 2026 r. w sprawie ustalenia Regulaminu "
             "określającego wysokość oraz szczegółowe warunki przyznawania nauczycielom dodatków",
    "IsTechnicalPosition": False, "BindingDateFrom": "", "ActDate": "2026-01-21T00:00:00",
    "PublicationDate": "2026-02-03T09:56:29.53",
    "ActStatus": {"IsInvalid": False, "IsPartialInvalid": True,
                  "Description": "Stwierdzono częściową nieważność aktu", "ShowCheckDateMsg": False},
    "ActRelations": [
        {"RelationType": "Uchyla", "Description": "Uchyla", "LegalActsRelated": [
            {"CaseNumber": "XXXII/305/2018", "ActDate": "2018-10-18T10:10:55", "LegalActType": "Uchwała",
             "Position": 5364, "Year": 2018, "Description": "DZ. URZ. WOJ. 2018.5364"}]},
        {"RelationType": "PartialDecision", "Description": "Ma rozstrzygnięcie nadzorcze (nieważność w części)",
         "LegalActsRelated": [
             {"CaseNumber": "PNK-N.4131.117.1.2026.AO", "ActDate": "2026-02-25T16:10:52",
              "LegalActType": "Rozstrzygnięcie nadzorcze", "Position": 1099, "Year": 2026,
              "Description": "DZ. URZ. WOJ. 2026.1099"}]}]})
REJ_DS1099 = _rejestr({
    "Title": "Rozstrzygnięcie nadzorcze nr PNK-N.4131.117.1.2026.AO Wojewody Dolnośląskiego z dnia 25 lutego "
             "2026 r. stwierdzające nieważność § 6 ust. 4 we fragmencie „wyniki pracy placówki” uchwały nr "
             "XXI/142/2026 Rady Gminy Rudna  z dnia 21 stycznia 2026 r. w sprawie ustalenia Regulaminu",
    "IsTechnicalPosition": False, "ActStatus": _BRAK,
    "ActRelations": [{"RelationType": "PartialDecision",
                      "Description": "Jest rozstrzygnięciem nadzorczym dla (nieważność w części)",
                      "LegalActsRelated": [{"Position": 584, "Year": 2026, "LegalActType": "Uchwała"}]}]})
# WP 2025/9326 — RIO stwierdziła nieważność § 2 (tytuł uchwały RIO jednostki NIE podaje)
REJ_WP9326 = _rejestr({
    "Title": "Uchwała nr 114/25 Rady Gminy Rzgów z dnia 28 listopada 2025 r. w sprawie określenia wysokości "
             "stawek podatku od nieruchomości na 2026 rok", "IsTechnicalPosition": False, "BindingDateFrom": "",
    "ActStatus": {"IsInvalid": False, "IsPartialInvalid": True,
                  "Description": "Stwierdzono częściową nieważność aktu", "ShowCheckDateMsg": False},
    "ActRelations": [{"RelationType": "PartialDecision",
                      "Description": "Ma rozstrzygnięcie nadzorcze (nieważność w części)",
                      "LegalActsRelated": [{"CaseNumber": "24.1323.2025", "ActDate": "2025-12-17T00:00:00",
                                            "LegalActType": "Uchwała", "Position": 9932, "Year": 2025,
                                            "Description": "DZ. URZ. WOJ. 2025.9932"}]}]})
REJ_WP9932 = _rejestr({
    "Title": "Uchwała nr 24.1323.2025 Kolegium Regionalnej Izby Obrachunkowej w Poznaniu z dnia 17 grudnia "
             "2025 r. stwierdzające częściową nieważność uchwały Nr 114/25 Rady Gminy Rzgów z dnia 28 listopada "
             "2025 r. w sprawie określenia wysokości stawek podatku od nieruchomości na 2026 rok",
    "IsTechnicalPosition": False, "ActStatus": _BRAK, "ActRelations": []})
# MP 2025/7877 — sprostowana obwieszczeniem MP 2026/446
REJ_MP7877 = _rejestr({
    "Title": "Uchwała Nr XII/112/2025 Rady Miejskiej w Koszycach", "IsTechnicalPosition": False,
    "BindingDateFrom": "", "ActStatus": _BRAK,
    "ActRelations": [{"RelationType": "JestSprostowaniemDla", "Description": "Ma sprostowanie",
                      "LegalActsRelated": [{"CaseNumber": "", "ActDate": "2026-01-26T00:00:00",
                                            "LegalActType": "Obwieszczenie", "Position": 446, "Year": 2026,
                                            "Description": "DZ. URZ. WOJ. 2026.446"}]}]})
REJ_MP446 = _rejestr({"Title": "Obwieszczenie  Wojewody Małopolskiego z dnia 26 stycznia 2026 r. w sprawie "
                               "sprostowania błędu", "IsTechnicalPosition": False, "ActStatus": _BRAK,
                      "ActRelations": []})
# PM 2026/3104 — nieważność w CAŁOŚCI (rozstrzygnięcie PM 2026/3258)
REJ_PM3104 = _rejestr({
    "Title": "Uchwała nr XXXVII/356/2026 Rady Gminy Redzikowo", "IsTechnicalPosition": False,
    "ActStatus": {"IsInvalid": True, "IsPartialInvalid": False, "Description": "Stwierdzono nieważność aktu",
                  "ShowCheckDateMsg": False},
    "ActRelations": [{"RelationType": "FullDecision", "Description": "Ma rozstrzygnięcie nadzorcze (nieważność w całości)",
                      "LegalActsRelated": [{"CaseNumber": "PN-I.4131.47.2026.BS", "ActDate": "2026-08-07T00:00:00",
                                            "LegalActType": "Rozstrzygnięcie nadzorcze", "Position": 3258,
                                            "Year": 2026, "Description": "DZ. URZ. WOJ. 2026.3258"}]}]})
REJ_PM3258 = _rejestr({"Title": "Rozstrzygnięcie nadzorcze nr PN-I.4131.47.2026.BS Wojewody Pomorskiego z dnia "
                                "7 sierpnia 2026 r. w sprawie stwierdzenia nieważności uchwały Nr XXXVII/356/2026",
                       "IsTechnicalPosition": False, "ActStatus": _BRAK, "ActRelations": []})
# DS 2018/5364 — „Akt uchylony" z IsInvalid=true (flaga NIE oznacza nieważności)
REJ_DS5364 = _rejestr({
    "Title": "Uchwała nr XXXII/305/2018 Rady Gminy Rudna", "IsTechnicalPosition": False,
    "ActStatus": {"IsInvalid": True, "IsPartialInvalid": True, "Description": "Akt uchylony", "ShowCheckDateMsg": False},
    "ActRelations": [{"RelationType": "Uchyla", "Description": "Uchylany przez",
                      "LegalActsRelated": [{"CaseNumber": "XXI/142/2026", "ActDate": "2026-01-21T00:00:00",
                                            "LegalActType": "Uchwała", "Position": 584, "Year": 2026,
                                            "Description": "DZ. URZ. WOJ. 2026.584"}]}]})
# SL 2026/5464 — pozycja techniczna
REJ_SL5464 = _rejestr({"Title": "Z przyczyn technicznych pod tym numerem pozycji nie został opublikowany żaden akt prawny",
                       "IsTechnicalPosition": True, "LegalActType": None, "ActStatus": _BRAK, "ActRelations": []})

# fragment tekstu DS 2026/584 po pdftotext -layout (§ 6 ust. 4 zawiera unieważniony fragment)
PDF_DS584 = ("                     DZIENNIK URZĘDOWY\n             WOJEWÓDZTWA DOLNOŚLĄSKIEGO\n\n"
             "                    Wrocław, dnia 3 lutego 2026 r.\n                    Poz. 584\n\n"
             "   § 6. 1. Dyrektorowi przysługuje dodatek funkcyjny w wysokości od 20% do 60% jego wynagrodzenia.\n"
             "4. Wysokość dodatku funkcyjnego dla Dyrektora ustala Wójt Gminy w granicach stawek określonych w ust. 1\n"
             "uwzględniając m.in. wielkość placówki, wyniki pracy placówki.\n"
             "5. Wysokość dodatku funkcyjnego dla wicedyrektora ustala Dyrektor.\n"
             "   § 7. 1. Nauczycielowi przysługuje dodatek za warunki pracy.\n\x0c")


def _rejestry(mapa):
    """side_effect dla _get_rejestr: (rok, poz) → rejestr (rejestr aktu ≠ rejestr rozstrzygnięcia)."""
    return lambda host, rok, poz: mapa.get((int(rok), int(poz)))


def _get_pdf(host, path, params=None, raw=False):
    if path.endswith("text.pdf"):
        return b"%PDF-1.4 ..."
    raise AssertionError(path)


def _tekst(argv, rejestry, pdf=PDF_DS584):
    return _uruchom(argv, _get={"side_effect": _get_pdf}, _get_rejestr={"side_effect": _rejestry(rejestry)},
                    _pdftotext_dostepny={"return_value": "/usr/bin/pdftotext"}, _pdftotext={"return_value": pdf})


class TestTekstPowiazania(unittest.TestCase):
    """Pkt 1: `tekst` pokazuje nieważność/sprostowania jak `akt`; strict blokuje akt nieważny w całości,
    fragment z unieważnionej jednostki i brak rejestru; nieważność w części / sprostowanie = ostrzeżenie."""
    DS = {(2026, 584): REJ_DS584, (2026, 1099): REJ_DS1099}

    def test_ds584_fragment_w_uniewaznionej_jednostce_ostrzega(self):
        out, e = _tekst(["tekst", "DS", "2026", "584", "--fragment", "wyniki pracy placówki"], self.DS)
        self.assertIsNone(e)
        self.assertIn("UWAGA: NIEWAŻNOŚĆ W CZĘŚCI — Rozstrzygnięcie nadzorcze nr PNK-N.4131.117.1.2026.AO z 2026-02-25 "
                      "(DZ. URZ. WOJ. 2026.1099; tekst DS 2026 1099)", out)
        self.assertIn("unieważniona jednostka: § 6 ust. 4 we fragmencie „wyniki pracy placówki”", out)
        self.assertIn("UWAGA: wypisany fragment leży w § 6 — jednostce objętej stwierdzeniem nieważności", out)
        self.assertLess(out.index("NIEWAŻNOŚĆ W CZĘŚCI"), out.index("wyniki pracy placówki."))

    def test_ds584_strict_blokuje_uniewazniony_fragment(self):
        out, e = _tekst(["tekst", "DS", "2026", "584", "--fragment", "wyniki pracy placówki", "--strict"], self.DS)
        self.assertIsNotNone(e)
        self.assertIn("jednostce objętej stwierdzeniem nieważności", str(e))
        self.assertIn("§ 6 ust. 4", str(e))
        self.assertEqual(out, "")

    def test_ds584_strict_inna_jednostka_przechodzi_z_ostrzezeniem(self):
        out, e = _tekst(["tekst", "DS", "2026", "584", "--fragment", "§ 7", "--strict"], self.DS)
        self.assertIsNone(e)
        self.assertIn("NIEWAŻNOŚĆ W CZĘŚCI", out)
        self.assertNotIn("wypisany fragment leży", out)
        self.assertIn("§ 7. 1. Nauczycielowi", out)

    def test_wp9326_zakres_nieznany_wskazuje_tekst_rozstrzygniecia(self):
        out, e = _tekst(["tekst", "WP", "2025", "9326", "--strict"],
                        {(2025, 9326): REJ_WP9326, (2025, 9932): REJ_WP9932})
        self.assertIsNone(e)
        self.assertIn("NIEWAŻNOŚĆ W CZĘŚCI — Uchwała nr 24.1323.2025 z 2025-12-17", out)
        self.assertIn("Kolegium Regionalnej Izby Obrachunkowej w Poznaniu", out)
        self.assertIn("zakres nieważności nie wynika z tytułu — sprawdź treść: tekst WP 2025 9932", out)

    def test_mp7877_sprostowanie(self):
        out, e = _tekst(["tekst", "MP", "2025", "7877", "--strict"],
                        {(2025, 7877): REJ_MP7877, (2026, 446): REJ_MP446})
        self.assertIsNone(e)
        self.assertIn("UWAGA: SPROSTOWANIE — Obwieszczenie z 2026-01-26 (DZ. URZ. WOJ. 2026.446; tekst MP 2026 446): "
                      "„Obwieszczenie Wojewody Małopolskiego z dnia 26 stycznia 2026 r. w sprawie sprostowania błędu”", out)
        self.assertIn("BEZ sprostowania", out)

    def test_pm3104_niewaznosc_w_calosci(self):
        rej = {(2026, 3104): REJ_PM3104, (2026, 3258): REJ_PM3258}
        out, e = _tekst(["tekst", "PM", "2026", "3104"], rej)
        self.assertIsNone(e)
        self.assertIn("UWAGA: AKT NIEWAŻNY W CAŁOŚCI", out)
        self.assertIn("tekst PM 2026 3258", out)
        out, e = _tekst(["--strict", "tekst", "PM", "2026", "3104"], rej)
        self.assertIsNotNone(e)
        self.assertIn("AKT NIEWAŻNY W CAŁOŚCI", str(e))
        self.assertEqual(out, "")

    def test_brak_rejestru_ostrzega_a_strict_blokuje(self):
        out, e = _tekst(["tekst", "DS", "2026", "584"], {})
        self.assertIsNone(e)
        self.assertIn("NIESPRAWDZONE: nieważność", out)
        out, e = _tekst(["tekst", "DS", "2026", "584", "--strict"], {})
        self.assertIsNotNone(e)
        self.assertIn("nie udało się pobrać powiązań", str(e))
        self.assertEqual(out, "")

    def test_json_zawiera_powiazania(self):
        import json
        out, e = _tekst(["tekst", "DS", "2026", "584", "--json"], self.DS)
        d = json.loads(out)
        self.assertEqual(d["cytat"], "Dz. Urz. Woj. Dolnośląskiego z 2026 r. poz. 584")
        czesc = [w for w in d["powiazania"] if w["rodzaj"] == "niewaznosc_czesc"]
        self.assertEqual(czesc[0]["jednostki"], ["§ 6"])
        self.assertTrue(any("NIEWAŻNOŚĆ W CZĘŚCI" in u for u in d["uwagi_powiazan"]))

    def test_uchylony_z_isinvalid_to_nie_niewaznosc(self):
        # IsInvalid=true przy „Akt uchylony" — strict NIE blokuje jak nieważności, ostrzega o uchyleniu
        out, e = _tekst(["--strict", "tekst", "DS", "2018", "5364"], {(2018, 5364): REJ_DS5364})
        self.assertIsNone(e)
        self.assertIn("AKT UCHYLONY przez Uchwała nr XXI/142/2026 z 2026-01-21", out)
        self.assertNotIn("NIEWAŻNY", out)


class TestRodzajRelacji(unittest.TestCase):
    def test_kierunek_z_opisu(self):
        r = edz._rodzaj_relacji
        self.assertEqual(r("Ma rozstrzygnięcie nadzorcze (nieważność w części)"), "niewaznosc_czesc")
        self.assertEqual(r("Ma rozstrzygnięcie nadzorcze (nieważność w całości)"), "niewaznosc_calosc")
        self.assertEqual(r("Ma sprostowanie"), "sprostowanie")
        self.assertEqual(r("Uchylany przez"), "uchylenie")
        self.assertEqual(r("Jest zmieniany przez"), "zmiana")
        for czynne in ("Uchyla", "Zmienia", "Jest sprostowaniem dla",
                       "Jest rozstrzygnięciem nadzorczym dla (nieważność w części)"):
            self.assertEqual(r(czynne), "czynne", czynne)

    def test_status_uchylony_mimo_isinvalid(self):
        self.assertEqual(edz._status_rejestru(REJ_DS5364), "uchylony")
        self.assertEqual(edz._status_rejestru(REJ_PM3104), "niewaznosc_calosc")
        self.assertEqual(edz._status_rejestru(REJ_DS584), "niewaznosc_czesc")


class TestJednostkaZTytulu(unittest.TestCase):
    """Pkt 2: zakres nieważności z tytułu rozstrzygnięcia / uchwały RIO."""

    def test_paragraf_ustep_i_fragment(self):
        self.assertEqual(edz._jednostka_z_tytulu(REJ_DS1099["title"]),
                         ("§ 6 ust. 4 we fragmencie „wyniki pracy placówki”", ["§ 6"]))

    def test_tytul_bez_jednostki(self):
        self.assertEqual(edz._jednostka_z_tytulu(REJ_WP9932["title"]), (None, []))

    def test_kilka_jednostek_zalacznika(self):
        # DS 2018/5584 (rozstrzygnięcie dla DS 2018/5364)
        t = ("Rozstrzygnięcie nadzorcze nr NK-N.4131.117.8.2018.MF Wojewody Dolnośląskiego z dnia 7 listopada 2018 r. "
             "stwierdzające nieważność § 3 ust. 1 i ust. 2 oraz § 9 ust. 1 i ust. 4 załącznika do uchwały nr "
             "XXXII/305/2018 Rady Gminy Rudna z dnia 18 października 2018 r.")
        self.assertEqual(edz._jednostka_z_tytulu(t),
                         ("§ 3 ust. 1 i ust. 2 oraz § 9 ust. 1 i ust. 4 załącznika", ["§ 3", "§ 9"]))

    def test_w_czesci_dotyczacej(self):
        t = ("Rozstrzygnięcie nadzorcze stwierdzające nieważność uchwały nr X Rady Gminy Y w części dotyczącej "
             "§ 3 pkt 2 lit. b")
        self.assertEqual(edz._jednostka_z_tytulu(t), ("§ 3 pkt 2 lit. b", ["§ 3"]))


class TestAktPowiazaniaICytat(unittest.TestCase):
    """Pkt 2 i 7: tytuł rozstrzygnięcia (który § unieważniono), urzędowy cytat, wejście w życie."""
    ELI_DS584 = {"title": "Uchwała nr XXI/142/2026 Rady Gminy Rudna z dnia 21 stycznia 2026 r.", "type": "Uchwała",
                 "releasedby": ["Rada Gminy Rudna"], "status": "Stwierdzono częściową nieważność aktu",
                 "announcementdate": "2026-01-21T00:00:00", "promulgation": "2026-02-03T09:56:29.53",
                 "entryintoforce": "0001-01-01T00:00:00", "validfrom": "0001-01-01T00:00:00",
                 "displayaddress": "DZ. URZ. WOJ. 2026.584", "textpdf": True}

    def test_akt_ds584_tytul_i_zakres_rozstrzygniecia(self):
        wolania = []

        def rej(host, rok, poz):
            wolania.append((rok, poz))
            return {(2026, 584): REJ_DS584, (2026, 1099): REJ_DS1099}.get((rok, poz))
        out, e = _uruchom(["akt", "DS", "2026", "584"], _get={"return_value": dict(self.ELI_DS584)},
                          _get_rejestr={"side_effect": rej})
        self.assertIsNone(e)
        self.assertIn("tytuł: Rozstrzygnięcie nadzorcze nr PNK-N.4131.117.1.2026.AO Wojewody Dolnośląskiego", out)
        self.assertIn("zakres nieważności (z tytułu): § 6 ust. 4 we fragmencie „wyniki pracy placówki”", out)
        self.assertIn("UWAGA: częściowa nieważność aktu (rejestr dziennika) — unieważniono: § 6 ust. 4", out)
        # tytuł dociągany tylko dla rozstrzygnięcia, nie dla uchylanej uchwały 2018/5364
        self.assertEqual(wolania, [(2026, 584), (2026, 1099)])

    def test_cytat_urzedowy(self):
        out, e = _uruchom(["akt", "DS", "2026", "584"], _get={"return_value": dict(self.ELI_DS584)},
                          _get_rejestr={"return_value": None})
        self.assertIn("# Dz. Urz. Woj. Dolnośląskiego z 2026 r. poz. 584", out)
        self.assertIn("Cytat:    Dz. Urz. Woj. Dolnośląskiego z 2026 r. poz. 584  (urzędowa forma; adres w API: "
                      "DZ. URZ. WOJ. 2026.584)", out)
        self.assertEqual(edz._cytat("KP", 2026, 1), "Dz. Urz. Woj. Kujawsko-Pomorskiego z 2026 r. poz. 1")
        self.assertEqual(edz._cytat("WM", 2026, 100), "Dz. Urz. Woj. Warmińsko-Mazurskiego z 2026 r. poz. 100")
        self.assertEqual(edz._cytat("LD", 2025, 7), "Dz. Urz. Woj. Łódzkiego z 2025 r. poz. 7")
        self.assertEqual(edz._cytat("LB", 2026, 100), "Dz. Urz. Woj. Lubelskiego z 2026 r. poz. 100")
        self.assertEqual(edz._cytat("LS", 2026, 100), "Dz. Urz. Woj. Lubuskiego z 2026 r. poz. 100")

    def test_wejscie_w_zycie_placeholder_i_bindingdatefrom(self):
        out, e = _uruchom(["akt", "DS", "2026", "584"], _get={"return_value": dict(self.ELI_DS584)},
                          _get_rejestr={"return_value": None})
        self.assertIn("Wejście w życie: brak w metadanych (ELI: pusta wartość 0001-01-01) — ustal z treści aktu", out)
        rej_sl = dict(REJ_MP446, bindingdatefrom="01.01.2026")   # SL 2026/100: BindingDateFrom „01.01.2026"
        out, e = _uruchom(["akt", "SL", "2026", "100"], _get={"return_value": dict(self.ELI_DS584)},
                          _get_rejestr={"return_value": rej_sl})
        self.assertIn("Wejście w życie: 2026-01-01 (rejestr dziennika, pole BindingDateFrom", out)
        self.assertEqual(edz._data_rejestru(""), None)


class TestPozycjeTechniczne(unittest.TestCase):
    """Pkt 6: pozycje techniczne hosta śląskiego — poza listą trafień, czytelny komunikat w akt/tekst."""
    PUSTA = {"eli": None, "pos": 0, "year": 0, "title": None, "type": None, "displayaddress": None,
             "promulgation": "0001-01-01T00:00:00", "announcementdate": "0001-01-01T00:00:00"}

    def test_szukaj_pomija_pozycje_techniczne(self):
        akt = {"pos": 1, "year": 2026, "title": "Uchwała nr 133/XXIII/2025 Rady Gminy Kroczyce", "type": "Uchwała",
               "status": "obowiązujący", "displayaddress": "DZ. URZ. WOJ. SLA 2026.1",
               "promulgation": "2025-12-17T00:00:00", "announcementdate": "2026-01-02T07:49:15.85"}
        rocznik = {"items": [akt, dict(self.PUSTA), dict(self.PUSTA)], "totalcount": 3}
        out, e = _uruchom(["szukaj", "--woj", "SL", "--rok", "2026"], _get={"return_value": rocznik})
        self.assertIsNone(e)
        self.assertNotIn("[0/0]", out)
        self.assertIn("2026: 1/3", out)
        self.assertIn("pominięto pozycje techniczne bez aktu — 2026: 2", out)
        self.assertNotIn("NIEPEŁNA", out)

    def test_akt_pozycja_techniczna(self):
        out, e = _uruchom(["akt", "SL", "2026", "5464"], _get={"return_value": None},
                          _get_rejestr={"return_value": REJ_SL5464})
        self.assertIsNotNone(e)
        self.assertIn("POZYCJĄ TECHNICZNĄ", str(e))
        self.assertIn("Z przyczyn technicznych", str(e))
        self.assertNotIn("HTTP 400", str(e))

    def test_tekst_pozycja_techniczna_przed_pobraniem_pdf(self):
        out, e = _uruchom(["tekst", "SL", "2026", "5464"], _get={"side_effect": AssertionError("bez PDF")},
                          _get_rejestr={"return_value": REJ_SL5464})
        self.assertIn("POZYCJĄ TECHNICZNĄ", str(e))

    def test_przekierowanie_na_404_notfound_to_404(self):
        req = edz.urllib.request.Request("https://dzienniki.slask.eu/api/eli/acts/POL_WOJ_SL/2026/5464")
        with self.assertRaises(edz.urllib.error.HTTPError) as cm:
            edz._PrzekierowaniaHttps().redirect_request(
                req, None, 302, "Found", {}, "https://dzienniki.slask.eu/api/eli/acts/POL_WOJ_SL/2026/404_notfound")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()

    def test_akt_nieistniejacy_bez_rejestru(self):
        out, e = _uruchom(["akt", "SL", "2026", "99999"], _get={"return_value": None},
                          _get_rejestr={"return_value": None})
        self.assertIn("Nie znaleziono aktu POL_WOJ_SL/2026/99999", str(e))


class TestNaglowekDziennika(unittest.TestCase):
    """Pkt 5: miejscowość wielowyrazowa (LS) i dzień tygodnia po „dnia" (WM) — wiersze z prawdziwych PDF."""

    def _naglowek(self, linia, poz):
        surowy = ("                     DZIENNIK URZĘDOWY\n                 WOJEWÓDZTWA X\n\n" + linia + "\n"
                  "                                                   Data: 26.01.2026 15:59:51\n"
                  f"                                                 Poz. {poz}\n\n   § 1. Tekst.\n\x0c")
        return edz._czysc_pdf(surowy)[2]

    def test_gorzow_wielkopolski(self):
        self.assertEqual(self._naglowek("                         Gorzów Wielkopolski, dnia 26 stycznia 2026 r. "
                                        "Podpisany przez:", 100),
                         "Gorzów Wielkopolski, dnia 26 stycznia 2026 r., poz. 100")

    def test_dzien_tygodnia(self):
        self.assertEqual(self._naglowek("                             Olsztyn, dnia czwartek, 8 stycznia 2026 r."
                                        "            Podpisany przez:", 100),
                         "Olsztyn, dnia 8 stycznia 2026 r., poz. 100")


class TestTlsDiagnoza(unittest.TestCase):
    """Pkt 4: wygasły certyfikat (PM, notAfter 2026-10-01) ≠ niepełny łańcuch — bez AIA, prawdziwa diagnoza."""
    # fragment DER certyfikatu PM: pole validity (UTCTime notBefore/notAfter)
    DER = b"\x30\x1e\x17\x0d251001110527Z\x17\x0d261001110526Z\x30\x1d"

    def _wygasly(self):
        e = ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired (_ssl.c:1082)")
        e.verify_code, e.verify_message = 10, "certificate has expired"
        return urllib.error.URLError(e)

    def test_waznosc_z_der(self):
        self.assertEqual(edz._waznosc_certyfikatu(self.DER), ("2025-10-01 11:05:27 UTC", "2026-10-01 11:05:26 UTC"))
        self.assertIsNone(edz._waznosc_certyfikatu(b"\x30\x00"))

    def test_wygasly_certyfikat_bez_aia_i_z_data(self):
        aia = mock.Mock(return_value=None)
        with mock.patch.object(edz, "_otworz", mock.Mock(side_effect=self._wygasly())), \
                mock.patch.object(edz, "_ctx_z_aia", aia), mock.patch.object(edz, "_lisc_der", return_value=self.DER), \
                mock.patch.object(edz, "_SSL_CTX", None), mock.patch.object(edz.time, "sleep"):
            with self.assertRaises(SystemExit) as cm:
                edz._fetch("https://edziennik.gdansk.uw.gov.pl/api/eli/acts", "edziennik.gdansk.uw.gov.pl")
        msg = str(cm.exception)
        self.assertIn("Certyfikat serwera edziennik.gdansk.uw.gov.pl WYGASŁ (ważny do 2026-10-01 11:05:26 UTC)", msg)
        self.assertNotIn("wysyła niepełny łańcuch", msg)
        self.assertNotIn("SSL_CERT_FILE z dołożonym", msg)
        aia.assert_not_called()

    def test_rodzaj_po_tresci_gdy_brak_kodu(self):
        self.assertEqual(edz._rodzaj_bledu_tls(Exception("certificate verify failed: certificate has expired")), "wygasl")
        self.assertEqual(edz._rodzaj_bledu_tls(Exception("unable to get local issuer certificate")), "lancuch")
        self.assertEqual(edz._rodzaj_bledu_tls(Exception("Hostname mismatch")), "inny")


class TestFiltrMazowiecki(unittest.TestCase):
    """Pkt 3: filtr Akamai na edziennik.mazowieckie.pl przetrzymuje żądania bez Accept-Language i z URL
    w User-Agent (z polskiego IP) — silnik wysyła akceptowane nagłówki, a komunikat nie mówi o „spoza PL"."""

    def test_naglowki_akceptowane_przez_filtr(self):
        odp = mock.MagicMock()
        odp.__enter__.return_value.read.return_value = b"[]"
        otworz = mock.Mock(return_value=odp)
        with mock.patch.object(edz, "_otworz", otworz):
            edz._fetch("https://edziennik.mazowieckie.pl/api/eli/acts", "edziennik.mazowieckie.pl")
        req = otworz.call_args.args[0]
        self.assertTrue(req.get_header("Accept-language"))
        self.assertNotIn("http", req.get_header("User-agent"))

    def test_komunikat_timeoutu_mowi_prawde(self):
        with mock.patch.object(edz, "_otworz", mock.Mock(side_effect=urllib.error.URLError(TimeoutError("timed out")))), \
                mock.patch.object(edz.time, "sleep"):
            with self.assertRaises(SystemExit) as cm:
                edz._fetch("https://edziennik.mazowieckie.pl/api/eli/acts", "edziennik.mazowieckie.pl")
        msg = str(cm.exception)
        self.assertIn("filtrem antybotowym (Akamai)", msg)
        self.assertIn("NIE jest blokada geograficzna", msg)
        self.assertNotIn("spoza PL", msg)

    def test_lista_dziennikow_bez_spoza_pl(self):
        out, e = _uruchom(["dzienniki"])
        self.assertNotIn("spoza PL", out)


# --- pomiar na losowej próbie (2026-10-05): E4 ----------------------------------------------------
# Wycinek `pdftotext -layout` urzędowego PDF LD 2024/794 (dziennik.lodzkie.eu): § 1 uchwały, a w
# Załączniku Nr 3 tabela z kolumną „§" (klasyfikacja) — samotny „§" nad wierszem numeracji kolumn.
PDF_LD794 = (
    "                         DZIENNIK URZĘDOWY\n"
    "                                  WOJEWÓDZTWA ŁÓDZKIEGO\n\n"
    "                                        Łódź, dnia 26 stycznia 2024 r.                         Podpisany przez:\n"
    "                                                                                                Aleksandra Brochocka\n"
    "                                                       Poz. 794\n\n\n"
    "                                            UCHWAŁA NR LXII/367/23\n"
    "                                            RADY GMINY MNISZKÓW\n\n"
    "Rada Gminy Mniszków uchwala, co następuje:\n"
    "    § 1. Dokonuje się zmian w planie dochodów budżetowych, zgodnie z Załącznikiem Nr 1.\n"
    "    § 2. Dokonuje sie zmian w planie wydatków budżetowych, zgodnie z Załącznikiem Nr 2.\n"
    "   § 10. Wykonanie uchwały powierza się Wójtowi Gminy.\n"
    "   § 11. Uchwała wchodzi w życie z dniem podjęcia i podlega ogłoszeniu w Dzienniku Urzędowym Województwa\n"
    "Łódzkiego.\n"
    "\x0cDziennik Urzędowy Województwa Łódzkiego               –5–                                                   Poz. 794\n\n\n\n"
    "                                                            Załącznik Nr 3 do uchwały Nr LXII/367/23\n"
    "                                                            Rady Gminy Mniszków\n\n"
    "                                               Przychody i rozchody\n\n\n\n"
    "Lp.                                   Treść                                  Klasyfikacja         Kwota\n"
    "                                                                                  §\n"
    " 1.                                       2.                                      3.                   4.\n\n"
    "                          Przychody ogółem:                                                       1.804.019,26\n\n"
    " 2.    Wolne środki, których mowa w art. 27 ust. 2 pkt 6 ustawy                   950             1.752.420,00\n"
    "\x0c")


class TestE4ParagrafWTabeliZalacznika(unittest.TestCase):
    """E4: „§ 1" nie łapie nagłówka kolumny „§" + numeracji kolumn „1. 2. 3. 4." z tabeli załącznika;
    trafienia w załączniku są oznaczone „[w załączniku: …]"."""

    def setUp(self):
        p = mock.patch.object(edz, "_get_rejestr", return_value={
            "actstatus": {"isinvalid": False, "ispartialinvalid": False, "description": ""}, "actrelations": []})
        p.start()
        self.addCleanup(p.stop)

    def _tekst(self, pdf, fragment):
        return _uruchom(["tekst", "LD", "2024", "794", "--fragment", fragment], _get={"side_effect": _get_pdf},
                        _pdftotext_dostepny={"return_value": "/usr/bin/pdftotext"}, _pdftotext={"return_value": pdf})

    def test_ld794_paragraf_1_bez_naglowka_tabeli(self):
        out, e = self._tekst(PDF_LD794, "§ 1")
        self.assertIsNone(e)
        self.assertIn("§ 1. Dokonuje się zmian w planie dochodów budżetowych, zgodnie z Załącznikiem Nr 1.", out)
        self.assertNotIn("Przychody ogółem", out)
        self.assertNotIn("[...]", out)
        self.assertIn("jednostka § 1: 1 wystąpień", out)

    def test_samotny_paragraf_nad_numeracja_kolumn_nie_jest_jednostka(self):
        txt, _, _ = edz._czysc_pdf(PDF_LD794)
        self.assertIn("§\n1.  2.  3.  4.", txt)  # tak wygląda tabela po _czysc_pdf
        self.assertEqual(len(edz._fragmenty(txt, "§ 1")), 1)
        self.assertEqual(len(edz._fragmenty(txt, "§ 2")), 1)

    def test_wiersz_numeracji_kolumn_w_jednej_linii_odfiltrowany(self):
        t = ("§ 1. Uchwała.\n§ 2. Wykonanie.\nZałącznik Nr 1\n§ 1.  2.  3.  4.\nDochody ogółem  100,00\n")
        spans = edz._fragmenty(t, "§ 1")
        self.assertEqual([t[s:e].strip() for s, e in spans], ["§ 1. Uchwała."])

    def test_paragraf_z_samym_numerem_ustepu_w_linii_zostaje(self):
        t = "§ 1. 1.\nTreść ustępu pierwszego.\n§ 2. Drugi.\n"
        spans = edz._fragmenty(t, "§ 1")
        self.assertEqual([t[s:e].strip() for s, e in spans], ["§ 1. 1.\nTreść ustępu pierwszego."])

    def test_paragraf_statutu_w_zalaczniku_oznaczony(self):
        out, e = self._tekst("   " + TestFragmenty.TEKST.replace("\n", "\n   "), "§ 1")
        self.assertIsNone(e)
        uchwala, statut = out.split("[...]")
        self.assertNotIn("[w załączniku", uchwala)
        self.assertIn("[w załączniku: Załącznik do uchwały]\n\n§ 1. 1. Żłobek nosi nazwę.", statut)


if __name__ == "__main__":
    unittest.main(verbosity=2)
