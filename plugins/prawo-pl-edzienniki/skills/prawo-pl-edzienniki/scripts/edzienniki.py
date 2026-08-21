#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do API ELI WOJEWÓDZKICH DZIENNIKÓW URZĘDOWYCH (16 dzienników, prawo miejscowe).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET), bez klucza.

Każde województwo prowadzi własny e-Dziennik z API zgodnym z ELI (ten sam wzorzec co
api.sejm.gov.pl/eli, prefiks /api/eli), ale na WŁASNYM hoście — silnik zna tabelę 16 hostów.
Zakres: akty prawa MIEJSCOWEGO (uchwały rad gmin/powiatów/sejmików, rozporządzenia wojewody,
zarządzenia). Prawo krajowe (Dz.U./M.P.) → skill prawo-pl-eli (scripts/eli.py) — treść ustaw
i rozporządzeń krajowych zawsze stamtąd.

Komendy:
  dzienniki [--woj W]                 lista dzienników / roczniki i liczba aktów województwa
  szukaj --woj W ["<fraza tytułu>"] [--rok RRRR] [--limit N] [--strona N]
  akt <woj> <rok> <poz>               metadane aktu (+ linki do PDF/HTML)
  tekst <woj> <rok> <poz> [--fragment "<fraza>"] [--pdf ŚCIEŻKA]
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "1.6.6"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)

# kod → (województwo, host, publisher ELI). Kody = sufiks publishera (POL_WOJ_XX).
WOJEWODZTWA = {
    "DS": ("dolnośląskie", "edzienniki.duw.pl", "POL_WOJ_DS"),
    "KP": ("kujawsko-pomorskie", "edzienniki.bydgoszcz.uw.gov.pl", "POL_WOJ_KP"),
    "LB": ("lubelskie", "edziennik.lublin.uw.gov.pl", "POL_WOJ_LB"),
    "LS": ("lubuskie", "dzienniki.luw.pl", "POL_WOJ_LS"),
    "LD": ("łódzkie", "dziennik.lodzkie.eu", "POL_WOJ_LD"),
    "MP": ("małopolskie", "edziennik.malopolska.uw.gov.pl", "POL_WOJ_MP"),
    "MZ": ("mazowieckie", "edziennik.mazowieckie.pl", "POL_WOJ_MZ"),
    "OP": ("opolskie", "duwo.opole.uw.gov.pl", "POL_WOJ_OP"),
    "PK": ("podkarpackie", "edziennik.rzeszow.uw.gov.pl", "POL_WOJ_PK"),
    "PL": ("podlaskie", "edziennik.bialystok.uw.gov.pl", "POL_WOJ_PL"),
    "PM": ("pomorskie", "edziennik.gdansk.uw.gov.pl", "POL_WOJ_PM"),
    "SL": ("śląskie", "dzienniki.slask.eu", "POL_WOJ_SL"),
    "SK": ("świętokrzyskie", "edziennik.kielce.uw.gov.pl", "POL_WOJ_SK"),
    "WM": ("warmińsko-mazurskie", "edzienniki.olsztyn.uw.gov.pl", "POL_WOJ_WM"),
    "WP": ("wielkopolskie", "edziennik.poznan.uw.gov.pl", "POL_WOJ_WP"),
    "ZP": ("zachodniopomorskie", "e-dziennik.szczecin.uw.gov.pl", "POL_WOJ_ZP"),
}


def _ascii(s):
    return s.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _woj(s):
    """Kod (DS) albo nazwa województwa ('dolnośląskie', 'dolnoslaskie') → (kod, nazwa, host, publisher)."""
    if not s:
        sys.exit("Podaj województwo: --woj <kod|nazwa>, np. --woj DS albo --woj dolnośląskie.\n"
                 "Kody: " + ", ".join(f"{k}={v[0]}" for k, v in sorted(WOJEWODZTWA.items())))
    kod = s.strip().upper()
    if kod in WOJEWODZTWA:
        return (kod,) + WOJEWODZTWA[kod]
    szukane = _ascii(s.strip())
    for k, (nazwa, host, pub) in WOJEWODZTWA.items():
        if _ascii(nazwa).startswith(szukane):
            return k, nazwa, host, pub
    sys.exit(f"Nieznane województwo: {s!r}. Kody: "
             + ", ".join(f"{k}={v[0]}" for k, v in sorted(WOJEWODZTWA.items())))


