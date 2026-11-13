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
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania; ostrzeżenia idą wtedy na stderr)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności)
Kody wyjścia: 0 = wynik; 1 = zweryfikowany brak (zero trafień, frazy nie ma w treści);
              2 = wszystko inne (sieć, przerwa techniczna, zły argument, nieistniejące id, --strict…)
"""
import sys, json, re, time, datetime, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.6.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://www.saos.org.pl/api"
CONTENT_HOSTS = ("saos.org.pl",)


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


class BrakWynikow(SystemExit):
    """ZWERYFIKOWANY brak wyników: zapytanie poprawne, SAOS odpowiedział, zero trafień (albo frazy nie
    ma w pobranym tekście). main() kończy go kodem 1; każde inne zakończenie z komunikatem — kodem 2.

    Dlaczego osobna klasa: do wersji 2.3.0 każde sys.exit("tekst") dawało kod 1, więc awaria sieci,
    przerwa techniczna, zły argument, nieistniejące id i blokada --strict wyglądały dla skryptu
    tak samo jak „nic nie ma” (sprawdzone 2026-11-13: `orzeczenie 999999999` → HTTP 404 → kod 1).
    Ujednolicone z orzeczenia_ms.py: 0 = wynik, 1 = zweryfikowany brak, 2 = wszystko inne."""


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
    "SUPREME": ("2016-06-22", "nowsze orzeczenia SN: wyszukiwarka orzeczeń SN "
                              "(https://www.sn.pl/pl/wyszukiwarka-orzeczen)"),
    "CONSTITUTIONAL_TRIBUNAL": ("2015-12-09", "nowsze orzeczenia TK: OTK (ipo.trybunal.gov.pl)"),
    "NATIONAL_APPEAL_CHAMBER": ("2018-09-06", "nowsze orzeczenia KIO: UZP "
                                              "(orzeczenia.uzp.gov.pl)"),
}
_granice_na_zywo = {}  # courtType -> data najnowszego orzeczenia wg API (cache tylko w procesie)


def _dzis():
    """Dzisiejsza data ISO (RRRR-MM-DD). Osobna funkcja, żeby testy mogły ją podmienić."""
    return datetime.date.today().isoformat()


def _z_przyszlosci(iso):
    """Czy data orzeczenia jest późniejsza niż dziś — czyli na pewno błędna.

    SAOS ma rekordy z niemożliwymi datami (sprawdzone 2026-11-13: I ACa 772/13 → 3013-12-04,
    II K 84/16 → 2101-04-14, I ACa 1027/23 → 2026-12-16); sortowanie po dacie malejąco wynosi je
    na początek listy. Brak daty albo inny format to nie „przyszłość"."""
    iso = str(iso or "")
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", iso)) and iso > _dzis()


