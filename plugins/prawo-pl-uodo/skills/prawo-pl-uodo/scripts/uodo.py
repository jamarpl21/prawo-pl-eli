#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO API Portalu Orzeczeń UODO (https://orzeczenia.uodo.gov.pl/api-doc/).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET), bez klucza i bez autoryzacji.

Portal (od 2025 r.) publikuje DECYZJE PREZESA UODO (kary za naruszenia RODO, upomnienia, nakazy)
oraz powiązane orzeczenia sądów w sprawach z tych decyzji. Identyfikatory: sygnatura
(np. DKN.5131.9.2025) i URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9). Treść RODO bierz z EUR-Lex
(skill prawo-eu-eurlex); wyroki WSA/NSA ze skarg na decyzje UODO — z CBOSA (prawo-pl-cbosa).

Komendy:
  najnowsze [--limit N]                          ostatnio opublikowane dokumenty
  szukaj ["<fraza>"] [--tytul F] [--od RRRR-MM-DD] [--do RRRR-MM-DD]
         [--warunek "indeks:operator:wartość"] [--limit N] [--strona N]
  decyzja <sygnatura|URN> [--fragment "<fraza>"]  metadane + pełna treść decyzji
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error

__version__ = "1.6.4"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://orzeczenia.uodo.gov.pl/api"
POLA = "id,refid,refname,title,dates,kind"  # domyślne pola listy wyników


def _get(path, params=None, raw=False):
    """GET z jednym ponowieniem na błąd przejściowy. raw=True: zwróć tekst (body.txt)."""
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": f"prawo-pl-uodo/{__version__} (+https://github.com/jamarpl21/prawo-pl-eli)",
        "Accept": "text/plain" if raw else "application/json"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                tresc = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if e.code == 404:
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}\n"
                         "Sprawdź sygnaturę/URN — format np. DKN.5131.9.2025 albo "
                         "urn:ndoc:gov:pl:uodo:2025:dkn_5131_9.")
            if e.code in (401, 403):
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\n"
                         "API UODO zaczęło wymagać autoryzacji na tym endpointcie — sprawdź "
                         "https://orzeczenia.uodo.gov.pl/api-doc/ (dotąd działało bez klucza).")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    if raw:
        return tresc
    try:
        return json.loads(tresc)
    except json.JSONDecodeError:
        sys.exit(f"BŁĄD: API UODO zwróciło nie-JSON dla {url} — spróbuj ponownie za chwilę.")


def _pl(v):
    """Pole wielojęzyczne ({'pl': …}) albo zwykły string → tekst."""
    if isinstance(v, dict):
        return v.get("pl") or next(iter(v.values()), "")
    return v or ""


def _daty(item):
    """Lista dates[] → '{ogłoszenie}' / '{publikacja}' (use=announcement/publication)."""
    out = {}
    for d in item.get("dates") or []:
        out[d.get("use", "")] = d.get("date", "")
    return out


def _refid(s):
    """Sygnatura (DKN.5131.9.2025) albo URN → URN. Rok = ostatni 4-cyfrowy człon sygnatury.
    URN-y portalu są w ASCII — polskie znaki sygnatur transliterujemy (ZSOŚS → zsoss)."""
    s = s.strip()
    if s.lower().startswith("urn:"):
        return s.lower()
    czlony = [c for c in re.split(r"[.\s/]+", s) if c]
    rok = next((c for c in reversed(czlony) if re.fullmatch(r"(19|20)\d{2}", c)), None)
    if not rok or len(czlony) < 2:
        sys.exit(f"Nie umiem zbudować URN z {s!r}. Podaj sygnaturę (np. DKN.5131.9.2025) "
                 "albo pełny URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9).")
    reszta = "_".join(c.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
                      for c in czlony if c != rok)
    return f"urn:ndoc:gov:pl:uodo:{rok}:{reszta}"


def _timespan(od, do):
    return f"{od or ''},{do or ''}"


def _szukaj(timespan, warunek=None, limit=10, strona=0):
    """GET /documents/search/PublicDocument/{timespan}[/{warunek}] — lista dokumentów."""
    sciezka = f"/documents/search/PublicDocument/{urllib.parse.quote(timespan, safe=',')}"
    if warunek:
        sciezka += "/" + urllib.parse.quote(warunek, safe=":,()")
    return _get(sciezka, {"order": "-id", "from": max(0, strona) * limit,
                          "count": max(1, min(limit, 100)), "fields": POLA})


def _wiersz(item):
    refname = item.get("refname") or item.get("refid") or "?"
    daty = _daty(item)
    tytul = re.sub(r"\s+", " ", _pl(item.get("title"))).strip()
    print(f"  [{refname}]  ({daty.get('announcement', '?')}, publikacja {daty.get('publication', '?')})"
          f"  {item.get('kind', '')}")
    if tytul:
        print(f"    {tytul[:400]}{'…' if len(tytul) > 400 else ''}")
    print(f"    → decyzja {refname}")
    print()


def cmd_najnowsze(a):
    d = _szukaj(_timespan(None, None), limit=a.limit)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, list) or not d:
        sys.exit("Brak wyników — spróbuj ponownie za chwilę.")
    print(f"Ostatnio opublikowane w portalu orzeczeń UODO ({len(d)}):\n")
    for it in d:
        _wiersz(it)


