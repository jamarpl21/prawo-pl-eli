#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for eureka.py (no network — `_http` is mocked). Run: python3 tools/test_eureka.py"""
import contextlib
import copy
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eureka", ROOT / "plugins/prawo-pl-eureka/skills/prawo-pl-eureka/scripts/eureka.py")
eureka = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eureka)


# --- fixtures: shapes copied from the live API (2026-11-17) ---------------------------------
def _doc(id_, pola, nazwa="Interpretacja indywidualna", version=1):
    return {"id": int(id_), "versionId": version, "nazwa": nazwa,
            "dokument": {"fields": [{"dataType": "StringType", "key": k, "value": v} for k, v in pola.items()]}}


DOC_UCHYLONA = _doc(682533, {  # status „Aktualna”, a WSA uchylił — jedyny ślad w KOMENTARZE_BIP
    "ID_INFORMACJI": "682533", "KATEGORIA_INFORMACJI": "1", "STATUS_INFORMACJI": "27",
    "DATA_PUBLIKACJI": "2026-03-13T10:00:00.000Z", "DT_WYD": "2026-03-08T23:00:00.000Z",
    "SYG": "0112-KDIL2-2.4011.893.2025.2.AA", "TEZA": "Usługi pośrednictwa a art. 29 ust. 1 pkt 5 PIT.",
    "KOMENTARZE_BIP": [58],
    "KOM_BIP_OPIS": "<b>Interpretacja uchylona wyrokiem Wojewódzkiego Sądu Administracyjnego w Poznaniu "
                    "22.07.2026 r. sygn. akt I SA/Po 763/26</b>",
    "TRESC_INTERESARIUSZ": "<p>Interpretacja indywidualna</p><p>Usługi pośrednictwa handlowego…</p>",
    "ZALACZNIKI": []})
META_UCHYLONA = {"ID_INFORMACJI": "682533", "SYG": "0112-KDIL2-2.4011.893.2025.2.AA", "DT_WYD": "2026-03-09",
                 "KATEGORIA_INFORMACJI": ["Interpretacja indywidualna"], "STATUS_INFORMACJI": ["Aktualna"],
                 "KOMENTARZE_BIP": ["Interpretacja uchylona wyrokiem WSA"], "AUTOR": ["Dyrektor Krajowej Informacji Skarbowej"],
                 "KOM_BIP_OPIS": "Interpretacja uchylona wyrokiem Wojewódzkiego Sądu Administracyjnego w Poznaniu "
                                 "22.07.2026 r. sygn. akt I SA/Po 763/26"}
DOC_ZMIENIONA = _doc(669739, {
    "ID_INFORMACJI": "669739", "KATEGORIA_INFORMACJI": "1", "STATUS_INFORMACJI": "29",
    "INFORMACJA_ZMIENIAJACA": "717939", "DT_WYD": "2025-12-03T21:05:39.581Z",
    "SYG": "0114-KDIP4-2.4012.772.2025.2.WH", "TEZA": "Brak prawa do odliczenia…", "KOMENTARZE_BIP": [63],
    "TRESC_INTERESARIUSZ": "<p>Treść</p>", "ZALACZNIKI": []})
META_ZMIENIONA = {"ID_INFORMACJI": "669739", "SYG": "0114-KDIP4-2.4012.772.2025.2.WH",
                  "STATUS_INFORMACJI": ["Nieaktualna/Zmieniona/Wygaszona/Archiwalna"],
                  "KOMENTARZE_BIP": ["Interpretacja zmieniona przez Szefa Krajowej Administracji Skarbowej"],
                  "INFORMACJA_ZMIENIAJACA": "717939"}
