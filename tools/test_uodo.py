#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline unit tests for uodo.py (no network — `_get`/`_szukaj` are mocked). Run: python3 tools/test_uodo.py"""
import contextlib
import io
import json
import sys
import importlib.util
import pathlib
import unittest
import urllib.error
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "uodo", ROOT / "plugins/prawo-pl-uodo/skills/prawo-pl-uodo/scripts/uodo.py")
uodo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uodo)


# --- fixtures: shapes copied from the live API (2026-08-23) ---------------------------------
META_BISNODE = {  # ZSPR.421.3.2018: status final, but the fine (pkt 2) annulled by WSA; NSA dismissed cassation
    "refid": "urn:ndoc:gov:pl:uodo:2018:zspr_421_3", "refname": "ZSPR.421.3.2018", "kind": "decision",
    "name": {"pl": "Decyzja Prezesa UODO nr ZSPR.421.3.2018"}, "title": {"pl": "nakaz … oraz nałożenie kary."},
    "publication": {"status": "final", "inforce": True}, "parts": 1,
    "dates": [
        {"date": "2019-03-15", "use": "announcement", "status": "nonfinal"},
        {"date": "2019-03-26", "use": "publication", "status": "nonfinal"},
        {"date": "2019-12-11", "use": "repealed", "status": "nonfinal", "refid": "urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030"},
        {"date": "2023-09-19", "use": "defended", "status": "final", "refid": "urn:ndoc:court:pl:sa:2021:iii_osk_2538"},
    ]}
META_MORELE = {  # ZSPR.421.2.2019: annulled in full by NSA; API still says inforce=True
    "refid": "urn:ndoc:gov:pl:uodo:2019:zspr_421_2", "refname": "ZSPR.421.2.2019", "kind": "decision",
    "name": {"pl": "Decyzja Prezesa UODO nr ZSPR.421.2.2019"}, "title": {"pl": "nałożenie kary."},
    "publication": {"status": "repealed", "inforce": True}, "parts": 1,
    "dates": [
        {"date": "2019-09-10", "use": "announcement"}, {"date": "2019-09-19", "use": "publication"},
        {"date": "2020-09-03", "use": "defended", "refid": "urn:ndoc:court:pl:sa:2019:ii_sa-wa_2559"},
        {"date": "2023-02-09", "use": "repealed", "status": "repealed", "refid": "urn:ndoc:court:pl:sa:2021:iii_osk_3945"},
    ]}
META_KONTROLNA = {  # DKN.5131.33.2021: both courts dismissed the complaints — final stays final
    "refid": "urn:ndoc:gov:pl:uodo:2021:dkn_5131_33", "refname": "DKN.5131.33.2021", "kind": "decision",
    "name": {"pl": "Decyzja Prezesa UODO nr DKN.5131.33.2021"}, "title": {"pl": "kara."},
    "publication": {"status": "final", "inforce": True}, "parts": 1,
    "dates": [
        {"date": "2022-01-19", "use": "announcement"},
        {"date": "2022-11-15", "use": "defended", "refid": "urn:ndoc:court:pl:sa:2022:ii_sa-wa_546"},
        {"date": "2023-01-23", "use": "publication"},
        {"date": "2026-03-06", "use": "defended", "status": "final", "refid": "urn:ndoc:court:pl:sa:2023:iii_osk_377"},
    ]}
META_NONFINAL = {
    "refid": "urn:ndoc:gov:pl:uodo:2023:dkn_5131_34", "refname": "DKN.5131.34.2023", "kind": "decision",
    "name": {"pl": "Decyzja Prezesa UODO nr DKN.5131.34.2023"}, "title": {"pl": "kara."},
    "publication": {"status": "nonfinal", "inforce": True}, "parts": 1,
    "dates": [{"date": "2026-06-13", "use": "announcement"}, {"date": "2026-06-25", "use": "publication"}]}
