#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO API ELI Sejmu (https://api.sejm.gov.pl/eli).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET). Źródło pierwotne prawa polskiego: Dziennik Ustaw (DU) i Monitor Polski (MP).

Komendy:
  szukaj ["<fraza>"] [--typ T] [--rok R] [--wyd DU|MP] [--haslo H] [--obowiazujace] [--limit N] [--offset N]
  meta <sygnatura...>            np. meta DU 2024 18  |  meta "Dz.U. 2024 poz. 18"  |  meta WDU20240000018
  tekst <sygnatura...> [--fragment "art. 299" | "§ 4" | "art. 25 § 2"] [--pdf ŚCIEŻKA]
                                 tekst aktu (text.html → czysty tekst; gdy API nie ma HTML — własny urzędowy
                                 PDF aktu przez `pdftotext -layout`, jeśli jest w PATH); --fragment wycina
                                 tylko jednostki z frazą (np. jeden artykuł); --pdf zapisuje urzędowy PDF
  struktura <sygnatura...> [--filtr F] [--poziom N]   spis jednostek redakcyjnych aktu (z /struct)
  odniesienia <sygnatura...>     nowelizacje, tekst jednolity, podstawa prawna
  tj <sygnatura...>              znajduje AKTUALNY TEKST JEDNOLITY dla aktu i podaje jego sygnaturę
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik PRZED emisją, gdy nie udało się zweryfikować aktualności lub
                      kompletności: nowszy t.j., awaria kontroli, tekst ze STARSZEGO t.j. zamiast własnego
                      PDF, niepełna lista nowelizacji, tekst z OCR, strony PDF bez warstwy tekstowej,
                      obowiązująca zmiana po stanie tekstu, której tekst nie zawiera (przy --fragment — gdy
                      dotyczy tego artykułu albo nie da się tego ustalić), utrata mocy po wyroku TK)
Kod wyjścia: 0 = wynik; 1 = zweryfikowany brak (zero trafień, frazy nie ma w tekście); 2 = wszystko inne.
"""
import sys, json, re, time, argparse, shutil, subprocess, tempfile, os, datetime, hashlib
import concurrent.futures
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser
from html import unescape as _unescape

__version__ = "2.6.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://api.sejm.gov.pl/eli"
CONTENT_HOSTS = ("api.sejm.gov.pl",)
# Pamięć podręczna udanych GET-ów bez parametrów (metadane, odniesienia) w obrębie jednego
# uruchomienia — te same odniesienia aktu bazowego potrzebuje kontrola nowszego t.j. i lista
# nowelizacji; zapora api.sejm.gov.pl źle znosi powtórzone żądania.
_CACHE = {}


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


class BrakWynikow(SystemExit):
    """ZWERYFIKOWANY brak wyników: zapytanie wykonane poprawnie, API odpowiedziało, zero trafień (albo frazy
    nie ma w pobranym tekście) — kod wyjścia 1. Każde inne zakończenie z komunikatem (błąd sieci/HTTP,
    nieoczekiwana odpowiedź, zła sygnatura, blokada --strict, VerificationUnknown) daje kod 2 — tak jak
    w orzeczenia_ms.py. Dotąd `sys.exit("…")` dawało 1 w obu sytuacjach, więc skrypt nie odróżniał
    „sprawdzone, nic nie ma" od awarii (ujednolicenie 2026-11)."""


def _nie_zweryfikowano(co, blad):
    sys.exit(f"BŁĄD: nie udało się zweryfikować {co} ({blad}). "
             "Spróbuj ponownie za chwilę.")


def _wymus_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed)
                    for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi HTTP na hostach treści ELI, a obce cele HTTP odrzuca."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)


_opener = urllib.request.build_opener(_PrzekierowaniaHttps())


def _get(path, params=None, soft=False):
    """GET z jednym ponowieniem.

    Zwraca dane, w tym pustą odpowiedź jako VERIFIED_ABSENT. Przy soft=True:
    HTTP 404 to zweryfikowany brak zasobu (None) — API ELI odpowiada 404 wyłącznie dla
    nieistniejącego adresu (zapora daje 200 + "Request Rejected", przeciążenie 5xx);
    każdy inny błąd żądania to osobny stan UNKNOWN (VerificationUnknown).
    """
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    elif url in _CACHE:
        return _CACHE[url]
    wynik = _pobierz(url, soft)
    if not params:
        _CACHE[url] = wynik
    return wynik


def _pobierz(url, soft):
    req = urllib.request.Request(url, headers={"User-Agent": f"eli-skill/{__version__}", "Accept": "application/json, text/html"})
    raw, ctype = None, ""
    for attempt in (1, 2):
        try:
            with _opener.open(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                raw = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if soft:
                if e.code == 404:
                    return None
                raise VerificationUnknown(f"HTTP {e.code}: {url}") from e
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 1:
                time.sleep(2); continue
            if soft:
                raise VerificationUnknown(f"błąd sieci: {url} ({e})") from e
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    if raw is not None and "Request Rejected" in raw and "rejected" in raw.lower():
        # zapora (WAF) api.sejm.gov.pl potrafi odrzucać wybrane URL-e (m.in. /text.html/{tree})
        if soft:
            raise VerificationUnknown(f"zapora api.sejm.gov.pl odrzuciła żądanie: {url}")
        sys.exit(f"BŁĄD: zapora api.sejm.gov.pl odrzuciła żądanie (Request Rejected): {url}\n"
                 "Spróbuj ponownie; do pojedynczego artykułu użyj: tekst <syg> --fragment \"art. N\".")
    if "json" in ctype:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _get_bytes(url, soft=False):
    """Pobiera plik (PDF). soft=True: awaria to VerificationUnknown zamiast zakończenia programu."""
    req = urllib.request.Request(url, headers={"User-Agent": f"eli-skill/{__version__}"})
    try:
        with _opener.open(req, timeout=60) as r:
            return r.read()
    except Exception as e:
        if soft:
            raise VerificationUnknown(f"błąd pobierania {url} ({e})") from e
        sys.exit(f"BŁĄD pobierania: {url} ({e})")


def _expect_dict(d, what):
    if not isinstance(d, dict):
        sys.exit(f"BŁĄD: API zwróciło nieoczekiwaną odpowiedź ({what}) — spróbuj ponownie za chwilę.")
    return d


class _Stripper(HTMLParser):
    """HTML z text.html → tekst.

    Odsyłacz do przypisu API zapisuje jako <a class="gloss-link tooltip"><sup>N)</sup>
    <span class="tooltip-text">treść przypisu</span></a> — WEWNĄTRZ numeru jednostki:
    „<h3>2<a…><sup>1)</sup>…</a>)</h3>" (pkt 2 z przypisem 1), „Art. 66c<a…><sup>6)</sup>…</a>."
    Przepisywanie tego liniowo dawało „2 1)", „a 2)", „§ 1 12)" — cyfra odsyłacza wchodziła
    w numer jednostki (pkt 2 czytało się jak pkt 21). Dlatego numer odsyłacza NIE trafia do
    tekstu, a treść przypisu czeka w kolejce i wychodzi na najbliższej granicy bloku jako
    osobna linia „[przypis N)] …" (etykieta jest konieczna: 7 przypisów w k.p.c. zaczyna się
    od „Art. 598…"/„Tytuł działu…" i na początku linii udawałoby nagłówek jednostki — _GRANICE).

    Te same przypisy API powtarza w bloku <div class="gloss-section"> pod treścią („1)", „treść") — przepisany
    liniowo dawał drugi raz każdy przypis, a jego „1)" udawało punkt ostatniej jednostki aktu. Przypis z bloku
    wychodzi tylko wtedy, gdy nie było go w odsyłaczu. Przycisk „Pokaż całość" (<button>) nie jest treścią.
    Tytuł w <h1> to trzy <span class="head-…"> (rodzaj, data, tytuł) — każdy zaczyna nowy wiersz, bez tego
    wychodziło „Ministra Zdrowiaz dnia … r.zmieniające".
    """

    BLOKI = ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4")

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0
        self.gloss = 0            # głębokość <a> wewnątrz odsyłacza do przypisu
        self.w_sup = False        # w <sup> odsyłacza (numer przypisu)
        self.tt = 0               # głębokość <span> wewnątrz treści przypisu (tooltip-text)
        self.nr, self.tresc = [], []
        self.oczekujace = []      # przypisy do wypisania na najbliższej granicy bloku
        self.href = ""            # adres odsyłacza („#gloss-0:1:") — ten sam id ma przypis w gloss-section
        self.wypisane_id, self.wypisane = set(), set()   # przypisy już wypisane z odsyłaczy (id, (nr, treść))
        self.sekcja = 0           # głębokość <div> wewnątrz <div class="gloss-section">
        self.glossa = 0           # głębokość <div> wewnątrz jednego <div class="gloss"> w tej sekcji
        self.glossa_id, self.glossa_tekst = "", []

    def _granica(self):
        for nr, tresc in self.oczekujace:
            if tresc:
                self.out.append(f"\n[przypis {nr}] {tresc}" if nr else f"\n[przypis] {tresc}")
        self.oczekujace = []
        self.out.append("\n")

    def zakoncz(self):
        if self.oczekujace:
            self._granica()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "button"):
            self.skip += 1
        atrybuty = dict(attrs)
        klasa = atrybuty.get("class", "") or ""
        if self.sekcja:
            if tag == "div":
                self.sekcja += 1
                if self.glossa:
                    self.glossa += 1
                elif "gloss" in klasa.split():
                    self.glossa, self.glossa_id, self.glossa_tekst = 1, atrybuty.get("id", "") or "", []
            if self.glossa and tag in self.BLOKI:
                self.glossa_tekst.append(" ")
            return
        if tag == "div" and "gloss-section" in klasa.split():
            self._granica()
            self.sekcja = 1
            return
        if tag in self.BLOKI or tag == "span" and klasa.startswith("head-") and not self.gloss:
            self._granica()
        if tag == "a" and self.gloss:
            self.gloss += 1
        elif tag == "a" and "gloss-link" in klasa:
            self.gloss, self.nr, self.tresc = 1, [], []
            self.href = (atrybuty.get("href") or "").lstrip("#")
        if tag == "sup":
            if self.gloss:
                self.w_sup = True
            else:
                # Indeks górny artykułu (np. "Art. 21<sup>1</sup>.") rozdzielamy spacją, żeby
                # "Art. 21 1." było odróżnialne od "Art. 211." — inaczej art. 21¹ i art. 211
                # sklejają się do tego samego napisu i _fragmenty zwraca oba (znany bug).
                self.out.append(" ")
        if tag == "span":
            if self.tt:
                self.tt += 1
            elif "tooltip-text" in klasa:
                if self.gloss:
                    self.tt = 1
                else:   # dymek poza odsyłaczem (nieznany wariant znaczników) — jak dawniej, inline
                    self.out.append("\n[przypis] ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "button") and self.skip:
            self.skip -= 1
        if self.sekcja:
            if tag == "div":
                self.sekcja -= 1
                if self.glossa:
                    self.glossa -= 1
                    if not self.glossa:
                        self._glossa_z_sekcji()
            return
        if tag == "sup":
            self.w_sup = False
        if tag == "span" and self.tt:
            self.tt -= 1
        if tag == "a" and self.gloss:
            self.gloss -= 1
            if not self.gloss:
                nr = "".join(self.nr).replace("\xa0", " ").strip()
                tresc = " ".join("".join(self.tresc).replace("\xa0", " ").split())
                self.oczekujace.append((nr, tresc))
                if tresc:
                    self.wypisane.add((nr, tresc))
                    if self.href:
                        self.wypisane_id.add(self.href)
                self.nr, self.tresc, self.href = [], [], ""

    def _glossa_z_sekcji(self):
        """Przypis z bloku gloss-section: „1)" + treść. Wypisany tylko, gdy nie było go w odsyłaczu."""
        tekst = " ".join("".join(self.glossa_tekst).replace("\xa0", " ").split())
        m = re.match(r"^(\d+[a-z]?\)|[a-z]\)|\*+)\s*(.*)$", tekst)
        nr, tresc = (m.group(1), m.group(2)) if m else ("", tekst)
        if tresc and self.glossa_id not in self.wypisane_id and (nr, tresc) not in self.wypisane:
            self.oczekujace.append((nr, tresc))
            self.wypisane.add((nr, tresc))
        self.glossa_id, self.glossa_tekst = "", []

    def handle_data(self, data):
        if self.skip:
            return
        if self.sekcja:
            if self.glossa:
                self.glossa_tekst.append(data)
            return
        if self.gloss:
            if self.w_sup:
                self.nr.append(data)
            elif self.tt:
                self.tresc.append(data)
            return
        self.out.append(data)


def html_to_text(html):
    p = _Stripper()
    p.feed(html)
    p.zakoncz()
    # API ELI używa twardych spacji (NBSP), np. "Art.\xa0299." — normalizuj, żeby frazy były wyszukiwalne
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# ---------------------------------------------------------------------------------------------
# Urzędowy PDF → tekst (gdy API ma textHTML=false: np. k.c. DU 2026 795, Konstytucja DU 1997 483)
# ---------------------------------------------------------------------------------------------
# „Dziennik Ustaw Nr 105        — 7006 —        Poz. 990 i 991" — nagłówek strony zeszytu z lat do 2011
_PDF_NAGLOWEK = re.compile(r"^\s*(©\s*Kancelaria Sejmu|(Dziennik Ustaw|Monitor Polski)\s+–\s*\d+\s*–"
                           r"|(Dziennik Ustaw|Monitor Polski)\s+Nr\s+\d+\s+[—–]\s*\d+\s*[—–])")
_PDF_STOPKA = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|–?\s*\d{1,4}\s*–?)\s*$")
# „1)I)" — przypis z dwoma numerami: arabskim i rzymskim odnośnika do tytułu (k.k.s. DU 1999/930, tekst ujednolicony, s. 1:
# „1)I) Niniejsza ustawa w zakresie swojej regulacji: 1) realizuje…" i niżej „I) Odnośnik do tytułu ustawy w brzmieniu…").
# Dotąd ten wiersz nie był numerem przypisu, więc blok przypisów strony szedł do art. 1 § 3 (z punktami 1) i 2) dyrektyw
# udającymi punkty paragrafu), a przy tytule stało „treści przypisu nie odnaleziono".
_PDF_PRZYPIS = re.compile(r"^(\d{1,3})\)(?:[IVX]{1,4}\))?(?:\s+(.*))?$")
# glued odsyłacz do przypisu: „§ 1.3)", „(uchylony)5)", „chemicznych2)" — cyfry + „)" sklejone
# z poprzedzającym znakiem, który nie jest spacją, cyfrą ani nawiasem otwierającym — także „<" z tekstu
# ujednoliconego: „<6) udostępnianie…" to punkt 6 w brzmieniu przyszłym, nie odsyłacz do przypisu 6
# „±"/„–"/„-"/„+"/„/" przed cyframi to liczba w nawiasie, nie odsyłacz: „50 (+/–5) km/godz.", „(art. 5–7)"
_PDF_ODSYLACZ = re.compile(r"(?<=[^\s\d\[(„\"'«<±–+/-])(\d{1,3})\)")
_PDF_FRAZY_PRZYPISU = ("przez art.", "weszła w życie", "wszedł w życie", "wchodzi w życie", "Dodany ",
                       "W brzmieniu", "Uchylony", "Ze zmianą", "Zmiany tekstu", "odnośniku", "Obecnie",
                       "kieruje działem", "wdraża dyrektyw")
# początek NOWEGO akapitu (jednostki redakcyjnej) — tylko to nie jest doklejane do poprzedniej linii
# „[" / „<" przed jednostką: tekst ujednolicony Kancelarii Sejmu (PDF typu U) otacza brzmienie zastępowane
# nawiasem kwadratowym, a brzmienie, które dopiero wejdzie w życie — ostrym („[§ 1[1]. …]", „<§ 1[1]. …>")
# Numer punktu/litery bywa sklejony z odsyłaczem do przypisu („2b)25) przeciwdziałać…", „6)14) przebieg…" — k.p. DU 2026/1245;
# 13.11.2026): bez `(?:\d{1,3}\))?` taki punkt doklejał się do poprzedniego („…tempie; 2b) przeciwdziałać…").
_PDF_NOWY_AKAPIT = re.compile(
    r"^[„\"\[<]?(Art\.\s*\d|§\s*\d|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|Księga\s|KSIĘGA\s|"
    r"Oddział\s|ODDZIAŁ\s|Załącznik|Część\s|CZĘŚĆ\s|Preambuła|PREAMBUŁA|\d+[a-z]?\)(?:\u2063?\d{1,3}\))?\s|[a-z]\)(?:\u2063?\d{1,3}\))?\s|–\s|"
    r"\d+[a-z]?\.\s)")
# jednostka na LEWYM marginesie (pkt/lit./tiret mają wysunięty numer; „Art."/„§" bywają niewcięte)
_PDF_JEDNOSTKA = re.compile(
    r"^[\[<]?(Art\.\s*\d|§\s*\d|\d+[a-z]?\)(?:\u2063?\d{1,3}\))?\s|[a-z]\)(?:\u2063?\d{1,3}\))?\s|–\s|Rozdział\s|ROZDZIAŁ\s|Dział\s|"
    r"DZIAŁ\s|Tytuł\s|TYTUŁ\s|Księga\s|KSIĘGA\s|Oddział\s|Załącznik)")
_PDF_WCIECIE_NAGLOWKA = 16   # krótki wiersz wcięty co najmniej tyle = wyśrodkowany nagłówek (DZIAŁ IV)
_PDF_MAKS_NAGLOWEK = 70


def pdftotext_dostepny():
    return shutil.which("pdftotext") is not None


def _pdftotext(args, pdf_bytes):
    """`pdftotext <args> plik -` na bajtach PDF → tekst (pusty napis przy awarii)."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp = f.name
        r = subprocess.run(["pdftotext", *args, tmp, "-"], capture_output=True, timeout=180)
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def pdf_do_tekstu_layout(pdf_bytes):
    """`pdftotext -layout` na bajtach PDF → surowy tekst (pusty napis przy awarii)."""
    return _pdftotext(["-layout", "-enc", "UTF-8"], pdf_bytes)


_PDF_SLOWO = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>')


def _pdf_slowa(bbox_html, obrocone=None):
    """Wynik `pdftotext -bbox` → [(szerokość, wysokość, [(x0, y0, x1, y1, słowo)])] dla każdej strony. Strona z tekstem obróconym
    o 90° jest we współrzędnych kierunku czytania (`_pdf_obroc`); `obrocone` (dict) dostaje wtedy {nr strony: dolna krawędź
    poziomego nagłówka strony w pierwotnych współrzędnych albo None}."""
    strony = []
    for kawalek in bbox_html.split("<page ")[1:]:
        w = re.search(r'width="([\d.]+)" height="([\d.]+)"', kawalek)
        if not w:
            return []
        slowa = [(float(a), float(b), float(c), float(d), _unescape(t))
                 for a, b, c, d, t in _PDF_SLOWO.findall(kawalek)]
        strona = _pdf_obroc(float(w.group(1)), float(w.group(2)), slowa)
        if strona[2] is not slowa and obrocone is not None:
            obrocone[len(strony)] = _pdf_naglowek_poziomy(float(w.group(2)), slowa)
        strony.append(strona)
    return strony


def _pdf_naglowek_poziomy(h, slowa):
    """Strona obrócona: dolna krawędź poziomego nagłówka strony („Dziennik Ustaw – 40 – Poz. 615", górne 10% strony) albo None.
    `-layout` wstawia jego słowa między wiersze obróconej treści („[przypis 21)] Poz. 615 Ze zmianą…" — DU 2025/615 s. 40), więc
    taka strona jest czytana od tej krawędzi w dół. Gdy pod tą krawędzią zaczyna się jakiekolwiek inne słowo (koniec obróconego
    wiersza sięga nagłówka) — None: strona zostaje w całości."""
    kotwice = [s for s in slowa if s[3] < 0.1 * h and re.fullmatch(r"Dziennik|Monitor|Poz\.", s[4])]
    if not kotwice:
        return None
    g, d = min(s[1] for s in kotwice), max(s[3] for s in kotwice)
    naglowek = [s for s in slowa if s[1] >= g - 1 and s[3] <= d + 1]
    if any(s[1] < d + 1 and s not in naglowek and s[4].strip("﻿ ") for s in slowa):
        return None
    return d


def _pdf_obroc(w, h, slowa):
    """Strona z tekstem obróconym o 90° (tabela w poziomie na stronie pionowej: DU 2025/615 s. 31–40 i 103–116) → te same słowa
    we współrzędnych KIERUNKU CZYTANIA: x wzdłuż wiersza, y w dół wierszy. `-layout` czyta taką stronę poprawnie (obraca ją), ale
    współrzędne z `-bbox` są pionowe, więc testy indeksu górnego i odsyłacza („mniejsze i podniesione") nie działały: „3.4" +
    odsyłacz „21)" (s. 40 PDF) zostawał w `-layout` jako „3.421)", a dawna reguła kształtu brała „421)" za odsyłacz — „3." w treści
    i „przypis 421) bez treści" (13.11.2026; obraz strony). Strona jest obrócona, gdy co najmniej połowa słów z ≥3 znaków jest
    pionowa (wysokość > 1,5 × szerokość), a takich słów jest co najmniej 10. Kierunek (w górę / w dół) — z kolejności słów `-bbox`
    w wierszu. Słowa poziome na takiej stronie (nagłówek „Dziennik Ustaw – 40 – Poz. 615") też są przeliczane — nie należą do
    wierszy treści. Zwraca (szerokość, wysokość, słowa) — dla strony obróconej z zamienionymi wymiarami."""
    dlugie = [s for s in slowa if len(s[4]) >= 3]
    pionowe = {id(s) for s in dlugie if (s[3] - s[1]) > 1.5 * (s[2] - s[0])}
    if len(dlugie) < 10 or len(pionowe) < 0.5 * len(dlugie):
        return w, h, slowa
    w_gore = w_dol = 0
    for a, b in zip(slowa, slowa[1:]):
        if id(a) in pionowe and id(b) in pionowe and abs(a[0] - b[0]) < 2:
            if b[1] < a[1]:
                w_gore += 1
            else:
                w_dol += 1
    if w_gore >= w_dol:     # czytane od dołu do góry: góra litery na lewo (mniejsze x)
        return h, w, [(h - s[3], s[0], h - s[1], s[2], s[4]) for s in slowa]
    return h, w, [(s[1], w - s[2], s[3], w - s[0], s[4]) for s in slowa]


_PDF_DATA_WYDRUKU = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PDF_INDEKS = re.compile(r"(\d+[a-z]*)(\.?)$")


def _pdf_indeks_gorny(a, b):
    """Czy słowo `b` z bbox to indeks górny doklejony do liczby `a` („13" + „1" = art. 13¹)? Stoi tuż za nią,
    jest wyraźnie niższe i podniesione. `-layout` skleja je w „131", więc bez tego art. 13¹ udaje art. 131."""
    return (re.fullmatch(r"\d+[a-z]*", a[4]) is not None and _PDF_INDEKS.match(b[4]) is not None
            and b[4][:1].isdigit() and abs(b[0] - a[2]) < 0.6
            and (b[3] - b[1]) < 0.8 * (a[3] - a[1]) and b[1] < a[1] - 0.3)


def _pdf_linie(slowa, tol=2.5):
    """Słowa z bbox → wiersze [(y, [słowa od lewej])]; indeks górny (nieco wyżej) zostaje w swoim wierszu."""
    linie = []
    for s in sorted(slowa, key=lambda s: (s[1], s[0])):
        if linie and s[1] - linie[-1][0] <= tol:
            linie[-1][1].append(s)
        else:
            linie.append([s[1], [s]])
    # y wiersza = najniższe y0 jego słów: podniesiony indeks górny, który otworzył wiersz, nie zawyża odstępu
    return [(max(s[1] for s in ws), sorted(ws, key=lambda s: s[0])) for _, ws in linie]


def _pdf_tekst_wiersza(ws):
    """Słowa jednego wiersza (od lewej) → tekst; indeks górny jako „[1]", jak w PDF z nawiasami („125[1]")."""
    tekst, prev = "", None
    for s in ws:
        if prev is not None and _pdf_indeks_gorny(prev, s):
            m = _PDF_INDEKS.match(s[4])
            tekst += f"[{m.group(1)}]{m.group(2)}"
        elif prev is not None and (s[4].startswith("[") and s[0] - prev[2] < 1.5
                                   or re.fullmatch(r"[.,;:)]+", s[4]) and s[0] - prev[2] < 0.6):
            tekst += s[4]       # „[1]" albo kropka tuż za indeksem górnym („13[1].")
        else:
            tekst += (" " if tekst else "") + s[4]
        prev = s
    return tekst


def _pdf_oznacz_indeksy(tekst, slowa, w_miejscu=False):
    """Strona z `pdftotext -layout` → indeksy górne sklejone z liczbą („Art. 131." = art. 13¹) oznaczone jak
    w PDF z nawiasami („Art. 13[1]."). Pary (liczba, indeks) pochodzą z bbox tej strony (`slowa` — tylko te,
    które są w tekście strony); w tekście są szukane po kolei, razem ze słowem poprzedzającym w wierszu.

    `w_miejscu` (zeszyt Dz.U./M.P. 2000–2011 w dwóch łamach, DU 2008 poz. 288: „art. 2221 § 3" = art. 222¹): indeks
    z samych cyfr zamieniany na znaki indeksu górnego tej samej długości („222¹") — wstawione „[1]" przesunęłoby prawy
    łam względem rynny, którą dzieli stronę `_pdf_lamy`. Kolejność słów bbox (łam po łamie) nie jest kolejnością
    wierszy `-layout` (łamy obok siebie), więc każda para jest szukana od początku strony."""
    pos = 0
    for i in range(1, len(slowa)):
        a, b = slowa[i - 1], slowa[i]
        if not _pdf_indeks_gorny(a, b):
            continue
        idx = _PDF_INDEKS.match(b[4]).group(1)
        if w_miejscu and not idx.isdigit():
            continue
        prev = slowa[i - 2] if i >= 2 and abs(slowa[i - 2][1] - a[1]) < 3 and 0 <= a[0] - slowa[i - 2][2] < 20 else None
        wzor = re.compile((re.escape(prev[4]) + "[ ]+" if prev else r"(?:^|(?<=[\s(„]))")
                          + "(" + re.escape(a[4]) + ")" + re.escape(idx) + r"(?![\d\[])", re.M)
        m = wzor.search(tekst, 0 if w_miejscu else pos)
        if not m:
            continue
        if w_miejscu:
            tekst = tekst[:m.end(1)] + idx.translate(_NA_SUP) + tekst[m.end(1) + len(idx):]
            continue
        tekst = tekst[:m.end(1)] + f"[{idx}]" + tekst[m.end(1) + len(idx):]
        pos = m.end(1) + len(idx) + 2
    return tekst


def _pdf_rozstrzelone(tekst, slowa):
    """Rozstrzelony tytuł („M IN I S TR A F IN AN SÓ W I G O SPO DA RK I 1)", MP 2025 726): `-layout` rozbija go na
    kawałki po 1–3 litery z pojedynczą spacją, więc granice wyrazów widać tylko we współrzędnych — odstęp liter
    to ≈1 pt, a między wyrazami ≈4 pt. Taki wiersz strony zastępujemy „MINISTRA FINANSÓW I GOSPODARKI1)".
    Zwykły wiersz wersalikami ma między wyrazami normalne spacje (mediana odstępów duża) — zostaje bez zmian."""
    for _, ws in _pdf_linie(slowa, 2):
        tok = [s[4] for s in ws]
        odsylacz = re.fullmatch(r"\d{1,3}\)", tok[-1]) is not None
        litery = ws[:-1] if odsylacz else ws
        if len(litery) < 4 or not all(s[4].isalpha() and s[4].isupper() for s in litery) \
                or sum(len(s[4]) <= 2 for s in litery) < 0.6 * len(litery):
            continue
        wys = litery[0][3] - litery[0][1]
        przerwy = [b[0] - a[2] for a, b in zip(litery, litery[1:])]
        mala = sorted(przerwy)[len(przerwy) // 2]
        if mala > 0.2 * wys:
            continue
        granica = max(2.5 * mala, 0.25 * wys)
        out = litery[0][4] + "".join((" " if p > granica else "") + s[4] for p, s in zip(przerwy, litery[1:]))
        if odsylacz:
            out += ("" if ws[-1][0] - litery[-1][2] <= granica else " ") + tok[-1]
        wzor = re.compile(r"(?m)^([ \f]*)" + r"[ ]+".join(re.escape(t) for t in tok) + r"[ ]*$")
        tekst = wzor.sub(lambda m: m.group(1) + out, tekst, count=1)
    return tekst


def _pdf_prawa_krawedz(strony):
    """Prawa krawędź justowanego tekstu (x): najczęstszy koniec wiersza „prozy" — co najmniej 4 słowa od lewego
    marginesu, bez szerokich przerw. Wiersze tabel (kolumny liczb kończące się w jednym miejscu, DU 2025 1154)
    tego warunku nie spełniają, więc nie przesuwają krawędzi; notki z marginesu też nie (zaczynają się z prawej)."""
    konce, wiersze = {}, {}
    for w, h, slowa in strony:
        for _, ws in _pdf_linie(slowa, 3):
            if len(ws) >= 4 and ws[0][0] < 0.25 * w and ws[-1][2] > 0.6 * w:
                k = round(ws[-1][2])
                wiersze[k] = wiersze.get(k, 0) + 1
                if max(b[0] - a[2] for a, b in zip(ws, ws[1:])) < 12:
                    konce[k] = konce.get(k, 0) + 1
    konce = konce or wiersze
    if not konce:
        for w, h, slowa in strony:
            for s in slowa:
                if s[2] > 0.6 * w:
                    konce[round(s[2])] = konce.get(round(s[2]), 0) + 1
    return max(konce, key=konce.get) if konce else None


def _pdf_margines(strony):
    """Notki z prawego marginesu tekstu ujednoliconego (PDF typu U) wg WSPÓŁRZĘDNYCH słów.

    Zwraca (próg x, {nr strony: (szerokość przycięcia, {k: [notka]})}, info). Margines = słowa zaczynające się
    za prawą krawędzią tekstu (`_pdf_prawa_krawedz`). Notka to „pudełko" wierszy o wspólnym lewym brzegu, co
    najwyżej jeden odstęp wiersza od siebie — dwie notki, które na siebie nachodzą, mają różne brzegi (DU 2026
    622, art. 297); pudełko bez frazy notki tuż pod notką to jej ogon („(ust. 1 … wszedł w życie)"). Notka trafia
    do k-tego niepustego wiersza strony (licząc od 1), obok którego się zaczyna. Strona jest w słowniku, gdy da się
    ją przyciąć tuż przed kolumną notek bez utraty treści (inna treść za tym miejscem, np. szeroka tabela — nie).
    `info`: „podstawa" (Opracowano na podstawie…), „data_wydruku"."""
    info = {}
    if not strony:
        return None, {}, info
    szerokosci = {}
    for w, _, _ in strony:
        szerokosci[round(w)] = szerokosci.get(round(w), 0) + 1
    szer = max(szerokosci, key=szerokosci.get)    # strony poziome (załączniki) mają inny układ — bez notek
    krawedz = _pdf_prawa_krawedz([s for s in strony if round(s[0]) == szer])
    if krawedz is None:
        return None, {}, info
    prog = krawedz + 0.2
    out, ostatnia = {}, None      # ostatnia: (nr strony, lista notek wiersza) — ostatnia notka może ciągnąć się dalej
    for nr, (w, h, slowa) in enumerate(strony):
        for s in slowa:
            if s[1] > 0.9 * h and _PDF_DATA_WYDRUKU.match(s[4]):
                info.setdefault("data_wydruku", s[4])
        if round(w) != szer:
            continue
        # data wydruku stoi w stopce, czasem tuż obok notki — nie jest jej częścią
        brzeg = [s for s in slowa if s[0] >= prog and 0.05 * h < s[1] < 0.995 * h and not _PDF_DATA_WYDRUKU.match(s[4])]
        if not brzeg:
            continue
        pudla = []      # [x0, y pierwszego wiersza, y ostatniego, [teksty wierszy], [słowa]]
        for y, ws in _pdf_linie(brzeg):
            p = next((p for p in reversed(pudla) if abs(p[0] - ws[0][0]) <= 2 and 0 < y - p[2] <= 13.5), None)
            if p is None:
                p = [ws[0][0], y, y, [], []]
                pudla.append(p)
            p[2] = y
            p[3].append(_pdf_tekst_wiersza(ws))
            p[4].extend(ws)
        # pudełko bez frazy tuż NAD pudełkiem z frazą (ten sam brzeg) to początek tej samej notki
        for i in range(len(pudla) - 2, -1, -1):
            a, b = pudla[i], pudla[i + 1]
            if abs(a[0] - b[0]) <= 2 and 0 < b[1] - a[2] <= 16 and not _PDF_NOTKA.search(" ".join(a[3])) \
                    and _PDF_NOTKA.search(" ".join(b[3])):
                pudla[i:i + 2] = [[a[0], a[1], b[2], a[3] + b[3], a[4] + b[4]]]
        grupy, obce, ciag = [], [], []
        for x0, y0, y1, wiersze, ws in pudla:
            t = " ".join(wiersze)
            g = grupy[-1] if grupy else None
            if _PDF_NOTKA.search(t):
                grupy.append([x0, y0, y1, t, ws])
            elif g is not None and abs(g[0] - x0) <= 2 and y0 - g[2] <= 30:
                g[2], g[3], g[4] = y1, g[3] + " " + t, g[4] + ws      # ogon notki
            elif not grupy and ostatnia is not None and ostatnia[0] == nr - 1 and not obce \
                    and not re.search(r"[.)]$", ostatnia[1][-1]) and y0 < 0.2 * h:
                ostatnia[1][-1] += " " + t      # notka przechodzi z poprzedniej strony
                ostatnia, ciag = None, ws
            else:
                obce.extend(ws)
        if not grupy and not ciag:
            continue
        przyciecie = int(min(s[0] for s in ciag + [s for g in grupy for s in g[4]]) - 0.05)
        if any(s[2] > przyciecie + 0.5 for s in obce):
            # przycięcie zgubiłoby inną treść za kolumną notek — strona zostaje w całości, a notkę oddziela tylko reguła odstępów
            # w wierszu (`_pdf_notki_marginesu`); słowa notki i treści mogą się przemieszać (VAT DU 2026/1263 s. 414: „Dz. Urz.
            # poz. UE1270). L 48" w przypisie 1) — `pdf_do_tekstu_z_notkami` oznacza taką stronę jako obszar niepewny
            if grupy:
                info.setdefault("nieprzyciete", []).append((nr, " ".join(grupy[0][3].split()[:6])))
            continue
        wiersze_y = []
        for y in sorted(s[1] for s in slowa if s[0] < przyciecie):
            if not wiersze_y or y - wiersze_y[-1] > 3:
                wiersze_y.append(y)
        strona = {}
        for _, y0, _, t, _ in grupy:
            if "Opracowano na" in t:
                info.setdefault("podstawa", t[t.index("Opracowano na"):])
                strona.setdefault(0, [])
                continue
            k = max(1, sum(1 for wy in wiersze_y if wy <= y0 + 2))
            strona.setdefault(k, []).append(t)
            ostatnia = (nr, strona[k])
        out[nr] = (przyciecie, strona)
    return prog, out, info


def _pdf_notki_z_bbox(strony):
    """Jak `_pdf_margines`, ale notki bez szerokości przycięcia: (próg x, {nr strony: {k: [notka]}}, info)."""
    prog, out, info = _pdf_margines(strony)
    return prog, {nr: strona for nr, (_, strona) in out.items()}, info


# Ukryta warstwa tekstu (DU 2025/615, tabele w załącznikach; 13.11.2026, współrzędne `-bbox`, strumień strony i obraz s. 10, 40,
# 63): strona tabeli to osadzony PDF (InDesign „PlacedPDF") z WŁASNYMI przypisami i nagłówkami załączników, przycięty ramką —
# na s. 10 ścieżka przycinająca kończy się na y ≈ 748 pt, a przypis „9) Ze zmianą…" osadzonego PDF stoi na y = 757 (słowa wysokie na 13,7 pt).
# Na obrazie strony go nie ma; widać tylko przypis złożony od nowa przez InDesign na y = 783 (szeryfowy, 12,1 pt). `pdftotext` nie
# uwzględnia przycięcia, więc daje oba: „9)" ⏎ „9)" ⏎ „Ze zmianą…" ⏎ „9)" ⏎ „Ze zmianą…". Blok przypisów nie był rozpoznawany
# i treść przypisu szła do tabeli (28 miejsc „N) … w treści przepisu" na 118 stronach). Nagłówki („Załącznik nr 5",
# „WYKAZ ŚWIADCZEŃ…") są w obu warstwach złamane inaczej i `-layout` przeplata je w jeden wiersz.
_PDF_NUMER_PRZYPISU = re.compile(r"\d{1,3}\)")


def _pdf_ukryta_warstwa(slowa):
    """Słowa bbox strony → (zdublowane przypisy [(numer, treść)], inne zdublowane wiersze [tekst]).

    Przypis zdublowany: wiersz „N) treść" (numer bywa powtórzony: „9) 9) Ze zmianą…") stoi na stronie co najmniej dwa razy z tą
    samą treścią (≥ 10 znaków), w innej wielkości czcionki (różnica > 0,5 pt i > 5% — osadzony PDF ma inny krój). Ten sam wiersz
    „N) …" tej samej wielkości to zwykle punkt wyliczenia powtórzony w kolejnych wierszach tabeli (DU 2025/615 s. 88: „3) sprawdzenie
    toru oddychania;" dwa razy — oba widoczne) i zostaje. Inny zdublowany wiersz: ten sam tekst (≥ 2 słowa, ≥ 10 znaków) dwa razy,
    w innej wielkości czcionki i od innego miejsca (x różni się o > 1 pt) — powtórzone hasło w kolejnych wierszach tabeli ma ten
    sam początek wiersza (VAT DU 2017/1221 s. 310: „przeznaczone na nieodpłatne lub odpłatne" w poz. 73 i 74 — oba widoczne, ale
    w wierszach o innej wysokości)."""
    def wysokosc(ws):
        wys = sorted(s[3] - s[1] for s in ws)
        return wys[len(wys) // 2]

    def rozne(a, b):
        return abs(a - b) > 0.5 and max(a, b) > 1.05 * min(a, b)
    przypisy, wiersze = {}, {}
    for _, ws in _pdf_linie(slowa, 2.5):
        k = 0
        while k < len(ws) and _PDF_NUMER_PRZYPISU.fullmatch(ws[k][4]):
            k += 1
        reszta = " ".join(s[4] for s in ws[k:])
        if k and len({s[4] for s in ws[:k]}) == 1 and len(reszta) >= 10:
            przypisy.setdefault((ws[0][4][:-1], reszta), []).append(wysokosc(ws[k:]))
        tekst = " ".join(s[4] for s in ws)
        if len(ws) >= 2 and len(tekst) >= 10:
            wiersze.setdefault(tekst, []).append((wysokosc(ws), ws[0][0]))
    zdublowane = [k for k, v in przypisy.items() if any(rozne(a, b) for a in v for b in v)]
    inne = []
    for tekst, v in wiersze.items():
        if any(rozne(a[0], b[0]) and abs(a[1] - b[1]) > 1 for a in v for b in v) \
                and not any(tekst.endswith(t) for _, t in zdublowane):
            inne.append(tekst)
    return zdublowane, inne


def _pdf_bez_ukrytych_przypisow(tekst, slowa):
    """Strona z `-layout` + słowa bbox → (strona bez ukrytych kopii przypisu, [opisy zdublowanych miejsc, których nie usunięto]).

    Z każdego przypisu zdublowanego (`_pdf_ukryta_warstwa`) zostaje OSTATNIA kopia w `-layout` — przypis widoczny stoi na samym dole
    strony, pod kopią ukrytą; obie mają tę samą treść, więc żadne słowo nie ginie. Usuwane są też samotne numery „N)" między ostatnim
    wierszem treści a zostawioną kopią, poza ostatnim przed nią, gdy sama kopia jest bez numeru. Przypis, którego kopii nie da się
    wskazać w `-layout` jako całych wierszy (np. złamany inaczej w dwóch warstwach), i inne zdublowane wiersze (nagłówki) zostają
    bez zmian — wracają jako opisy do ostrzeżenia."""
    zdublowane, inne = _pdf_ukryta_warstwa(slowa)
    if not zdublowane:
        return tekst, inne
    lines = tekst.split("\n")
    for n, t in zdublowane:
        kopie = [i for i, l in enumerate(lines) if l is not None and " ".join(l.split()) in (t, n + ") " + t)]
        if len(kopie) < 2:
            inne.append(f"{n}) {t}")
            continue
        ost = kopie[-1]
        for i in kopie[:-1]:
            lines[i] = None
        sam = re.compile(r"^\s*" + n + r"\)\s*$")
        samotne, i = [], ost - 1
        while i >= 0 and (lines[i] is None or not lines[i].strip() or sam.match(lines[i])):
            if lines[i] is not None and sam.match(lines[i]):
                samotne.append(i)
            i -= 1
        if samotne and " ".join(lines[ost].split()) == t:
            samotne = samotne[1:]       # numer widocznej kopii stoi wiersz wyżej (pierwszy od dołu)
        for i in samotne:
            lines[i] = None
    return "\n".join(l for l in lines if l is not None), inne


def pdf_do_tekstu_z_notkami(pdf_bytes, zeszyt=False, indeksy=True):
    """Jak `pdf_do_tekstu_layout`, ale strony z notkami na prawym marginesie (tekst ujednolicony
    Kancelarii Sejmu) są czytane PRZYCIĘTE tuż przed kolumną notek, a notki wracają osobno. Indeksy górne
    sklejone przez `-layout` z liczbą („Art. 131.") są oznaczone jak w PDF z nawiasami („Art. 13[1].").
    `zeszyt` (PDF zeszytu Dz.U./M.P. 1990–2011, dwa łamy): bez notek, a indeks górny jako znak indeksu tej samej
    długości („222¹" — `_pdf_oznacz_indeksy(w_miejscu=True)`), żeby nie przesunąć łamów; `indeksy=False` (OCR skanów
    z lat 90.: współrzędne słów z OCR są niepewne) — bez oznaczania indeksów.

    Zwraca (surowy tekst, {nr strony: {k: [notka]}}, info) — patrz `_pdf_margines`.

    Gdy `-layout` dał tekst, a `-bbox` nie dał tyle samo stron (awaria `pdftotext -bbox` — `_pdftotext` zwraca wtedy pusty
    napis), indeksy górne i odsyłacze NIE są oznaczane (strony mogłyby się rozjechać), a `info` dostaje
    „bez_wspolrzednych" = (stron bbox, stron -layout). Działa wtedy dawna reguła kształtu `_PDF_ODSYLACZ`, która zjada cyfry
    liczby przed przypisem („152,61" + „3)" → „152,") — dotąd po cichu; `cmd_tekst` ostrzega, a `--strict` blokuje."""
    raw = pdf_do_tekstu_layout(pdf_bytes)
    if not raw or zeszyt and not indeksy:
        return raw, {}, {}
    obrocone = {}
    strony = _pdf_slowa(_pdftotext(["-bbox", "-enc", "UTF-8"], pdf_bytes), obrocone)
    caly = raw.split("\f")
    stron_layout = len(caly) - 1 if raw.endswith("\f") else len(caly)
    if len(strony) != stron_layout:
        info = {"bez_wspolrzednych": (len(strony), stron_layout)}
        return raw, {}, info
    if zeszyt:
        for nr, (_, _, slowa) in enumerate(strony):
            caly[nr] = _pdf_oznacz_indeksy(caly[nr], slowa, w_miejscu=True)
        return "\f".join(caly), {}, {}
    for nr, y in obrocone.items():
        if y is not None:       # strona obrócona — bez poziomego nagłówka (`_pdf_naglowek_poziomy`)
            h, w = strony[nr][0], strony[nr][1]
            ciete = _pdftotext(["-layout", "-enc", "UTF-8", "-f", str(nr + 1), "-l", str(nr + 1), "-x", "0", "-y",
                                str(int(y) + 1), "-W", str(int(w) + 1), "-H", str(int(h - y) + 1)], pdf_bytes)
            if ciete.strip():
                caly[nr] = ciete.rstrip("\f")
    _, marg, info = _pdf_margines(strony)
    for nr, notka in info.pop("nieprzyciete", []):
        info.setdefault("strony_flagi", {}).setdefault(nr, []).append(("notka", notka))
    notki, przyciecie = {}, {}
    if marg:
        wys = max(h for _, h, _ in strony)
        for szer in sorted({p for p, _ in marg.values()}):
            ciete = _pdftotext(["-layout", "-enc", "UTF-8", "-x", "0", "-y", "0", "-W", str(szer),
                                "-H", str(int(wys) + 1)], pdf_bytes).split("\f")
            if len(ciete) != len(caly):
                continue
            for nr, (p, strona) in marg.items():
                if p == szer:
                    caly[nr], notki[nr], przyciecie[nr] = ciete[nr], strona, p
    for nr, (w, h, slowa) in enumerate(strony):
        na_stronie = [s for s in slowa if s[0] < przyciecie.get(nr, w + 1)]
        caly[nr], zdublowane = _pdf_bez_ukrytych_przypisow(caly[nr], na_stronie)
        if zdublowane:      # → `pdf_layout_do_tekstu`, obszar niepewny „warstwa"
            info.setdefault("strony_flagi", {}).setdefault(nr, []).append(("warstwa", zdublowane[0][:60]))
        caly[nr] = _pdf_oznacz_odsylacze(_pdf_rozstrzelone(_pdf_oznacz_indeksy(caly[nr], na_stronie), na_stronie),
                                         na_stronie)
    return "\f".join(caly), notki, info


# Notka na PRAWYM marginesie tekstu ujednoliconego Kancelarii Sejmu (PDF typu U): „Opracowano na
# podstawie: t.j. Dz. U. z 2026 r. poz. 468, 473, 830…" (strona 1) i przy każdej zmianie, która jeszcze
# nie weszła w życie w dniu wydruku: „Nowe brzmienie § 1[1] w art. 461 wejdzie w życie z dn. 5.11.2026 r.
# (Dz. U. z 2026 r. poz. 1046).". `-layout` dokleja ją do wierszy treści — rozpoznajemy ją po treści,
# bo justowany tekst też miewa szerokie odstępy („…   być"), których ruszać nie wolno. Fraza bywa w liczbie
# mnogiej („Dodane pkt 6 i 7 … wejdą w życie"), z wielkiej litery („Utraci moc…") i z literówką („wejdziei").
_PDF_NOTKA = re.compile(r"(?i)wejd\w* w życie|wchodz\w* w życie|utrac[ią] moc|trac[ią] moc|Opracowano na")
_PDF_NOTKA_KOLUMNA = 0.68   # notka zaczyna się dalej niż tyle szerokości strony
_PDF_NOTKA_ODSTEP = 6       # …i jest oddzielona od treści co najmniej tyloma spacjami


def _pdf_notki_marginesu(lines):
    """Wiersze strony → (wiersze bez notek marginesowych, {indeks wiersza: [treść notki]}).

    Notka = ciąg kolejnych wierszy z tekstem za kolumną `_PDF_NOTKA_KOLUMNA` (puste wiersze nie
    przerywają ciągu, wiersz treści bez takiego tekstu — tak) z frazą `_PDF_NOTKA`. Notka trafia do
    pierwszego wiersza treści, który z nią sąsiaduje, żeby wyszła pod właściwym akapitem."""
    niepuste = [l for l in lines if l.strip()]
    if not niepuste:
        return lines, {}
    prog = int(max(len(l.rstrip()) for l in niepuste) * _PDF_NOTKA_KOLUMNA)

    def margines(l):
        if not l.strip():
            return None
        if len(l) - len(l.lstrip(" ")) >= prog:
            return 0
        for m in re.finditer(r"\S( {%d,})(?=\S)" % _PDF_NOTKA_ODSTEP, l):
            if m.end() >= prog:
                return m.start() + 1
        return None
    cut = [margines(l) for l in lines]
    out, notki = list(lines), {}
    i = 0
    while i < len(lines):
        if cut[i] is None:
            i += 1
            continue
        j, grupa = i, []
        while j < len(lines) and (cut[j] is not None or not lines[j].strip()):
            if cut[j] is not None:
                grupa.append(j)
            j += 1
        tekst = " ".join(" ".join(lines[k][cut[k]:].split()) for k in grupa)
        if _PDF_NOTKA.search(tekst):
            for k in grupa:
                out[k] = lines[k][:cut[k]].rstrip()
            cel = next((k for k in grupa if out[k].strip()), None)
            if cel is None:
                cel = next((k for k in range(i - 1, -1, -1) if out[k].strip()), None)
            if cel is None:
                cel = next((k for k in range(j, len(lines)) if out[k].strip()), i)
            notki.setdefault(cel, []).append(tekst)
        i = j
    return out, notki


# Odsyłacz do przypisu to indeks górny: mniejsza czcionka, podniesiony. `-layout` gubi tę informację, więc
# `_PDF_ODSYLACZ` zgaduje go po kształcie „cyfry + )" — i myli się (13.11.2026, 40 losowych tekstów jednolitych 2025–2026
# i 18 kodeksów i ustaw; współrzędne słów z `-bbox`, obraz strony obejrzany dla DU 2026/218 s. 13 i DU 2025/1016 s. 9):
# - zabiera cyfry liczby: „0,01" + odsyłacz „2)" to w `-layout` „0,012)", a wynik miał „0," (DU 2025/1016 — najwyższe
#   dopuszczalne zawartości w paszach); „152,61" + „3)" → „152," (DU 2026/218 — kwota); „2.4" + „12)" → „2." (DU 2025/615);
# - bierze za odsyłacz zwykły tekst: „SpO2)," → „SpO," (DU 2026/1391), „(E561)" → „(E" (DU 2025/1016), „(WGS84)" → „(WGS";
# - nie widzi odsyłacza wydrukowanego w OSOBNYM wierszu nad wierszem, do którego należy: „                2)" nad
#   „Art. 179. (utracił moc)" (k.c. DU 2026/795) i „      14)" nad „6)          przebieg…" (k.p. DU 2026/1245, art. 22¹
#   § 1 pkt 6). Wiersz „2)" doklejał się do poprzedniego akapitu, a przypis z dołu strony — bez odsyłacza w treści i bez
#   frazy przypisu — szedł jako tekst do OSTATNIEGO przepisu strony: k.c. art. 182 § 2 kończył się przypisem o wyroku TK
#   K 9/04, który należy do art. 179; w k.p. art. 22¹ znikał przypis 14, a „14)" zostawało w pkt 5.
# Dlatego na stronach z warstwą tekstową odsyłacz bierzemy ze współrzędnych słów (`pdftotext -bbox`): osobne słowo „N)"
# (albo „N" + „)"), niższe od słowa tuż z lewej i podniesione — pdftotext zaczyna nowe słowo przy zmianie wielkości
# czcionki. Taki odsyłacz dostaje znacznik `_ODS_POTW` przed cyframi, więc `_PDF_ODSYLACZ` zabiera DOKŁADNIE jego cyfry.
# Kandydat, którego cyfry i „)" leżą w jednym słowie bbox razem z poprzedzającym znakiem („SpO2),", „(E561)"), to tekst
# o jednej wielkości czcionki — dostaje `_ODS_NIE` między cyfry a „)", więc zostaje w tekście. Gdy współrzędne milczą
# (brak pary słów, strona z OCR, zeszyt 1990–2011), działa dawna reguła `_PDF_ODSYLACZ`. Znaczniki znikają z wyniku
# w `pdf_layout_do_tekstu`.
_ODS_POTW = "\u2063"     # przed cyframi odsyłacza potwierdzonego współrzędnymi
_ODS_NIE = "\u2064"      # między cyframi a „)" — to zwykły tekst, nie odsyłacz
_PDF_SAM_ODSYLACZ = re.compile(r"^( *)(\d{1,3}\)) *$")


def _pdf_odsylacze_bbox(slowa):
    """Słowa bbox strony → [(słowo tuż z lewej, numer, czy to słowo jest odsyłaczem)] odsyłaczy-indeksów górnych, w kolejności
    słów bbox. Dwa odsyłacze obok siebie („§ 1." „6)" „7)" — k.p.c. DU 2026/468 art. 49, w `-layout` „§ 1.6) 7) Niezależnie"):
    drugi stoi za pierwszym, a nie za wyższym słowem — dotąd „7)" zostawało w tekście i udawało punkt 7.

    Drugi odsyłacz: ta sama wysokość co pierwszy (±0,5 pt), ta sama góra (±1 pt), przerwa do 12 pt. Odstęp bywa szeroki
    w wierszu justowanym: k.p.c. art. 1 („Art. 1." „2)" „3)" „Kodeks…") — 5,84 pt, a próg 5 pt zostawiał „3)" w treści
    („Art. 1. 3) Kodeks…"), przypis 3 (wyrok TK K 10/17) był bez odsyłacza i lądował przy art. 2. 13.11.2026, 30 PDF
    Dz.U. 2025–2026 (głównie t.j.): par „N)" „N)" tej samej wysokości na jednej linii są dwie (3,44 i 5,84 pt, obie
    w k.p.c.); „7)" „7)" na dole strony DU 2026/1044 s. 4 (14,76 pt, wysokości 7,88 i 8,73) to nie para. Warunek
    wysokości odrzuca też punkt „1)" zwykłą czcionką tuż za odsyłaczem (górę ma prawie tę samą)."""
    return [x[:3] for x in _pdf_odsylacze_bbox_pelne(slowa)]


def _pdf_odsylacze_bbox_pelne(slowa):
    """Jak `_pdf_odsylacze_bbox`, ale z dwoma polami więcej:
    - czy słowo z lewej jest w PDF sklejone ze słowem PRZED nim (przerwa < 0,6 pt w tym samym wierszu). Tak jest z kropką za
      indeksem górnym: k.p. DU 2026/1245 s. 95 „Art." „302" „[1]" „." „62)" — pdftotext zaczyna nowe słowo przy każdej zmianie
      wielkości czcionki, więc „." jest osobnym słowem, a w `-layout` stoi w „302[1].62)" bez spacji przed sobą;
    - czy NIŻEJ na stronie stoi numer przypisu N: słowo „N)" albo „N" mniejsze od słowa z lewej odsyłacza (przypisy są
      składane mniejszą czcionką: „15)" na dole DU 2026/1473 s. 51 ma 6,6 pt, tekst 11 pt). Punkt wyliczenia „1)" zwykłą
      czcionką tego warunku nie spełnia — DU 2014/900 s. 1: „innych ustaw 1), 2)" nad punktem „1) w art. 2:".
    Oba pola służą `_pdf_oznacz_odsylacze`."""
    out, potw = [], set()
    for k, o in enumerate(slowa):
        # „1)," — odsyłacz z przecinkiem przed następnym odsyłaczem (k.p.c. DU 2021/1805 s. 1: „Art. 1." „1)," „2)" — dotąd „2)"
        # zostawało w tekście: „Art. 1., 2) Kodeks…", a przypis 2 był „bez odsyłacza")
        mm = re.fullmatch(r"(\d{1,3})(\),?)?", o[4])
        if not mm:
            continue
        gora, dol = o[1], o[3]
        if not mm.group(2):     # „23" + „)" jako dwa słowa (DU 2025/609 s. 10: „karnego" „23" „)")
            nast = slowa[k + 1] if k + 1 < len(slowa) else None
            if not (nast and nast[4].startswith(")") and -1 < nast[0] - o[2] < 1 and abs(nast[1] - o[1]) < 3):
                continue
            dol = max(dol, nast[3])
        wys = dol - gora
        lewe = [w for w in slowa if w is not o and abs(w[1] - gora) < 6 and -1.5 < o[0] - w[2] < 4
                and wys < 0.85 * (w[3] - w[1]) and dol < w[3] - 0.5]
        sklejone = przypis_nizej = False
        if lewe:
            lw = min(lewe, key=lambda w: abs(o[0] - w[2]))
            slowo, za_odsylaczem = lw[4], False
            sklejone = any(p is not lw and abs(p[1] - lw[1]) < 3 and -0.5 < lw[0] - p[2] < 0.6 for p in slowa)
            przypis_nizej = any(w[4] in (mm.group(1), mm.group(1) + ")") and w[1] > dol + 5
                                and (w[3] - w[1]) < 0.85 * (lw[3] - lw[1]) for w in slowa)
        else:
            # za innym odsyłaczem tej samej wysokości (przerwa do 12 pt — patrz wyżej)
            lewe = [w for w in slowa if id(w) in potw and abs(w[1] - gora) < 1 and abs((w[3] - w[1]) - wys) < 0.5
                    and -1.5 < o[0] - w[2] < 12]
            # „2)," w środku trzech odsyłaczy „1), 2), 3)" też jest odsyłaczem — dotąd tylko „2)" bez przecinka, więc „3)" nie miało
            # odsyłacza z lewej i zostawało w tekście („Art. 1. 3) Kodeks…"), a przypis 3 był „bez odsyłacza"
            if not lewe or not o[4].rstrip(",").endswith(")"):
                continue
            slowo, za_odsylaczem = lewe[0][4], True
        # jak w `_PDF_ODSYLACZ`: po cudzysłowie, nawiasie, „<", „±", myślniku i ukośniku nie usuwamy — obwieszczenie t.j.
        # cytuje przypis z jego numerem („które stanowią: „2) Tekst Programu…" — DU 2025/151)
        if slowo[-1:] not in "[(„\"'«<±–+/-":
            out.append((slowo, mm.group(1), za_odsylaczem, sklejone, przypis_nizej))
            potw.add(id(o))
    return out


def _pdf_spacja_przed_odsylaczem(lines, i, n):
    """Czy odsyłacz „N)" oddzielony w `-layout` spacją od słowa w wierszu `i` ma pod sobą przypis: wiersz NIŻEJ na tej stronie
    zaczyna się od „N)" na marginesie (numer przypisu). Współrzędne mówią już, że „N)" jest mniejsze i podniesione — to drugi,
    niezależny warunek, żeby spacja nie zdjęła z tekstu niczego, co nie ma przypisu na tej stronie."""
    wzor = re.compile(r"^ {0,3}" + n + r"(?:\)(?:\s|$)| *$)")      # „N" sam w wierszu: numer rozbity na „N" ⏎ „ )" (DU 2014/900)
    return any(l is not None and wzor.match(l) for l in lines[i + 1:])


def _pdf_oznacz_odsylacze(tekst, slowa):
    """Strona z `-layout` + słowa bbox tej strony → strona z odsyłaczami oznaczonymi wg współrzędnych (patrz wyżej).
    Potwierdzony odsyłacz dostaje `_ODS_POTW` przed cyframi („moc)" + znacznik + „2)"); samotny wiersz z odsyłaczem nad
    wierszem jest doklejony do słowa, za którym odsyłacz stoi w PDF („6)" + znacznik + „14)    przebieg…"). Zaprzeczony
    kandydat dostaje `_ODS_NIE` między cyfry a „)". Każdy odsyłacz z bbox oznacza jedno miejsce w tekście, tylko tuż za
    swoim słowem (bez spacji). Samotny odsyłacz, którego słowa nie ma w następnym wierszu (przypis formularza w DU 2025/609
    stoi wiersz wyżej), i numer przypisu pod kreską (nie ma słowa z lewej) zostają bez zmian."""
    if not slowa:
        return tekst
    lines = tekst.split("\n")
    for slowo, n, za_odsylaczem, sklejone, przypis_nizej in _pdf_odsylacze_bbox_pelne(slowa):
        if za_odsylaczem:   # „6) 7)" → oba oznaczone, bez spacji między nimi (znika razem z odsyłaczami)
            # „1), 2)" → przecinek między odsyłaczami też znika („Art. 1. Kodeks…", nie „Art. 1., Kodeks…")
            wzory = [re.compile("(" + _ODS_POTW + re.escape(slowo.rstrip(",")) + ")" + ("," if slowo.endswith(",") else "")
                                + r" ?(" + n + r"\))")]
        else:
            wzory = [re.compile(r"(?<!\S)(" + re.escape(slowo) + r")(" + n + r"\))")]
            if sklejone and re.fullmatch(r"[.,;:)]+", slowo):
                # kropka za indeksem górnym: „Art. 302[1].62)" (k.p. DU 2026/1245 s. 95) — w bbox osobne słowo „.", w `-layout`
                # sklejone z „[1]"; dotąd odsyłacz 62 nie był potwierdzony, więc blok przypisów 60–62 tej strony nie był
                # rozpoznany i jego treść zostawała w art. 302¹ § 7. Tak samo nawias za odsyłaczem: „zm.1))2)" (DU 2026/55 s. 2)
                wzory.append(re.compile(r"(?<=\S)(" + re.escape(slowo) + r")(" + n + r"\))"))
            # spacja w `-layout`, której w PDF nie ma: „Pożarnej 15)" (DU 2026/1473 s. 51 — w bbox „15)" zaczyna się 0,33 pt za
            # „Pożarnej"); dotąd „15)" zostawało w tytule rozdziału, a przypis 15 był „bez odsyłacza". Tylko gdy niżej na stronie
            # stoi numer przypisu N mniejszą czcionką (bbox) i wiersz zaczynający się od „N)" (`_pdf_spacja_przed_odsylaczem`)
            if przypis_nizej:
                wzory.append(re.compile(r"(?<!\S)(" + re.escape(slowo) + r")( +)(?=(" + n + r"\)))"))
        for wzor in wzory:
            for i, l in enumerate(lines):
                m = wzor.search(l) if l is not None else None
                if m and m.re.groups == 3 and not _pdf_spacja_przed_odsylaczem(lines, i, n):
                    m = None
                if m:
                    lines[i] = l[:m.end(1)] + _ODS_POTW + l[m.end(2) if m.re.groups == 3 else m.start(2):]
                    break
            else:
                continue
            break
        else:
            if za_odsylaczem:
                continue
            for i in range(len(lines) - 1):
                m = _PDF_SAM_ODSYLACZ.match(lines[i] or "")
                nast = lines[i + 1] or ""
                if not (m and m.group(2) == n + ")" and nast.strip()
                        and len(m.group(1)) > len(nast) - len(nast.lstrip(" "))):
                    continue
                konce = [w.end() for w in re.finditer(r"(?<!\S)" + re.escape(slowo) + r"(?!\S)", nast)]
                if konce:
                    q = min(konce, key=lambda k: abs(k - len(m.group(1))))
                    lines[i], lines[i + 1] = None, nast[:q] + _ODS_POTW + n + ")" + nast[q:]
                    break
    lines = [l for l in lines if l is not None]
    teksty = [w[4] for w in slowa]
    for i, l in enumerate(lines):
        if not _PDF_ODSYLACZ.search(l):
            continue
        kawalki, poz = [], 0
        for m in _PDF_ODSYLACZ.finditer(l):
            if l[m.start() - 1] == _ODS_POTW:
                continue
            przed = re.search(r"\S{1,3}$", l[:m.start()]).group(0)
            if any(przed + m.group(0) in t for t in teksty):
                kawalki += [l[poz:m.end(1)], _ODS_NIE]
                poz = m.end(1)
        if kawalki:
            lines[i] = "".join(kawalki) + l[poz:]
    return "\n".join(lines)


def _pdf_punkt_obwieszczenia(blok, numery, tresc_strony):
    """Czy „blok przypisów" z dołu strony obwieszczenia Marszałka Sejmu to w rzeczywistości punkt jego wyliczenia („5) art. 48
    ustawy z dnia … (Dz. U. poz. 1123), który stanowi: „Art. 48. …”")? Pozycja listy przepisów przejściowych stoi na dole
    strony po pustym wierszu, zaczyna się od „N)" na marginesie i zawiera frazę przypisu („wchodzi w życie"), więc reguła
    bloku przypisów brała ją za przypis N. Dawny silnik gubił taki punkt bez śladu, nowszy drukował go jako „[przypis 5)]"
    i karmił nim listę „PRZEPISY, KTÓRE JESZCZE NIE OBOWIĄZUJĄ" (DU 2026/1473: fałszywe „od 2028-01-01: przepis z przypisem
    5)"). 13.11.2026, 23 PDF Dz.U. 2025–2026 (t.j. i obwieszczenia ministrów): tak było w DU 2026/795 (pkt 4), 1315
    (pkt 3), 1342 (pkt 4), 1395 (pkt 3), 1473 (pkt 5), w k.p. DU 2026/1245 (pkt 6 i podpis — ginęły bez śladu, bo na
    stronie nie było innej treści) oraz w DU 2025/1016 i 2026/1391 (pkt 4); prawdziwego przypisu nad „Załącznik do
    obwieszczenia" bez potwierdzonego odsyłacza w tej próbie nie było (przypis „1)" przy tytule ministra — wielką literą).

    Punkt wyliczenia: tekst pierwszego „N)" zaczyna się małą literą albo „§" (dalszy ciąg zdania „…przepisów: 1) art. …"),
    a żaden numer bloku nie ma na stronie odsyłacza potwierdzonego współrzędnymi. Przypis zaczyna się wielką literą
    („Zmiany tekstu jednolitego…", „Niniejsza ustawa…") albo ma potwierdzony odsyłacz."""
    pierwszy = next((m.group(2) or "" for w, t, _ in blok if w == 0 for m in [_PDF_PRZYPIS.match(t)] if m), "")
    if not (pierwszy[:1].islower() or pierwszy.startswith("§")):
        return False
    return not any(_ODS_POTW + n + ")" in tresc_strony for n in numery)


def _pdf_strona(page, pierwsza, info=None, notki_bbox=None, obwieszczenie=False, stopka=None, przypis_niedokonczony=False):
    """Jedna strona z `pdftotext -layout` → (linie treści [(wcięcie, tekst, notki)], przypisy {nr: treść}).

    Usuwa nagłówek („©Kancelaria Sejmu  s. 1/119", „Dziennik Ustaw – 22 – Poz. 795"), stopkę
    (znacznik daty „2026-06-22", numer strony), normalizuje margines strony i wydziela blok
    przypisów z dołu strony (po pustej linii; „3)   W brzmieniu ustalonym…" + wcięte kontynuacje).
    Notki z prawego marginesu (tekst ujednolicony) wychodzą osobno przy wierszu, obok którego stoją;
    „Opracowano na podstawie…" i data wydruku trafiają do `info` („podstawa", „data_wydruku").
    `notki_bbox` ({k: [notka]} z `_pdf_notki_z_bbox`; strona już przycięta do szerokości treści)
    zastępuje rozpoznawanie notek po odstępach w wierszu. `obwieszczenie` — strona obwieszczenia Marszałka Sejmu
    przed stroną „Załącznik do obwieszczenia": punkt jego wyliczenia na dole strony nie jest przypisem
    (`_pdf_punkt_obwieszczenia`). `stopka` — data wydruku w starym zapisie („18.01.2021"), którą `_pdf_data_stopki` potwierdziła
    jako ostatni wiersz większości stron PDF: ostatni wiersz strony równy tej dacie jest stopką. `przypis_niedokonczony` — ostatni
    przypis poprzedniej strony urywa się w pół zdania (`_pdf_dokonczenie_przypisu`).
    """
    # znak U+FEFF przed nagłówkiem strony („\ufeff Dziennik Ustaw –2– Poz. 712") — nagłówek nie był rozpoznawany i wchodził
    # w treść przepisu („…ryb 0,05 Dziennik Ustaw – 10 – Poz. 1016 1 2 3 4 DDT"); 13.11.2026: 9 z 58 tekstów (DU 2026/1391:
    # 111 razy). Ten znak nie jest treścią (niewidoczna spacja zerowej szerokości).
    lines = page.replace("\ufeff", "").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    # numer przypisu rozbity na dwa wiersze: „1" ⏎ „ )     Niniejszą ustawą zmienia się…" (DU 2014/900 s. 1 — w bbox „)" stoi
    # 2 pt niżej niż „1"); dotąd blok przypisów nie był rozpoznany, a przypisy 1 i 2 szły do treści jako „1 ) Niniejszą…".
    # Tylko na stronie z odsyłaczem potwierdzonym współrzędnymi — bez nich blok i tak nie byłby rozpoznany, a sklejone „1)"
    # zaczynałoby nowy akapit, udając punkt wyliczenia
    for k in range(len(lines) - 1 if _ODS_POTW in page else 0):
        if lines[k] is not None and re.fullmatch(r" {0,3}\d{1,3}", lines[k]) and re.match(r" {0,3}\)\s", lines[k + 1]):
            lines[k + 1] = lines[k] + lines[k + 1].lstrip(" ")
            lines[k] = None
    lines = [l for l in lines if l is not None]
    przypiete = {}
    if notki_bbox is not None:
        niepuste_idx = [k for k, l in enumerate(lines) if l.strip()]
        for k, teksty in notki_bbox.items():
            if teksty and niepuste_idx:
                cel = niepuste_idx[min(max(k, 1), len(niepuste_idx)) - 1]
                przypiete.setdefault(cel, []).extend(teksty)
    # nagłówek: pierwsze niepuste linie
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and _PDF_NAGLOWEK.match(lines[i]):
        i += 1
    lines = lines[i:]
    # notka obok nagłówka strony (k = 1) idzie do pierwszego wiersza treści — nie do pustego wiersza pod nagłówkiem
    pierwszy = next((k for k, l in enumerate(lines) if l.strip()), 0)
    przesuniete = {}
    for k, v in przypiete.items():
        przesuniete.setdefault(max(k - i, pierwszy), []).extend(v)
    przypiete = przesuniete
    # stopka: znacznik daty / numer strony na samym dole; `stopka` — data wydruku „18.01.2021" potwierdzona na innych stronach
    while lines and (not lines[-1].strip() or _PDF_STOPKA.match(lines[-1]) or stopka and lines[-1].strip() == stopka):
        d = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s*$", lines[-1])
        if d and info is not None:
            info.setdefault("data_wydruku", d.group(1))
        lines.pop()
    if notki_bbox is not None:
        notki_wierszy = {}
        for k, v in przypiete.items():
            notki_wierszy.setdefault(min(k, max(len(lines) - 1, 0)), []).extend(v)
    else:
        lines, notki_wierszy = _pdf_notki_marginesu(lines)
    for k in sorted(notki_wierszy):
        zostaja = []
        for n in notki_wierszy[k]:
            if n.startswith("Opracowano na") or (pierwsza and "Opracowano na" in n):
                if info is not None:
                    info.setdefault("podstawa", n[n.index("Opracowano na"):])
            else:
                zostaja.append(n)
        notki_wierszy[k] = zostaja
    niepuste = [l for l in lines if l.strip()]
    if not niepuste:
        return [], {}
    margines = min(len(l) - len(l.lstrip(" ")) for l in niepuste)
    wiersze = [(0, "", []) if not l.strip() else
               (len(l) - len(l.lstrip(" ")) - margines, " ".join(l.split()), notki_wierszy.get(k) or [])
               for k, l in enumerate(lines)]
    przypisy = {}
    # przypisy pod kreską („———————" + „1) …" do pustego wiersza) — w zeszytach z lat 1990–2011 stoją na dole
    # łamu, więc po rozdzieleniu łamów bywają w środku strony (`_pdf_lamy`)
    i = 0
    while i < len(wiersze):
        if _PDF_KRESKA.match(wiersze[i][1]):
            j = i + 1
            while j < len(wiersze) and wiersze[j][1]:
                j += 1
            blok = wiersze[i + 1:j]
            reszta = "\n".join(t for w, t, _ in wiersze[:i] + wiersze[j:])
            numery = [m.group(1) for w, t, _ in blok if w == 0 for m in [_PDF_PRZYPIS.match(t)] if m]
            if blok and blok[0][0] == 0 and _PDF_PRZYPIS.match(blok[0][1]) and \
                    (set(numery) & set(_PDF_ODSYLACZ.findall(reszta)) or any(f in " ".join(t for _, t, _ in blok) for f in _PDF_FRAZY_PRZYPISU)):
                biezacy = None
                for w, t, _ in blok:
                    m = _PDF_PRZYPIS.match(t) if w == 0 else None
                    if m:
                        biezacy = m.group(1)
                        przypisy[biezacy] = m.group(2) or ""
                    elif biezacy is not None and not _PDF_KRESKA.match(t):
                        przypisy[biezacy] = _doklej(przypisy[biezacy], t)
                del wiersze[i:j]
                continue
        i += 1
    # blok przypisów: ostatni blok po pustej linii, zaczynający się od „N)" na marginesie
    k = len(wiersze)
    while k > 0 and wiersze[k - 1][1]:
        k -= 1
    # przypis z kilkoma akapitami (k.s.h. art. 459: „10)" + wyrok TK + adnotacja o jego publikacji): akapity po
    # pustej linii są wcięte — blok przypisów zaczyna się wyżej, od „N)" na marginesie
    j = k
    for _ in range(5):
        if j < 2 or wiersze[j - 2][1] == "" or any(w == 0 for w, t, _ in wiersze[j:] if t):
            break
        j -= 1
        while j > 0 and wiersze[j - 1][1]:
            j -= 1
        if wiersze[j][0] == 0 and _PDF_PRZYPIS.match(wiersze[j][1]):
            k = j
            break
    blok = [x for x in wiersze[k:] if x[1]]
    if k > 0 and blok and blok[0][0] == 0 and _PDF_PRZYPIS.match(blok[0][1]):
        numery = [m.group(1) for w, t, _ in blok if w == 0 for m in [_PDF_PRZYPIS.match(t)] if m]
        tresc_strony = "\n".join(t for w, t, _ in wiersze[:k])
        odsylacze = set(_PDF_ODSYLACZ.findall(tresc_strony))
        tekst_bloku = " ".join(t for w, t, _ in blok)
        if ((set(numery) & odsylacze) or any(f in tekst_bloku for f in _PDF_FRAZY_PRZYPISU)) \
                and not (obwieszczenie and _pdf_punkt_obwieszczenia(blok, numery, tresc_strony)):
            biezacy = None
            for w, t, _ in blok:
                m = _PDF_PRZYPIS.match(t) if w == 0 else None
                if m:
                    biezacy = m.group(1)
                    przypisy[biezacy] = m.group(2) or ""
                elif biezacy is not None:
                    przypisy[biezacy] = _doklej(przypisy[biezacy], t)
            wiersze = wiersze[:k]
            return [(w, t, n) for w, t, n in wiersze if t], przypisy
    wiersze = _pdf_przypisy_po_odstepie(wiersze, przypisy, przypis_niedokonczony)
    if not przypisy:
        wiersze = _pdf_przypisy_bez_odstepu(wiersze, przypisy)
    if przypis_niedokonczony and not przypisy:
        wiersze = _pdf_dokonczenie_przypisu(wiersze, przypisy)
    return [(w, t, n) for w, t, n in wiersze if t], przypisy


_PDF_NAGLOWEK_JEDNOSTKI = re.compile(r"^[\[<„]?(Art\.\s*\d|§\s*\d|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|"
                                     r"Księga\s|KSIĘGA\s|Oddział\s|ODDZIAŁ\s|Załącznik)")


def _pdf_przypisy_po_odstepie(wiersze, przypisy, ciag=False):
    """Blok przypisów, którego nie rozpoznała reguła „ostatni blok po pustej linii od „N)" na marginesie". Zweryfikowane
    13.11.2026 na obrazie strony: k.p.c. DU 2026/468 s. 14 — blok zaczyna się dokończeniem przypisu 5 z poprzedniej strony,
    a za nim „6)", „7)" (wyroki TK P 13/19, P 10/19); k.p. DU 2026/1245 s. 95 — pusty wiersz w środku przypisu 61; DU 2026/1315
    s. 85 — przypisy 20–24 (wyroki TK) z pustymi wierszami. Treść tych przypisów szła do OSTATNIEGO przepisu strony: k.p.c.
    art. 50 § 2 kończył się zdaniami o utracie mocy art. 49 („6) Utracił moc z dniem 9 czerwca 2020 r. …").

    Blok = wiersze pod ostatnim odstępem co najmniej dwóch pustych wierszy, w których każdy niepusty wiersz to „N)" na
    marginesie (początek przypisu) albo wiersz wcięty (jego ciąg dalszy). Numery mają iść po kolei i KAŻDY musi mieć w treści
    strony nad blokiem odsyłacz potwierdzony współrzędnymi (`_ODS_POTW`) — bez współrzędnych (zeszyty 1990–2011, OCR) reguła
    nie działa. Wcięte wiersze przed pierwszym „N)" to dokończenie ostatniego przypisu poprzedniej strony (klucz „+").

    `ciag` — ostatni przypis poprzedniej strony urywa się w pół zdania: dokończenie może się wtedy zaczynać także od liczby
    („1566, 1909, 2180 i 2494 oraz z 2018 r. poz. 138 i 379), która weszła w życie…" — k.p. DU 2018/917 s. 86, tekst
    ujednolicony; dotąd przypis 9 tej strony był „nie odnaleziony", a cały blok szedł do art. 175¹ pkt 2), byle nie od numeru
    ustępu albo punktu („2. …", „2) …")."""
    if not any(_ODS_POTW in t for _, t, _ in wiersze):
        return wiersze
    najlepszy = None
    for p in range(len(wiersze) - 1, 1, -1):
        if not (wiersze[p][1] and wiersze[p - 1][1] == "" and wiersze[p - 2][1] == ""):
            continue
        blok = [(w, t) for w, t, _ in wiersze[p:] if t]
        if not all(w > 0 and not _PDF_NAGLOWEK_JEDNOSTKI.match(t) or w == 0 and _PDF_PRZYPIS.match(t) for w, t in blok):
            break       # wyżej byłby blok z treścią przepisu (artykuł, paragraf, nagłówek jednostki); punkty „8) dyrektywy…"
                        # wcięte pod numerem przypisu to wyliczenie W przypisie (CIT DU 2026/554 s. 2) — dozwolone
        # dokończenie przypisu z poprzedniej strony zaczyna się w środku zdania (małą literą); wcięty wiersz wielką literą
        # przed pierwszym „N)" to raczej treść (podpis, wyśrodkowany nagłówek) — takiego bloku nie bierzemy
        if blok and blok[0][0] > 0 and not blok[0][1][:1].islower() \
                and not (ciag and blok[0][1][:1].isdigit() and not re.match(r"\d+[a-z]*[.)]\s", blok[0][1])):
            continue
        numery = [m.group(1) for w, t in blok if w == 0 for m in [_PDF_PRZYPIS.match(t)] if m]
        nad = "\n".join(t for _, t, _ in wiersze[:p])
        potw = set(re.findall(_ODS_POTW + r"(\d{1,3})\)", nad))
        if numery and all(int(b) == int(a) + 1 for a, b in zip(numery, numery[1:])) and set(numery) <= potw:
            najlepszy = p
    if najlepszy is None:
        return wiersze
    biezacy, dalszy = None, ""
    for w, t, _ in wiersze[najlepszy:]:
        if not t:
            continue
        m = _PDF_PRZYPIS.match(t) if w == 0 else None
        if m:
            biezacy = m.group(1)
            przypisy[biezacy] = m.group(2) or ""
        elif biezacy is not None:
            przypisy[biezacy] = _doklej(przypisy[biezacy], t)
        else:
            dalszy = _doklej(dalszy, t)
    if dalszy:
        przypisy["+"] = dalszy
    return wiersze[:najlepszy]


def _pdf_przypisy_bez_odstepu(wiersze, przypisy):
    """Blok przypisów, który stoi tuż pod tekstem, BEZ pustego wiersza. DU 2013/714 s. 1: „…wniesienia protestu." ⏎ „1)" ⏎
    „     Zmiany tekstu jednolitego wymienionej ustawy…" — reguła „ostatni blok po pustej linii" brała cały akapit ust. 3 za
    blok, więc przypis 1 szedł do ust. 3 („…protestu. 1) Zmiany tekstu…"), a przy art. 1 stało „nie odnaleziono".

    Blok = od wiersza „N)" na marginesie do końca strony; dalej tylko wiersze wcięte (ciąg przypisu) i kolejne „M)" na
    marginesie. Każdy numer musi mieć na stronie odsyłacz potwierdzony współrzędnymi (`_ODS_POTW`), numery idą po kolei,
    a każdy przypis zaczyna się od samego „N)" w wierszu albo zawiera frazę przypisu (`_PDF_FRAZY_PRZYPISU`) — punkt
    wyliczenia „1) projekt…" z wciętym ciągiem dalszym tego nie spełnia."""
    niepuste = [i for i, (_, t, _) in enumerate(wiersze) if t]
    if not niepuste or not any(_ODS_POTW in t for _, t, _ in wiersze):
        return wiersze
    najlepszy = None        # najwyższy początek bloku, który spełnia warunki — przypis nad przypisem nie może zostać w treści
    for p in reversed(niepuste):
        w, t, _ = wiersze[p]
        if not (w == 0 and _PDF_PRZYPIS.match(t)):
            if w == 0:
                break       # wiersz treści na marginesie — wyżej bloku przypisów już nie ma
            continue
        blok = [(wiersze[i][0], wiersze[i][1]) for i in niepuste if i >= p]
        if not all(w > 0 and not _PDF_NAGLOWEK_JEDNOSTKI.match(t) or w == 0 and _PDF_PRZYPIS.match(t) for w, t in blok):
            break
        przyp, biezacy = {}, None
        for w, t in blok:
            m = _PDF_PRZYPIS.match(t) if w == 0 else None
            if m:
                biezacy = m.group(1)
                przyp[biezacy] = m.group(2) or ""
                przyp[biezacy + "?"] = not m.group(2) or any(f in t for f in _PDF_FRAZY_PRZYPISU)
            else:
                przyp[biezacy] = _doklej(przyp[biezacy], t)
                przyp[biezacy + "?"] = przyp[biezacy + "?"] or any(f in t for f in _PDF_FRAZY_PRZYPISU)
        numery = [k for k in przyp if not k.endswith("?")]
        nad = "\n".join(t for _, t, _ in wiersze[:p])
        potw = set(re.findall(_ODS_POTW + r"(\d{1,3})\)", nad))
        if numery and set(numery) <= potw and all(przyp[k + "?"] for k in numery) \
                and all(int(b) == int(a) + 1 for a, b in zip(numery, numery[1:])):
            najlepszy = (p, {k: przyp[k] for k in numery})
    if najlepszy is None:
        return wiersze
    przypisy.update(najlepszy[1])
    return wiersze[:najlepszy[0]]


def _pdf_dokonczenie_przypisu(wiersze, przypisy):
    """Strona bez własnych przypisów, na której dole stoi tylko DOKOŃCZENIE ostatniego przypisu poprzedniej strony. DU 2019/1500
    s. 2–3 (obraz strony): przypis 1 urywa się na s. 2 w pół zdania („…weszła w życie z dniem 25 maja 2018"), a na s. 3 pod
    odstępem stoi wcięte „r. Przepisy ustawy wymienione w zdaniu pierwszym utraciły moc z dniem 6 lutego 2019 r., …". Strona nie
    ma żadnego odsyłacza, więc `_pdf_przypisy_po_odstepie` jej nie bierze, a dokończenie szło w środek art. 4 ust. 4b („…w dziale
    6 rejestru r. Przepisy ustawy … przedsiębiorców wpisano…").

    Warunki: ostatni przypis poprzedniej strony nie kończy się kropką ani innym znakiem końca zdania (sprawdza wywołujący);
    blok stoi pod odstępem co najmniej dwóch pustych wierszy, wszystkie jego wiersze są wcięte, nie zaczynają się od „N)" ani od
    nagłówka jednostki, a pierwszy zaczyna się małą literą (dalszy ciąg zdania). Wynik — jak w `_pdf_przypisy_po_odstepie`:
    klucz „+", który `pdf_layout_do_tekstu` dokleja do ostatniego przypisu poprzedniej strony."""
    p = len(wiersze)
    while p > 0 and wiersze[p - 1][1]:
        p -= 1
    blok = [(w, t) for w, t, _ in wiersze[p:] if t]
    if not blok or p < 2 or wiersze[p - 1][1] or wiersze[p - 2][1] or not any(t for _, t, _ in wiersze[:p]):
        return wiersze
    if not blok[0][1][:1].islower() or not all(w > 0 and not _PDF_PRZYPIS.match(t) and not _PDF_NAGLOWEK_JEDNOSTKI.match(t)
                                               for w, t in blok):
        return wiersze
    dalszy = ""
    for _, t in blok:
        dalszy = _doklej(dalszy, t)
    przypisy["+"] = dalszy
    return wiersze[:p]


def _zlacz_rozstrzelone(t):
    """Rozstrzelony tytuł z PDF („US T AW A", „O B W IE S Z CZ E N I E") → „USTAWA"; tylko wiersze
    z samych wielkich liter, w których większość „wyrazów" to 1–2 znaki."""
    tok = t.split()
    if len(tok) >= 4 and all(x.isalpha() and x.isupper() for x in tok) \
            and sum(len(x) <= 2 for x in tok) >= 0.6 * len(tok) and len("".join(tok)) <= 20:
        return "".join(tok)
    return t


def _doklej(a, b):
    """Łączy wiersz zawinięty w PDF: „zabez-" + „pieczenia" → „zabezpieczenia" (dzielenie wyrazów),
    inaczej przez spację. Heurystyka: myślnik na końcu + mała litera na początku następnego wiersza."""
    if not a:
        return b
    if a.endswith(("-", "­")) and b[:1].islower():
        return a[:-1] + b
    # łamanie wyrazu złożonego z dywizem powtórzonym na początku wiersza (zasada składu): „Środkowo-" +
    # „-Wschodniej" (MP 2001 poz. 329) → „Środkowo-Wschodniej"
    if a.endswith("-") and b[:1] == "-" and b[1:2].isalpha() and a[-2:-1].isalpha():
        return a + b[1:]
    return a + " " + b


def _pdf_normalizuj_indeksy(t):
    # indeks górny w PDF: „Art. 68[1]." / „art. 385[1]–385[3]" / „68¹" / „art. 6b[3]" → jak w ścieżce HTML: „Art. 68 1."
    t = re.sub(r"(\d[a-z]{0,3})\[(\d+[a-z]?)\]", r"\1 \2", t)
    return re.sub(r"(?<=\d)([" + _SUPS + r"]+)", lambda m: " " + m.group(1).translate(_SUP), t)


# „Art. 13 1a." (po `_pdf_normalizuj_indeksy`) → baza „13", indeks „ 1a"; w notce: „§ 2 w art. 125 1 wejdzie…"
_ART_NAGLOWEK = re.compile(r"^[\[<]?Art\.\s*(\d+[a-z]*)((?:\s+\d+[a-z]*)?)\s*\.")
_ART_W_NOTCE = re.compile(r"\bart\.\s*(\d+[a-z]*)((?:\s+\d+[a-z]*)?)(?!\w)")
_JEDNOSTKA_W_NOTCE = re.compile(r"(?i)\b(księg|tytuł|dział|rozdział|oddział)\w*\s+([IVXLC]+[A-Z]{0,3}|\d+[a-z]*)\b")


def _id_art(m):
    """„13" + „ 1" → „13 1" (art. 13¹). „65a" + „ 65b" to wyliczenie („art. 65a 65b"), nie indeks — indeks górny
    jest mniejszy od numeru artykułu (art. 479⁸⁸ⁱ, art. 1¹)."""
    baza, idx = m.group(1), m.group(2).strip()
    if idx:
        i, b = int(re.match(r"\d+", idx).group(0)), int(re.match(r"\d+", baza).group(0))
        if i > b or i == b and not baza.isdigit():
            idx = ""
    return baza + (" " + idx if idx else "")


def _przypnij_notki(akapity):
    """Notka stoi obok wiersza, przy którym zaczyna się jej pudełko — a to bywa kilka punktów nad nagłówkiem
    „Art. 100a." (notka trafia do ostatniego akapitu art. 100) albo pod końcem artykułu, którego dotyczy.
    Gdy notka wymienia artykuł („Dodany art. 100a", „§ 2 w art. 125 1") albo rozdział/dział („Dodany rozdział
    6a"), a akapit leży gdzie indziej, przenosimy ją do najbliższego akapitu tego artykułu (nagłówka tej
    jednostki) NA TEJ SAMEJ STRONIE. Bez takiego akapitu na stronie notka zostaje na miejscu."""
    kontekst, art = [], None
    for a in akapity:
        m = _ART_NAGLOWEK.match(_pdf_normalizuj_indeksy(a[1]))
        if m:
            art = _id_art(m)
        kontekst.append(art)
    na_stronie = {}
    for j, a in enumerate(akapity):
        for nr in a[2]:
            na_stronie.setdefault(nr, []).append(j)

    def cel(i, notka):
        t = _pdf_normalizuj_indeksy(notka)
        m = _PDF_NOTKA.search(t)
        podmiot = t[:m.start()] if m else t
        obok = sorted({j for nr in akapity[i][2] for j in na_stronie.get(nr, [])})
        for jm in _JEDNOSTKA_W_NOTCE.finditer(podmiot):
            wzor = re.compile(r"(?i)^[\[<]?" + jm.group(1) + r"\w*\s+" + re.escape(jm.group(2)) + r"\b")
            kand = [j for j in obok if wzor.match(akapity[j][1])]
            if kand:
                return min(kand, key=lambda j: abs(j - i))
        ids = {_id_art(x) for x in _ART_W_NOTCE.finditer(podmiot)}
        if not ids or kontekst[i] in ids:
            return i
        kand = [j for j in obok if kontekst[j] in ids]
        return min(kand, key=lambda j: abs(j - i)) if kand else i
    for i, a in enumerate(akapity):
        if not a[3]:
            continue
        zostaja = []
        for n in a[3]:
            j = cel(i, n)
            (zostaja if j == i else akapity[j][3]).append(n)
        a[3] = zostaja


# Przypis spod tekstu strony, którego odsyłacza w treści nie ustalono (`pdf_layout_do_tekstu`): etykieta mówi wprost, że
# NIE wiadomo, której jednostki dotyczy. Ostrzeżenia o przypisach fragmentu (wyrok TK, przepisy, które jeszcze nie
# obowiązują, przypis przy nagłówku rozdziału) nie biorą go za przypis tej jednostki — podają go osobno z zastrzeżeniem.
_PRZYPIS_BEZ_ODSYLACZA = "bez odsyłacza w treści tej strony; nie przypisuj go do sąsiedniego przepisu"
_NIEPRZYPISANY = "przypis nieprzypisany do jednostki"      # tak go nazywają ostrzeżenia
_PRZYPIS_BEZ_ODSYLACZA_LINIA = re.compile(r"(?m)^\[przypis (\d+[a-z]?)\) — " + re.escape(_PRZYPIS_BEZ_ODSYLACZA) + r"\] (.*)$")
# akapit zaczynający jednostkę (artykuł, paragraf, ustęp, nagłówek) — przed nim stawiamy przypis bez odsyłacza
_PDF_POCZATEK_JEDNOSTKI = re.compile(r"^[\[<]?(?:Art\.\s*\d|§\s*\d|\d+[a-z]*\.\s|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|"
                                     r"Tytuł\s|TYTUŁ\s|Księga\s|KSIĘGA\s|Oddział\s|ODDZIAŁ\s|Załącznik)")


# Data wydruku w stopce tekstu ujednoliconego Kancelarii Sejmu w starym zapisie: „18.01.2021" zamiast „2026-09-25". 13.11.2026,
# 25 PDF typu U wygenerowanych w latach 2016–2026: stary zapis ma 11 (wygenerowane od 04.2018 do 01.2022, np. DU 2019/1500,
# DU 2020/920), ISO — 14. `_PDF_STOPKA` znała tylko ISO, więc data zostawała w treści na każdej stronie: jako osobny wiersz
# w środku zdania („…współpracy sądów ⏎ 18.01.2021 ⏎ w szczególności…"), doklejona do ostatniego przypisu strony („…str. 18.
# 18.01.2021") albo zasłaniała blok przypisów (ostatni blok strony nie zaczynał się od „N)" — przypis 1 DU 2019/1500 „nie
# odnaleziono"). Stopką jest tylko data, która stoi SAMA w ostatnim wierszu co najmniej połowy stron z treścią (i co najmniej
# dwóch) — prawdziwa data w przepisie nie powtarza się na końcu kolejnych stron. W akcie jednostronicowym powtórzenia nie ma:
# data zostaje (DU 2020/2374 — pod podpisem Prezydenta) i daje ostrzeżenie `_niepewne_miejsca_pdf`.
_PDF_DATA_STOPKI = re.compile(r"\d{2}\.\d{2}\.\d{4}")
# akapit, który jest SAMĄ datą — w treści aktu tak nie bywa; to stopka strony, której `_pdf_data_stopki` nie potwierdziła
# (`_niepewne_miejsca_pdf`)
_PDF_SAMA_DATA = re.compile(r"\d{1,2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}")
# treść przypisu w treści przepisu: „N) " + początek typowy dla przypisu tekstu jednolitego (wielką literą — punkt wyliczenia
# zaczyna się małą). Tak wyglądał k.p. DU 2026/1245 art. 302¹ § 7 przed poprawką („60) Zmiany tekstu jednolitego…") i k.c.
# art. 182 § 2 w wersji 2.3.0 („…kosztów. 2) Z dniem 15 lipca 2006 r. na podstawie wyroku…"); zaślepka „nie odnaleziono"
# stoi przy odsyłaczu, często w innym artykule niż wklejona treść (`_niepewne_miejsca_pdf`)
_PDF_PRZYPIS_W_TRESCI = re.compile(
    r"(?:^|(?<=\s))(\d{1,3}) ?\) (?=(?:Zmiany tekstu jednolitego|Zmiany? wymienion|W brzmieniu ustalonym|Dodan[yae] przez|"
    r"Uchylon[yae] przez|Ze zmianą wprowadzoną|Niniejsz[aą] ustaw|Niniejsze rozporządzenie|Obecnie minister|Z dniem \d|"
    r"Utracił[ay]? moc)\b)")


def _pdf_data_stopki(strony, wzor=_PDF_DATA_STOPKI):
    """Strony z `-layout` → data wydruku w starym zapisie (`wzor`), która jest ostatnim wierszem większości stron (albo None)."""
    ostatnie, z_trescia = {}, 0
    for s in strony:
        niepuste = [l.strip() for l in s.split("\n") if l.strip()]
        if not niepuste:
            continue
        z_trescia += 1
        if wzor.fullmatch(niepuste[-1]):
            ostatnie[niepuste[-1]] = ostatnie.get(niepuste[-1], 0) + 1
    if not ostatnie:
        return None
    data = max(ostatnie, key=ostatnie.get)
    return data if ostatnie[data] >= 2 and ostatnie[data] * 2 >= z_trescia else None


def _pdf_notka_pod_data(strona, data):
    """Strona z `-layout` → (strona bez daty wydruku i bez urwanej notki pod nią, tekst notki albo None).

    Notka marginesu na samym dole strony, POD datą wydruku w stopce, bywa w PDF urwana na krawędzi strony: DU 2026/1263 s. 25
    „Zmiana (skreślenie wyrazów) w pkt 9" (pełna notka dopiero przy art. 13 na s. 28), DU 2022/1138 s. 47 „Nowe brzmienie § 1
    i przepis" (dalszy ciąg „uchylający § 1a w" wystaje poza stronę, a na s. 48 go nie ma) — 13.11.2026, obraz stron. Strony
    nie da się przyciąć przed kolumną notek (notka bez frazy „wejdzie w życie"), więc data i początek notki zostawały w treści
    i rozcinały zdanie („…import takich towarów ⏎ 2026-11-12 ⏎ Zmiana ⏎ (skreślenie ⏎ wyrazów) w pkt 9 ⏎ z terytorium państwa
    trzeciego…"). Warunki: data (ISO) równa dacie wydruku potwierdzonej na innych stronach (`data`), pod nią do końca strony
    tylko wiersze wcięte co najmniej na 55% szerokości strony (kolumna notek)."""
    if not data or data not in strona:
        return strona, None
    lines = strona.split("\n")
    niepuste = [k for k, l in enumerate(lines) if l.strip()]
    szer = max((len(l.rstrip()) for l in lines), default=0)
    k = next((k for k in reversed(niepuste[-8:]) if lines[k].strip() == data), None)
    pod = [i for i in niepuste if k is not None and i > k]
    if k is None or not pod or any(len(lines[i]) - len(lines[i].lstrip(" ")) < 0.55 * szer for i in pod):
        return strona, None
    notka = ""
    for i in pod:
        notka = _doklej(notka, " ".join(lines[i].split()))
    return "\n".join(lines[:k]), notka


def _przypis_bez_odsylacza(n, tresc):
    return f"[przypis {n}) — {_PRZYPIS_BEZ_ODSYLACZA}] {tresc}"


# Obszary tekstu z PDF odczytane niepewnie, które nie mają w tekście własnej etykiety (jak „[przypis N)] (treści … nie
# odnaleziono…)"): tabela z wielowierszowymi komórkami, strona z ukrytą warstwą tekstu, luka albo odwrócenie w numeracji jednostek.
# `pdf_layout_do_tekstu` oznacza akapity takiego obszaru niewidocznymi znakami (prywatny zakres Unicode), a po złożeniu tekstu
# zdejmuje je i zapisuje w `info["niepewne_obszary"]` pozycje (początek, koniec) w WYNIKOWYM tekście — bez żadnej zmiany samego
# tekstu. `_niepewne_miejsca_pdf` ostrzega o obszarze, gdy zachodzi on na wypisany fragment (albo zawsze — przy pełnym tekście).
_RODZAJE_OBSZAROW = ("tabela", "warstwa", "numeracja", "notka")
_ZN_KONIEC = ""          # koniec oznaczonego akapitu
_ZN_OBSZAR = 0xF0000           # + 0x1000 × rodzaj + strona (od 0): początek akapitu obszaru
_ZN_STRONA = 0xF8000           # + strona (od 0): akapit zaczynający jednostkę — do stron luk w numeracji
_ZNACZNIKI = re.compile("[\U000f0000-\U000fffff]")


def _pdf_oznacz_obszar(t, flagi, strona=None):
    """Akapit `t` → ten sam akapit ze znacznikami obszarów `flagi` ({(rodzaj, strona od 0)}) i strony (`_pdf_zdejmij_znaczniki`)."""
    if not t.strip():
        return t
    pocz = "".join(chr(_ZN_OBSZAR + 0x1000 * _RODZAJE_OBSZAROW.index(r) + s) for r, s in sorted(flagi) if 0 <= s < 0x1000)
    if strona is not None and 0 <= strona < 0x7000:
        pocz = chr(_ZN_STRONA + strona) + pocz
    return pocz + t + _ZN_KONIEC * sum(1 for c in pocz if ord(c) < _ZN_STRONA)


def _pdf_zdejmij_znaczniki(t):
    """Tekst ze znacznikami → (tekst bez nich, [(rodzaj, strona, początek, koniec)], [(pozycja, strona)] początków jednostek)."""
    if not _ZNACZNIKI.search(t):
        return t, [], []
    out, obszary, strony, stos, poz = [], [], [], [], 0
    for kawalek in re.split("([\U000f0000-\U000fffff])", t):
        if not kawalek:
            continue
        if len(kawalek) == 1 and _ZNACZNIKI.match(kawalek):
            k = ord(kawalek)
            if kawalek == _ZN_KONIEC:
                if stos:
                    rodzaj, strona, start = stos.pop()
                    obszary.append((rodzaj, strona, start, poz))
            elif k >= _ZN_STRONA:
                strony.append((poz, k - _ZN_STRONA))
            else:
                stos.append((_RODZAJE_OBSZAROW[(k - _ZN_OBSZAR) // 0x1000], (k - _ZN_OBSZAR) % 0x1000, poz))
            continue
        out.append(kawalek)
        poz += len(kawalek)
    return "".join(out), sorted(obszary, key=lambda o: o[2]), strony


def _pdf_scal_obszary(obszary):
    """Akapity jednego rodzaju na tej samej albo kolejnej stronie → jeden obszar [(rodzaj, strona od, strona do, początek, koniec)]."""
    out = []
    for rodzaj, strona, s, e in sorted(obszary, key=lambda o: (_RODZAJE_OBSZAROW.index(o[0]), o[1], o[2])):
        if out and out[-1][0] == rodzaj and strona <= out[-1][2] + 1:
            out[-1] = (rodzaj, out[-1][1], max(out[-1][2], strona), min(out[-1][3], s), max(out[-1][4], e))
        else:
            out.append((rodzaj, strona, strona, s, e))
    return out


# Tabela, której komórki `-layout` przeplata (13.11.2026, DU 2025/615 s. 10, VAT DU 2026/1263 s. 414, DU 2026/1252 s. 41 —
# obraz strony): gdy w jednym wierszu tabeli zawija się więcej niż jedna komórka, `-layout` daje wiersz po wierszu strony
# („13   bez względu   Wyroby medyczne w rozumieniu…" ⏎ „     na CN         medycznych (Dz. U.…"), a sklejanie akapitów —
# „13 bez względu Wyroby medyczne w rozumieniu… na CN medycznych…". Komórka zawinięta tylko w jednej kolumnie (zwykły wiersz VAT
# „poz. | PKWiU | nazwa") czyta się dobrze — takich tabel nie oznaczamy. Wiersz tabeli = co najmniej dwa segmenty oddzielone
# ≥ 3 spacjami (numer punktu „1)   tekst" to jeden segment); rozstrzelony wiersz justowanej prozy („Powództwo     wzajemne
# o ustalenie") — nie. Blok tabeli: ≥ 3 takie wiersze (przerwy do 2 wierszy) z kolumną zaczynającą się w tym samym miejscu
# w ≥ 3 wierszach. Niepewny: zawiera wiersz ciągu dalszego komórki, który `-layout` stawia za tekstem kolumny bardziej na prawo
# (`_pdf_wiersz_przemieszany`).
_PDF_SEGMENT = re.compile(r"\S+(?: {1,2}\S+)*")
_PDF_ZNAK_WYLICZENIA = re.compile(r"\d+[a-z]*\)|[a-z]\)|[–-]|\d+[a-z]*\.|§ ?\d+[a-z]*\.|Art\. ?\d+[a-z]*\.|\*+")


def _pdf_segmenty(l):
    out = []
    for m in _PDF_SEGMENT.finditer(l):
        if len(out) == 1 and _PDF_ZNAK_WYLICZENIA.fullmatch(out[0][1]):
            out[0] = (out[0][0], out[0][1] + " " + m.group(0))
        else:
            out.append((m.start(), m.group(0)))
    return out


def _pdf_tabela_niepewna(lines):
    """Wiersze strony → zbiór tekstów wierszy (spacje znormalizowane jak w `_pdf_strona`) z bloków tabeli, których komórki
    `-layout` mógł przemieszać (patrz wyżej)."""
    seg = [_pdf_segmenty(l) for l in lines]

    def proza(s):
        # ≥ 5 segmentów: wiersz tabeli z 4 kolumnami słów („4   Starszy terapeuta zajęciowy   VIII   według odrębnych przepisów"
        # — DU 2021/1960 s. 58) to nie proza
        return len(s) >= 5 and sum(1 for _, t in s if len(t.split()) <= 3 and t[:1].isalpha()) >= 0.75 * len(s)
    wiele = [i for i, s in enumerate(seg) if len(s) >= 2 and not proza(s)]
    bloki, biezacy = [], []
    for i in wiele:
        # między wierszami tabeli bywa wiele wierszy dalszego ciągu jednej komórki (nazwa towaru VAT na 9 wierszy — DU 2017/1221
        # s. 310); blok przerywają dopiero 3 puste wiersze z rzędu
        if biezacy and "\n\n\n\n" in "\n".join(l if l.strip() else "" for l in lines[biezacy[-1]:i + 1]):
            bloki.append(biezacy)
            biezacy = []
        biezacy.append(i)
    if biezacy:
        bloki.append(biezacy)
    out = set()
    for b in bloki:
        if len(b) < 3:
            continue
        kolumny = {}
        for i in b:
            for x, _ in seg[i][1:]:
                kolumny[x] = kolumny.get(x, 0) + 1
        wyrownane = {x for x in kolumny if kolumny[x] + kolumny.get(x - 1, 0) + kolumny.get(x + 1, 0) >= 3}
        if not wyrownane:
            continue
        lewy = min(seg[i][0][0] for i in b)
        koniec = min(b[-1] + 3, len(lines))       # dalszy ciąg ostatniego wiersza tabeli bywa jednym segmentem
        if any(_pdf_wiersz_przemieszany(seg, i, lewy, b[0], wyrownane) for i in range(b[0] + 1, koniec)):
            out.update(" ".join(lines[i].split()) for i in range(b[0], koniec) if lines[i].strip())
    return out


def _pdf_wiersz_przemieszany(seg, i, lewy, od=0, wyrownane=()):
    """Czy wiersz `i` bloku tabeli to dalszy ciąg komórki, który `-layout` postawi ZA tekstem kolumny bardziej na prawo
    z wiersza wyżej? Dalszy ciąg: pierwsza komórka pusta (pierwszy segment dalej niż lewy brzeg bloku). Tabela bez kolumny
    „Lp.", w której zawija się pierwsza kolumna, tego warunku nie spełnia — takiego przemieszania reguła nie wykrywa.
    Przemieszany: poprzedni niepusty wiersz (najwyżej 2 wiersze wyżej) ma segment zaczynający się na prawo od tego wiersza —
    „Ścierka do wycierania na-   szt.   4   2   1" ⏎ „czyń" daje „…na- szt. 4 2 1 czyń" (DU 1998/1012).

    Czytają się dobrze, więc nie są alarmem: zawinięta tylko OSTATNIA kolumna („Wyroby medyczne…" ⏎ „medycznych…"), wartość
    w wierszu ciągu nazwy („z azbestem (torkret)…… 25" — DU 1998/895), wiersz zaczynający się od numeru z treścią
    („2. Tynk…", „II …", „a) …") — nowy wiersz tabeli — i dalszy ciąg, który sięga za początek tamtej kolumny (objaśnienie
    symbolu wzoru: „ZOr – minimalną … ro-" ⏎ „py naftowej wyrażoną…" — DU 2014/900). Sam wcięty numer („0" — podwiersz
    M.P. 2023/1279 s. 6, którego wartości „110 110 110" `-layout` postawił wiersz wyżej, przy nazwie poz. 45) jest sprawdzany
    jak dalszy ciąg."""
    pierwszy = seg[i][0][1] if seg[i] else ""
    numer = re.match(r"(?:\d+[a-z]?|[IVX]+|[a-z])[.)]?(?:\s|$)|[a-z]{1,3}\)", pierwszy)
    sam_numer = numer and len(seg[i]) == 1 and re.fullmatch(r"(?:\d+[a-z]?|[IVX]+)[.)]?", pierwszy)
    if not seg[i] or seg[i][0][0] <= lewy + 2 or pierwszy.startswith(("–", "-")) or numer and not sam_numer \
            or " " not in pierwszy and len(pierwszy) <= 6 and not pierwszy[:1].islower() and len(seg[i]) > 1 \
            or pierwszy.endswith(("–", ":")) or len(seg[i]) > 1 and seg[i][1][1].startswith("–") or _pdf_wzor(seg[i]):
        return False        # numer wiersza z treścią, tiret, objaśnienie symbolu wzoru („R –", „n−i – rok…", „gdzie:")
    j = next((k for k in (i - 1, i - 2) if k >= od and seg[k]), None)
    if j is None or _pdf_wzor(seg[j]) or _PDF_NAGLOWEK.match(" ".join(t for _, t in seg[j])):
        return False        # nad nim wzór, nagłówek strony („©Kancelaria Sejmu   s. 76/134") albo tytuł nad tabelą
    koniec = seg[i][0][0] + len(seg[i][0][1])
    # wiersz wyżej od tej kolumny w prawo to justowany tekst jednej komórki („Pozostałe usługi profesjonalne,   naukowe   i
    # techniczne,   gdzie   indziej" ⏎ „niesklasyfikowane" — VAT DU 2026/1263 s. 442), nie kilka kolumn: same krótkie słowa, żadne
    # nie zaczyna się w kolumnie wyrównanej w bloku (kolumna „VIII   według odrębnych przepisów" — DU 2021/1960 s. 58 — tak)
    ogon = [(x, t) for x, t in seg[j] if x >= seg[i][0][0] - 2]
    if len(ogon) >= 3 and sum(1 for _, t in ogon if len(t.split()) <= 3 and t[:1].isalpha()) >= 0.75 * len(ogon) \
            and not any(abs(x - w) <= 1 for x, _ in ogon[1:] for w in wyrownane):
        return False
    return any(x > seg[i][0][0] + 2 and x > koniec for x, _ in seg[j])


def _pdf_wzor(segmenty):
    """Wiersz wzoru („DPITn = DPITn−i ∙   ∙", „𝐾𝑃 =", licznik ułamka „P−G–O" — DU 2014/900 s. 5): znak „=", znak działania
    (minus U+2212, „×", „∙", „÷", „Σ", „√") albo litery matematyczne Unicode."""
    t = " ".join(s for _, s in segmenty)
    return any(c in t for c in "=−×∙÷Σ√") or any("\U0001d400" <= c <= "\U0001d7ff" for c in t)


# Luki i odwrócenia w numeracji jednostek tekstu z zeszytu Dz.U./M.P. (1990–2011) i z OCR (13.11.2026): DU 1994/73 — „§ '0."
# (OCR „1" jako „'") wklejone w § 9 ust. 2, „586." (OCR „§" jako „5") na stronie-skanie; DU 1996/78 — „34. 1. Pryzmy… §" (znak „§"
# przesunięty w warstwie OCR); DU 2004/1453 — łamy przemieszane („wrze- § 2. Ustala się…"). Nagłówek „§ N." / „Art. N." na początku
# wiersza (także „§ 11 .", „§ 40:", „§9." z OCR), poza cudzysłowem (akt zmieniający cytuje cudze jednostki). Paragrafy liczone
# osobno w każdym artykule (i od nowa od „§ 1": załącznik, regulamin); numer z literą („§ 5a") to ta sama baza.
# Zakres („Art. 139–144. (uchylone)") — tylko z nawiasem za nim: „§ 11—14." na początku wiersza to dalszy ciąg odesłania
# („…stosując zasady określone w ⏎ § 11—14." — DU 1998/875), nie nagłówek.
_NAGLOWEK_NUMERACJI = re.compile(r"(?m)^[\[<]?(Art\.|§)[ ]?(\d{1,4})[a-z]{0,4}(?:[ ]?[–—-][ ]?(\d{1,4})[a-z]{0,4}(?=[ ]?\.[ ]?\())?"
                                 r"(?:[ ]\d{1,3}[a-z]{0,2})?[ ]?[.:,]")


def _luki_numeracji(t):
    """Tekst → [(opis, początek, koniec)]: luka („brak § 10") albo odwrócenie („§ 40 po § 48") w numeracji; obszar sięga od
    nagłówka przed luką do końca wiersza nagłówka za nią.

    Akt zmieniający (co najmniej dwa „otrzymuje brzmienie" / „dodaje się") nie jest sprawdzany: cytuje cudze artykuły, a w
    OCR z lat 90. cudzysłów bywa odczytany jako „,," albo `"`, więc maska cytatów nie działa — DU 1995/504 dawał 6 fałszywych
    luk („art. 2–33 brak…", bo „Art. 34b." z cytatu stał na początku wiersza; 13.11.2026)."""
    if len(re.findall(r"(?i)otrzymuj\w*\s+brzmienie|dodaje\s+się", t)) >= 2:
        return []
    maska, _ = _maska_cytatow(t)
    widziane = {"Art.": set(), "§": set()}
    naglowki = []
    for m in _NAGLOWEK_NUMERACJI.finditer(t):
        if not maska[m.start():m.end()].strip():
            continue        # w cudzysłowie
        do = int(m.group(3)) if m.group(3) and int(m.group(3)) > int(m.group(2)) else int(m.group(2))     # „Art. 139–144."
        naglowki.append((m.group(1), int(m.group(2)), do, m.start(), t.find("\n", m.start()) % (len(t) + 1)))
    for rodzaj, n, do, _, _ in naglowki:
        widziane[rodzaj].update(range(n, do + 1))
    out, ostatni = [], {"Art.": None, "§": None}
    for rodzaj, n, do, s, e in naglowki:
        nazwa = "art." if rodzaj == "Art." else "§"
        poprz = ostatni[rodzaj]
        if poprz is not None:
            p, ps = poprz[0], poprz[1]
            if n > p + 1:
                brak = range(p + 1, n)
                zakres = f"{nazwa} {p + 1}" + (f"–{n - 1}" if n - 1 > p + 1 else "")
                gdzie = ("stoi w innym miejscu odczytu — możliwy zły porządek łamów"
                         if all(k in widziane[rodzaj] for k in brak) else "brak w odczycie")
                out.append((f"{zakres} {gdzie} (między {nazwa} {p} a {nazwa} {n})", ps, e))
            elif n < p and n != 1:
                out.append((f"{nazwa} {n} po {nazwa} {p} — kolejność jednostek w odczycie", ps, e))
        elif rodzaj == "§" and n > 2:
            art = ostatni["Art."]
            out.append((f"§ {n} jako pierwszy paragraf" + (f" art. {art[0]}" if art else "") + f" — § 1–{n - 1} "
                        + ("stoją w innym miejscu odczytu" if all(k in widziane["§"] for k in range(1, n)) else "brak w odczycie"),
                        art[1] if art else s, e))
        ostatni[rodzaj] = (do, s)
        if rodzaj == "Art.":
            # „Art. 5. § 1. …" — § 1 w wierszu artykułu
            ostatni["§"] = (1, s) if re.match(r"[\[<]?Art\.[ ]?\d+[a-z]*\.[ ]?§[ ]?1\.", t[s:e]) else None
    return out


def pdf_layout_do_tekstu(raw, info=None, notki=None, strony_flagi=None, strona0=0, numeracja=False):
    """Tekst z `pdftotext -layout` → tekst w układzie jak z text.html.

    Strony są czyszczone z nagłówków/stopek, zawinięte wiersze są sklejane w akapity (nowy akapit
    zaczyna się od wciętego „Art."/„§"/„N)"/„lit."…, kontynuacja stoi na lewym marginesie),
    a odsyłacze do przypisów („§ 1.3)") znikają z numeracji — treść przypisu wychodzi pod
    akapit jako „[przypis 3)] …", dokładnie jak w ścieżce HTML. Notki z prawego marginesu tekstu
    ujednoliconego wychodzą pod akapitem jako „[margines: …]"; `info` (dict) dostaje „podstawa"
    (notka „Opracowano na podstawie…") i „data_wydruku". `notki` — z `pdf_do_tekstu_z_notkami`.

    Obszary niepewne (`_RODZAJE_OBSZAROW`) trafiają do `info["niepewne_obszary"]` jako (rodzaj, opis, strona PDF od, strona PDF
    do, początek, koniec): tabele z przemieszanymi komórkami (`_pdf_tabela_niepewna`), strony z `strony_flagi` ({nr strony od 0:
    [(rodzaj, opis)]} — ukryta warstwa tekstu z `pdf_do_tekstu_z_notkami`) i — z `numeracja` (zeszyt 1990–2011, OCR) — luki
    w numeracji (`_luki_numeracji`). `strona0` — numer strony PDF (od 0) pierwszej strony `raw` (akt wycięty z zeszytu).
    """
    strony = raw.split("\f")
    akapity = []        # [wcięcie_nagłówka(bool), tekst, {strony}, [notki marginesu], {(rodzaj obszaru, strona)}]
    przypisy = {}       # nr strony → {nr przypisu: treść}
    poprzedni_naglowek = False
    # strony obwieszczenia Marszałka Sejmu (przed stroną „Załącznik do obwieszczenia") — `_pdf_punkt_obwieszczenia`
    strona_zal = next((i for i, s in enumerate(strony) if re.search(r"(?im)^\W*Załącznik\s+do\s+obwieszczenia", s)), None)
    if strona_zal is not None and not re.search(r"jednolitego\s+tekstu", "\n".join(strony[:strona_zal + 1])):
        strona_zal = None
    stopka = _pdf_data_stopki(strony)
    if stopka and info is not None:
        d, m, r = stopka.split(".")
        info.setdefault("data_wydruku", f"{r}-{m}-{d}")
    # data wydruku (ISO) potwierdzona jako ostatni wiersz większości stron — `_pdf_notka_pod_data`
    data_iso = _pdf_data_stopki(strony, _PDF_DATA_WYDRUKU)
    daty_w_tresci = set()       # indeksy akapitów: sama data, po której na tej stronie są jeszcze co najmniej 3 wiersze
    strony_flagi = {k: list(v) for k, v in (strony_flagi or {}).items()}
    for nr, strona in enumerate(strony):
        strona, urwana = _pdf_notka_pod_data(strona, data_iso)
        if urwana:
            strony_flagi.setdefault(nr, []).append(("notka", urwana))
        poprzednie = przypisy.get(nr - 1)
        urwany = bool(poprzednie) and not re.search(r"[.;:!?)”\"]$", poprzednie[max(poprzednie, key=int)].rstrip())
        wiersze, przyp = _pdf_strona(strona, pierwsza=(nr == 0), info=info,
                                     notki_bbox=(notki or {}).get(nr) if notki else None,
                                     obwieszczenie=strona_zal is not None and nr < strona_zal, stopka=stopka,
                                     przypis_niedokonczony=urwany)
        dalszy = None
        if przyp.get("+") is not None:
            # dokończenie ostatniego przypisu poprzedniej strony (`_pdf_przypisy_po_odstepie`); gdy tamta strona nie ma
            # przypisów, tekst zostaje w treści — jak dotąd, nic nie ginie
            dalszy, poprzednie = przyp.pop("+"), przypisy.get(nr - 1)
            if poprzednie:
                ostatni = max(poprzednie, key=int)
                poprzednie[ostatni] = _doklej(poprzednie[ostatni], dalszy)
                dalszy = None
        if przyp:
            przypisy[nr] = przyp
        # tabela z przemieszanymi komórkami — na wierszach po zdjęciu notek marginesu (te bywają w `-layout` obok treści)
        linie = strona.replace("﻿", "").split("\n")
        tabela = _pdf_tabela_niepewna(linie if (notki or {}).get(nr) is not None and notki else _pdf_notki_marginesu(linie)[0])
        flagi_strony = {(r, nr) for r, _ in (strony_flagi or {}).get(nr, [])}
        szer_strony = max((w + len(t) for w, t, _ in wiersze), default=0)
        for k, (wciecie, t, notki_wiersza) in enumerate(wiersze):
            flagi = flagi_strony | ({("tabela", nr)} if t in tabela else set())
            if _PDF_SAMA_DATA.fullmatch(t) and sum(1 for w, _, _ in wiersze[k + 1:] if w < 0.55 * szer_strony) >= 3:
                daty_w_tresci.add(len(akapity))     # data w środku strony — komórka tabeli, nie stopka (patrz niżej)
            naglowek = wciecie >= _PDF_WCIECIE_NAGLOWKA and len(t) <= _PDF_MAKS_NAGLOWEK
            if naglowek:
                t = _zlacz_rozstrzelone(t)
            nowy = (naglowek or poprzedni_naglowek or not akapity
                    or (wciecie > 0 and _PDF_NOWY_AKAPIT.match(t)) or _PDF_JEDNOSTKA.match(t))
            if nowy:
                akapity.append([naglowek, t, {nr}, list(notki_wiersza), flagi])
            else:
                akapity[-1][1] = _doklej(akapity[-1][1], t)
                akapity[-1][2].add(nr)
                akapity[-1][3].extend(notki_wiersza)
                akapity[-1][4] |= flagi
            poprzedni_naglowek = naglowek
        if dalszy:
            akapity.append([False, dalszy, {nr}, [], set(flagi_strony)])
        if urwana and akapity:
            akapity[-1][3].append(f"{urwana} … (notka urwana na dole strony {nr + 1 + strona0} PDF — dalszej części na tej "
                                  "stronie nie ma)")
    if any(a[3] for a in akapity):
        _przypnij_notki(akapity)
    # Tekst jednolity zaczyna się od OBWIESZCZENIA Marszałka Sejmu (z cytowanymi przepisami
    # przejściowymi: „Art. 3. Ustawa wchodzi w życie…"). To nie jest treść aktu, a na początku
    # linii udawałoby nagłówek artykułu — wiersze obwieszczenia dostają znacznik „» ".
    zal = next((i for i, a in enumerate(akapity[:400]) if a[1].lower().startswith("załącznik do obwieszczenia")), None)
    przesuniecie = 0        # o tyle przesunęły się indeksy akapitów (`daty_w_tresci`)
    if zal and any("jednolitego tekstu" in a[1] for a in akapity[:zal]):
        przesuniecie = 1
        for a in akapity[:zal]:
            a[1] = "» " + a[1]
        akapity.insert(0, [True, "[obwieszczenie Marszałka Sejmu sprzed załącznika — wiersze ze znakiem » NIE są treścią aktu]",
                           set(), [], set()])
    out, w_zalaczniku = [], False
    niepewne = []       # (rodzaj, numer przypisu albo data, strona PDF od 1) — `_niepewne_miejsca_pdf`
    uzyte, koniec_strony = set(), {}      # (strona, nr) przypisów wypisanych przy odsyłaczu; strona → indeks w `out`
    jednostki = []      # (indeks w `out` przed akapitem, najniższa strona akapitu) — akapity zaczynające jednostkę
    for nr_akapitu, (naglowek, t, na_stronach, notki_akapitu, flagi) in enumerate(akapity):
        if re.match(r"(?i)załącznik(?!\s+do\s+obwieszczenia)", t):
            w_zalaczniku = True
        t = _pdf_normalizuj_indeksy(t)
        odsylacze = []
        t = _PDF_ODSYLACZ.sub(lambda m: odsylacze.append(m.group(1)) or "", t)
        if na_stronach and (naglowek or _PDF_POCZATEK_JEDNOSTKI.match(t)):
            jednostki.append((len(out), min(na_stronach)))
        if naglowek or re.match(r"^[\[<]?Art\.\s*\d", t):
            out.append("")
        out.append(_pdf_oznacz_obszar(t, flagi, min(na_stronach) if numeracja and na_stronach else None))
        # sama data w środku strony to komórka tabeli z datą (termin w wykazie), nie stopka — stopka stoi na dole strony
        # (13.11.2026: test syntetyczny — w próbie takiej tabeli nie było; dotąd każda taka komórka dawała „samodzielną datę")
        if na_stronach and _PDF_SAMA_DATA.fullmatch(t.replace(_ODS_POTW, "").replace(_ODS_NIE, "").strip()) \
                and nr_akapitu - przesuniecie not in daty_w_tresci:
            niepewne.append(("data", t.strip(), min(na_stronach) + 1 + strona0))
        if na_stronach and not t.startswith(("[", "» ")):
            niepewne.extend(("przypis_w_tresci", m.group(1), min(na_stronach) + 1 + strona0)
                            for m in _PDF_PRZYPIS_W_TRESCI.finditer(t.replace(_ODS_POTW, "").replace(_ODS_NIE, "")))
        for n in odsylacze:
            tresc = None
            for s in sorted(na_stronach) + [max(na_stronach) + 1, min(na_stronach) - 1]:
                if n in przypisy.get(s, {}):
                    tresc = przypisy[s][n]
                    uzyte.add((s, n))
                    break
            if tresc is None:
                # treść na innej stronie: przypis łamu/akapitu przeniesiony przez skład, przypisy tabeli pod jej końcem,
                # a w treści aktu także odsyłacz powtórzony na dalszych stronach (k.p. przypis 46 „Obecnie minister
                # właściwy do spraw pracy…" — treść tylko przy pierwszym odsyłaczu). Tylko gdy jednoznaczna. W ZAŁĄCZNIKU
                # — tylko z najwyżej 3 stron DALEJ: formularz i tabela mają własne przypisy „1)", „2)", a dotąd odsyłacz
                # z formularza („Grupa krwi i czynnik Rh1)", DU 2026/1391 s. 42) albo tabeli („0,01" + „2)", DU 2025/1016
                # s. 9) dostawał przypis o tym numerze z początku aktu („Minister Zdrowia kieruje działem…", „Przepisy
                # rozporządzenia wdrażają…"). 13.11.2026: na 40 losowych t.j. 2025–2026 przypisów branych z dalekiej
                # strony wstecz było 67; obejrzane DU 2026/1391 s. 42, DU 2025/609 s. 11–12, DU 2026/1197 s. 11 (układ
                # `-layout`: formularz ma własne „1) Jeżeli oznaczono…", „Niepotrzebne skreślić", „Wpisać…") i DU 2025/1016
                # s. 9 (obraz strony: objaśnienia pod tabelą) — wszystkie te w załącznikach błędne; w treści aktu odsyłacz
                # powtórzony dalej bywa prawidłowy, dlatego tam reguła zostaje. Lepiej „nie odnaleziono" niż cudzy przypis.
                inne = {p[n] for s, p in przypisy.items() if p.get(n)
                        and (not w_zalaczniku or max(na_stronach) < s <= max(na_stronach) + 3)}
                tresc = inne.pop() if len(inne) == 1 else None
                uzyte.update((s, n) for s, p in przypisy.items() if tresc and p.get(n) == tresc)
            out.append(f"[przypis {n})] {tresc}" if tresc else f"[przypis {n})] (treści przypisu nie odnaleziono na tej stronie PDF)")
            if not tresc and na_stronach:
                niepewne.append(("brak_tresci", n, min(na_stronach) + 1 + strona0))
        for n in notki_akapitu:
            out.append(f"[margines: {_pdf_normalizuj_indeksy(n)}]")
        if na_stronach:
            koniec_strony[max(na_stronach)] = len(out)
    # Przypis rozpoznany pod tekstem strony, do którego żaden odsyłacz nie trafił (odsyłacz w komórce tabeli albo
    # formularza, którego `-layout` nie skleił ze słowem), nie może zniknąć. 13.11.2026: DU 2025/609 (formularz) gubił
    # „9) Załączany dokument należy zaznaczyć X." i „15) Ustawa … – Kodeks karny", gdy blok przypisów zaczął być rozpoznawany.
    # Nie wiadomo jednak, której jednostki dotyczy — dotąd wychodził pod ostatnim akapitem strony w tej samej postaci co
    # przypis dopasowany, więc wyglądał na przypis tego akapitu: k.p.c. DU 2026/468 — przypis 3 (wyrok TK K 10/17, należy
    # do art. 1) stał między § 2 a § 3 art. 2, a `--fragment "art. 2"` dawał „UWAGA — WYROK TK W PRZYPISIE TEGO FRAGMENTU";
    # DU 2026/1473 — „Tytuł rozdziału w brzmieniu…" między pkt 2 a pkt 3 ustępu. Teraz ma etykietę `_przypis_bez_odsylacza`
    # (ostrzeżenia o przypisach fragmentu traktują go osobno) i stoi przed najbliższą jednostką (artykuł, paragraf, ustęp,
    # nagłówek) zaczynającą się na tej albo następnej stronie — nie rozcina wyliczenia; bez takiej jednostki — pod ostatnim
    # akapitem strony. Strona bez akapitów (sam blok przypisów) — pod ostatnim akapitem wcześniejszej strony.
    wstawki = []
    for nr in sorted(przypisy):
        osierocone = [_przypis_bez_odsylacza(n, tresc) for n, tresc in sorted(przypisy[nr].items(), key=lambda x: int(x[0]))
                      if (nr, n) not in uzyte and tresc]
        strona = nr if nr in koniec_strony else max((s for s in koniec_strony if s < nr), default=None)
        if not osierocone or strona is None:
            continue
        niepewne.extend(("bez_odsylacza", n, nr + 1 + strona0) for n, tresc in przypisy[nr].items()
                        if (nr, n) not in uzyte and tresc)
        poz = koniec_strony[strona]
        # indeks jednostki wskazuje pusty wiersz przed „Art."/nagłówkiem albo sam akapit „§ 2."/„3." — przypis idzie przed nie
        dalej = next((i for i, s in jednostki if i >= poz and s <= strona + 1), None)
        wstawki.append((poz if dalej is None else dalej, nr, osierocone))
    for poz, _, osierocone in sorted(wstawki, reverse=True):
        out[poz:poz] = osierocone
    if info is not None and niepewne:
        info["niepewne_miejsca"] = niepewne
    t = "\n".join(out).replace(_ODS_POTW, "").replace(_ODS_NIE, "")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    t, obszary, poczatki = _pdf_zdejmij_znaczniki(t)
    opisy = {(nr, r): o for nr, flagi in (strony_flagi or {}).items() for r, o in flagi}
    wynik = [(r, opisy.get((od, r), ""), od + 1 + strona0, do + 1 + strona0, s, e)
             for r, od, do, s, e in _pdf_scal_obszary(obszary)]
    if numeracja:
        for opis, s, e in _luki_numeracji(t):
            od, do = ([nr for p, nr in poczatki if p <= x] or [0] for x in (s, e - 1))
            wynik.append(("numeracja", opis, od[-1] + 1 + strona0, max(od[-1], do[-1]) + 1 + strona0, s, e))
    if info is not None and wynik:
        info["niepewne_obszary"] = sorted(wynik, key=lambda o: o[4])
    return t


# Dz.U. i M.P. 1990–2011: PDF ogłoszonego aktu (typ O) to strony całego zeszytu, złożone w dwóch łamach, więc
# zawiera też akty wydrukowane na tych samych stronach (w latach 90. — skan z warstwą tekstową z OCR). W latach 2000–2009 polskie litery są w fontach
# QuarkXPress „…PL" z kodami Mac Central European, które PDF opisuje jako Mac Roman: pdftotext daje
# „Za∏àcznik", „rozporzàdzenia", „wyp∏at´". Tabela zmienia też prawdziwe „à", „ç", „è" (np. francuskie
# teksty umów), dlatego tylko dla tych lat.
_MAC_CE = str.maketrans({bytes([b]).decode("mac_roman"): bytes([b]).decode("mac_latin2")
                         for b in range(128, 256) if bytes([b]).decode("mac_latin2") in "ąćęłńśźżĄĆĘŁŃŚŹŻ"})
# pionowy znak wodny www.rcl.gov.pl na stronach z lat 2010–2011 biegnie przez rynnę; -layout daje go
# jako osobne wiersze po 1–3 znaki („.go", „v.p", „rcl")
_ZNAK_WODNY = set("www.rcl.gov.pl")
_PDF_MIN_LAM = 0.35   # wiersz tylko na lewo od rynny, a zaczyna się dalej niż tyle szerokości strony = wyśrodkowany
_PDF_RYNNA_WOLNE = 0.5   # rynna: tyle niepustych wierszy strony ma w niej spację…
_PDF_RYNNA_OBA = 0.15    # …a tyle ma tekst po obu jej stronach
_PDF_TABELA = 0.3   # tyle wierszy bloku z ≥2 przerwami ≥3 spacji = wiersze tabeli, nie dwa łamy
_PDF_LUZ_OCR = 8     # lata 90. (OCR): o tyle znaków łam jednego aktu może odbiegać od rynny strony
# kreska nad przypisami na dole łamu („———————" + „1) Minister Kultury kieruje działem…")
_PDF_KRESKA = re.compile(r"^[—–_-]{5,}$")
_MOJIBAKE = "∏Ê˝ƒÑ¸Â˚¡"   # bez „à"/„´": te bywają prawdziwe (francuski tekst umowy)


def _pdf_bez_znaku_wodnego(raw):
    out = []
    for l in raw.split("\n"):
        t = l.strip("\f ").replace(" ", "")
        # kawałek napisu „www.rcl.gov.pl" („.go", „v.p", „rcl .", „ov.pl") — w łamie z lat 2010–2011 samotne
        # „w"/„o" na końcu wiersza nie występują (reguła składu), więc nie gubimy treści
        if t and (len(t) <= 5 and t in "www.rcl.gov.pl" or len(t) <= 4 and set(t) <= _ZNAK_WODNY):
            l = "\f" if l.startswith("\f") else ""
        else:
            # „w. ww Dziennik Ustaw Nr 1 — 3 — Poz. 1": kawałek znaku wodnego przed nagłówkiem strony
            l = re.sub(r"^([\f ]*)(?:[wrclgovp.]{1,4} +)+(?=(?:Dziennik Ustaw|Monitor Polski)\s)", r"\1", l)
            # „alarmowego,          l": kawałek znaku wodnego w rynnie na końcu wiersza łamu (DU 2010 poz. 1128)
            l = re.sub(r"(?<=\S) {8,}([wrclgovp.]{1,3}) *$", lambda m: "" if m.group(1) in "www.rcl.gov.pl" else m.group(0), l)
        out.append(l)
    return "\n".join(out)


# okruch OCR: wiersz z samej drobnej interpunkcji (bez liter, cyfr, „§", myślników i gwiazdek)
_PDF_OKRUCH_OCR = re.compile(r"[.,:;'`\"·•~^°„”’‘]{1,8}")


def _pdf_lamy(strona, luz=0):
    """Strona z `pdftotext -layout` złożona w dwóch łamach → najpierw lewy łam, potem prawy.

    -layout stawia łamy obok siebie, więc sklejanie wierszy mieszałoby je („ustawy z dnia § 4. 1. Minimalna
    norma"). Rynna = kolumna znaków, w której (z sąsiednią) prawie każdy wiersz ma spację. Wiersze przez nią
    przechodzące (tytuł aktu, tabela na całą szerokość), nagłówek strony i wiersze wyśrodkowane (numer pozycji aktu)
    zostają na swoim miejscu i dzielą stronę na bloki; każdy blok to lewy łam, potem prawy, oba bez wcięcia
    strony. Strona bez takiej rynny wraca bez zmian.

    `luz` (OCR skanów z lat 90.): łamy jednego aktu bywają przesunięte o kilka znaków względem rynny strony
    („…Układu Europejs-       1991 r." wchodzi lewym łamem w rynnę). Wiersz przecinający rynnę, który ma przerwę
    ≥3 spacji najwyżej `luz` znaków od niej, dzieli się w tej przerwie zamiast zamykać blok.

    Przy `luz` wiersz z samych okruchów OCR („'. :", „." . .") jest pusty: dotąd taki okruch w środku strony, daleko od lewego
    marginesu, uchodził za wyśrodkowany tytuł i zamykał blok — DU 1994/73 s. 4: prawy łam nad okruchem (§ 46–48) wychodził
    przed resztą lewego (§ 39 pkt 3, § 40–45; 13.11.2026, obraz strony).
    """
    lines = strona.split("\n")
    if luz:
        lines = [("\f" if l.startswith("\f") else "") if _PDF_OKRUCH_OCR.fullmatch(re.sub(r"[\s\f]", "", l)) else l
                 for l in lines]
    niepuste = [l for l in lines if l.strip()]
    if len(niepuste) < 8:
        return strona
    szer = max(len(l.rstrip()) for l in niepuste)

    def wolne(l, x):
        return all(x + k >= len(l) or l[x + k] == " " for k in (0, 1))
    najl, rynna = 0, None
    for x in range(int(szer * 0.35), int(szer * 0.65)):
        w = sum(wolne(l, x) for l in niepuste)
        oba = sum(wolne(l, x) and l[:x].strip() != "" and l[x:].strip() != "" for l in niepuste)
        if oba >= _PDF_RYNNA_OBA * len(niepuste) and w > najl:
            najl, rynna = w, x
    if rynna is None or najl < _PDF_RYNNA_WOLNE * len(niepuste):
        return strona

    # prawa krawędź lewego łamu: najczęstszy koniec lewej części wierszy z tekstem po obu stronach rynny
    konce = {}
    for l in niepuste:
        if wolne(l, rynna) and l[:rynna].strip() and l[rynna:].strip():
            konce[len(l[:rynna].rstrip())] = konce.get(len(l[:rynna].rstrip()), 0) + 1
    prawa_lewego = max(konce, key=konce.get) if konce else None

    def podzial(k, w_lamie=False):
        """Kolumna, w której wiersz `k` dzieli się na łamy, albo None (wiersz zostaje na swoim miejscu).
        `w_lamie`: poprzedni niepusty wiersz to wiersz łamu (tekst po obu stronach rynny albo do krawędzi łamu)."""
        l = lines[k]
        if _PDF_NAGLOWEK.match(l) or _pdf_numer_pozycji(lines, k):
            return None
        if wolne(l, rynna):
            if l[rynna:].strip() or len(l) - len(l.lstrip(" ")) < _PDF_MIN_LAM * szer:
                return rynna
            # wiersz tylko po lewej, daleko od marginesu: wyśrodkowany na stronie numer pozycji albo tytuł („USTAWA")
            # zamyka blok, ale podpis dosunięty do prawej krawędzi LEWEGO łamu („W. Dąbrowski" nad pustym prawym
            # łamem) to zwykły wiersz łamu — inaczej trafiłby za prawy łam. Środka strony nie da się tu wiarygodnie
            # zmierzyć (-layout zwęża duże litery tytułu), więc rozpoznajemy podpis: stoi pod wierszem łamu, kończy się
            # dokładnie na prawej krawędzi lewego łamu, ma co najmniej dwa wyrazy i małe litery (tytuł, numer pozycji
            # i data pod tytułem — „z dnia 30 czerwca 2011 r." — tego nie spełniają)
            if w_lamie and prawa_lewego is not None and abs(len(l.rstrip()) - prawa_lewego) <= 1 \
                    and len(l.split()) >= 2 and re.search(r"[a-ząćęłńóśźż]", l):
                return rynna
            return None
        przerwy = [m for m in re.finditer(r" {3,}", l) if m.start() - luz <= rynna <= m.end() + luz
                   and l[:m.start()].strip() and l[m.end():].strip()] if luz else []
        if not przerwy:
            return None
        m = min(przerwy, key=lambda m: abs((m.start() + m.end()) // 2 - rynna))
        return (m.start() + m.end()) // 2

    def bez_wciecia(ws):
        wc = min((len(l) - len(l.lstrip(" ")) for l in ws if l.strip()), default=0)
        return [l[wc:].rstrip() for l in ws]

    def przypisy_lamu(ws):
        """Łam kończący się kreską i przypisami („———————", „1) Minister Kultury kieruje…") → (łam bez nich,
        [kreska + przypisy]). Przypisy z dołu LEWEGO łamu wypadałyby inaczej między łamami, w środku treści."""
        for r in range(len(ws) - 1, -1, -1):
            if _PDF_KRESKA.match(ws[r].strip()):
                reszta = [l for l in ws[r + 1:] if l.strip()]
                if reszta and _PDF_PRZYPIS.match(reszta[0]) and all(_PDF_PRZYPIS.match(l) or l.startswith(" ") for l in reszta):
                    return ws[:r], [ws[r].strip()] + [l for l in ws[r + 1:] if l.strip()]
                break
        r = przypisy_bez_kreski(ws)
        if r is not None:
            return ws[:r], ["—————"] + [l for l in ws[r:] if l.strip()]
        return ws, []

    def przypisy_bez_kreski(ws):
        """Kreska nad przypisami bywa grafiką, nie tekstem (DU 2010 poz. 1128 s. 1): przypisy „1) Minister … kieruje
        działem…", „2) Zmiany tekstu jednolitego…" stoją wtedy na dole łamu bez „———". Rozpoznajemy je po kolei od
        końca łamu: numery „N)" na lewym marginesie łamu, kolejne, każdy z ciągiem wierszy wciętych; wszystkie numery
        mają odsyłacz sklejony z tekstem strony („ADMINISTRACJI1)", „zm.2))"), a blok ma frazę przypisu. Zwraca indeks
        pierwszego wiersza przypisów albo None."""
        tresc = [k for k, l in enumerate(ws) if l.strip()]
        poczatki = []       # wiersze „N)" na lewym marginesie, od końca łamu: N, N-1, …
        for k in reversed(tresc):
            if ws[k].startswith(" "):
                continue        # ciąg przypisu (albo, wyżej, zwykły wiersz łamu)
            m = _PDF_PRZYPIS.match(ws[k])
            if not m or poczatki and int(m.group(1)) != int(_PDF_PRZYPIS.match(ws[poczatki[-1]]).group(1)) - 1:
                break
            poczatki.append(k)
        if not poczatki or poczatki[-1] == tresc[0]:
            return None
        pierwszy = poczatki[-1]
        ciag = [ws[k] for k in tresc if k > pierwszy and k not in poczatki]
        # ciąg przypisu ma jedno wcięcie (szerokość „N) ") i nie zaczyna jednostki; blok nie jest dłuższy niż pół łamu
        wciecia = {len(l) - len(l.lstrip(" ")) for l in ciag}
        if len(wciecia) > 1 or wciecia and max(wciecia) > 5 or len(ciag) + len(poczatki) > max(4, len(tresc) // 2) \
                or any(_PDF_JEDNOSTKA.match(l.strip()) or _PDF_NOWY_AKAPIT.match(l.strip()) for l in ciag):
            return None
        blok = ""
        for k in tresc:
            if k >= pierwszy:
                blok = _doklej(blok, " ".join(ws[k].split()))     # justowanie: „2) Zmiany   tekstu"
        numery = {_PDF_PRZYPIS.match(ws[k]).group(1) for k in poczatki}
        if numery <= odsylacze_strony and any(f in blok for f in _PDF_FRAZY_PRZYPISU):
            return pierwszy
        return None
    odsylacze_strony = set(_PDF_ODSYLACZ.findall(strona))
    out, blok = [], []     # blok: [(wiersz, kolumna podziału)]

    def zamknij():
        tresc = [l.strip() for l, _ in blok if l.strip()]
        # blok z wierszami tabeli (≥2 wewnętrzne przerwy ≥3 spacji: „4   Radio z magnetofonem…   1,5") — nie łamy;
        # zostaje w całości, jak dotąd
        tabela = sum(len(re.findall(r"\S {3,}(?=\S)", l)) >= 2 for l in tresc) >= _PDF_TABELA * len(tresc)
        if not tresc or tabela:
            out.extend(l for l, _ in blok)
        else:
            lewy, przyp_l = przypisy_lamu(bez_wciecia([l[:x] for l, x in blok]))
            prawy, przyp_p = przypisy_lamu(bez_wciecia([" " * x + l[x:] for l, x in blok]))
            out.extend(lewy)
            out.extend(prawy)
            if przyp_l or przyp_p:      # pod oboma łamami, pod jedną kreską, między pustymi wierszami — patrz `_pdf_strona`
                out.extend(["", (przyp_l or przyp_p)[0]] + przyp_l[1:] + przyp_p[1:] + [""])
        blok.clear()
    w_lamie = False
    for k, l in enumerate(lines):
        x = podzial(k, w_lamie) if l.strip() else rynna
        if l.strip():   # wiersz łamu: z tekstem po obu stronach rynny albo do prawej krawędzi lewego łamu
            w_lamie = x is not None and (bool(l[x:].strip()) or prawa_lewego is not None
                                         and abs(len(l.rstrip()) - prawa_lewego) <= 1)
        if x is not None:
            blok.append((l, x))
        else:
            zamknij()
            out.append(l)
    zamknij()
    return "\n".join(out)


def _pdf_numer_pozycji(lines, i, n=None):
    """Czy wiersz `i` to sam numer pozycji aktu (`n` albo dowolny) nad tytułem aktu?"""
    t = lines[i].strip("\f ")
    # OCR z lat 90. dokleja do numeru okruchy („463 .", „602 '")
    if not (re.sub(r"[\s.,'`]+$", "", t) == str(n) if n is not None else t.isdigit() and len(t) <= 5):
        return False
    # pod numerem pozycji stoi tytuł aktu WIELKIMI LITERAMI („ROZPORZĄDZENIE MINISTRA ZDROWIA1)", „UCHWAŁA Nr 85 RADY MINISTRÓW") albo numer
    # rejestru postanowienia Prezydenta („Rej. 250/2002") albo sygnatura uchwały TK („Sygn. akt W. 7/96"; OCR: „Rej . 184/94"); komórka tabeli / „Załącznik nr 2" / nagłówek strony — nie
    dalej = next((l.strip("\f ") for l in lines[i + 1:i + 9]
                  if l.strip("\f ") and not _PDF_NAGLOWEK.match(l) and not _PDF_STOPKA.match(l)), "")
    litery = [c for c in dalej if c.isalpha()]
    return bool(re.match(r"(?:Rej|Sygn)\s?\.", dalej)) or bool(litery) and sum(c.isupper() for c in litery) >= 0.8 * len(litery)


def _pdf_wytnij_akt(raw, poz, ocr=False, start=None):
    """Tekst zeszytu → tylko akt poz. `poz`: od wiersza z samym numerem pozycji (nad tytułem aktu) do
    wiersza z numerem następnej pozycji albo do końca. Bez znalezionego numeru tekst wraca bez zmian.
    `ocr` (lata 90.): numer następnej pozycji może mieć jedną cyfrę źle odczytaną („600" zamiast „500").
    `start` (lista) dostaje numer strony PDF (od 0), na której zaczyna się wycięty tekst — strony wyniku liczone od niej."""
    lines = raw.split("\n")
    od = next((i for i in range(len(lines)) if _pdf_numer_pozycji(lines, i, poz)), None)
    if od is None:
        return raw
    nast = str(poz + 1)

    def nastepny(i):
        if _pdf_numer_pozycji(lines, i, poz + 1):
            return True
        t = lines[i].strip("\f ")
        return ocr and len(t) == len(nast) and t != str(poz) and sum(a != b for a, b in zip(t, nast)) == 1 \
            and _pdf_numer_pozycji(lines, i)
    do = next((i for i in range(od + 1, len(lines)) if nastepny(i)), len(lines))
    if start is not None:
        start.append("\n".join(lines[:od]).count("\f"))
    return "\n".join(lines[od:do])


# Stopka wydawnicza zeszytu (prenumerata, wydawca, ISSN, cena) — na ostatniej stronie zeszytu, więc trafia
# do tekstu OSTATNIEGO aktu w zeszycie. Początek stopki rozpoznajemy po stałych frazach z lat 2000–2011.
_PDF_STOPKA_ZESZYTU = re.compile(r"Egzemplarze bieżące oraz archiwalne|Wydawca: Kancelaria|Szanowni Państwo"
                                 r"|WYDZIAŁ WYDAWNICTW I POLIGRAFII")
# Lata 90. (OCR, porównanie bez spacji: „Egzemplarze b i eżące"): stopkę poprzedzają ogłoszenia wydawcy — punkty
# sprzedaży („Pojedyncze egzemplarze Dziennika Ustaw i Monitora Polskiego można nabywać…"), prenumerata,
# „Uprzejmie informujemy, iż nakładem…" — też nie są treścią aktu
_PDF_STOPKA_ZESZYTU_OCR = re.compile(r"Egzemp\w{2,5}bieżące|Pojedyncze\W{0,2}egzemplarze|^Wydawca:|SzanowniPaństwo"
                                     r"|[Uu]przejmieinformuj|Cena(?:rocznej)?prenumeraty|WYDZIAŁWYDAWNICTW")


# Ogłoszenie wydawcy na ostatnich stronach zeszytu (2000–2011): nagłówek wersalikami (porównanie bez spacji — także
# po OCR skanu) i znacznik oferty dalej na stronie
_PDF_REKLAMA = re.compile(r"CENTRUMOBSŁUGIKANCELARII|WYDZIAŁWYDAWNICTWIPOLIGRAFII")
_PDF_REKLAMA_OFERTA = re.compile(r"(?i)\bproponuje\b|Pełna\s+oferta|Cena\s+brutto|wydawnictwa\s*\.\s*cokprm|infolinia"
                                 r"|zamówie\w+\s+można|prenumerat")


def _pdf_reklama(linie):
    """Indeks wiersza, od którego na stronie zaczyna się ogłoszenie wydawcy („CENTRUM OBSŁUGI KANCELARII PREZESA RADY
    MINISTRÓW / WYDZIAŁ WYDAWNICTW I POLIGRAFII / proponuje … Cena brutto … Pełna oferta: www.wydawnictwa…"), albo
    None. Nagłówek ogłoszenia jest WERSALIKAMI (w treści aktu ta nazwa ma zwykłą pisownię), a dalej na stronie musi
    stać znacznik oferty (`_PDF_REKLAMA_OFERTA`) — sama nazwa wydawcy w stopce zeszytu („Skład, druk i kolportaż:
    Centrum Obsługi…") ogłoszeniem nie jest."""
    for k, l in enumerate(linie):
        if _PDF_REKLAMA.search(re.sub(r"\s", "", l)) and _PDF_REKLAMA_OFERTA.search("\n".join(linie[k:])):
            return k
    return None


def _pdf_strona_reklamowa(tekst):
    """Cała strona to ogłoszenie wydawcy: przed jego nagłówkiem stoi najwyżej numer/nagłówek strony (≤ 3 krótkie
    wiersze). Takie strony bywają skanami bez warstwy tekstowej (DU 2010 poz. 20 i 1128) — po OCR nie są treścią aktu."""
    linie = [l for l in tekst.split("\n") if l.strip()]
    k = _pdf_reklama(linie)
    return k is not None and k <= 3 and all(len(l.strip()) <= 60 for l in linie[:k])


def _pdf_bez_stopki_zeszytu(raw, ocr=False):
    """Usuwa stopkę wydawniczą z ostatniej strony zeszytu (tej z „ISSN …"); bez niej tekst wraca bez zmian.
    Działa na wyniku `-layout` PRZED rozdzieleniem łamów — stopka jest na całą szerokość strony, pod oboma łamami.
    Ogłoszenie wydawcy (`_pdf_reklama`) przed stopką — na tej samej stronie albo na jednej z dwóch poprzednich —
    też nie jest treścią aktu: cięcie zaczyna się od niego (DU 2010 poz. 1128: strona-reklama „RID … Pełna oferta"
    doklejała się do zał. 6 pkt 5).
    `ocr` (lata 90.): początek stopki także po frazach `_PDF_STOPKA_ZESZYTU_OCR`."""
    def poczatek(l):
        return _PDF_STOPKA_ZESZYTU.search(l) or ocr and _PDF_STOPKA_ZESZYTU_OCR.search(re.sub(r"\s", "", l))
    strony = raw.split("\f")
    nr = next((i for i in range(len(strony) - 1, -1, -1) if re.search(r"ISSN\s*\d{4}-\d{3}[\dX]", strony[i])), None)
    if nr is None:
        return raw
    ciecie = None       # (strona, wiersz) — początek stopki
    for i in range(nr, max(nr - 2, -1), -1):      # stopka nie zaczyna się dalej niż stronę przed tą z ISSN
        k = next((j for j, l in enumerate(strony[i].split("\n")) if poczatek(l)), None)
        if k is not None:
            ciecie = (i, k)
            break
    # ogłoszenie wydawcy przed stopką: najwcześniejsze, za którym do strony z ISSN są już tylko ogłoszenia, puste
    # strony albo stopka (tekstu aktu nie tniemy)
    for i in range(max(nr - 2, 0), nr + 1):
        r = _pdf_reklama(strony[i].split("\n"))
        if r is None or ciecie is not None and (i, r) >= ciecie:
            continue
        dalej = range(i + 1, ciecie[0] if ciecie else nr + 1)
        if all(_pdf_strona_reklamowa(strony[j]) or len(re.sub(r"[\W\d_]", "", strony[j])) < 20 for j in dalej):
            ciecie = (i, r)
            break
    if ciecie is None:
        return raw
    i, k = ciecie
    strony[i] = "\n".join(strony[i].split("\n")[:k])
    for j in range(i + 1, nr + 1):
        strony[j] = ""
    return "\f".join(strony)


# Lata 1990–1999: warstwa tekstowa zeszytu to OCR skanu, więc nagłówek strony bywa zniekształcony, z myślnikami
# ASCII albo bez nich („Dziennik Ustaw Nr 24   319   Poz. 141 i 142", „Dzienn ik Ustaw Nr 98 ~ 3089 ~ Poz. 602",
# „Dzienr.!k Ustaw Nr 119 - 1655 - Poz. 517", „Monitor Polski Nr 65 .. 834 Poz. 578", samo „- 2186 - Poz. 428").
# Dopasowanie do wiersza BEZ spacji i tylko do pierwszego niepustego wiersza strony.
_PDF_NAGLOWEK_OCR = re.compile(r"(?:Dz\S{2,8}Ustaw|MonitorPolski)\S{0,2}Nr\S{0,40}?P[oa]z[.,]"
                               r"|[-—–~.,:']*\d{1,5}[-—–~.,:']*P[oa]z[.,]|[-—–~]+\d{1,5}[-—–~]+$")
_PDF_NAGLOWEK_RESZTA = re.compile(r"(?:Dz\S{2,8}Ustaw|MonitorPolski)\S{0,2}Nr[\d.,]{1,6}\S{0,2}$")
# „TREŚĆ:" nad spisem treści na pierwszej stronie zeszytu (OCR: „TRE$Ć:", „TREŚC:", „TREŚĆ,")
_PDF_TRESC = re.compile(r"^\s*TRE\S{1,3}\s*$")


def _pdf_bez_naglowkow_stron(raw):
    """Usuwa nagłówek strony zeszytu (także zniekształcony przez OCR, patrz `_PDF_NAGLOWEK_OCR`) z początku każdej
    strony, razem z okruchami nad nim („•", „I") i z drugą połową nagłówka, którą OCR dał w osobnym wierszu
    („- 2186 - Poz. 428 i 429" + „Dziennik Ustaw Nr 85")."""
    strony = raw.split("\f")
    for n, s in enumerate(strony):
        linie = s.split("\n")
        smieci, naglowek = [], False
        for k, l in enumerate(linie):
            t = re.sub(r"\s", "", l)
            if not t:
                continue
            if _PDF_NAGLOWEK_OCR.match(t) or naglowek and _PDF_NAGLOWEK_RESZTA.match(t):
                for j in smieci + [k]:
                    linie[j] = ""
                smieci, naglowek = [], True
                continue
            # nagłówek rozbity przez OCR tesseracta na trzy krótkie wiersze: „Dziennik Ustaw Nr 21 —" ⏎ „274 —" ⏎ „Poz. 73"
            # (DU 1994/73 s. 7 — skan bez warstwy tekstowej; dotąd wchodził w treść § 85 ust. 2)
            dalej = [j for j in range(k, len(linie)) if linie[j].strip()][:3]
            if not naglowek and len(dalej) == 3 and all(len(linie[j].strip()) <= 30 for j in dalej) \
                    and re.match(r"(?:Dz\S{2,8}Ustaw|MonitorPolski)", t) \
                    and _PDF_NAGLOWEK_OCR.match("".join(re.sub(r"\s", "", linie[j]) for j in dalej)):
                for j in smieci + dalej:
                    linie[j] = ""
                break
            if len(t) <= 2 and not naglowek:
                smieci.append(k)
                continue
            break
        strony[n] = "\n".join(linie)
    return "\f".join(strony)


def _pdf_bez_spisu_tresci(raw):
    """Pierwsza strona zeszytu: winieta („DZIENNIK USTAW … Warszawa, dnia … Nr 55") i spis treści („TREŚĆ: Poz.:
    351 - z dnia …  2133") aż do numeru pierwszej pozycji wymienionej w spisie. Spis biegnie przez całą szerokość
    strony, więc zasłaniałby rynnę między łamami aktu pod nim. Bez numeru pozycji na tej stronie — bez zmian."""
    strony = raw.split("\f")
    for n, s in enumerate(strony):
        linie = s.split("\n")
        niepuste = [k for k, l in enumerate(linie) if l.strip()][:8]
        t = next((k for k in niepuste if _PDF_TRESC.match(linie[k])), None)
        if t is None:
            continue
        w_spisie = {m.group(1) for l in linie[t + 1:] for m in [re.match(r"\s*(\d{1,5})\s*[-—–~.,]", l)] if m}
        od = next((k for k in range(t + 1, len(linie)) if linie[k].strip() in w_spisie
                   and _pdf_numer_pozycji(linie, k)), None)
        if od is not None:
            strony[n] = "\n".join(linie[od:])
    return "\f".join(strony)


def pdf_zeszyt_do_aktu(raw, rok, poz, start=None):
    """`pdftotext -layout` PDF-u ogłoszonego aktu Dz.U./M.P. 1990–2011 → tekst tylko tego aktu, łam po łamie,
    z polskimi literami (2000–2009), bez nagłówków stron, spisu treści i stopki zeszytu. Dla lat 90. (OCR skanu)
    także nagłówki zniekształcone przez OCR, łamy przesunięte względem rynny strony i ogłoszenia wydawcy przed
    stopką. Wynik idzie dalej do `pdf_layout_do_tekstu`."""
    if rok >= 2010:
        raw = _pdf_bez_znaku_wodnego(raw)
    # fonty Mac CE poznaje się po treści (DU 2010 poz. 1 ma je mimo roku): strona bez żadnej polskiej litery,
    # za to ze znakami „∏", „Ê", „˝"… — tabela tylko dla takich stron
    raw = "\f".join(s.translate(_MAC_CE) if any(c in s for c in _MOJIBAKE)
                     and not any(c in s for c in "ąęłńśźżĄĘŁŃŚŹŻ") else s for s in raw.split("\f"))
    raw = _pdf_bez_stopki_zeszytu(raw, ocr=rok < 2000)
    raw = _pdf_bez_naglowkow_stron(raw)
    raw = _pdf_bez_spisu_tresci(raw)
    # sam numer strony zeszytu („— 3052 —") na stronie bez nagłówka „Dziennik Ustaw Nr …"
    raw = re.sub(r"(?m)^[ \f]*[—–]\s*\d{1,5}\s*[—–][ ]*$", lambda m: "\f" if "\f" in m.group(0) else "", raw)
    raw = "\f".join(_pdf_lamy(s, luz=_PDF_LUZ_OCR if rok < 2000 else 0) for s in raw.split("\f"))
    return _pdf_wytnij_akt(raw, poz, ocr=rok < 2000, start=start) if poz else raw


def _wybierz_pdf(meta):
    """Najlepszy urzędowy PDF z metadanych: tekst ujednolicony (U) > jednolity (T) > ogłoszony (O)."""
    texts = meta.get("texts") or []
    for code in ("U", "T", "O"):
        pick = next((t for t in texts if t.get("type") == code
                     and (t.get("fileName") or "").lower().endswith(".pdf")), None)
        if pick:
            return pick
    return None


# granice jednostek redakcyjnych w tekście po konwersji (nagłówki na początku linii)
_GRANICE = r"(?m)^[\[<]?(Art\.\s*\d|Tytuł\s|TYTUŁ\s|Dział\s|DZIAŁ\s|Rozdział\s|Oddział\s|Księga\s|KSIĘGA\s|Załącznik)"

# unicodowe indeksy górne (art. 21¹) — do rozpoznania w zapytaniu i przełożenia na cyfry ASCII
_SUPS = "¹²³⁴⁵⁶⁷⁸⁹⁰"
# litera indeksu górnego we frazie („art. 130¹ᵃ", „art. 18³ᵉ" k.p.) — dotąd fraza z taką literą nie była rozpoznawana
# jako oznaczenie artykułu i kończyła się „Nie znaleziono frazy" (13.11.2026, k.p.c. DU 2026/468 art. 130¹ᵃ)
_SUPS_LITERY = "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ"
_SUP = str.maketrans(_SUPS + _SUPS_LITERY, "1234567890" + "abcdefghijklmnoprstuvwxyz")
_NA_SUP = str.maketrans("1234567890", _SUPS)

# Warianty myślnika (w tekstach ELI trafia się m.in. U+2012) — ujednolicane WYŁĄCZNIE na potrzeby
# porównania, znak w znak, żeby nie przesunąć pozycji względem oryginału.
_MYSLNIKI = str.maketrans("‐‑‒–—―−", "-------")


def _norm(s):
    """Postać do porównywania fraz: bez rozróżniania wielkości liter i wariantów myślnika."""
    return s.translate(_MYSLNIKI).lower()


# Koniec oznaczenia artykułu w nagłówku: albo kropka ("Art. 66."), albo ODSYŁACZ DO PRZYPISU
# sklejony z numerem ("Art. 66c 6)Dodany przez art. 3 pkt 2 ustawy…" — kropka artykułu jest
# dopiero za treścią przypisu). Przypis nowelizacyjny ma w tekście jednolitym KAŻDY niedawno
# dodany lub zmieniony przepis, więc wymaganie samej kropki dawało fałszywy negatyw dokładnie
# tam, gdzie prawo jest najświeższe. Rozróżnienie od indeksu górnego („Art. 60 1." = art. 60¹)
# trzyma się nawiasu: przypis to CYFRY + „)", indeks górny — cyfry + kropka albo litera.
# Spacja przed przypisem jest OBOWIĄZKOWA (odsyłacz siedzi w <sup>, a _Stripper zawsze go
# odspacjowuje) — bez tego „art. 669¹" łapało też „Art. 669 101)", czyli art. 669 z przypisem 101.
_KONIEC_ART = r"(?:\.|\s+\d+\))"


# Oznaczenie jednostki we frazie: baza + opcjonalny INDEKS GÓRNY (art. 21¹: unicode "21¹", nawiasowy "21(1)"/"21[1]"/
# "21^1", z /struct "21_1") + opcjonalny SUFIKS LITEROWY (art. 1a, art. 168e)
_NUMER_FRAZY = (r"(\d+[a-z]*?)"                                           # (1) baza (też z literą przed indeksem: „6b^3")
                r"(?:[\(\[\^_]\s*(\d+[a-z]?)\s*[\)\]]?|([" + _SUPS + r"]+[" + _SUPS_LITERY + r"]?))?"   # (2) nawiasowy | (3) unicode
                r"([a-z]*)\.?")                                          # (4) sufiks literowy


def _wzor_numeru(m, od=1):
    """Grupy `_NUMER_FRAZY` (od grupy `od`) → wzorzec numeru w tekście. Indeks górny jest w tekście rozdzielony spacją
    („Art. 21 1." — patrz _Stripper), a sufiks literowy sklejony („Art. 1a."); dlatego indeks z \\s+, a litera z \\s*."""
    base, idx, sup, letter = m.group(od), m.group(od + 1), m.group(od + 2), m.group(od + 3) or ""
    idx = idx or (sup.translate(_SUP) if sup else "")
    # "art. 130(1a)" → indeks "1" + litera "a"; w tekście i one bywają rozdzielone
    # („Art. 130 1 a."), więc literę doklejamy z \s*, nie na sztywno.
    mi = re.match(r"(\d+)([a-z]*)$", idx)
    if mi:
        idx, letter = mi.group(1), letter or mi.group(2)
    if idx:                       # indeks górny → w tekście rozdzielony spacją
        return rf"{re.escape(base)}\s+{re.escape(idx)}\s*{re.escape(letter)}"
    if letter:                    # sufiks literowy → sklejony z numerem
        return rf"{re.escape(base)}\s*{re.escape(letter)}"
    return re.escape(base)


def _hity_naglowka(txt, fraza):
    """Pozycje NAGŁÓWKÓW artykułu wskazanego frazą ("art. 299", "art. 21¹", "art. 66c").

    Pusta lista = fraza nie jest oznaczeniem artykułu ALBO tego artykułu nie ma w akcie.
    """
    m = re.match(r"(?i)^art\.?\s*" + _NUMER_FRAZY + "$", fraza.strip())
    if not m:
        return []
    pat = rf"(?m)^[\[<]?Art\.\s*{_wzor_numeru(m)}{_KONIEC_ART}"
    return [h.start() for h in re.finditer(pat, txt)]


# „§ 4", „§ 2¹", „§ 2(1)", „art. 25 § 2" (paragraf w artykule)
_FRAZA_PARAGRAFU = re.compile(r"(?i)^(?:art\.?\s*(\S+?)\s*)?§\s*" + _NUMER_FRAZY + "$")
# nagłówek paragrafu na początku wiersza — sam („§ 4." w HTML) albo za nagłówkiem artykułu w tym samym wierszu
# („Art. 25. § 1. O nazwisku…" w tekście z PDF)
_NAGLOWEK_ART_W_WIERSZU = r"(?:Art\.[ ]*\d+[a-z]*(?:[ ]+\d+[a-z]*)?[ ]*\.[ ]*(?:\d+\)[ ]*)?[\[<]?)?"
_NAGLOWEK_PARAGRAFU = re.compile(r"(?m)^[\[<]?" + _NAGLOWEK_ART_W_WIERSZU + r"§\s*\d+[a-z]*(?:\s+\d+[a-z]*)?\s*" + _KONIEC_ART)
_NAGLOWEK_ART = re.compile(r"(?m)^[\[<]?Art\.\s*\d")
_OTWARCIE, _ZAMKNIECIE = "„", "”“"
_MAKS_CYTAT = 30000     # znaków: dłużej otwarty cudzysłów uznajemy za zgubione zamknięcie


def _naglowki_paragrafow(txt):
    """Pozycje nagłówków paragrafów TEGO aktu (początki wierszy) — bez paragrafów cytowanych w przepisach zmieniających.

    Akt zmieniający cytuje nowe brzmienie („§ 5 otrzymuje brzmienie: „§ 5. …”"): w HTML cudzysłów stoi w osobnym
    wierszu, a kolejne cytowane paragrafy („§ 6. …") zaczynają wiersz jak nagłówki. Odrzucamy nagłówki, przed którymi
    — licząc od poprzedniego przyjętego nagłówka artykułu albo paragrafu — został otwarty, a niezamknięty cudzysłów.
    Gdy filtr odrzuciłby wszystko (zgubiony cudzysłów zamykający), zostają wszystkie nagłówki."""
    par = {m.start() for m in _NAGLOWEK_PARAGRAFU.finditer(txt)}
    kand = sorted(par | {m.start() for m in _NAGLOWEK_ART.finditer(txt)})
    cudzyslowy = [(m.start(), m.group(0) in _OTWARCIE) for m in re.finditer("[" + _OTWARCIE + _ZAMKNIECIE + "]", txt)]
    przyjete, otwarte, q = [], [], 0
    for c in kand:
        while q < len(cudzyslowy) and cudzyslowy[q][0] < c:
            if cudzyslowy[q][1]:
                otwarte.append(cudzyslowy[q][0])
            elif otwarte:
                otwarte.pop()
            q += 1
        if otwarte and c - otwarte[0] > _MAKS_CYTAT:
            otwarte = []        # cudzysłów bez zamknięcia (literówka, OCR) nie może ukryć reszty aktu
        if not otwarte and c in par:
            przyjete.append(c)
    return przyjete if przyjete or not par else sorted(par)


def _hity_paragrafu(txt, fraza):
    """Pozycje NAGŁÓWKÓW paragrafu wskazanego frazą („§ 4", „§ 2¹", „art. 25 § 2"). W akcie z artykułami „§ N" bez
    artykułu jest niejednoznaczne — wtedy wszystkie trafienia (wywołujący pokazuje artykuł, w którym leżą)."""
    m = _FRAZA_PARAGRAFU.match(fraza.strip())
    if not m:
        return []
    wzor = re.compile(r"[\[<]?" + _NAGLOWEK_ART_W_WIERSZU + r"§\s*" + _wzor_numeru(m, 2) + _KONIEC_ART)
    hity = [p for p in _naglowki_paragrafow(txt) if wzor.match(txt, p)]
    if not hity:    # tylko w cytacie (albo filtr cudzysłowów się pomylił) — lepiej pokazać niż zgubić
        hity = [m.start() for m in _NAGLOWEK_PARAGRAFU.finditer(txt) if wzor.match(txt, m.start())]
    if m.group(1):
        granice = _granice(txt)
        art = [(h, next((b for b in granice if b > h), len(txt))) for h in _hity_naglowka(txt, "art. " + m.group(1))]
        hity = [p for p in hity if any(s <= p < e for s, e in art)]
    return hity


def _granice(txt, paragrafy=False):
    """Pozycje granic jednostek redakcyjnych (`_GRANICE`); `paragrafy` — także nagłówki paragrafów tego aktu."""
    out = {m.start() for m in re.finditer(_GRANICE, txt)}
    if paragrafy:
        out |= set(_naglowki_paragrafow(txt))
    return sorted(out)


def _artykul_nad(txt, pos):
    """Nagłówek artykułu („Art. 25."), w którym leży pozycja `pos` (paragraf w artykule), albo ""."""
    m = re.match(r"[\[<]?(Art\.[ ]*\d+[a-z]*(?:[ ]+\d+[a-z]*)?[ ]*\.)", txt[pos:pos + 40])
    if m:
        return m.group(1)
    ostatni = None
    for m in re.finditer(r"(?m)^[\[<]?(Art\.[ ]*\d+[a-z]*(?:[ ]+\d+[a-z]*)?[ ]*\.)", txt[:pos]):
        ostatni = m
    return ostatni.group(1) if ostatni else ""


def _naglowek_poza_numeracja(txt, fraza):
    """Fraza „art. N" (bez indeksu i litery), nagłówka nie ma, a między „Art. N−1." i najbliższym dalszym artykułem stoi
    nagłówek, którego numer nie pasuje do ciągu → ten nagłówek („Art. 1522."), inaczej "". Dane API bywają błędne:
    13.11.2026 k.p.c. t.j. DU 2024/1568 (HTML i /struct) — art. 15 („Sąd właściwy w chwili wniesienia pozwu…") ma nagłówek
    „Art. 1522.", a PDF tego t.j. — „Art. 15."; `--fragment "art. 15"` pokazywał wtedy tylko odesłania."""
    m = re.match(r"(?i)^art\.?\s*(\d+)$", (fraza or "").strip())
    if not m or int(m.group(1)) < 2:
        return ""
    n = int(m.group(1))
    naglowki = [(h.start(), h.group(1), h.group(2)) for h in re.finditer(r"(?m)^[\[<]?Art\.\s*(\d+)([a-z]*(?:\s+\d+[a-z]*)?)\s*\.", txt)]
    for i, (_, num, reszta) in enumerate(naglowki[:-2]):
        if int(num) == n - 1 and not reszta.strip():
            for _, num2, reszta2 in naglowki[i + 1:i + 40]:
                if int(num2) == n - 1:
                    continue            # art. N−1 z indeksem (14¹, 14a)
                if int(num2) > n and not (int(num2) == n + 1 and not reszta2.strip()) and int(num2) > 10 * n:
                    return f"Art. {num2}{reszta2}."
                break
    return ""


def _fragmenty(txt, fraza, maks=8):
    """Spany (start, end) fragmentów z frazą, docięte do granic jednostek redakcyjnych.

    Fraza w formie "art. 299" trafia w NAGŁÓWEK artykułu (nie w odesłania w treści), a „§ 4" / „art. 25 § 2" —
    w nagłówek paragrafu (fragment kończy się na następnym paragrafie, artykule albo jednostce wyższej);
    inna fraza działa jak wyszukiwanie pełnotekstowe (bez rozróżniania wielkości liter).
    Gdy nagłówka nie ma, fraza jest ponawiana pełnotekstowo — lepiej pokazać odesłanie
    niż odpowiedzieć „nie znaleziono" na przepis, który w akcie jest.
    """
    hits = _hity_naglowka(txt, fraza)
    paragraf = not hits and bool(_FRAZA_PARAGRAFU.match(fraza.strip()))
    if paragraf:
        hits = _hity_paragrafu(txt, fraza)
    # akt bez artykułów (rozporządzenie): paragraf jest podstawową jednostką — też przy trafieniu pełnotekstowym
    bounds = _granice(txt, paragrafy=bool(hits) and paragraf or not _NAGLOWEK_ART.search(txt))
    if not hits:
        # Szukamy po kopii znormalizowanej ZNAK W ZNAK (myślniki → "-"), żeby pozycje zgadzały się
        # z oryginałem — dzięki temu wycinamy dosłowny tekst aktu, a nie jego przerobioną wersję.
        low, f = _norm(txt), _norm(fraza).strip()
        if len(low) != len(txt):        # awaryjnie (np. znaki zmieniające długość przy lower())
            low, f = txt, fraza.strip()
        if not f:
            return []
        hits, p = [], low.find(f)
        while p != -1:
            hits.append(p)
            p = low.find(f, p + len(f))
    spans = []
    for pos in hits:
        if len(spans) >= maks:
            break
        # bez granicy przed trafieniem: od początku wiersza (nie w środku wyrazu — „ię oświetlenie…")
        start = max((b for b in bounds if b <= pos), default=txt.rfind("\n", 0, max(0, pos - 400)) + 1)
        end = min((b for b in bounds if b > pos), default=len(txt))
        if spans and start < spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


# ---------------------------------------------------------------------------------------------
# Przepisy, które jeszcze NIE obowiązują (wejście w życie po dniu dzisiejszym)
# ---------------------------------------------------------------------------------------------
_MIESIACE = {m: i for i, m in enumerate(("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
                                         "sierpnia", "września", "października", "listopada", "grudnia"), 1)}
_DATA_SLOWNIE = r"(\d{1,2})\s+(" + "|".join(_MIESIACE) + r")\s+(\d{4})"
_DATA_CYFRY = r"(\d{1,2})\.(\d{1,2})\.(\d{4})"
# „wejdzie w życie z dn. 5.11.2026 r." (notka tekstu ujednoliconego), „wejdzie w życie z dniem 1 listopada
# 2028 r." (przypis t.j.), „utraci moc z dniem …" — czas PRZYSZŁY = w dniu wydania tekstu jeszcze nie obowiązywało.
# Notki bywają w liczbie mnogiej („wejdą w życie", „utracą moc"), w czasie teraźniejszym („wchodzą w życie"),
# z literówką („wejdziei") i bez „dn." („wejdzie w życie z 1.01.2027 r.", „wejdzie w życie 1.09.2026 r.").
# Tylko czas przyszły/teraźniejszy: „utracił/utraciła moc" (przypis o wyroku TK, k.s.h. art. 459) to przeszłość.
_WEJDZIE = re.compile(r"(?i)(wejd\w* w życie|wchodz\w* w życie|utrac[ią] moc|trac[ią] moc)\s+(?:z\s+(?:dn\.|dniem)?\s*)?(?:"
                      + _DATA_CYFRY + "|" + _DATA_SLOWNIE + ")")
# notka/przypis o PRZYSZŁYM wejściu w życie bez daty: „z dniem określonym w komunikacie, o którym mowa w art. 33
# ustawy…", „po upływie 7 dni od dnia ogłoszenia…" — termin nieznany, ale przepis w dniu wydruku nie obowiązywał
_WEJDZIE_BEZ_DATY = re.compile(r"(?i)\b(?:wejd\w* w życie|utrac[ią] moc)\b")
_BEZ_DATY = {"komunikat": "termin wejścia w życie nieznany — określi komunikat",
             "nieznany": "termin wejścia w życie nie wynika z notki/przypisu — sprawdź w akcie zmieniającym"}


def _dzis():
    """Dzisiejsza data (ISO) — osobna funkcja, żeby testy nie starzały się wraz z kalendarzem."""
    return datetime.date.today().isoformat()


def _iso(d, m, r):
    try:
        return datetime.date(int(r), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def _data_wejscia(tekst):
    """Ostatnia data „wejdzie w życie z dn./dniem …" w tekście notki/przypisu (ISO) albo "".

    Przypis bywa dwuczęściowy: „…ustawy (Dz. U. poz. 507), która wejdzie w życie z dniem 14 października
    2026 r.; wejdzie w życie z dniem 1 listopada 2028 r." — o samym przepisie mówi OSTATNIA data."""
    daty = []
    for m in _WEJDZIE.finditer(tekst):
        if m.group(2):
            daty.append(_iso(m.group(2), m.group(3), m.group(4)))
        else:
            daty.append(_iso(m.group(5), _MIESIACE[m.group(6).lower()], m.group(7)))
    daty = [d for d in daty if d]
    return daty[-1] if daty else ""


def _termin_bez_daty(tekst):
    """Notka/przypis o przyszłym wejściu w życie BEZ daty → „komunikat" (termin określi komunikat), „nieznany"
    (np. „po upływie 7 dni od dnia ogłoszenia") albo "" (jest data albo to nie taka notka)."""
    if not _WEJDZIE_BEZ_DATY.search(tekst) or _data_wejscia(tekst):
        return ""
    return "komunikat" if "komunikac" in tekst.lower() else "nieznany"


def _zmiany_w_tekscie(txt, dzis=None):
    """Linie „[margines: …]" i „[przypis N)] …" o wejściu w życie → ([(data, opis)] przyszłe,
    [(data, opis)] już obowiązujące). Opis = treść notki/przypisu (skrócona). Notka bez daty (termin określi
    komunikat itp.) jest wśród przyszłych, z „datą" „komunikat"/„nieznany" (`_BEZ_DATY`) — na końcu listy."""
    dzis = dzis or _dzis()
    przyszle, minione, bylo = [], [], set()
    for l in txt.split("\n"):
        if not (l.startswith("[margines: ") or l.startswith("[przypis ")):
            continue
        d = _data_wejscia(l) or _termin_bez_daty(l)
        if not d:
            continue
        if l.startswith("[margines: "):
            opis = l[len("[margines: "):-1]
        else:
            # przypis: najważniejsze jest ZDANIE z datą (bywa na końcu długiego przypisu) + początek przypisu
            n = re.match(r"^\[przypis (\d+)\)\]\s*(.*)$", l)
            bez = _PRZYPIS_BEZ_ODSYLACZA_LINIA.match(l)
            ostatnie = (list(_WEJDZIE.finditer(l)) or list(_WEJDZIE_BEZ_DATY.finditer(l)))[-1]
            poczatek = n.group(2) if n else bez.group(2) if bez else l
            kto = (f"przepis z przypisem {n.group(1)})" if n else
                   f"{_NIEPRZYPISANY} {bez.group(1)}) — nie wiadomo, którego przepisu dotyczy; sprawdź w PDF —" if bez else
                   "przepis z przypisem ?)")
            opis = (f"{kto} {ostatnie.group(0)}"
                    + (" r." if d not in _BEZ_DATY else "") + " — "
                    + (poczatek if len(poczatek) <= 140 else poczatek[:137] + "…"))
        opis = opis if len(opis) <= 260 else opis[:257] + "…"
        if (d, opis) in bylo:
            continue
        bylo.add((d, opis))
        (przyszle if d in _BEZ_DATY or d > dzis else minione).append((d, opis))
    return sorted(przyszle), sorted(minione)


def _ostrzezenie_przyszle(txt, ujednolicony, dzis=None, maks=15):
    """Ostrzeżenie dla wypisywanego tekstu (całego lub fragmentu) o brzmieniu, które jeszcze nie obowiązuje
    — i, w tekście ujednoliconym, o zmianach, które od jego wydruku zdążyły już wejść w życie."""
    dzis = dzis or _dzis()
    przyszle, minione = _zmiany_w_tekscie(txt, dzis)
    # przypis bez odsyłacza (`_przypis_bez_odsylacza`) nie mówi, KTÓRY przepis jeszcze nie obowiązuje — osobny blok
    nieprzypisane = [(d, o) for d, o in przyszle + (minione if ujednolicony else []) if o.startswith(_NIEPRZYPISANY)]
    przyszle = [(d, o) for d, o in przyszle if not o.startswith(_NIEPRZYPISANY)]
    minione = [(d, o) for d, o in minione if not o.startswith(_NIEPRZYPISANY)]
    out = []
    if przyszle:
        bez_daty = sum(d in _BEZ_DATY for d, _ in przyszle)
        out.append(f"UWAGA — PRZEPISY, KTÓRE JESZCZE NIE OBOWIĄZUJĄ (dziś {dzis}): {len(przyszle)}"
                   + (f" (w tym {bez_daty} bez daty wejścia w życie)" if bez_daty else ""))
        # przy długiej liście pozycje bez daty nie mogą zniknąć za limitem — idą na początek
        for d, opis in (sorted(przyszle, key=lambda x: x[0] not in _BEZ_DATY) if len(przyszle) > maks else przyszle)[:maks]:
            out.append(f"  - {_BEZ_DATY[d]}: {opis}" if d in _BEZ_DATY else f"  - od {d}: {opis}")
        if len(przyszle) > maks:
            out.append(f"  - … i {len(przyszle) - maks} kolejnych (pełna lista: linie „[margines:”/„[przypis” w tekście)")
        if ujednolicony:
            out.append("  Na dziś obowiązuje brzmienie w nawiasie kwadratowym [ … ] (albo brak przepisu, gdy jest tylko"
                       " < … >); brzmienie w < … > zacznie obowiązywać w podanym dniu. Nie cytuj go jako obowiązującego.")
        else:
            out.append("  Przepis, przy którym stoi taki przypis, jeszcze NIE obowiązuje — nie cytuj go jako obowiązującego.")
    if nieprzypisane:
        out.append(f"UWAGA — PRZYPIS O WEJŚCIU W ŻYCIE BEZ ODSYŁACZA ({_NIEPRZYPISANY}): {len(nieprzypisane)} — odsyłacza "
                   "w treści nie ustalono, więc NIE wiadomo, którego przepisu dotyczy; nie przypisuj go przepisowi, przy "
                   "którym stoi. Sprawdź w PDF (--pdf plik.pdf):")
        for d, opis in nieprzypisane[:maks]:
            out.append(f"  - {_BEZ_DATY[d]}: {opis}" if d in _BEZ_DATY else
                       f"  - od {d}{' (już obowiązuje)' if d <= dzis else ''}: {opis}")
    if ujednolicony and minione:
        out.append(f"UWAGA: od wydruku tekstu ujednoliconego weszły już w życie zmiany ({len(minione)}) — dla nich obowiązuje"
                   " brzmienie w < … >, a brzmienie w [ … ] jest NIEAKTUALNE:")
        for d, opis in minione[:maks]:
            out.append(f"  - od {d}: {opis}")
    return out


_POZIOMY = ((r"KSIĘGA\s|Księga\s", 0), (r"TYTUŁ\s|Tytuł\s", 1), (r"DZIAŁ\s|Dział\s", 2), (r"ROZDZIAŁ\s|Rozdział\s", 3),
            (r"ODDZIAŁ\s|Oddział\s", 4), (r"Art\.\s*\d", 5), (r"§\s*\d|\d+[a-z]?\.\s", 6), (r"\d+[a-z]?\)\s", 7),
            (r"[a-z]\)\s", 8), (r"–\s", 9))


def _poziom(t):
    """Poziom jednostki redakcyjnej na początku wiersza (0 = księga … 9 = tiret) albo None."""
    for wzor, poziom in _POZIOMY:
        if re.match(wzor, t):
            return poziom
    return None


def _otwarte_bloki(txt, pos):
    """Tekst ujednolicony: bloki „< …" / „[ …" otwarte PRZED pozycją `pos` i niezamknięte do niej (np. cały
    „<DZIAŁ IVFA … >" z dziesięcioma artykułami) → [(wiersz otwierający, [linie „[margines: …]" przy nim])].

    Blok zamyka „>"/„]" na końcu akapitu ALBO następna jednostka tego samego lub wyższego poziomu
    (Kancelaria Sejmu zdarza się zgubić nawias zamykający, np. k.p.c. art. 477⁹ § 2¹)."""
    linie = txt[:pos].split("\n")
    stos = []   # (znak otwarcia, indeks wiersza, poziom)
    for i, l in enumerate(linie):
        t = l.strip()
        if not t or t.startswith(("[przypis ", "[margines: ", "[obwieszczenie", "» ")):
            continue
        otw = t[0] if t[0] in "<[" else ""
        poziom = _poziom(t[1:] if otw else t)
        if poziom is not None:
            stos = [b for b in stos if b[2] is None or b[2] < poziom]
        zam = {"<": ">", "[": "]"}
        if otw and not t.endswith(zam[otw]):
            stos.append((otw, i, poziom))
        elif not otw and stos and t.endswith(zam[stos[-1][0]]):
            stos.pop()
    # wiersz, od którego zaczyna się fragment, też zamyka bloki tego samego lub niższego poziomu
    biezacy = txt[pos:].split("\n", 1)[0].strip()
    poziom = _poziom(biezacy[1:] if biezacy[:1] in "<[" and biezacy else biezacy)
    if poziom is not None:
        stos = [b for b in stos if b[2] is None or b[2] < poziom]
    return [(linie[i], [l for l in linie[i + 1:i + 4] if l.startswith("[margines: ")]) for _, i, _ in stos]


def _naglowki_nadrzedne(txt, pos):
    """Nagłówki jednostek nadrzędnych (księga … oddział), w których leży pozycja `pos`, z przypisami/notkami
    stojącymi tuż pod nimi → [(nagłówek, [linie „[przypis …"/„[margines: …"])], tylko te, które je mają.
    Przypis „Rozdział dodany przez … ustawy, która wejdzie w życie z dniem 18 lutego 2027 r." przy nagłówku
    „Rozdział 11b" (k.wyb.) dotyczy każdego artykułu tego rozdziału, choć stoi poza wycinkiem art. 103d."""
    linie = txt[:pos].split("\n")
    otwarte = {}    # poziom → indeks wiersza nagłówka
    for i, l in enumerate(linie):
        t = l.strip()
        if not t or t.startswith(("[przypis ", "[margines: ", "[obwieszczenie", "» ")):
            continue
        poziom = _poziom(t[1:] if t[0] in "<[" else t)
        if poziom is not None and poziom <= 4:
            otwarte = {k: v for k, v in otwarte.items() if k < poziom}
            otwarte[poziom] = i
    out = []
    for poziom in sorted(otwarte):
        i = otwarte[poziom]
        przy = []
        for l in linie[i + 1:i + 6]:
            t = l.strip().lstrip("<[")
            if _PRZYPIS_BEZ_ODSYLACZA_LINIA.match(l):
                continue        # przypis bez odsyłacza nie należy do nagłówka, pod którym stoi
            if l.startswith(("[przypis ", "[margines: ")):
                przy.append(l)
            elif _poziom(t) is not None:
                break
        if przy:
            out.append((linie[i].strip(), przy))
    return out


def _podstawa_ujednolicenia(notka):
    """„Opracowano na podstawie: t.j. Dz. U. z 2025 r. poz. 383, 1818, 1872 oraz z 2026 r. poz. 902, 988."
    → {(2025, 383), (2025, 1818), (2025, 1872), (2026, 902), (2026, 988)} (też „Nr 78, poz. 483").
    Kancelaria Sejmu zdarza się postawić kropkę zamiast przecinka („poz. 13, 426. 737, 912.") albo zgubić „z"
    przed rokiem („poz. 161, 1971 r. Nr 27") — lista ciągnie się dalej, kropka kończy ją dopiero na końcu notki."""
    out, rok = set(), None
    for m in re.finditer(r"(?:\bz\s+)?\b(\d{4})\s*r\.|poz\.\s*((?:\d+(?!\d|\s*r\.)(?:\s*(?:,|\.|i|oraz)\s*(?=\d))?)+)",
                         notka or ""):
        if m.group(1):
            rok = int(m.group(1))
        elif rok:
            out |= {(rok, int(n)) for n in re.findall(r"\d+", m.group(2))}
    return out


_POZ_LISTY = re.compile(r"^  - (?:Dz\.U\.|M\.P\.) (\d{4}) (?:nr \d+ )?poz\. (\d+)\b")


def _oznacz_uwzglednione(linie, podstawa):
    """Lista nowelizacji po t.j. a tekst ujednolicony: pozycje, które Kancelaria Sejmu już wpisała do
    tekstu, są oznaczone — nakładanie ich ręcznie po raz drugi zdublowałoby zmianę."""
    if not podstawa:
        return linie
    out = []
    for l in linie:
        m = _POZ_LISTY.match(l)
        if m:
            l += ("  [UWZGLĘDNIONA w tym tekście ujednoliconym — NIE nakładaj ponownie]"
                  if (int(m.group(1)), int(m.group(2))) in podstawa else
                  "  [poza listą „Opracowano na podstawie” — sprawdź przypis/margines przy przepisie; jeśli tej zmiany "
                  "tam nie ma, nałóż ją ręcznie]")
        elif l.startswith("UWAGA: po tym tekście jednolitym"):
            l += " (oznaczenia przy pozycjach: patrz notka „Opracowano na podstawie”)"
        out.append(l)
    return out


def _pozycje_dz_u(tekst):
    """Pozycje Dz.U. cytowane w nawiasach („(Dz. U. poz. 807)", „(Dz. U. z 2026 r. poz. 25)", „(Dz. U. poz. 2140
    i 2243)") → {(rok, poz)}. Cytat bez roku dotyczy roku aktu („ustawą z dnia 4 czerwca 2025 r. … (Dz. U. poz.
    807)" = 2025/807)."""
    out = set()
    for m in re.finditer(r"\((Dz\.\s*U\.[^()]*)\)", tekst):
        wn = " ".join(m.group(1).split())
        rok_aktu = re.findall(r"z dnia \d{1,2}\s+\w+\s+(\d{4})\s*r\.", tekst[:m.start()])
        if not re.match(r"Dz\.\s*U\.\s*z\s+\d{4}", wn) and rok_aktu:
            wn = re.sub(r"^Dz\.\s*U\.\s*", f"Dz. U. z {rok_aktu[-1]} r. ", wn)
        out |= _podstawa_ujednolicenia(wn)
    return out


def _obwieszczenie_tj(txt):
    """Obwieszczenie Marszałka Sejmu na początku tekstu jednolitego (PDF typu T albo text.html t.j.) →
    (uwzględnione {(rok, poz)}, nieobjęte {(rok, poz)}, ogłoszone przed: data ISO albo "").

    Pkt 1: „…jednolity tekst ustawy … z uwzględnieniem zmian wprowadzonych: 1) ustawą … (Dz. U. poz. 1046)" — te
    zmiany SĄ w tekście jednolitym (także te, które wejdą w życie później — t.j. podaje wtedy oba brzmienia
    z przypisami), a „oraz zmian wynikających z przepisów ogłoszonych przed dniem 6 grudnia 2023 r." obejmuje
    wszystkie zmiany ogłoszone przed tym dniem. Pkt 2 („nie obejmuje: …"): pozycje „zmian wprowadzonych …" NIE są
    w tekście (pozycje „art. N ustawy …" to przepisy przejściowe i końcowe, nie zmiany)."""
    glowa = re.sub(r"(?m)^» ", "", txt[:60000])
    m = re.search(r"(?s)ogłasza się w załączniku.{0,600}?z uwzględnieniem(.{0,8000}?)"
                  r"(?:\n\s*2\.\s|\n\s*Załącznik|\n\s*Marszałek Sejmu|\Z)", glowa)
    if not m:
        return set(), set(), ""
    pkt1 = m.group(1)
    uwzgl = _pozycje_dz_u(pkt1[pkt1.find("zmian"):]) if "zmian" in pkt1 else set()
    przed = re.search(r"ogłoszonych przed dniem " + _DATA_SLOWNIE, pkt1)
    przed = _iso(przed.group(1), _MIESIACE[przed.group(2)], przed.group(3)) if przed else ""
    nieobj = set()
    m2 = re.search(r"(?s)\n\s*2\.\s+Podany.{0,200}?nie obejmuje(.*?)(?:\n\s*3\.\s|\nZałącznik|\Z)", glowa[m.end(1):])
    if m2:
        for poz in re.split(r"\n\s*\d+[a-z]?\)\s", "\n" + m2.group(1)):
            if re.match(r"\s*:?\s*zmian", poz):
                nieobj |= _pozycje_dz_u(poz)
    return uwzgl - nieobj, nieobj, przed


def _oznacz_obwieszczenie(linie, uwzgl, nieobj, przed="", dzis=None):
    """Lista nowelizacji po t.j. a obwieszczenie (`_obwieszczenie_tj`): pozycja wymieniona w pkt 1 (albo ogłoszona
    przed dniem z formuły „przepisów ogłoszonych przed dniem …") JEST w tekście jednolitym, pozycja z pkt 2
    („nie obejmuje: zmian wprowadzonych …") — nie. Bez tego zmianę ujętą w t.j. opisywano jak zmianę „po tym t.j."."""
    dzis = dzis or _dzis()
    if not (uwzgl or nieobj or przed):
        return linie
    out, oznaczono = [], False
    for l in linie:
        m = _POZ_LISTY.match(l)
        if m:
            klucz = (int(m.group(1)), int(m.group(2)))
            ogl = re.search(r"ogłoszono (\d{4}-\d{2}-\d{2})", l)
            if klucz in nieobj:
                l += "  [NIE objęta tym t.j. — obwieszczenie (pkt 2) ją wyłącza; nałóż ją ręcznie]"
                oznaczono = True
            elif klucz in uwzgl or przed and ogl and ogl.group(1) < przed:
                wej = re.search(r"wejście w życie[^,)]*?(\d{4}-\d{2}-\d{2})", l)
                l += ("  [UWZGLĘDNIONA w tym t.j. — obejmuje ją obwieszczenie (pkt 1); NIE nakładaj ponownie"
                      + (f"; wchodzi w życie {wej.group(1)} — do tego dnia obowiązuje dotychczasowe brzmienie, "
                         "nowe stoi w t.j. z przypisem o wejściu w życie" if wej and wej.group(1) > dzis else "") + "]")
                oznaczono = True
        out.append(l)
    if oznaczono:
        out = [l + " (oznaczenia przy pozycjach: wg obwieszczenia Marszałka Sejmu na początku tekstu jednolitego)"
               if l.startswith("UWAGA: po tym tekście jednolitym") else l for l in out]
    return out


# odesłanie przypisu do innego przypisu: „…ustawy, o której mowa w odnośniku 6.", „…w odnośnikach 5 i 6"
_ODNOSNIK = re.compile(r"(?i)\bodnośnik\w*\s+(\d+[a-z]?(?:\s*(?:,|i|oraz)\s*\d+[a-z]?)*)")
_OBOWIAZUJE_DO = re.compile(r"(?i)obowiązuj\w*\s+do\s+(?:dnia\s+)?wejścia\s+w\s+życie")
_PRZYPIS_LINIA = re.compile(r"(?m)^\[przypis (\d+[a-z]?)\)\] (.*)$")
_BRAK_TRESCI = "(treści przypisu nie odnaleziono"


def _przypisy_aktu(txt):
    """Linie „[przypis N)] treść" całego tekstu → {N: treść} (pierwsza znaleziona treść każdego numeru). Przypis bez
    odsyłacza (`_przypis_bez_odsylacza`) też — tylko gdy numeru nie ma wśród dopasowanych: odesłanie „…w odnośniku 15"
    musi znaleźć jego treść, choć nie wiadomo, przy którym przepisie stoi."""
    out = {}
    for m in _PRZYPIS_LINIA.finditer(txt):
        if not m.group(2).startswith(_BRAK_TRESCI):
            out.setdefault(m.group(1), m.group(2))
    for m in _PRZYPIS_BEZ_ODSYLACZA_LINIA.finditer(txt):
        out.setdefault(m.group(1), m.group(2))
    return out


def _odeslania(tresc):
    return [n for m in _ODNOSNIK.finditer(tresc) for n in re.findall(r"\d+[a-z]?", m.group(1))]


def _termin_przez_odnosniki(nr, przypisy, bylo=None):
    """Termin wejścia w życie (ISO, „komunikat"/„nieznany" albo "") z przypisu `nr` albo z przypisów, do których
    odsyła („…ustawy, o której mowa w odnośniku 6") — najwyżej 4 kroki. Zwraca (termin, [łańcuch numerów])."""
    bylo = (bylo or set()) | {nr}
    t = przypisy.get(nr, "")
    d = _data_wejscia(t) or _termin_bez_daty(t)
    if d:
        return d, [nr]
    if len(bylo) <= 4:
        for n in _odeslania(t):
            if n not in bylo:
                d, lancuch = _termin_przez_odnosniki(n, przypisy, bylo)
                if d:
                    return d, [nr] + lancuch
    return "", []


def _przypisy_fragmentu(fragment, txt, dzis=None):
    """Przypisy fragmentu odsyłające do przypisów SPOZA niego (treść stoi przy innym przepisie, na innej stronie).

    Zwraca (dociągnięte linie do wypisania, linie do analizy wejścia w życie, ostrzeżenia). Przypis „W brzmieniu
    ustalonym przez … ustawy, o której mowa w odnośniku 6." dostaje do analizy treść przypisu 6 („…która wejdzie
    w życie z dniem 5 listopada 2026 r.") — bez tego dwa brzmienia art. 94³ k.p. wychodziły bez wskazania, które
    obowiązuje dziś. Przypis „W tym brzmieniu obowiązuje do wejścia w życie zmiany, o której mowa w odnośniku 29."
    daje ostrzeżenie „obowiązuje tylko DO …" (to brzmienie DZIŚ obowiązuje, nie jest przyszłe)."""
    dzis = dzis or _dzis()
    przypisy = _przypisy_aktu(txt)
    w_fragmencie = {}
    for m in _PRZYPIS_LINIA.finditer(fragment):
        w_fragmencie.setdefault(m.group(1), m.group(2) if not m.group(2).startswith(_BRAK_TRESCI)
                                else przypisy.get(m.group(1), m.group(2)))
    dociagniete, analiza, ostrz, kolejka, bylo = [], [], [], list(w_fragmencie), set(w_fragmencie)
    while kolejka:
        nr = kolejka.pop(0)
        for n in _odeslania(w_fragmencie.get(nr) or przypisy.get(nr, "")):
            if n not in bylo and n in przypisy:
                bylo.add(n)
                kolejka.append(n)
                dociagniete.append(f"[przypis {n})] {przypisy[n]}")
    for nr, tresc in w_fragmencie.items():
        if not _odeslania(tresc) or _data_wejscia(tresc) or _termin_bez_daty(tresc):
            continue
        d, lancuch = _termin_przez_odnosniki(nr, dict(przypisy, **{nr: tresc}))
        if not d or len(lancuch) < 2:
            continue
        zrodlo = "przypis " + " → ".join(n + ")" for n in lancuch[1:])
        if _OBOWIAZUJE_DO.search(tresc):
            kiedy = _BEZ_DATY.get(d, d)
            ostrz.append(f"UWAGA: brzmienie z przypisem {nr}) obowiązuje tylko DO wejścia w życie zmiany ({kiedy}; "
                         f"{zrodlo})" + (" — dziś jeszcze obowiązuje; nowe brzmienie (z przypisem odsyłającym do tej "
                                          "zmiany) zacznie obowiązywać w tym dniu." if d in _BEZ_DATY or d > dzis else
                                          " — ta zmiana już weszła w życie, to brzmienie NIE obowiązuje."))
        else:
            analiza.append(f"[przypis {nr})] {tresc} [{zrodlo}: {przypisy[lancuch[-1]]}]")
    return dociagniete, analiza, ostrz


def _cytat(d):
    """Urzędowa forma cytatu publikatora: „Dz. U. z 2001 r. Nr 112, poz. 1198" / „Dz. U. z 2024 r. poz. 18"."""
    m = re.match(r"^(Dz\.U\.|M\.P\.)\s+(\d{4})\s+(?:nr\s+(\d+)\s+)?poz\.\s+(\d+)$", (d.get("displayAddress") or "").strip())
    if not m:
        return ""
    pub = "Dz. U." if m.group(1) == "Dz.U." else "M.P."
    return f"{pub} z {m.group(2)} r. " + (f"Nr {m.group(3)}, " if m.group(3) else "") + f"poz. {m.group(4)}"


# typy pozycji, które same nie „wchodzą w życie" (puste entryIntoForce jest dla nich normalne)
_TYPY_BEZ_WEJSCIA = ("Obwieszczenie", "Komunikat", "Oświadczenie rządowe", "Orzeczenie", "Postanowienie", "Ogłoszenie",
                     "Informacja", "Sprostowanie", "Dokument wypowiedzenia", "Apel", "Deklaracja", "Wyrok", "Uchwała")


def _jeszcze_nie_obowiazuje(d, dzis=None):
    """Ostrzeżenia dla aktu w vacatio legis / z jednostkami wchodzącymi w życie później (lista linii)."""
    dzis = dzis or _dzis()
    out = []
    eif = d.get("entryIntoForce") or ""
    if eif and eif > dzis:
        out.append(f"UWAGA: akt jeszcze NIE WSZEDŁ W ŻYCIE — wejście w życie {eif} (dziś {dzis}). Status API "
                   f"„{d.get('status', '')}” znaczy tylko, że akt nie został uchylony; do tego dnia jego przepisy nie obowiązują.")
    komentarz = " ".join(str(d.get("comments") or "").split())
    if not eif and d.get("type") not in _TYPY_BEZ_WEJSCIA:
        # entryIntoForce puste (próba 2026-11-13, DU 2000–2026): przy rozporządzeniach i ustawach z terminem
        # „określonym w komunikacie / decyzji Komisji Europejskiej" (DU 2026/1218, 2025/267, 2024/1688) API nie podaje
        # ani daty, ani tego, czy zdarzenie nastąpiło; przy DU 2025/845, 2024/635, 2017/1972 data stoi tylko w
        # przepisie o wejściu w życie. Status „obowiązujący" niczego tu nie potwierdza.
        if re.search(r"(?i)wchodzi\s+w\s+życie\s+z\s+dniem\s+(?:określonym|wystąpienia|wdrożenia|ogłoszenia\s+komunikatu)", komentarz):
            out.append("UWAGA: API nie podaje daty wejścia w życie, a termin zależy od zdarzenia (komunikat, decyzja, "
                       "wdrożenie — patrz „Uwagi”). API nie mówi, czy zdarzenie nastąpiło; status „"
                       f"{d.get('status', '')}” tego nie potwierdza. Sprawdź komunikat / decyzję, zanim uznasz akt za obowiązujący.")
        else:
            out.append("UWAGA: API nie podaje daty wejścia w życie (entryIntoForce puste). Ustal ją z przepisu "
                       "o wejściu w życie: tekst <sygnatura> --fragment \"wchodzi w życie\".")
    pozniej = sorted({x for x in (_iso(a, _MIESIACE[b], c) for a, b, c in re.findall(_DATA_SLOWNIE, komentarz)) if x > dzis})
    if pozniej:
        out.append("UWAGA: część przepisów wchodzi w życie PÓŹNIEJ (" + ", ".join(pozniej) + ") — patrz „Uwagi”; "
                   "do tych dni te przepisy nie obowiązują.")
    return out


def _eli_rok_poz(act):
    try:
        _, rok, poz = (act.get("ELI") or "").split("/")
        return (int(rok), int(poz))
    except Exception:
        return (0, 0)


def _tj_acts(refs):
    """Akty z kategorii 'Inf. o tekście jednolitym' (tylko na akcie bazowym), najnowszy pierwszy."""
    key = next((k for k in refs if k.lower().startswith("inf. o tekście jednolit")), None)
    if not key:
        return []
    items = refs[key] if isinstance(refs[key], list) else [refs[key]]
    acts = [r.get("act") for r in items if isinstance(r, dict) and isinstance(r.get("act"), dict)]
    return sorted(acts, key=_eli_rok_poz, reverse=True)


def _akt_bazowy(refs):
    """Akt BAZOWY z kategorii 'Tekst jednolity dla aktu' (tylko gdy akt sam jest t.j.), inaczej None."""
    key = next((k for k in refs if k.lower().startswith("tekst jednolity dla aktu")), None)
    if not key:
        return None
    items = refs[key] if isinstance(refs[key], list) else [refs[key]]
    return next((r.get("act") for r in items if isinstance(r, dict) and isinstance(r.get("act"), dict)), None)


def _nowszy_tj(path, refs):
    """Tekst jednolity NOWSZY niż akt pod `path`, jeśli istnieje — inaczej None.

    Akt bazowy ma listę swoich t.j. we własnych odniesieniach ('Inf. o tekście jednolitym').
    Akt, który SAM jest t.j., ma w odniesieniach tylko wskazanie aktu bazowego — listę t.j.
    trzeba pobrać z odniesień aktu bazowego (dodatkowe zapytanie; awaria → VerificationUnknown,
    o reakcji decyduje wywołujący). Bez tego `tekst` na przestarzałym t.j. wyglądał jak aktualny.
    """
    m = re.match(r"^/acts/(?:DU|MP)/(\d+)/(\d+)$", path)
    if not m:
        return None
    wlasne = (int(m.group(1)), int(m.group(2)))
    tj = _tj_acts(refs)
    if not tj:
        base = _akt_bazowy(refs)
        if not (base and base.get("ELI")):
            return None
        base_refs = _get(f"/acts/{base['ELI']}/references", soft=True)
        tj = _tj_acts(base_refs) if isinstance(base_refs, dict) else []
    if tj and _eli_rok_poz(tj[0]) > wlasne:
        return tj[0]
    return None


def _akt_opis(act):
    return act.get("displayAddress") or act.get("ELI", "") or ""


def _daty_aktu(act):
    """Daty z obiektu aktu w odniesieniach: (data aktu = „z dnia" w tytule, ogłoszono w Dz.U./M.P.)."""
    return act.get("announcementDate") or "", act.get("promulgation") or ""


_ETYKIETY_DATY = (
    # kategoria odniesień (początek nazwy, małe litery) → co oznacza pole `date` (zweryfikowane 2026-08)
    ("akty zmieniające", "wejście w życie zmiany"),
    ("akty zmienione", "wejście w życie zmiany"),
    ("nowelizacje po tekście jednolit", "data aktu"),
)


def _etykieta_daty(kind):
    k = (kind or "").lower()
    return next((et for pref, et in _ETYKIETY_DATY if k.startswith(pref)), "data wg API")


def _fmt_ref(ref, kind=None):
    """Linia odniesienia z JEDNOZNACZNYMI etykietami dat: `date` z API znaczy co innego w każdej
    kategorii („Akty zmieniające" = wejście w życie zmiany, „Nowelizacje po t.j." = data aktu)."""
    if not isinstance(ref, dict):
        return f"  - {ref}"
    act = ref.get("act") if isinstance(ref.get("act"), dict) else None
    extra = []
    if act:
        line = f"  - {_akt_opis(act)}  {act.get('title', '')}".rstrip()
        data_aktu, ogloszono = _daty_aktu(act)
        if data_aktu:
            extra.append(f"data aktu {data_aktu}")
        if ogloszono:
            extra.append(f"ogłoszono {ogloszono}")
    else:
        line = f"  - {ref.get('displayAddress') or ref.get('ELI', '') or ref}"
    if ref.get("date"):
        et = _etykieta_daty(kind)
        if not (et == "data aktu" and f"data aktu {ref['date']}" in extra):
            extra.append(f"{et} {ref['date']}")
    if ref.get("art"):
        extra.append(f"art. {ref['art']}")
    if extra:
        line += "  (" + ", ".join(extra) + ")"
    if act and _czy_tk(act) and (kind or "").lower().startswith("akty zmieniające"):
        line += _ZNACZNIK_TK
    return line


def _lista(refs, prefix):
    key = next((k for k in refs if k.lower().startswith(prefix)), None)
    if not key:
        return []
    items = refs[key] if isinstance(refs[key], list) else [refs[key]]
    return [r for r in items if isinstance(r, dict) and isinstance(r.get("act"), dict)]


def _zmiany_bazowe_po(base_eli, stan_prawny):
    """„Akty zmieniające" aktu bazowego OGŁOSZONE lub WCHODZĄCE W ŻYCIE po dacie stanu prawnego t.j.

    Tylko te nie są (w całości) oddane w tekście jednolitym. Może rzucić VerificationUnknown
    (soft GET odniesień aktu bazowego).
    """
    base_refs = _get(f"/acts/{base_eli}/references", soft=True)
    if not isinstance(base_refs, dict):
        return []
    out = []
    for r in _lista(base_refs, "akty zmieniające"):
        _, ogloszono = _daty_aktu(r["act"])
        wejscie = r.get("date") or ""
        # ogłoszenie „>=", nie „>": obwieszczenie bywa sformułowane „zmian … ogłoszonych PRZED dniem X" (DU 2024/18,
        # 2024/499, 2022/1575 — API podaje wtedy legalStatusDate = X), więc akt ogłoszony w dniu X NIE jest w t.j.
        # Zmiana wchodząca w życie w dniu X jest w stanie prawnym „na dzień X" — tu „>" wystarcza; gdy obwieszczenie
        # podaje wcześniejszy dzień niż API (k.p.c. DU 2026/468: API 2026-03-25, obwieszczenie „24 marca 2026 r.",
        # sprawdzone 2026-11-13), listę uzupełnia `_sprawdz_stan_obwieszczenia`. Pozycję ujętą w t.j. oznacza obwieszczenie.
        if (ogloszono and ogloszono >= stan_prawny) or (wejscie and wejscie > stan_prawny):
            out.append(r)
    return out


def _stan_zastepczy(refs, path):
    """Data odcięcia listy zmian, gdy t.j. NIE MA legalStatusDate (DU 2023/2631, DU 2026/1055, starsze t.j. —
    sprawdzone 2026-11-13): data obwieszczenia POPRZEDNIEGO t.j. tego aktu, a bez niego — data aktu bazowego.
    Wcześniejsza data daje dłuższą listę; pozycje ujęte w t.j. oznacza potem obwieszczenie. Zwraca (data, opis)
    albo ("", ""). Może rzucić VerificationUnknown."""
    base = _akt_bazowy(refs)
    if not (base and base.get("ELI")):
        return "", ""
    base_refs = _get(f"/acts/{base['ELI']}/references", soft=True)
    m = re.match(r"^/acts/(?:DU|MP)/(\d+)/(\d+)$", path or "")
    wlasne = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    starsze = [a for a in (_tj_acts(base_refs) if isinstance(base_refs, dict) else []) if _eli_rok_poz(a) < wlasne]
    if starsze:
        d = starsze[0].get("announcementDate") or starsze[0].get("promulgation") or ""
        if d:
            return d, f"data poprzedniego t.j. {_akt_opis(starsze[0])}"
    d = base.get("announcementDate") or base.get("promulgation") or ""
    return (d, f"data aktu bazowego {_akt_opis(base)}") if d else ("", "")


_MAKS_DOPYTAN = 10


def _nowelizacje_po_tj(refs, path, strict=False, wynik=None):
    """Nowelizacje po tekście jednolitym: własna kategoria t.j. UZUPEŁNIONA o „Akty zmieniające"
    aktu bazowego po `legalStatusDate` t.j. (Sejm nie synchronizuje obu list — audyt 2026-08:
    k.p.c. t.j. 2026/468 wykazywał 2 z 5, k.s.h. 2024/18 — 1 z 4). Zwraca (linie, uwagi).

    Każda pozycja ma daty opisane jednoznacznie: data aktu / ogłoszono / wejście w życie zmiany.
    Awaria dopytań: w strict → VerificationUnknown, inaczej uwaga o możliwej niekompletności.
    `wynik` (dict) dostaje „pozycje" (lista słowników jak w środku funkcji), „stan" (data odcięcia) i „baza".
    """
    wlasne = _lista(refs, "nowelizacje po tekście jednolit")
    base = _akt_bazowy(refs)
    if not (wlasne or base):
        return [], []
    uwagi = []
    stan_prawny, opis_stanu = "", ""
    zrodla = ["odniesienia t.j."] if wlasne else []
    pozycje = {}   # ELI → {act, data_aktu, ogloszono, wejscie, etykieta}
    for r in wlasne:
        act = r["act"]
        data_aktu, ogloszono = _daty_aktu(act)
        pozycje[act.get("ELI")] = {"act": act, "data_aktu": data_aktu or r.get("date") or "",
                                   "ogloszono": ogloszono, "wejscie": "", "etykieta": ""}
    if base and base.get("ELI"):
        try:
            meta = _get(path, soft=True) if path else None
            stan_prawny = (meta or {}).get("legalStatusDate") or "" if isinstance(meta, dict) else ""
            if not stan_prawny:
                # bez legalStatusDate lista nie mogła być uzupełniona — a to dokładnie przypadek, w którym
                # wyrok TK SK 90/22 (2024) po t.j. DU 2023/2631 nie był wymieniony wcale (sprawdzone 2026-11-13)
                stan_prawny, opis_stanu = _stan_zastepczy(refs, path)
                if stan_prawny:
                    uwagi.append(f"UWAGA: brak legalStatusDate w metadanych {path} — listę zmian liczę od {stan_prawny} "
                                 f"({opis_stanu}); zmiany ujęte w tym t.j. wskazuje jego obwieszczenie.")
            if stan_prawny:
                for r in _zmiany_bazowe_po(base["ELI"], stan_prawny):
                    act = r["act"]
                    data_aktu, ogloszono = _daty_aktu(act)
                    poz = pozycje.setdefault(act.get("ELI"), {"act": act, "data_aktu": data_aktu,
                                                              "ogloszono": ogloszono, "wejscie": "", "etykieta": ""})
                    poz["wejscie"], poz["etykieta"] = r.get("date") or "", "wejście w życie zmiany"
                zrodla.append(f"„Akty zmieniające\" aktu bazowego {_akt_opis(base)} ogłoszone lub "
                              f"wchodzące w życie od {stan_prawny}")
            else:
                uwagi.append(f"UWAGA: brak legalStatusDate w metadanych {path} — listy nowelizacji nie "
                             "uzupełniono z aktu bazowego (może być niepełna).")
        except VerificationUnknown as e:
            if strict:
                raise VerificationUnknown(f"nie udało się uzupełnić listy nowelizacji z aktu bazowego ({e})") from e
            uwagi.append(f"UWAGA: nie udało się uzupełnić listy nowelizacji z aktu bazowego ({e}) — "
                         "lista poniżej może być NIEPEŁNA.")
    # wejście w życie dla pozycji tylko z listy t.j. (bez `date` = wejście w życie): dopytaj metadane
    dopytania = 0
    for eli_id, poz in pozycje.items():
        if poz["wejscie"] or not eli_id or dopytania >= _MAKS_DOPYTAN:
            continue
        dopytania += 1
        try:
            m = _get(f"/acts/{eli_id}", soft=True)
        except VerificationUnknown as e:
            if strict:
                raise
            uwagi.append(f"UWAGA: nie udało się pobrać wejścia w życie {eli_id} ({e}).")
            continue
        if isinstance(m, dict):
            poz["wejscie"], poz["etykieta"] = m.get("entryIntoForce") or "", "wejście w życie aktu"
            if m.get("comments"):
                poz["uwagi"] = m["comments"]
    linie = []
    for poz in sorted(pozycje.values(), key=lambda x: (x["ogloszono"] or x["data_aktu"] or ""), reverse=True):
        act = poz["act"]
        daty = []
        if poz["data_aktu"]:
            daty.append(f"data aktu {poz['data_aktu']}")
        if poz["ogloszono"]:
            daty.append(f"ogłoszono {poz['ogloszono']}")
        daty.append(f"{poz['etykieta'] or 'wejście w życie'} {poz['wejscie'] or '— sprawdź: meta ' + (act.get('ELI') or '').replace('/', ' ')}")
        linie.append(f"  - {_akt_opis(act)}  {act.get('title', '')}".rstrip() + "  (" + ", ".join(daty) + ")"
                     + (_ZNACZNIK_TK if _czy_tk(act) else ""))
        if poz.get("uwagi"):
            linie.append(f"      uwagi: {poz['uwagi']}")
    if linie:
        naglowek = (f"UWAGA: po tym tekście jednolitym" + ((f" (API nie podaje stanu prawnego — lista od {stan_prawny})"
                                                            if opis_stanu else f" (stan prawny na {stan_prawny})")
                                                           if stan_prawny else "")
                    + f" odnotowano zmiany ({len(linie) - sum(1 for l in linie if l.startswith('      uwagi'))})"
                    " — sprawdź ich wejście w życie" + (f" [źródła: {'; '.join(zrodla)}]" if zrodla else "") + ":")
        linie.insert(0, naglowek)
    if wynik is not None:
        wynik.update(pozycje=list(pozycje.values()), stan=stan_prawny, baza=base)
    return linie, uwagi


_ZNACZNIK_TK = "  [WYROK TK — skutek dla przepisów: patrz sentencja]"


def _czy_tk(act):
    """Wyrok TK albo obwieszczenie Prezesa TK o utracie mocy (lata 90.) — pozycja „Akty zmieniające" bez nowego brzmienia."""
    t = (act or {}).get("title") or ""
    return t.startswith("Wyrok Trybunału Konstytucyjnego") or "Prezesa Trybunału Konstytucyjnego" in t \
        or (act or {}).get("type") == "Orzeczenie"


def _zmiany_aktu_bez_tj(refs, path, wynik=None):
    """Akt BEZ tekstu jednolitego: text.html i PDF to brzmienie OGŁOSZONE, a każda pozycja jego „Akty zmieniające"
    jest zmianą, której w tym tekście nie ma. Dotąd `tekst` takiego aktu nie ostrzegał wcale — np. DU 2006/550
    (zmieniana 4 razy, ostatnio DU 2026/1340 od 2026-10-30) wychodził z pierwotnym art. 13 ust. 3 i kodem 0
    także w --strict (sprawdzone 2026-11-13). Zwraca linie ostrzeżenia; `wynik` dostaje pozycje i stan."""
    if _tj_acts(refs) or _akt_bazowy(refs):
        return []
    zmiany = _lista(refs, "akty zmieniające")
    if not zmiany:
        return []
    pozycje = []
    for r in zmiany:
        act = r["act"]
        data_aktu, ogloszono = _daty_aktu(act)
        pozycje.append({"act": act, "data_aktu": data_aktu, "ogloszono": ogloszono, "wejscie": r.get("date") or "",
                        "etykieta": "wejście w życie zmiany"})
    pozycje.sort(key=lambda x: (x["wejscie"] or x["ogloszono"] or ""), reverse=True)
    linie = [f"UWAGA: ten akt NIE MA tekstu jednolitego, a jego text.html i PDF ogłoszony to brzmienie PIERWOTNE. Akt "
             f"zmieniano ({len(pozycje)}) — tych zmian nie ma w tekście ogłoszonym (wyjątek: tekst ujednolicony, patrz "
             "oznaczenia przy pozycjach); przed cytatem nanieś je albo sprawdź artykuł przez --fragment (narzędzie "
             "wskaże, czy dana zmiana go dotyczy):"]
    for p in pozycje:
        daty = [f"data aktu {p['data_aktu']}"] if p["data_aktu"] else []
        if p["ogloszono"]:
            daty.append(f"ogłoszono {p['ogloszono']}")
        daty.append(f"wejście w życie zmiany {p['wejscie'] or '— sprawdź: meta ' + (p['act'].get('ELI') or '').replace('/', ' ')}")
        linie.append(f"  - {_akt_opis(p['act'])}  {p['act'].get('title', '')}".rstrip() + "  (" + ", ".join(daty) + ")"
                     + (_ZNACZNIK_TK if _czy_tk(p["act"]) else ""))
    if wynik is not None:
        wynik.update(pozycje=pozycje, stan="", baza={"ELI": path[len("/acts/"):]} if path else None, bez_tj=True)
    return linie


def _ostrzezenia(refs, path=None, strict=False, wynik=None):
    """Ostrzeżenia o aktualności (lista linii) na podstawie odniesień aktu. `wynik` (dict) dostaje pozycje zmian
    po stanie tekstu (dla analizy imiennej przy --fragment)."""
    out = []
    tj = _tj_acts(refs)
    if tj:
        a = tj[0]
        out.append(f"UWAGA: ten akt ma TEKST JEDNOLITY — cytuj z najnowszego: "
                   f"{a.get('displayAddress') or a.get('ELI', '')} (ELI {a.get('ELI', '')}).")
    linie, uwagi = _nowelizacje_po_tj(refs, path, strict, wynik)
    return out + uwagi + linie + _zmiany_aktu_bez_tj(refs, path, wynik)


def _tj_z_tekstem(path, refs):
    """Najnowszy tekst jednolity z NIEPUSTYM text.html, pomijając akt bieżący.

    API potrafi zwrócić 200 i 0 bajtów dla text.html (textHTML=false) — także dla KILKU kolejnych
    t.j. (k.c.: 2026/795 i 2025/1071 bez HTML, dopiero 2024/1061 z HTML). To jest ostatnia deska
    ratunku, gdy nie da się przetworzyć urzędowego PDF. Zwraca (akt, tekst, pominięte_ELI) albo None.
    """
    biezacy_eli = path[len("/acts/"):]
    tj = _tj_acts(refs)
    if not tj:
        # akt sam jest t.j. — pełną listę tekstów jednolitych mają odniesienia aktu BAZOWEGO
        base = _akt_bazowy(refs)
        if base and base.get("ELI"):
            base_refs = _get(f"/acts/{base['ELI']}/references", soft=True)
            tj = _tj_acts(base_refs) if isinstance(base_refs, dict) else []
    niepewne = None
    pominiete = []
    for act in tj:
        eli_id = act.get("ELI")
        if not eli_id or eli_id == biezacy_eli:
            continue
        # awaria pobrania JEDNEGO kandydata nie może udawać, że zapasowego t.j. nie ma —
        # próbujemy kolejnych, a UNKNOWN zgłaszamy dopiero gdy żaden nie dał tekstu
        try:
            html = _get(f"/acts/{eli_id}/text.html", soft=True)
        except VerificationUnknown as e:
            niepewne = e
            continue
        txt = html_to_text(html) if isinstance(html, str) else ""
        if txt:
            return act, txt, pominiete
        pominiete.append(eli_id)
    if niepewne is not None:
        raise VerificationUnknown(f"nie wszystkie teksty jednolite dało się pobrać ({niepewne})")
    return None


# ---------------------------------------------------------------------------------------------
# Imienna analiza zmian po stanie tekstu: KTÓRE artykuły zmienia akt zmieniający i których dotyczy
# wyrok TK — z TREŚCI aktu zmieniającego / sentencji wyroku (text.html albo urzędowy PDF).
# Dotąd ostrzeżenie było tylko ogólne (lista nowelizacji aktu), a --strict przepuszczał stare
# brzmienie: `--strict tekst DU 2024 499 --fragment "art. 5b"` (radcy prawni, t.j. ze stanem na
# 2024-03-14) dawał kod 0 i art. 5b sprzed zmiany DU 2026/731 (w życie od 2026-06-18).
# ---------------------------------------------------------------------------------------------
_MAKS_ANALIZ = 8          # tyle aktów zmieniających / wyroków TK pobieramy przy jednym wywołaniu
# numer jednostki w tekście: „5b", „4 1" (indeks górny art. 4¹ — HTML i PDF rozdzielają go spacją), „477 6a"
_NUM_JEDN = r"\d+[a-z]*(?:[ ]\d+[a-z]*)?"
_SEP_JEDN = r"(?:\s*,\s*|\s+i\s+|\s+oraz\s+|\s*[–—-]\s*)"
_ART_LISTA = re.compile(r"(?i)(\bpo\s+)?\bart\.\s*(" + _NUM_JEDN + r"(?:" + _SEP_JEDN + r"(?:art\.\s*)?" + _NUM_JEDN + r")*)")
_PAR_LISTA = re.compile(r"(\bpo\s+)?§\s*(" + _NUM_JEDN + r"(?:" + _SEP_JEDN + r"(?:§\s*)?" + _NUM_JEDN + r")*)")
_NAGLOWEK_ZMIANY = re.compile(r"(?m)^[ \t]*(Art\.|§)\s*(\d+[a-z]*)\.\s*(?:\d+\.\s*)?W\s+(?:ustawie|rozporządzeniu|uchwale|"
                              r"kodeksie|dekrecie|zarządzeniu|obwieszczeniu)\b")
_NAGLOWEK_ARTYKULU_ZM = re.compile(r"(?m)^[ \t]*(?:Art\.|§)\s*\d+[a-z]*\.")
# uchylenie albo nowe brzmienie całej jednostki wyższej — artykuły, które w niej były, nie są wymienione z numeru
_JEDNOSTKA_WYZSZA = re.compile(r"(?i)uchyla\s+się\s+(?:rozdział|dział|oddział|tytuł|księg|część)|"
                               r"(?:rozdział|dział|oddział|tytuł)\s+[0-9IVXLa-z]+\s+otrzymuje\s+brzmienie")
_CALA_USTAWA = re.compile(r"(?i)w\s+całej\s+(?:ustawie|rozporządzeniu)|użyt\w*\s+w\s+(?:całej\s+)?(?:ustawie|rozporządzeniu)"
                          r"|w\s+(?:ustawie|rozporządzeniu)\s+(?:w\s+różnej\s+liczbie\s+i\s+przypadku\s+)?wyraz")


def _klucz(numer):
    """Numer jednostki → wspólny klucz z tekstu i z frazy: „5b" → „5b", „4 1" → „4^1", „477 6 a" → „477^6a"."""
    cz = (numer or "").lower().split()
    return "" if not cz else cz[0] if len(cz) == 1 else cz[0] + "^" + "".join(cz[1:])


def _klucz_frazy(fraza):
    """„art. 5b" / „art. 4(1)" / „art. 4¹" / „art. 130(1a)" → klucz jak `_klucz`, inaczej ""."""
    m = re.match(r"(?i)^art\.?\s*" + _NUMER_FRAZY, (fraza or "").strip())
    if not m:
        return ""
    base, idx, sup, letter = m.group(1), m.group(2), m.group(3), m.group(4) or ""
    idx = idx or (sup.translate(_SUP) if sup else "")
    return (base + "^" + idx + letter if idx else base + letter).lower()


def _klucz_naglowka(naglowek):
    """„Art. 4 1." / „Art. 5b." (wynik `_artykul_nad`) → klucz."""
    m = re.match(r"Art\.[ ]*(\d+[a-z]*(?:[ ]+\d+[a-z]*)?)", naglowek or "")
    return _klucz(m.group(1)) if m else ""


def _baza_numeru(klucz):
    m = re.match(r"\d+", klucz or "")
    return int(m.group(0)) if m else None


def _rozwin_liste(lista):
    """„5, 6 i 8" / „5–7" / „459-478" / „4 1 i 4 2" → (klucze, zakresy niedające się rozwinąć [(od, do)])."""
    tokeny = re.split(r"(" + _SEP_JEDN + r")", re.sub(r"(?i)(?:art\.|§)\s*", "", lista))
    klucze, zakresy = set(), []
    poprzedni, myslnik = None, False
    for t in tokeny:
        if re.fullmatch(_SEP_JEDN, t):
            myslnik = bool(re.search(r"[–—-]", t))
            continue
        k = _klucz(t)
        if not k:
            continue
        if myslnik and poprzedni:
            if re.fullmatch(r"\d+", poprzedni) and re.fullmatch(r"\d+", k) and 0 < int(k) - int(poprzedni) <= 400:
                klucze |= {str(n) for n in range(int(poprzedni), int(k) + 1)}
            else:
                zakresy.append((poprzedni, k))
        klucze.add(k)
        poprzedni, myslnik = k, False
    return klucze, zakresy


def _w_zakresie(klucz, zakresy):
    """Czy klucz MOŻE leżeć w nierozwiniętym zakresie (np. „art. 479 88a–479 88j" a art. 479⁸⁸ᶜ)."""
    b = _baza_numeru(klucz)
    return b is not None and any(_baza_numeru(od) is not None and _baza_numeru(do) is not None
                                 and _baza_numeru(od) <= b <= _baza_numeru(do) for od, do in zakresy)


_MAKS_CYTAT_ZMIANY = 200000   # cytat w akcie zmieniającym bywa długi: DU 2026/1296 dodaje rozdziały 4a–4f (ok. 30 tys. znaków)


def _maska_cytatow(txt):
    """Tekst z treścią cudzysłowów „…” zastąpioną spacjami (pozycje i znaki nowej linii bez zmian) + lista obszarów
    niepewnych [(od, do)]. W akcie zmieniającym cytat to NOWE brzmienie, nie polecenie: „Art. 5c. 1. …” w DU 2026/731
    nie jest artykułem aktu zmieniającego, a „po wyrazach „art. 87 § 3”" nie zmienia art. 87. Cudzysłowy bywają
    zagnieżdżone („…o „pracowniku”…”), więc liczymy głębokość; cudzysłów otwarty dłużej niż `_MAKS_CYTAT_ZMIANY`
    albo niezamknięty do końca tekstu to zgubione zamknięcie — w tym obszarze analiza jest niepewna (nie wolno z niej
    wnosić, że artykułu nie zmieniono)."""
    out, glebokosc, start, niepewne = [], 0, 0, []
    for i, ch in enumerate(txt):
        if ch in _OTWARCIE:
            if glebokosc == 0:
                start = i
            glebokosc += 1
            out.append(" ")
        elif glebokosc and ch in _ZAMKNIECIE:
            glebokosc -= 1
            out.append(" ")
        elif glebokosc:
            if i - start > _MAKS_CYTAT_ZMIANY:
                glebokosc = 0
                niepewne.append((start, i))
                out.append(ch)
            else:
                out.append("\n" if ch == "\n" else " ")
        else:
            out.append(ch)
    if glebokosc:
        niepewne.append((start, len(txt)))
    return "".join(out), niepewne


def _cytowane_pozycje(glowa):
    """PIERWSZA pozycja Dz.U. z pierwszego nawiasu „(Dz. U. …)" w nagłówku przepisu zmieniającego / punkcie
    sentencji — to akt albo jego t.j.; dalsze pozycje to jego wcześniejsze NOWELIZACJE. Wszystkich brać nie wolno:
    DU 1996/646 art. 2 zmienia k.c. „(Dz. U. Nr 16, poz. 93, … z 1993 r. Nr 17, poz. 78 …)" — a 1993/78 to ustawa
    o planowaniu rodziny, którą zmienia art. 1 (sprawdzone 2026-11-13). Zwraca zbiór 0–1 elementów."""
    m = re.search(r"\((Dz\.\s*U\.[^()]*)\)", glowa)
    if not m:
        return set()
    pierwsza = re.match(r"(Dz\.\s*U\.\s*(?:z\s+\d{4}\s*r\.\s*)?(?:Nr\s*\d+\s*,\s*)?poz\.\s*\d+)", m.group(1))
    if not pierwsza:
        return set()
    wn = pierwsza.group(1)
    if not re.search(r"z\s+\d{4}\s*r\.", wn):
        # „(Dz. U. Nr 16, poz. 93, …)" / „(Dz. U. poz. 550, …)" bez roku — rok aktu z „z dnia … RRRR r." przed nawiasem
        lata = re.findall(r"\b(\d{4})\s*r\.", glowa[:m.start()])
        if not lata:
            return set()
        wn = re.sub(r"^Dz\.\s*U\.\s*", f"Dz. U. z {lata[-1]} r. ", wn)
    return _podstawa_ujednolicenia(wn)


def _tytul_bazy(base):
    """Rdzeń tytułu aktu bazowego („Kodeks postępowania cywilnego", „o radcach prawnych") — małe litery, bez kropki."""
    t = re.sub(r"(?i)^.*?\bz\s+dnia\s+\d{1,2}\s+\w+\s+\d{4}\s*r\.\s*[-–—]?\s*", "", (base or {}).get("title") or "", count=1)
    return _norm(" ".join(t.split())).rstrip(". ")


def _data_slownie(iso):
    try:
        r, m, d = (int(x) for x in iso.split("-"))
    except (ValueError, AttributeError):
        return ""
    nazwa = next((k for k, v in _MIESIACE.items() if v == m), "")
    return f"{d} {nazwa} {r}" if nazwa else ""


def _segmenty_dla_aktu(txt, cele, base):
    """Przepisy aktu zmieniającego, które zmieniają AKT BAZOWY. Rozpoznanie po cytacie publikatora w nagłówku
    („W ustawie z dnia … (Dz. U. z 2024 r. poz. 499, …)") — pozycja aktu bazowego albo któregoś jego t.j. (`cele`);
    sama data nie wystarcza: k.k., k.k.w. i k.p.k. są „z dnia 6 czerwca 1997 r.". Bez cytatu — data i rdzeń
    tytułu. Zwraca (maska, oryginał, [(start, koniec, numer przepisu)], obszary niepewne)."""
    maska, niepewne = _maska_cytatow(txt)
    naglowki = list(_NAGLOWEK_ZMIANY.finditer(maska))
    granice = [m.start() for m in _NAGLOWEK_ARTYKULU_ZM.finditer(maska)]
    data = _data_slownie((base or {}).get("announcementDate") or "")
    tytul = _tytul_bazy(base)
    seg = []
    for m in naglowki:
        glowa = maska[m.start():m.start() + 900]
        poz = _cytowane_pozycje(glowa)
        g = " ".join(glowa.split())
        g = g[:g.find("(Dz. U.")] if "(Dz. U." in g else g      # tytuł stoi przed cytatem publikatora
        g = re.split(r"\s(?:wprowadza\s|w\s+art\.|po\s+art\.|art\.\s*\d|uchyla\s|dodaje\s|w\s+§|§\s*\d)", g)[0]
        g = re.sub(r"^(?:Art\.|§)\s*\d+[a-z]*\.\s*(?:\d+\.\s*)?W\s+", "", g)
        # cytat zaczyna się od aktu albo jego t.j.; bez cytatu albo z t.j. spoza listy API — data i tytuł z granicą
        # wyrazu (k.k. a „Kodeks karny wykonawczy" — oba „z dnia 6 czerwca 1997 r.")
        trafia = bool(poz & cele) or bool(data and tytul and _ten_akt(g, base, data) and f"z dnia {data} r." in _norm(g))
        if trafia:
            koniec = next((g for g in granice if g > m.start() + 3), len(maska))
            seg.append((m.start(), koniec, ("art. " if m.group(1) == "Art." else "§ ") + m.group(2)))
    return maska, txt, seg, niepewne


def _zmiany_w_akcie(txt, cele, base):
    """Treść aktu zmieniającego → dict: „klucze" (artykuły zmienione, dodane albo uchylone w akcie bazowym),
    „zakresy" (nierozwinięte), „calosc" (zmiana „w całej ustawie"), „przepisy" (numery przepisów zmieniających),
    „wejscie" (zdanie o wejściu w życie), „blad" (gdy nie ustalono)."""
    maska, orig, seg, niepewne = _segmenty_dla_aktu(txt, cele, base)
    wynik = {"klucze": set(), "zakresy": [], "calosc": False, "przepisy": [s[2] for s in seg], "wejscie": "",
             "blad": "", "paragrafy": set()}
    if not seg:
        wynik["blad"] = "nie znalazłem w nim przepisu zmieniającego ten akt"
    # niedomknięty cudzysłów PRZED przepisem albo w nim mógł ukryć polecenie zmiany albo nagłówek przepisu
    if any(od < e for od, do in niepewne for s, e, _ in seg) or (niepewne and not seg):
        wynik["blad"] = wynik["blad"] or "niedomknięty cudzysłów w tekście — analiza niepewna"
    for s, e, _ in seg:
        instr = maska[s:e]
        # pierwszy wiersz nagłówka zawiera tytuł i cytat aktu bazowego — żadnych numerów jednostek tego aktu
        cyt = re.search(r"\(Dz\.\s*U\.[^()]*\)", instr)
        # bez nagłówka „Art. 1." przepisu zmieniającego — to nie jest numer jednostki aktu bazowego
        instr_bez = instr[cyt.end():] if cyt else re.sub(r"^\s*(?:Art\.|§)\s*\d+[a-z]*\.", "", instr)
        for m in _ART_LISTA.finditer(instr_bez):
            k, z = _rozwin_liste(m.group(2))
            if m.group(1):          # „po art. 5c dodaje się art. 5d" — art. 5c to tylko miejsce wstawienia
                k = set()
            wynik["klucze"] |= k
            wynik["zakresy"] += z
        for m in _PAR_LISTA.finditer(instr_bez):
            if not m.group(1):
                k, _ = _rozwin_liste(m.group(2))
                wynik["paragrafy"] |= k
        # nowe brzmienie / dodane artykuły — nagłówki „Art. N." na początku cytatu
        for m in re.finditer(r"(?m)^[ \t]*[„]?\s*(Art\.|§)\s*(" + _NUM_JEDN + r")\.", orig[s:e]):
            if maska[s + m.start(2)] == " ":     # leży w cytacie
                (wynik["klucze"] if m.group(1) == "Art." else wynik["paragrafy"]).add(_klucz(m.group(2)))
        if _CALA_USTAWA.search(instr_bez):
            wynik["calosc"] = True
        if _JEDNOSTKA_WYZSZA.search(instr_bez):
            wynik["calosc"] = True
            wynik["jednostka"] = True
    w = list(re.finditer(r"(?:Ustawa|Rozporządzenie|Uchwała|Zarządzenie)\s+wchodzi\s+w\s+życie[^\n]{0,400}", maska))
    if w:
        wynik["wejscie"] = " ".join(orig[w[-1].start():w[-1].end()].split())[:400]
    return wynik


_WERDYKT = re.compile(r"(?i)\b(nie\s+(?:jest|są)\s+niezgodn|(?:jest|są)\s+zgodn|(?:jest|są)\s+nie\s*-?\s*zgodn|"
                      r"(?:jest|są)\s+niezgodn|tra(?:ci|cą)\s+moc)")
# wyrok zakresowy / interpretacyjny: brzmienie zostaje, przepis nie obowiązuje tylko w pewnym zakresie. „w zakresie"
# bywa ZA rozstrzygnięciem („…jest niezgodny … w zakresie, w którym odnosi się do spadków otwartych od tego dnia" —
# SK 4/99), więc szukamy w całym punkcie; „w brzmieniu" i „w związku z art." — tylko w przedmiocie (we wzorcach
# kontroli „w związku z art. 31 ust. 3 Konstytucji" to nie zakres).
_ZAKRESOWY = re.compile(r"(?i)w\s+zakresie|w\s+części,?\s+(?:w\s+jakiej|w\s+której|dotycz|obejmuj)|rozumian|"
                        r"przez\s+to,?\s+że|wskutek\s+pominięcia|pomija")   # nie „Przepis wymieniony w części I traci moc"
_ZAKRESOWY_PRZEDMIOT = re.compile(r"(?i)w\s+brzmieniu|w\s+związku\s+z\s+(?:art|§)")
_ODESLANIE_AKTU = re.compile(r"(?i)ustawy\s+powołanej|tej\s+ustawy|tejże\s+ustawy|ustawy\s+wymienionej|rozporządzenia\s+"
                             r"powołanego|tego\s+rozporządzenia|kodeksu\s+powołanego|powołan\w+\s+w\s+(?:pkt|punkcie|części)")
_AKT_W_SENTENCJI = re.compile(r"(?i)\b(ustawy|rozporządzenia|kodeksu|dekretu|uchwały|ustawie)\b")
# początek punktu sentencji: „orzeka: Art. …", „1. Art. …", „I § 31 …", „…; b) …" — NIE „Art. 673 § 3" (paragraf
# w artykule) ani „w związku z § 5 rozporządzenia"
_PUNKT_SENTENCJI = re.compile(r"(?:^|[:;]\s*|\.\s+|(?:^|\s)(?:[IVX]+|\d+\.|\d+\)|[a-z]\))\s+)$")
_SLOWA_POMIJANE = {"z", "w", "o", "i", "oraz", "dnia", "r", "-", "–", "—"}


def _rdzen_tytulu(slowa, n=4):
    return [w[:4] for w in slowa if w not in _SLOWA_POMIJANE and not re.fullmatch(r"\d+\.?", w)][:n]


def _ten_akt(fraza, base, data):
    """Czy fraza aktu z sentencji („ustawy z dnia 23 kwietnia 1964 r. - Kodeks cywilny", „Kodeksu cywilnego") to akt
    bazowy: data (gdy podana) i pierwsze wyrazy tytułu porównane po 4 literach (odmiana: „Kodeksu cywilnego"),
    a za nimi nie może stać dalszy wyraz tytułu (k.k. a „Kodeksu karnego wykonawczego")."""
    f = _norm(" ".join(fraza.split()))
    dm = re.search(r"z dnia (\d{1,2} \w+ \d{4}) r\.", f)
    if dm and data and dm.group(1) != data:
        return False
    reszta = re.sub(r"^.*?z dnia \d{1,2} \w+ \d{4} r\.\s*[-–—]?\s*", "", f, count=1) if dm else f
    slowa_f = re.findall(r"[^\s,()]+", reszta.split("(")[0])
    slowa_b = re.findall(r"[^\s,()]+", _tytul_bazy(base))
    rb = _rdzen_tytulu(slowa_b)
    if not rb:
        return False
    rf = _rdzen_tytulu(slowa_f, len(rb) + 1)
    if rf[:len(rb)] != rb:
        return False
    # tytuł bazowy krótszy niż 4 znaczące wyrazy — następny wyraz frazy nie może go przedłużać
    return len(_rdzen_tytulu(slowa_b, 99)) > len(rb) or len(rf) == len(rb) or not re.match(
        r"[a-ząćęłńóśźż]", (slowa_f[len(rb):len(rb) + 1] or [""])[0]) or (slowa_f[len(rb):len(rb) + 1] or [""])[0] in (
        "w", "jest", "są", "nie", "oraz", "i", "z", "dodany", "rozumiany", "rozumiane", "ze")


def _sentencja_tk(txt, cele, base):
    """Sentencja wyroku TK (od „orzeka:" do „Ponadto postanawia" itp.) → lista punktów: {„tekst", „klucze",
    „paragrafy", „zakresy", „czesc" (wskazano ust./§/pkt/zdanie), „zakresowy", „werdykty" ({'niezgodny','zgodny'};
    „traci moc" = niezgodny), „akt" ('ten' | 'inny' | '?')}. None, gdy sentencji nie rozpoznano. Przedmiot punktu
    zaczyna się od „Art." WIELKĄ literą albo od „§" — małe „art." to wzorce kontroli („z art. 2 Konstytucji")
    albo odesłania („w związku z art. 13"). Akt punktu: cytat publikatora tuż za nazwą aktu (pozycja aktu bazowego
    albo jego t.j.), inaczej data i tytuł, inaczej odesłanie („ustawy powołanej w punkcie 1") do poprzedniego punktu."""
    t = " ".join(txt.split())
    m = re.search(r"(?i)\b(orzeka|ogłaszam)\s*:?\s*(.*?)(?:Ponadto\s+postanawia|\bpostanawia\s*:|Orzeczenie\s+zapadło|"
                  r"Poświadczono|Uzasadnienie|UZASADNIENIE|Zdanie\s+odrębne|\Z)", t)
    if not m:
        return None
    oper = m.group(2)
    data = _data_slownie((base or {}).get("announcementDate") or "")
    starty = [x.start() for x in re.finditer(r"(?:Art\.|§)\s*\d", oper) if _PUNKT_SENTENCJI.search(oper[:x.start()])]
    if not starty or m.group(1).lower() == "ogłaszam":
        starty = [0]
    punkty, poprzedni_akt = [], "?"
    for i, s in enumerate(starty):
        tekst = oper[s:starty[i + 1] if i + 1 < len(starty) else len(oper)].strip()
        wer = _WERDYKT.search(tekst)
        przedmiot = tekst[:wer.start()] if wer else tekst
        werdykty = set()
        for w in _WERDYKT.finditer(tekst):
            g = re.sub(r"\s+", " ", w.group(1).lower())
            werdykty.add("zgodny" if g.startswith("nie ") or re.search(r"(?:jest|są) zgodn", g) else "niezgodny")
        a = _AKT_W_SENTENCJI.search(przedmiot)
        lista = przedmiot[:a.start()] if a else przedmiot
        klucze, paragrafy, zakresy, czesc = set(), set(), [], False
        for x in _ART_LISTA.finditer(lista):
            k, z = _rozwin_liste(x.group(2))
            klucze |= k
            zakresy += z
            czesc = czesc or bool(re.match(r"\s*(?:ust\.|§|pkt|zdani|lit\.)", lista[x.end():]))
        if lista.lstrip().startswith("§"):
            x = _PAR_LISTA.match(lista.lstrip())
            if x:
                paragrafy |= _rozwin_liste(x.group(2))[0]
                czesc = czesc or bool(re.match(r"\s*(?:ust\.|pkt|zdani|lit\.)", lista.lstrip()[x.end():]))
        # fraza aktu: od nazwy aktu do pierwszego zastrzeżenia zakresu / rozstrzygnięcia
        fraza = przedmiot[a.start():] if a else ""
        koniec = re.search(r"(?i)\s(?:w\s+brzmieniu|w\s+zakresie|w\s+części|rozumian|[a-z]\)\s|w\s+związku)", fraza)
        fraza = fraza[:koniec.start()] if koniec else fraza
        poz = _cytowane_pozycje(fraza) if fraza else set()
        if not a and _AKT_W_SENTENCJI.search(tekst) is None and re.search(r"(?i)Kodeks\w*\s", przedmiot):
            fraza = przedmiot
        if poz:
            akt = "ten" if poz & cele or _ten_akt(fraza, base, data) else "inny"
        elif _ODESLANIE_AKTU.search(fraza or przedmiot):
            akt = poprzedni_akt
        elif fraza and _ten_akt(fraza, base, data):
            akt = "ten"
        elif fraza or re.search(r"(?i)\bKodeks", przedmiot):
            kod = re.search(r"(?i)\bKodeks\w*(?:\s+[a-ząćęłńóśźż]+){1,3}", przedmiot)
            akt = "ten" if kod and _ten_akt(kod.group(0), base, data) else ("inny" if fraza or kod else "?")
        else:
            akt = "?"
        poprzedni_akt = akt
        punkty.append({"tekst": tekst, "klucze": klucze, "paragrafy": paragrafy, "zakresy": zakresy, "czesc": czesc,
                       "zakresowy": bool(_ZAKRESOWY.search(tekst) or _ZAKRESOWY_PRZEDMIOT.search(przedmiot)),
                       "werdykty": werdykty, "akt": akt})
    return punkty


def _tekst_aktu_po_eli(eli_id):
    """Tekst aktu do analizy (text.html, a bez niego urzędowy PDF przez pdftotext — bez OCR). Zwraca (tekst, błąd)."""
    path = f"/acts/{eli_id}"
    try:
        html = _get(path + "/text.html", soft=True)
        txt = html_to_text(html) if isinstance(html, str) else ""
        if txt:
            return txt, ""
        meta = _get(path, soft=True)
        if not isinstance(meta, dict):
            return "", "brak metadanych aktu"
        info = {}
        txt, _, blad = _tekst_z_pdf(path, eli_id.replace("/", " "), meta, info, ocr=False)
        if txt and (info.get("ocr") or info.get("puste_strony")):
            # OCR skanu z lat 90. przekłamuje cudzysłowy i numery („,,", „art. 5 1") — z takiego tekstu nie wolno
            # wnosić, że artykułu NIE zmieniono (ustawy 1991–2010 mają HTML; dotyczy to rzadkich rozporządzeń)
            return "", "tekst aktu to OCR skanu albo ma strony bez warstwy tekstowej — analiza niepewna"
        if txt and info.get("bez_wspolrzednych"):
            # bez współrzędnych numery („art. 131" zamiast 13¹) i liczby przed przypisem mogą być przekłamane
            return "", "pdftotext -bbox nie zwrócił współrzędnych słów (indeksy górne i odsyłacze niepotwierdzone) — analiza niepewna"
        return txt, blad
    except VerificationUnknown as e:
        return "", str(e)
    except Exception as e:      # analiza pomocnicza: awaria = „nie ustalono", nigdy ślad stosu zamiast tekstu
        return "", f"{type(e).__name__}: {e}"


def _stan_z_obwieszczenia(txt):
    """Data z obwieszczenia o t.j.: („stan", ISO) dla „z uwzględnieniem stanu prawnego na dzień X" albo
    („przed", ISO) dla „…zmian wynikających z przepisów ogłoszonych przed dniem X"; inaczej ("", "").
    Pomiar 2026-11-13 na 15 t.j.: 9 z datą w PDF, w 8 zgodna z legalStatusDate, k.p.c. DU 2026/468 — API
    2026-03-25, obwieszczenie „24 marca 2026 r."; 2 obwieszczenia rozporządzeń nie podają daty wcale."""
    glowa = " ".join(re.sub(r"(?m)^» ", "", txt[:60000]).split())
    for rodzaj, wzor in (("stan", r"stanu prawnego na dzień "), ("przed", r"ogłoszonych przed dniem ")):
        m = re.search(wzor + _DATA_SLOWNIE, glowa)
        if m:
            return rodzaj, _iso(m.group(1), _MIESIACE[m.group(2)], m.group(3))
    return "", ""


def _cytowane_w_przypisach(txt):
    """Pozycje Dz.U. cytowane w przypisach i notkach marginesu (wiersze „[przypis", „[margines:") — dowód, że tekst
    zawiera zmianę z tej pozycji (k.p.c. DU 2026/468: poz. 26 tylko w notkach). Obwieszczenia („» ") tu nie bierzemy:
    jego pkt 2 („nie obejmuje…") cytuje zmiany, których w tekście NIE ma — obwieszczenie ma własny parser."""
    linie = "\n".join(l for l in txt.split("\n") if l.startswith(("[przypis", "[margines:")))
    return _pozycje_dz_u(linie)


def _cele_bazy(base_eli, base_refs):
    cele = {_eli_rok_poz({"ELI": base_eli})} if base_eli else set()
    if isinstance(base_refs, dict):
        cele |= {_eli_rok_poz(a) for a in _tj_acts(base_refs)}
    return {c for c in cele if c != (0, 0)}


def _sygn_tk(act):
    t = (act or {}).get("title") or ""
    m = re.search(r"sygn\.\s*akt\s*(.+?)\.?$", t)
    if m:
        return m.group(1).strip()
    if "Prezesa Trybunału Konstytucyjnego" in t:
        return "obwieszczenie Prezesa TK" + (f" z {act['announcementDate']}" if act.get("announcementDate") else "")
    return _akt_opis(act)


def _orzeczenia_tk(base_refs):
    """Wyroki TK z kategorii „Orzeczenie TK" aktu bazowego (najnowsze pierwsze), z datą skutku z „Akty zmieniające"
    (tam API podaje datę utraty mocy, także odroczonej: SK 109/20 — ogłoszony 2023-12-28, skutek 2024-12-29).
    Uwaga: kategoria obejmuje też wyroki „zgodny" i wyroki o INNYCH aktach powołujących ten akt (k.c.: P 46/09
    dotyczy ustawy zmieniającej k.c.) — przynależność do listy nie znaczy, że przepis utracił moc."""
    if not isinstance(base_refs, dict):
        return []
    skutki = {r["act"].get("ELI"): r.get("date") or "" for r in _lista(base_refs, "akty zmieniające") if _czy_tk(r["act"])}
    out = []
    for r in _lista(base_refs, "orzeczenie tk"):
        out.append({"act": r["act"], "skutek": skutki.get(r["act"].get("ELI"), "")})
    return sorted(out, key=lambda x: x["act"].get("promulgation") or x["act"].get("announcementDate") or "", reverse=True)


def _linie_tk_meta(base_refs, maks=5):
    tk = _orzeczenia_tk(base_refs)
    if not tk:
        return []
    out = [f"Orzeczenia TK dotyczące aktu (API, kategoria „Orzeczenie TK”): {len(tk)} — lista obejmuje też wyroki "
           "„zgodny” i wyroki o innych aktach; czy przepis utracił moc, mówi sentencja (tekst <sygnatura wyroku>):"]
    for x in tk[:maks]:
        a = x["act"]
        out.append(f"  - {_sygn_tk(a)}  {_akt_opis(a)}  (ogłoszono {a.get('promulgation') or '—'}"
                   + (f"; skutek dla tego aktu od {x['skutek']}" if x["skutek"] else "; API nie podaje skutku (utraty mocy)")
                   + ")")
    if len(tk) > maks:
        out.append(f"  - … i {len(tk) - maks} starszych (pełna lista: odniesienia <akt bazowy>)")
    return out


def _opis_kluczy(klucze):
    """[„5b", „4^1", „§31"] → „art. 5b, art. 4(1), § 31"."""
    out = []
    for k in klucze:
        if k.startswith("§"):
            out.append("§ " + k[1:].replace("^", "(") + (")" if "^" in k else ""))
        else:
            out.append("art. " + k.replace("^", "(") + (")" if "^" in k else ""))
    return ", ".join(out)


def _aktualnosc(wynik, txt, klucze, info, strict, dzis=None):
    """Imienna analiza zmian po stanie tekstu. `wynik` — z `_ostrzezenia` (pozycje, stan, baza, bez_tj); `klucze` —
    artykuły wypisywanego fragmentu (None = cały tekst); `info` — {podstawa, uwzgl, nieobj, przed, stan_obw}.
    Zwraca (linie ostrzeżeń, powód blokady --strict albo "")."""
    dzis = dzis or _dzis()
    pozycje, stan, base = wynik.get("pozycje") or [], wynik.get("stan") or "", wynik.get("baza") or {}
    base_eli = (base or {}).get("ELI") or ""
    try:
        base_refs = _get(f"/acts/{base_eli}/references", soft=True) if base_eli else None
    except VerificationUnknown:
        base_refs = None
    cele = _cele_bazy(base_eli, base_refs)
    linie, blokady = [], []
    # wyroki TK ogłoszone po stanie tekstu, których API nie zapisało jako zmiany („zgodny" albo bez daty skutku)
    znane = {p["act"].get("ELI") for p in pozycje}
    for x in _orzeczenia_tk(base_refs):
        ogl = x["act"].get("promulgation") or ""
        if x["act"].get("ELI") not in znane and ogl and (wynik.get("bez_tj") or (stan and ogl >= stan)):
            pozycje = pozycje + [{"act": x["act"], "ogloszono": ogl, "wejscie": x["skutek"] or ogl, "bez_skutku": True}]
    podstawa, uwzgl, nieobj = info.get("podstawa") or set(), info.get("uwzgl") or set(), info.get("nieobj") or set()
    przed, cytowane = info.get("przed") or "", _cytowane_w_przypisach(txt)
    do_analizy = []
    for p in pozycje:
        act = p["act"]
        k = _eli_rok_poz(act)
        ogl, wej = p.get("ogloszono") or "", p.get("wejscie") or ""
        if k in nieobj:
            stan_wl = "brak"
        elif k in podstawa or k in uwzgl or (przed and ogl and ogl < przed) or k in cytowane:
            continue
        elif wynik.get("bez_tj") or (ogl and stan and ogl >= stan):
            stan_wl = "brak"
        else:
            stan_wl = "niepewne"
        do_analizy.append(dict(p, wlaczenie=stan_wl, obowiazuje=not wej or wej <= dzis))
    if not do_analizy:
        return linie, ""
    if base_eli and not base.get("title"):
        # tytuł i data aktu bazowego — do rozpoznania go w akcie zmieniającym bez cytatu publikatora
        try:
            m = _get(f"/acts/{base_eli}", soft=True)
            base = m if isinstance(m, dict) else base
        except Exception:       # bez tytułu zostaje rozpoznanie po cytacie publikatora
            pass
    # najpierw zmiany już obowiązujące (od nich zależy blokada), w nich najnowsze — limit analiz obcina najstarsze
    do_analizy.sort(key=lambda p: (p["obowiazuje"], p.get("wejscie") or ""), reverse=True)
    tk_po = [p for p in do_analizy if _czy_tk(p["act"])]
    if tk_po:
        linie.append(f"UWAGA — WYROKI TK OGŁOSZONE PO STANIE TEGO TEKSTU" + (f" ({stan})" if stan else "") + f": {len(tk_po)} — "
                     "ich skutków (utraty mocy przepisu albo jego części) NIE MA w tekście poniżej:")
        for p in tk_po:
            linie.append(f"  - {_sygn_tk(p['act'])}  {_akt_opis(p['act'])}  (ogłoszono {p.get('ogloszono') or '—'}; "
                         + ("API nie podaje skutku dla tego aktu — możliwy wyrok „zgodny”" if p.get("bez_skutku")
                            else f"skutek od {p.get('wejscie') or '—'}") + ")")
    nazwa = "fragmentu" if klucze is not None else "tekstu"
    analizy = 0
    niezbadane = []
    for p in do_analizy:
        act, eli_id = p["act"], p["act"].get("ELI") or ""
        opis = f"{_akt_opis(act)}" + (f" ({_sygn_tk(act)})" if _czy_tk(act) else "")
        wej = p.get("wejscie") or "—"
        tk = _czy_tk(act)
        if klucze is None and not tk:
            # cały tekst: każda zmiana w życiu, której tekst nie ma, to stare brzmienie gdzieś w akcie
            if p["obowiazuje"]:
                blokady.append(f"{opis} (wejście w życie zmiany {wej}) — "
                               + ("NIE MA jej w tym tekście" if p["wlaczenie"] == "brak" else "nie ustaliłem, czy tekst ją zawiera"))
            continue
        if not klucze and klucze is not None:
            if p["obowiazuje"] and not p.get("bez_skutku"):
                niezbadane.append(f"{opis} (od {wej})")
            continue
        if analizy >= _MAKS_ANALIZ:
            if p["obowiazuje"]:
                niezbadane.append(f"{opis} (od {wej}; limit {_MAKS_ANALIZ} analiz)")
            continue
        analizy += 1
        tekst_zm, blad = _tekst_aktu_po_eli(eli_id) if eli_id else ("", "brak ELI")
        if not tekst_zm:
            if p["obowiazuje"]:
                niezbadane.append(f"{opis} (od {wej}; treści nie udało się pobrać: {blad})")
            continue
        if tk:
            pkt = _sentencja_tk(tekst_zm, cele, base)
            if not pkt:
                if p["obowiazuje"]:
                    niezbadane.append(f"{opis} (nie rozpoznałem sentencji)")
                continue
            trafiono = False
            if not p.get("bez_skutku") and not any(x["akt"] in ("ten", "?") for x in pkt):
                # API zapisało skutek wyroku dla TEGO aktu, a żaden punkt sentencji go nie wskazuje (wyrok o przepisie
                # innej ustawy: SK 34/08 — art. 5 ustawy zmieniającej k.c.; SK 49/05; obwieszczenia Prezesa TK z lat 90.)
                if p["obowiazuje"] or (p.get("wejscie") or "") > dzis:
                    niezbadane.append(f"{opis} (skutek dla tego aktu od {wej} wg API, ale sentencja nie wskazuje go wprost "
                                      "— przeczytaj ją: tekst " + eli_id.replace("/", " ") + ")")
                continue
            for x in pkt:
                wszystkie = x["klucze"] | {"§" + k for k in x["paragrafy"]}
                trafione = sorted(k for k in (klucze or set()) if k in wszystkie or _w_zakresie(k, x["zakresy"])) \
                    if klucze is not None else sorted(wszystkie)
                if not trafione or x["akt"] == "inny" or not x["werdykty"]:
                    if trafione and x["akt"] != "inny" and not x["werdykty"] and p["obowiazuje"]:
                        niezbadane.append(f"{opis} (nie rozpoznałem rozstrzygnięcia dla art. {', '.join(trafione)})")
                    continue
                trafiono = True
                cytat = x["tekst"] if len(x["tekst"]) <= 500 else x["tekst"][:497] + "…"
                arty = _opis_kluczy(trafione)
                if x["werdykty"] == {"zgodny"}:
                    linie.append(f"Informacja: wyrok TK {opis} uznał {arty} za zgodny z Konstytucją (bez skutku dla "
                                 f"brzmienia). Sentencja: „{cytat}”")
                    continue
                # „niezgodny" z jednym wzorcem przy „zgodny" z innymi to nadal utrata mocy: K 9/04 (art. 179 k.c.)
                # i SK 18/2000 (art. 418 k.c.) — t.j. ma „Art. 179. (utracił moc)" i przypis przy art. 418
                calosc = "niezgodny" in x["werdykty"] and not x["zakresowy"]
                pewny = x["akt"] == "ten"
                if p.get("wejscie") and p["wejscie"] > dzis:
                    linie.append(f"UWAGA — WYROK TK: {arty} {'' if pewny else '(może dotyczyć tego aktu) '}uznany za "
                                 f"niezgodny z Konstytucją ({opis}); utrata mocy ODROCZONA do {p['wejscie']} — do tego "
                                 f"dnia przepis obowiązuje. Sentencja: „{cytat}”")
                elif calosc:
                    linie.append(f"UWAGA — PRZEPIS UTRACIŁ MOC (wyrok TK {opis}, skutek od {wej}): {arty}"
                                 + (" — co najmniej wskazana jednostka (ust./§/pkt)" if x["czesc"] else " — w całości")
                                 + f"; tekst poniżej (stan {stan or 'ogłoszony'}) tego NIE uwzględnia"
                                 + ("" if pewny else " (nie ustaliłem pewnie, że punkt sentencji dotyczy TEGO aktu)")
                                 + f". Sentencja: „{cytat}”")
                    blokady.append(f"{arty}: utrata mocy po wyroku TK {opis} od {wej}")
                else:
                    linie.append(f"UWAGA — WYROK TK ZAKRESOWY albo częściowy ({opis}, skutek od {wej}): {arty} — "
                                 "brzmienie się nie zmienia, ale przepis w zakresie wskazanym w sentencji nie obowiązuje"
                                 + ("" if pewny else " (nie ustaliłem pewnie, że punkt dotyczy TEGO aktu)")
                                 + f". Sentencja: „{cytat}”")
            if not trafiono and klucze:
                linie.append(f"Informacja: sentencja wyroku TK {opis} nie wymienia {_opis_kluczy(sorted(klucze))}.")
            continue
        z = _zmiany_w_akcie(tekst_zm, cele, base)
        if z["blad"] or z["calosc"]:
            powod = z["blad"] or ("uchylenie albo nowe brzmienie całego rozdziału/działu — artykuły w nim nie są "
                                  "wymienione z numeru" if z.get("jednostka") else
                                  "zmiana „w całej ustawie” (np. wyrazów w wielu przepisach)")
            if p["obowiazuje"]:
                niezbadane.append(f"{opis} (od {wej}; {powod})")
            else:
                linie.append(f"UWAGA: {opis} wejdzie w życie {wej} — nie ustaliłem, czy zmienia ten {nazwa} ({powod}).")
            continue
        wszystkie = z["klucze"] | {"§" + k for k in z["paragrafy"]}
        trafione = sorted(k for k in klucze if k in wszystkie or (not k.startswith("§") and _w_zakresie(k, z["zakresy"])))
        arty = _opis_kluczy(trafione)
        przepis = f"{', '.join(z['przepisy'])} aktu zmieniającego"
        if not trafione:
            linie.append(f"Informacja: w treści {opis} ({przepis}) nie znalazłem zmiany {_opis_kluczy(sorted(klucze))}.")
            continue
        wejscie_opis = f"; przepis o wejściu w życie: „{z['wejscie']}”" if z["wejscie"] else ""
        # data z API to termin dla aktu zmieniającego jako całości; punkty bywają odroczone (DU 2026/1290: „z wyjątkiem
        # art. 1 pkt 16, 41 i 42 … 1 stycznia 2027 r."; DU 2026/26 — część zmian k.p.c. od 2027-01-01)
        rozne = "wyjątkiem" in z["wejscie"] or bool(p.get("uwagi"))
        wej = f"{wej} (data API dla aktu zmieniającego" + ("; dla poszczególnych punktów terminy są RÓŻNE — sprawdź "
                                                           "przepis o wejściu w życie)" if rozne else ")")
        if not p["obowiazuje"]:
            linie.append(f"UWAGA: {arty} ZMIENIA {opis} ({przepis}) z dniem {wej} — do tego dnia obowiązuje brzmienie "
                         f"poniżej, potem nowe{wejscie_opis}.")
        elif p["wlaczenie"] == "brak":
            linie.append(f"UWAGA — TEN PRZEPIS ZMIENIONO po stanie tego tekstu: {arty} zmienia {opis} ({przepis}), wejście "
                         f"w życie zmiany {wej}{wejscie_opis}. "
                         + ("Poniżej brzmienie bez tej zmiany (czy nowe już obowiązuje, zależy od terminu dla danego "
                            "punktu)" if rozne else "Poniżej brzmienie SPRZED tej zmiany")
                         + f" — aktualne nanieś z: tekst {eli_id.replace('/', ' ')} --fragment \"{z['przepisy'][0]}\".")
            blokady.append(f"{arty}: zmiana {opis} od {wej}, której ten tekst nie zawiera")
        else:
            linie.append(f"UWAGA: {arty} zmienia {opis} ({przepis}), wejście w życie zmiany {wej} — nie ustaliłem, czy "
                         f"ten tekst już ją zawiera; porównaj z: tekst {eli_id.replace('/', ' ')} --fragment "
                         f"\"{z['przepisy'][0]}\".")
            blokady.append(f"{arty}: nie ustaliłem, czy tekst zawiera zmianę {opis} od {wej}")
    if niezbadane:
        linie.append(f"UWAGA: nie ustaliłem, czy te zmiany po stanie tekstu dotyczą tego {nazwa}: " + "; ".join(niezbadane)
                     + " — sprawdź je ręcznie, zanim zacytujesz.")
        blokady.append("nieustalony wpływ zmian: " + "; ".join(niezbadane))
    # ostrzeżenia najpierw, informacje („nie znalazłem zmiany") na końcu
    return sorted(linie, key=lambda l: l.startswith("Informacja")), "; ".join(blokady)


def _klucze_fragmentu(txt, spans):
    """Artykuły (klucze `_klucz`), w których leżą wypisywane fragmenty; w akcie bez artykułów (rozporządzenie) —
    paragrafy jako „§N". Pusty zbiór = fragmentu nie da się przypisać do jednostki (analiza zgłosi niepewność)."""
    klucze = set()
    ma_art = bool(_NAGLOWEK_ART.search(txt))
    for s, e in spans or []:
        if ma_art:
            k = _klucz_naglowka(_artykul_nad(txt, s))
            if k:
                klucze.add(k)
            for m in re.finditer(r"(?m)^[\[<]?Art\.[ ]*(\d+[a-z]*(?:[ ]+\d+[a-z]*)?)[ ]*\.", txt[s:e]):
                klucze.add(_klucz(m.group(1)))
        else:
            ostatni = None
            for m in re.finditer(r"(?m)^[\[<]?§\s*(\d+[a-z]*(?:\s+\d+[a-z]*)?)\s*\.", txt[:s + 30]):
                ostatni = m
            if ostatni:
                klucze.add("§" + _klucz(ostatni.group(1)))
            for m in re.finditer(r"(?m)^[\[<]?§\s*(\d+[a-z]*(?:\s+\d+[a-z]*)?)\s*\.", txt[s:e]):
                klucze.add("§" + _klucz(m.group(1)))
    return klucze


def _tk_w_przypisach(fragment):
    """Przypisy t.j. o wyrokach TK wydanych PRZED jego stanem prawnym — t.j. oddaje je przypisem, a jednostkę oznacza
    „(utracił moc)" (ustawa o planowaniu rodziny DU 2022/1575, art. 4a ust. 1 pkt 2: „[przypis 12)] Z dniem
    27 stycznia 2021 r. na podstawie wyroku Trybunału Konstytucyjnego … K 1/20"; k.c. DU 2026/795: wyroki zakresowe
    „Utracił moc … w zakresie, w którym…"). Dotąd przypis stał w tekście bez ostrzeżenia — łatwo go przeoczyć.

    Przypis bez odsyłacza (`_przypis_bez_odsylacza`) o wyroku TK nie jest „przypisem tego fragmentu" — stoi tylko w jego
    sąsiedztwie (k.p.c. DU 2026/468: przypis 3 o K 10/17 należy do art. 1, a stał w art. 2). Wychodzi osobno, z zastrzeżeniem,
    że nie wiadomo, którego przepisu dotyczy — informacja o wyroku nie ginie, ale nie jest podana jako pewna."""
    out, obce = [], []
    for l in fragment.split("\n"):
        if l.startswith("[przypis") and "Trybunału Konstytucyjnego" in l and re.search(r"(?i)utraci\w*\s+moc|na\s+podstawie\s+wyroku|"
                                                          r"obwieszczenia\s+Prezesa|w\s+zakresie", l):
            l = " ".join(l.split())
            (obce if _PRZYPIS_BEZ_ODSYLACZA_LINIA.match(l) else out).append(l if len(l) <= 320 else l[:317] + "…")
    uwagi = []
    if obce:
        uwagi = (["UWAGA — WYROK TK W PRZYPISIE NIEPRZYPISANYM DO JEDNOSTKI (odsyłacza w treści nie ustalono): NIE wiadomo, "
                  "czy dotyczy przepisu z tego fragmentu, czy sąsiedniego — sprawdź w PDF (--pdf plik.pdf), zanim go "
                  "zacytujesz albo uznasz za obowiązujący:"] + ["  " + l for l in dict.fromkeys(obce)])
    if not out:
        # k.c. DU 2026/795 z PDF: „Art. 179. (utracił moc)" bez przypisu — odsyłacz zgubił się przy ekstrakcji
        # (treść przypisu o wyroku K 9/04 trafiła do art. 182; sprawdzone 2026-11-13)
        if re.search(r"\(utraci\w*\s+moc\)", fragment):
            return ["UWAGA: w tym fragmencie jest jednostka oznaczona „(utracił moc)” — NIE obowiązuje (zwykle skutek "
                    "wyroku TK); przypisu z podstawą nie ma we fragmencie — sprawdź go w PDF (--pdf plik.pdf)."] + uwagi
        return uwagi
    return (["UWAGA — WYROK TK W PRZYPISIE TEGO FRAGMENTU: część przepisu utraciła moc albo obowiązuje w ograniczonym "
             "zakresie (tekst jednolity oddaje to przypisem, a jednostkę bywa oznaczona „(utracił moc)”) — nie cytuj tej "
             "części jako obowiązującej:"] + ["  " + l for l in dict.fromkeys(out)] + uwagi)


def _sprawdz_stan_obwieszczenia(txt, meta, wynik):
    """„Stan prawny na" z API (legalStatusDate) a data z obwieszczenia t.j. — przy rozbieżności ostrzeżenie, a gdy
    obwieszczenie podaje WCZEŚNIEJSZĄ datę, lista zmian dostaje pozycje z dni pomiędzy (k.p.c. DU 2026/468: API
    2026-03-25, obwieszczenie „stan prawny na dzień 24 marca 2026 r." — sprawdzone 2026-11-13)."""
    rodzaj, d_obw = _stan_z_obwieszczenia(txt)
    lsd = (meta or {}).get("legalStatusDate") or "" if isinstance(meta, dict) else ""
    out = []
    if not d_obw:
        return out
    formula = "stan prawny na dzień" if rodzaj == "stan" else "zmiany z przepisów ogłoszonych przed dniem"
    if lsd and d_obw != lsd:
        out.append(f"UWAGA: obwieszczenie tego t.j. podaje „{formula} {d_obw}”, a API (legalStatusDate) — {lsd}; "
                   "wiążące jest obwieszczenie.")
    stan = wynik.get("stan") or ""
    base = wynik.get("baza") or {}
    if stan and d_obw < stan and base.get("ELI"):
        try:
            dodatkowe = [r for r in _zmiany_bazowe_po(base["ELI"], d_obw)
                         if r["act"].get("ELI") not in {p["act"].get("ELI") for p in wynik.get("pozycje") or []}]
        except VerificationUnknown as e:
            out.append(f"UWAGA: nie udało się sprawdzić zmian z dni {d_obw}…{stan} ({e}).")
            return out
        if dodatkowe:
            out.append(f"UWAGA: zmiany ogłoszone lub wchodzące w życie od {d_obw} (data z obwieszczenia), których lista "
                       f"liczona od {stan} nie objęła ({len(dodatkowe)}):")
            for r in dodatkowe:
                data_aktu, ogloszono = _daty_aktu(r["act"])
                out.append(_fmt_ref(r, "Akty zmieniające"))
                wynik.setdefault("pozycje", []).append({"act": r["act"], "data_aktu": data_aktu, "ogloszono": ogloszono,
                                                        "wejscie": r.get("date") or "", "etykieta": "wejście w życie zmiany"})
        wynik["stan"] = d_obw
    return out


def act_path(sig_parts):
    """Zwraca ścieżkę bazową aktu, akceptując różne formy sygnatury."""
    s = " ".join(sig_parts).strip()
    compact = re.sub(r"\s+", "", s)
    # ISAP address, np. WDU20240000018 (W + DU/MP + 9–12 cyfr)
    m = re.match(r"^(W?)(DU|MP)(\d{11})$", compact, re.I)
    if m:
        addr = "W" + m.group(2).upper() + m.group(3)
        return f"/acts/{addr}", s
    pub = "DU"
    mp = re.search(r"\b(DU|MP)\b", s, re.I)
    if mp:
        pub = mp.group(1).upper()
    elif re.search(r"\bM\.?\s*P\.?\b", s):
        pub = "MP"
    nums = re.findall(r"\d+", s)
    year = next((n for n in nums if len(n) == 4 and n[:2] in ("19", "20")), None)
    poz = re.search(r"poz\.?\s*(\d+)", s, re.I)
    if poz:
        pos = poz.group(1)
    else:
        rest = [n for n in nums if n != year]
        pos = rest[-1] if rest else None
    if year and pos:
        return f"/acts/{pub}/{year}/{int(pos)}", f"{pub} {year} poz. {int(pos)}"
    sys.exit(f"Nie rozpoznano sygnatury: {s!r}. Przykłady: 'DU 2024 18', 'Dz.U. 2024 poz. 18', 'WDU20240000018'.")


def _sprawdz_haslo(haslo):
    """Hasło spoza słownika /keywords daje w API po prostu 0 trafień — to zły argument, nie „brak aktów".
    API porównuje hasła bez względu na wielkość liter, ale z polskimi znakami (2026-11-13: „Sygnaliści" → 2,
    „sygnalisci" → 0). Kończy programem (kod 2) z podpowiedzią, gdy hasła nie ma albo słownika nie da się
    pobrać; zwraca, gdy hasło jest w słowniku."""
    import difflib
    try:
        slownik = _get("/keywords", soft=True)
    except VerificationUnknown as e:
        sys.exit(f"BŁĄD: 0 trafień dla hasła {haslo!r}, a słownika haseł API nie udało się pobrać ({e}) — nie wiem, "
                 "czy hasło istnieje. Spróbuj ponownie albo szukaj frazą tytułu.")
    if not isinstance(slownik, list):
        sys.exit(f"BŁĄD: 0 trafień dla hasła {haslo!r}, a słownik haseł API ma nieoczekiwany format.")
    hasla = [str(h) for h in slownik]
    if haslo.strip().lower() in {h.lower() for h in hasla}:
        return
    podobne = difflib.get_close_matches(haslo.strip().lower(), [h.lower() for h in hasla], n=5, cutoff=0.6)
    sys.exit(f"BŁĄD: hasła {haslo!r} nie ma w słowniku haseł API ELI ({len(hasla)} haseł) — dlatego 0 trafień; to NIE "
             "jest wynik wyszukiwania. " + (f"Podobne hasła: {', '.join(podobne)}. " if podobne else "")
             + "Pełna lista: https://api.sejm.gov.pl/eli/keywords; albo szukaj frazą tytułu.")


def _norm_tytul(t):
    return re.sub(r"[\s\-–—.]+", " ", (t or "").lower()).strip()


def _akty_bazowe_dalej(a, total, items):
    """Akty, których tytuł (bez „Ustawa z dnia … r.") JEST frazą, a które nie zmieściły się na tej stronie.

    API sortuje nowelizacje przed aktem bazowym: `szukaj "Kodeks karny" --typ Ustawa` (2026-11-13) — 101 trafień,
    k.k. (DU/1997/553) dopiero na pozycji 92, więc na domyślnej stronie (10) go nie ma. Jedno dodatkowe zapytanie
    (do 500 pozycji); awaria → [] (to tylko podpowiedź, lista wyników jest pełna co do liczby)."""
    if not a.fraza or a.offset or total > 500:
        return []
    fraza = _norm_tytul(a.fraza)
    params = {"title": a.fraza, "limit": total, "offset": 0, "type": a.typ, "year": a.rok,
              "publisher": a.wyd, "keyword": a.haslo}
    if a.obowiazujace:
        params["inForce"] = 1
    try:
        wszystkie = _get("/acts/search", params, soft=True)
    except VerificationUnknown:
        return []
    if not isinstance(wszystkie, dict):
        return []
    na_stronie = {it.get("ELI") for it in items}
    out = []
    for n, it in enumerate(wszystkie.get("items") or []):
        rdzen = re.sub(r"(?i)^\s*\w+\s+z\s+dnia\s+\d{1,2}\s+\w+\s+\d{4}\s*r\.?\s*", "", it.get("title") or "")
        if _norm_tytul(rdzen) == fraza and it.get("ELI") not in na_stronie:
            out.append((n, it))
    return out


def cmd_szukaj(a):
    # sam filtr (np. --wyd MP --rok 2026) API przyjmuje — odmowa była zbędna (audyt 2026-08, „eli × zero-results")
    if not (a.fraza or a.haslo or a.typ or a.rok or a.wyd):
        sys.exit("Podaj frazę tytułu (np. szukaj \"Kodeks cywilny\"), --haslo albo filtr (--typ, --rok, --wyd).")
    params = {"title": a.fraza, "limit": a.limit, "offset": a.offset, "type": a.typ,
              "year": a.rok, "publisher": a.wyd, "keyword": a.haslo}
    if a.obowiazujace:
        params["inForce"] = 1
    d = _get("/acts/search", params)
    d = _expect_dict(d, "wyniki wyszukiwania")
    items = d.get("items") or []
    total = d.get("totalCount")
    if total is None:
        total = d.get("count", len(items))
    if not items:
        try:
            wszystkich = int(total)
        except (TypeError, ValueError):
            wszystkich = None
        if wszystkich and wszystkich > 0:
            # offset za końcem listy — to NIE jest zero trafień (kod 2, nie 1)
            sys.exit(f"BŁĄD: offset {a.offset} jest za końcem listy — zapytanie ma {wszystkich} trafień. "
                     f"Użyj --offset mniejszego niż {wszystkich}.")
        if a.haslo:
            _sprawdz_haslo(a.haslo)
        # zero trafień = komunikat + kod wyjścia 1 (także z --json) — pusty JSON wyglądałby jak
        # „sprawdzone, nic nie ma", a to tylko brak dopasowania TEJ frazy/filtrów
        raise BrakWynikow(f"Brak wyników (totalCount={total}, offset {a.offset}) dla: fraza={a.fraza!r}, typ={a.typ!r}, "
                          f"rok={a.rok!r}, wyd={a.wyd!r}, haslo={a.haslo!r}. To NIE dowodzi, że aktu nie ma — spróbuj "
                          "krótszej frazy z tytułu, bez --typ/--rok, albo --haslo.")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Znaleziono: {total} (pokazuję {len(items)}, offset {a.offset})")
    try:
        pozostalo = int(total) - (a.offset + len(items))
    except (TypeError, ValueError):
        pozostalo = 0
    if pozostalo > 0:
        print(f"UWAGA: to NIE wszystkie wyniki — pozostało {pozostalo}. Kolejna strona: "
              f"--offset {a.offset + len(items)} --limit {a.limit}; albo zwiększ --limit (np. 100). "
              "Akt bazowy bywa na końcu listy (API sortuje nowelizacje przed aktem bazowym).")
        for n, it in _akty_bazowe_dalej(a, int(total), items):
            print(f"  → akt o tytule równym frazie jest dalej na liście (pozycja {n + 1} z {total}): "
                  f"{it.get('address', '')}  [{it.get('status', '')}]  {(it.get('title') or '').strip()[:120]}"
                  + (f"  ELI: {it['ELI']}" if it.get("ELI") else ""))
    print()
    for it in items:
        print(f"  {it.get('address','')}  [{it.get('status','')}]")
        print(f"    {it.get('title','').strip()[:160]}")
        if it.get("ELI"):
            print(f"    ELI: {it['ELI']}")
        print()


def cmd_meta(a):
    path, label = act_path(a.sygnatura)
    d = _get(path)
    d = _expect_dict(d, "metadane aktu")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Akt: {label}")
    print(f"  Tytuł:   {d.get('title','').strip()}")
    print(f"  Adres:   {d.get('displayAddress','')}")
    if _cytat(d):
        print(f"  Cytat:   {_cytat(d)}   (urzędowa forma w piśmie)")
    print(f"  Typ:     {d.get('type','')}")
    print(f"  Status:  {d.get('status','')}  (inForce={d.get('inForce','')})")
    try:
        refs = _get(path + "/references", soft=True)
        refs = refs if isinstance(refs, dict) else {}
    except VerificationUnknown:
        refs = None
    if "uchylon" in (d.get("status") or "").lower():
        uchylajace = _lista(refs, "akty uchylające") if refs is not None else None
        if uchylajace:
            for r in uchylajace:
                print(f"  Uchylony przez: {_akt_opis(r['act'])}  {r['act'].get('title', '').strip()}"
                      + (f"  (data wg API {r['date']})" if r.get("date") else ""))
        elif uchylajace is None:
            print(f"  Uchylony przez: nie udało się sprawdzić — odniesienia {label}")
        else:
            print("  Uchylony przez: API nie wskazuje aktu uchylającego (sprawdź „odniesienia” — np. utrata mocy z mocy ustawy)")
    # announcementDate = data AKTU (wydania/podpisania — „z dnia" w tytule), promulgation = data
    # OGŁOSZENIA w Dz.U./M.P. — vacatio legis liczy się od ogłoszenia (audyt 2026-08: mylono je)
    print(f"  Data aktu: {d.get('announcementDate') or '—'}   (data wydania — „z dnia” w tytule)")
    print(f"  Ogłoszono: {d.get('promulgation') or '— (brak w API)'}   (publikacja w Dz.U./M.P.)")
    print(f"  WEJŚCIE W ŻYCIE: {d.get('entryIntoForce') or '—'}")
    for w in _jeszcze_nie_obowiazuje(d):
        print(f"  {w}")
    if d.get("legalStatusDate"):
        # obwieszczenie bywa „z uwzględnieniem stanu prawnego na dzień X" albo „zmian … ogłoszonych przed dniem X";
        # w 1 z 9 sprawdzonych t.j. API podaje dzień później niż obwieszczenie (k.p.c. DU 2026/468) — 2026-11-13
        print(f"  Stan prawny na: {d['legalStatusDate']}   (pole legalStatusDate; wiąże data z obwieszczenia t.j. — "
              f"`tekst {label}` porównuje je, gdy czyta t.j. z obwieszczeniem, a nie tekst ujednolicony)")
    if d.get("validFrom") and d.get("validFrom") != d.get("entryIntoForce"):
        print(f"  Obowiązuje od: {d['validFrom']}")
    if d.get("comments"):
        # np. „art. 5 ust. 4 … wchodzą w życie z dniem 25 grudnia 2024 r." — RÓŻNE daty dla jednostek
        print(f"  Uwagi:   {' '.join(str(d['comments']).split())}")
    if d.get("keywordsNames"):
        print(f"  Hasła:   {', '.join(d['keywordsNames'])}")
    print(f"  ELI:     {d.get('ELI','')}")
    texts = d.get("texts", [])
    if texts:
        print("  Dostępne teksty (type/fileName):")
        for t in texts:
            print(f"    - {t.get('type','?')}: {t.get('fileName','')}")
    if d.get("textHTML") is False:
        print(f"  Tekst HTML: BRAK w API (textHTML=false) — `tekst {label}` czyta urzędowy PDF przez pdftotext")
    else:
        print(f"  Tekst HTML: {BASE}{path}/text.html")
    if "tekst jednolity" in (d.get("status") or "").lower():
        print(f"  → Akt ma tekst jednolity — ustal aktualny: python3 {sys.argv[0]} tj {label}")
    # wyroki TK (kategoria „Orzeczenie TK" aktu bazowego — t.j. sam jej nie ma)
    if refs is None:
        print(f"  Orzeczenia TK: nie udało się sprawdzić — odniesienia {label}")
    else:
        base = _akt_bazowy(refs)
        try:
            base_refs = _get(f"/acts/{base['ELI']}/references", soft=True) if base and base.get("ELI") else refs
        except VerificationUnknown:
            base_refs = None
            print(f"  Orzeczenia TK: nie udało się sprawdzić — odniesienia {_akt_opis(base)}")
        for l in _linie_tk_meta(base_refs):
            print(f"  {l}")


# ---------------------------------------------------------------------------------------------
# Opcjonalny OCR stron bez warstwy tekstowej (skany) — tesseract + pdftoppm, jeśli są w PATH
# ---------------------------------------------------------------------------------------------
_OCR_AUTO_MAKS = 10        # tyle stron-skanów odczytuje się samo; więcej (ok. 7 s/stronę) — tylko z --ocr
_OCR_SEK_NA_STRONE = 7
_OCR_DPI = 300


def ocr_dostepny():
    """tesseract i pdftoppm w PATH oraz polski model językowy („pol")."""
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        return False
    try:
        r = subprocess.run(["tesseract", "--list-langs"], capture_output=True, timeout=20)
    except Exception:
        return False
    return "pol" in r.stdout.decode("utf-8", "replace").split()


def _ocr_katalog():
    baza = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(baza, "prawo-pl-eli", "ocr")


# Poprawki tekstu z OCR tesseracta. Pamięć podręczna trzyma SUROWY wynik tesseracta, a poprawki idą po odczycie
# z niej (`_ocr_strona`) i po złożeniu stron (`_ocr_popraw_paragrafy`) — muszą więc być idempotentne. Dzięki temu
# nowa albo poprawiona reguła działa też na stronach odczytanych wcześniej, bez zmiany klucza pamięci.
#
# tesseract czyta „§" jako „$" (aktów prawnych nie pisze się w dolarach): „$ 2", „$2." → „§ 2", „§2."
_OCR_DOLAR = re.compile(r"\$(?=\s?\d)")
# …i jako „8" w odesłaniu w środku zdania. Zamieniamy tylko w kontekście, w którym „8 N" nie ma innego sensu:
# - po „w"/„z"/„zastrzeżeniem" (też przez koniec wiersza), a przed „ust."/„pkt", zakresem „N—M" albo znakiem
#   kończącym odesłanie (po „do" — tylko przed „ust."/„pkt"/zakresem: „do 8 30," to godzina) (DU 1998 poz. 875: „o których mowa w 8 1 ust. 2", „stosując zasady określone w 8 7—13",
#   „określone w\n8 11—14."; DU 1998 poz. 767: „ustalonego zgodnie z 8 16."; DU 2000 poz. 313: „określonych w 8 14,
#   a ponadto"; DU 1996 poz. 457: „z zastrzeżeniem\n8 14, wszystkie");
# - za numerem artykułu, przed nazwą aktu (DU 2000 poz. 313: „art. 23715 8 1 Kodeksu pracy" — indeks górny „237¹⁵"
#   zgubiony przez OCR).
# Numer ma 1–2 cyfry (bez zera na początku), a zakres — półpauzę albo pauzę, nie dywiz: „z 8 500 zł" (kwota ze
# spacją tysięcy), „w 8 egzemplarzach", „w 8-10 dni", „z dnia 8 1 marca", „w 8 12. 1998", „w 8 14,5 m",
# „od 7 00 do 8 00." zostają bez zmian.
_OCR_8_ODESLANIE = re.compile(r"(?<![\w§])((?:w|z|zastrzeżeniem)[ \t]*\n?[ \t]*)8[ ]([1-9]\d?)"
                              r"(?=[ ](?:ust\.|pkt\b)|[ ]?[—–][ ]?\d|,(?!\d)|[;:]|(\.)(?:\s+[^\d\s]|\s*$))"
                              r"|(?<![\w§])(do[ \t]*\n?[ \t]*)8[ ]([1-9]\d?)(?=[ ](?:ust\.|pkt\b)|[ ]?[—–][ ]?\d)")
_OCR_8_W_ARTYKULE = re.compile(r"(?<![\w§])(art\.[ ]?\d{1,6}[a-z]*[ ])8[ ](\d{1,2})(?=[ ][A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż])")


def _ocr_popraw_strone(tekst):
    """Poprawki jednej strony tekstu z OCR tesseracta (idempotentne): „$"→„§" i jednoznaczne odesłania „w 8 N ust."."""
    tekst = _OCR_DOLAR.sub("§", tekst)
    # „z\n8 16." na początku wiersza wyglądałoby po zamianie jak nagłówek „§ 16." — taki wiersz zostaje
    tekst = _OCR_8_ODESLANIE.sub(lambda m: m.group(0) if m.group(3) and "\n" in m.group(1)
                                 else (m.group(1) or m.group(4)) + "§ " + (m.group(2) or m.group(5)), tekst)
    return _OCR_8_W_ARTYKULE.sub(r"\1§ \2", tekst)


# Nagłówek paragrafu odczytany jako „8": „8 11. Zebranie dokonuje…" (rozstrzelony) albo „85. 1. Pracownikowi…"
# (sklejony; DU 1992 poz. 316: „81.", „82. 1." … „84. 1." to § 1–4). Za numerem — wielka litera, ewentualnie po
# numerze ustępu („8 4. 1. Ustala się:"). Bez litery w numerze i bez numeru 0 („80.").
_OCR_8_PARAGRAF = re.compile(r"(?m)^([ \t]*)8([ ]?)([1-9]\d{0,2})\.(?=[ \t]+(?:\d{1,2}\.[ \t]+)?[A-ZĄĆĘŁŃÓŚŹŻ])")
# Warianty, których `_OCR_8_PARAGRAF` nie bierze (13.11.2026, raw OCR skanów): śmieć przed numerem („„8 12, 1. Głosowanie"
# — DU 1998 poz. 875; „. 817. 1. Urządzenie" — DU 1993 poz. 58), przecinek zamiast kropki („„8 12,"), inna cyfra zamiast
# „§" („5 2, Ilekroć" i „3 16. Dopuszczalne" — DU 2000 poz. 313). Zamiana TYLKO wtedy, gdy numer N potwierdzają oba
# sąsiednie pewne nagłówki: tuż przed nim § N−1 (albo „co następuje" dla N = 1), tuż za nim § N+1. Sklejony („817.") —
# z „8", a z „3"/„5" tylko przy numerze dwucyfrowym („586. 1. Kwas siarkowy" między § 85 a § 87 — DU 1994 poz. 73 s. 7, skan
# odczytany przez tesseract, obraz strony: „§ 86."; dotąd § 86 znikał z numeracji); „317." przy § 2 i § 4 nie jest § 17.
_OCR_8_WARIANT = re.compile(r"(?m)^([ \t]*)([„\".,'][ \t]?)?(?:([358])[ ]|(8)|([35])(?=[1-9]\d[.,]))([1-9]\d{0,2})([.,])"
                            r"(?=[ \t]+(?:\d{1,2}\.[ \t]+)?[A-ZĄĆĘŁŃÓŚŹŻ])")
# świadectwa numeracji: nagłówek paragrafu „§ N." na początku wiersza, początek aktu („zarządza się, co następuje:"
# — przed § 1), nagłówek artykułu (rozdziela ciągi; nie świadczy o numerze paragrafu)
_OCR_SWIADEK_PARAGRAF = re.compile(r"(?m)^[ \t]*§[ \t]?(\d{1,3})[a-z]?\.")
_OCR_SWIADEK_POCZATEK = re.compile(r"co\s+nastę-?\s*puje")
_OCR_SWIADEK_ART = re.compile(r"(?m)^[ \t]*Art\.[ \t]?\d")


def _ocr_popraw_paragrafy(strony, ocr_idx, luz=0):
    """Strony tekstu PDF (lista, kolejność dokumentu) → te same strony, na stronach z OCR tesseracta (`ocr_idx`)
    z nagłówkami paragrafów „8 N." / „8N." poprawionymi na „§". Idempotentne (powtarzane do stałego wyniku).

    Sam wzorzec nie wystarcza: „8 1." bywa wierszem tabeli, a „81."–„89." — punktem wyliczenia. Rozstrzyga ciąg
    numeracji paragrafów w całym dokumencie (strony z warstwą tekstową czytane łam po łamie, `_pdf_lamy(luz)`):
    - rozstrzelony „8 N." — gdy poprzedni albo następny nagłówek („§ M." albo inny rozstrzelony kandydat) ma numer
      N−1 albo N+1 (DU 1998 poz. 875: 20 z 21 paragrafów to „8 N.");
    - sklejony „8N." — tylko jako ciąg kolejnych numerów a…b, który zamykają z OBU stron nagłówki: tuż przed nim
      § a−1 (dla a = 1 — „co następuje"), tuż za nim § b+1, przy czym co najmniej jeden z nich pewny (DU 1992
      poz. 316: „co następuje:", 81.–84., „§ 5." na następnej stronie; DU 1996 poz. 457: „8 4.", „85. 1.", „8 6.").
      Punkty 81.–89. wyliczenia w § k nie mają takiej ramy: przed nimi § k, za nimi § k+1. Sklejony kandydat nie
      świadczy o niczym, dopóki nie zostanie przyjęty."""
    ocr_idx = {i for i in ocr_idx if i < len(strony)}
    if not any(_OCR_8_PARAGRAF.search(strony[i]) or _OCR_8_WARIANT.search(strony[i]) for i in ocr_idx):
        return strony
    # świadkowie ze stron z warstwą tekstową nie zmieniają się między przebiegami — liczeni raz
    stali = [z for i, s in enumerate(strony) if i not in ocr_idx for z in _ocr_swiadkowie(i, _pdf_lamy(s, luz=luz))]
    # każdy przebieg tylko zamienia „8" na „§", więc ciąg przebiegów się kończy
    while True:
        nowe = _ocr_popraw_paragrafy_raz(strony, ocr_idx, stali)
        if nowe == strony:
            return list(strony)
        strony = nowe


def _ocr_swiadkowie(i, tekst):
    """Znaczniki numeracji strony `i`: (strona, pozycja, rodzaj, numer, dopasowanie); pozycja tylko do kolejności."""
    return ([(i, m.start(), "paragraf", int(m.group(1)), None) for m in _OCR_SWIADEK_PARAGRAF.finditer(tekst)]
            + [(i, m.start(), "poczatek", 0, None) for m in _OCR_SWIADEK_POCZATEK.finditer(tekst)]
            + [(i, m.start(), "art", None, None) for m in _OCR_SWIADEK_ART.finditer(tekst)])


def _ocr_popraw_paragrafy_raz(strony, ocr_idx, stali):
    znaczniki = list(stali)
    for i in ocr_idx:
        s = strony[i]
        zwykle = [(i, m.start(), "rozstrzelony" if m.group(2) else "sklejony", int(m.group(3)), m)
                  for m in _OCR_8_PARAGRAF.finditer(s)]
        znaczniki += zwykle
        poczatki = {z[1] for z in zwykle}
        znaczniki += [(i, m.start(), "wariant", int(m.group(6)), m) for m in _OCR_8_WARIANT.finditer(s)
                      if m.start() not in poczatki and (m.group(2) or m.group(3) != "8" or m.group(7) == ",")]
        znaczniki += _ocr_swiadkowie(i, s)
    znaczniki.sort(key=lambda z: (z[0], z[1]))
    przyjete = set()
    # rozstrzelone: świadkami są pewne nagłówki i pozostali rozstrzeleni kandydaci
    sw = [k for k, z in enumerate(znaczniki) if z[2] in ("paragraf", "poczatek", "rozstrzelony")]
    for j, k in enumerate(sw):
        z = znaczniki[k]
        if z[2] != "rozstrzelony":
            continue
        przed = znaczniki[sw[j - 1]] if j > 0 else None
        za = znaczniki[sw[j + 1]] if j + 1 < len(sw) else None
        # pewny nagłówek z TYM SAMYM numerem tuż obok — nie ma tu miejsca na paragraf („§ 3.", „8 3. Pszenica 15,00"
        # w tabeli, „§ 4." — wiersz tabeli, nie drugi § 3)
        if any(n and n[2] == "paragraf" and n[3] == z[3] for n in (przed, za)):
            continue
        if przed and przed[3] == z[3] - 1 or za and za[2] != "poczatek" and za[3] == z[3] + 1:
            przyjete.add(k)
    # sklejone: ciąg kolejnych numerów, tuż przed nim nagłówek a−1, tuż za nim nagłówek b+1. Jedną z tych ram może
    # być odrzucony dotąd rozstrzelony kandydat — wtedy przyjmujemy też jego (DU 1996 poz. 457: ostatni z ciągu
    # „§ 4.", „85.", „8 6." nie miał sąsiada 5 ani 7 wśród świadków), ale druga rama musi być pewna.
    def pewny(k):
        return znaczniki[k][2] in ("paragraf", "poczatek") or k in przyjete

    def rama(k, numer):
        """0 — nie pasuje, 1 — pasuje rozstrzelony kandydat, 2 — pasuje pewny nagłówek"""
        if not 0 <= k < len(znaczniki) or znaczniki[k][3] != numer:
            return 0
        return 2 if pewny(k) else 1 if znaczniki[k][2] == "rozstrzelony" else 0
    k = 0
    while k < len(znaczniki):
        if znaczniki[k][2] != "sklejony":
            k += 1
            continue
        koniec = k
        while koniec + 1 < len(znaczniki) and znaczniki[koniec + 1][2] == "sklejony" \
                and znaczniki[koniec + 1][3] == znaczniki[koniec][3] + 1:
            koniec += 1
        a, b = znaczniki[k][3], znaczniki[koniec][3]
        przed = rama(k - 1, a - 1)
        za = rama(koniec + 1, b + 1) if koniec + 1 < len(znaczniki) and znaczniki[koniec + 1][2] != "poczatek" else 0
        if przed and za and przed + za >= 3:
            przyjete.update(range(k - (przed == 1), koniec + 1 + (za == 1)))
        k = koniec + 1
    # warianty: oba sąsiednie znaczniki (bez kandydatów, których jeszcze nie przyjęto) to pewne nagłówki N−1 i N+1
    warianty = set()
    for k, z in enumerate(znaczniki):
        if z[2] != "wariant":
            continue
        przed = next((znaczniki[j] for j in range(k - 1, -1, -1) if pewny(j) or znaczniki[j][2] == "art"), None)
        za = next((znaczniki[j] for j in range(k + 1, len(znaczniki)) if pewny(j) or znaczniki[j][2] == "art"), None)
        if przed and za and przed[2] != "art" and za[2] == "paragraf" and przed[3] == z[3] - 1 and za[3] == z[3] + 1:
            warianty.add(k)
    if not przyjete and not warianty:
        return strony
    wynik = list(strony)
    for k in przyjete:
        i, _, _, _, m = znaczniki[k]
        p = m.start() + len(m.group(1))         # „8" za wcięciem → „§" (znak za znak — pozycje innych bez zmian)
        wynik[i] = wynik[i][:p] + "§" + wynik[i][p + 1:]
    # wariant zmienia długość wiersza — od końca strony, żeby pozycje wcześniejszych dopasowań zostały ważne
    for k in sorted(warianty, key=lambda k: (znaczniki[k][0], znaczniki[k][1]), reverse=True):
        i, _, _, n, m = znaczniki[k]
        wynik[i] = wynik[i][:m.start()] + m.group(1) + f"§ {n}." + wynik[i][m.end():]
    return wynik


def _ocr_strona(pdf_path, nr, klucz):
    """OCR jednej strony (nr od 1) → tekst; surowy wynik tesseracta zapamiętany na dysku (ten sam PDF = ten sam
    wynik), poprawki `_ocr_popraw_strone` — po odczycie, także z pamięci."""
    plik = os.path.join(_ocr_katalog(), f"{klucz}-{nr}-{_OCR_DPI}.txt")
    try:
        with open(plik, encoding="utf-8") as f:
            return _ocr_popraw_strone(f.read())
    except OSError:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        obraz = os.path.join(tmp, "s")
        subprocess.run(["pdftoppm", "-f", str(nr), "-l", str(nr), "-r", str(_OCR_DPI), "-gray", "-png",
                        "-singlefile", pdf_path, obraz], capture_output=True, timeout=180, check=True)
        r = subprocess.run(["tesseract", obraz + ".png", "-", "-l", "pol", "--psm", "1"],
                           capture_output=True, timeout=300, check=True)
    tekst = r.stdout.decode("utf-8", "replace")
    try:
        os.makedirs(_ocr_katalog(), exist_ok=True)
        with open(plik, "w", encoding="utf-8") as f:
            f.write(tekst)
    except OSError:
        pass        # brak zapisu (piaskownica) nie przeszkadza — następnym razem OCR od nowa
    return _ocr_popraw_strone(tekst)


def pdf_ocr_stron(pdf_bytes, numery):
    """OCR stron o indeksach `numery` (od 0) → {indeks: tekst}; równolegle, z pamięcią na dysku.
    Strona, której OCR się nie udał, zostaje pominięta (brak klucza)."""
    klucz = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        sciezka = f.name

    def jedna(i):
        try:
            return i, _ocr_strona(sciezka, i + 1, klucz)
        except Exception:
            return i, None
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as ex:
            return {i: t for i, t in ex.map(jedna, numery) if t}
    finally:
        os.unlink(sciezka)


def _tekst_z_pdf(path, label, meta, info=None, ocr=None):
    """Tekst aktu z jego WŁASNEGO urzędowego PDF (pdftotext -layout). Zwraca (tekst, url, błąd).

    `info` (dict) dostaje „typ" wybranego PDF, a dla tekstu ujednoliconego także „podstawa" (notka
    „Opracowano na podstawie…") i „data_wydruku". Strony bez warstwy tekstowej (skany) są odczytywane
    przez OCR (tesseract), gdy jest dostępny: same, jeśli jest ich najwyżej `_OCR_AUTO_MAKS`, a wszystkie
    — z `ocr=True` (--ocr); `info` dostaje wtedy „ocr_strony" (odczytane, wszystkie) albo
    „ocr_pominiete" (liczba stron, szacowany czas w s)."""
    info = {} if info is None else info
    pick = _wybierz_pdf(meta)
    if not pick:
        return "", "", "brak PDF w metadanych aktu"
    if not pdftotext_dostepny():
        return "", "", "brak programu pdftotext (poppler) w PATH"
    url = f"{BASE}{path}/text/{pick['type']}/{pick['fileName']}"
    try:
        data = _get_bytes(url, soft=True)
    except VerificationUnknown as e:
        return "", url, str(e)
    rok = meta.get("year")
    zeszyt = pick["type"] == "O" and meta.get("publisher") in ("DU", "MP") and isinstance(rok, int) and 1990 <= rok <= 2011
    raw, notki, info_pdf = pdf_do_tekstu_z_notkami(data, zeszyt=zeszyt, indeksy=not (zeszyt and rok < 2000))
    strony_flagi = info_pdf.pop("strony_flagi", None)
    info.update(info_pdf)
    info["typ"] = pick["type"]
    # strony bez warstwy tekstowej (skany: DU 2010 poz. 1 ma 564 z 566) — ich treści w wyniku nie będzie
    strony = [p for p in raw.split("\f")][:-1] if raw.endswith("\f") else raw.split("\f")
    # nagłówek strony nie jest treścią — także zniekształcony przez OCR skanu z lat 90. („Dzienn ik Ustaw Nr 98 ~ 3089 ~ Poz. 602")
    puste_idx = [i for i, p in enumerate(strony) if len(re.sub(r"[\W\d_]", "", "\n".join(
        l for l in p.split("\n") if not _PDF_NAGLOWEK.match(l)
        and not _PDF_NAGLOWEK_OCR.match(re.sub(r"\s", "", l))))) < 20]
    if raw and puste_idx and ocr is not False and ocr_dostepny():
        if ocr or len(puste_idx) <= _OCR_AUTO_MAKS:
            odczytane = pdf_ocr_stron(data, puste_idx)
            if odczytane:
                kawalki = raw.split("\f")
                reklamy = 0
                for i, t in odczytane.items():
                    if _pdf_strona_reklamowa(t):
                        # skan ogłoszenia wydawcy na końcu zeszytu (DU 2010 poz. 20, 1128) — nie jest treścią aktu
                        kawalki[i], reklamy = "", reklamy + 1
                    else:
                        kawalki[i] = t.replace("\f", "")
                # „8 11." → „§ 11." na stronach z OCR, gdy potwierdza to ciąg numeracji paragrafów całego PDF
                kawalki = _ocr_popraw_paragrafy(kawalki, [i for i in odczytane if kawalki[i]],
                                                luz=_PDF_LUZ_OCR if zeszyt and rok < 2000 else 0)
                raw = "\f".join(kawalki)
                if len(odczytane) > reklamy:
                    info["ocr_strony"] = (len(odczytane) - reklamy, len(strony))
                if reklamy:
                    info["reklama_strony"] = reklamy
                puste_idx = [i for i in puste_idx if i not in odczytane]
        else:
            info["ocr_pominiete"] = (len(puste_idx), len(puste_idx) * _OCR_SEK_NA_STRONE)
    puste = len(puste_idx)
    if raw and puste:
        info["puste_strony"] = (puste, len(strony))
    start = []
    if raw and zeszyt:
        raw = pdf_zeszyt_do_aktu(raw, rok, meta.get("pos"), start=start)
        if rok < 2000:
            info["ocr"] = True      # warstwa tekstowa zeszytów z lat 90. to OCR skanu
    # strony zeszytu po wycięciu aktu liczone od strony PDF, na której akt się zaczyna (dotąd numer strony był pomijany jako mylący);
    # luki w numeracji jednostek — w tekście zeszytu i w PDF odczytanym przez OCR co najmniej w połowie (jedna strona-skan w tekście
    # ujednoliconym VAT nie czyni z niego tekstu z OCR, a uchylone „Art. 139–144." dawały fałszywą lukę)
    txt = pdf_layout_do_tekstu(raw, info, notki, strony_flagi=strony_flagi, strona0=start[0] if start else 0,
                               numeracja=zeszyt or bool(info.get("ocr_strony"))
                               and 2 * info["ocr_strony"][0] >= info["ocr_strony"][1]) if raw else ""
    if zeszyt and not start and info.get("niepewne_miejsca"):
        # numeru pozycji nie znaleziono — strony wyniku nie są stronami PDF
        info["niepewne_miejsca"] = [(r, n, None) for r, n, _ in info["niepewne_miejsca"]]
    if zeszyt and not start and info.get("niepewne_obszary"):
        info["niepewne_obszary"] = [(r, o, None, None, s, e) for r, o, _, _, s, e in info["niepewne_obszary"]]
    if not txt:
        rada = (f"; odczyt OCR: tekst {label} --ocr (ok. {info['ocr_pominiete'][1] // 60 + 1} min)"
                if info.get("ocr_pominiete") else "")
        return "", url, "pdftotext nie zwrócił tekstu (PDF bez warstwy tekstowej albo błąd konwersji)" + rada
    return txt, url, ""


_PATRZ_ORYGINAL = re.compile(r"(?i)patrz\s+orygina[łl]")
# `pdf_do_tekstu_z_notkami`: „bez_wspolrzednych" — `pdftotext -bbox` nie dał współrzędnych słów dla stron z `-layout`
_UWAGA_BEZ_WSPOLRZEDNYCH = ("pdftotext -bbox nie zwrócił współrzędnych słów dla stron tego PDF, więc indeksy górne („art. 13¹”) "
                            "i odsyłacze do przypisów NIE zostały potwierdzone współrzędnymi — rozpoznano je tylko po kształcie "
                            "tekstu: liczby stojące tuż przed przypisem mogą być obcięte („152,61” z odsyłaczem „3)” → „152,”), "
                            "a indeks górny może być sklejony z numerem („art. 131” zamiast 13¹).")


# Miejsca tekstu z PDF, których odczyt nie jest pewny, a które dotąd nie dawały żadnego ostrzeżenia (13.11.2026):
# - „[przypis N)] (treści przypisu nie odnaleziono…)" — odsyłacz jest, a treści przypisu pod tekstem strony nie znaleziono;
#   bywa, że została w treści przepisu jako akapit „N) Zmiany tekstu…" (k.p. DU 2026/1245 art. 302¹ § 7 przed poprawką);
# - przypis „bez odsyłacza w treści tej strony" — jego odsyłacz mógł zostać w treści jako „N)", udając punkt wyliczenia
#   (DU 2026/1473: „…Państwowej Straży Pożarnej 15)" w tytule rozdziału przed poprawką);
# - akapit, który jest samą datą — stopka strony wklejona w treść (DU 2019/1500: „18.01.2021" w środku zdania).
_RODZAJ_NIEPEWNY = (
    ("brak_tresci", re.compile(r"(?m)^\[przypis (\d+[a-z]?)\)\] \(treści przypisu nie odnaleziono"),
     "przypis {n}) bez treści{gdzie} — treści nie znaleziono pod tekstem strony; mogła zostać w treści przepisu jako akapit "
     "„{n}) …”"),
    ("bez_odsylacza", _PRZYPIS_BEZ_ODSYLACZA_LINIA,
     "przypis {n}) bez odsyłacza{gdzie} — odsyłacz mógł zostać w treści jako „{n})” i udawać punkt wyliczenia"),
    ("data", re.compile(r"(?m)^(" + _PDF_SAMA_DATA.pattern + r")$"),
     "samodzielna data „{n}”{gdzie} — prawdopodobnie stopka strony PDF w treści; zdanie wokół niej może być rozcięte"),
    ("przypis_w_tresci", _PDF_PRZYPIS_W_TRESCI,
     "„{n}) …” w treści przepisu{gdzie} — to najpewniej przypis z dołu strony PDF wklejony w przepis, nie jego część"),
)


_WIERSZ_TRESCI = re.compile(r"(?m)^(?!\[|» ).+$")


def _trafienia_niepewne(rodzaj, wzor, txt, s, e):
    """Trafienia wzorca rodzaju w txt[s:e]; „przypis_w_tresci" — tylko w wierszach treści (nie w „[przypis …]",
    „[margines: …]" ani w wierszach obwieszczenia „» …")."""
    if rodzaj != "przypis_w_tresci":
        return list(wzor.finditer(txt, s, e))
    out = []
    for w in _WIERSZ_TRESCI.finditer(txt, s, e):
        out.extend(wzor.finditer(txt, w.start(), w.end()))
    return out


_NA_SUP_LITERY = str.maketrans("abcdefghijklmnoprstuvwxyz", _SUPS_LITERY)
_ZALACZNIK_W_TEKSCIE = re.compile(r"(?m)^Załącznik(?!\s+do\s+obwieszczenia)")

# Obszary niepewne (`pdf_layout_do_tekstu`, info „niepewne_obszary") — opis ostrzeżenia dla każdego rodzaju
_OPIS_OBSZARU = {
    "tabela": "tabela{gdzie} — komórki zawinięte na kilka wierszy są czytane wiersz po wierszu strony, więc słowa sąsiednich "
              "kolumn mogą być przemieszane (tak samo wygląda tekst w dwóch łamach, których nie udało się rozdzielić); liczby "
              "i przypisanie wartości do pozycji sprawdź w PDF",
    "warstwa": "strona z ukrytą warstwą tekstu{gdzie} — wiersz „{opis}” jest w PDF dwa razy (jedna kopia niewidoczna), więc "
               "tekst bywa tu zdublowany albo przemieszany",
    "numeracja": "{opis}{gdzie} — numeracja jednostek w odczycie się nie zgadza (OCR albo kolejność łamów); treść sąsiednich "
                 "jednostek może być sklejona albo przestawiona",
    "notka": "strona z notką na marginesie, której nie dało się odciąć od treści{gdzie} — słowa notki („{opis}…”) mogą być "
             "wtrącone w tekst przepisu albo przypisu",
}


def _jednostka_w_miejscu(txt, pos):
    """Artykuł, w którym leży pozycja `pos` („art. 302¹" — indeks górny czytelny, jak w PDF), „załącznik" za ostatnim artykułem
    aktu albo ""."""
    m = _ART_NAGLOWEK.match(_artykul_nad(txt, pos))
    art = ""
    if m:
        baza, _, idx = _id_art(m).partition(" ")
        art = "art. " + baza + idx.translate(_NA_SUP).translate(_NA_SUP_LITERY)
    zal = None
    for z in _ZALACZNIK_W_TEKSCIE.finditer(txt, 0, pos):
        zal = z
    if zal and zal.start() > txt.rfind("\nArt.", 0, pos):
        art = "załącznik"       # za ostatnim artykułem aktu — nie „art. 176"
    return art


def _niepewne_miejsca_pdf(txt, spans, niepewne, obszary=None):
    """Opisy miejsc z `_RODZAJ_NIEPEWNY` w tekście z PDF (`spans` — tylko te fragmenty; None — cały tekst). `niepewne` — lista
    (rodzaj, numer, strona PDF) z `pdf_layout_do_tekstu` (info „niepewne_miejsca"): k-te wystąpienie etykiety o danym numerze
    w tekście to k-ty wpis tej listy. Każdy opis podaje stronę PDF (gdy znana) i artykuł, w którym stoi etykieta.
    `obszary` — info „niepewne_obszary" (rodzaj, opis, strona od, strona do, początek, koniec): opis, gdy obszar zachodzi na
    któryś z `spans` (przy pełnym tekście — zawsze)."""
    if spans is not None and not spans:
        return []
    strony = {}
    for r, n, st in niepewne or []:
        strony.setdefault((r, n), []).append(st)
    opisy = []
    for s, e in spans or [(0, len(txt))]:
        for rodzaj, wzor, szablon in _RODZAJ_NIEPEWNY:
            for m in _trafienia_niepewne(rodzaj, wzor, txt, s, e):
                n = m.group(1)
                k = sum(1 for x in _trafienia_niepewne(rodzaj, wzor, txt, 0, m.start()) if x.group(1) == n)
                lista = strony.get((rodzaj, n), [])
                st = lista[k] if k < len(lista) else None
                gdzie = ", ".join(x for x in ((f"s. {st} PDF" if st else ""), _jednostka_w_miejscu(txt, m.start())) if x)
                opisy.append((m.start(), szablon.format(n=n, gdzie=f" ({gdzie})" if gdzie else "")))
    for rodzaj, opis, od, do, s, e in obszary or []:
        if spans is not None and not any(s < fe and fs < max(e, s + 1) for fs, fe in spans):
            continue
        strona = (f"s. {od} PDF" if od == do else f"s. {od}–{do} PDF") if od else ""
        gdzie = ", ".join(x for x in (strona, _jednostka_w_miejscu(txt, s)) if x)
        opisy.append((s, _OPIS_OBSZARU[rodzaj].format(opis=opis, gdzie=f" ({gdzie})" if gdzie else "")))
    return [o for _, o in sorted(opisy)]


def _odmowa_nadpisania(plik):
    return (f"BŁĄD: plik {plik!r} już istnieje — nie nadpisuję go. Podaj inną nazwę pliku albo usuń "
            "istniejący, jeśli na pewno nie jest potrzebny.")


def cmd_tekst(a):
    strict = getattr(a, "strict", False)
    # --pdf nie nadpisuje istniejącego pliku (dotąd open(…, "wb") kasował go bez słowa — np. wcześniej pobrany
    # PDF innego aktu albo własny dokument o tej nazwie; 13.11.2026). Sprawdzenie PRZED pobieraniem czegokolwiek;
    # zapis w trybie „xb" domyka wyścig, gdy plik powstanie w trakcie pobierania.
    if a.pdf and os.path.lexists(a.pdf):
        sys.exit(_odmowa_nadpisania(a.pdf))
    path, label = act_path(a.sygnatura)
    # odniesienia służą tylko ostrzeżeniom o aktualności — ich awaria nie może odebrać
    # użytkownikowi samego tekstu; zamiast tego tekst dostaje GŁOŚNE ostrzeżenie
    try:
        refs = _get(path + "/references", soft=True)
        if not isinstance(refs, dict) or any(
                not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
                for rows in refs.values()):
            raise VerificationUnknown("nieoczekiwana odpowiedź endpointu /references")
    except VerificationUnknown as e:
        if strict:
            raise
        refs = None
        ostrz = [f"UWAGA: nie udało się zweryfikować aktualności aktu {label} ({e}) — "
                 "sprawdź nowelizacje i teksty jednolite ręcznie, zanim zacytujesz."]
    wynik_zmian = {}
    if refs is not None:
        ostrz = _ostrzezenia(refs, path, strict, wynik_zmian) if isinstance(refs, dict) else []
        # nowszy t.j. — zarówno dla aktu bazowego, jak i dla aktu, który SAM jest (starszym) t.j.
        try:
            nowszy = _nowszy_tj(path, refs) if isinstance(refs, dict) else None
        except VerificationUnknown as e:
            if strict:
                raise
            nowszy = None
            ostrz.append(f"UWAGA: nie udało się sprawdzić, czy istnieje nowszy tekst jednolity ({e}) — "
                         f"zweryfikuj: tj {label}, zanim zacytujesz.")
        if nowszy:
            aktualny = nowszy.get("displayAddress") or nowszy.get("ELI", "")
            if strict:
                sys.exit(f"BŁĄD: istnieje nowszy tekst jednolity: {aktualny}. "
                         "Tryb strict blokuje starszą treść.")
            if not _tj_acts(refs):  # akt bazowy ma już ostrzeżenie „cytuj z najnowszego" z _ostrzezenia()
                sig = (nowszy.get("ELI") or "").replace("/", " ")
                ostrz.insert(0, f"UWAGA: {label} to NIEAKTUALNY tekst jednolity — istnieje NOWSZY: "
                                f"{aktualny}. Cytuj z niego: tekst {sig}")
    if a.pdf:
        # pobierz urzędowy PDF (preferuj tekst jednolity, typ 'U'/'T', inaczej oryginał 'O')
        meta = _expect_dict(_get(path), "metadane aktu")
        pick = _wybierz_pdf(meta)
        if not pick:
            sys.exit("Brak PDF w metadanych aktu.")
        url = f"{BASE}{path}/text/{pick['type']}/{pick['fileName']}"
        data = _get_bytes(url)
        try:
            with open(a.pdf, "xb") as f:
                f.write(data)
        except FileExistsError:
            sys.exit(_odmowa_nadpisania(a.pdf))
        print(f"Zapisano PDF ({len(data)} B): {a.pdf}\n(źródło: {url})")
        for w in ostrz:
            print(w)
        return
    ujednolicony = False
    html = _get(path + "/text.html")
    if not isinstance(html, str):
        sys.exit("BŁĄD: API zwróciło nieoczekiwaną odpowiedź zamiast tekstu HTML aktu.")
    # akt w vacatio legis / z jednostkami wchodzącymi w życie później — status API „obowiązujący" tego nie mówi
    try:
        meta_aktu = _get(path, soft=True)
    except VerificationUnknown:
        meta_aktu = None
    if isinstance(meta_aktu, dict):
        ostrz = _jeszcze_nie_obowiazuje(meta_aktu) + ostrz
    txt = html_to_text(html if isinstance(html, str) else "")
    zrodlo = "z text.html; HTML→tekst"
    if not txt:
        # textHTML=false (np. k.c. DU 2026 795, Konstytucja DU 1997 483, świeże pozycje): najpierw
        # WŁASNY urzędowy PDF tego aktu — to jest jego tekst, więc --strict go przepuszcza
        meta = _expect_dict(_get(path), "metadane aktu")
        info_pdf = {}
        txt, url, pdf_blad = _tekst_z_pdf(path, label, meta, info_pdf,
                                          ocr=False if getattr(a, "bez_ocr", False) else (True if getattr(a, "ocr", False) else None))
        if txt and info_pdf.get("ocr_strony") and strict:
            n, m = info_pdf["ocr_strony"]
            sys.exit(f"BŁĄD (strict): {n} z {m} stron PDF dla {label} to skany odczytane przez OCR (tesseract) — tekst "
                     "niepewny (cyfry, daty, kwoty). Tryb strict blokuje tekst z OCR; bez --strict zostanie wypisany "
                     f"z ostrzeżeniem, a wiążący jest PDF: tekst {label} --pdf plik.pdf")
        if txt and info_pdf.get("puste_strony") and strict:
            n, m = info_pdf["puste_strony"]
            sys.exit(f"BŁĄD (strict): {n} z {m} stron PDF dla {label} nie ma warstwy tekstowej (skan) — ich treści NIE "
                     "byłoby w wyniku, więc tekst jest NIEKOMPLETNY. Tryb strict blokuje niekompletny tekst. Odczytaj "
                     f"skany przez OCR bez --strict (tekst {label} --ocr; wynik z ostrzeżeniem o niepewności OCR) "
                     f"albo pobierz PDF: tekst {label} --pdf plik.pdf")
        if txt and info_pdf.get("bez_wspolrzednych") and strict:
            sys.exit(f"BŁĄD (strict): {label} — {_UWAGA_BEZ_WSPOLRZEDNYCH} Tryb strict blokuje taki tekst; bez --strict "
                     f"zostanie wypisany z ostrzeżeniem, a wiążący jest PDF: tekst {label} --pdf plik.pdf")
        if txt:
            zrodlo = "z urzędowego PDF przez pdftotext -layout"
            if info_pdf.get("podstawa"):
                ujednolicony = True
                opis = (f"TEKST UJEDNOLICONY Kancelarii Sejmu — nieurzędowy"
                        + (f", wydruk z {info_pdf['data_wydruku']}" if info_pdf.get("data_wydruku") else "")
                        + f"; {info_pdf['podstawa'].rstrip('.')}. "
                        + (f"To t.j. ze stanem prawnym na {meta['legalStatusDate']} PLUS późniejsze zmiany z tej listy"
                           if meta.get("legalStatusDate") else "To akt w brzmieniu nadanym zmianami z tej listy")
                        + ", także tymi, które jeszcze nie weszły w życie: brzmienie "
                        "przyszłe stoi w < … >, brzmienie przez nie zastępowane w [ … ], datę wejścia w życie podaje "
                        "linia „[margines: …]”")
                ostrz = _oznacz_uwzglednione(ostrz, _podstawa_ujednolicenia(info_pdf["podstawa"]))
            else:
                opis = (f"stan prawny na {meta['legalStatusDate']}" if meta.get("legalStatusDate") else "tekst ogłoszony")
                if info_pdf.get("ocr"):
                    opis += ("; warstwa tekstowa tego PDF to OCR skanu z lat 90. — możliwe przekłamane litery i cyfry "
                             "oraz rozbite wyrazy („sk ładu”), a liczby, daty i kwoty sprawdź w PDF")
            if info_pdf.get("puste_strony"):
                n, m = info_pdf["puste_strony"]
                rada = (f" Odczyt OCR wszystkich stron: tekst {label} --ocr (ok. {info_pdf['ocr_pominiete'][1] // 60 + 1} min)."
                        if info_pdf.get("ocr_pominiete") else
                        " OCR niedostępny (zainstaluj tesseract z językiem polskim i poppler)." if not ocr_dostepny() else "")
                ostrz.insert(0, f"UWAGA: {n} z {m} stron tego PDF nie ma warstwy tekstowej (skan albo strona pusta) — "
                                "ich treści NIE MA poniżej. Sprawdź PDF: tekst " + label + " --pdf plik.pdf." + rada)
            if info_pdf.get("bez_wspolrzednych"):
                ostrz.insert(0, f"UWAGA: {_UWAGA_BEZ_WSPOLRZEDNYCH} Do cytatu (zwłaszcza liczb, kwot i numerów jednostek) "
                                f"sprawdź PDF: tekst {label} --pdf plik.pdf")
            if info_pdf.get("ocr_strony"):
                n, m = info_pdf["ocr_strony"]
                ostrz.insert(0, f"UWAGA — OCR: {n} z {m} stron tego PDF to skany odczytane przez OCR (tesseract). Tekst "
                                "z tych stron jest NIEPEWNY: litery, cyfry, daty i kwoty mogą być przekłamane, a układ "
                                "(akapity, tabele) zniekształcony. Przed cytatem sprawdź w PDF: tekst " + label + " --pdf plik.pdf")
            ostrz = [f"ELI_TEXT_SOURCE_PDF={url}",
                     f"UWAGA: text.html dla {label} jest PUSTE w API (textHTML=false) — poniżej tekst "
                     f"WYEKSTRAHOWANY z urzędowego PDF tego aktu ({opis}). "
                     "Sklejanie wierszy i dzielonych wyrazów jest automatyczne; linie „[przypis N)]\" to "
                     "przypisy z dołu strony PDF. Do dosłownego cytatu: tekst " + label + " --pdf plik.pdf"] + ostrz
        else:
            if strict:
                sys.exit(f"BŁĄD: text.html dla {label} jest PUSTE w API (textHTML=false), a urzędowego PDF "
                         f"nie dało się przetworzyć ({pdf_blad}). Tryb strict blokuje zastępczy (STARSZY) tekst "
                         f"jednolity, bo jego kompletności nie da się zweryfikować. Zainstaluj pdftotext "
                         f"(poppler: brew install poppler / apt install poppler-utils) albo pobierz PDF: "
                         f"tekst {label} --pdf plik.pdf")
            try:
                fb = _tj_z_tekstem(path, refs if isinstance(refs, dict) else {})
            except VerificationUnknown as e:
                _nie_zweryfikowano(f"zapasowego tekstu jednolitego dla {label}", e)
            if not fb:
                sys.exit(f"BŁĄD: text.html dla {label} jest PUSTE w API (textHTML=false — dla tego aktu API nie "
                         f"udostępnia HTML), urzędowego PDF nie dało się przetworzyć ({pdf_blad}) i nie ma innego "
                         f"tekstu jednolitego z tekstem. Zainstaluj pdftotext (poppler) albo pobierz PDF: "
                         f"tekst {label} --pdf plik.pdf")
            act, txt, pominiete = fb
            addr = _akt_opis(act)
            actual_eli = act.get("ELI") or ""
            zrodlo = "z text.html STARSZEGO t.j.; HTML→tekst"
            ostrz = _ostrzezenie_starszego_tj(path, label, refs, meta, act, pominiete, pdf_blad) + ostrz
            label = f"{label} (NIEAKTUALNE BRZMIENIE MOŻLIWE — tekst z: {addr})"
    if not ujednolicony and "STARSZEGO" not in zrodlo and isinstance(refs, dict) and _akt_bazowy(refs):
        # tekst jednolity: obwieszczenie mówi, które zmiany z listy „po tym t.j." są już w tekście (pkt 1), a które nie
        ostrz = _oznacz_obwieszczenie(ostrz, *_obwieszczenie_tj(txt))
    # imienna analiza zmian po stanie tekstu i wyroków TK — PRZED wypisaniem, bo --strict blokuje przed emisją
    if wynik_zmian.get("pozycje") is not None and "STARSZEGO" not in zrodlo:
        info_akt = {}
        if ujednolicony:
            info_akt["podstawa"] = _podstawa_ujednolicenia(info_pdf.get("podstawa"))
        elif _akt_bazowy(refs):
            info_akt["uwzgl"], info_akt["nieobj"], info_akt["przed"] = _obwieszczenie_tj(txt)
            ostrz += _sprawdz_stan_obwieszczenia(txt, meta_aktu, wynik_zmian)
        spans_wst = _fragmenty(txt, a.fragment) if a.fragment else None
        if not a.fragment or spans_wst:
            linie_zm, blokada = _aktualnosc(wynik_zmian, txt, _klucze_fragmentu(txt, spans_wst) if a.fragment else None,
                                            info_akt, strict)
            if blokada and strict:
                sys.exit(f"BŁĄD (strict): {label} — " + ("ten fragment" if a.fragment else "ten tekst")
                         + f" może podawać NIEAKTUALNE brzmienie: {blokada}. Tryb strict blokuje taki wynik; bez --strict "
                         "zostanie wypisany z ostrzeżeniem imiennym.")
            ostrz += linie_zm
        elif _klucz_frazy(a.fragment):
            # artykułu nie ma w tekście — może go DODAĆ zmiana po stanie tekstu (imprezy turystyczne DU 2026/925:
            # art. 47a dodany przez DU 2026/1006 od 2026-08-11); wtedy to nie jest „brak", tylko nieaktualny tekst
            linie_zm, blokada = _aktualnosc(wynik_zmian, txt, {_klucz_frazy(a.fragment)}, info_akt, strict)
            nazwane = [l for l in linie_zm if l.startswith("UWAGA") and "nie ustaliłem" not in l]
            if nazwane:
                sys.exit(f"Nie znaleziono {a.fragment.strip()!r} w tekście {label} — ale ten przepis dotyczy zmiana po "
                         "stanie tego tekstu:\n" + "\n".join(l.replace("Poniżej brzmienie SPRZED tej zmiany — aktualne "
                                                                       "nanieś z:", "Brzmienie:") for l in nazwane)
                         + "\nTo NIE jest brak przepisu: tekst jest nieaktualny. Brzmienie weź z aktu zmieniającego.")
    if zrodlo.startswith("z text.html"):
        # HTML API ma w miejscu tabeli, wzoru albo rysunku samo odesłanie „patrz oryginał" (link do PDF) — tej treści nie
        # ma w wyniku, a narzędzie dotąd milczało (13.11.2026: 6 z 30 losowych aktów Dz.U. 2024 z HTML, np. t.j. DU 2023/2190
        # — załącznik nr 1 z dietami za granicą to tylko „patrz oryginał"; DU 2024/694 — 6 załączników)
        zakres = "\n".join(txt[s:e] for s, e in _fragmenty(txt, a.fragment)) if a.fragment else txt
        brakujace = len(_PATRZ_ORYGINAL.findall(zakres))
        if brakujace and strict:
            sys.exit(f"BŁĄD (strict): {label} — w {'tym fragmencie' if a.fragment else 'tym tekście'} {brakujace}× zamiast "
                     "treści stoi odsyłacz API „patrz oryginał” (tabela, wzór albo rysunek), więc tekst jest NIEKOMPLETNY. "
                     f"Tryb strict blokuje niekompletny tekst; treść jest w PDF: tekst {label} --pdf plik.pdf")
        if brakujace:
            ostrz.insert(0, f"UWAGA: w {'tym fragmencie' if a.fragment else 'tym tekście'} {brakujace}× zamiast treści stoi "
                            "odsyłacz API „patrz oryginał” — tej części (zwykle tabeli, wzoru albo rysunku w załączniku) NIE MA "
                            f"poniżej. Jest tylko w PDF: tekst {label} --pdf plik.pdf")
    if zrodlo.startswith("z urzędowego PDF"):
        # miejsca odczytane z PDF niepewnie (`_niepewne_miejsca_pdf`) — dotąd tylko etykieta w tekście, bez ostrzeżenia i bez
        # blokady --strict; z --fragment liczą się tylko te w wypisanym fragmencie
        miejsca = _niepewne_miejsca_pdf(txt, _fragmenty(txt, a.fragment) if a.fragment else None,
                                        info_pdf.get("niepewne_miejsca"), info_pdf.get("niepewne_obszary"))
        if miejsca:
            gdzie = "tym fragmencie" if a.fragment else "tym tekście"
            n = len(miejsca)
            lista = "; ".join(miejsca[:12]) + (f"; … i {n - 12} innych" if n > 12 else "")
            if strict:
                sys.exit(f"BŁĄD (strict): {label} — w {gdzie} są miejsca odczytane z PDF niepewnie ({n}): {lista}. Tryb "
                         "strict blokuje taki wynik; bez --strict zostanie wypisany z ostrzeżeniem, a wiążący jest PDF: "
                         f"tekst {label} --pdf plik.pdf")
            ostrz.insert(2 if ostrz and ostrz[0].startswith("ELI_TEXT_SOURCE_PDF=") else 0,
                         f"UWAGA: w {gdzie} są miejsca odczytane z PDF niepewnie ({n}) — przed cytatem porównaj je z PDF "
                         f"(tekst {label} --pdf plik.pdf): {lista}.")
    print(f"# {label} — tekst ({zrodlo}; do dosłownego cytatu zweryfikuj z PDF urzędowym)\n")
    for w in ostrz:
        print(w)
    if ostrz:
        print()
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            goly = re.sub(r"(?i)^art\.?\s*", "", a.fragment.strip()) or "N"
            # kod 1: tekst pobrany, frazy w nim nie ma — ale to nadal NIE dowód, że przepisu nie ma
            raise BrakWynikow(f"Nie znaleziono frazy {a.fragment!r} w tekście aktu ({len(txt)} znaków).\n"
                              "UWAGA: to NIE dowodzi, że przepisu nie ma — zanim tak napiszesz, sprawdź samym "
                              f"numerem (--fragment \"{goly}\"), słowem kluczowym z treści "
                              "albo pobierz pełny tekst bez --fragment.")
        # tryb nagłówkowy zawiódł → poniżej trafienia pełnotekstowe, więc mogą to być ODESŁANIA
        paragraf = bool(_FRAZA_PARAGRAFU.match(a.fragment.strip())) and not _hity_naglowka(txt, a.fragment)
        hity_par = _hity_paragrafu(txt, a.fragment) if paragraf else []
        if (re.match(r"(?i)^art\.?\s*\d", a.fragment.strip()) and not paragraf and not _hity_naglowka(txt, a.fragment)
                or paragraf and not hity_par):
            print(f"UWAGA: nie znalazłem NAGŁÓWKA {a.fragment!r} w tym akcie — poniżej trafienia "
                  "pełnotekstowe; sprawdź, czy to sam przepis, czy tylko odesłanie do niego.\n")
            obcy = _naglowek_poza_numeracja(txt, a.fragment)
            if obcy:
                print(f"UWAGA: w miejscu {a.fragment.strip()!r} (między sąsiednimi artykułami) stoi nagłówek „{obcy}” — "
                      "to może być ten przepis z błędnym numerem w danych API (np. k.p.c. t.j. DU 2024/1568: art. 15 ma w "
                      f"HTML i /struct nagłówek „Art. 1522.”). Sprawdź numer w PDF: tekst {label} --pdf plik.pdf\n")
        artykuly = [_artykul_nad(txt, s) for s, _ in spans] if hity_par else []
        if hity_par and len(hity_par) > 1 and any(artykuly) and not _FRAZA_PARAGRAFU.match(a.fragment.strip()).group(1):
            print(f"UWAGA: {a.fragment.strip()!r} bez numeru artykułu jest NIEJEDNOZNACZNE — paragraf o tym numerze "
                  f"występuje w tym akcie {len(hity_par)} razy (w różnych artykułach)"
                  + (f"; pokazuję pierwsze {len(spans)}" if len(spans) < len(hity_par) else "")
                  + "; przy każdym trafieniu podaję artykuł. Jeden przepis: --fragment \"art. N "
                  + a.fragment.strip() + "\".\n")
        kontekst, dodatkowe = [], []
        bloki = _otwarte_bloki(txt, spans[0][0]) if ujednolicony else []
        for otwarcie, notki in bloki:
            kontekst.append(f"UWAGA: ten fragment leży WEWNĄTRZ bloku „{otwarcie[:80]}” — "
                            + ("brzmienie PRZYSZŁE (< … >)" if otwarcie.startswith("<") else "brzmienie zastępowane ([ … ])")
                            + (": " + " ".join(n[len('[margines: '):-1] for n in notki) if notki else ""))
            dodatkowe += notki
        # przypis/notka przy nagłówku rozdziału, działu… obejmuje też ten fragment
        for naglowek, przy in _naglowki_nadrzedne(txt, spans[0][0]):
            if any(naglowek == o for o, _ in bloki) or not any(_data_wejscia(l) or _termin_bez_daty(l) for l in przy):
                continue
            kontekst.append(f"UWAGA: ten fragment leży w jednostce „{naglowek[:80]}” — przypis/notka przy jej nagłówku "
                            "dotyczy także tego fragmentu (patrz niżej).")
            dodatkowe += przy
        # przypisy fragmentu odsyłające do innych przypisów („…ustawy, o której mowa w odnośniku 6") — data wejścia
        # w życie bywa tylko w tamtym przypisie, przy innym przepisie
        dociagniete, analiza, ostrz_przypisow = _przypisy_fragmentu("\n".join(txt[s:e] for s, e in spans), txt)
        kontekst += _tk_w_przypisach("\n".join(txt[s:e] for s, e in spans))
        for w in kontekst + ostrz_przypisow + _ostrzezenie_przyszle(
                "\n".join(dodatkowe + analiza + [txt[s:e] for s, e in spans]), ujednolicony):
            print(w)
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            if artykuly and artykuly[i] and not re.match(r"[\[<]?Art\.", txt[s:e]) \
                    and not _FRAZA_PARAGRAFU.match(a.fragment.strip()).group(1):
                print(f"[{a.fragment.strip()} — w: {artykuly[i]}]")
            print(txt[s:e].strip())
        if dociagniete:
            print("\n[przypisy spoza fragmentu, do których odsyłają przypisy fragmentu:]")
            for l in dociagniete:
                print(l)
        print(f"\n(fragmenty: {len(spans)} — pominięto resztę aktu; pełny tekst: bez --fragment)")
        return
    przyszle = _ostrzezenie_przyszle(txt, ujednolicony)
    for w in przyszle:
        print(w)
    if przyszle:
        print()
    if len(txt) > 60000:
        print(f"(UWAGA: pełny tekst ma {len(txt)} znaków — do pojedynczego przepisu użyj --fragment \"art. N\")\n")
    print(txt)


def _ostrzezenie_starszego_tj(path, label, refs, meta, act, pominiete, pdf_blad):
    """Nagłówek ostrzegawczy dla tekstu ze STARSZEGO t.j. + INLINE lista zmian aktu bazowego po jego
    stanie prawnym (dawna instrukcja „odniesienia <stary t.j.>" była martwa: wygasły t.j. nie ma
    w API kategorii „Nowelizacje po tekście jednolitym")."""
    addr = _akt_opis(act)
    actual_eli = act.get("ELI") or ""
    out = [f"ELI_TEXT_SOURCE_FALLBACK={actual_eli}"]
    stan = ""
    try:
        m = _get(f"/acts/{actual_eli}", soft=True) if actual_eli else None
        stan = m.get("legalStatusDate") or "" if isinstance(m, dict) else ""
    except VerificationUnknown:
        stan = ""
    out.append(f"UWAGA — NIEAKTUALNE BRZMIENIE MOŻLIWE: text.html dla {label} jest PUSTE w API (textHTML=false), "
               f"a urzędowego PDF nie dało się przetworzyć ({pdf_blad}). Poniżej tekst STARSZEGO tekstu "
               f"jednolitego {addr}" + (f" (stan prawny na {stan})" if stan else "") + "."
               + (f" Pominięto t.j. z pustym text.html: {', '.join(pominiete)}." if pominiete else ""))
    base = _akt_bazowy(refs) if isinstance(refs, dict) else None
    if not base and isinstance(refs, dict) and _tj_acts(refs):
        base = {"ELI": path[len("/acts/"):], "displayAddress": meta.get("displayAddress", "")}
    if base and base.get("ELI") and stan:
        try:
            zmiany = _zmiany_bazowe_po(base["ELI"], stan)
        except VerificationUnknown as e:
            out.append(f"UWAGA: nie udało się pobrać zmian aktu bazowego po {stan} ({e}) — sprawdź ręcznie: "
                       f"odniesienia {base['ELI'].replace('/', ' ')} (sekcja „Akty zmieniające\").")
        else:
            if zmiany:
                out.append(f"Zmiany aktu bazowego {_akt_opis(base)} ogłoszone lub wchodzące w życie po {stan} "
                           f"({len(zmiany)}) — NIE ma ich w poniższym tekście, nałóż je sam:")
                out += [_fmt_ref(r, "Akty zmieniające") for r in zmiany]
            else:
                out.append(f"W API brak zmian aktu bazowego {_akt_opis(base)} po {stan} (indeksacja bywa opóźniona).")
    else:
        out.append("UWAGA: nie ustaliłem stanu prawnego starszego t.j. — sprawdź „Akty zmieniające\" aktu bazowego ręcznie.")
    out.append(f"Do dosłownego, aktualnego brzmienia: zainstaluj pdftotext (poppler) i powtórz, albo: tekst {label} --pdf plik.pdf")
    return out


def cmd_struktura(a):
    path, label = act_path(a.sygnatura)
    # /struct istnieje głównie dla t.j. i starszych aktów — 404 to zweryfikowany brak, nie awaria
    d = _get(path + "/struct", soft=True)
    nodes = d if isinstance(d, list) else [d] if isinstance(d, dict) else None
    if not nodes:
        komunikat = (f"Brak struktury dla tego aktu ({label}) — API udostępnia /struct głównie dla tekstów "
                     f"jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają. "
                     f"Spis jednostek odczytasz z tekstu: tekst {label} | grep -n \"^Art\\.\"")
        # 404 na /struct wygląda tak samo dla „akt bez struktury" i „nie ma takiego aktu" — zweryfikowany
        # brak (kod 1) tylko wtedy, gdy metadane aktu istnieją; inna odpowiedź niż 404/pusta lista — kod 2
        if d is None or d == []:
            try:
                istnieje = isinstance(_get(path, soft=True), dict)
            except VerificationUnknown:
                istnieje = False
            if istnieje:
                raise BrakWynikow(komunikat)
            sys.exit(f"Brak struktury dla tego aktu ({label}), a API nie zwraca też jego metadanych — sprawdź "
                     f"sygnaturę (meta {label}).")
        sys.exit(f"BŁĄD: API zwróciło nieoczekiwaną odpowiedź zamiast struktury aktu {label}. " + komunikat)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    filtr = (a.filtr or "").lower()
    poziom = a.poziom if a.poziom is not None else (None if filtr else 3)
    print(f"Struktura: {label}  (frazę z 'title' podaj w: tekst {label} --fragment \"...\"; indeks górny "
          f"„Art. 7_1.” = art. 7¹ — podaj go jako \"art. 7(1)\")\n")
    wypisane = 0

    def walk(n, depth=0):
        nonlocal wypisane
        tytul = (n.get('title') or '').strip()
        line = f"{'  ' * depth}{n.get('id','?')}  [{n.get('type','')}]  {tytul}"
        # /struct zapisuje indeks górny jako „7_1" — gotowa postać do --fragment
        m = re.match(r"(?i)^art\.\s*(\d+[a-z]*)_(\d+[a-z]*)\.?$", tytul)
        if m:
            line += f"  (--fragment \"art. {m.group(1)}({m.group(2)})\")"
        if (not filtr or filtr in line.lower()) and (poziom is None or depth < poziom):
            print(line)
            wypisane += 1
        for c in n.get("children") or []:
            walk(c, depth + 1)

    for n in nodes:
        walk(n)
    if not wypisane:
        raise BrakWynikow(f"Nic w strukturze {label} nie pasuje do filtra {a.filtr!r} — sprawdź pisownię "
                          "(np. 'Art. 7_1' dla art. 7¹) albo przeszukaj tekst: tekst … --fragment.")


def cmd_odniesienia(a):
    path, label = act_path(a.sygnatura)
    d = _get(path + "/references")
    d = _expect_dict(d, "odniesienia aktu")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Odniesienia dla: {label}")
    print("(daty: „data aktu\" = data wydania z tytułu, „ogłoszono\" = publikacja w Dz.U./M.P., "
          "„wejście w życie zmiany\" = pole date w „Akty zmieniające\")\n")
    for kind, lst in d.items():
        items = lst if isinstance(lst, list) else [lst]
        print(f"## {kind}  ({len(items)})")
        for ref in items:
            print(_fmt_ref(ref, kind))
        print()
    if _akt_bazowy(d):
        linie, uwagi = _nowelizacje_po_tj(d, path, getattr(a, "strict", False))
        for w in uwagi + linie:
            print(w)


def cmd_tj(a):
    path, label = act_path(a.sygnatura)
    d = _get(path + "/references")
    d = _expect_dict(d, "odniesienia aktu")
    # akt bazowy: kategoria 'Inf. o tekście jednolitym' listuje obwieszczenia z t.j.
    tj = _tj_acts(d)
    if tj:
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=2)); return
        print(f"TEKSTY JEDNOLITE dla {label} (najnowszy pierwszy):")
        for i, act in enumerate(tj):
            marker = "  ← AKTUALNY" if i == 0 else ""
            print(f"  - {act.get('displayAddress') or act.get('ELI','')}  [{act.get('status','')}]{marker}")
        eli = tj[0].get("ELI", "")
        if eli:
            sig = eli.replace("/", " ")
            print(f"\nDalej: python3 {sys.argv[0]} tekst {sig} --fragment \"art. N\"")
            print(f"(tekst na t.j. sam wypisuje „Nowelizacje po tekście jednolitym\" — uzupełnione o „Akty "
                  f"zmieniające\" aktu bazowego po stanie prawnym t.j.; pełna lista: odniesienia {label})")
        return
    # akt sam jest tekstem jednolitym: kategoria 'Tekst jednolity dla aktu' wskazuje akt BAZOWY
    base_key = next((k for k in d if k.lower().startswith("tekst jednolity dla aktu")), None)
    if base_key:
        base = _akt_bazowy(d)
        # sprawdź na akcie bazowym, czy nie ma już NOWSZEGO tekstu jednolitego
        kontrola = None
        try:
            newer = _nowszy_tj(path, d)
        except VerificationUnknown as e:
            if getattr(a, "strict", False):
                raise
            kontrola = (f"UWAGA: nie udało się sprawdzić, czy istnieje nowszy tekst jednolity ({e}) — "
                        "zweryfikuj ręcznie, zanim zacytujesz.")
        else:
            if newer:
                aktualny = newer.get("displayAddress") or newer.get("ELI", "")
                if getattr(a, "strict", False):
                    sys.exit(f"BŁĄD: istnieje nowszy tekst jednolity: {aktualny}. "
                             "Tryb strict blokuje starszy wynik.")
                kontrola = f"UWAGA: istnieje NOWSZY tekst jednolity: {aktualny} — cytuj z niego."
        ostrz = _ostrzezenia(d, path, getattr(a, "strict", False))
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=2)); return
        print(f"{label} SAM JEST tekstem jednolitym" + (f" (dla: {base.get('displayAddress','')} — {base.get('title','')[:90]})" if base else "") + ".")
        for w in ostrz:
            print(w)
        if kontrola:
            print(kontrola)
        return
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Dla {label} brak tekstu jednolitego w odniesieniach — akt może nie mieć t.j. (cytuj z aktu, "
          f"ale sprawdź „Akty zmieniające\" w: odniesienia {label}).")


def main():
    ap = argparse.ArgumentParser(description="API ELI Sejmu (read-only). Źródło pierwotne prawa polskiego.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    ap.add_argument("--strict", action="store_true",
                    help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("szukaj"); s.add_argument("fraza", nargs="?", default=None); s.add_argument("--typ"); s.add_argument("--rok")
    s.add_argument("--wyd", type=str.upper, choices=["DU", "MP"]); s.add_argument("--obowiazujace", action="store_true")
    s.add_argument("--haslo", help="hasło przedmiotowe (keyword)"); s.add_argument("--offset", type=int, default=0)
    s.add_argument("--limit", type=int, default=10); s.set_defaults(func=cmd_szukaj)

    for name, fn in (("meta", cmd_meta), ("odniesienia", cmd_odniesienia), ("tj", cmd_tj)):
        p = sub.add_parser(name); p.add_argument("sygnatura", nargs="+"); p.set_defaults(func=fn)

    t = sub.add_parser("tekst"); t.add_argument("sygnatura", nargs="+"); t.add_argument("--pdf")
    t.add_argument("--fragment", help='wytnij tylko jednostki z frazą, np. "art. 299", "§ 4", "art. 25 § 2" albo "przedawnienie"')
    o = t.add_mutually_exclusive_group()
    o.add_argument("--ocr", action="store_true",
                   help=f"odczytaj OCR (tesseract) WSZYSTKIE strony-skany PDF (domyślnie tylko do {_OCR_AUTO_MAKS} stron)")
    o.add_argument("--bez-ocr", action="store_true", help="nie używaj OCR nawet dla krótkich skanów")
    t.set_defaults(func=cmd_tekst)

    st = sub.add_parser("struktura"); st.add_argument("sygnatura", nargs="+")
    st.add_argument("--filtr", help="pokaż tylko linie z frazą (np. 'Art. 299')")
    st.add_argument("--poziom", type=int, help="maks. głębokość drzewa (domyślnie 3; z --filtr bez limitu)")
    st.set_defaults(func=cmd_struktura)

    # Flagi globalne działają też PO komendzie (modele piszą je właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności")

    a = ap.parse_args()   # błąd argumentów: argparse sam kończy kodem 2
    try:
        a.func(a)
    except VerificationUnknown as e:
        _nie_zweryfikowano("danych w API ELI", e)


def kod_wyjscia(argv=None):
    """Uruchamia `main()` i zwraca kod wyjścia procesu: 0 = jest wynik; 1 = ZWERYFIKOWANY brak wyników
    (BrakWynikow); 2 = wszystko inne — błąd sieci/HTTP, nieoczekiwana odpowiedź, zła sygnatura, błąd argumentów,
    blokada --strict, VerificationUnknown i nieprzewidziany wyjątek (zamiast śladu stosu z kodem 1). `main()`
    zostaje bez zmian (SystemExit z komunikatem) — tak wołają go testy i inne skrypty w tym samym procesie."""
    if argv is not None:
        sys.argv = [sys.argv[0]] + list(argv)
    try:
        main()
    except BrakWynikow as e:
        print(e.code, file=sys.stderr)
        return 1
    except SystemExit as e:
        if e.code is None or isinstance(e.code, int):
            return e.code or 0
        print(e.code, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Przerwano.", file=sys.stderr)
        return 2
    except BrokenPipeError:
        # odbiorca zamknął wyjście (`tekst … | head`) — to nie błąd narzędzia; bez tego Python dopisałby ślad stosu
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0
    except Exception as e:  # nieprzewidziany błąd nigdy nie może dać kodu 1 („brak wyników")
        print(f"BŁĄD: nieoczekiwany błąd eli.py ({type(e).__name__}: {e}) — wynik NIEZNANY; spróbuj ponownie, "
              "a jeśli błąd się powtarza, zgłoś go z tą komendą.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(kod_wyjscia())
