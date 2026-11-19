#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do publicznego API systemu EUREKA Ministerstwa Finansów (https://eureka.mf.gov.pl) —
baza interpretacji podatkowych i innych informacji podatkowych KIS/MF: interpretacje
indywidualne i ogólne (wraz ze zmianami), objaśnienia podatkowe, WIS, WIA, interpretacje JST,
pisma i wytyczne MF, odpowiedzi na interpelacje, orzeczenia sądów opisane w bazie.
Tylko biblioteka standardowa Pythona (urllib/json/re/html.parser) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (wyszukiwarka to POST bez skutków ubocznych), bez klucza.

API (`/api/public/v1`) jest tym samym, z którego korzysta strona eureka.mf.gov.pl — MF nie
publikuje jego dokumentacji, więc kształt odpowiedzi może się zmienić bez zapowiedzi. Silnik
sprawdza kształt każdej odpowiedzi i przy niezgodności kończy się kodem 2 (nigdy „brak wyników”).

STATUS w EUREKA ≠ interpretacja się ostała: interpretacja uchylona wyrokiem WSA bywa nadal
oznaczona jako „Aktualna” (np. 0112-KDIL2-2.4011.893.2025.2.AA, ID 682533, sprawdzone 2026-11-17).
O uchyleniu/zmianie/wygaśnięciu informuje pole KOMENTARZE_BIP (+ opis KOM_BIP_OPIS) oraz
powiązanie INFORMACJA_ZMIENIAJACA — silnik pokazuje je na początku treści, a --strict blokuje.

Komendy:
  kategorie                                     kategorie informacji (aliasy do --kategoria)
  szukaj ["<fraza>"] [--kategoria K[,K2]] [--dokladnie] [--tylko-tresc] [--sygnatura S]
         [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--aktualne] [--sort data|najstarsze|trafnosc]
         [--limit N] [--strona N]
  sygnatura <sygnatura>                         dokładne dopasowanie sygnatury (+ częściowe, gdy brak)
  tresc <ID|sygnatura> [--fragment "<fraza>"] [--pdf PLIK.pdf]
                                                metadane, stan (uchylona/zmieniona?) i pełna treść
Globalnie: --json  (surowy JSON zamiast podsumowania)
           --strict (tresc: blokuje informację uchyloną, zmienioną, wygaszoną, nieaktualną albo bez treści)
Kody wyjścia: 0 = jest wynik; 1 = ZWERYFIKOWANY brak wyników (zapytanie poprawne, zero trafień albo
frazy --fragment nie ma w treści); 2 = wszystko inne (sieć/HTTP, zła odpowiedź API, zły argument,
nieznane ID/sygnatura, blokada --strict, pusta dalsza strona, nieprzewidziany błąd).
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.6.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
HOST = "eureka.mf.gov.pl"
BASE = f"https://{HOST}/api/public/v1"
PODGLAD = f"https://{HOST}/informacje/podglad/"
MAX_LIMIT = 100
MAX_OKNO = 10000  # Elasticsearch EUREKA: page*size ≥ 10 000 → HTTP 500 (sprawdzone 2026-11-17)

# Kategorie informacji (słownik sid=1, odczyt 2026-11-17). Aliasy → ID pozycji słownika.
# Komenda `kategorie` czyta słownik na żywo; ta tabela służy tylko do aliasów.
KATEGORIE = {
    "indywidualna": (1, "Interpretacja indywidualna"),
    "zmiana": (2, "Zmiana interpretacji indywidualnej"),
    "ogolna": (3, "Interpretacja ogólna"),
    "zmiana-ogolnej": (4, "Zmiana interpretacji ogólnej"),
    "jst": (69341, "Interpretacja indywidualna JST"),
    "objasnienia": (11, "Objaśnienia podatkowe"),
    "wis": (18, "Wiążąca informacja stawkowa"),
    "zmiana-wis": (19, "Zmiana wiążącej informacji stawkowej"),
    "uchylenie-wis": (62915, "Uchylenie wiążącej informacji stawkowej"),
    "wia": (16, "Wiążąca informacja akcyzowa"),
    "zmiana-wia": (17, "Zmiana wiążącej informacji akcyzowej"),
    "uchylenie-wia": (62916, "Uchylenie wiążącej informacji akcyzowej"),
    "broszura": (5, "Broszura informacyjna"),
    "pismo": (9, "Pismo Ministerstwa Finansów"),
    "wytyczne": (8, "Wytyczne/wyjaśnienia Ministerstwa Finansów"),
    "interpelacja": (6, "Odpowiedź na interpelację/zapytanie poselskie"),
    "senatorowie": (7, "Odpowiedź dla senatorów"),
    "orzeczenia": (26, "Orzeczenia sądów"),
    "opinia-zabezpieczajaca": (14, "Informacja o wydaniu opinii zabezpieczającej"),
    "spor-o-wlasciwosc": (23, "Postanowienie rozstrzygające spór o właściwość"),
    "media": (10, "Informacja Ministerstwa Finansów przekazywana do mediów na zapytania dziennikarzy"),
}
GRUPY = {  # aliasy zbiorcze
    "interpretacje": ("indywidualna", "zmiana", "ogolna", "zmiana-ogolnej", "jst"),
    "wis-wszystkie": ("wis", "zmiana-wis", "uchylenie-wis"),
    "wia-wszystkie": ("wia", "zmiana-wia", "uchylenie-wia"),
    "wszystko": (),
}
DOMYSLNA_KATEGORIA = "interpretacje"
STATUS_AKTUALNA = 27  # słownik sid=3: 27 Aktualna, 29 Nieaktualna/Zmieniona/Wygaszona/Archiwalna, 30 Nieaktualna
SORT = {"data": "DT_WYD,DESC", "najstarsze": "DT_WYD,ASC", "trafnosc": "_score,DESC"}
KOLUMNY_LISTY = ["ID_INFORMACJI", "SYG", "DT_WYD", "TEZA", "KATEGORIA_INFORMACJI", "STATUS_INFORMACJI",
                 "KOMENTARZE_BIP", "AUTOR"]