UWAGA_PRZYSZLOSC = "data z przyszłości — błąd w danych SAOS, sprawdź datę w treści"


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
        rok, mies = int(d[:4]), int(d[5:7])
        if not 1 <= mies <= 12:
            sys.exit(f"BŁĄD: data {d!r} ma zły miesiąc — użyj RRRR-MM-DD (albo RRRR / RRRR-MM).")
        if not koniec:
            return f"{d}-01"
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

    Zapytanie ma górną granicę „dziś": SAOS ma rekordy z datą z przyszłości (2026-11-13: sądy
    powszechne 3013-12-04, 2101-04-14). Taki rekord na szczycie zbioru SN/TK/KIO przesunąłby granicę
    w przyszłość i wyłączył ostrzeżenie oraz blokadę --strict — najgroźniejszy fałszywy negatyw.
    Gdyby API zignorowało filtr, rekord z przyszłości i tak odrzucamy (granica zostaje znana).
    """
    znana = ZASIEG[ct][0]
    if ct in _granice_na_zywo:
        zywa = _granice_na_zywo[ct]
        return (max(znana, zywa), True) if zywa else (znana, False)
    zywa = None
    try:
        d = _get("/search/judgments", {"courtType": ct, "judgmentDateTo": _dzis(), "pageSize": 1,
                                       "pageNumber": 0, "sortingField": "JUDGMENT_DATE",
                                       "sortingDirection": "DESC"}, soft=True)
        items = d.get("items") if isinstance(d, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict) \
                and re.match(r"^\d{4}-\d{2}-\d{2}$", str(items[0].get("judgmentDate", ""))) \
                and not _z_przyszlosci(items[0]["judgmentDate"]):
            zywa = items[0]["judgmentDate"]
    except VerificationUnknown:
        zywa = None
    _granice_na_zywo[ct] = zywa
    return (max(znana, zywa), True) if zywa else (znana, False)


def _granica_znana(ct, items=()):
    """Granica zbioru bez zapytania: cache z potwierdzenia na żywo albo stała; a jeśli wśród wyników
    (posortowanych po dacie malejąco) jest orzeczenie PÓŹNIEJSZE niż granica, to ona się przesuwa —
    wynik wyszukiwania sam dowodzi, że SAOS wznowił zasilanie. Data z przyszłości niczego nie
    dowodzi (błąd w danych SAOS, zob. _z_przyszlosci) i granicy nie przesuwa."""
    granica, potwierdzona = ZASIEG[ct][0], False
    zywa = _granice_na_zywo.get(ct)
    if zywa:
        granica, potwierdzona = max(granica, zywa), True
    daty = [str(it.get("judgmentDate") or "") for it in items if isinstance(it, dict)]
    daty = [d for d in daty if re.match(r"^\d{4}-\d{2}-\d{2}$", d) and d > granica
            and not _z_przyszlosci(d)]
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


# Przerwa techniczna nie mówi nic o istnieniu orzeczeń — zamiast samego „ponów" wskazujemy źródła,
# które działają niezależnie od SAOS (kiedy przerwy występują, nie jest zweryfikowane).
UWAGA_PRZERWA = (
    "BŁĄD: SAOS ma przerwę techniczną (serwis chwilowo niedostępny) — spróbuj ponownie później. "
    "To NIE jest brak orzeczeń. W czasie przerwy szukaj u źródła: sądy powszechne — skill "
    "prawo-pl-orzeczenia-ms (Portal Orzeczeń MS); sądy administracyjne — skill prawo-pl-cbosa (CBOSA); "
    "SN — wyszukiwarka https://www.sn.pl/pl/wyszukiwarka-orzeczen (zbiór SN w SAOS i tak kończy się "
    "na 22.06.2016).")


def _get(path, params=None, soft=False, baza=BASE):
    """GET z jednym ponowieniem (limit 90 s na żądanie — /search/judgments bywa bardzo wolny).

    Zwraca dane, w tym pusty wynik jako VERIFIED_ABSENT. Przy soft=True błąd
    żądania ma osobny stan UNKNOWN (VerificationUnknown), nigdy None/pusty wynik.
    Przekroczenie czasu to „BŁĄD sieci" (kod 2) — nigdy fałszywe zero trafień.
    `baza` pozwala sięgnąć poza /api (słownik haseł: zob. _slownik_hasel).
    """
    url = baza + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": f"saos-skill/{__version__}", "Accept": "application/json"})
    raw = None
    for attempt in (1, 2):
        try:
            with _opener.open(req, timeout=90) as r:
                ctype = r.headers.get("Content-Type", "")
                raw = r.read().decode("utf-8", "replace")
            # SAOS bywa w oknie serwisowym — zwraca stronę HTML zamiast JSON (HTTP 200). Sama fraza
            # nie wystarcza: „Przerwa techniczna" bywa treścią orzeczenia (np. KIO o przerwanej
            # aukcji) albo szukaną frazą — wtedy odpowiedź jest poprawnym JSON-em i musi przejść
            if "Przerwa techniczna" in raw and ("json" not in ctype.lower() or raw.lstrip().startswith("<")):
                if attempt == 1:
                    time.sleep(2); continue
                if soft:
                    raise VerificationUnknown("SAOS ma przerwę techniczną")
                sys.exit(UWAGA_PRZERWA)
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
        sys.exit(f"BŁĄD: nieznany sąd: {s!r}. Użyj: SN, TK, powszechne, admin, KIO.")
    return ct


def _jtype(s):
    if not s:
        return None
    t = TYPY.get(s.strip().upper())
    if not t:
        sys.exit(f"BŁĄD: nieznany typ orzeczenia: {s!r}. Użyj: wyrok, postanowienie, uchwala, zarzadzenie, uzasadnienie.")
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


def _klucz_sadu(it):
    """Tożsamość sądu trafienia: id/nazwa sądu powszechnego, a dla SN/TK/KIO — typ sądu."""
    court = (it.get("division") or {}).get("court") or {}
    return (it.get("courtType", ""), court.get("id") or court.get("name") or "")


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


SN_WYSZUKIWARKA = "https://www.sn.pl/pl/wyszukiwarka-orzeczen"
SN_PROXY = "https://www.sn.pl/pl/index.php?option=com_ajax&plugin=snproxy&format=json"
SN_TIMEOUT = 7  # s; dawniej 20 — przy filtrze antybotowym sn.pl (2026-11) link zwykle i tak się nie uda


def _norm_sygn(s):
    return re.sub(r"\s+", " ", str(s or "")).strip().upper()


def _sn_id(sygnatura, data):
    """Identyfikator orzeczenia w wyszukiwarce SN (www.sn.pl/pl/wyszukiwarka-orzeczen).

    Strona wyszukiwarki pobiera wyniki z JSON-owego proxy sn.pl (`task=searchOrzeczenia`), a stronę
    orzeczenia otwiera jako `?orzeczenie=<id>` — sprawdzone na żywo 2026-10-05 (I UK 328/10 →
    FV3ISJcBZvGrB8P_g845). Proxy dopasowuje sygnaturę fragmentem („328/10” zwraca też IV CSK 328/10),
    a jedna sygnatura może mieć kilka orzeczeń — dlatego wymagamy DOKŁADNEJ sygnatury i daty.
    Dane pomocnicze: każda awaria, brak albo niejednoznaczność → None (bez wpływu na --strict).

    Od października 2026 sn.pl stoi za filtrem antybotowym (Incapsula): 2026-11-13 to samo żądanie
    dostało HTTP 200 ze stroną wyzwania (text/html, `_Incapsula_Resource`) zamiast JSON-a. Wtedy
    json.loads zawodzi → None → link do wyszukiwarki z instrukcją „wpisz sygnaturę" — to dziś
    zwykły wynik. Filtra NIE obchodzimy (żadnych nagłówków przeglądarki, ciasteczek, headless);
    próbę zostawiamy, bo z innej sieci albo po zmianie reguł filtra proxy może odpowiedzieć.
    Limit czasu jest krótki (SN_TIMEOUT): pomocniczy link nie może opóźniać całej odpowiedzi."""
    if not sygnatura or not data:
        return None
    url = SN_PROXY + "&" + urllib.parse.urlencode(
        {"task": "searchOrzeczenia", "sygnatura": sygnatura, "strona": 1, "rozmiar_strony": 25})
    req = urllib.request.Request(url, headers={"User-Agent": f"saos-skill/{__version__}",
                                               "Accept": "application/json"})
    try:
        with _opener.open(req, timeout=SN_TIMEOUT) as r:
            odp = json.loads(r.read().decode("utf-8", "replace"))
        lista = odp["data"][0]["data"]
    except Exception:  # noqa: BLE001 — link pomocniczy; awaria = link do wyszukiwarki z instrukcją
        return None
    if not isinstance(lista, list):
        return None
    ids = {it.get("id") for it in lista
           if isinstance(it, dict) and it.get("id")
           and _norm_sygn(it.get("sygnatura_sprawy") or it.get("sygnatura")) == _norm_sygn(sygnatura)
           and str(it.get("data_wydania") or "")[:10] == data[:10]}
    return ids.pop() if len(ids) == 1 else None


def _zrodla_urzedowe(data):
    """Linki do weryfikacji w źródle urzędowym.

    `source.judgmentUrl` z SAOS dla SN/TK/KIO jest martwy albo ogólny (sprawdzone 2026-08-23:
    sn.pl/…/Baza_orzeczen → 404, otk.trybunal.gov.pl i ftp.uzp.gov.pl nieosiągalne), więc NIE
    jest ścieżką weryfikacji. Dawny wzorzec PDF SN (sites/orzecznictwo/Orzeczenia3/<sygn>.pdf)
    przekierowuje obecnie na sn.pl/404.html (5/5 w próbie 2026-10-05) — SN ma nową wyszukiwarkę:
    link do strony orzeczenia (`?orzeczenie=<id>`, id z _sn_id), a gdy się nie uda — link do
    wyszukiwarki z instrukcją (od 2026-11 to zwykły wynik: proxy sn.pl odpowiada stroną filtra
    antybotowego). Dla TK i KIO wyszukiwarki OTK / UZP. Linki sądów powszechnych
    (apiorzeczenia.*.sa.gov.pl) działają.
    """
    ct = data.get("courtType", "")
    cn = next((c.get("caseNumber") for c in (data.get("courtCases") or []) if c.get("caseNumber")), "")
    src = (data.get("source") or {}).get("judgmentUrl")
    linie = []
    if ct == "SUPREME" and cn:
        data_orz = str(data.get("judgmentDate") or "")
        sn_id = _sn_id(cn, data_orz)
        if sn_id:
            linie.append(f"  Źródło urzędowe: {SN_WYSZUKIWARKA}?orzeczenie={urllib.parse.quote(sn_id, safe='')}"
                         "  (strona orzeczenia w wyszukiwarce SN: metryczka + treść PDF/HTML)")
        else:
            linie.append(f"  Źródło urzędowe: {SN_WYSZUKIWARKA}  (wpisz w pole „Sygnatura” {cn}, "
                         f"wybierz orzeczenie z datą {data_orz or '?'} → „Szczegóły”; bezpośredniego "
                         "linku nie udało się ustalić)")
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


# Sygnatura sądu powszechnego: numer wydziału rzymski + repertorium z małą literą (C a, AC a, Ns, Kp —
# ale nie SN-owskie NSNc), jednoliterowe (C, K, P, U, W) albo G…/R… (GC, GNc, RC). Sprawdzane PO
# wzorcach SN/TK/KIO, więc „III CZP 81/16” i „IV CO 1/15” zostają SN.
_SYGN_POWSZECHNY = re.compile(r"^[IVXL]+\s+(?:(?!NSN)[A-Z][A-Za-z-]*[a-z][A-Za-z-]*|[A-Z]|[GR][A-Za-z-]*)"
                              r"\s*\d+/\d{2,4}$")


def _ksztalt_sadu_powszechnego(sig):
    sig = sig.strip()
    return bool(_SYGN_POWSZECHNY.match(sig)) and not (
        _SYGN_SN.match(sig) or _SYGN_TK.match(sig) or _SYGN_KIO.match(sig))


# Pomiar luk sądów powszechnych (2026-11-13): 40 orzeczeń z Portalu Orzeczeń MS (po 10 z publikacji
# 12.11.2026, 12–27.05.2026, 15–22.11.2024 i 15–18.11.2021), każde szukane w SAOS po sygnaturze
# i dopasowane po identyfikatorze portalu w source.judgmentUrl: w SAOS było 39 z 40, także wszystkie
# 10 opublikowanych w portalu dzień wcześniej. Brakujące (XXVIII C 22637/22) pochodzi z XXVIII Wydziału
# Cywilnego SO w Warszawie, którego SAOS w ogóle nie zna (słownik /dump/commonCourts kończy się na
# XXVII Wydziale; filtr ccDivisionName dla XXVIII → 0, dla XXVII → 1054). Luka jest więc wydziałowa,
# nie czasowa — dlatego komunikat zera daje gotową komendę portalu MS, a nie „poczekaj na import".
LUKA_SP = ("SAOS nie ma wszystkich orzeczeń sądów powszechnych: w próbie 13.11.2026 miał 39 z 40 "
           "orzeczeń z Portalu Orzeczeń MS (publikacje 2021–2026, także te sprzed jednego dnia), a brakujące "
           "pochodziło z wydziału, którego SAOS w ogóle nie zna (XXVIII Wydział Cywilny SO w Warszawie: "
           "nie ma go w słowniku sądów SAOS, a wyszukiwanie po nazwie wydziału daje 0 trafień)")


def _komenda_ms(*czesci):
    """Komenda skilla prawo-pl-orzeczenia-ms z argumentami w cudzysłowach powłoki (shlex)."""
    import shlex
    return "orzeczenia_ms.py " + " ".join(shlex.quote(str(c)) for c in czesci)


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
    komenda = _komenda_ms("sygnatura", *sig.split())
    if _ksztalt_sadu_powszechnego(sig):
        # „XXVIII C 22637/22” to sygnatura SO, nie SN/TK/KIO — linie „jeśli to TK: NIE MA z założenia”
        # były szumem i odsuwały jedyną użyteczną radę (2026-11-13)
        return [f"sąd powszechny (kształt sygnatury SA/SO/SR): {LUKA_SP}. Sprawdź u źródła skillem "
                f"prawo-pl-orzeczenia-ms: {komenda}"]
    sady = _sady_dla_sygnatury(sig)
    rozpoznany = len(sady) == 1
    linie = []
    if not rozpoznany:
        linie.append(f"sąd powszechny (sygnatura nierozpoznana jako SN/TK/KIO): {LUKA_SP}. Sprawdź u źródła "
                     f"skillem prawo-pl-orzeczenia-ms: {komenda}")
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


_MIESIACE = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia",
             "września", "października", "listopada", "grudnia")
_DATA_NAGLOWKA = re.compile(r"\bdnia\s+(\d{1,2})\s+(" + "|".join(_MIESIACE) + r")\s+(\d{4})", re.I)


def _data_z_naglowka(tekst, okno=400):
    """Pierwsza data „dnia 20 maja 2026” z początku treści → ISO (albo None).

    Dokument typu REASONS (uzasadnienie) ma w API datę sporządzenia uzasadnienia; nagłówek treści
    („Warszawa, dnia 20 maja 2026 r. Sygn. akt …”) zwykle podaje datę wyroku. To heurystyka —
    wynik oznaczamy jako do sprawdzenia."""
    m = _DATA_NAGLOWKA.search((tekst or "")[:okno])
    if not m:
        return None
    try:
        dzien, mies, rok = int(m.group(1)), _MIESIACE.index(m.group(2).lower()) + 1, int(m.group(3))
    except ValueError:
        return None
    if not 1 <= dzien <= 31:
        return None
    return f"{rok:04d}-{mies:02d}-{dzien:02d}"


PROBA_UZASADNIEN = ("w próbie 13.11.2026 z 22 uzasadnień sądów powszechnych 12 miało datę uzasadnienia, "
                    "6 — datę wyroku, 4 — nie do ustalenia")
_DATA_ID_MS = re.compile(r"_(\d{4}-\d{2}-\d{2})_\d{3}$")


def _data_dokumentu_ms(data):
    """Data dokumentu zapisana w identyfikatorze Portalu Orzeczeń MS (…_Uz_2021-11-09_001) albo None.

    To „Data orzeczenia” tego dokumentu w portalu MS — dla uzasadnienia: data uzasadnienia."""
    m = _DATA_ID_MS.search(_id_portalu_ms((data.get("source") or {}).get("judgmentUrl")) or "")
    return m.group(1) if m else None


def _uwaga_uzasadnienie(data, tekst=""):
    """Ostrzeżenie dla typu „uzasadnienie”: judgmentDate bywa datą uzasadnienia, a bywa datą wyroku.

    Do wersji 2.3.0 silnik twierdził zawsze „to data sporządzenia UZASADNIENIA”. Pomiar 2026-11-13
    (22 uzasadnienia sądów powszechnych porównane z Portalem Orzeczeń MS, rozstrzygane nagłówkiem treści
    i wyrokiem tej sprawy w SAOS): 12 — data uzasadnienia (wyrok sprawy ma wcześniejszą datę), 6 — data
    wyroku, 4 — nie do ustalenia. Z tych 6: w 5 SAOS podał datę WCZEŚNIEJSZĄ niż data dokumentu w portalu
    MS (VI Ka 468/21: „Warszawa, dnia 22 października 2021 r.”, portal MS: 9.11.2021; II C 1470/25 i
    II K 403/24: data wyroku tej sprawy w SAOS), a w 1 data równa portalowi jest też datą z nagłówka
    wyroku (I C 401/20: wyrok i uzasadnienie w jednym dokumencie). Rozstrzyga data w identyfikatorze
    portalu MS z source.judgmentUrl i nagłówek treści."""
    if data.get("judgmentType") != "REASONS":
        return None
    d = data.get("judgmentDate") or "?"
    z_ms = _data_dokumentu_ms(data)
    z_naglowka = _data_z_naglowka(tekst)
    if z_ms and re.match(r"^\d{4}-\d{2}-\d{2}$", d) and d < z_ms:
        linia = (f"UWAGA: typ „uzasadnienie” — portal orzeczeń MS datuje ten dokument uzasadnienia na "
                 f"{_data_pl(z_ms)}, a SAOS podaje {d}: tu {d} to najpewniej data WYROKU, nie uzasadnienia "
                 f"(SAOS bywa niespójny — {PROBA_UZASADNIEN}). Potwierdź datę wyroku w sentencji albo w wyroku "
                 "tej sprawy.")
        if z_naglowka == d:
            linia += f"\n          Nagłówek treści potwierdza: {_data_pl(z_naglowka)}."
        return linia
    if z_ms == d and z_naglowka == d:
        # I C 401/20 (2026-11-13): „uzasadnienie” z 9.11.2021 zaczyna się „W Y R O K … Dnia 9 listopada
        # 2021 roku” — data portalu jest tu zarazem datą wyroku; „NIE data wyroku” byłoby nieprawdą
        return (f"UWAGA: typ „uzasadnienie” — {d} to data dokumentu w portalu orzeczeń MS i zarazem data "
                "z nagłówka treści: może to być także data WYROKU (wyrok i uzasadnienie w jednym dokumencie). "
                "Potwierdź w sentencji, zanim policzysz od niej terminy.")
    if z_ms == d:
        linia = (f"UWAGA: typ „uzasadnienie” — {d} to data sporządzenia UZASADNIENIA (portal orzeczeń MS "
                 "datuje ten dokument tak samo), NIE data wydania wyroku. Terminów i chronologii nie licz "
                 "od tej daty.")
    elif z_ms:
        linia = (f"UWAGA: typ „uzasadnienie” — SAOS podaje {d}, a portal orzeczeń MS datuje ten dokument na "
                 f"{_data_pl(z_ms)}; nie wiadomo, która to data wyroku. Terminów i chronologii nie licz od "
                 "żadnej z nich bez sprawdzenia w sentencji.")
    else:
        linia = (f"UWAGA: typ „uzasadnienie” — {d} bywa datą sporządzenia UZASADNIENIA, a bywa datą wyroku "
                 f"({PROBA_UZASADNIEN}); tu nie da się tego rozstrzygnąć. Terminów i chronologii nie licz od tej "
                 "daty bez sprawdzenia.")
    if z_naglowka and z_naglowka != d:
        linia += (f"\n          W nagłówku treści: {_data_pl(z_naglowka)} — prawdopodobna data wyroku "
                  "(heurystyka z początku tekstu — może to być data innego orzeczenia cytowanego na wstępie; "
                  "potwierdź w sentencji).")
    else:
        linia += "\n          Datę wyroku ustal w treści (nagłówek/sentencja) albo w źródle oryginalnym."
    return linia


def _dopisek_daty(it):
    """Dopisek przy dacie na liście wyników: data z przyszłości (błąd SAOS), typ „uzasadnienie”.

    Lista wyszukiwania nie ma source.judgmentUrl, więc przy uzasadnieniu nie da się tu rozstrzygnąć,
    czy data to data uzasadnienia, czy wyroku (zob. _uwaga_uzasadnienie) — rozstrzyga `orzeczenie <id>`."""
    dop = f" — {UWAGA_PRZYSZLOSC}" if _z_przyszlosci(it.get("judgmentDate")) else ""
    if it.get("judgmentType") == "REASONS":
        dop += " — uzasadnienie: data uzasadnienia albo wyroku, nie licz od niej terminów (orzeczenie <id>)"
    return dop


def _na_stderr(uwagi):
    """Ostrzeżenia przy --json: na stderr, żeby stdout został czystym, nietkniętym JSON-em z API.

    Do wersji 2.3.0 przy --json ginęły wszystkie ostrzeżenia poza granicą zbioru (np. data
    3013-12-04 szła w surowym JSON bez słowa)."""
    for u in uwagi:
        if u:
            print(u if u.startswith(("UWAGA", "BŁĄD")) else "UWAGA: " + u, file=sys.stderr)


def _uwagi_pozycji(items):
    """Ostrzeżenia dla pozycji listy (do stderr przy --json): data z przyszłości, typ „uzasadnienie”."""
    uwagi = []
    for it in items:
        dop = _dopisek_daty(it) if isinstance(it, dict) else ""
        if dop:
            uwagi.append(f"UWAGA: [{it.get('id')}] {_case_numbers(it) or '—'} ({it.get('judgmentDate', '?')})"
                         + dop)
    return uwagi


def _wiersz(it):
    """Jedna pozycja listy wyników (id, sygnatura, sąd, data, forma, hasła, snippet)."""
    cn = _case_numbers(it) or "—"
    ctype = CT_PL.get(it.get("courtType", ""), it.get("courtType", ""))
    court = _court_label(it)
    forma = _forma(it)
    uz = _dopisek_daty(it)
    print(f"  [{it.get('id')}]  {cn}   ({it.get('judgmentDate', '?')}{uz})")
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


# Hasła tematyczne (`keywords`) ma w SAOS TYLKO zbiór sądów powszechnych — sprawdzone 2026-11-13:
# żadne ze 100 najnowszych orzeczeń SN, TK i KIO ani ze 100 najstarszych SN nie ma haseł, a --haslo
# „odszkodowanie”, „umowa o pracę”, „zamówienia publiczne” dają dla SN/TK/KIO po 0 trafień (dla sądów
# powszechnych 20 032 / 354 / 303). Zero z --haslo dla tych sądów to więc brak HASEŁ, nie orzeczeń.
SADY_BEZ_HASEL = ("SUPREME", "CONSTITUTIONAL_TRIBUNAL", "NATIONAL_APPEAL_CHAMBER", "ADMINISTRATIVE")
# Słownik haseł sądów powszechnych: podpowiadacz wyszukiwarki SAOS (poza /api; GET, JSON
# [{"id", "phrase"}], dopasowanie od POCZĄTKU hasła, bez rozróżniania wielkości liter). Sprawdzone
# 2026-11-13: każde ze 100 haseł z 300 orzeczeń sądów powszechnych jest w słowniku; --haslo to
# DOKŁADNE hasło („przestępstwo przeciwko zdrowiu” 1882 trafienia, „przestępstwa przeciwko zdrowiu” 256,
# „przestępstwo” 583 — to osobne hasła), więc odmiana daje inne albo zero trafień.
SLOWNIK_HASEL = "https://www.saos.org.pl/keywords/COMMON/"


def _rdzen(slowo):
    return slowo[:max(3, len(slowo) - 2)]


def _slownik_hasel(haslo):
    """(czy hasło jest w słowniku, podobne hasła) albo None, gdy słownika nie udało się sprawdzić.

    Jedno zapytanie o rdzeń pierwszego słowa; „podobne” = hasła zawierające rdzenie pozostałych słów
    (przestępstwo przeciwko zdrowiu → przestępstwa przeciwko zdrowiu, … przeciwko życiu i zdrowiu)."""
    slowa = haslo.lower().split()
    if not slowa:
        return None
    try:
        d = _get(urllib.parse.quote(_rdzen(slowa[0]), safe=""), soft=True, baza=SLOWNIK_HASEL)
    except VerificationUnknown:
        return None
    if not isinstance(d, list):
        return None
    frazy = [x["phrase"] for x in d if isinstance(x, dict) and isinstance(x.get("phrase"), str)]
    cel = " ".join(slowa)
    jest = any(" ".join(f.lower().split()) == cel for f in frazy)
    podobne = [f for f in frazy if " ".join(f.lower().split()) != cel
               and all(_rdzen(s) in f.lower() for s in slowa[1:])]
    return jest, podobne[:12]


def _komenda_ms_szukaj(a):
    """Odpowiednik zapytania w Portalu Orzeczeń MS (te kryteria, które portal ma) albo None."""
    czesci = ["szukaj"] + ([a.fraza] if a.fraza else [])
    for flaga, wartosc in (("--sygnatura", a.sygnatura), ("--od", _norma_data(a.od)),
                           ("--do", _norma_data(a.do, koniec=True)), ("--sedzia", a.sedzia),
                           ("--haslo", a.haslo), ("--przepis", a.przepis)):
        if wartosc:
            czesci += [flaga, wartosc]
    return _komenda_ms(*czesci) if len(czesci) > 1 else None


def _linia_portalu_ms(a):
    kom = _komenda_ms_szukaj(a)
    return (f"{LUKA_SP}. Sądy powszechne sprawdź u źródła skillem prawo-pl-orzeczenia-ms"
            + (f": {kom}" if kom else " (portal wymaga co najmniej jednego kryterium: frazy, sygnatury, daty…)")
            + ".")


def _komenda_saos_fraza(a, fraza=None):
    """To samo zapytanie SAOS z hasłem jako frazą pełnotekstową (bez --haslo)."""
    import shlex
    fraza = fraza or a.haslo
    if not fraza:
        return None
    czesci = ["saos.py", "szukaj", fraza]
    for flaga, wartosc in (("--sad", a.sad), ("--od", a.od), ("--do", a.do)):
        if wartosc:
            czesci += [flaga, wartosc]
    return " ".join(shlex.quote(c) for c in czesci)


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
        sys.exit("BŁĄD: podaj kryterium: frazę albo --sad / --sygnatura / --przepis / --sedzia / --haslo / "
                 "--typ / zakres dat.")
    d = _get("/search/judgments", params)
    d = _expect_search(d, "wyników wyszukiwania")
    items = d.get("items", [])
    total = (d.get("info") or {}).get("totalResults", "?")
    bez_hasel = bool(a.haslo) and ct in SADY_BEZ_HASEL
    if not items:
        # zero trafień = komunikat + kod 1, TAKŻE z --json (pusty JSON wyglądałby jak
        # „sprawdzone, nic nie ma") — z wyjaśnieniem granicy zamkniętego zbioru, gdy dotyczy
        linie = [f"Brak trafień w SAOS dla podanych kryteriów (totalResults: {total})."]
        blad = None  # zero, które NIE jest zweryfikowanym brakiem (kod 2)
        zle_haslo = False  # hasło spoza słownika — komenda portalu MS z tym samym hasłem nic nie da
        if bez_hasel:
            fraza = _komenda_saos_fraza(a)
            blad = (f"BŁĄD: --haslo nie działa dla zbioru {CT_PL.get(ct, ct)}: SAOS ma hasła tematyczne TYLKO "
                    "dla sądów powszechnych (sprawdzone 13.11.2026 — orzeczenia SN, TK i KIO nie mają w SAOS "
                    "żadnych haseł), więc zero wynika z braku HASEŁ, nie orzeczeń. Szukaj frazą"
                    + (f": {fraza}" if fraza else "") + ".")
        elif a.haslo:
            slownik = _slownik_hasel(a.haslo)
            if slownik is None:
                blad = (f"BŁĄD: nie udało się sprawdzić, czy hasło {a.haslo!r} istnieje w słowniku haseł SAOS — "
                        "zero może wynikać z hasła spoza słownika (--haslo to DOKŁADNE hasło), a nie z braku "
                        "orzeczeń. Szukaj też frazą.")
            elif not slownik[0]:
                zle_haslo = True
                blad = (f"BŁĄD: hasła {a.haslo!r} NIE MA w słowniku haseł SAOS (sądy powszechne), więc zero "
                        "wynika ze złego hasła, nie z braku orzeczeń. --haslo to DOKŁADNE hasło ze słownika: "
                        "inna odmiana albo fragment to inne hasło. "
                        + (f"Hasła ze słownika o podobnym brzmieniu: {'; '.join(slownik[1])}. " if slownik[1]
                           else "W słowniku nie ma haseł o podobnym brzmieniu. ")
                        + "Albo szukaj frazą: " + (_komenda_saos_fraza(a, a.haslo) or "") + ".")
            else:
                linie.append(f"Hasło {a.haslo!r} jest w słowniku SAOS — zero dotyczy orzeczeń z tym hasłem przy "
                             "pozostałych kryteriach.")
        if ct == "ADMINISTRATIVE":
            linie.append("Sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — orzecznictwo "
                         "administracyjne pobieraj skillem prawo-pl-cbosa (scripts/cbosa.py, baza CBOSA).")
        if ct in ZASIEG:
            granica, potwierdzona = _granica_zbioru(ct)
            linie.append(_ostrzezenie_zasiegu(ct, a.od, a.do, granica, potwierdzona))
        if ct in (None, "COMMON") and not zle_haslo:
            linie.append(_linia_portalu_ms(a))
        if a.przepis:
            linie.append("--przepis to luźne dopasowanie pełnotekstowe w polu powołanych przepisów "
                         "(lista SAOS bywa niepełna) — spróbuj też samą frazą"
                         + (" albo --haslo (tylko sądy powszechne)." if ct in (None, "COMMON") else "."))
        linie.append("SAOS to baza wtórna (agregat z lukami) — brak trafień tu nie dowodzi braku orzecznictwa.")
        if blad:
            sys.exit("\n".join([blad] + linie[1:]))
        raise BrakWynikow("\n".join(linie))
    granica = _ostrzezenie_zasiegu(ct, a.od, a.do, *(_granica_znana(ct, items) if ct in ZASIEG else ()))
    uwagi = []
    if ct == "ADMINISTRATIVE":
        uwagi.append("UWAGA: sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — orzecznictwo "
                     "administracyjne pobieraj skillem prawo-pl-cbosa (scripts/cbosa.py, baza CBOSA).")
    if granica:
        uwagi.append(granica)
    if a.przepis:
        uwagi.append(f"UWAGA: --przepis {a.przepis!r} to LUŹNE dopasowanie pełnotekstowe w polu powołanych "
                     "przepisów — „art. 415” trafia art. 415 k.c., k.p.c. i k.p.k. jednakowo, a lista powołanych "
                     "przepisów w SAOS bywa niepełna. Dopisz nazwę aktu (np. \"Kodeks cywilny art. 415\") "
                     "i sprawdź w 'orzeczenie <id>', którego aktu dotyczy trafienie.")
    if a.haslo and not bez_hasel:
        slownik = _slownik_hasel(a.haslo) if ct in (None, "COMMON") else None
        uwagi.append(f"UWAGA: --haslo {a.haslo!r} to DOKŁADNE hasło ze słownika SAOS i działa tylko dla sądów "
                     "powszechnych" + (" (SN, TK i KIO nie mają w SAOS haseł — wyniki ich nie obejmują)" if ct is None
                                         else "") + ". Inna odmiana "
                     "tematu to osobne hasło"
                     + (f"; w słowniku są też: {'; '.join(slownik[1])} — sprawdź je osobno." if slownik and slownik[1]
                        else "."))
    if a.json:
        _na_stderr(uwagi + _uwagi_pozycji(items))
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Znaleziono: {total}  (pokazuję {len(items)}, strona {params['pageNumber']}, po {params['pageSize']})\n")
    for u in uwagi:
        print(u + "\n")
    for it in items:
        _wiersz(it)
    if items:
        print(f"Pełna treść: orzeczenie <id>  (np. orzeczenie {items[0].get('id')})")
        if isinstance(total, int) and total > len(items):
            print(f"Kolejna strona: --strona {params['pageNumber'] + 1}")


_ID_PORTALU_MS = re.compile(r"[0-9]{15}_[A-Za-z0-9_-]+")  # ten sam walidator co w orzeczenia_ms.py


def _id_portalu_ms(url):
    """Identyfikator dokumentu w Portalu Orzeczeń MS z `source.judgmentUrl` (parametr `id=`) albo None.

    Kształt linku (2026-11-13, 36/36 orzeczeń bez treści w próbie): `https://apiorzeczenia.wroclaw.sa.gov.pl/
    ncourt-api/judgement/details?id=150515150001006_II_K_000295_2025_Uz_2026-09-16_002`."""
    try:
        wartosci = urllib.parse.parse_qs(urllib.parse.urlsplit(str(url or "")).query).get("id") or []
    except ValueError:
        return None
    return next((w for w in wartosci if _ID_PORTALU_MS.fullmatch(w)), None)


def _uwaga_brak_tresci_ms(data, cn):
    """Ostrzeżenie dla orzeczenia sądu powszechnego bez treści w SAOS — z komendą do portalu MS.

    SAOS importuje czasem orzeczenia sądów powszechnych bez tekstu (sama metryka). Tak było z
    orzeczeniami publikowanymi podczas awarii Portalu Orzeczeń MS od 24.09.2026: w próbie z 2026-11-13
    (200 z 578 orzeczeń z 1.09–10.10.2026) wszystkie 36 z publikacją 24.09–9.10 nie miały treści,
    a od publikacji 12.10 wszystkie ją miały. Portal MS wydaje dziś treść tych dokumentów (3/3
    sprawdzone, m.in. II K 295/25 — 16 331 znaków), więc „brak u źródła" byłby nieprawdą. Dlatego
    warunkiem nie jest data, tylko sam brak treści, a komunikat odsyła do portalu."""
    ident = _id_portalu_ms((data.get("source") or {}).get("judgmentUrl"))
    if ident:
        jak = f"orzeczenia_ms.py orzeczenie {ident}"
    else:
        sygn = next((c.get("caseNumber") for c in (data.get("courtCases") or []) if c.get("caseNumber")),
                    cn if cn and cn != "—" else "<sygnatura>")
        jak = (f"orzeczenia_ms.py sygnatura {sygn}  (identyfikatora portalu nie da się odczytać "
               "z source.judgmentUrl — wybierz trafienie właściwego sądu i daty)")
    return ("UWAGA: SAOS nie ma treści tego orzeczenia sądu powszechnego (import bez tekstu — tak było "
            "m.in. z orzeczeniami publikowanymi podczas awarii Portalu Orzeczeń MS od 24.09.2026 do "
            "początku października 2026). Brak treści w SAOS nie znaczy, że nie ma jej u źródła: pobierz "
            f"ją z Portalu Orzeczeń MS skillem prawo-pl-orzeczenia-ms: {jak}. "
            "Nie odtwarzaj treści z pamięci ani z fragmentów.")


def _uwagi_orzeczenia_json(data):
    """Ostrzeżenia istotne dla poprawności cytatu, które tryb tekstowy drukuje przy `orzeczenie` —
    przy --json idą na stderr (bez zapytania o link SN: to dane pomocnicze, nie ostrzeżenie)."""
    if not isinstance(data, dict):
        return []
    ct = data.get("courtType", "")
    cn = _case_numbers(data) or "—"
    uwagi = []
    if _z_przyszlosci(data.get("judgmentDate")):
        uwagi.append(f"UWAGA: judgmentDate {data.get('judgmentDate')} — {UWAGA_PRZYSZLOSC} (sentencja/nagłówek) "
                     "albo w źródle oryginalnym.")
    txt = html_to_text(data.get("textContent") or "")
    uwagi.append(_uwaga_uzasadnienie(data, txt))
    if ct in ZASIEG:
        uwagi.append(UWAGA_INDEKSY + ".")
        if (data.get("source") or {}).get("judgmentUrl"):
            uwagi.append("UWAGA: source.judgmentUrl dla SN/TK/KIO jest martwy albo ogólny — nie służy do "
                         "weryfikacji; źródło urzędowe: " + {"SUPREME": SN_WYSZUKIWARKA,
                                                             "CONSTITUTIONAL_TRIBUNAL": "https://ipo.trybunal.gov.pl/ipo/",
                                                             "NATIONAL_APPEAL_CHAMBER": "https://orzeczenia.uzp.gov.pl/Home/Search"}[ct]
                         + f" (sygnatura {cn}).")
    reg = data.get("referencedRegulations") or []
    zle = [r for r in reg if isinstance(r, dict) and "prawdopodobnie uszkodzony" in _wpis_przepisu(r)]
    if zle:
        uwagi.append(f"UWAGA: {len(zle)} z {len(reg)} wpisów referencedRegulations wygląda na uszkodzone "
                     "(spłaszczone indeksy, „Nr 0”, sklejone słowa) — np. "
                     f"{(zle[0].get('text') or '')[:120]!r}; zweryfikuj w treści orzeczenia.")
    if reg:
        uwagi.append("UWAGA: referencedRegulations to automatyczna ekstrakcja SAOS — bywa niepełna; przepisy "
                     "rozstrzygnięcia czytaj z sentencji.")
    if not txt:
        uwagi.append(_uwaga_brak_tresci_ms(data, cn) if ct == "COMMON"
                     else "UWAGA: brak treści w API SAOS — otwórz źródło urzędowe.")
    return uwagi


def cmd_orzeczenie(a):
    d = _get(f"/judgments/{a.id}")
    d = _expect_judgment(d, a.id)
    if a.json:
        _na_stderr(_uwagi_orzeczenia_json(d.get("data", d)))
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    data = d.get("data", d)
    cn = _case_numbers(data) or "—"
    ctype = CT_PL.get(data.get("courtType", ""), data.get("courtType", ""))
    court = _court_label(data)
    print(f"# Orzeczenie [{data.get('id')}]  {cn}")
    print(f"  Sąd:    {ctype}" + (f" — {court}" if court else ""))
    print(f"  Data:   {data.get('judgmentDate', '?')}    typ: {_forma(data)}")
    if _z_przyszlosci(data.get("judgmentDate")):
        print(f"  UWAGA: {UWAGA_PRZYSZLOSC} (sentencja/nagłówek) albo w źródle oryginalnym.")
    txt = html_to_text(data.get("textContent") or "")
    uwaga_uz = _uwaga_uzasadnienie(data, txt)
    if uwaga_uz:
        print(f"  {uwaga_uz}")
    publikacja = (data.get("source") or {}).get("publicationDate")
    if publikacja:
        print(f"  Publikacja: {publikacja}  (data publikacji w źródle — source.publicationDate)")
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

    print(f"\n## Treść uzasadnienia ({len(txt)} znaków)")
    if ct in ZASIEG:
        print(f"({UWAGA_INDEKSY})")
    if not txt:
        print("(brak treści w API — otwórz źródło urzędowe wyżej)")
        if ct == "COMMON":
            print(_uwaga_brak_tresci_ms(data, cn))
        if a.fragment:
            # bez treści nie da się sprawdzić frazy — to nie „frazy nie ma” (kod 2, nie 1 ani 0)
            sys.exit(f"BŁĄD: nie można sprawdzić frazy {a.fragment!r} — SAOS nie ma treści tego orzeczenia. "
                     "To NIE jest brak frazy w orzeczeniu; treść pobierz ze źródła wskazanego wyżej.")
        return
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            raise BrakWynikow(f"Nie znaleziono frazy {a.fragment!r} w treści ({len(txt)} znaków). "
                              "Spróbuj inną frazą.")
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
        powody = _wyjasnienie_zera_sygnatury(sig)
        granice = " (granice zamkniętych zbiorów SN/TK/KIO do DNIA)" if any(
            "zbiór kończy się" in w or "mieści się w zbiorze" in w for w in powody) else ""
        raise BrakWynikow(f"Nie znaleziono orzeczenia o sygnaturze {sig!r} w SAOS.\n"
                          "SAOS to baza wtórna (nie ma wszystkiego) — brak tu nie dowodzi, że orzeczenia nie ma; "
                          "sygnatury sądów administracyjnych (NSA/WSA) szukaj skillem prawo-pl-cbosa.\n"
                          f"Dlaczego SAOS może jej nie mieć{granice}:\n"
                          + "\n".join("  - " + w for w in powody))
    sady = {_klucz_sadu(it) for it in items}
    # ta sama sygnatura w różnych sądach (np. „I ACa 100/13” w 4 sądach apelacyjnych) to RÓŻNE
    # sprawy — bez nazwy sądu przy trafieniu łatwo zacytować cudzy wyrok
    kolizja = (f"UWAGA: sygnatura {sig!r} występuje w {len(sady)} RÓŻNYCH sądach — to RÓŻNE sprawy, "
               "nie wersje jednego orzeczenia. Wybierz trafienie właściwego sądu (nazwa sądu przy "
               "każdej pozycji); dopasowanie po samej sygnaturze nie identyfikuje sprawy.") \
        if len(sady) > 1 else None
    if a.json:
        _na_stderr([kolizja] + _uwagi_pozycji(items))
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Sygnatura {sig!r}: dopasowań {total}\n")
    if kolizja:
        print(kolizja + "\n")
    for it in items:
        cn = _case_numbers(it)
        ctype = CT_PL.get(it.get("courtType", ""), it.get("courtType", ""))
        court = _court_label(it)
        forma = _forma(it)
        uz = _dopisek_daty(it)
        print(f"  [{it.get('id')}]  {cn}  ({it.get('judgmentDate', '?')}{uz})  {forma}".rstrip())
        print(f"    {ctype}" + (f" — {court}" if court else "") + f"   → orzeczenie {it.get('id')}")
    if len(items) == 1:
        print(f"\nPełna treść: orzeczenie {items[0].get('id')}")
    else:
        print("\nPełna treść: orzeczenie <id> wybranego trafienia (każda pozycja ma własne id — "
              "sprawdź sąd, zanim zacytujesz).")
    if isinstance(total, int) and total > len(items):
        print(f"Pokazano {len(items)} z {total} — pełna lista: szukaj --sygnatura {sig!r} --limit 100")


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

    a = ap.parse_args()  # zły argument wiersza poleceń: argparse sam kończy kodem 2
    # Kody wyjścia jak w orzeczenia_ms.py: 0 = wynik, 1 = ZWERYFIKOWANY brak (BrakWynikow),
    # 2 = wszystko inne (sieć/HTTP, przerwa techniczna, nieoczekiwana odpowiedź, zły argument,
    # nieistniejące id, blokada --strict, VerificationUnknown, nieprzewidziany wyjątek).
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
    except VerificationUnknown as e:
        print(f"BŁĄD: nie udało się zweryfikować danych w SAOS ({e}). Spróbuj ponownie za chwilę. "
              "To NIE jest brak wyników.", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # noqa: BLE001 — nieprzewidziany błąd nigdy nie może dać kodu 1 („brak wyników”)
        print(f"BŁĄD: nieoczekiwany błąd helpera ({e!r}). To NIE jest brak wyników — ponów albo zgłoś błąd.",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
