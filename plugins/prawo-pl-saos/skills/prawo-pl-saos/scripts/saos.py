#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do PUBLICZNEGO API SAOS (System Analizy Orzeczeń Sądowych, https://www.saos.org.pl/api).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET).

SAOS to baza WTÓRNA orzecznictwa polskiego (agregat orzeczeń jawnych): Sąd Najwyższy (SN),
Trybunał Konstytucyjny (TK), sądy powszechne (SA/SO/SR) i Krajowa Izba Odwoławcza (KIO).
Sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — dla nich użyj skilla
prawo-pl-cbosa (baza CBOSA, https://orzeczenia.nsa.gov.pl). Treść przepisów bierz z ELI
(skill prawo-pl-eli), nie z SAOS — tu szukasz, JAK sądy stosują przepisy.

Komendy:
  szukaj ["<fraza>"] [--sad SN|TK|powszechne|admin|KIO] [--sygnatura S] [--przepis P]
         [--sedzia N] [--haslo H] [--typ wyrok|postanowienie|uchwala|zarzadzenie|uzasadnienie]
         [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--limit N] [--strona N]
  orzeczenie <id> [--fragment "<fraza>"]   pełne orzeczenie: metadane, powołane przepisy/orzeczenia, treść
  sygnatura <sygnatura...>                  znajdź orzeczenie po numerze sprawy (caseNumber)
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.0.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://www.saos.org.pl/api"
CONTENT_HOSTS = ("saos.org.pl",)


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


def _wymus_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed)
                    for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi HTTP na hostach treści SAOS, a obce cele HTTP odrzuca."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)


_opener = urllib.request.build_opener(_PrzekierowaniaHttps())

# Aliasy typów sądów (courtType w API SAOS)
SADY = {
    "SN": "SUPREME", "SUPREME": "SUPREME",
    "TK": "CONSTITUTIONAL_TRIBUNAL", "TRYBUNAŁ": "CONSTITUTIONAL_TRIBUNAL",
    "TRYBUNAL": "CONSTITUTIONAL_TRIBUNAL", "CONSTITUTIONAL_TRIBUNAL": "CONSTITUTIONAL_TRIBUNAL",
    "POWSZECHNE": "COMMON", "POWSZECHNY": "COMMON", "COMMON": "COMMON",
    "SA": "COMMON", "SO": "COMMON", "SR": "COMMON",
    "ADMIN": "ADMINISTRATIVE", "ADMINISTRACYJNE": "ADMINISTRATIVE",
    "NSA": "ADMINISTRATIVE", "WSA": "ADMINISTRATIVE", "ADMINISTRATIVE": "ADMINISTRATIVE",
    "KIO": "NATIONAL_APPEAL_CHAMBER", "NATIONAL_APPEAL_CHAMBER": "NATIONAL_APPEAL_CHAMBER",
}
# Aliasy typów orzeczeń (judgmentTypes w API SAOS)
TYPY = {
    "WYROK": "SENTENCE", "SENTENCE": "SENTENCE",
    "POSTANOWIENIE": "DECISION", "DECISION": "DECISION",
    "UCHWAŁA": "RESOLUTION", "UCHWALA": "RESOLUTION", "RESOLUTION": "RESOLUTION",
    "ZARZĄDZENIE": "REGULATION", "ZARZADZENIE": "REGULATION", "REGULATION": "REGULATION",
    "UZASADNIENIE": "REASONS", "REASONS": "REASONS",
}
# Czytelne nazwy typów sądów
CT_PL = {
    "SUPREME": "Sąd Najwyższy", "CONSTITUTIONAL_TRIBUNAL": "Trybunał Konstytucyjny",
    "COMMON": "sąd powszechny", "ADMINISTRATIVE": "sąd administracyjny",
    "NATIONAL_APPEAL_CHAMBER": "Krajowa Izba Odwoławcza",
}
# Czytelne nazwy typów orzeczeń (judgmentType w API SAOS) — API zwraca surowy enum
TYPY_PL = {
    "SENTENCE": "wyrok", "DECISION": "postanowienie", "RESOLUTION": "uchwała",
    "REGULATION": "zarządzenie", "REASONS": "uzasadnienie",
}
# SAOS jest bazą WTÓRNĄ i część zbiorów przestała być zasilana. Bez tej informacji zero trafień
# z nowszą datą wygląda jak „nie ma takiego orzecznictwa" — a to najgroźniejszy fałszywy negatyw
# w pracy prawnika. Granica = data NAJNOWSZEGO orzeczenia danego sądu w SAOS (sortowanie po dacie
# malejąco), sprawdzona na żywo 2026-08-23; porównujemy z DOKŁADNOŚCIĄ DO DNIA, bo np. uchwały SN
# z II półrocza 2016 r. (III CZP 81/16 z 8.12.2016) istnieją, a w SAOS ich nie ma. Sądy powszechne
# są zasilane na bieżąco, więc ich tu nie ma. Silnik dodatkowo potwierdza granicę na żywo
# (_granica_zbioru), gdy ma zablokować albo wyjaśnić zero wyników — gdyby SAOS wznowił zasilanie.
ZASIEG = {
    "SUPREME": ("2016-06-22", "nowsze orzeczenia SN: portal SN (www.sn.pl/orzecznictwo) "
                              "albo Baza Orzeczeń SN"),
    "CONSTITUTIONAL_TRIBUNAL": ("2015-12-09", "nowsze orzeczenia TK: OTK (ipo.trybunal.gov.pl)"),
    "NATIONAL_APPEAL_CHAMBER": ("2018-09-06", "nowsze orzeczenia KIO: UZP "
                                              "(orzeczenia.uzp.gov.pl)"),
}
_granice_na_zywo = {}  # courtType -> data najnowszego orzeczenia wg API (cache tylko w procesie)