def _norm(obj):
    """Rekurencyjnie znormalizuj klucze JSON do lowercase — 4 z 16 hostów (starsze wdrożenia
    ABC PRO) zwracają klucze PascalCase (Title/Items) zamiast camelCase (title/items)."""
    if isinstance(obj, dict):
        return {k.lower(): _norm(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(x) for x in obj]
    return obj


def _get(host, path, params=None, raw=False):
    """GET https://{host}/api/eli{path}. Nagłówki jak przeglądarka (WAF na duwo.opole.uw.gov.pl
    odrzuca gołe klienty); krótki timeout (edziennik.mazowieckie.pl bywa nieosiągalny spoza PL)."""
    url = f"https://{host}/api/eli{path}"
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; prawo-pl-eli-edzienniki/"
                      f"{__version__}; +https://github.com/jamarpl21/prawo-pl-eli)",
        "Accept": "application/octet-stream" if raw else "application/json, text/html;q=0.9"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                dane = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if e.code == 404:
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}")
            if e.code == 403:
                sys.exit(f"BŁĄD HTTP 403: {url}\n"
                         f"Host {host} odrzucił żądanie (WAF) — sprawdź akt w przeglądarce: https://{host}/")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e})\n"
                     f"Uwaga: host {host} bywa niedostępny (np. edziennik.mazowieckie.pl odcina "
                     f"ruch spoza PL) — sprawdź w przeglądarce: https://{host}/")
    if raw:
        return dane
    tekst = dane.decode("utf-8", "replace")
    try:
        return _norm(json.loads(tekst))
    except json.JSONDecodeError:
        return tekst