DOC_CZYSTA = _doc(718641, {
    "ID_INFORMACJI": "718641", "KATEGORIA_INFORMACJI": "1", "STATUS_INFORMACJI": "27",
    "DT_WYD": "2026-11-10T08:00:00.000Z", "SYG": "0115-KDIT1.4011.700.2026.1.MR",
    "TEZA": "Koszty uzyskania przychodu - waluta wirtualna.",
    "TRESC_INTERESARIUSZ": "<p>Stanowisko nieprawidłowe.</p><p>Koszty uzyskania przychodów z waluty wirtualnej "
                           "ustala się zgodnie z art. 22 ust. 14.</p>", "ZALACZNIKI": []})
META_CZYSTA = {"ID_INFORMACJI": "718641", "SYG": "0115-KDIT1.4011.700.2026.1.MR", "DT_WYD": "2026-11-10",
               "KATEGORIA_INFORMACJI": ["Interpretacja indywidualna"], "STATUS_INFORMACJI": ["Aktualna"],
               "PRZEPISY": ["[PIT] Ustawa o podatku dochodowym od osób fizycznych-Rozdział 4-art. 22-ust. 14"]}
DOC_POTWIERDZONA = _doc(500, {"ID_INFORMACJI": "500", "STATUS_INFORMACJI": "27", "SYG": "X.1",
                              "TRESC_INTERESARIUSZ": "<p>T</p>", "KOMENTARZE_BIP": [53]})
META_POTWIERDZONA = {"ID_INFORMACJI": "500", "SYG": "X.1", "STATUS_INFORMACJI": ["Aktualna"],
                     "KOMENTARZE_BIP": ["Interpretacja potwierdzona prawomocnym wyrokiem"]}


def _wynik(results, total=None):
    return {"results": results, "facetedResults": [], "elasticDocumentId": "x",
            "totalHits": len(results) if total is None else total}


class FakeAPI:
    """Mock `_http`: GET /informacje/{id}, POST /wyszukiwarka/informacje (po filtrze), GET słownika."""

    def __init__(self, docs=None, meta=None, zmieniajace=None, lista=None, sygnatury=None, pula=None):
        self.docs, self.meta = docs or {}, meta or {}
        self.zmieniajace, self.lista, self.sygnatury = zmieniajace or {}, lista, sygnatury or {}
        self.pula = pula or []  # rekordy do filtra SYG: fragment, wielkość liter MA znaczenie (jak EUREKA)
        self.total_syg = None   # wymuszone totalHits dla filtra SYG (symulacja >100 trafień)
        self.wywolania = []

    def __call__(self, path, body=None, binarne=False, brak_ok=False):
        self.wywolania.append((path, body))
        if path.startswith("/informacje/") and path.endswith("/eksport/PDF"):
            return b"%PDF-1.4 test"
        if path.startswith("/informacje/"):
            id_ = path.rsplit("/", 1)[1]
            if id_ in self.docs:
                return self.docs[id_]
            if brak_ok:
                return None
            raise SystemExit("BŁĄD HTTP 404")
        if path.startswith("/pozycje-slownika/"):
            return {"content": [{"id": 1, "wartosc": "Interpretacja indywidualna"},
                                {"id": 64697, "wartosc": "Informacje prasowe KIS"}], "totalElements": 2}
        if path.startswith("/wyszukiwarka/informacje"):
            f = body["filter"]
            if "ID_INFORMACJI" in f:
                m = self.meta.get(f["ID_INFORMACJI"])
                return _wynik([m] if m else [])
            if "INFORMACJA_ZMIENIANA" in f:
                return _wynik(self.zmieniajace.get(f["INFORMACJA_ZMIENIANA"], []))
            if "SYG" in f and f["SYG"] in self.sygnatury:
                return _wynik(self.sygnatury[f["SYG"]])
            if "SYG" in f:
                trafienia = [m for m in self.pula if f["SYG"] in m.get("SYG", "")]
                return _wynik(trafienia, total=self.total_syg)
            return self.lista if self.lista is not None else _wynik([])
        raise AssertionError(f"nieoczekiwane wywołanie {path}")