def _data_pl(iso):
    """'2016-06-22' → '22.06.2016' (format dat w polskich pismach)."""
    if iso and re.match(r"^\d{4}-\d{2}-\d{2}$", iso):
        return f"{iso[8:10]}.{iso[5:7]}.{iso[:4]}"
    return iso or "?"


def _norma_data(d, koniec=False):
    """Normalizuje datę z CLI do RRRR-MM-DD (RRRR → 1 stycznia / 31 grudnia; RRRR-MM → 1./ostatni dzień).

    Dzięki temu granice zbiorów porównujemy po prostu leksykograficznie (ISO 8601).
    """
    if d is None:
        return None
    d = d.strip()
    if re.match(r"^\d{4}$", d):
        return f"{d}-12-31" if koniec else f"{d}-01-01"
    if re.match(r"^\d{4}-\d{2}$", d):
        if not koniec:
            return f"{d}-01"
        rok, mies = int(d[:4]), int(d[5:7])
        dni = 29 if mies == 2 and (rok % 4 == 0 and (rok % 100 != 0 or rok % 400 == 0)) else \
            [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mies - 1]
        return f"{d}-{dni:02d}"
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        sys.exit(f"BŁĄD: data {d!r} ma zły format — użyj RRRR-MM-DD (albo RRRR / RRRR-MM).")
    return d


def _granica_zbioru(ct):
    """Koniec zamkniętego zbioru: znana data z ZASIEG, potwierdzona na żywo (najnowsze orzeczenie).

    Zwraca (data_ISO, potwierdzona_na_zywo: bool). Jedno tanie zapytanie (pageSize=1, sort po dacie
    malejąco) na proces; przy awarii transportu lub przerwie technicznej zostaje znana granica.
    Gdyby SAOS wznowił zasilanie, granica na żywo będzie późniejsza od znanej i to ona obowiązuje.
    """
    znana = ZASIEG[ct][0]
    if ct in _granice_na_zywo:
        zywa = _granice_na_zywo[ct]
        return (max(znana, zywa), True) if zywa else (znana, False)
    zywa = None
    try:
        d = _get("/search/judgments", {"courtType": ct, "pageSize": 1, "pageNumber": 0,
                                       "sortingField": "JUDGMENT_DATE",
                                       "sortingDirection": "DESC"}, soft=True)
        items = d.get("items") if isinstance(d, dict) else None
        if isinstance(items, list) and items and re.match(r"^\d{4}-\d{2}-\d{2}$",
                                                           str(items[0].get("judgmentDate", ""))):
            zywa = items[0]["judgmentDate"]
    except VerificationUnknown:
        zywa = None
    _granice_na_zywo[ct] = zywa
    return (max(znana, zywa), True) if zywa else (znana, False)


def _granica_znana(ct, items=()):
    """Granica zbioru bez zapytania: cache z potwierdzenia na żywo albo stała; a jeśli wśród wyników
    (posortowanych po dacie malejąco) jest orzeczenie PÓŹNIEJSZE niż granica, to ona się przesuwa —
    wynik wyszukiwania sam dowodzi, że SAOS wznowił zasilanie."""
    granica, potwierdzona = ZASIEG[ct][0], False
    zywa = _granice_na_zywo.get(ct)
    if zywa:
        granica, potwierdzona = max(granica, zywa), True
    daty = [str(it.get("judgmentDate") or "") for it in items if isinstance(it, dict)]
    daty = [d for d in daty if re.match(r"^\d{4}-\d{2}-\d{2}$", d) and d > granica]
    if daty:
        granica, potwierdzona = max(daty), True
        _granice_na_zywo[ct] = granica
    return granica, potwierdzona


def _ostrzezenie_zasiegu(ct, od=None, do=None, granica=None, potwierdzona=False):
    """Komunikat o granicy zbioru dla typu sądu (None = zbiór zasilany na bieżąco).

    Porównania z dokładnością do DNIA: zakres zaczynający się po granicy jest cały poza zbiorem;
    zakres sięgający poza granicę (albo bez --do) ma w SAOS tylko część sprzed granicy.
    """
    wpis = ZASIEG.get(ct or "")
    if not wpis:
        return None
    gdzie = wpis[1]
    granica = granica or wpis[0]
    jak = "potwierdzone na żywo: najnowsze orzeczenie w SAOS" if potwierdzona \
        else "data najnowszego orzeczenia w SAOS, sprawdzona 2026-08-23"
    tekst = (f"UWAGA: w SAOS {CT_PL.get(ct, ct)} kończy się DOKŁADNIE na {_data_pl(granica)} ({jak}) — "
             f"ten zbiór nie jest już zasilany. Brak nowszych trafień to NIE brak orzecznictwa; {gdzie}.")
    od, do = _norma_data(od), _norma_data(do, koniec=True)
    if od and od > granica:
        tekst += (f"\n     Twój zakres zaczyna się {_data_pl(od)}, czyli POZA zbiorem — stąd zero wyników; "
                  f"orzeczenia z tego okresu istnieją, ale nie w SAOS.")
    elif do is None or do > granica:
        koniec = "nie ma górnej granicy" if do is None else f"sięga {_data_pl(do)}"
        tekst += (f"\n     Twój zakres {koniec} — po {_data_pl(granica)} SAOS nie ma NIC; "
                  f"orzeczenia z okresu po tej dacie sprawdź w źródle urzędowym.")
    return tekst