def cmd_szukaj(a):
    warunki = []
    if a.warunek:
        warunki.append(a.warunek)
    if a.fraza:
        warunki.append(f"content_pl:regex:{a.fraza}")
    if a.tytul:
        warunki.append(f"title_pl:regex:{a.tytul}")
    if not warunki and not (a.od or a.do):
        sys.exit("Podaj kryterium: frazę (pełnotekstowo) albo --tytul / --warunek / zakres dat --od/--do.")
    if len(warunki) > 1:
        print(f"UWAGA: API UODO stosuje JEDEN warunek na zapytanie — używam: {warunki[0]!r} "
              f"(pomijam: {', '.join(repr(w) for w in warunki[1:])}).\n", file=sys.stderr)
    d = _szukaj(_timespan(a.od, a.do), warunki[0] if warunki else None, a.limit, a.strona)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, list):
        sys.exit("BŁĄD: API UODO zwróciło nieoczekiwaną odpowiedź.")
    if not d:
        sys.exit("Brak wyników. Fraza działa jak regex (bez rozróżniania wielkości liter) i szuka "
                 "DOSŁOWNIE — a tytuły i treści są po polsku ODMIENIONE ('nałożenie kary "
                 "pieniężnej', nie 'kara pieniężna'). Podaj RDZEŃ bez końcówki: 'pieniężn', "
                 "'biometr', 'monitoring'. To NIE dowód, że takich decyzji nie ma.")
    print(f"Znaleziono: {len(d)}  (strona {a.strona}, po {a.limit}; sortowanie od najnowszych)\n")
    for it in d:
        _wiersz(it)
    if len(d) == a.limit:
        print(f"Kolejna strona: --strona {a.strona + 1}")


def cmd_decyzja(a):
    refid = _refid(a.id)
    meta = _get(f"/documents/public/items/{urllib.parse.quote(refid, safe=':')}/meta.json")
    if a.json:
        meta["_body"] = _get(f"/documents/public/items/{urllib.parse.quote(refid + ':0', safe=':')}/body.txt",
                             params={"lang": "pl"}, raw=True)
        print(json.dumps(meta, ensure_ascii=False, indent=2)); return
    refname = meta.get("refname", a.id)
    daty = _daty(meta)
    pub = meta.get("publication") or {}
    print(f"# {_pl(meta.get('name')) or refname}   [{refname}]")
    print(f"  URN:        {meta.get('refid', refid)}")
    print(f"  Rodzaj:     {meta.get('kind', '?')}   status: {pub.get('status', '?')}"
          f"   w obrocie: {'tak' if pub.get('inforce') else 'nie/brak danych'}")
    print(f"  Daty:       ogłoszenie {daty.get('announcement', '?')}, publikacja "
          f"{daty.get('publication', '?')}, walidacja {daty.get('validation', '—')}")
    print(f"  Portal:     https://orzeczenia.uodo.gov.pl (API: {BASE}/documents/public/items/{refid}/meta.json)")
    tytul = _pl(meta.get("title"))
    if tytul:
        print(f"\n## Przedmiot\n{tytul.strip()}")
    txt = _get(f"/documents/public/items/{urllib.parse.quote(refid + ':0', safe=':')}/body.txt",
               params={"lang": "pl"}, raw=True).strip()
    print(f"\n## Treść decyzji ({len(txt)} znaków)")
    if not txt:
        print("(brak treści w API — zob. portal wyżej)")
        return
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w treści ({len(txt)} znaków). Tekst jest "
                     "po polsku odmieniony — podaj RDZEŃ bez końcówki ('pieniężn' zamiast 'kara "
                     "pieniężna'). To NIE dowód, że decyzja o tym nie mówi.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(txt[s:e].strip())
        print(f"\n(okna: {len(spans)} — pominięto resztę; pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długa decyzja {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Okna tekstu wokół wystąpień frazy (bez rozróżniania wielkości liter) — jak w saos.py."""
    low, f = txt.lower(), fraza.lower().strip()
    if not f:
        return []
    spans, p = [], low.find(f)
    while p != -1 and len(spans) < maks:
        start = max(0, p - okno // 3)
        end = min(len(txt), p + len(f) + okno)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        p = low.find(f, p + len(f))
    return spans


def main():
    ap = argparse.ArgumentParser(
        description="Oficjalne API Portalu Orzeczeń UODO (read-only, bez klucza): "
                    "decyzje Prezesa UODO i powiązane orzeczenia sądów.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("najnowsze")
    n.add_argument("--limit", type=int, default=10, help="ile wyników (1–100)")
    n.set_defaults(func=cmd_najnowsze)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None,
                   help="wyszukiwanie pełnotekstowe (regex, bez rozróżniania wielkości liter)")
    s.add_argument("--tytul", help="fraza w tytule/przedmiocie decyzji (regex)")
    s.add_argument("--warunek", help='surowy warunek API: "indeks:operator:wartość", '
                                     'np. "publicator_subtype:eq:uodo" (operatory: eq ne lt gt le ge in glob regex)')
    s.add_argument("--od", help="data publikacji od (RRRR-MM-DD)")
    s.add_argument("--do", help="data publikacji do (RRRR-MM-DD)")
    s.add_argument("--limit", type=int, default=10, help="ile wyników (1–100)")
    s.add_argument("--strona", type=int, default=0, help="numer strony (od 0)")
    s.set_defaults(func=cmd_szukaj)

    d = sub.add_parser("decyzja")
    d.add_argument("id", help="sygnatura (DKN.5131.9.2025) albo URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9)")
    d.add_argument("--fragment", help='wytnij okna wokół frazy w treści — podawaj RDZEŃ, np. "pieniężn"')
    d.set_defaults(func=cmd_decyzja)

    # --json działa też PO komendzie (modele piszą flagi właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
