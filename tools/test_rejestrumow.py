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


class Wyjscie:
    """Zakończenie main(): kod wyjścia + komunikat ze stderr (main() wypisuje go tam i kończy kodem
    1 = zweryfikowany brak wyników, 2 = wszystko inne)."""

    def __init__(self, code, err):
        self.code, self.err = code, err

    def __str__(self):
        return self.err


def uruchom_main(argv, req=None, side_effect=None):
    """main() z podmienionym _req; zwraca (stdout, Wyjscie albo None)."""
    out, err = io.StringIO(), io.StringIO()
    patch = (mock.patch.object(rejestrumow, "_req", side_effect=side_effect) if side_effect is not None
             else mock.patch.object(rejestrumow, "_req", return_value=req))
    with patch, mock.patch.object(sys, "argv", ["rejestrumow.py", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rejestrumow.main()
        except SystemExit as e:
            return out.getvalue(), Wyjscie(e.code, err.getvalue())
    return out.getvalue(), None


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


class TestListResponseValidation(unittest.TestCase):
    def test_bad_list_responses_fail_before_output(self):
        responses = [None, [], {}, {"error": "Service unavailable"},
                     {"content": None, "totalMatchingElements": 0},
                     {"content": [None], "totalMatchingElements": 1},
                     {"content": [], "totalMatchingElements": "0"},
                     {"content": [], "totalMatchingElements": False},
                     {"content": [], "totalMatchingElements": -1},
                     {"content": [], "totalMatchingElements": 1}]
        for command in (["najnowsze"], ["szukaj", "remont"]):
            for flags in ([], ["--json"], ["--strict"], ["--json", "--strict"]):
                for response in responses:
                    with self.subTest(command=command, flags=flags, response=response):
                        out, e = uruchom_main([*command, *flags], req=response)
                        self.assertIsNotNone(e)
                        self.assertEqual(e.code, 2)      # nieoczekiwana odpowiedź ≠ „brak wyników”
                        self.assertIn("BŁĄD", str(e))
                        self.assertEqual(out, "")

    def test_valid_empty_latest_list_is_not_an_api_error(self):
        out = io.StringIO()
        with mock.patch.object(rejestrumow, "_req", return_value={"content": [], "totalMatchingElements": 0}), \
                mock.patch.object(sys, "argv", ["rejestrumow.py", "najnowsze", "--strict", "--json"]), \
                contextlib.redirect_stdout(out):
            rejestrumow.main()
        self.assertIn('"totalMatchingElements": 0', out.getvalue())


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
        self.assertEqual(rejestrumow._kwota(2733508), "2 733 508,00")

    def test_grosze(self):
        self.assertEqual(rejestrumow._kwota(1188.31), "1 188,31")

    def test_bez_waluty(self):
        # rejestr nie podaje waluty ani netto/brutto — kwota nie może sugerować „zł”
        self.assertNotIn("zł", rejestrumow._kwota(5015))
        self.assertNotIn("PLN", rejestrumow._kwota(5015))

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
        return uruchom_main(argv, req=odp)

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
        wywolania = []

        def fake_req(path, params=None, body=None):
            wywolania.append(dict(params or {}))
            return odp(params) if callable(odp) else odp
        out, e = uruchom_main(argv, side_effect=fake_req)
        return out, e, wywolania

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


class TestPrzekierowaniaHttps(unittest.TestCase):
    def test_host_rejestru_jest_podnoszony_a_obcy_odrzucany(self):
        req = rejestrumow.urllib.request.Request("https://rejestrumow.gov.pl/api-dp/v1/agreements/search")
        h = rejestrumow._PrzekierowaniaHttps()
        nowy = h.redirect_request(req, None, 302, "Found", {}, "http://rejestrumow.gov.pl/api-dp/v1/agreement/x")
        self.assertEqual(nowy.full_url, "https://rejestrumow.gov.pl/api-dp/v1/agreement/x")
        with self.assertRaisesRegex(rejestrumow.urllib.error.URLError, "niezaufany host"):
            h.redirect_request(req, None, 302, "Found", {}, "http://example.test/x")

    def test_req_uzywa_openera_z_kontrola_przekierowan(self):
        odp = mock.MagicMock()
        odp.__enter__.return_value.read.return_value = b'{"content": []}'
        odp.__enter__.return_value.headers = {"Content-Type": "application/json"}
        with mock.patch.object(rejestrumow._opener, "open", return_value=odp) as op:
            rejestrumow._req("/agreements/search", {"limit": 1}, {})
        self.assertEqual(op.call_count, 1)


class TestLimit429(unittest.TestCase):
    """API odpowiada 429 po ~10 szybkich zapytaniach — helper czeka (Retry-After) i ponawia."""

    @staticmethod
    def _429(retry_after=None):
        naglowki = {"Retry-After": retry_after} if retry_after is not None else {}
        return rejestrumow.urllib.error.HTTPError(
            "https://rejestrumow.gov.pl/api-dp/v1/agreements/search", 429, "Too Many Requests",
            naglowki, None)

    @staticmethod
    def _ok():
        odp = mock.MagicMock()
        odp.__enter__.return_value.read.return_value = b'{"content": []}'
        return odp

    def _uruchom(self, side_effect):
        spanie = []
        with mock.patch.object(rejestrumow._opener, "open", side_effect=side_effect) as op, \
                mock.patch.object(rejestrumow.time, "sleep", side_effect=spanie.append):
            wynik = rejestrumow._req("/agreements/search", {"limit": 1}, {})
        return wynik, op.call_count, spanie

    def test_ponawia_zgodnie_z_retry_after(self):
        wynik, proby, spanie = self._uruchom([self._429("3"), self._429("5"), self._ok()])
        self.assertEqual(wynik, {"content": []})
        self.assertEqual(proby, 3)
        self.assertEqual(spanie, [3.0, 5.0])

    def test_bez_naglowka_rosnacy_odstep(self):
        _, proby, spanie = self._uruchom([self._429(), self._429(), self._ok()])
        self.assertEqual(proby, 3)
        self.assertEqual(spanie, [2.0, 4.0])

    def test_retry_after_jako_data_http(self):
        teraz = 1_800_000_000.0
        data = rejestrumow.email.utils.formatdate(teraz + 7, usegmt=True)
        with mock.patch.object(rejestrumow.time, "time", return_value=teraz):
            self.assertAlmostEqual(rejestrumow._retry_after({"Retry-After": data}, 2.0), 7.0, delta=1)
        self.assertEqual(rejestrumow._retry_after({"Retry-After": "bzdura"}, 2.0), 2.0)
        self.assertEqual(rejestrumow._retry_after({}, 2.0), 2.0)

    def test_trwaly_429_konczy_czytelnym_bledem_w_limicie_czekania(self):
        spanie = []
        with mock.patch.object(rejestrumow._opener, "open", side_effect=[self._429("10")] * 20), \
                mock.patch.object(rejestrumow.time, "sleep", side_effect=spanie.append):
            with self.assertRaises(SystemExit) as caught:
                rejestrumow._req("/agreements/search", {"limit": 1}, {})
        msg = str(caught.exception.code)
        self.assertIn("HTTP 429", msg)
        self.assertIn("ogranicza liczbę zapytań", msg)
        self.assertIn("nie uruchamiaj wielu zapytań naraz", msg)
        self.assertLessEqual(sum(spanie), rejestrumow.LIMIT_CZEKANIA_429)
        self.assertGreaterEqual(sum(spanie), 50)

    def test_zbyt_dlugi_retry_after_bez_czekania(self):
        spanie = []
        with mock.patch.object(rejestrumow._opener, "open", side_effect=[self._429("3600")]), \
                mock.patch.object(rejestrumow.time, "sleep", side_effect=spanie.append):
            with self.assertRaisesRegex(SystemExit, "każe czekać jeszcze 3600 s"):
                rejestrumow._req("/agreements/search", {"limit": 1}, {})
        self.assertEqual(spanie, [])

    def test_retry_after_zero_nie_zapetla(self):
        spanie = []
        with mock.patch.object(rejestrumow._opener, "open", side_effect=[self._429("0")] * 200), \
                mock.patch.object(rejestrumow.time, "sleep", side_effect=spanie.append):
            with self.assertRaises(SystemExit):
                rejestrumow._req("/agreements/search", {"limit": 1}, {})
        self.assertLessEqual(len(spanie), rejestrumow.LIMIT_CZEKANIA_429)

    def test_5xx_nadal_jedno_ponowienie(self):
        err = rejestrumow.urllib.error.HTTPError("https://rejestrumow.gov.pl/api-dp/v1/agreements/search",
                                                 503, "Unavailable", {}, None)
        with mock.patch.object(rejestrumow._opener, "open", side_effect=[err, err, err]) as op, \
                mock.patch.object(rejestrumow.time, "sleep"):
            with self.assertRaisesRegex(SystemExit, "HTTP 503"):
                rejestrumow._req("/agreements/search", {"limit": 1}, {})
        self.assertEqual(op.call_count, 2)


class TestKodyWyjscia(unittest.TestCase):
    """0 = wynik; 1 = ZWERYFIKOWANY brak wyników; 2 = wszystko inne (sieć, HTTP, 429, nieoczekiwana
    odpowiedź, zły argument, nieistniejące idUmowy, nieprzewidziany wyjątek). Dotąd sys.exit("…")
    dawał 1 w obu sytuacjach."""
    ZERO = {"content": [], "totalMatchingElements": 0}
    UMOWA = TestStrict.UMOWA

    def test_zero_trafien_to_kod_1_takze_z_json(self):
        for argv in (["szukaj", "zzqq"], ["szukaj", "zzqq", "--json"], ["--json", "szukaj", "--nip", "5732331963"]):
            out, e = uruchom_main(argv, req=self.ZERO)
            self.assertEqual(e.code, 1, argv)
            self.assertIn("Brak wyników", str(e))
            self.assertEqual(out, "")

    def test_wynik_to_kod_0(self):
        out, e = uruchom_main(["szukaj", "remont"], req={"content": [self.UMOWA], "totalMatchingElements": 1})
        self.assertIsNone(e)
        self.assertIn("remont drogi", out)

    def test_strona_poza_zakresem_i_oknem_oraz_strict_to_kod_2(self):
        out, e = uruchom_main(["szukaj", "--regon", "1", "--strona", "12", "--limit", "50"],
                              req={"content": [], "totalMatchingElements": 455})
        self.assertEqual(e.code, 2)
        out, e = uruchom_main(["szukaj", "remont", "--strict"],
                              req={"content": [self.UMOWA], "totalMatchingElements": rejestrumow.OKNO + 1})
        self.assertEqual(e.code, 2)

    def test_zle_argumenty_to_kod_2(self):
        for argv in (["umowa", "xyz"], ["szukaj"], ["szukaj", "--od", "01.07.2026"],
                     ["szukaj", "--wartosc-od", "sto"], ["szukaj", "--zmiana-rodzaj", "Aneks"],
                     ["szukaj", "--zapytanie", "{zly"], ["szukaj", "x", "--strona", "-1"],
                     ["szukaj", "--sort", "zly"]):          # ostatni: błąd argparse
            out, e = uruchom_main(argv, req=self.ZERO)
            self.assertEqual(e.code, 2, argv)

    def test_bledy_http_i_sieci_to_kod_2(self):
        err400 = rejestrumow.urllib.error.HTTPError(
            "https://rejestrumow.gov.pl/api-dp/v1/agreement/x", 400, "Bad Request", {}, io.BytesIO(
                '{"message": "Nie znaleziono umowy", "details": {"[0]": "Agreement not found for ID: x"}}'
                .encode("utf-8")))
        for blad in (err400, rejestrumow.urllib.error.URLError("timeout")):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(rejestrumow._opener, "open", side_effect=[blad, blad]), \
                    mock.patch.object(rejestrumow.time, "sleep"), \
                    mock.patch.object(sys, "argv", ["rejestrumow.py", "umowa",
                                                    "11111111-2222-4333-8444-555555555555"]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as c:
                    rejestrumow.main()
            self.assertEqual(c.exception.code, 2, blad)
            self.assertIn("BŁĄD", err.getvalue())

    def test_trwaly_429_to_kod_2(self):
        e429 = rejestrumow.urllib.error.HTTPError("https://rejestrumow.gov.pl/api-dp/v1/agreements/search",
                                                  429, "Too Many Requests", {"Retry-After": "3600"}, None)
        err = io.StringIO()
        with mock.patch.object(rejestrumow._opener, "open", side_effect=[e429]), \
                mock.patch.object(sys, "argv", ["rejestrumow.py", "najnowsze"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as c:
                rejestrumow.main()
        self.assertEqual(c.exception.code, 2)
        self.assertIn("HTTP 429", err.getvalue())

    def test_nieprzewidziany_wyjatek_to_kod_2_z_krotkim_komunikatem(self):
        out, e = uruchom_main(["umowa", "11111111-2222-4333-8444-555555555555"], req=["nie", "słownik"])
        self.assertEqual(e.code, 2)
        self.assertIn("BŁĄD: nieoczekiwany błąd", str(e))
        self.assertNotIn("Traceback", str(e))

    def test_version_to_kod_0(self):
        with mock.patch.object(sys, "argv", ["rejestrumow.py", "--version"]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as c:
                rejestrumow.main()
        self.assertEqual(c.exception.code, 0)


class TestNieJson(unittest.TestCase):
    """2026-11-13: po serii zapytań poprawne /agreement/{uuid} oddało nie-JSON, a po przerwie JSON —
    komunikat nie może kazać „sprawdzić endpointu”."""

    def _req_z_trescia(self, tresc):
        odp = mock.MagicMock()
        odp.__enter__.return_value.read.return_value = tresc.encode("utf-8")
        with mock.patch.object(rejestrumow._opener, "open", return_value=odp):
            with self.assertRaises(SystemExit) as c:
                rejestrumow._req("/agreement/11111111-2222-4333-8444-555555555555")
        return str(c.exception.code)

    def test_strona_imperva(self):
        # treść SYNTETYCZNA (typowa strona blokady Incapsula) — prawdziwej z 2026-11-13 nie zachowano
        msg = self._req_z_trescia('<html><body>Request unsuccessful. Incapsula incident ID: 123-456'
                                  '<iframe src="/_Incapsula_Resource?x=1"></iframe></body></html>')
        self.assertIn("ochrona CDN", msg)
        self.assertIn("odczekaj", msg)
        self.assertIn("Incapsula incident ID", msg)   # próbka treści do diagnozy

    def test_inny_html(self):
        msg = self._req_z_trescia("<!doctype html><html><head><title>Centralny Rejestr Umów</title></head></html>")
        self.assertIn("nie-JSON", msg)
        self.assertIn("odczekaj", msg)
        self.assertIn("Centralny Rejestr Umów", msg)
        self.assertNotIn("sprawdź endpoint", msg)


class TestBrakWynikowPodpowiedzi(unittest.TestCase):
    """Komunikat zera dobiera podpowiedzi do użytych filtrów; zero ≠ „takich umów nie ma”.
    Na żywo 2026-11-13: --wykonawca „…UBEZPIECZEN” 0, „…UBEZPIECZEŃ” 1342; --nip 5732331964 0."""

    def _msg(self, **kw):
        with self.assertRaises(rejestrumow.BrakWynikow) as c:
            rejestrumow._brak_wynikow(ns(**kw))
        return str(c.exception.code)

    def test_wykonawca_pisownia_bez_frazy_przedmiotu(self):
        msg = self._msg(wykonawca="COMPENSA TOWARZYSTWO UBEZPIECZEN")
        self.assertIn("polskimi znakami", msg)
        self.assertIn("rdzeniem nazwy", msg)
        self.assertIn("tylko DRUGIEJ strony", msg)
        self.assertNotIn("PRZEDMIOCIE", msg)
        self.assertIn("NIE dowodzi", msg)
        self.assertIn("1.07.2026", msg)

    def test_nip_z_bledna_suma(self):
        msg = self._msg(nip="573-233-19-64")
        self.assertIn("NIP 5732331964 ma błędną sumę kontrolną", msg)
        self.assertIn("tylko ZAMAWIAJĄCEGO", msg)

    def test_poprawny_nip_bez_uwagi_o_sumie(self):
        self.assertNotIn("sumę kontrolną", self._msg(nip="5732331963"))

    def test_regon_zla_dlugosc_i_14_cyfr(self):
        self.assertIn("nie ma 9 ani 14 cyfr", self._msg(wykonawca_regon="12345"))
        self.assertNotIn("sumę", self._msg(regon="01751157500147"))   # GDDKiA O/Opole, 14 cyfr
        self.assertIn("błędną sumę", self._msg(regon="01751157500148"))

    def test_fraza_i_daty_przed_startem_rejestru(self):
        msg = self._msg(fraza="remont", od="2026-06-01")
        self.assertIn("PRZEDMIOCIE", msg)
        self.assertIn("przed 1.07.2026", msg)

    def test_suma_kontrolna_na_numerach_z_rejestru(self):
        # NIP/REGON stron umów odczytanych 2026-11-13 (jednostki publiczne i spółki) — wszystkie poprawne
        for nip in ("5732331963", "5751839378", "5291004197", "6681929905", "5260214686", "8361702207"):
            self.assertIsNone(rejestrumow._uwaga_identyfikator("NIP", nip), nip)
        for regon in ("150309847", "000525889", "311019527", "006216959", "750772302", "01751157500147"):
            self.assertIsNone(rejestrumow._uwaga_identyfikator("REGON", regon), regon)


class TestStronyKolejnosc(unittest.TestCase):
    """API oddaje strony 2..n w zmiennej kolejności (umowa 16d6af06-…: 2 z 4 odczytów odwrotnie) —
    wydruk ma być identyczny niezależnie od odczytu, a strona 1 (JSFP z wiersza 'szukaj') zostaje."""
    BIBL = {"rodzaj": "JSFP", "nazwa": "MIEJSKO-GMINNA BIBLIOTEKA PUBLICZNA W TULISZKOWIE",
            "nip": "6681721889", "regon": "000972039", "kraj": "Polska", "daneAdresowe": {}}
    GMINA = {"rodzaj": "JSFP", "nazwa": "GMINA I MIASTO TULISZKÓW", "nip": "6681929905",
             "regon": "311019527", "kraj": "Polska", "daneAdresowe": {}}
    TU = {"rodzaj": "Przedsiębiorca", "nazwa": "COMPENSA TOWARZYSTWO UBEZPIECZEŃ SPÓŁKA AKCYJNA",
          "nip": "5260214686", "regon": "006216959", "kraj": "Polska", "daneAdresowe": {}}

    def _wydruk(self, strony):
        d = {"idUmowy": "16d6af06-0c9e-4ff6-8abd-6105959013df",
             "podstawoweDane": {"statusUmowy": "Aktywna", "dataZawarciaUmowy": "15.10.2026"},
             "szczegolyUmowy": {"przedmiotUmowy": "ubezpieczenie mienia", "wartoscPrzedmiotu": 7455.75},
             "stronyUmowy": strony}
        out, e = uruchom_main(["umowa", d["idUmowy"]], req=d)
        self.assertIsNone(e)
        return out

    def test_wydruk_niezalezny_od_kolejnosci_api(self):
        a = self._wydruk([self.BIBL, self.TU, self.GMINA])
        b = self._wydruk([self.BIBL, self.GMINA, self.TU])
        self.assertEqual(a, b)
        self.assertLess(a.index("1. [JSFP] MIEJSKO-GMINNA"), a.index("2. [JSFP] GMINA I MIASTO"))
        self.assertLess(a.index("2. [JSFP] GMINA"), a.index("3. [Przedsiębiorca] COMPENSA"))
        self.assertIn("strona 1 = JSFP, która wpisała umowę", a)

    def test_dwie_strony_bez_zmian_i_bez_noty(self):
        out = self._wydruk([self.BIBL, self.TU])
        self.assertNotIn("strona 1 = JSFP", out)
        self.assertEqual(rejestrumow._uporzadkuj_strony([self.TU, self.BIBL]), [self.TU, self.BIBL])


class TestUwagaStatus(unittest.TestCase):
    """2026-11-13: 392 z 7 805 umów z „Rozwiązaniem” ma status „Aktywna” (np. umowa CUS w Koszalinie)."""

    def _out(self, status, zmiany):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rejestrumow._uwaga_status(status, zmiany)
        return out.getvalue()

    def test_aktywna_z_rozwiazaniem(self):
        out = self._out("Aktywna", [{"rodzajZmiany": "Rozwiązanie umowy", "dataZmiany": "29.10.2026"}])
        self.assertIn("UWAGA: status „Aktywna”", out)
        self.assertIn("Rozwiązanie umowy", out)

    def test_bez_uwagi(self):
        self.assertEqual(self._out("Nieaktywna", [{"rodzajZmiany": "Wygaśnięcie umowy"}]), "")
        self.assertEqual(self._out("Aktywna", [{"rodzajZmiany": "Aneks do umowy"}]), "")
        self.assertEqual(self._out("Aktywna", []), "")


class TestUwagaWartoscPoAneksie(unittest.TestCase):
    """API ma jedno pole wartości. 2026-11-13: Pałac w Ostoi — rejestr = wartość po aneksie;
    PSSE w Radomiu — komentarz aneksu 608 667,40, rejestr 534 070 — nie zgadujemy, ostrzegamy."""

    def _out(self, zmiany):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rejestrumow._uwaga_wartosc_po_aneksie(zmiany)
        return out.getvalue()

    def test_aneks_o_wartosci(self):
        out = self._out([{"rodzajZmiany": "Aneks do umowy", "dataZmiany": "29.10.2026",
                          "komentarz": "Zmianie uległa wartość umowy na kwotę 608 667,40 zł"}])
        self.assertIn("UWAGA: aneks dotyczy wartości", out)
        self.assertIn("nie podaje wartości pierwotnej", out)

    def test_aneks_bez_wartosci_i_inne_zmiany(self):
        self.assertEqual(self._out([{"rodzajZmiany": "Aneks do umowy", "komentarz": "Zmiana numeru umowy"}]), "")
        self.assertEqual(self._out([{"rodzajZmiany": "Inne", "komentarz": "zmiana wartości"}]), "")
        self.assertEqual(self._out([]), "")


class TestNajnowszeKolejnosc(unittest.TestCase):
    def test_nota_o_dokladnosci_daty_publikacji(self):
        out, e = uruchom_main(["najnowsze", "--limit", "1"],
                              req={"content": [TestStrict.UMOWA], "totalMatchingElements": 1565760})
        self.assertIsNone(e)
        self.assertIn("wszystkich umów: 1565760", out)
        self.assertIn("z dokładnością do dnia", out)
        self.assertIn("wg idUmowy", out)

    def test_kolejna_strona_ostrzega_o_rosnacym_zbiorze(self):
        odp = {"content": [TestStrict.UMOWA] * 10, "totalMatchingElements": 455}
        out, _ = uruchom_main(["szukaj", "--regon", "1"], req=odp)
        self.assertIn("--pub-do", out)
        out, _ = uruchom_main(["szukaj", "--regon", "1", "--pub-do", "2026-11-12"], req=odp)
        self.assertNotIn("rejestr rośnie", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