def _sprawdz_zasieg_strict(a, ct):
    """Blokuje zakres sięgający poza znany koniec zamkniętego zbioru SAOS (porównanie do DNIA)."""
    if not getattr(a, "strict", False) or ct not in ZASIEG:
        return
    od, do = _norma_data(getattr(a, "od", None)), _norma_data(a.do, koniec=True)
    if do is not None and do <= ZASIEG[ct][0]:
        return
    # zakres wychodzi poza znaną granicę — zanim zablokujemy, potwierdź granicę na żywo
    # (gdyby SAOS wznowił zasilanie, granica przesunie się i zakres może być w zbiorze)
    granica, potwierdzona = _granica_zbioru(ct)
    if do is not None and do <= granica:
        return
    gdzie = ZASIEG[ct][1]
    # blokada jest DETERMINISTYCZNA (znana granica zbioru), nie awarią transportu —
    # komunikat nie może sugerować ponowienia, tylko jak zawęzić zakres albo gdzie szukać
    if od and od > granica:
        zakres, rada = f"zaczyna się {_data_pl(od)}", gdzie
    else:
        zakres = "nie ma górnej granicy" if do is None else f"sięga {_data_pl(do)}"
        rada = f"zawęź zakres: --do {granica}; {gdzie}"
    jak = "granica potwierdzona na żywo" if potwierdzona \
        else "nie udało się potwierdzić granicy na żywo — przyjęto znaną z 2026-08-23"
    sys.exit(f"BŁĄD: zbiór {CT_PL.get(ct, ct)} w SAOS kończy się na orzeczeniu z {_data_pl(granica)} "
             f"({jak}), a żądany zakres {zakres} — tryb strict nie zwróci wyniku, którego "
             f"kompletności nie da się zweryfikować. {rada[0].upper() + rada[1:]}.")


def _get(path, params=None, soft=False):
    """GET z jednym ponowieniem (limit 90 s na żądanie — /search/judgments bywa bardzo wolny).

    Zwraca dane, w tym pusty wynik jako VERIFIED_ABSENT. Przy soft=True błąd
    żądania ma osobny stan UNKNOWN (VerificationUnknown), nigdy None/pusty wynik.
    Przekroczenie czasu to „BŁĄD sieci" (kod ≠ 0) — nigdy fałszywe zero trafień.
    """
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": f"saos-skill/{__version__}", "Accept": "application/json"})
    raw = None
    for attempt in (1, 2):
        try:
            with _opener.open(req, timeout=90) as r:
                raw = r.read().decode("utf-8", "replace")
            if "Przerwa techniczna" in raw:
                # SAOS bywa w oknie serwisowym — zwraca stronę HTML zamiast JSON (HTTP 200)
                if attempt == 1:
                    time.sleep(2); continue
                if soft:
                    raise VerificationUnknown("SAOS ma przerwę techniczną")
                sys.exit("BŁĄD: SAOS ma przerwę techniczną (serwis chwilowo niedostępny) — spróbuj ponownie później.")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if soft:
                raise VerificationUnknown(f"HTTP {e.code}: {url}") from e
            if e.code == 404:
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 1:
                time.sleep(2); continue
            if soft:
                raise VerificationUnknown(f"błąd sieci: {url} ({e})") from e
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _expect_dict(d, what):
    if not isinstance(d, dict):
        sys.exit(f"BŁĄD: nie udało się zweryfikować {what}, ponieważ API SAOS zwróciło "
                 "nieoczekiwaną odpowiedź. Spróbuj ponownie za chwilę.")
    return d


def _expect_search(d, what):
    d = _expect_dict(d, what)
    info = d.get("info")
    if not isinstance(d.get("items"), list) or not isinstance(info, dict) \
            or "totalResults" not in info:
        sys.exit(f"BŁĄD: nie udało się zweryfikować {what}, ponieważ odpowiedź API SAOS "
                 "nie zawiera kompletnego wyniku wyszukiwania. Spróbuj ponownie za chwilę.")
    return d


def _expect_judgment(d, judgment_id):
    d = _expect_dict(d, f"orzeczenia {judgment_id}")
    data = d.get("data", d)
    if not isinstance(data, dict) or data.get("id") is None:
        sys.exit(f"BŁĄD: nie udało się zweryfikować orzeczenia {judgment_id}, ponieważ odpowiedź "
                 "API SAOS nie zawiera danych orzeczenia. Spróbuj ponownie za chwilę.")
    return d


_INDEKS_GORNY = str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
_INLINE = ("sup", "sub", "em", "strong", "b", "i", "u", "a", "span", "font")