META_WYROK = {  # court ruling record: parts=0, no body in the portal
    "refid": "urn:ndoc:court:pl:sa:2023:iii_osk_377", "refname": "III OSK 377/23", "kind": "Wyrok",
    "name": {"pl": "Wyrok - Naczelny Sąd Administracyjny"},
    "title": {"pl": "Naczelny Sąd Administracyjny w składzie: … oddala skargę kasacyjną"},
    "publication": {"status": "published", "inforce": True}, "parts": 0,
    "dates": [{"date": "2026-03-06", "use": "announcement", "text": {"pl": "Data orzeczenia"}},
              {"date": "2023-02-21", "use": "other", "text": {"pl": "Data wpływu"}}]}
META_SAD_WSA = {"refid": "urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030", "refname": "II SA/Wa 1030/19",
                "kind": "Wyrok", "name": {"pl": "Wyrok - Wojewódzki Sąd Administracyjny w Warszawie"}, "parts": 0}

HTML_DECYZJI = """<h1>
<span>Decyzja</span>
<span>DKN.1.1.2025</span>
</h1>
<dl class="first_level"><dd><h2 id="B1"><div class="num"></div><div> </div></h2>
<dl class="first_level"><dt class="first_level"></dt><dd id="n0">
<div>Na podstawie art. 104 (<a href=urn:ndoc:pro:pl:durp:2025:1691>Dz. U. z 2025 r. poz. 1691</a>), polegające na:</div><dl>
<dt>a)</dt><dd id="n0:la"><div>niewdrożeniu środków,</div></dd></dl>
<dl><dt>b)</dt><dd id="n0:lb"><div>braku testowania.</div></dd></dl>
</dd></dl></dd></dl>
<dl class="first_level"><dd><h2 id="B2"><div class="num">II.</div><div><span>Uzasadnienie</span></div></h2>
<dl class="first_level"><dt class="first_level">1.</dt><dd id="s1">
<div>Zgodnie z art. 34 u.o.d.o.<sup><a href="#g1">[1]</a></sup>, Prezes UODO jest organem &amp; basta.</div></dd></dl>
<dl class="first_level"><dt class="first_level">2.</dt><dd id="s2"><div>Administrator wyjaśnił, że</div><dl>
<dt>„</dt><dd id="s2:c1"><div><i>W celu zabezpieczenia podjęto środki:</i></div><dl>
<dt>1)</dt><dd id="s2:c1:p1"><div><i>(…),</i></div></dd></dl>
<dl><dt>2)</dt><dd id="s2:c1:p2"><div><i>(…)”</i></div></dd></dl></dd></dl></dd></dl>
</dd></dl>

<div class="glosses">
<div id="g1">[1] <span>Ustawa z dnia 10 maja 2018 r. o ochronie danych osobowych, dalej jako „u.o.d.o.”</span></div>
<div id="g2">[2] <span>Tamże, str. 10, pkt 24.</span></div>
</div>
"""


def _fake_get(odpowiedzi):
    """Zastępuje uodo._get: dopasowanie po fragmencie ścieżki; brak dopasowania = HTTP 404."""
    def f(path, params=None, raw=False, brak_ok=False):
        for klucz, wart in odpowiedzi.items():
            if klucz in path:
                if isinstance(wart, BaseException):
                    raise wart
                return wart
        if brak_ok:
            return None
        raise SystemExit(f"BŁĄD HTTP 404 (nie znaleziono): {path}")
    return f


def _uruchom(argv, odpowiedzi=None, szukaj=None):
    """main() z podmienioną siecią; zwraca (stdout, kod wyjścia albo None)."""
    out = io.StringIO()
    patches = [mock.patch.object(sys, "argv", ["uodo.py"] + argv), contextlib.redirect_stdout(out)]
    if odpowiedzi is not None:
        patches.append(mock.patch.object(uodo, "_get", side_effect=_fake_get(odpowiedzi)))
    if szukaj is not None:
        patches.append(mock.patch.object(uodo, "_szukaj", return_value=szukaj))
    kod = None
    with contextlib.ExitStack() as stos:
        for p in patches:
            stos.enter_context(p)
        try:
            uodo.main()
        except SystemExit as e:
            kod = e.code if isinstance(e.code, int) else 1
            if not isinstance(e.code, int):
                kod = (1, str(e.code))
    return out.getvalue(), kod


