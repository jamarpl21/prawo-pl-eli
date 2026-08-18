#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do publicznego API Centralnego Rejestru Umów JSFP (https://rejestrumow.gov.pl).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only, bez klucza i bez autoryzacji (rejestr jest jawny —
art. 34a ust. 11 ustawy o finansach publicznych; ponowne wykorzystywanie bezpłatne —
art. 34b ust. 7).

Rejestr (od 1.07.2026) zawiera umowy zawierane przez jednostki sektora finansów
publicznych (JSFP): zamawiający, wykonawca (przedsiębiorca / osoba fizyczna / inna JSFP),
przedmiot, wartość, okres, zmiany umowy (aneksy, rozwiązania), wyłączenia jawności.
Identyfikator umowy: UUID (idUmowy). Treść przepisów bierz z ELI (skill prawo-pl-eli);
ogłoszenia o zamówieniach publicznych są w BZP/TED (poza tym skillem).

Komendy:
  najnowsze [--limit N]                          ostatnio opublikowane umowy
  szukaj ["<fraza przedmiotu>"] [--jsfp N] [--regon R] [--nip N] [--wykonawca W]
         [--woj W] [--status Aktywna|Nieaktywna] [--od RRRR-MM-DD] [--do RRRR-MM-DD]
         [--wartosc-od N] [--wartosc-do N] [--sort KLUCZ] [--limit N] [--strona N]
  umowa <idUmowy>                                pełne szczegóły umowy (strony, adresy, zmiany)
  slownik <nazwa>                                słownik API (kraje, strony_umowy, …)
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error

__version__ = "1.6.5"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://rejestrumow.gov.pl/api-dp/v1"

SORTY = ["unitNameAsc", "unitNameDesc", "unitVoivodeshipAsc", "unitVoivodeshipDesc",
         "unitDistrictAsc", "unitDistrictDesc", "unitCommuneAsc", "unitCommuneDesc",
         "unitCityAsc", "unitCityDesc", "modificationDateAsc", "modificationDateDesc",
         "lastChangeDateAsc", "lastChangeDateDesc", "publicationDateAsc", "publicationDateDesc",
         "executionDateAsc", "executionDateDesc", "periodAsc", "periodDesc",
         "priceAsc", "priceDesc"]
SLOWNIKI = ["kraje", "strony_umowy", "rodzaje_zmian_umowy",
            "podstawy_wylaczenia_jawnosci", "zakres_wylaczenia_jawnosci"]
OKNO = 10000  # API przegląda maks. 10 000 wyników na zapytanie (zawężaj filtrami dat)


def _req(path, params=None, body=None):
    """GET (body=None) albo POST JSON, z jednym ponowieniem na błąd przejściowy."""
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        if q:
            url += "?" + q
    dane = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=dane, headers={
        "User-Agent": f"prawo-pl-rejestr-umow/{__version__} (+https://github.com/jamarpl21/prawo-pl-eli)",
        "Accept": "application/json",
        **({"Content-Type": "application/json"} if dane is not None else {})})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                tresc = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            szcz = ""
            try:
                blad = json.loads(e.read().decode("utf-8", "replace"))
                szcz = "; ".join(f"{k}: {v}" for k, v in (blad.get("details") or {}).items()) \
                       or blad.get("message", "")
            except Exception:  # noqa: BLE001
                pass
            if e.code >= 500 and "/agreement/" in path:
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\n"
                         "Zwykle oznacza nieistniejące idUmowy — podaj UUID z wyników komendy "
                         "'szukaj' (np. 0002c775-2526-484f-9b93-5a60e2b934c4).")
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if e.code in (401, 403):
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\n"
                         "API rejestru zaczęło wymagać autoryzacji na tym endpointcie — sprawdź "
                         "https://rejestrumow.gov.pl (dotąd działało bez klucza)."
                         + (f"\nSzczegóły: {szcz}" if szcz else ""))
            sys.exit(f"BŁĄD HTTP {e.code}: {url}" + (f"\nSzczegóły: {szcz}" if szcz else ""))
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    try:
        return json.loads(tresc)
    except json.JSONDecodeError:
        sys.exit(f"BŁĄD: API rejestru zwróciło nie-JSON dla {url} (nieznana ścieżka zwraca "
                 "stronę HTML aplikacji) — sprawdź endpoint / spróbuj ponownie.")


def _uuid_ok(s):
    """idUmowy to UUID (małe/wielkie litery hex)."""
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                             r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s or ""))