def _indeks_gorny(tekst):
    """Treść <sup> → indeks górny w Unicode, w jednej linii: 'art. 556<sup>1</sup>' → 'art. 556¹'.

    W tekstach sądów powszechnych SAOS wstawia do <sup> łamanie linii i komentarz
    (`<sup>\n<!-- -->1</sup>`), co bez tej obsługi rozbija numer przepisu na dwie linie.
    """
    rdzen = tekst.strip()
    koncowka = " " if tekst and tekst[-1].isspace() and rdzen else ""
    return rdzen.translate(_INDEKS_GORNY) + koncowka


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip, self.sup, self.po_inline = [], 0, None, False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.out.append("\n")
        if tag == "sup" and not self.skip:
            self.sup = []
        self.po_inline = tag in _INLINE

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "sup" and self.sup is not None:
            self.out.append(_indeks_gorny("".join(self.sup)))
            self.sup = None
        self.po_inline = False

    def handle_data(self, data):
        if self.skip:
            return
        if self.po_inline:
            # SAOS wstawia „\n<!-- -->” tuż za znacznikiem inline (<em>, <sup>, <a>) — to artefakt,
            # nie koniec akapitu
            data = data.lstrip("\n")
            self.po_inline = False
        if self.sup is not None:
            self.sup.append(data)
        else:
            self.out.append(data)