class TestRefid(unittest.TestCase):
    def test_sygnatura_dkn(self):
        self.assertEqual(uodo._refid("DKN.5131.9.2025"), "urn:ndoc:gov:pl:uodo:2025:dkn_5131_9")

    def test_sygnatura_dke(self):
        self.assertEqual(uodo._refid("DKE.561.1.2026"), "urn:ndoc:gov:pl:uodo:2026:dke_561_1")

    def test_diakrytyki_transliterowane(self):
        # URN-y portalu są w ASCII: ZSOŚS → zsoss (zweryfikowane na żywym API)
        self.assertEqual(uodo._refid("ZSOŚS.440.259.2019"), "urn:ndoc:gov:pl:uodo:2019:zsoss_440_259")

    def test_urn_przechodzi(self):
        urn = "urn:ndoc:gov:pl:uodo:2025:dkn_5131_9"
        self.assertEqual(uodo._refid(urn), urn)
        self.assertEqual(uodo._refid(urn.upper()), urn)

    def test_bez_roku_exits(self):
        with self.assertRaises(SystemExit):
            uodo._refid("DKN")

    def test_sygnatura_sadowa_to_urn_rekordu_powiazanego(self):
        # D6: 'decyzja "III OSK 377/23"' nie może kończyć się 'Nie umiem zbudować URN'
        self.assertEqual(uodo._refid("III OSK 377/23"), "urn:ndoc:court:pl:sa:2023:iii_osk_377")
        self.assertEqual(uodo._refid("II SA/Wa 1030/19"), "urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030")
        self.assertEqual(uodo._refid("III SA/Łd 147/24"), "urn:ndoc:court:pl:sa:2024:iii_sa-łd_147")
        self.assertEqual(uodo._refid("I C 475/19"), "urn:ndoc:court:pl:sp:2019:i_c_475")


class TestSygnaturaZUrn(unittest.TestCase):
    """Odtwarzanie sygnatury sądowej z refid (zweryfikowane na 171/172 parach refid→refname z indeksu;
    jedyna rozbieżność to literówka portalu 'II SA-Wa 609-20')."""

    def test_wsa(self):
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030"), "II SA/Wa 1030/19")

    def test_nsa(self):
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sa:2021:iii_osk_3945"), "III OSK 3945/21")

    def test_diakrytyk_i_sufiks_p(self):
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sa:2024:iii_sa-łd_147"), "III SA/Łd 147/24")
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sa:2023:i_ops_1_p_20230515"), "I OPS 1/23")
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sa:2021:iii_osk_6508_p"), "III OSK 6508/21")

    def test_sad_powszechny_i_tsue(self):
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sp:2018:vi_aca_397"), "VI ACa 397/18")
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:pl:sp:2021:iv_pa_10"), "IV Pa 10/21")
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:court:eu:tsue:2022:c_621"), "C-621/22")

    def test_nieznany_urn_bez_zmian(self):
        self.assertEqual(uodo._sygnatura_z_urn("urn:ndoc:pro:pl:durp:2023:1368"), "urn:ndoc:pro:pl:durp:2023:1368")
        self.assertEqual(uodo._sygnatura_z_urn(""), "")


class TestTimespan(unittest.TestCase):
    def test_pusty(self):
        self.assertEqual(uodo._timespan(None, None), ",")

    def test_od(self):
        self.assertEqual(uodo._timespan("2026-01-01", None), "2026-01-01,")

    def test_od_do(self):
        self.assertEqual(uodo._timespan("2026-01-01", "2026-06-30"), "2026-01-01,2026-06-30")


class TestPl(unittest.TestCase):
    def test_dict_pl(self):
        self.assertEqual(uodo._pl({"pl": "tytuł"}), "tytuł")

    def test_dict_bez_pl(self):
        self.assertEqual(uodo._pl({"en": "title"}), "title")

    def test_string(self):
        self.assertEqual(uodo._pl("zwykły"), "zwykły")

    def test_none(self):
        self.assertEqual(uodo._pl(None), "")