def run(argv, api):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(eureka, "_http", api), mock.patch.object(sys, "argv", ["eureka.py"] + argv), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            eureka.main()
            kod = 0
        except SystemExit as e:
            kod = e.code if isinstance(e.code, int) else 2
    return kod, out.getvalue(), err.getvalue()


def _api_trzy():
    return FakeAPI(docs={"682533": DOC_UCHYLONA, "669739": DOC_ZMIENIONA, "718641": DOC_CZYSTA, "500": DOC_POTWIERDZONA},
                   meta={"682533": META_UCHYLONA, "669739": META_ZMIENIONA, "718641": META_CZYSTA,
                         "500": META_POTWIERDZONA},
                   sygnatury={"0115-KDIT1.4011.700.2026.1.MR": [META_CZYSTA]})


class TestData(unittest.TestCase):
    def test_utc_zima_przesuwa_na_nastepny_dzien(self):
        self.assertEqual(eureka._data("2026-03-08T23:00:00.000Z"), "2026-03-09")

    def test_utc_lato_cest(self):
        self.assertEqual(eureka._data("2026-07-21T22:30:00Z"), "2026-07-22")
        self.assertEqual(eureka._data("2026-07-21T21:59:00Z"), "2026-07-21")

    def test_granica_zmiany_czasu(self):  # 2026-03-29 01:00 UTC = start CEST
        self.assertEqual(eureka._data("2026-03-28T23:30:00Z"), "2026-03-29")   # CET +1
        self.assertEqual(eureka._data("2026-10-25T00:30:00Z"), "2026-10-25")   # jeszcze CEST +2

    def test_data_dzienna_bez_zmian(self):
        self.assertEqual(eureka._data("2026-11-10"), "2026-11-10")
        self.assertEqual(eureka._data(None), "")


class TestHtml(unittest.TestCase):
    def test_przypis_worda(self):
        html = ('<p>Ordynacja podatkowa<a href="#_ftn1"><sup>[1]</sup></a><sup>)</sup>, w celu</p>'
                '<hr><p><a href="#_ftnref1"><sup><span>[1]</span></sup></a><sup>)</sup> Dz. U. z 2026 r. poz. 622</p>')
        t = eureka._html_na_tekst(html)
        self.assertIn("Ordynacja podatkowa[1], w celu", t)
        self.assertIn("[1] Dz. U. z 2026 r. poz. 622", t)
        self.assertNotIn("[[", t)
        self.assertNotIn("[1])", t)

    def test_zwykly_indeks_gorny_bez_nawiasow(self):
        self.assertIn("100 m2", eureka._html_na_tekst("<p>100 m<sup>2</sup></p>"))

    def test_akapity_listy_tabele_encje(self):
        t = eureka._html_na_tekst("<p>A&nbsp;&amp;&nbsp;B</p><ul><li>jeden</li><li>dwa</li></ul>"
                                  "<table><tr><td>k1</td><td>k2</td></tr></table><script>x()</script>")
        self.assertEqual(t.splitlines(), ["A & B", "- jeden", "- dwa", "k1 | k2"])


class TestKategorie(unittest.TestCase):
    def test_domyslne_interpretacje(self):
        self.assertEqual(eureka._kategorie_z_argumentu(None), [1, 2, 3, 4, 69341])

    def test_aliasy_zmian_wis_wia(self):
        self.assertEqual(eureka._kategorie_z_argumentu("wis-wszystkie"), [18, 19, 62915])
        self.assertEqual(eureka._kategorie_z_argumentu("wia-wszystkie"), [16, 17, 62916])
        self.assertEqual(eureka._kategorie_z_argumentu("zmiana-wis,uchylenie-wia"), [19, 62916])

    def test_aliasy_id_wszystko(self):
        self.assertEqual(eureka._kategorie_z_argumentu("ogolna, objasnienia,18"), [3, 11, 18])
        self.assertEqual(eureka._kategorie_z_argumentu("wszystko"), [])

    def test_nieznana_kategoria_kod_2(self):
        kod, _, err = run(["szukaj", "x", "--kategoria", "bzdura"], FakeAPI())
        self.assertEqual(kod, 2)
        self.assertIn("nieznana kategoria", err)

    def test_komenda_kategorie(self):
        kod, out, _ = run(["kategorie"], FakeAPI())
        self.assertEqual(kod, 0)
        self.assertIn("indywidualna", out)
        self.assertIn("Informacje prasowe KIS", out)