def html_to_text(html):
    """HTML/snippet → czysty tekst. SAOS używa m.in. <em> w podświetleniach i twardych spacji (NBSP);
    <sup> (indeksy górne numerów przepisów) trafia do tekstu jako cyfry w indeksie górnym (art. 556¹)."""
    p = _Stripper()
    p.feed(html)
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Okna tekstu wokół wystąpień frazy (bez rozróżniania wielkości liter).

    Uzasadnienia bywają wielostronicowe i nie mają jednostek redakcyjnych jak ustawy —
    dlatego pokazujemy kontekst wokół trafień, a nie całe „artykuły".
    """
    low, f = txt.lower(), fraza.lower().strip()
    if not f:
        return []
    spans, p = [], low.find(f)
    while p != -1 and len(spans) < maks:
        start = _do_granicy(txt, max(0, p - okno // 3), p, poczatek=True)
        end = _do_granicy(txt, min(len(txt), p + len(f) + okno), p + len(f), poczatek=False)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        p = low.find(f, p + len(f))
    return spans


# koniec zdania: kropka/!/? + biały znak + wielka litera; NIE po skrótach prawniczych („art. 535",
# „ust. 2", „poz. 93", „2016 r. Sąd", inicjałach „M. B." ani po „k.c."/„k.p.c.")
_KONIEC_ZDANIA = re.compile(
    r"(?<![A-ZĄĆĘŁŃÓŚŹŻ])(?<!\.[a-z])(?<!\bart)(?<!\bust)(?<!\bpoz)(?<!\bzob)(?<!\bnp)(?<!\bpor)"
    r"(?<!\bsygn)(?<!\btj)(?<!\bpkt)(?<!\blit)(?<!\bNr)(?<!\bnr)(?<!\br)(?<!\bs)(?<!\bt)(?<!\bz)"
    r"(?<!\bm\.in)(?<!\bitp)(?<!\bitd)(?<!\bwyd)(?<!\bred)(?<!\bop)(?<!\bcyt)"
    r"[.!?…]\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ„(])")


def _do_granicy(txt, poz, granica_trafienia, poczatek, margines=120):
    """Dosuwa brzeg okna do granicy zdania (w zasięgu `margines`), a w braku — do granicy słowa.

    Okno nigdy nie wchodzi w trafienie (`granica_trafienia`); gdy w tekście nie ma żadnej
    spacji w zasięgu (np. sztuczny ciąg znaków), zostaje surowy offset.
    """
    if poczatek:
        if poz == 0:
            return 0
        lo = max(0, poz - margines)
        zdania = [m.end() for m in _KONIEC_ZDANIA.finditer(txt, lo, min(len(txt), poz + margines))
                  if m.end() <= granica_trafienia]
        if zdania:
            return min(zdania, key=lambda e: abs(e - poz))
        nl = txt.rfind("\n", lo, poz)
        if nl != -1:
            return nl + 1
        if txt[poz - 1].isspace():
            return poz
        sp = txt.find(" ", poz, granica_trafienia)
        return sp + 1 if sp != -1 else poz
    if poz >= len(txt):
        return len(txt)
    hi = min(len(txt), poz + margines)
    zdania = [m.start() + 1 for m in _KONIEC_ZDANIA.finditer(txt, max(0, poz - margines), hi)
              if m.start() + 1 >= granica_trafienia]
    if zdania:
        return min(zdania, key=lambda e: abs(e - poz))
    nl = txt.find("\n", poz, hi)
    if nl != -1:
        return nl
    if txt[poz].isspace() or txt[poz - 1].isspace():
        return poz
    sp = txt.rfind(" ", granica_trafienia, poz)
    return sp if sp != -1 else poz


def _court_type(s):
    if not s:
        return None
    ct = SADY.get(s.strip().upper())
    if not ct:
        sys.exit(f"Nieznany sąd: {s!r}. Użyj: SN, TK, powszechne, admin, KIO.")
    return ct


def _jtype(s):
    if not s:
        return None
    t = TYPY.get(s.strip().upper())
    if not t:
        sys.exit(f"Nieznany typ orzeczenia: {s!r}. Użyj: wyrok, postanowienie, uchwala, zarzadzenie, uzasadnienie.")
    return t


def _court_label(it):
    """Czytelny opis sądu/izby z pola division (zależny od rodzaju sądu)."""
    div = it.get("division") or {}
    court = div.get("court") or {}
    if court.get("name"):  # sądy powszechne: nazwa sądu + wydział
        parts = [court["name"]]
        if div.get("name"):
            parts.append(div["name"])
        return ", ".join(parts)
    # SN: izby — w wyszukiwaniu pole 'chambers' (lista), w szczególe 'chamber' (jeden)
    ch = div.get("chamber") or {}
    chambers = div.get("chambers") or ([ch] if ch.get("name") else [])
    names = [c.get("name") for c in chambers if c.get("name")]
    if names:
        return ", ".join(names)
    return div.get("name") or ""


def _case_numbers(it):
    return ", ".join(c.get("caseNumber", "") for c in (it.get("courtCases") or []) if c.get("caseNumber"))


def _judges(it, maks=3):
    js = [j.get("name") for j in (it.get("judges") or []) if j.get("name")]
    extra = f" (+{len(js) - maks})" if len(js) > maks else ""
    return ", ".join(js[:maks]) + extra


def _forma(it):
    """Typ orzeczenia po polsku (wyrok / postanowienie / uchwała / zarządzenie / uzasadnienie).

    API zwraca surowy enum `judgmentType` (SENTENCE…) dla każdego sądu; `judgmentForm` (w /search
    string 'wyrok SN', w /judgments/{id} obiekt {'name': …}) ma tylko SN i bywa dokładniejszy
    ('uchwała składu 7 sędziów SN') — wtedy dopisujemy go w nawiasie.
    """
    typ = it.get("judgmentType") or ""
    typ_pl = TYPY_PL.get(typ, typ)
    f = it.get("judgmentForm")
    if isinstance(f, dict):
        f = f.get("name")
    if f and f.strip().lower() not in ("", typ_pl.lower()):
        return f"{typ_pl} ({f.strip()})" if typ_pl else f.strip()
    return typ_pl


# Wpisy „Powołane przepisy" to automatyczna ekstrakcja SAOS — bywa uszkodzona: 'Nr 0' (brak numeru
# Dz.U.), spłaszczone indeksy górne sklejone w jeden numer (art. 479³⁶–479⁴⁵ → 'art. 4793647945'),
# sklejone słowa ('art. 2oraz', 'ust. atakże'), 'art. n'.
_USZKODZONY_WPIS = (
    (re.compile(r"\bart\. ?\d{5,}"), "sklejony numer artykułu (spłaszczone indeksy górne)"),
    (re.compile(r"\b(?:art|ust|pkt|lit)\. ?\d+[a-ząćęłńóśźż]{2,}|\b(?:ust|pkt)\. ?[a-ząćęłńóśźż]{2,}\b"),
     "sklejone słowa"),
    (re.compile(r"\bart\. n\b"), "„art. n” zamiast numeru"),
    (re.compile(r"\bNr 0\b"), "„Nr 0” zamiast publikatora"),
)


def _wpis_przepisu(r):
    """Tekst wpisu z referencedRegulations + ostrzeżenie (z powodem), gdy wpis wygląda na uszkodzony."""
    tekst = r.get("text") or r.get("journalTitle", "") or ""
    powody = [opis for wzor, opis in _USZKODZONY_WPIS if wzor.search(tekst)]
    if powody:
        tekst += f"   (wpis SAOS prawdopodobnie uszkodzony: {', '.join(powody)} — zweryfikuj w treści orzeczenia)"
    return tekst


UWAGA_INDEKSY = ("UWAGA: SAOS spłaszcza indeksy górne w tekstach SN/TK/KIO (art. 417¹ → 4171) — "
                 "numerację przepisów weryfikuj w źródle")


def _zrodla_urzedowe(data):
    """Linki do weryfikacji w źródle urzędowym.

    `source.judgmentUrl` z SAOS dla SN/TK/KIO jest martwy albo ogólny (sprawdzone 2026-08-23:
    sn.pl/…/Baza_orzeczen → 404, otk.trybunal.gov.pl i ftp.uzp.gov.pl nieosiągalne), więc NIE
    jest ścieżką weryfikacji. Dla SN działa wzorzec adresu PDF (zweryfikowany curl -I na
    II KK 56/16, I CSK 364/15, III CZP 17/15): sygnatura z '/'→'-' i spacjami %20; dla TK i KIO
    wyszukiwarki OTK / UZP. Linki sądów powszechnych (apiorzeczenia.*.sa.gov.pl) działają.
    """
    ct = data.get("courtType", "")
    cn = next((c.get("caseNumber") for c in (data.get("courtCases") or []) if c.get("caseNumber")), "")
    src = (data.get("source") or {}).get("judgmentUrl")
    linie = []
    if ct == "SUPREME" and cn:
        pdf = urllib.parse.quote(cn.replace("/", "-"), safe="")
        linie.append(f"  Źródło urzędowe: https://www.sn.pl/sites/orzecznictwo/Orzeczenia3/{pdf}.pdf"
                     "  (sprawdź — wzorzec adresu; bywa też z przyrostkiem -1.pdf dla uzasadnienia)")
    elif ct == "CONSTITUTIONAL_TRIBUNAL":
        linie.append(f"  Źródło urzędowe: https://ipo.trybunal.gov.pl/ipo/  (wyszukaj sygnaturę {cn or '?'})")
    elif ct == "NATIONAL_APPEAL_CHAMBER":
        linie.append(f"  Źródło urzędowe: https://orzeczenia.uzp.gov.pl/Home/Search  (wyszukaj sygnaturę {cn or '?'})")
    if src:
        if ct in ZASIEG:
            linie.append(f"  Link z metadanych SAOS (dla SN/TK/KIO zwykle martwy lub ogólny — nie służy "
                         f"do weryfikacji): {src}")
        else:
            linie.append(f"  Źródło oryginalne: {src}")
    return linie


_SYGN_TK = re.compile(r"^(?:K|Kp|Kpt|P|Pp|SK|U|Tw|Ts|Tp|S|W|M)\s*\d+/\d{2}$", re.I)
_SYGN_KIO = re.compile(r"^KIO(?:/(?:KU|KD|UZP|W))?\s*\d+/\d{2}$", re.I)
# symbole SN (repertoria): CSK/CZP/CZ/CO/CNP/CSKP, KK/KZP/KZ/KO/KSK, PK/PZP/PO, UK/UZP/UO, BU/BP, SNO/SDI…
_SYGN_SN = re.compile(r"^(?:I|II|III|IV|V|VI|VII)\s+(?:C[A-Z]{1,3}|K[A-Z]{1,3}|P[A-Z]{1,3}|U[A-Z]{1,3}|"
                      r"B[A-Z]{1,2}|S[DN][A-Z]?)\s*\d+/\d{2,4}$")


def _sady_dla_sygnatury(sig):
    """Zgaduje zamknięty zbiór po kształcie sygnatury (KIO…, K 35/15 → TK, III CZP 81/16 → SN)."""
    sig = sig.strip()
    if _SYGN_KIO.match(sig):
        return ["NATIONAL_APPEAL_CHAMBER"]
    if _SYGN_TK.match(sig):
        return ["CONSTITUTIONAL_TRIBUNAL"]
    if _SYGN_SN.match(sig):
        return ["SUPREME"]
    return list(ZASIEG)


def _rok_sygnatury(sig):
    m = re.search(r"/(\d{2,4})\s*$", sig.strip())
    if not m:
        return None
    r = int(m.group(1))
    return r if r >= 1000 else (1900 + r if r >= 50 else 2000 + r)


def _wyjasnienie_zera_sygnatury(sig):
    """Dlaczego SAOS może nie mieć tej sygnatury — z granicą zbioru podaną do DNIA.

    Sygnatura z rocznika granicy (III CZP 81/16 z 8.12.2016 przy końcu zbioru SN 22.06.2016)
    NIE może dostać „nie ma" — tylko „może być późniejsze niż koniec zbioru".
    """
    rok = _rok_sygnatury(sig)
    sady = _sady_dla_sygnatury(sig)
    rozpoznany = len(sady) == 1
    linie = []
    if not rozpoznany:
        linie.append("sąd powszechny (sygnatura nierozpoznana jako SN/TK/KIO): zbiór zasilany na bieżąco, "
                     "ale z opóźnieniem i lukami — sprawdź portal orzeczeń sądów powszechnych "
                     "(orzeczenia.ms.gov.pl)")
    for ct in sady:
        granica = ZASIEG[ct][0]
        nazwa, gdzie = CT_PL[ct], ZASIEG[ct][1]
        if not rozpoznany:
            nazwa = "jeśli to " + nazwa
        if rok is None:
            linie.append(f"{nazwa}: zbiór kończy się na {_data_pl(granica)} — {gdzie}")
        elif rok > int(granica[:4]):
            linie.append(f"{nazwa}: zbiór kończy się na {_data_pl(granica)}, a sygnatura jest z {rok} r. — "
                         f"SAOS tego orzeczenia NIE MA z założenia; {gdzie}")
        elif rok == int(granica[:4]):
            linie.append(f"{nazwa}: zbiór kończy się na {_data_pl(granica)}, a sygnatura jest z {rok} r. — "
                         f"orzeczenie może być późniejsze niż koniec zbioru {_data_pl(granica)}; {gdzie}")
        else:
            linie.append(f"{nazwa}: rocznik {rok} mieści się w zbiorze (do {_data_pl(granica)}), "
                         f"ale SAOS to agregat z lukami — {gdzie}")
    return linie


def _wiersz(it):
    """Jedna pozycja listy wyników (id, sygnatura, sąd, data, forma, hasła, snippet)."""
    cn = _case_numbers(it) or "—"
    ctype = CT_PL.get(it.get("courtType", ""), it.get("courtType", ""))
    court = _court_label(it)
    forma = _forma(it)
    print(f"  [{it.get('id')}]  {cn}   ({it.get('judgmentDate', '?')})")
    drugi = f"    {ctype}" + (f" — {court}" if court else "")
    if forma:
        drugi += f"  · {forma}"
    print(drugi)
    kw = it.get("keywords") or []
    if kw:
        print(f"    hasła: {', '.join(kw)}")
    snip = html_to_text(it.get("textContent") or "")
    if snip:
        snip = snip.replace("\n", " ")
        print(f"    …{snip[:200].strip()}…")
    print(f"    → orzeczenie {it.get('id')}")
    print()


def cmd_szukaj(a):
    ct = _court_type(a.sad)
    _sprawdz_zasieg_strict(a, ct)
    params = {
        "all": a.fraza,
        "caseNumber": a.sygnatura,
        "referencedRegulation": a.przepis,
        "judgeName": a.sedzia,
        "keywords": a.haslo,
        "courtType": ct,
        "judgmentTypes": _jtype(a.typ),
        "judgmentDateFrom": _norma_data(a.od),
        "judgmentDateTo": _norma_data(a.do, koniec=True),
        "pageSize": max(1, min(a.limit, 100)),
        "pageNumber": max(0, a.strona),
        "sortingField": "JUDGMENT_DATE",
        "sortingDirection": "DESC",
    }
    kryteria = any(params.get(k) for k in
                   ("all", "caseNumber", "referencedRegulation", "judgeName", "keywords",
                    "courtType", "judgmentTypes", "judgmentDateFrom", "judgmentDateTo"))
    if not kryteria:
        sys.exit("Podaj kryterium: frazę albo --sad / --sygnatura / --przepis / --sedzia / --haslo / --typ / zakres dat.")
    d = _get("/search/judgments", params)
    d = _expect_search(d, "wyników wyszukiwania")
    items = d.get("items", [])
    total = (d.get("info") or {}).get("totalResults", "?")
    if not items:
        # zero trafień = komunikat + kod ≠ 0, TAKŻE z --json (pusty JSON wyglądałby jak
        # „sprawdzone, nic nie ma") — z wyjaśnieniem granicy zamkniętego zbioru, gdy dotyczy
        linie = [f"Brak trafień w SAOS dla podanych kryteriów (totalResults: {total})."]
        if ct == "ADMINISTRATIVE":
            linie.append("Sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — orzecznictwo "
                         "administracyjne pobieraj skillem prawo-pl-cbosa (scripts/cbosa.py, baza CBOSA).")
        if ct in ZASIEG:
            granica, potwierdzona = _granica_zbioru(ct)
            linie.append(_ostrzezenie_zasiegu(ct, a.od, a.do, granica, potwierdzona))
        if a.przepis:
            linie.append("--przepis to luźne dopasowanie pełnotekstowe w polu powołanych przepisów "
                         "(lista SAOS bywa niepełna) — spróbuj też samą frazą albo --haslo.")
        linie.append("SAOS to baza wtórna (agregat z lukami) — brak trafień tu nie dowodzi braku orzecznictwa.")
        sys.exit("\n".join(linie))
    granica = _ostrzezenie_zasiegu(ct, a.od, a.do, *(_granica_znana(ct, items) if ct in ZASIEG else ()))
    if a.json:
        if granica:
            print(granica, file=sys.stderr)
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Znaleziono: {total}  (pokazuję {len(items)}, strona {params['pageNumber']}, po {params['pageSize']})\n")
    if ct == "ADMINISTRATIVE":
        print("UWAGA: sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — orzecznictwo "
              "administracyjne pobieraj skillem prawo-pl-cbosa (scripts/cbosa.py, baza CBOSA).\n")
    if granica:
        print(granica + "\n")
    if a.przepis:
        print(f"UWAGA: --przepis {a.przepis!r} to LUŹNE dopasowanie pełnotekstowe w polu powołanych "
              "przepisów — „art. 415” trafia art. 415 k.c., k.p.c. i k.p.k. jednakowo, a lista powołanych "
              "przepisów w SAOS bywa niepełna. Dopisz nazwę aktu (np. \"Kodeks cywilny art. 415\") "
              "i sprawdź w 'orzeczenie <id>', którego aktu dotyczy trafienie.\n")
    for it in items:
        _wiersz(it)
    if items:
        print(f"Pełna treść: orzeczenie <id>  (np. orzeczenie {items[0].get('id')})")
        if isinstance(total, int) and total > len(items):
            print(f"Kolejna strona: --strona {params['pageNumber'] + 1}")


def cmd_orzeczenie(a):
    d = _get(f"/judgments/{a.id}")
    d = _expect_judgment(d, a.id)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    data = d.get("data", d)
    cn = _case_numbers(data) or "—"
    ctype = CT_PL.get(data.get("courtType", ""), data.get("courtType", ""))
    court = _court_label(data)
    print(f"# Orzeczenie [{data.get('id')}]  {cn}")
    print(f"  Sąd:    {ctype}" + (f" — {court}" if court else ""))
    print(f"  Data:   {data.get('judgmentDate', '?')}    typ: {_forma(data)}")
    js = _judges(data, maks=12)
    if js:
        print(f"  Skład:  {js}")
    kw = data.get("keywords") or []
    if kw:
        print(f"  Hasła:  {', '.join(kw)}")
    for linia in _zrodla_urzedowe(data):
        print(linia)
    print(f"  SAOS:   https://www.saos.org.pl/judgments/{data.get('id')}")
    ct = data.get("courtType", "")
    if ct in ZASIEG:
        print(f"  {UWAGA_INDEKSY}")

    reg = data.get("referencedRegulations") or []
    if reg:
        print(f"\n## Powołane przepisy ({len(reg)}) — lista z SAOS (automatyczna ekstrakcja) — bywa niepełna; "
              "przepisy rozstrzygnięcia zobacz w sentencji")
        for r in reg[:40]:
            print(f"  - {_wpis_przepisu(r)}")
        if len(reg) > 40:
            print(f"  … (+{len(reg) - 40}; pełna lista: --json)")

    rcc = data.get("referencedCourtCases") or []
    if rcc:
        print(f"\n## Powołane orzeczenia ({len(rcc)})")
        for r in rcc[:40]:
            ids = r.get("judgmentIds") or []
            hint = f"   → orzeczenie {ids[0]}" if ids else ""
            print(f"  - {r.get('caseNumber', '?')}{hint}")
        if len(rcc) > 40:
            print(f"  … (+{len(rcc) - 40}; pełna lista: --json)")

    summary = html_to_text(data.get("summary") or "")
    if summary:
        print(f"\n## Teza / streszczenie\n{summary}")

    txt = html_to_text(data.get("textContent") or "")
    print(f"\n## Treść uzasadnienia ({len(txt)} znaków)")
    if ct in ZASIEG:
        print(f"({UWAGA_INDEKSY})")
    if not txt:
        print("(brak treści w API — otwórz źródło urzędowe wyżej)")
        return
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w treści ({len(txt)} znaków). Spróbuj inną frazą.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            # okna są dosunięte do granic zdań/słów; „…" oznacza, że tekst ciągnie się dalej
            print(("…" if s > 0 else "") + txt[s:e].strip() + ("…" if e < len(txt) else ""))
        print(f"\n(okna: {len(spans)} — pominięto resztę; pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długie uzasadnienie {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def cmd_sygnatura(a):
    sig = " ".join(a.sygnatura).strip()
    d = _get("/search/judgments", {"caseNumber": sig, "pageSize": 20, "pageNumber": 0,
                                   "sortingField": "JUDGMENT_DATE", "sortingDirection": "DESC"})
    d = _expect_search(d, f"orzeczenia o sygnaturze {sig!r}")
    items = d.get("items", [])
    total = (d.get("info") or {}).get("totalResults", 0)
    if not items:
        wyjasnienie = "\n".join("  - " + w for w in _wyjasnienie_zera_sygnatury(sig))
        sys.exit(f"Nie znaleziono orzeczenia o sygnaturze {sig!r} w SAOS.\n"
                 "SAOS to baza wtórna (nie ma wszystkiego) — sprawdź też portal właściwego sądu; "
                 "sygnatury sądów administracyjnych (NSA/WSA) szukaj skillem prawo-pl-cbosa.\n"
                 "Granice zamkniętych zbiorów (do DNIA; sądy powszechne są na bieżąco):\n" + wyjasnienie)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Sygnatura {sig!r}: dopasowań {total}\n")
    for it in items:
        cn = _case_numbers(it)
        ctype = CT_PL.get(it.get("courtType", ""), it.get("courtType", ""))
        forma = _forma(it)
        print(f"  [{it.get('id')}]  {cn}  · {ctype}  ({it.get('judgmentDate', '?')})  {forma}".rstrip())
    print(f"\nPełna treść: orzeczenie {items[0].get('id')}")


def main():
    ap = argparse.ArgumentParser(description="API SAOS (read-only). Baza orzecznictwa polskiego: SN/TK/sądy powszechne/KIO.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    ap.add_argument("--strict", action="store_true",
                    help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None)
    s.add_argument("--sad", help="SN | TK | powszechne | admin | KIO")
    s.add_argument("--sygnatura", help="numer sprawy (caseNumber)")
    s.add_argument("--przepis", help="powołany przepis/akt (referencedRegulation), np. \"Kodeks cywilny\" albo \"art. 299\"")
    s.add_argument("--sedzia", help="nazwisko sędziego (judgeName)")
    s.add_argument("--haslo", help="hasło tematyczne (keywords)")
    s.add_argument("--typ", help="wyrok | postanowienie | uchwala | zarzadzenie | uzasadnienie")
    s.add_argument("--od", help="data orzeczenia od (RRRR-MM-DD)")
    s.add_argument("--do", help="data orzeczenia do (RRRR-MM-DD)")
    s.add_argument("--limit", type=int, default=10, help="ile wyników (1–100)")
    s.add_argument("--strona", type=int, default=0, help="numer strony (od 0)")
    s.set_defaults(func=cmd_szukaj)

    o = sub.add_parser("orzeczenie")
    o.add_argument("id")
    o.add_argument("--fragment", help='wytnij okna wokół frazy w uzasadnieniu (np. "rękojmia")')
    o.set_defaults(func=cmd_orzeczenie)

    sy = sub.add_parser("sygnatura")
    sy.add_argument("sygnatura", nargs="+")
    sy.set_defaults(func=cmd_sygnatura)

    # Flagi globalne działają też PO komendzie (modele piszą je właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności")

    a = ap.parse_args()
    try:
        a.func(a)
    except VerificationUnknown as e:
        sys.exit(f"BŁĄD: nie udało się zweryfikować danych w SAOS ({e}). "
                 "Spróbuj ponownie za chwilę.")


if __name__ == "__main__":
    main()