class TestDaty(unittest.TestCase):
    def test_announcement_publication(self):
        item = {"dates": [
            {"date": "2025-08-07", "use": "announcement"},
            {"date": "2025-08-27", "use": "publication"},
            {"date": "2025-09-11", "use": "validation"},
        ]}
        d = uodo._daty(item)
        self.assertEqual(d["announcement"], "2025-08-07")
        self.assertEqual(d["publication"], "2025-08-27")
        self.assertEqual(d["validation"], "2025-09-11")

    def test_brak_dat(self):
        self.assertEqual(uodo._daty({}), {})


class TestKontrolaSadowa(unittest.TestCase):
    """D1: wpisy dates[] use=repealed/defended/trial z refid wyroku nie mogą ginąć."""

    def test_bisnode_dwa_wpisy_z_sygnaturami(self):
        k = uodo._kontrola_sadowa(META_BISNODE)
        self.assertEqual([(x["use"], x["date"], x["sygnatura"]) for x in k],
                         [("repealed", "2019-12-11", "II SA/Wa 1030/19"),
                          ("defended", "2023-09-19", "III OSK 2538/21")])
        self.assertIn("UCHYLONA", k[0]["znaczenie"])
        self.assertIn("utrzymana", k[1]["znaczenie"])

    def test_uchylona_mimo_statusu_final(self):
        # częściowe uchylenie (sama kara) — portal zostawia status 'final'
        self.assertTrue(uodo._uchylona(META_BISNODE, uodo._kontrola_sadowa(META_BISNODE)))
        self.assertTrue(uodo._uchylona(META_MORELE, uodo._kontrola_sadowa(META_MORELE)))
        self.assertFalse(uodo._uchylona(META_KONTROLNA, uodo._kontrola_sadowa(META_KONTROLNA)))
        self.assertFalse(uodo._uchylona(META_NONFINAL, uodo._kontrola_sadowa(META_NONFINAL)))

    def test_rekord_sadowy_bez_kontroli(self):
        # 'other' = „Data wpływu" bez refid na rekordzie wyroku — to nie kontrola sądowa
        self.assertEqual(uodo._kontrola_sadowa(META_WYROK), [])

    def test_trial_w_toku(self):
        k = uodo._kontrola_sadowa({"dates": [{"date": "2025-04-10", "use": "trial",
                                              "refid": "urn:ndoc:court:pl:sa:2024:ii_sa-wa_1266"}]})
        self.assertEqual(k[0]["sygnatura"], "II SA/Wa 1266/24")
        self.assertIn("w toku", k[0]["znaczenie"])


class TestTekstZHtml(unittest.TestCase):
    """D3: tekst z body.html — numeracja list, odnośniki [n], jeden blok przypisów."""

    def setUp(self):
        self.txt = uodo._tekst_z_html(HTML_DECYZJI)
        self.linie = self.txt.split("\n")

    def test_naglowek_i_etykiety_list(self):
        self.assertEqual(self.linie[0], "Decyzja DKN.1.1.2025")
        self.assertIn("  a) niewdrożeniu środków,", self.linie)
        self.assertIn("  b) braku testowania.", self.linie)
        self.assertIn("1. Zgodnie z art. 34 u.o.d.o.[1], Prezes UODO jest organem & basta.", self.linie)
        self.assertIn("2. Administrator wyjaśnił, że", self.linie)
        self.assertIn("  „W celu zabezpieczenia podjęto środki:", self.linie)
        self.assertIn("    1) (…),", self.linie)
        self.assertIn("    2) (…)”", self.linie)

    def test_naglowek_sekcji_z_numerem_w_jednej_linii(self):
        self.assertIn("II. Uzasadnienie", self.linie)

    def test_przypisy_raz_z_numerami(self):
        self.assertEqual(self.txt.count("Ustawa z dnia 10 maja 2018 r."), 1)
        self.assertIn("[1] Ustawa z dnia 10 maja 2018 r. o ochronie danych osobowych, dalej jako „u.o.d.o.”", self.linie)
        self.assertIn("[2] Tamże, str. 10, pkt 24.", self.linie)
        self.assertIn("Przypisy:", self.linie)

    def test_bez_znacznikow_i_pusty(self):
        self.assertNotIn("<", self.txt)
        self.assertEqual(uodo._tekst_z_html(""), "")