class TestSzukaj(unittest.TestCase):
    def test_zapytanie_i_filtry(self):
        api = FakeAPI(lista=_wynik([META_UCHYLONA, META_CZYSTA], total=2))
        kod, out, _ = run(["szukaj", "pośrednictwo", "--kategoria", "indywidualna", "--od", "2026-01-01",
                           "--do", "2026-12-31", "--aktualne", "--dokladnie"], api)
        self.assertEqual(kod, 0)
        path, body = api.wywolania[0]
        self.assertIn("sort=_score%2CDESC", path)  # fraza → trafność
        self.assertEqual(body["filter"], {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": "2026-01-01",
                                          "DT_WYD_end": "2026-12-31", "STATUS_INFORMACJI": [27]})
        self.assertTrue(body["searchInFullPhrase"])
        self.assertEqual(body["searchQuery"], "pośrednictwo")
        self.assertIn("!!! Interpretacja uchylona wyrokiem WSA", out)  # znacznik na liście
        self.assertIn("filtr „aktualne” to status EUREKA", out)

    def test_bez_frazy_sort_po_dacie(self):
        api = FakeAPI(lista=_wynik([META_CZYSTA]))
        run(["szukaj", "--od", "2026-11-01"], api)
        self.assertIn("sort=DT_WYD%2CDESC", api.wywolania[0][0])

    def test_zero_trafien_kod_1(self):
        kod, _, err = run(["szukaj", "qqzx"], FakeAPI(lista=_wynik([])))
        self.assertEqual(kod, 1)
        self.assertIn("brak w EUREKA ≠ brak interpretacji", err)
        self.assertIn("--kategoria wszystko", err)

    def test_pusta_dalsza_strona_kod_2(self):
        kod, _, err = run(["szukaj", "x", "--strona", "5"], FakeAPI(lista=_wynik([], total=12)))
        self.assertEqual(kod, 2)
        self.assertIn("NIE jest brak", err)

    def test_okno_10000_kod_2_bez_zapytania(self):
        api = FakeAPI(lista=_wynik([META_CZYSTA]))
        kod, _, err = run(["szukaj", "x", "--strona", "200", "--limit", "50"], api)
        self.assertEqual(kod, 2)
        self.assertIn("10000", err)
        self.assertEqual(api.wywolania, [])

    def test_bledne_argumenty_kod_2(self):
        for argv in (["szukaj"], ["szukaj", "x", "--od", "2026/01/01"], ["szukaj", "x", "--limit", "0"]):
            kod, _, _ = run(argv, FakeAPI())
            self.assertEqual(kod, 2, argv)

    def test_odpowiedz_z_errors_to_blad_nie_brak(self):
        api = FakeAPI(lista={"errors": [{"errorCode": "INTERNAL_SERVER_ERROR", "errorResult": "Nieoczekiwany błąd"}]})
        kod, _, err = run(["szukaj", "x"], api)
        self.assertEqual(kod, 2)
        self.assertIn("NIE jest brak", err)

    def test_json_bez_wynikow_nie_drukuje_pustego_jsona(self):
        kod, out, err = run(["szukaj", "qqzx", "--json"], FakeAPI(lista=_wynik([])))
        self.assertEqual(kod, 1)
        self.assertEqual(out, "")
        self.assertIn("Brak wyników", err)

    def test_json_po_komendzie(self):
        kod, out, _ = run(["szukaj", "x", "--json"], FakeAPI(lista=_wynik([META_CZYSTA])))
        self.assertEqual(kod, 0)
        self.assertEqual(json.loads(out)["results"][0]["ID_INFORMACJI"], "718641")