def _data(s):
    """Walidacja daty filtra: API przyjmuje RRRR-MM-DD (odpowiedzi zwracają DD.MM.RRRR)."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        sys.exit(f"Zła data {s!r} — format RRRR-MM-DD (np. 2026-07-01).")
    return s


def _liczba(s):
    """'1 000 000,50' / '1000000.50' → float (kwoty w PLN)."""
    try:
        return float(str(s).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        sys.exit(f"Zła kwota {s!r} — podaj liczbę, np. 50000 albo 49999,99.")


def _kwota(v):
    """5015 → '5 015,00 zł' (separator tysięcy = spacja, przecinek dziesiętny)."""
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}".replace(",", " ").replace(".", ",") + " zł"
    except (TypeError, ValueError):
        return str(v)


def _pelne(d):
    return {k: v for k, v in d.items() if v not in (None, "")}


def _filtry(a):
    """Argumenty CLI → body zapytania POST /agreements/search (sekcje wg formularza UI).
    Nieznane sekcje/pola API ignoruje po cichu — trzymaj się nazw z references/api.md."""
    menu = _pelne({
        "przedmiotUmowy": a.fraza,
        "nazwa": a.jsfp, "regon": a.regon, "nip": a.nip,
        "statusUmowy": a.status,
        "dataZawarciaOd": _data(a.od) if a.od else None,
        "dataZawarciaDo": _data(a.do) if a.do else None,
        "dataPublikacjiOd": _data(a.pub_od) if a.pub_od else None,
        "dataPublikacjiDo": _data(a.pub_do) if a.pub_do else None,
        "wartoscOd": _liczba(a.wartosc_od) if a.wartosc_od is not None else None,
        "wartoscDo": _liczba(a.wartosc_do) if a.wartosc_do is not None else None,
    })
    jsfp = _pelne({"wojewodztwo": a.woj, "powiat": a.powiat,
                   "gmina": a.gmina, "miejscowosc": a.miejscowosc})
    wykonawca = _pelne({"nazwa": a.wykonawca, "nip": a.wykonawca_nip,
                        "regon": a.wykonawca_regon})
    body = {}
    if menu:
        body["menuGlowne"] = menu
    if jsfp:
        body["jsfp"] = jsfp
    if wykonawca:
        body["inneStronyUmowy"] = wykonawca
    return body


def _limit(n):
    """API twardo tnie stronę do 50 — obcinamy JAWNIE, żeby nagłówek i numeracja stron nie kłamały
    (przy --limit 100 silnik pobierał 50, a pisał „po 100")."""
    return max(1, min(n, 50))


def _szukaj(body, limit=10, strona=0, sort=None):
    """POST /agreements/search?offset&limit&sortKey — lista umów (limit obcinany do 50)."""
    limit = _limit(limit)
    return _req("/agreements/search",
                {"offset": max(0, strona) * limit, "limit": limit, "sortKey": sort}, body)


def _wiersz(it):
    print(f"  [{it.get('idUmowy', '?')}]")
    print(f"    JSFP: {it.get('nazwa', '?')} (REGON {it.get('regon', '?')})")
    print(f"    zawarta {it.get('dataZawarciaUmowy') or '?'}, "
          f"koniec {it.get('dataZakonczeniaUmowy') or '—'}, "
          f"status: {it.get('statusUmowy') or '?'}, "
          f"wartość: {_kwota(it.get('wartoscPrzedmiotuUmowy'))}")
    przedmiot = re.sub(r"\s+", " ", it.get("przedmiotUmowy") or "").strip()
    if przedmiot:
        print(f"    {przedmiot[:300]}{'…' if len(przedmiot) > 300 else ''}")
    print(f"    → umowa {it.get('idUmowy', '?')}")
    print()


def _lista(d, limit, strona):
    tresc = d.get("content") or []
    ile = d.get("totalMatchingElements", len(tresc))
    if not tresc:
        sys.exit("Brak wyników. Fraza szuka w PRZEDMIOCIE umowy; nazwy JSFP wpisują jednostki "
                 "(bywają skróty/literówki) — spróbuj krótszej frazy, --regon/--nip albo --wykonawca.\n"
                 "Pamiętaj: rejestr obejmuje umowy zawarte od 1.07.2026.")
    print(f"Pasujących umów: {ile}  (strona {strona}, po {limit})\n")
    for it in tresc:
        _wiersz(it)
    if ile > OKNO:
        print(f"UWAGA: API przegląda maks. {OKNO} wyników na zapytanie — zawęź filtrami "
              "(--od/--do, --pub-od/--pub-do, --woj, --wartosc-od).")
    if len(tresc) == limit:
        print(f"Kolejna strona: --strona {strona + 1}")


def cmd_najnowsze(a):
    a.limit = _limit(a.limit)
    d = _szukaj({}, a.limit, sort="publicationDateDesc")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Ostatnio opublikowane w Centralnym Rejestrze Umów "
          f"(wszystkich umów: {d.get('totalMatchingElements', '?')}):\n")
    for it in d.get("content") or []:
        _wiersz(it)