class TestTresc(unittest.TestCase):
    def test_html_ma_pierwszenstwo(self):
        with mock.patch.object(uodo, "_get", side_effect=_fake_get({"body.html": HTML_DECYZJI, "body.txt": "txt"})):
            txt, zrodlo = uodo._tresc("urn:ndoc:gov:pl:uodo:2025:dkn_1_1")
        self.assertEqual(zrodlo, "body.html")
        self.assertIn("[1] Ustawa", txt)

    def test_awaryjnie_body_txt(self):
        with mock.patch.object(uodo, "_get", side_effect=_fake_get({"body.txt": "  tekst z txt  "})):
            self.assertEqual(uodo._tresc("urn:ndoc:gov:pl:uodo:2025:dkn_1_1"), ("tekst z txt", "body.txt"))


class TestWiersz(unittest.TestCase):
    def _wiersz(self, item):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            uodo._wiersz(item)
        return out.getvalue()

    def test_orzeczenie_sadu_bez_strzalki_decyzja(self):
        # D6: 200/700 rekordów to wyroki bez treści — nie reklamuj '→ decyzja'
        w = self._wiersz(META_WYROK)
        self.assertNotIn("→ decyzja", w)
        self.assertIn('prawo-pl-cbosa sygnatura "III OSK 377/23"', w)
        self.assertIn("status: published (rekord niebędący decyzją)", w)

    def test_decyzja_uchylona_ma_marker(self):
        w = self._wiersz(META_BISNODE)
        self.assertIn("→ decyzja ZSPR.421.3.2018", w)
        self.assertIn("kontrola sądowa: UCHYLONA (w całości lub w części) — II SA/Wa 1030/19 (2019-12-11)", w)
        w = self._wiersz(META_MORELE)
        self.assertIn("status: repealed (UCHYLONA)", w)

    def test_inne_rekordy_powiazane(self):
        self.assertIn("prawo-pl-eli", self._wiersz({"refid": "urn:ndoc:pro:pl:durp:2023:1368", "refname": "Dz.U. 2023 poz. 1368"}))
        self.assertIn("prawo-eu-eurlex", self._wiersz({"refid": "urn:ndoc:court:eu:tsue:2022:c_621", "refname": "62022CJ0621"}))


class TestFragmenty(unittest.TestCase):
    def test_okno_wokol_frazy(self):
        txt = ("X" * 50) + "KARA" + ("Y" * 50)
        spans = uodo._fragmenty(txt, "kara")
        self.assertEqual(len(spans), 1)
        self.assertIn("KARA", txt[spans[0][0]:spans[0][1]])

    def test_brak_trafien(self):
        self.assertEqual(uodo._fragmenty("dowolny tekst", "nie ma"), [])


class TestFlagaJson(unittest.TestCase):
    """--json musi działać także PO komendzie — modele piszą flagi właśnie tam."""

    ARGV = ["szukaj"]

    def _parsuj(self, argv):
        """Uruchamia main() z podmienionym cmd_szukaj — parsowanie bez wykonania (bez sieci)."""
        zlapane = {}
        oryg_argv, oryg_cmd = sys.argv, uodo.cmd_szukaj
        uodo.cmd_szukaj = lambda a: zlapane.update(vars(a))
        sys.argv = ["silnik.py"] + argv
        try:
            uodo.main()
        finally:
            sys.argv, uodo.cmd_szukaj = oryg_argv, oryg_cmd
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

    def test_pub_od_do(self):
        a = self._parsuj(self.ARGV + ["--pub-od", "2026-01-01", "--pub-do", "2026-03-31"])
        self.assertEqual((a["pub_od"], a["pub_do"]), ("2026-01-01", "2026-03-31"))