def _data(v):
    """Data ISO z godziną → sama data; sentinel 0001-01-01 → None."""
    if not v or str(v).startswith("0001-01-01"):
        return None
    return str(v)[:10]


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _Stripper()
    p.feed(html)
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Okna tekstu wokół wystąpień frazy (bez rozróżniania wielkości liter) — jak w eli.py."""
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


def _stronicuj(trafienia, limit, strona):
    """Okno paginacji na liście PRZEFILTROWANYCH trafień (nigdy na surowej odpowiedzi API):
    → (wycinek, indeks startu, liczba stron). Gwarantuje, że strony 1..N pokrywają cały zbiór."""
    strony = max(1, -(-len(trafienia) // limit))
    start = (strona - 1) * limit
    return trafienia[start:start + limit], start, strony


def _roczniki(host, publisher):
    """GET /acts → wpis publishera: lista lat + liczba aktów."""
    d = _get(host, "/acts")
    if isinstance(d, list):
        for wpis in d:
            if wpis.get("code") == publisher:
                return wpis
    return None


def cmd_dzienniki(a):
    if not a.woj:
        if a.json:
            print(json.dumps({k: {"wojewodztwo": v[0], "host": v[1], "publisher": v[2]}
                              for k, v in WOJEWODZTWA.items()}, ensure_ascii=False, indent=2)); return
        print("Wojewódzkie dzienniki urzędowe (API ELI na hoście każdego województwa):\n")
        for k, (nazwa, host, pub) in sorted(WOJEWODZTWA.items()):
            dopisek = "  (bywa nieosiągalny spoza PL)" if k == "MZ" else ""
            print(f"  {k}  {nazwa:22s}  https://{host}/  ({pub}){dopisek}")
        print("\nRoczniki województwa: dzienniki --woj <kod>;  wyszukiwanie: szukaj --woj <kod> \"<fraza>\"")
        return
    kod, nazwa, host, pub = _woj(a.woj)
    wpis = _roczniki(host, pub)
    if a.json:
        print(json.dumps(wpis, ensure_ascii=False, indent=2)); return
    if not wpis:
        sys.exit(f"Host {host} nie zwrócił wpisu publishera {pub} — sprawdź https://{host}/")
    lata = wpis.get("years") or []
    print(f"# Dziennik Urzędowy — województwo {nazwa}  ({pub}, https://{host}/)")
    print(f"  Aktów łącznie: {wpis.get('deedscount') or wpis.get('count') or '?'}")
    if lata:
        print(f"  Roczniki: {lata[0]}–{lata[-1]}" if len(lata) > 1 else f"  Rocznik: {lata[0]}")


def cmd_szukaj(a):
    kod, nazwa, host, pub = _woj(a.woj)
    if a.limit < 1 or a.strona < 1:
        sys.exit("--limit i --strona muszą być ≥ 1.")
    if a.rok:
        lata = [a.rok]
    else:
        wpis = _roczniki(host, pub)
        lata = sorted((wpis or {}).get("years") or [], reverse=True)[:3]
        if not lata:
            sys.exit(f"Nie udało się pobrać roczników z {host} — podaj --rok RRRR.")
    # API dzienników IGNORUJE serwerowe filtry (title/type/keyword) — pobieramy cały rocznik
    # jednym żądaniem (brak limitu strony po stronie serwera) i filtrujemy lokalnie po tytule.
    fraza = _ascii(a.fraza) if a.fraza else None
    zebrane, total_info = [], {}
    for rok in lata:
        d = _get(host, f"/acts/{pub}/{rok}", {"limit": 100000})
        if not isinstance(d, dict):
            continue
        items = d.get("items") or []
        trafione = [it for it in items if not fraza or fraza in _ascii(it.get("title") or "")]
        total_info[rok] = f"{len(trafione)}/{d.get('totalcount', len(items))}"
        zebrane.extend(sorted(trafione, key=lambda it: it.get("pos") or 0, reverse=True))
        # bez frazy trafienia = cały rocznik — dalsze roczniki pobieramy tylko, gdy okno strony
        # tego wymaga; Z frazą pobieramy wszystkie żądane roczniki, by licznik trafień był pełny
        if not fraza and len(zebrane) >= a.strona * a.limit:
            break
    okno, start, strony = _stronicuj(zebrane, a.limit, a.strona)
    if a.json:
        print(json.dumps({"wojewodztwo": nazwa, "publisher": pub, "lata": lata,
                          "total": total_info, "trafien": len(zebrane), "strona": a.strona,
                          "stron": strony, "items": okno},
                         ensure_ascii=False, indent=2)); return
    if not zebrane:
        sys.exit(f"Brak wyników w {nazwa} ({', '.join(map(str, lata))}). Filtr frazy działa po "
                 "TYTULE aktu (podłańcuch, bez diakrytyków) — spróbuj krótszej frazy albo innego "
                 "roku (--rok).")
    if not okno:
        sys.exit(f"Strona {a.strona} poza zakresem: {len(zebrane)} trafień = {strony} "
                 f"stron(y) przy --limit {a.limit}.")
    laty = f"{lata[-1]}–{lata[0]}" if len(lata) > 1 else str(lata[0])
    print(f"Województwo {nazwa}, roczniki {laty} "
          f"(trafienia/akty wg lat: {', '.join(f'{r}: {t}' for r, t in total_info.items())})\n")
    for it in okno:
        adres = it.get("displayaddress") or f"{pub} {it.get('year')} poz. {it.get('pos')}"
        tytul = re.sub(r"\s+", " ", it.get("title") or "").strip()
        status = it.get("status") or ""
        print(f"  [{it.get('year')}/{it.get('pos')}]  {adres}")
        print(f"    {it.get('type', '?')}: {tytul[:250]}{'…' if len(tytul) > 250 else ''}")
        if status:
            print(f"    status: {status}  · ogłoszono: {_data(it.get('announcementdate')) or '?'}")
        print()
    if strony > 1:
        print(f"Pokazano {start + 1}–{start + len(okno)} z {len(zebrane)} trafień — reszta: "
              f"--strona <1..{strony}> (po {a.limit} na stronę) albo --limit {len(zebrane)}.")
    pierwsze = okno[0]
    print(f"Metadane/tekst: akt {kod} {pierwsze.get('year')} {pierwsze.get('pos')}  "
          f"/ tekst {kod} {pierwsze.get('year')} {pierwsze.get('pos')}")


def cmd_akt(a):
    kod, nazwa, host, pub = _woj(a.woj)
    d = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, dict) or not d.get("title"):
        sys.exit(f"Nie znaleziono aktu {pub}/{a.rok}/{a.poz} na {host}.")
    tytul = re.sub(r"\s+", " ", d.get("title") or "").strip()
    print(f"# {d.get('displayaddress') or f'{pub} {a.rok} poz. {a.poz}'}")
    print(f"  Typ:      {d.get('type', '?')}")
    print(f"  Tytuł:    {tytul}")
    print(f"  Organ:    {', '.join(d.get('releasedby') or []) or '—'}")
    print(f"  Ogłoszony: {_data(d.get('announcementdate')) or '?'}   "
          f"wejście w życie: {_data(d.get('entryintoforce')) or _data(d.get('validfrom')) or '—'}")
    # pola inForce/entryIntoForce bywają na hostach wojewódzkich niewypełnione — pokazujemy status
    st = d.get("status") or ""
    if st:
        print(f"  Status:   {st}")
    uch = _data(d.get("repealdate")) or _data(d.get("expirationdate"))
    if uch:
        print(f"  Uchylenie/wygaśnięcie: {uch}")
    kw = d.get("keywords") or []
    if kw:
        print(f"  Hasła:    {', '.join(kw)}")
    print(f"  ELI:      https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}")
    if d.get("texthtml"):
        print(f"  Tekst HTML: https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}/text.html")
    if d.get("textpdf"):
        print(f"  Tekst PDF:  https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}/text.pdf")
    print(f"\nTreść: tekst {kod} {a.rok} {a.poz}  [--fragment \"<fraza>\"] [--pdf plik.pdf]")


def cmd_tekst(a):
    kod, nazwa, host, pub = _woj(a.woj)
    if a.pdf:
        dane = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}/text.pdf", raw=True)
        if not dane.startswith(b"%PDF"):
            sys.exit(f"Host {host} nie zwrócił PDF dla {pub}/{a.rok}/{a.poz} — sprawdź: akt {kod} {a.rok} {a.poz}")
        with open(a.pdf, "wb") as f:
            f.write(dane)
        print(f"Zapisano urzędowy PDF: {a.pdf} ({len(dane)} bajtów)")
        return
    surowe = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}/text.html", raw=True).decode("utf-8", "replace")
    txt = html_to_text(surowe)
    if a.json:
        print(json.dumps({"publisher": pub, "rok": a.rok, "poz": a.poz, "tekst": txt},
                         ensure_ascii=False, indent=2)); return
    if not txt.strip():
        sys.exit(f"Pusty text.html dla {pub}/{a.rok}/{a.poz} — pobierz PDF: tekst {kod} {a.rok} {a.poz} --pdf akt.pdf")
    print(f"# {pub} {a.rok} poz. {a.poz}  (tekst z text.html, {len(txt)} znaków)\n")
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w treści ({len(txt)} znaków). Spróbuj inną frazą.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(txt[s:e].strip())
        print(f"\n(okna: {len(spans)} — pominięto resztę; pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długi akt {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def main():
    ap = argparse.ArgumentParser(
        description="API ELI wojewódzkich dzienników urzędowych (read-only, bez klucza): "
                    "prawo miejscowe 16 województw. Prawo krajowe (Dz.U./M.P.): scripts/eli.py.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dz = sub.add_parser("dzienniki")
    dz.add_argument("--woj", help="kod (DS) albo nazwa województwa — pokaże roczniki i liczbę aktów")
    dz.set_defaults(func=cmd_dzienniki)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None,
                   help="fraza z TYTUŁU aktu (podłańcuch, bez rozróżniania diakrytyków)")
    s.add_argument("--woj", required=True, help="kod (DS) albo nazwa województwa")
    s.add_argument("--rok", type=int, help="rocznik (bez --rok: do 3 najnowszych roczników)")
    s.add_argument("--limit", type=int, default=10, help="ile trafień pokazać na stronę")
    s.add_argument("--strona", type=int, default=1,
                   help="strona przefiltrowanych trafień (1..N — N podaje stopka wyniku)")
    s.set_defaults(func=cmd_szukaj)

    ak = sub.add_parser("akt")
    ak.add_argument("woj", help="kod (DS) albo nazwa województwa")
    ak.add_argument("rok", type=int)
    ak.add_argument("poz", type=int)
    ak.set_defaults(func=cmd_akt)

    t = sub.add_parser("tekst")
    t.add_argument("woj", help="kod (DS) albo nazwa województwa")
    t.add_argument("rok", type=int)
    t.add_argument("poz", type=int)
    t.add_argument("--fragment", help='wytnij okna wokół frazy (np. "§ 3")')
    t.add_argument("--pdf", help="zapisz urzędowy PDF do pliku")
    t.set_defaults(func=cmd_tekst)

    # --json działa też PO komendzie (modele piszą flagi właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
