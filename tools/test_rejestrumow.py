#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for rejestrumow.py pure functions (no network). Run: python3 tools/test_rejestrumow.py"""
import contextlib
import io
import sys
import importlib.util
import pathlib
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rejestrumow", ROOT / "plugins/prawo-pl-rejestr-umow/skills/prawo-pl-rejestr-umow/scripts/rejestrumow.py")
rejestrumow = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rejestrumow)


def ns(**kw):
    """Namespace jak z argparse dla komendy szukaj (wszystkie filtry puste, chyba że podane)."""
    base = dict(fraza=None, jsfp=None, regon=None, nip=None, rola="zamawiajacy",
                wykonawca=None, wykonawca_nip=None, wykonawca_regon=None,
                woj=None, powiat=None, gmina=None, miejscowosc=None,
                status=None, od=None, do=None, pub_od=None, pub_do=None,
                wartosc_od=None, wartosc_do=None,
                zmiana_rodzaj=None, zmiana_od=None, zmiana_do=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


class TestFiltry(unittest.TestCase):
    def test_puste(self):
        self.assertEqual(rejestrumow._filtry(ns()), {})

    def test_fraza_do_przedmiotu(self):
        self.assertEqual(rejestrumow._filtry(ns(fraza="remont drogi")),
                         {"menuGlowne": {"przedmiotUmowy": "remont drogi"}})

    def test_sekcje_rozdzielone(self):
        # --jsfp/--regon/--nip → sekcja jsfp (TYLKO zamawiający), nie menuGlowne (dowolna strona):
        # na żywo REGON 000001301: jsfp 455, inneStronyUmowy 17, menuGlowne 472 = 455 + 17
        body = rejestrumow._filtry(ns(jsfp="gmina", woj="dolnośląskie", wykonawca="KAMA"))
        self.assertEqual(body, {"jsfp": {"wojewodztwo": "dolnośląskie", "nazwa": "gmina"},
                                "inneStronyUmowy": {"nazwa": "KAMA"}})

    def test_regon_nip_domyslnie_tylko_zamawiajacy(self):
        body = rejestrumow._filtry(ns(regon="000001301", nip="8960005408"))
        self.assertEqual(body, {"jsfp": {"regon": "000001301", "nip": "8960005408"}})
        self.assertNotIn("menuGlowne", body)

    def test_rola_dowolna_to_menu_glowne(self):
        body = rejestrumow._filtry(ns(regon="000001301", rola="dowolna"))
        self.assertEqual(body, {"menuGlowne": {"regon": "000001301"}})

    def test_rola_wykonawca_to_inne_strony(self):
        body = rejestrumow._filtry(ns(nip="8960005408", rola="wykonawca"))
        self.assertEqual(body, {"inneStronyUmowy": {"nip": "8960005408"}})

    def test_rola_wykonawca_konflikt_z_wykonawca_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._filtry(ns(nip="8960005408", rola="wykonawca", wykonawca="KAMA"))

    def test_nip_regon_bez_kresek(self):
        # API porównuje dosłownie: '896-000-54-08' → 0 trafień na żywo
        body = rejestrumow._filtry(ns(nip="896-000-54-08", wykonawca_regon="000 001 301"))
        self.assertEqual(body, {"jsfp": {"nip": "8960005408"},
                                "inneStronyUmowy": {"regon": "000001301"}})

    def test_zmiany_umowy_sekcja_najwyzszego_poziomu(self):
        # na żywo: {"zmianyUmowy":{"rodzajZmiany":"TSU02"}} → 1056; zagnieżdżenie 'zmianyUmowie' → cały rejestr
        body = rejestrumow._filtry(ns(zmiana_rodzaj="tsu02", zmiana_od="2026-08-15", zmiana_do="2026-08-23"))
        self.assertEqual(body, {"zmianyUmowy": {"rodzajZmiany": "TSU02", "dataZmianyOd": "2026-08-15",
                                                "dataZmianyDo": "2026-08-23"}})
        self.assertEqual(rejestrumow._filtry(ns(zmiana_rodzaj="INNE")), {"zmianyUmowy": {"rodzajZmiany": "inne"}})

    def test_zmiana_rodzaj_nazwa_zamiast_kodu_exits(self):
        with self.assertRaises(SystemExit):  # 'Aneks do umowy' → 0 trafień na żywo; wymagamy kodu
            rejestrumow._filtry(ns(zmiana_rodzaj="Aneks do umowy"))

    def test_daty_i_wartosci(self):
        body = rejestrumow._filtry(ns(od="2026-07-01", pub_do="2026-07-23", wartosc_od="100 000,50"))
        self.assertEqual(body["menuGlowne"], {"dataZawarciaOd": "2026-07-01",
                                              "dataPublikacjiDo": "2026-07-23",
                                              "wartoscOd": 100000.50})

    def test_zla_data_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._filtry(ns(od="01.07.2026"))  # API wymaga RRRR-MM-DD


class TestLiczba(unittest.TestCase):
    def test_spacje_i_przecinek(self):
        self.assertEqual(rejestrumow._liczba("1 000 000,50"), 1000000.50)

    def test_kropka(self):
        self.assertEqual(rejestrumow._liczba("49999.99"), 49999.99)

    def test_zla_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._liczba("sto złotych")


class TestKwota(unittest.TestCase):
    def test_format_polski(self):
        # separator tysięcy = spacja niełamliwa, przecinek dziesiętny
        self.assertEqual(rejestrumow._kwota(2733508), "2 733 508,00 zł")

    def test_grosze(self):
        self.assertEqual(rejestrumow._kwota(1188.31), "1 188,31 zł")

    def test_none(self):
        self.assertEqual(rejestrumow._kwota(None), "—")


class TestUuid(unittest.TestCase):
    def test_poprawny(self):
        self.assertTrue(rejestrumow._uuid_ok("0002c775-2526-484f-9b93-5a60e2b934c4"))

    def test_wielkie_litery(self):
        self.assertTrue(rejestrumow._uuid_ok("0002C775-2526-484F-9B93-5A60E2B934C4"))

    def test_niepoprawne(self):
        # API zwraca 500 (nie 404) na złe id — walidacja przed wysłaniem jest konieczna
        for zly in ("xyz", "", None, "0002c775-2526-484f-9b93", "0002c775252648 f9b935a60e2b934c4"):
            self.assertFalse(rejestrumow._uuid_ok(zly))


class TestData(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(rejestrumow._data("2026-07-01"), "2026-07-01")

    def test_format_polski_exits(self):
        with self.assertRaises(SystemExit):
            rejestrumow._data("23.07.2026")


class TestPelne(unittest.TestCase):
    def test_usuwa_puste(self):
        self.assertEqual(rejestrumow._pelne({"a": 1, "b": None, "c": "", "d": 0}),
                         {"a": 1, "d": 0})


class TestLimit(unittest.TestCase):
    """API tnie stronę do 50 — nagłówek i numeracja stron muszą mówić o REALNEJ wielkości."""

    def test_obciete_do_50(self):
        self.assertEqual(rejestrumow._limit(100), 50)
        self.assertEqual(rejestrumow._limit(50), 50)

    def test_male_wartosci_bez_zmian(self):
        self.assertEqual(rejestrumow._limit(10), 10)
        self.assertEqual(rejestrumow._limit(1), 1)

    def test_zero_i_ujemne_podnoszone(self):
        self.assertEqual(rejestrumow._limit(0), 1)
        self.assertEqual(rejestrumow._limit(-5), 1)

    def test_obciecie_jest_jawne_na_stderr(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(rejestrumow._limit(100, glosno=True), 50)
            self.assertEqual(rejestrumow._limit(50, glosno=True), 50)
        self.assertIn("--limit 100 obcięty do 50", err.getvalue())
        self.assertEqual(err.getvalue().count("obcięty"), 1)  # 50 nie jest obcinane → bez komunikatu


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, rejestrumow.cmd_szukaj
        rejestrumow.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            rejestrumow.main()
        finally:
            sys.argv, rejestrumow.cmd_szukaj = oryg_argv, oryg_cmd
        return zlapane

    def test_flaga_po_komendzie(self):
        self.assertTrue(self._parsuj(self.ARGV + ["--json"])["json"])

    def test_flaga_przed_komenda(self):
        self.assertTrue(self._parsuj(["--json"] + self.ARGV)["json"])

    def test_bez_flagi(self):
        self.assertFalse(self._parsuj(self.ARGV)["json"])


class TestStrict(unittest.TestCase):
    """--strict: więcej trafień niż okno API = zbiór NIEKOMPLETNY → blokada (domyślnie ostrzeżenie).
    Zero trafień kończy się komunikatem także z --json."""
    UMOWA = {"idUmowy": "0f3c1c3e-1111-4222-8333-444455556666", "nazwa": "Gmina X", "regon": "1",
             "dataZawarciaUmowy": "2026-07-02", "statusUmowy": "obowiązująca",
             "wartoscPrzedmiotuUmowy": 1000, "przedmiotUmowy": "remont drogi"}

    def _uruchom(self, argv, odp):
        out = io.StringIO()
        with mock.patch.object(rejestrumow, "_req", return_value=odp), \
                mock.patch.object(sys, "argv", ["rejestrumow.py", *argv]), \
                contextlib.redirect_stdout(out):
            try:
                rejestrumow.main()
            except SystemExit as e:
                return out.getvalue(), e
        return out.getvalue(), None

    def test_strict_blokuje_zbior_ponad_oknem_api_i_stdout_jest_pusty(self):
        odp = {"content": [self.UMOWA], "totalMatchingElements": rejestrumow.OKNO + 1}
        for argv in (["szukaj", "remont", "--strict"], ["szukaj", "remont", "--json", "--strict"]):
            out, e = self._uruchom(argv, odp)
            self.assertIsNotNone(e, argv)
            self.assertIn("zawęź", str(e).lower())
            self.assertEqual(out, "")

    def test_domyslnie_zbior_ponad_oknem_ostrzega(self):
        odp = {"content": [self.UMOWA], "totalMatchingElements": rejestrumow.OKNO + 1}
        out, e = self._uruchom(["szukaj", "remont"], odp)
        self.assertIsNone(e)
        self.assertIn("UWAGA: API przegląda maks.", out)

    def test_strict_w_granicach_okna_przechodzi(self):
        odp = {"content": [self.UMOWA], "totalMatchingElements": 1}
        out, e = self._uruchom(["szukaj", "remont", "--strict"], odp)
        self.assertIsNone(e)
        self.assertIn("remont drogi", out)

    def test_json_zero_trafien_konczy_sie_komunikatem(self):
        out, e = self._uruchom(["szukaj", "zzqq", "--json"], {"content": [], "totalMatchingElements": 0})
        self.assertIsNotNone(e)
        self.assertIn("Brak wyników", str(e))
        self.assertEqual(out, "")


class TestStronicowanie(unittest.TestCase):
    """Pusta strona ≠ zero trafień. Na żywo (woj. podlaskie, 11 076 umów): offset 9950 → 50 wierszy,
    offset 10 000 → pusta lista i total ZANIŻONY do 10 000. Strona spoza zbioru (455 umów, --strona 12)
    też zwraca pustą listę — to nie jest 'Brak wyników'."""
    UMOWA = TestStrict.UMOWA

    def _uruchom(self, argv, odp):
        out, wywolania = io.StringIO(), []

        def fake_req(path, params=None, body=None):
            wywolania.append(dict(params or {}))
            return odp(params) if callable(odp) else odp
        with mock.patch.object(rejestrumow, "_req", side_effect=fake_req), \
                mock.patch.object(sys, "argv", ["rejestrumow.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                rejestrumow.main()
            except SystemExit as e:
                return out.getvalue(), e, wywolania
        return out.getvalue(), None, wywolania

    def test_strona_poza_zakresem_nie_jest_zerem(self):
        odp = {"content": [], "totalMatchingElements": 455}
        for argv in (["szukaj", "--regon", "1", "--strona", "12", "--limit", "50"],
                     ["szukaj", "--regon", "1", "--strona", "12", "--limit", "50", "--json"]):
            out, e, _ = self._uruchom(argv, odp)
            self.assertIsNotNone(e, argv)
            self.assertIn("poza zakresem", str(e))
            self.assertIn("stron: 10", str(e))
            self.assertIn("--strona 9", str(e))
            self.assertNotIn("Brak wyników", str(e))
            self.assertEqual(out, "")

    def test_strona_za_oknem_api_pobiera_realny_total_i_kaze_zawezic(self):
        def odp(params):  # API: offset ≥ 10 000 → pusta lista, total zaniżony do 10 000
            if params["offset"] >= rejestrumow.OKNO:
                return {"content": [], "totalMatchingElements": rejestrumow.OKNO}
            return {"content": [self.UMOWA] * params["limit"], "totalMatchingElements": 11076}
        for argv in (["szukaj", "--woj", "podlaskie", "--strona", "200", "--limit", "50"],
                     ["szukaj", "--woj", "podlaskie", "--strona", "200", "--limit", "50", "--json"]):
            out, e, wyw = self._uruchom(argv, odp)
            self.assertIsNotNone(e, argv)
            self.assertIn("poza oknem API", str(e))
            self.assertIn("11076", str(e))          # realny total, nie zaniżone 10 000
            self.assertIn("zawęź", str(e).lower())
            self.assertNotIn("Brak wyników", str(e))
            self.assertEqual(out, "")
            # nie strzelamy w offset ≥ 10 000 (API i tak zwraca pusto) — total z pierwszej strony
            self.assertEqual(wyw, [{"offset": 0, "limit": 1, "sortKey": None}])

    def test_strona_za_oknem_ale_zbior_mniejszy_to_poza_zakresem(self):
        odp = {"content": [], "totalMatchingElements": 455}
        out, e, _ = self._uruchom(["szukaj", "--regon", "1", "--strona", "300", "--limit", "50"], odp)
        self.assertIn("poza zakresem", str(e))
        self.assertNotIn("oknem", str(e))

    def test_kolejna_strona_tylko_w_oknie(self):
        odp = {"content": [self.UMOWA] * 50, "totalMatchingElements": 11076}
        out, e, _ = self._uruchom(["szukaj", "--woj", "podlaskie", "--strona", "198", "--limit", "50"], odp)
        self.assertIsNone(e)
        self.assertIn("Kolejna strona: --strona 199", out)
        out, e, _ = self._uruchom(["szukaj", "--woj", "podlaskie", "--strona", "199", "--limit", "50"], odp)
        self.assertIsNone(e)
        self.assertNotIn("Kolejna strona", out)
        self.assertIn("Okno 10000 wyników wyczerpane", out)
        self.assertIn("zawęź", out)

    def test_ostatnia_strona_w_malym_zbiorze(self):
        odp = {"content": [self.UMOWA] * 5, "totalMatchingElements": 455}
        out, e, _ = self._uruchom(["szukaj", "--regon", "1", "--strona", "9", "--limit", "50"], odp)
        self.assertIsNone(e)
        self.assertNotIn("Kolejna strona", out)
        self.assertIn("Ostatnia strona (stron: 10)", out)
        odp = {"content": [self.UMOWA] * 50, "totalMatchingElements": 455}
        out, e, _ = self._uruchom(["szukaj", "--regon", "1", "--strona", "8", "--limit", "50"], odp)
        self.assertIn("Kolejna strona: --strona 9", out)

    def test_pelna_strona_na_koncu_zbioru_bez_kolejnej(self):
        odp = {"content": [self.UMOWA] * 50, "totalMatchingElements": 100}
        out, e, _ = self._uruchom(["szukaj", "--regon", "1", "--strona", "1", "--limit", "50"], odp)
        self.assertIsNone(e)
        self.assertNotIn("Kolejna strona", out)

    def test_stron(self):
        self.assertEqual(rejestrumow._stron(455, 50), 10)
        self.assertEqual(rejestrumow._stron(11076, 50), 200)   # okno ucina do 10 000
        self.assertEqual(rejestrumow._stron(0, 50), 0)
        self.assertEqual(rejestrumow._stron(1, 50), 1)


class TestUmowaFormat(unittest.TestCase):
    """Szczegóły umowy: adres z powiatem i gminą/dzielnicą, wyłączenie jawności bez repr dict/None."""
    STRONA = {"kraj": "Polska", "rodzaj": "Przedsiębiorca", "nazwa": "TEXMET S.C.", "nip": "8982125781",
              "regon": "020646100", "czyKonsorcjum": None,
              "daneAdresowe": {"ulica": "ul. Mikołaja Reja", "numerNieruchomosci": "35", "numerLokalu": None,
                               "wojewodztwo": "DOLNOŚLĄSKIE", "powiat": "Wrocław",
                               "gminaMiastoDzielnica": "Wrocław-Śródmieście", "miejscowosc": "Wrocław",
                               "kodPocztowy": "50-338"},
              "niejawnoscStrony": None}
    NIEJAWNA = {"kraj": "Polska", "rodzaj": "Osoba fizyczna", "nazwa": None, "nip": None, "regon": None,
                "imie": None, "nazwisko": None, "czyKonsorcjum": False, "daneAdresowe": {},
                "niejawnoscStrony": {"podstawa": "Art. 5 ust. 2 ustawy o dostępie do informacji publicznej",
                                     "zakres": "Dane strony umowy",
                                     "organLubOsobaWylaczajaca": "osoba odpowiedzialna za przygotowanie umowy",
                                     "komentarz": None}}

    def _strona(self, s):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rejestrumow._strona_umowy(2, s)
        return out.getvalue()

    def test_adres_z_powiatem_i_gmina(self):
        out = self._strona(self.STRONA)
        self.assertIn("ul. Mikołaja Reja 35, 50-338 Wrocław, gmina/dzielnica Wrocław-Śródmieście, "
                      "powiat Wrocław, woj. dolnośląskie", out)

    def test_niejawnosc_sformatowana_bez_repr(self):
        out = self._strona(self.NIEJAWNA)
        self.assertIn("(dane strony niejawne)", out)
        self.assertIn("WYŁĄCZENIE JAWNOŚCI strony: zakres: Dane strony umowy; podstawa: Art. 5 ust. 2", out)
        self.assertIn("wyłączający: osoba odpowiedzialna", out)
        for zly in ("{'", "None", "komentarz"):
            self.assertNotIn(zly, out)

    def test_niejawnosc_helper(self):
        self.assertEqual(rejestrumow._niejawnosc({"podstawa": "INNA", "zakres": "Wartość umowy",
                                                  "organLubOsobaWylaczajaca": "Dyrektor", "komentarz": "art. 29a"}),
                         "zakres: Wartość umowy; podstawa: INNA; wyłączający: Dyrektor; komentarz: art. 29a")
        self.assertEqual(rejestrumow._niejawnosc({"podstawa": None}), "(bez szczegółów)")
        self.assertEqual(rejestrumow._niejawnosc("tekst"), "tekst")

    def test_cmd_umowa_wylaczenie_wartosci(self):
        d = {"idUmowy": "00385481-27c7-4311-83c4-7d3866a18019", "podstawoweDane": {"statusUmowy": "Aktywna"},
             "szczegolyUmowy": {"przedmiotUmowy": "KONCERT", "wartoscPrzedmiotu": None,
                                "niejawnoscWartosciPrzedmiotu": {"podstawa": "INNA", "zakres": "Wartość umowy",
                                                                 "organLubOsobaWylaczajaca": "Dyrektor",
                                                                 "komentarz": None},
                                "opisWartosciPrzedmiotu": None},
             "stronyUmowy": [self.STRONA]}
        out = io.StringIO()
        with mock.patch.object(rejestrumow, "_req", return_value=d), \
                mock.patch.object(sys, "argv", ["rejestrumow.py", "umowa", d["idUmowy"]]), \
                contextlib.redirect_stdout(out):
            rejestrumow.main()
        self.assertIn("WYŁĄCZENIE JAWNOŚCI wartości: zakres: Wartość umowy; podstawa: INNA; wyłączający: Dyrektor",
                      out.getvalue())
        self.assertNotIn("{'", out.getvalue())
        self.assertNotIn("None", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