class TestDecyzja(unittest.TestCase):
    SIEC_BISNODE = {"zspr_421_3/meta.json": META_BISNODE, "ii_sa-wa_1030/meta.json": META_SAD_WSA,
                    "body.html": HTML_DECYZJI}

    def test_uchylona_naglowek_kontrola_i_inforce_z_zastrzezeniem(self):
        out, kod = _uruchom(["decyzja", "ZSPR.421.3.2018"], self.SIEC_BISNODE)
        self.assertIsNone(kod)
        self.assertTrue(out.startswith("!!! DECYZJA UCHYLONA PRZEZ SĄD (w całości lub w części)"))
        self.assertIn('prawo-pl-cbosa sygnatura "II SA/Wa 1030/19"', out)
        self.assertIn("## Kontrola sądowa", out)
        self.assertIn("2019-12-11  UCHYLONA (w całości lub w części)  Wyrok - Wojewódzki Sąd Administracyjny w Warszawie, II SA/Wa 1030/19", out)
        self.assertIn("2023-09-19  utrzymana (oddalono skargę)  III OSK 2538/21", out)  # bez meta sądu: sygnatura z URN
        self.assertIn("publication.inforce wg API: tak (pole nie odzwierciedla uchylenia)", out)
        self.assertNotIn("w obrocie: tak", out)
        self.assertIn("źródło: body.html", out)
        self.assertIn("[1] Ustawa z dnia 10 maja 2018 r.", out)

    def test_strict_blokuje_uchylona_takze_json(self):
        for argv in (["--strict", "decyzja", "ZSPR.421.3.2018"], ["decyzja", "ZSPR.421.3.2018", "--strict", "--json"]):
            out, kod = _uruchom(argv, self.SIEC_BISNODE)
            self.assertEqual(out, "")
            self.assertEqual(kod[0], 1)
            self.assertIn("UCHYLONA", kod[1])
            self.assertIn('prawo-pl-cbosa sygnatura "II SA/Wa 1030/19"', kod[1])
        out, kod = _uruchom(["decyzja", "ZSPR.421.2.2019", "--strict"],
                            {"zspr_421_2/meta.json": META_MORELE, "body.html": HTML_DECYZJI})
        self.assertEqual(out, "")
        self.assertIn("III OSK 3945/21 z 2023-02-09", kod[1])

    def test_strict_przepuszcza_nonfinal_i_kontrolna(self):
        out, kod = _uruchom(["decyzja", "DKN.5131.34.2023", "--strict"],
                            {"dkn_5131_34/meta.json": META_NONFINAL, "body.html": HTML_DECYZJI})
        self.assertIsNone(kod)
        self.assertIn("UWAGA:      decyzja NIEPRAWOMOCNA (status nonfinal)", out)
        self.assertNotIn("UCHYLONA", out)
        out, kod = _uruchom(["decyzja", "DKN.5131.33.2021", "--strict"],
                            {"dkn_5131_33/meta.json": META_KONTROLNA, "body.txt": "treść"})
        self.assertIsNone(kod)
        self.assertIn("utrzymana (oddalono skargę)  II SA/Wa 546/22", out)
        self.assertNotIn("!!!", out)
        self.assertIn("źródło: body.txt", out)

    def test_json_zawiera_kontrole(self):
        out, kod = _uruchom(["decyzja", "ZSPR.421.3.2018", "--json"], self.SIEC_BISNODE)
        self.assertIsNone(kod)
        d = json.loads(out)
        self.assertTrue(d["_uchylona"])
        self.assertEqual(d["_tresc_zrodlo"], "body.html")
        self.assertEqual([k["sygnatura"] for k in d["_kontrola_sadowa"]], ["II SA/Wa 1030/19", "III OSK 2538/21"])
        self.assertIn("[1] Ustawa", d["_body"])

    def test_rekord_sadowy_wyjasnia_zamiast_literowki(self):
        for ident in ("III OSK 377/23", "urn:ndoc:court:pl:sa:2023:iii_osk_377"):
            out, kod = _uruchom(["decyzja", ident], {"iii_osk_377/meta.json": META_WYROK})
            self.assertEqual(out, "")
            self.assertEqual(kod[0], 1)
            self.assertIn("To NIE jest decyzja Prezesa UODO", kod[1])
            self.assertIn('prawo-pl-cbosa sygnatura "III OSK 377/23"', kod[1])
            self.assertNotIn("Sprawdź sygnaturę", kod[1])

    def test_brak_w_portalu_z_zastrzezeniem(self):
        out, kod = _uruchom(["decyzja", "DKN.9999.99.2025"], {})
        self.assertEqual(out, "")
        self.assertIn("brak w portalu ≠ nieistnienie decyzji", kod[1])
        self.assertIn("https://uodo.gov.pl", kod[1])
        self.assertNotIn("uodo.gov.pl/decyzje", kod[1])
        out, kod = _uruchom(["decyzja", "II SA/Wa 9999/19"], {})
        self.assertIn('prawo-pl-cbosa sygnatura "II SA/Wa 9999/19"', kod[1])

    def test_t14_strict_blokuje_brak_pelnej_tresci_i_stdout_jest_pusty(self):
        out, kod = _uruchom(["decyzja", "DKN.5131.34.2023", "--strict"],
                            {"dkn_5131_34/meta.json": META_NONFINAL, "body.txt": ""})
        self.assertEqual(out, "")
        self.assertEqual(kod[0], 1)
        self.assertIn("bez zweryfikowanej pełnej treści", kod[1])

    def test_awaria_pobrania_tresci_nie_zostawia_czesciowego_stdout(self):
        for argv in (["decyzja", "DKN.5131.34.2023"], ["decyzja", "DKN.5131.34.2023", "--json", "--strict"]):
            out, kod = _uruchom(argv, {"dkn_5131_34/meta.json": META_NONFINAL, "body.txt": SystemExit("awaria body.txt")})
            self.assertEqual(out, "")
            self.assertIsNotNone(kod)