KOLUMNY_META = KOLUMNY_LISTY + ["DATA_PUBLIKACJI", "KOM_BIP_OPIS", "PRZEPISY", "SLOWA_KLUCZOWE",
                                "ZAGADNIENIA", "INFORMACJA_ZMIENIAJACA", "INFORMACJA_ZMIENIANA"]
# Komentarze BIP, które NIE podważają informacji (reszta: uchylona / zmieniona / wygaszona / nieprawidłowa…)
KOMENTARZE_NEUTRALNE = re.compile(r"potwierdzona prawomocnym wyrokiem|nie wygasła", re.I)
KOMENTARZE_NEUTRALNE_ID = {"53", "62639"}  # słownik sid=10: te same dwie pozycje po ID
HEDGE_BRAK = ("UWAGA: brak w EUREKA ≠ brak interpretacji. Baza obejmuje interpretacje od 2007 r., ale nie "
              "każdy dokument ma komplet metadanych; sprawdź też wyszukiwarkę na https://eureka.mf.gov.pl.")
NIEOFICJALNE = ("API EUREKA nie ma publicznej dokumentacji MF — zmiana po stronie MF może wymagać "
                "aktualizacji skilla (zgłoś: https://github.com/jamarpl21/prawo-pl-eli/issues).")


class BrakWynikow(SystemExit):
    """ZWERYFIKOWANY brak wyników → kod wyjścia 1. Wszystko inne (sys.exit("tekst")) → kod 2."""