class TestSygnatura(unittest.TestCase):
    def test_dokladna_sygnatura(self):
        kod, out, _ = run(["sygnatura", "0115-KDIT1.4011.700.2026.1.MR"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("[718641]", out)

    def test_male_litery_proba_wielkich(self):
        kod, out, _ = run(["sygnatura", "0115-kdit1.4011.700.2026.1.mr"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("[718641]", out)

    def test_brak_sygnatury_kod_1(self):
        kod, _, _ = run(["sygnatura", "NIE.MA.123.2026"], _api_trzy())
        self.assertEqual(kod, 1)

    def test_same_cyfry_brak_kod_1(self):
        kod, _, err = run(["sygnatura", "9999/99"], _api_trzy())
        self.assertEqual(kod, 1)
        self.assertNotIn("Wielkość liter", err)

    def test_litery_bez_fragmentu_cyfr_to_nie_brak(self):
        """Sygnatura z literami, bez fragmentu ≥4 cyfr: nie ma jak zapytać niezależnie od wielkości liter,
        więc zero trafień NIE jest zweryfikowanym brakiem (kod 2, nie 1)."""
        for argv in (["sygnatura", "NIE.MA.1"], ["tresc", "NIE.MA.1"]):
            kod, out, err = run(argv, _api_trzy())
            self.assertEqual(kod, 2, argv)
            self.assertEqual(out, "")
            self.assertIn("NIE jest brak", err)
            self.assertIn("wielkość liter", err)

    def test_czesciowe_trafienia(self):
        api = _api_trzy()
        api.sygnatury["0115-KDIT1.4011.700"] = [META_CZYSTA]
        kod, out, _ = run(["sygnatura", "0115-KDIT1.4011.700"], api)
        self.assertEqual(kod, 0)
        self.assertIn("brak DOKŁADNEJ sygnatury", out)


META_JST = {"ID_INFORMACJI": "715247", "SYG": "Fn-II-2.310.1.2026.AB", "DT_WYD": "2026-10-01",
            "KATEGORIA_INFORMACJI": ["Interpretacja indywidualna JST"], "STATUS_INFORMACJI": ["Aktualna"]}
META_PODWOJNA = {"ID_INFORMACJI": "800", "SYG": "COP-13.310.6.2026.GWA; COP-13/31101/1360/GWA/26",
                 "KATEGORIA_INFORMACJI": ["Interpretacja indywidualna JST"], "STATUS_INFORMACJI": ["Aktualna"]}


class TestSygnaturaMieszaneLitery(unittest.TestCase):
    """Recenzja PR: filtr SYG rozróżnia wielkość liter, sygnatury JST mają litery mieszane."""

    def _api(self):
        api = _api_trzy()
        api.pula = [META_JST, META_PODWOJNA, META_CZYSTA]
        api.docs["715247"] = _doc(715247, {"ID_INFORMACJI": "715247", "SYG": "Fn-II-2.310.1.2026.AB",
                                           "STATUS_INFORMACJI": "27", "TRESC_INTERESARIUSZ": "<p>Podatek od nieruchomości</p>"})
        api.meta["715247"] = META_JST
        return api

    def test_wielkie_litery_znajduje_przez_fragment_bez_liter(self):
        api = self._api()
        kod, out, _ = run(["sygnatura", "FN-II-2.310.1.2026.AB"], api)
        self.assertEqual(kod, 0)
        self.assertIn("[715247] Fn-II-2.310.1.2026.AB", out)
        self.assertIn({"SYG": "2.310.1.2026"}, [b["filter"] for _, b in api.wywolania if b])

    def test_male_litery_i_tresc(self):
        kod, out, _ = run(["tresc", "fn-ii-2.310.1.2026.ab"], self._api())
        self.assertEqual(kod, 0)
        self.assertIn("ID: 715247", out)

    def test_brak_z_literami_mowi_o_wielkosci_liter(self):
        kod, _, err = run(["sygnatura", "Xy-9.9999.1.2026.QQ"], self._api())
        self.assertEqual(kod, 1)
        self.assertIn("Wielkość liter", err)

    def test_wiecej_trafien_niz_pobrano_to_nie_brak(self):
        api = self._api()
        api.pula = [META_CZYSTA] * 100   # 100 pobranych, żadne nie jest szukaną
        api.total_syg = 500
        kod, _, err = run(["sygnatura", "Qq-1.2026.1.2026.ZZ"], api)
        self.assertEqual(kod, 2)
        self.assertIn("NIE jest brak", err)

    def test_fragment_sprawdzany_mimo_trafien_czesciowych(self):
        """Zapis wielkimi literami trafia częściowo w INNY dokument — fragment bez liter i tak jest pytany."""
        api = self._api()
        inny = dict(META_CZYSTA, ID_INFORMACJI="900", SYG="FN-II-2.310.1.2026.AB.PO1")
        api.pula = [inny, META_JST]
        kod, out, _ = run(["sygnatura", "fn-ii-2.310.1.2026.ab"], api)
        self.assertEqual(kod, 0)
        self.assertIn("[715247] Fn-II-2.310.1.2026.AB", out)
        self.assertNotIn("[900]", out)
        self.assertNotIn("brak DOKŁADNEJ", out)

    def test_czesciowe_i_niepelny_fragment_mowi_o_niepewnosci(self):
        api = self._api()
        api.pula = [dict(META_CZYSTA, ID_INFORMACJI="900", SYG="Qq-1.2026.1.2026.ZZ.PO1")]
        api.total_syg = 500
        kod, out, _ = run(["sygnatura", "Qq-1.2026.1.2026.ZZ"], api)
        self.assertEqual(kod, 0)
        self.assertIn("brak DOKŁADNEJ sygnatury", out)
        self.assertIn("braku nie udało się potwierdzić", out)
        kod, _, err = run(["tresc", "Qq-1.2026.1.2026.ZZ"], api)
        self.assertEqual(kod, 2)

    def test_pole_z_dwiema_sygnaturami(self):
        kod, out, _ = run(["sygnatura", "COP-13/31101/1360/GWA/26"], self._api())
        self.assertEqual(kod, 0)
        self.assertIn("[800]", out)
        self.assertNotIn("Brak DOKŁADNEJ", out)


class TestJsonCzystyStdout(unittest.TestCase):
    def test_czesciowe_trafienia_json_uwaga_na_stderr(self):
        api = _api_trzy()
        api.sygnatury["0115-KDIT1.4011.700"] = [META_CZYSTA]
        kod, out, err = run(["sygnatura", "0115-KDIT1.4011.700", "--json"], api)
        self.assertEqual(kod, 0)
        json.loads(out)  # stdout = sam JSON
        self.assertIn("brak DOKŁADNEJ sygnatury", err)
        self.assertIn("INNE dokumenty", err)

    def test_aktualne_json_uwaga_na_stderr(self):
        kod, out, err = run(["szukaj", "x", "--aktualne", "--json"], FakeAPI(lista=_wynik([META_CZYSTA])))
        self.assertEqual(kod, 0)
        json.loads(out)
        self.assertIn("filtr „aktualne”", err)


class TestMetadaneBrakWiersza(unittest.TestCase):
    """Recenzja PR: brak wiersza metadanych nie może dać „brak wpisów o uchyleniu” ani przejść --strict."""

    def test_brak_wiersza_kod_2(self):
        api = _api_trzy()
        del api.meta["682533"]
        for argv in (["tresc", "682533"], ["tresc", "682533", "--strict"]):
            kod, out, err = run(argv, api)
            self.assertEqual(kod, 2, argv)
            self.assertEqual(out, "")
            self.assertIn("nie zwróciła metadanych", err)

    def test_wiersz_z_innym_id_kod_2(self):
        api = _api_trzy()
        api.meta["682533"] = dict(META_CZYSTA)  # ID 718641 zamiast 682533
        kod, _, err = run(["tresc", "682533"], api)
        self.assertEqual(kod, 2)

    def test_kontrola_po_id_z_dokumentu(self):
        api = _api_trzy()
        api.meta["682533"] = {k: v for k, v in META_UCHYLONA.items() if k not in ("KOMENTARZE_BIP", "KOM_BIP_OPIS")}
        kod, out, _ = run(["tresc", "682533"], api)
        self.assertEqual(kod, 0)
        self.assertTrue(out.startswith("!!! INFORMACJA PODWAŻONA"))
        kod, _, _ = run(["tresc", "682533", "--strict"], api)
        self.assertEqual(kod, 2)

    def test_strict_blokuje_brak_statusu_w_obu_zrodlach(self):
        api = _api_trzy()
        api.meta["718641"] = {k: v for k, v in META_CZYSTA.items() if k != "STATUS_INFORMACJI"}
        doc = copy.deepcopy(DOC_CZYSTA)
        doc["dokument"]["fields"] = [f for f in doc["dokument"]["fields"] if f["key"] != "STATUS_INFORMACJI"]
        api.docs["718641"] = doc
        kod, out, _ = run(["tresc", "718641"], api)
        self.assertEqual(kod, 0)
        self.assertIn("status EUREKA: ?", out)
        kod, out, err = run(["tresc", "718641", "--strict"], api)
        self.assertEqual(kod, 2)
        self.assertEqual(out, "")
        self.assertIn("nie podała statusu", err)

    def test_strict_status_tylko_w_dokumencie_przechodzi(self):
        api = _api_trzy()
        api.meta["718641"] = {k: v for k, v in META_CZYSTA.items() if k != "STATUS_INFORMACJI"}
        kod, _, _ = run(["tresc", "718641", "--strict"], api)
        self.assertEqual(kod, 0)

    def test_status_z_dokumentu_inny_niz_aktualna(self):
        api = _api_trzy()
        doc = copy.deepcopy(DOC_CZYSTA)  # nie zmieniaj wspólnego obiektu testowego
        doc["dokument"]["fields"] = [f if f["key"] != "STATUS_INFORMACJI" else dict(f, value="29")
                                     for f in doc["dokument"]["fields"]]
        api.docs["718641"] = doc
        kod, _, _ = run(["tresc", "718641", "--strict"], api)
        self.assertEqual(kod, 2)


class TestTresc(unittest.TestCase):
    def test_uchylona_mimo_statusu_aktualna(self):
        kod, out, _ = run(["tresc", "682533"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertTrue(out.startswith("!!! INFORMACJA PODWAŻONA"))
        self.assertIn("„Aktualna” nie wyklucza uchylenia", out)
        self.assertIn('cbosa.py sygnatura "I SA/Po 763/26"', out)
        self.assertIn("wydanie 2026-03-09", out)  # UTC → czas polski
        self.assertIn("Usługi pośrednictwa handlowego", out)

    def test_strict_blokuje_uchylona(self):
        kod, out, err = run(["tresc", "682533", "--strict"], _api_trzy())
        self.assertEqual(kod, 2)
        self.assertEqual(out, "")
        self.assertIn("BLOKADA --strict", err)
        self.assertIn("I SA/Po 763/26", err)

    def test_strict_blokuje_zmieniona(self):
        kod, _, err = run(["--strict", "tresc", "669739"], _api_trzy())
        self.assertEqual(kod, 2)
        self.assertIn("717939", err)

    def test_zmieniona_wskazuje_nastepczynie(self):
        kod, out, _ = run(["tresc", "669739"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("zmieniona informacją ID 717939 → tresc 717939", out)

    def test_zmiana_znaleziona_tylko_przez_odwrotne_powiazanie(self):
        api = _api_trzy()
        api.zmieniajace["718641"] = [{"ID_INFORMACJI": "999", "SYG": "DOP.1", "DT_WYD": "2026-12-01"}]
        kod, out, _ = run(["tresc", "718641"], api)
        self.assertIn("wskazana jako zmieniana przez: DOP.1", out)
        kod, _, _ = run(["tresc", "718641", "--strict"], api)
        self.assertEqual(kod, 2)

    def test_potwierdzona_wyrokiem_nie_blokuje(self):
        kod, out, _ = run(["tresc", "500", "--strict"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertNotIn("PODWAŻONA", out)

    def test_czysta_i_strict(self):
        kod, out, _ = run(["tresc", "718641", "--strict"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("brak wpisów o uchyleniu/zmianie", out)
        self.assertIn("[PIT] Ustawa", out)

    def test_po_sygnaturze(self):
        kod, out, _ = run(["tresc", "0115-KDIT1.4011.700.2026.1.MR"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("ID: 718641", out)

    def test_fragment(self):
        kod, out, _ = run(["tresc", "718641", "--fragment", "waluty wirt"], _api_trzy())
        self.assertEqual(kod, 0)
        self.assertIn("Fragmenty z „waluty wirt” (1)", out)
        kod, _, err = run(["tresc", "718641", "--fragment", "zzzz"], _api_trzy())
        self.assertEqual(kod, 1)
        self.assertIn("nie ma w treści", err)

    def test_nieznane_id_kod_2(self):
        kod, _, err = run(["tresc", "99999999"], _api_trzy())
        self.assertEqual(kod, 2)
        self.assertIn("nie znaleziono informacji o ID 99999999", err)

    def test_brak_tresci_strict(self):
        api = _api_trzy()
        api.docs["7"] = _doc(7, {"ID_INFORMACJI": "7", "SYG": "S.7"})
        api.meta["7"] = {"ID_INFORMACJI": "7", "STATUS_INFORMACJI": ["Aktualna"]}
        kod, out, _ = run(["tresc", "7"], api)
        self.assertEqual(kod, 0)
        self.assertIn("--pdf", out)
        kod, _, err = run(["tresc", "7", "--strict"], api)
        self.assertEqual(kod, 2)

    def test_pdf(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.pdf")
            kod, _, err = run(["tresc", "718641", "--pdf", p], _api_trzy())
            self.assertEqual(kod, 0)
            with open(p, "rb") as fh:
                self.assertTrue(fh.read().startswith(b"%PDF"))

    def test_zly_ksztalt_dokumentu_kod_2(self):
        api = _api_trzy()
        api.docs["8"] = {"id": 8}
        kod, _, err = run(["tresc", "8"], api)
        self.assertEqual(kod, 2)
        self.assertIn("nieoczekiwany kształt", err)

    def test_json(self):
        kod, out, _ = run(["tresc", "682533", "--json"], _api_trzy())
        d = json.loads(out)
        self.assertEqual(d["id"], "682533")
        self.assertIn("pośrednictwa", d["tekst"])


class TestSygnaturyWyrokow(unittest.TestCase):
    def test_wyciaganie(self):
        t = "uchylona wyrokiem WSA w Warszawie z 11.06.2026 r. sygn. akt III SA/Wa 690/26 oraz NSA II FSK 12/25"
        self.assertEqual(eureka._sygnatury_wyrokow(t), ["III SA/Wa 690/26", "II FSK 12/25"])


class TestPrzekierowania(unittest.TestCase):
    def test_obcy_host_odrzucony(self):
        h = eureka._TylkoHttps()
        req = mock.Mock()
        with self.assertRaises(Exception):
            h.redirect_request(req, None, 302, "", {}, "https://evil.example/x")


if __name__ == "__main__":
    unittest.main(verbosity=1)