class TestListy(unittest.TestCase):
    WIERSZE = [
        {"refid": "urn:ndoc:gov:pl:uodo:2025:dkn_1_1", "refname": "DKN.1.1.2025", "kind": "decision",
         "publication": {"status": "final"}, "dates": [{"use": "announcement", "date": "2026-02-10"},
                                                       {"use": "publication", "date": "2026-04-22"}]},
        {"refid": "urn:ndoc:gov:pl:uodo:2024:dkn_2_2", "refname": "DKN.2.2.2024", "kind": "decision",
         "publication": {"status": "nonfinal"}, "dates": [{"use": "announcement", "date": "2025-12-29"},
                                                          {"use": "publication", "date": "2026-01-16"}]},
        META_WYROK,
    ]

    def test_najnowsze_naglowek_mowi_po_czym_sortuje(self):
        out, kod = _uruchom(["najnowsze", "--limit", "3"], szukaj=self.WIERSZE)
        self.assertIsNone(kod)
        self.assertIn("Ostatnio WYDANE", out)
        self.assertIn("po dacie decyzji/orzeczenia (announcement), NIE po dacie publikacji", out)
        self.assertNotIn("Ostatnio opublikowane", out)

    def test_szukaj_naglowek_filtr_po_dacie_decyzji(self):
        out, kod = _uruchom(["szukaj", "--od", "2026-01-01", "--do", "2026-03-31"], szukaj=self.WIERSZE)
        self.assertIsNone(kod)
        self.assertIn("filtr po dacie decyzji (announcement): 2026-01-01–2026-03-31", out)
        self.assertIn('prawo-pl-cbosa sygnatura "III OSK 377/23"', out)
        self.assertEqual(out.count("→ decyzja"), 2)

    def test_pub_do_filtruje_po_stronie_klienta(self):
        with mock.patch.object(uodo, "_szukaj", return_value=self.WIERSZE) as sz:
            out, kod = _uruchom(["szukaj", "--pub-od", "2026-01-01", "--pub-do", "2026-03-31", "--json"])
        self.assertIsNone(kod)
        self.assertEqual([r["refname"] for r in json.loads(out)], ["DKN.2.2.2024"])
        # jedyny wolny warunek → filtr 'od' po stronie API; okno announcement zawężone do pub-do
        self.assertEqual(sz.call_args.args[:2], (",2026-03-31", "date_publication:ge:2026-01-01"))

    def test_pub_filtr_z_fraza_jest_klient_side(self):
        with mock.patch.object(uodo, "_szukaj", return_value=self.WIERSZE) as sz:
            out, kod = _uruchom(["szukaj", "biometr", "--pub-od", "2026-04-01"])
        self.assertIsNone(kod)
        self.assertEqual(sz.call_args.args[1], "content_pl:regex:biometr")
        self.assertIn("po stronie klienta, tylko w obrębie 3 pobranych rekordów", out)
        self.assertIn("[DKN.1.1.2025]", out)
        self.assertNotIn("[DKN.2.2.2024]", out)

    def test_pub_filtr_zero_po_filtrze_konczy_bledem_takze_json(self):
        out, kod = _uruchom(["szukaj", "--pub-od", "2027-01-01", "--json"], szukaj=self.WIERSZE)
        self.assertEqual(out, "")
        self.assertEqual(kod[0], 1)
        self.assertIn("To NIE dowód", kod[1])

    def test_t15_json_strict_nie_emituje_wyniku_gdy_kontrola_nie_przeszla(self):
        for argv in (["najnowsze", "--json", "--strict"], ["szukaj", "biometr", "--json", "--strict"]):
            out, kod = _uruchom(argv, szukaj={"error": "maintenance"})
            self.assertEqual(out, "")
            self.assertIsNotNone(kod)