def cmd_szukaj(a):
    if a.zapytanie:
        try:
            body = json.loads(a.zapytanie)
        except json.JSONDecodeError as e:
            sys.exit(f"--zapytanie: niepoprawny JSON ({e}).")
    else:
        body = _filtry(a)
    if not body:
        sys.exit("Podaj kryterium: frazę przedmiotu umowy, --jsfp/--regon/--nip, --wykonawca, "
                 "--woj, --status, zakres dat/wartości albo --zapytanie '<json>'.\n"
                 "Pełną listę bez filtra daje komenda 'najnowsze'.")
    a.limit = _limit(a.limit)
    d = _szukaj(body, a.limit, a.strona, a.sort)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    _lista(d, a.limit, a.strona)


def _strona_umowy(i, s):
    kto = s.get("nazwa") or " ".join(x for x in (s.get("imie"), s.get("nazwisko")) if x) or "?"
    idn = "  ".join(f"{k} {v}" for k, v in (("NIP", s.get("nip")), ("REGON", s.get("regon"))) if v)
    print(f"  {i}. [{s.get('rodzaj') or '?'}] {kto}{' (konsorcjum)' if s.get('czyKonsorcjum') else ''}"
          + (f"   {idn}" if idn else ""))
    adr = s.get("daneAdresowe") or {}
    ulica = " ".join(x for x in (adr.get("ulica"), adr.get("numerNieruchomosci")) if x)
    if ulica and adr.get("numerLokalu"):
        ulica += f"/{adr['numerLokalu']}"
    czesci = [ulica, " ".join(x for x in (adr.get("kodPocztowy"), adr.get("miejscowosc")) if x),
              f"woj. {adr['wojewodztwo'].lower()}" if adr.get("wojewodztwo") else "",
              s.get("kraj") if s.get("kraj") not in (None, "Polska") else ""]
    linia = ", ".join(c for c in czesci if c)
    if linia:
        print(f"     {linia}")
    if s.get("niejawnoscStrony"):
        print(f"     WYŁĄCZENIE JAWNOŚCI strony: {s['niejawnoscStrony']}")


def _zmiana(z):
    if not isinstance(z, dict):
        print(f"  - {z}"); return
    rodzaj = z.get("rodzajZmiany") or z.get("rodzaj") or "zmiana"
    data = z.get("dataZmiany") or z.get("data") or "?"
    print(f"  - {rodzaj} ({data})")
    if z.get("komentarz"):
        print(f"    {z['komentarz']}")
    reszta = {k: v for k, v in z.items() if v not in (None, "", [])
              and k not in ("rodzajZmiany", "rodzaj", "dataZmiany", "data", "komentarz")}
    if reszta:
        print(f"    {json.dumps(reszta, ensure_ascii=False)}")


def cmd_umowa(a):
    if not _uuid_ok(a.id):
        sys.exit(f"idUmowy {a.id!r} nie wygląda na UUID — podaj identyfikator z wyników "
                 "komendy 'szukaj' (np. 0002c775-2526-484f-9b93-5a60e2b934c4).")
    d = _req(f"/agreement/{a.id.lower()}")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    pd = d.get("podstawoweDane") or {}
    okres = d.get("okresObowiazywania") or {}
    szcz = d.get("szczegolyUmowy") or {}
    numer = "bez numeru" if pd.get("brakNumeruUmowy") else (pd.get("numerUmowy") or "?")
    print(f"# Umowa {numer}   [{d.get('idUmowy', a.id)}]")
    print(f"  Status:      {pd.get('statusUmowy', '?')}")
    koniec = "na czas nieoznaczony" if okres.get("umowaNaCzasNieoznaczony") \
        else (pd.get("dataZakonczeniaUmowy") or "—")
    print(f"  Obowiązuje:  zawarta {pd.get('dataZawarciaUmowy') or '?'} → {koniec}"
          + (f"   (okres: {okres['okres']})" if okres.get("okres") else ""))
    print(f"  Wartość:     {_kwota(szcz.get('wartoscPrzedmiotu'))}"
          + (f"   ({szcz['opisWartosciPrzedmiotu']})" if szcz.get("opisWartosciPrzedmiotu") else ""))
    print(f"  Publikacja:  {d.get('dataPublikacji') or '?'}   "
          f"ostatnia modyfikacja: {d.get('dataModyfikacji') or '—'}")
    if d.get("finansowanaZeSrodkow") is not None:
        print(f"  Środki z art. 5 ust. 1 pkt 2–3 u.f.p. (UE/zagraniczne): "
              f"{'tak' if d['finansowanaZeSrodkow'] else 'nie'}")
    print(f"  Rejestr:     https://rejestrumow.gov.pl/umowa/{d.get('idUmowy', a.id)}")
    przedmiot = (szcz.get("przedmiotUmowy") or "").strip()
    print(f"\n## Przedmiot umowy\n{przedmiot or '(brak / wyłączenie jawności)'}")
    for pole, opis in (("niejawnoscPrzedmiotu", "przedmiotu"), ("niejawnoscWartosciPrzedmiotu", "wartości")):
        if szcz.get(pole):
            print(f"WYŁĄCZENIE JAWNOŚCI {opis}: {szcz[pole]}")
    strony = d.get("stronyUmowy") or []
    print(f"\n## Strony umowy ({len(strony)})")
    for i, s in enumerate(strony, 1):
        _strona_umowy(i, s)
    zmiany = d.get("zmianyUmowy") or []
    if zmiany:
        print(f"\n## Zmiany umowy ({len(zmiany)})")
        for z in zmiany:
            _zmiana(z)