class _TylkoHttps(urllib.request.HTTPRedirectHandler):
    """Przekierowania tylko na HTTPS w obrębie eureka.mf.gov.pl."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        host = (p.hostname or "").lower().rstrip(".")
        if host != HOST and not host.endswith("." + HOST):
            raise urllib.error.URLError(f"odrzucono przekierowanie poza {HOST}: {newurl}")
        if p.scheme.lower() != "https":
            newurl = "https" + newurl[len(p.scheme):]
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_TylkoHttps())


def _http(path, body=None, binarne=False, brak_ok=False):
    """GET (body=None) albo POST JSON. Jedno ponowienie na 5xx / błąd sieci.
    binarne=True → bytes; brak_ok=True → 404 zwraca None."""
    url = BASE + path
    dane = None if body is None else json.dumps(body).encode("utf-8")
    naglowki = {"User-Agent": f"prawo-pl-eureka/{__version__} (+https://github.com/jamarpl21/prawo-pl-eli)",
                "Accept": "application/pdf" if binarne else "application/json"}
    if dane is not None:
        naglowki["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=dane, headers=naglowki, method="POST" if dane is not None else "GET")
    for proba in (1, 2):
        try:
            with _opener.open(req, timeout=120 if binarne else 45) as r:
                surowe = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and proba == 1:
                time.sleep(2); continue
            if e.code == 404:
                if brak_ok:
                    return None
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}\n{HEDGE_BRAK}")
            if e.code in (401, 403):
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\nAPI EUREKA zaczęło wymagać autoryzacji. {NIEOFICJALNE}")
            if e.code >= 500 and dane is not None:
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\nWyszukiwarka EUREKA odrzuciła zapytanie (to NIE jest brak "
                         f"wyników) — sprawdź argumenty, spróbuj ponownie za chwilę. {NIEOFICJALNE}")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:  # noqa: BLE001
            if proba == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e}) — to NIE jest brak wyników.")
    if binarne:
        return surowe
    try:
        return json.loads(surowe.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        sys.exit(f"BŁĄD: API EUREKA zwróciło nie-JSON dla {url} — spróbuj ponownie za chwilę. {NIEOFICJALNE}")


# --- wyszukiwarka ----------------------------------------------------------------------------

def _zapytanie(filtr, fraza="", dokladnie=False, tylko_tresc=False, kolumny=None):
    return {"filter": filtr, "columns": list(kolumny or KOLUMNY_LISTY), "searchInFullPhrase": bool(dokladnie),
            "searchInContent": bool(tylko_tresc), "searchInSynonyms": False,
            "searchQuery": fraza or "", "warunkiDodatkowe": []}


def _szukaj(body, limit=10, strona=0, sort="data"):
    q = urllib.parse.urlencode({"size": limit, "page": strona, "sort": SORT[sort]})
    d = _http(f"/wyszukiwarka/informacje?{q}", body=body)
    if not isinstance(d, dict) or not isinstance(d.get("results"), list) or "totalHits" not in d:
        if isinstance(d, dict) and d.get("errors"):
            sys.exit(f"BŁĄD: wyszukiwarka EUREKA zwróciła błąd: {_tekst_bledu(d)} — to NIE jest brak wyników.")
        sys.exit(f"BŁĄD: nieoczekiwana odpowiedź wyszukiwarki EUREKA. {NIEOFICJALNE}")
    return d


def _tekst_bledu(d):
    try:
        return "; ".join(str(e.get("errorResult") or e.get("errorCode")) for e in d["errors"])
    except Exception:  # noqa: BLE001
        return str(d)[:200]


def _lista(v):
    """Pole wyniku wyszukiwarki: lista etykiet, pojedynczy string albo brak → lista stringów."""
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    return [str(v)]


def _jedno(v):
    lst = _lista(v)
    return ", ".join(lst) if lst else ""


def _kategorie_z_argumentu(arg):
    """'indywidualna,ogolna' / 'interpretacje' / 'wszystko' / '1,3' → lista ID (pusta = bez filtra)."""
    ids = []
    for czesc in [c.strip().lower() for c in (arg or DOMYSLNA_KATEGORIA).split(",") if c.strip()]:
        if czesc in GRUPY:
            if not GRUPY[czesc]:
                return []
            ids += [KATEGORIE[a][0] for a in GRUPY[czesc]]
        elif czesc in KATEGORIE:
            ids.append(KATEGORIE[czesc][0])
        elif czesc.isdigit():
            ids.append(int(czesc))
        else:
            sys.exit(f"BŁĄD: nieznana kategoria „{czesc}”. Dostępne aliasy: "
                     f"{', '.join(list(GRUPY) + list(KATEGORIE))} (albo ID z komendy `kategorie`).")
    return sorted(set(ids))


def _ostatnia_niedziela(rok, miesiac):
    import datetime
    d = datetime.date(rok, miesiac, 31)
    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)


def _data(v):
    """Data dzienna w czasie POLSKIM. Dokument (/informacje/{id}) podaje znacznik UTC, np.
    '2026-03-08T23:00:00Z' = 9 marca w Polsce (wyszukiwarka pokazuje już '2026-03-09'); obcięcie do
    10 znaków przesuwało datę o dzień. Strefa: CET/CEST wg reguły UE (bez tzdata — Python 3.8)."""
    import datetime
    s = str(v or "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]00:?00)$", s)
    if m:
        utc = datetime.datetime(*(int(x or 0) for x in m.groups()[:6]))
        lato_od = datetime.datetime.combine(_ostatnia_niedziela(utc.year, 3), datetime.time(1))
        lato_do = datetime.datetime.combine(_ostatnia_niedziela(utc.year, 10), datetime.time(1))
        return (utc + datetime.timedelta(hours=2 if lato_od <= utc < lato_do else 1)).date().isoformat()
    return s[:10] if re.match(r"\d{4}-\d{2}-\d{2}", s) else s


def _sprawdz_date(s, nazwa):
    if s and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        sys.exit(f"BŁĄD: {nazwa} musi mieć format RRRR-MM-DD (podano „{s}”).")


def _podwazona(komentarze, status):
    """Czy informacja została podważona (uchylona/zmieniona/wygaszona/nieaktualna)?"""
    zle = [k for k in komentarze if not KOMENTARZE_NEUTRALNE.search(k)]
    nieaktualna = any(s and s.lower() != "aktualna" for s in status)
    return zle, nieaktualna


def _wiersz(r):
    kom, nieakt = _podwazona(_lista(r.get("KOMENTARZE_BIP")), _lista(r.get("STATUS_INFORMACJI")))
    znacznik = "  !!! " + "; ".join(kom) if kom else ""
    if nieakt and not kom:
        znacznik = f"  !!! status: {_jedno(r.get('STATUS_INFORMACJI'))}"
    teza = re.sub(r"\s+", " ", str(r.get("TEZA") or "")).strip()
    if len(teza) > 300:
        teza = teza[:297].rstrip() + "…"
    return (f"- [{r.get('ID_INFORMACJI')}] {r.get('SYG') or '(bez sygnatury)'} · {_data(r.get('DT_WYD'))} · "
            f"{_jedno(r.get('KATEGORIA_INFORMACJI'))}{znacznik}\n    {teza}")


def _drukuj_liste(d, a, opis):
    wyniki, total = d["results"], d.get("totalHits") or 0
    if not wyniki:  # bez pustego JSON-a: pusty wynik wyglądałby jak „sprawdzone, nic nie ma”
        if a.strona and total:
            sys.exit(f"BŁĄD: strona {a.strona} jest pusta (łącznie {total} wyników przy --limit {a.limit}) — "
                     "to NIE jest brak wyników.")
        podpowiedz = ""
        if getattr(a, "kategoria", None) != "wszystko" and getattr(a, "cmd", "") == "szukaj":
            podpowiedz = ("\nFiltr kategorii był włączony (domyślnie tylko interpretacje — bez objaśnień "
                          "podatkowych, WIS/WIA i pism MF); spróbuj --kategoria wszystko.")
        raise BrakWynikow(f"Brak wyników w EUREKA: {opis}.{podpowiedz}\n{HEDGE_BRAK}")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    od = a.strona * a.limit
    print(f"EUREKA — {opis}: {total} wyników (pozycje {od + 1}–{od + len(wyniki)}).")
    for r in wyniki:
        print(_wiersz(r))
    if od + len(wyniki) < total:
        print(f"\n(dalej: --strona {a.strona + 1})")
    print("\nPełna treść: tresc <ID>. „!!!” = informacja uchylona/zmieniona/wygaszona albo nieaktualna — "
          "przed powołaniem się na nią przeczytaj blok „Stan” w `tresc`.")


def cmd_kategorie(a):
    q = urllib.parse.urlencode({"sid": 1, "isFirstLevel": "true", "size": 100, "page": 0, "sort": "kolejnosc,asc"})
    d = _http(f"/pozycje-slownika/wyszukiwarka?{q}")
    if not isinstance(d, dict) or not isinstance(d.get("content"), list):
        sys.exit(f"BŁĄD: nieoczekiwana odpowiedź słownika kategorii. {NIEOFICJALNE}")
    if a.json:
        print(json.dumps(d["content"], ensure_ascii=False, indent=2)); return
    alias = {v[0]: k for k, v in KATEGORIE.items()}
    print("Kategorie informacji EUREKA (ID · alias · nazwa):")
    for x in d["content"]:
        print(f"- {x.get('id'):>6} · {alias.get(x.get('id'), '-'):<22} · {x.get('wartosc')}")
    zbiorcze = "; ".join(f"{k} = {', '.join(v)}" for k, v in GRUPY.items() if v)
    print(f"\nAliasy zbiorcze: {zbiorcze}; wszystko = bez filtra.")


def cmd_szukaj(a):
    if not 1 <= a.limit <= MAX_LIMIT:
        sys.exit(f"BŁĄD: --limit musi być w zakresie 1–{MAX_LIMIT}.")
    if a.strona < 0:
        sys.exit("BŁĄD: --strona musi być ≥ 0.")
    if (a.strona + 1) * a.limit > MAX_OKNO:
        sys.exit(f"BŁĄD: wyszukiwarka EUREKA zwraca najwyżej {MAX_OKNO} pierwszych pozycji (strona {a.strona} × "
                 f"--limit {a.limit} wychodzi poza to okno; API odpowiada wtedy HTTP 500). Zawęź zapytanie "
                 "zakresem dat (--od/--do), kategorią albo dokładniejszą frazą.")
    _sprawdz_date(a.od, "--od"); _sprawdz_date(a.do, "--do")
    filtr = {}
    kat = _kategorie_z_argumentu(a.kategoria)
    if kat:
        filtr["KATEGORIA_INFORMACJI"] = kat
    if a.sygnatura:
        filtr["SYG"] = a.sygnatura.strip()
    if a.od:
        filtr["DT_WYD_start"] = a.od
    if a.do:
        filtr["DT_WYD_end"] = a.do
    if a.aktualne:
        filtr["STATUS_INFORMACJI"] = [STATUS_AKTUALNA]
    if not (a.fraza or filtr.get("SYG") or a.od or a.do):
        sys.exit("BŁĄD: podaj frazę, --sygnatura albo zakres dat (--od/--do).")
    sort = a.sort or ("trafnosc" if a.fraza else "data")
    d = _szukaj(_zapytanie(filtr, a.fraza, a.dokladnie, a.tylko_tresc), a.limit, a.strona, sort)
    opis = ", ".join(x for x in (
        f"„{a.fraza}”" + (" (dokładna fraza)" if a.dokladnie else "") if a.fraza else "",
        f"kategoria: {a.kategoria or DOMYSLNA_KATEGORIA}",
        f"sygnatura zawiera „{a.sygnatura}”" if a.sygnatura else "",
        f"wydane {a.od or '…'}–{a.do or '…'}" if (a.od or a.do) else "",
        "tylko aktualne" if a.aktualne else "", f"sort: {sort}") if x)
    _drukuj_liste(d, a, opis)
    if a.aktualne:
        _uwaga(a, "UWAGA: filtr „aktualne” to status EUREKA — interpretacja uchylona przez WSA bywa nadal "
                  "„Aktualna”; patrz znaczniki „!!!”.")


LIMIT_SYGNATURY = 100


def _sygnatury_rekordu(r):
    """Pole SYG bywa listą kilku sygnatur rozdzielonych średnikiem
    („COP-13.310.6.2026.GWA; COP-13/31101/1360/GWA/26”) — porównujemy każdą osobno."""
    return [x.strip().lower() for x in re.split(r"[;\n]", str(r.get("SYG") or "")) if x.strip()]


def _fragment_bez_liter(syg):
    """Najdłuższy fragment sygnatury bez liter (cyfry i separatory), np. Fn-II-2.310.1.2026.AB → 2.310.1.2026.
    Filtr SYG rozróżnia wielkość liter, a sygnatury JST mają litery mieszane (Fn-II, WPiOL-VI, PiO)."""
    kandydaci = [c.strip(".-/ ") for c in re.findall(r"[0-9./\- ]+", syg)]
    kandydaci = [c for c in kandydaci if sum(ch.isdigit() for ch in c) >= 4]
    return max(kandydaci, key=len) if kandydaci else None


def _po_sygnaturze(syg):
    """Dokładne trafienia sygnatury → (dokladne, czesciowe, kompletne).

    EUREKA dopasowuje FRAGMENT i rozróżnia wielkość liter: `Fn-II-2.310.1.2026.AB` → 1, `FN-II-…` → 0.
    Kolejność: sygnatura jak podana, wielkimi literami, a przy zerze — fragment bez liter (wynik
    porównywany po stronie silnika bez rozróżniania wielkości liter). `kompletne=False`, gdy któreś
    zapytanie miało więcej trafień, niż pobrano — wtedy brak dokładnego trafienia NIE jest zweryfikowany."""
    syg = syg.strip()
    szukana = syg.lower()
    czesciowe, kompletne = None, True
    warianty = list(dict.fromkeys((syg, syg.upper())))
    fragment = _fragment_bez_liter(syg) if re.search(r"[^\W\d_]", syg) else None
    if fragment and fragment not in warianty:
        warianty.append(fragment)
    for i, wariant in enumerate(warianty):
        if i == len(warianty) - 1 and wariant == fragment and czesciowe:
            break  # są już trafienia częściowe dla samej sygnatury — fragment nic nie doda
        d = _szukaj(_zapytanie({"SYG": wariant}), limit=LIMIT_SYGNATURY, sort="data")
        dokladne = [r for r in d["results"] if szukana in _sygnatury_rekordu(r)]
        if dokladne:
            return dokladne, None, True
        if (d.get("totalHits") or 0) > len(d["results"]):
            kompletne = False
        if d["results"] and wariant != fragment and czesciowe is None:
            czesciowe = d
    return [], czesciowe, kompletne


def _uwaga(a, tekst):
    """Uwaga dla czytelnika: na stdout w trybie tekstowym, na stderr z --json (stdout = czysty JSON)."""
    print(tekst, file=sys.stderr if a.json else sys.stdout)


def _brak_sygnatury(syg, kompletne):
    if not kompletne:
        sys.exit(f"BŁĄD: nie udało się potwierdzić sygnatury „{syg}” — zapytanie ma więcej niż {LIMIT_SYGNATURY} "
                 "trafień, a dokładnej sygnatury nie było wśród pobranych. To NIE jest brak wyników; "
                 "podaj pełną sygnaturę albo szukaj: szukaj --sygnatura … --od/--do.")
    litery = (" Wielkość liter w sygnaturze ma znaczenie dla wyszukiwarki EUREKA; silnik sprawdził też "
              "fragment bez liter." if re.search(r"[^\W\d_]", syg) else "")
    return f"Brak sygnatury „{syg}” w EUREKA.{litery}\n{HEDGE_BRAK}"


def cmd_sygnatura(a):
    a.limit, a.strona = LIMIT_SYGNATURY, 0
    dokladne, czesciowe, kompletne = _po_sygnaturze(a.sygnatura)
    if dokladne:
        _drukuj_liste({"results": dokladne, "totalHits": len(dokladne)}, a, f"sygnatura {a.sygnatura}")
        return
    if czesciowe:
        _uwaga(a, f"UWAGA: brak DOKŁADNEJ sygnatury „{a.sygnatura}” — niżej trafienia częściowe (INNE dokumenty)"
                  + ("" if kompletne else f"; pokazano pierwsze {LIMIT_SYGNATURY}") + ".")
        _drukuj_liste(czesciowe, a, f"sygnatura zawiera „{a.sygnatura}”")
        return
    raise BrakWynikow(_brak_sygnatury(a.sygnatura, kompletne))


# --- treść -----------------------------------------------------------------------------------

class _TekstZHtml(HTMLParser):
    """TRESC_INTERESARIUSZ (HTML z edytora Word) → tekst: akapity, listy, tabele, przypisy [n]."""
    BLOKI = {"p", "div", "br", "li", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "hr",
             "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.linie, self._buf, self._pomin = [], [], 0
        self._sup = None          # bufor tekstu bieżącego <sup> (None = poza <sup>)
        self._a_ftn = 0           # głębokość <a href="#_ftn…"> (odsyłacz przypisu Worda)
        self._po_przypisie = False

    def _flush(self):
        t = re.sub(r"[ \t\r\f\v\xa0]+", " ", "".join(self._buf)).strip()
        self._buf = []
        if t:
            self.linie.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._pomin += 1
        elif tag in self.BLOKI:
            self._flush()
            if tag == "li":
                self._buf.append("- ")
        elif tag in ("td", "th"):
            if self._buf and "".join(self._buf).strip():
                self._buf.append(" | ")
        elif tag == "sup":
            self._sup = []
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            if re.match(r"#_?ftn", href):
                self._a_ftn += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._pomin = max(0, self._pomin - 1)
        elif tag == "sup" and self._sup is not None:
            t, self._sup = "".join(self._sup).strip(), None
            rdzen = t.strip("[]() ")
            if rdzen.isdigit() and ("[" in t or self._a_ftn):
                self._buf.append(f"[{rdzen}]")       # odsyłacz przypisu: <sup>[1]</sup> / <a #_ftn1><sup>1</sup>
                self._po_przypisie = True
                return
            if t == ")" and self._po_przypisie:      # Word: <sup>[1]</sup><sup>)</sup> → „[1]”
                return
            self._buf.append(t)                      # zwykły indeks górny (m2, 1a) — bez nawiasów
        elif tag == "a" and self._a_ftn:
            self._a_ftn -= 1
        elif tag in self.BLOKI:
            self._flush()

    def handle_data(self, data):
        if self._pomin:
            return
        if self._sup is not None:
            self._sup.append(data)
            return
        if data.strip():
            self._po_przypisie = False
        self._buf.append(data.replace("\n", " "))

    def tekst(self):
        self._flush()
        t = "\n".join(self.linie)
        return re.sub(r"\n{3,}", "\n\n", t).strip()


def _html_na_tekst(html):
    p = _TekstZHtml()
    p.feed(html or "")
    p.close()
    return p.tekst()


def _pola(doc):
    try:
        return {f["key"]: f.get("value") for f in doc["dokument"]["fields"]}
    except (KeyError, TypeError):
        sys.exit(f"BŁĄD: nieoczekiwany kształt dokumentu EUREKA (brak dokument.fields). {NIEOFICJALNE}")


def _rozwiaz_id(arg):
    """ID (liczba) albo sygnatura → ID. Kilka dokumentów o tej samej sygnaturze → lista i kod 2."""
    arg = arg.strip()
    if arg.isdigit():
        return arg
    dokladne, czesciowe, kompletne = _po_sygnaturze(arg)
    if len(dokladne) == 1:
        return str(dokladne[0].get("ID_INFORMACJI"))
    if len(dokladne) > 1:
        sys.exit(f"BŁĄD: sygnatura „{arg}” wskazuje {len(dokladne)} dokumenty — podaj ID:\n"
                 + "\n".join(_wiersz(r) for r in dokladne))
    if czesciowe:
        sys.exit(f"BŁĄD: brak DOKŁADNEJ sygnatury „{arg}” — trafienia częściowe (podaj ID):\n"
                 + "\n".join(_wiersz(r) for r in czesciowe["results"][:10]))
    sys.exit("BŁĄD: " + _brak_sygnatury(arg, kompletne))


def _metadane(id_):
    """Etykiety słownikowe (autor, status, komentarze, przepisy…) — wyszukiwarka zwraca je jako tekst."""
    d = _szukaj(_zapytanie({"ID_INFORMACJI": str(id_)}, kolumny=KOLUMNY_META), limit=1)
    wiersz = d["results"][0] if d["results"] else None
    if not isinstance(wiersz, dict) or str(wiersz.get("ID_INFORMACJI") or "").strip() != str(id_):
        # Bez tego wiersza nie ma etykiet statusu ani komentarzy BIP — wynik bez stanu prawnego byłby
        # mylący („brak wpisów o uchyleniu” przy uchylonej interpretacji), więc to błąd, nie wynik.
        sys.exit(f"BŁĄD: wyszukiwarka EUREKA nie zwróciła metadanych informacji {id_} (status, komentarze BIP) — "
                 "nie da się ustalić stanu prawnego. Spróbuj ponownie za chwilę; to NIE jest brak informacji. "
                 + NIEOFICJALNE)
    return wiersz


def _zmieniajace(id_):
    """Dokumenty, które wskazują ten dokument jako zmieniany (INFORMACJA_ZMIENIANA = id)."""
    d = _szukaj(_zapytanie({"INFORMACJA_ZMIENIANA": str(id_)}), limit=10)
    return d["results"]


def _sygnatury_wyrokow(tekst):
    return list(dict.fromkeys(re.findall(r"\b([IVX]{1,4} (?:SA|FSK|GSK|OSK|SAB|FZ)(?:/[A-ZŁŚŻ][a-ząćęłńóśźż]{1,2})? "
                                          r"\d{1,5}/\d{2})\b", tekst or "")))


def _fragmenty(txt, fraza, maks=6, okno=700):
    low, f = txt.lower(), (fraza or "").lower().strip()
    if not f:
        return []
    spans, p = [], low.find(f)
    while p != -1 and len(spans) < maks:
        s, e = max(0, p - okno // 3), min(len(txt), p + len(f) + okno)
        if spans and s <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], e))
        else:
            spans.append((s, e))
        p = low.find(f, p + len(f))
    wynik = []
    for s, e in spans:  # brzegi na granicach wyrazów
        while s > 0 and not txt[s - 1].isspace():
            s -= 1
        while e < len(txt) and not txt[e].isspace():
            e += 1
        wynik.append((s, e))
    return wynik


def cmd_tresc(a):
    id_ = _rozwiaz_id(a.id)
    doc = _http(f"/informacje/{id_}", brak_ok=True)
    if doc is None:
        sys.exit(f"BŁĄD: nie znaleziono informacji o ID {id_} (albo jest niedostępna publicznie).\n{HEDGE_BRAK}")
    if not isinstance(doc, dict):
        sys.exit(f"BŁĄD: nieoczekiwana odpowiedź EUREKA dla ID {id_}. {NIEOFICJALNE}")
    pola = _pola(doc)
    meta = _metadane(id_)
    zmieniajace = _zmieniajace(id_)
    tekst = _html_na_tekst(pola.get("TRESC_INTERESARIUSZ"))
    komentarze = _lista(meta.get("KOMENTARZE_BIP"))
    status = _lista(meta.get("STATUS_INFORMACJI"))
    opis_bip = re.sub(r"\s+", " ", _html_na_tekst(str(meta.get("KOM_BIP_OPIS") or pola.get("KOM_BIP_OPIS") or ""))).strip()
    zle, nieaktualna = _podwazona(komentarze, status)
    # Kontrola krzyżowa po ID z samego dokumentu (gdyby etykiety z wyszukiwarki się rozjechały):
    # komentarz spoza neutralnych (53 potwierdzona wyrokiem, 62639 WIS nie wygasła) albo status ≠ Aktualna.
    kom_id = {str(x) for x in (pola.get("KOMENTARZE_BIP") if isinstance(pola.get("KOMENTARZE_BIP"), list)
                               else [pola.get("KOMENTARZE_BIP")]) if x not in (None, "")}
    if kom_id - KOMENTARZE_NEUTRALNE_ID and not zle:
        zle = [f"komentarz BIP (ID {', '.join(sorted(kom_id - KOMENTARZE_NEUTRALNE_ID))}) bez etykiety w wyszukiwarce"]
    if str(pola.get("STATUS_INFORMACJI") or "").strip() not in ("", str(STATUS_AKTUALNA)):
        nieaktualna = True
    zmieniajaca = str(meta.get("INFORMACJA_ZMIENIAJACA") or pola.get("INFORMACJA_ZMIENIAJACA") or "").strip()
    zmieniajace = [r for r in zmieniajace if str(r.get("ID_INFORMACJI")) != str(id_)]
    wyroki = _sygnatury_wyrokow(opis_bip)
    podwazona = bool(zle or nieaktualna or zmieniajaca or zmieniajace)

    if a.strict:
        if not tekst:
            sys.exit(f"BLOKADA --strict: informacja {id_} nie ma treści w EUREKA (sprawdź PDF: --pdf).")
        if podwazona:
            powod = "; ".join(x for x in (", ".join(zle), opis_bip,
                                          f"status: {', '.join(status)}" if nieaktualna else "",
                                          f"zmieniona informacją ID {zmieniajaca}" if zmieniajaca else "",
                                          "wskazana jako zmieniana przez: " + ", ".join(
                                              f"{r.get('SYG')} (ID {r.get('ID_INFORMACJI')})" for r in zmieniajace)
                                          if zmieniajace else "") if x)
            sys.exit(f"BLOKADA --strict: informacja {id_} ({pola.get('SYG') or meta.get('SYG')}) została podważona: "
                     f"{powod}." + (f"\nWyrok: cbosa.py sygnatura \"{wyroki[0]}\"" if wyroki else ""))

    if a.pdf:
        pdf = _http(f"/informacje/{id_}/eksport/PDF", binarne=True)
        if not pdf.startswith(b"%PDF"):
            sys.exit(f"BŁĄD: eksport PDF dla ID {id_} nie jest plikiem PDF. {NIEOFICJALNE}")
        with open(a.pdf, "wb") as fh:
            fh.write(pdf)
        print(f"Zapisano urzędowy eksport PDF: {a.pdf} ({len(pdf)} B)", file=sys.stderr)

    if a.json:
        print(json.dumps({"id": id_, "dokument": doc, "metadane": meta, "zmieniajace": zmieniajace,
                          "tekst": tekst}, ensure_ascii=False, indent=2))
        return

    out = []
    if podwazona:
        out.append("!!! INFORMACJA PODWAŻONA — nie powołuj się na nią bez sprawdzenia, co ją zastąpiło !!!")
    out += [f"{_jedno(meta.get('KATEGORIA_INFORMACJI')) or doc.get('nazwa') or 'Informacja'} "
            f"{pola.get('SYG') or meta.get('SYG') or ''}".strip(),
            f"ID: {id_} · {PODGLAD}{id_}",
            f"Daty: wydanie {_data(pola.get('DT_WYD'))}" + (f" · publikacja {_data(pola.get('DATA_PUBLIKACJI'))}"
                                                           if pola.get("DATA_PUBLIKACJI") else ""),
            f"Organ: {_jedno(meta.get('AUTOR')) or '?'}"]
    teza = re.sub(r"\s+", " ", str(pola.get("TEZA") or "")).strip()
    if teza:
        out.append(f"Teza: {teza}")
    out.append("")
    out.append("Stan:")
    out.append(f"  status EUREKA: {', '.join(status) or '?'}"
               + ("  (UWAGA: „Aktualna” nie wyklucza uchylenia — patrz komentarze)" if zle and not nieaktualna else ""))
    for k in komentarze:
        out.append(f"  komentarz: {k}")
    if opis_bip:
        out.append(f"  opis: {opis_bip}")
    if zmieniajaca:
        out.append(f"  zmieniona informacją ID {zmieniajaca} → tresc {zmieniajaca}")
    for r in zmieniajace:
        if str(r.get("ID_INFORMACJI")) != zmieniajaca:
            out.append(f"  wskazana jako zmieniana przez: {r.get('SYG')} z {_data(r.get('DT_WYD'))} "
                       f"(ID {r.get('ID_INFORMACJI')}) → tresc {r.get('ID_INFORMACJI')}")
    for w in wyroki:
        out.append(f"  wyrok: cbosa.py sygnatura \"{w}\"  (skill prawo-pl-cbosa — sentencja i zakres)")
    if not (komentarze or zmieniajaca or zmieniajace):
        out.append("  brak wpisów o uchyleniu/zmianie w EUREKA (to nie gwarancja — MF dopisuje je z opóźnieniem; "
                   "przy ważnej sprawie sprawdź CBOSA)")
    zmieniana = str(meta.get("INFORMACJA_ZMIENIANA") or pola.get("INFORMACJA_ZMIENIANA") or "").strip()
    if zmieniana:
        out.append(f"  ten dokument ZMIENIA informację ID {zmieniana} → tresc {zmieniana}")
    for etykieta, klucz in (("Przepisy", "PRZEPISY"), ("Słowa kluczowe", "SLOWA_KLUCZOWE"),
                            ("Zagadnienia", "ZAGADNIENIA")):
        wart = _lista(meta.get(klucz))
        if wart:
            out.append(f"{etykieta}: " + "; ".join(wart))
    zal = pola.get("ZALACZNIKI") or []
    if isinstance(zal, list) and zal:
        out.append("Załączniki: " + ", ".join(str(z.get("fileName")) for z in zal if isinstance(z, dict))
                   + f"  (pobierz: /informacje/pliki/{id_}/{doc.get('versionId')}/<plik>)")
    out.append("")

    if not tekst:
        out.append("(Brak treści HTML w EUREKA — pobierz urzędowy PDF: tresc "
                   f"{id_} --pdf plik.pdf)")
        print("\n".join(out))
        return
    if a.fragment:
        spans = _fragmenty(tekst, a.fragment)
        if not spans:
            print("\n".join(out))
            raise BrakWynikow(f"Frazy „{a.fragment}” nie ma w treści informacji {id_} (podaj RDZEŃ słowa).")
        out.append(f"Fragmenty z „{a.fragment}” ({len(spans)}):")
        for s, e in spans:
            out += ["…", tekst[s:e].strip(), "…"]
    else:
        out.append(f"Treść ({len(tekst)} znaków):")
        out.append(tekst)
    print("\n".join(out))


def main():
    ap = argparse.ArgumentParser(
        description="Publiczne API EUREKA Ministerstwa Finansów (read-only, bez klucza): interpretacje "
                    "podatkowe, objaśnienia, WIS/WIA i inne informacje podatkowe.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="surowy JSON")
    ap.add_argument("--strict", action="store_true",
                    help="tresc: zakończ błędem, gdy informacja jest uchylona/zmieniona/wygaszona/nieaktualna "
                         "albo bez treści")
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kategorie", help="kategorie informacji i aliasy do --kategoria")
    k.set_defaults(func=cmd_kategorie)

    s = sub.add_parser("szukaj", help="wyszukiwarka EUREKA (fraza, kategoria, daty, sygnatura)")
    s.add_argument("fraza", nargs="?", default=None,
                   help="wyszukiwanie pełnotekstowe (odmiana słów uwzględniana przez wyszukiwarkę)")
    s.add_argument("--kategoria", help=f"alias(y) po przecinku albo ID; domyślnie „{DOMYSLNA_KATEGORIA}” "
                                       "(indywidualne, ogólne, JST i ich zmiany); „wszystko” = bez filtra")
    s.add_argument("--dokladnie", action="store_true", help="dokładna fraza (słowa obok siebie)")
    s.add_argument("--tylko-tresc", dest="tylko_tresc", action="store_true",
                   help="szukaj tylko w treści (bez tezy i metadanych)")
    s.add_argument("--sygnatura", help="sygnatura lub jej fragment (wielkość liter ma znaczenie)")
    s.add_argument("--od", help="data WYDANIA od (RRRR-MM-DD)")
    s.add_argument("--do", help="data WYDANIA do (RRRR-MM-DD)")
    s.add_argument("--aktualne", action="store_true", help="tylko status „Aktualna” (patrz zastrzeżenie)")
    s.add_argument("--sort", choices=sorted(SORT), help="data (domyślnie bez frazy) | najstarsze | trafnosc "
                                                        "(domyślnie z frazą)")
    s.add_argument("--limit", type=int, default=10, help=f"ile wyników (1–{MAX_LIMIT})")
    s.add_argument("--strona", type=int, default=0, help="numer strony (od 0)")
    s.set_defaults(func=cmd_szukaj)

    g = sub.add_parser("sygnatura", help="dokument po sygnaturze")
    g.add_argument("sygnatura")
    g.set_defaults(func=cmd_sygnatura)

    t = sub.add_parser("tresc", help="metadane, stan i pełna treść po ID albo sygnaturze")
    t.add_argument("id", help="ID informacji (np. 718641) albo sygnatura (np. 0115-KDIT1.4011.700.2026.1.MR)")
    t.add_argument("--fragment", help='okna wokół frazy w treści — podawaj RDZEŃ, np. "kosz"')
    t.add_argument("--pdf", help="zapisz urzędowy eksport PDF do tego pliku")
    t.set_defaults(func=cmd_tresc)

    for p in sub.choices.values():  # flagi globalne także PO komendzie
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="surowy JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="tresc: blokuj informację podważoną albo bez treści")

    a = ap.parse_args()
    try:
        a.func(a)
    except BrakWynikow as e:
        print(e.code, file=sys.stderr)
        sys.exit(1)
    except SystemExit as e:
        if e.code is None or isinstance(e.code, int):
            raise
        print(e.code, file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"BŁĄD: nieoczekiwany błąd helpera eureka.py: {e!r} — to NIE jest brak wyników; "
              "spróbuj ponownie, a przy powtórce zgłoś błąd.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