class TestT16PrzekierowaniaHttps(unittest.TestCase):
    def test_host_tresci_jest_podnoszony_a_obcy_host_odrzucany(self):
        req = uodo.urllib.request.Request("https://orzeczenia.uodo.gov.pl/api/documents/public")
        handler = uodo._PrzekierowaniaHttps()
        nowy = handler.redirect_request(
            req, None, 302, "Found", {}, "http://orzeczenia.uodo.gov.pl/api/body.txt")
        self.assertEqual(nowy.full_url, "https://orzeczenia.uodo.gov.pl/api/body.txt")
        with self.assertRaisesRegex(urllib.error.URLError, "niezaufany host"):
            handler.redirect_request(req, None, 302, "Found", {}, "http://example.test/body.txt")


class TestLimitIOffset(unittest.TestCase):
    def test_from_0_nie_jest_gubione_w_url(self):
        with mock.patch.object(uodo, "_get", return_value=[]) as g:
            uodo._szukaj("2026-01-01,2026-12-31", limit=10, strona=0)
        self.assertEqual(g.call_args.args[1]["from"], 0)

    def test_parametr_zero_trafia_do_query(self):
        with mock.patch.object(uodo._opener, "open") as op:
            op.return_value.__enter__.return_value.read.return_value = b"[]"
            uodo._get("/x", {"from": 0, "count": 5, "puste": "", "nic": None, "flaga": False})
        url = op.call_args.args[0].full_url
        self.assertIn("from=0", url)
        self.assertIn("count=5", url)
        self.assertNotIn("puste", url)
        self.assertNotIn("flaga", url)

    def test_limit_ponad_100_obciety_przed_offsetem(self):
        with mock.patch.object(uodo, "_get", return_value=[]) as g:
            uodo._szukaj("2026-01-01,2026-12-31", limit=200, strona=1)
        self.assertEqual(g.call_args.args[1]["from"], 100)
        self.assertEqual(g.call_args.args[1]["count"], 100)

    def test_cmd_szukaj_glosno_obcina_limit(self):
        err = io.StringIO()
        with mock.patch.object(uodo, "_szukaj", return_value=[]) as sz, \
                mock.patch.object(sys, "argv", ["uodo.py", "szukaj", "biometr", "--limit", "200", "--strona", "1"]), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                uodo.main()
        self.assertIn("obcięty do 100", err.getvalue())
        self.assertEqual(sz.call_args.args[2], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