def cmd_slownik(a):
    d = _req("/dictionary", {"name": a.nazwa})
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, list) or not d:
        sys.exit(f"Pusty/nieznany słownik {a.nazwa!r}. Znane: {', '.join(SLOWNIKI)}.")
    print(f"Słownik {a.nazwa!r} ({len(d)}):")
    for it in d:
        print(f"  {it.get('code', '?'):<8} {it.get('name', '?')}")


def main():
    ap = argparse.ArgumentParser(
        description="Publiczne API Centralnego Rejestru Umów JSFP (read-only, bez klucza): "
                    "umowy jednostek sektora finansów publicznych zawierane od 1.07.2026.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("najnowsze")
    n.add_argument("--limit", type=int, default=10, help="ile wyników (1–50)")
    n.set_defaults(func=cmd_najnowsze)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None,
                   help="fraza z PRZEDMIOTU umowy (np. 'remont drogi')")
    s.add_argument("--jsfp", help="nazwa JSFP (zamawiającego), np. 'urząd gminy'")
    s.add_argument("--regon", help="REGON JSFP")
    s.add_argument("--nip", help="NIP JSFP")
    s.add_argument("--wykonawca", help="nazwa drugiej strony umowy (wykonawcy)")
    s.add_argument("--wykonawca-nip", help="NIP wykonawcy")
    s.add_argument("--wykonawca-regon", help="REGON wykonawcy")
    s.add_argument("--woj", help="województwo JSFP (np. mazowieckie)")
    s.add_argument("--powiat", help="powiat JSFP")
    s.add_argument("--gmina", help="gmina JSFP")
    s.add_argument("--miejscowosc", help="miejscowość JSFP")
    s.add_argument("--status", choices=["Aktywna", "Nieaktywna"],
                   help="status umowy (dokładnie tak: Aktywna / Nieaktywna)")
    s.add_argument("--od", help="data zawarcia od (RRRR-MM-DD)")
    s.add_argument("--do", help="data zawarcia do (RRRR-MM-DD)")
    s.add_argument("--pub-od", help="data publikacji od (RRRR-MM-DD)")
    s.add_argument("--pub-do", help="data publikacji do (RRRR-MM-DD)")
    s.add_argument("--wartosc-od", help="wartość umowy od (PLN)")
    s.add_argument("--wartosc-do", help="wartość umowy do (PLN)")
    s.add_argument("--sort", choices=SORTY, default=None,
                   help="sortowanie, np. priceDesc, publicationDateDesc, executionDateAsc")
    s.add_argument("--zapytanie", help="surowe body JSON zapytania (pełny dostęp do wszystkich "
                                       "sekcji filtrów — zob. references/api.md)")
    s.add_argument("--limit", type=int, default=10, help="ile wyników (1–50; API obcina do 50)")
    s.add_argument("--strona", type=int, default=0, help="numer strony (od 0)")
    s.set_defaults(func=cmd_szukaj)

    u = sub.add_parser("umowa")
    u.add_argument("id", help="idUmowy (UUID z wyników 'szukaj')")
    u.set_defaults(func=cmd_umowa)

    sl = sub.add_parser("slownik")
    sl.add_argument("nazwa", help=f"nazwa słownika: {', '.join(SLOWNIKI)}")
    sl.set_defaults(func=cmd_slownik)

    # --json działa też PO komendzie (modele piszą flagi właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
