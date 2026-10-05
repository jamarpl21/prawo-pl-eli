#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO API ELI Sejmu (https://api.sejm.gov.pl/eli).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET). Źródło pierwotne prawa polskiego: Dziennik Ustaw (DU) i Monitor Polski (MP).

Komendy:
  szukaj ["<fraza>"] [--typ T] [--rok R] [--wyd DU|MP] [--haslo H] [--obowiazujace] [--limit N] [--offset N]
  meta <sygnatura...>            np. meta DU 2024 18  |  meta "Dz.U. 2024 poz. 18"  |  meta WDU20240000018
  tekst <sygnatura...> [--fragment "art. 299"] [--pdf ŚCIEŻKA]
                                 tekst aktu (text.html → czysty tekst; gdy API nie ma HTML — własny urzędowy
                                 PDF aktu przez `pdftotext -layout`, jeśli jest w PATH); --fragment wycina
                                 tylko jednostki z frazą (np. jeden artykuł); --pdf zapisuje urzędowy PDF
  struktura <sygnatura...> [--filtr F] [--poziom N]   spis jednostek redakcyjnych aktu (z /struct)
  odniesienia <sygnatura...>     nowelizacje, tekst jednolity, podstawa prawna
  tj <sygnatura...>              znajduje AKTUALNY TEKST JEDNOLITY dla aktu i podaje jego sygnaturę
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik PRZED emisją, gdy nie udało się zweryfikować aktualności lub
                      kompletności: nowszy t.j., awaria kontroli, tekst ze STARSZEGO t.j. zamiast własnego
                      PDF, niepełna lista nowelizacji; NIE wykrywa zmian przepisu po stanie prawnym t.j.)
"""
import sys, json, re, time, argparse, shutil, subprocess, tempfile, os, datetime, hashlib
import concurrent.futures
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser
from html import unescape as _unescape

__version__ = "2.1.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://api.sejm.gov.pl/eli"
CONTENT_HOSTS = ("api.sejm.gov.pl",)
# Pamięć podręczna udanych GET-ów bez parametrów (metadane, odniesienia) w obrębie jednego
# uruchomienia — te same odniesienia aktu bazowego potrzebuje kontrola nowszego t.j. i lista
# nowelizacji; zapora api.sejm.gov.pl źle znosi powtórzone żądania.
_CACHE = {}


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


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
        if tag in ("script", "style"):
            self.skip += 1
        klasa = dict(attrs).get("class", "") or ""
        if tag in self.BLOKI:
            self._granica()
        if tag == "a" and self.gloss:
            self.gloss += 1
        elif tag == "a" and "gloss-link" in klasa:
            self.gloss, self.nr, self.tresc = 1, [], []
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
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
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
                self.nr, self.tresc = [], []

    def handle_data(self, data):
        if self.skip:
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
_PDF_PRZYPIS = re.compile(r"^(\d{1,3})\)(?:\s+(.*))?$")
# glued odsyłacz do przypisu: „§ 1.3)", „(uchylony)5)", „chemicznych2)" — cyfry + „)" sklejone
# z poprzedzającym znakiem, który nie jest spacją, cyfrą ani nawiasem otwierającym — także „<" z tekstu
# ujednoliconego: „<6) udostępnianie…" to punkt 6 w brzmieniu przyszłym, nie odsyłacz do przypisu 6
_PDF_ODSYLACZ = re.compile(r"(?<=[^\s\d\[(„\"'«<])(\d{1,3})\)")
_PDF_FRAZY_PRZYPISU = ("przez art.", "weszła w życie", "wszedł w życie", "wchodzi w życie", "Dodany ",
                       "W brzmieniu", "Uchylony", "Ze zmianą", "Zmiany tekstu", "odnośniku", "Obecnie",
                       "kieruje działem", "wdraża dyrektyw")
# początek NOWEGO akapitu (jednostki redakcyjnej) — tylko to nie jest doklejane do poprzedniej linii
# „[" / „<" przed jednostką: tekst ujednolicony Kancelarii Sejmu (PDF typu U) otacza brzmienie zastępowane
# nawiasem kwadratowym, a brzmienie, które dopiero wejdzie w życie — ostrym („[§ 1[1]. …]", „<§ 1[1]. …>")
_PDF_NOWY_AKAPIT = re.compile(
    r"^[„\"\[<]?(Art\.\s*\d|§\s*\d|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|Księga\s|KSIĘGA\s|"
    r"Oddział\s|ODDZIAŁ\s|Załącznik|Część\s|CZĘŚĆ\s|Preambuła|PREAMBUŁA|\d+[a-z]?\)\s|[a-z]\)\s|–\s|\d+[a-z]?\.\s)")
# jednostka na LEWYM marginesie (pkt/lit./tiret mają wysunięty numer; „Art."/„§" bywają niewcięte)
_PDF_JEDNOSTKA = re.compile(
    r"^[\[<]?(Art\.\s*\d|§\s*\d|\d+[a-z]?\)\s|[a-z]\)\s|–\s|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|"
    r"Księga\s|KSIĘGA\s|Oddział\s|Załącznik)")
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


def _pdf_slowa(bbox_html):
    """Wynik `pdftotext -bbox` → [(szerokość, wysokość, [(x0, y0, x1, y1, słowo)])] dla każdej strony."""
    strony = []
    for kawalek in bbox_html.split("<page ")[1:]:
        w = re.search(r'width="([\d.]+)" height="([\d.]+)"', kawalek)
        if not w:
            return []
        slowa = [(float(a), float(b), float(c), float(d), _unescape(t))
                 for a, b, c, d, t in _PDF_SLOWO.findall(kawalek)]
        strony.append((float(w.group(1)), float(w.group(2)), slowa))
    return strony


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


def _pdf_oznacz_indeksy(tekst, slowa):
    """Strona z `pdftotext -layout` → indeksy górne sklejone z liczbą („Art. 131." = art. 13¹) oznaczone jak
    w PDF z nawiasami („Art. 13[1]."). Pary (liczba, indeks) pochodzą z bbox tej strony (`slowa` — tylko te,
    które są w tekście strony); w tekście są szukane po kolei, razem ze słowem poprzedzającym w wierszu."""
    pos = 0
    for i in range(1, len(slowa)):
        a, b = slowa[i - 1], slowa[i]
        if not _pdf_indeks_gorny(a, b):
            continue
        idx = _PDF_INDEKS.match(b[4]).group(1)
        prev = slowa[i - 2] if i >= 2 and abs(slowa[i - 2][1] - a[1]) < 3 and 0 <= a[0] - slowa[i - 2][2] < 20 else None
        wzor = re.compile((re.escape(prev[4]) + "[ ]+" if prev else r"(?:^|(?<=[\s(„]))")
                          + "(" + re.escape(a[4]) + ")" + re.escape(idx) + r"(?![\d\[])", re.M)
        m = wzor.search(tekst, pos)
        if not m:
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
            continue        # przycięcie zgubiłoby inną treść za kolumną notek — strona zostaje w całości
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


def pdf_do_tekstu_z_notkami(pdf_bytes, zeszyt=False):
    """Jak `pdf_do_tekstu_layout`, ale strony z notkami na prawym marginesie (tekst ujednolicony
    Kancelarii Sejmu) są czytane PRZYCIĘTE tuż przed kolumną notek, a notki wracają osobno. Indeksy górne
    sklejone przez `-layout` z liczbą („Art. 131.") są oznaczone jak w PDF z nawiasami („Art. 13[1].").
    `zeszyt` (PDF zeszytu Dz.U./M.P. 1990–2011, dwa łamy): bez notek i bez oznaczania indeksów — wstawiony
    „[1]" przesunąłby prawy łam wiersza względem rynny, którą `_pdf_lamy` dzieli stronę.

    Zwraca (surowy tekst, {nr strony: {k: [notka]}}, info) — patrz `_pdf_margines`."""
    raw = pdf_do_tekstu_layout(pdf_bytes)
    if not raw or zeszyt:
        return raw, {}, {}
    strony = _pdf_slowa(_pdftotext(["-bbox", "-enc", "UTF-8"], pdf_bytes))
    _, marg, info = _pdf_margines(strony)
    caly, notki, przyciecie = raw.split("\f"), {}, {}
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
    if len(strony) <= len(caly):
        for nr, (w, h, slowa) in enumerate(strony):
            na_stronie = [s for s in slowa if s[0] < przyciecie.get(nr, w + 1)]
            caly[nr] = _pdf_rozstrzelone(_pdf_oznacz_indeksy(caly[nr], na_stronie), na_stronie)
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


def _pdf_strona(page, pierwsza, info=None, notki_bbox=None):
    """Jedna strona z `pdftotext -layout` → (linie treści [(wcięcie, tekst, notki)], przypisy {nr: treść}).

    Usuwa nagłówek („©Kancelaria Sejmu  s. 1/119", „Dziennik Ustaw – 22 – Poz. 795"), stopkę
    (znacznik daty „2026-06-22", numer strony), normalizuje margines strony i wydziela blok
    przypisów z dołu strony (po pustej linii; „3)   W brzmieniu ustalonym…" + wcięte kontynuacje).
    Notki z prawego marginesu (tekst ujednolicony) wychodzą osobno przy wierszu, obok którego stoją;
    „Opracowano na podstawie…" i data wydruku trafiają do `info` („podstawa", „data_wydruku").
    `notki_bbox` ({k: [notka]} z `_pdf_notki_z_bbox`; strona już przycięta do szerokości treści)
    zastępuje rozpoznawanie notek po odstępach w wierszu.
    """
    lines = page.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
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
    # stopka: znacznik daty / numer strony na samym dole
    while lines and (not lines[-1].strip() or _PDF_STOPKA.match(lines[-1])):
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
        if (set(numery) & odsylacze) or any(f in tekst_bloku for f in _PDF_FRAZY_PRZYPISU):
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


def pdf_layout_do_tekstu(raw, info=None, notki=None):
    """Tekst z `pdftotext -layout` → tekst w układzie jak z text.html.

    Strony są czyszczone z nagłówków/stopek, zawinięte wiersze są sklejane w akapity (nowy akapit
    zaczyna się od wciętego „Art."/„§"/„N)"/„lit."…, kontynuacja stoi na lewym marginesie),
    a odsyłacze do przypisów („§ 1.3)") znikają z numeracji — treść przypisu wychodzi pod
    akapit jako „[przypis 3)] …", dokładnie jak w ścieżce HTML. Notki z prawego marginesu tekstu
    ujednoliconego wychodzą pod akapitem jako „[margines: …]"; `info` (dict) dostaje „podstawa"
    (notka „Opracowano na podstawie…") i „data_wydruku". `notki` — z `pdf_do_tekstu_z_notkami`.
    """
    strony = raw.split("\f")
    akapity = []        # [wcięcie_nagłówka(bool), tekst, {strony}, [notki marginesu]]
    przypisy = {}       # nr strony → {nr przypisu: treść}
    poprzedni_naglowek = False
    for nr, strona in enumerate(strony):
        wiersze, przyp = _pdf_strona(strona, pierwsza=(nr == 0), info=info,
                                     notki_bbox=(notki or {}).get(nr) if notki else None)
        if przyp:
            przypisy[nr] = przyp
        for wciecie, t, notki_wiersza in wiersze:
            naglowek = wciecie >= _PDF_WCIECIE_NAGLOWKA and len(t) <= _PDF_MAKS_NAGLOWEK
            if naglowek:
                t = _zlacz_rozstrzelone(t)
            nowy = (naglowek or poprzedni_naglowek or not akapity
                    or (wciecie > 0 and _PDF_NOWY_AKAPIT.match(t)) or _PDF_JEDNOSTKA.match(t))
            if nowy:
                akapity.append([naglowek, t, {nr}, list(notki_wiersza)])
            else:
                akapity[-1][1] = _doklej(akapity[-1][1], t)
                akapity[-1][2].add(nr)
                akapity[-1][3].extend(notki_wiersza)
            poprzedni_naglowek = naglowek
    if any(a[3] for a in akapity):
        _przypnij_notki(akapity)
    # Tekst jednolity zaczyna się od OBWIESZCZENIA Marszałka Sejmu (z cytowanymi przepisami
    # przejściowymi: „Art. 3. Ustawa wchodzi w życie…"). To nie jest treść aktu, a na początku
    # linii udawałoby nagłówek artykułu — wiersze obwieszczenia dostają znacznik „» ".
    zal = next((i for i, a in enumerate(akapity[:400]) if a[1].lower().startswith("załącznik do obwieszczenia")), None)
    if zal and any("jednolitego tekstu" in a[1] for a in akapity[:zal]):
        for a in akapity[:zal]:
            a[1] = "» " + a[1]
        akapity.insert(0, [True, "[obwieszczenie Marszałka Sejmu sprzed załącznika — wiersze ze znakiem » NIE są treścią aktu]", set(), []])
    out = []
    for naglowek, t, na_stronach, notki_akapitu in akapity:
        t = _pdf_normalizuj_indeksy(t)
        odsylacze = []
        t = _PDF_ODSYLACZ.sub(lambda m: odsylacze.append(m.group(1)) or "", t)
        if naglowek or re.match(r"^[\[<]?Art\.\s*\d", t):
            out.append("")
        out.append(t)
        for n in odsylacze:
            tresc = None
            for s in sorted(na_stronach) + [max(na_stronach) + 1, min(na_stronach) - 1]:
                if n in przypisy.get(s, {}):
                    tresc = przypisy[s][n]
                    break
            out.append(f"[przypis {n})] {tresc}" if tresc else f"[przypis {n})] (treści przypisu nie odnaleziono na tej stronie PDF)")
        for n in notki_akapitu:
            out.append(f"[margines: {_pdf_normalizuj_indeksy(n)}]")
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


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
        out.append(l)
    return "\n".join(out)


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
    """
    lines = strona.split("\n")
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
        return ws, []
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


def _pdf_wytnij_akt(raw, poz, ocr=False):
    """Tekst zeszytu → tylko akt poz. `poz`: od wiersza z samym numerem pozycji (nad tytułem aktu) do
    wiersza z numerem następnej pozycji albo do końca. Bez znalezionego numeru tekst wraca bez zmian.
    `ocr` (lata 90.): numer następnej pozycji może mieć jedną cyfrę źle odczytaną („600" zamiast „500")."""
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


def _pdf_bez_stopki_zeszytu(raw, ocr=False):
    """Usuwa stopkę wydawniczą z ostatniej strony zeszytu (tej z „ISSN …"); bez niej tekst wraca bez zmian.
    Działa na wyniku `-layout` PRZED rozdzieleniem łamów — stopka jest na całą szerokość strony, pod oboma łamami.
    `ocr` (lata 90.): początek stopki także po frazach `_PDF_STOPKA_ZESZYTU_OCR`."""
    def poczatek(l):
        return _PDF_STOPKA_ZESZYTU.search(l) or ocr and _PDF_STOPKA_ZESZYTU_OCR.search(re.sub(r"\s", "", l))
    strony = raw.split("\f")
    nr = next((i for i in range(len(strony) - 1, -1, -1) if re.search(r"ISSN\s*\d{4}-\d{3}[\dX]", strony[i])), None)
    if nr is None:
        return raw
    for i in range(nr, -1, -1):
        linie = strony[i].split("\n")
        k = next((j for j, l in enumerate(linie) if poczatek(l)), None)
        if k is not None:
            strony[i] = "\n".join(linie[:k])
            for j in range(i + 1, nr + 1):
                strony[j] = ""
            return "\f".join(strony)
        if i < nr - 1:      # stopka nie zaczyna się dalej niż stronę przed tą z ISSN
            break
    return raw


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


def pdf_zeszyt_do_aktu(raw, rok, poz):
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
    return _pdf_wytnij_akt(raw, poz, ocr=rok < 2000) if poz else raw


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
_SUP = str.maketrans(_SUPS, "1234567890")

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


def _hity_naglowka(txt, fraza):
    """Pozycje NAGŁÓWKÓW artykułu wskazanego frazą ("art. 299", "art. 21¹", "art. 66c").

    Pusta lista = fraza nie jest oznaczeniem artykułu ALBO tego artykułu nie ma w akcie.
    """
    # Baza + opcjonalny INDEKS GÓRNY (art. 21¹: unicode "21¹", nawiasowy "21(1)"/"21[1]"/"21^1", z /struct "21_1")
    # + opcjonalny SUFIKS LITEROWY (art. 1a, art. 168e). Rozróżnienie jest istotne, bo w tekście
    # indeks górny ma spację ("Art. 21 1." — patrz _Stripper), a sufiks literowy jest sklejony
    # ("Art. 1a."); dlatego indeks matchujemy z \s+, a literę z \s*.
    m = re.match(
        r"(?i)^art\.?\s*(\d+[a-z]*?)"                           # (1) baza (też z literą przed indeksem: „6b^3")
        r"(?:[\(\[\^_]\s*(\d+[a-z]?)\s*[\)\]]?|([" + _SUPS + r"]+))?"  # (2) nawiasowy | (3) unicode indeks
        r"([a-z]*)\.?$",                                        # (4) sufiks literowy
        fraza.strip())
    if not m:
        return []
    base = m.group(1)
    idx = m.group(2) or (m.group(3).translate(_SUP) if m.group(3) else "")
    letter = m.group(4) or ""
    # "art. 130(1a)" → indeks "1" + litera "a"; w tekście i one bywają rozdzielone
    # („Art. 130 1 a."), więc literę doklejamy z \s*, nie na sztywno.
    mi = re.match(r"(\d+)([a-z]*)$", idx)
    if mi:
        idx, letter = mi.group(1), letter or mi.group(2)
    if idx:                       # indeks górny → w tekście rozdzielony spacją
        pat = rf"(?m)^[\[<]?Art\.\s*{re.escape(base)}\s+{re.escape(idx)}\s*{re.escape(letter)}{_KONIEC_ART}"
    elif letter:                  # sufiks literowy → sklejony z numerem
        pat = rf"(?m)^[\[<]?Art\.\s*{re.escape(base)}\s*{re.escape(letter)}{_KONIEC_ART}"
    else:
        pat = rf"(?m)^[\[<]?Art\.\s*{re.escape(base)}{_KONIEC_ART}"
    return [h.start() for h in re.finditer(pat, txt)]


def _fragmenty(txt, fraza, maks=8):
    """Spany (start, end) fragmentów z frazą, docięte do granic jednostek redakcyjnych.

    Fraza w formie "art. 299" trafia w NAGŁÓWEK artykułu (nie w odesłania w treści);
    inna fraza działa jak wyszukiwanie pełnotekstowe (bez rozróżniania wielkości liter).
    Gdy nagłówka nie ma, fraza jest ponawiana pełnotekstowo — lepiej pokazać odesłanie
    niż odpowiedzieć „nie znaleziono" na przepis, który w akcie jest.
    """
    bounds = [m.start() for m in re.finditer(_GRANICE, txt)]
    hits = _hity_naglowka(txt, fraza)
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
        start = max((b for b in bounds if b <= pos), default=max(0, pos - 400))
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
            ostatnie = (list(_WEJDZIE.finditer(l)) or list(_WEJDZIE_BEZ_DATY.finditer(l)))[-1]
            poczatek = n.group(2) if n else l
            opis = (f"przepis z przypisem {n.group(1) if n else '?'}) {ostatnie.group(0)}"
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


def _cytat(d):
    """Urzędowa forma cytatu publikatora: „Dz. U. z 2001 r. Nr 112, poz. 1198" / „Dz. U. z 2024 r. poz. 18"."""
    m = re.match(r"^(Dz\.U\.|M\.P\.)\s+(\d{4})\s+(?:nr\s+(\d+)\s+)?poz\.\s+(\d+)$", (d.get("displayAddress") or "").strip())
    if not m:
        return ""
    pub = "Dz. U." if m.group(1) == "Dz.U." else "M.P."
    return f"{pub} z {m.group(2)} r. " + (f"Nr {m.group(3)}, " if m.group(3) else "") + f"poz. {m.group(4)}"


def _jeszcze_nie_obowiazuje(d, dzis=None):
    """Ostrzeżenia dla aktu w vacatio legis / z jednostkami wchodzącymi w życie później (lista linii)."""
    dzis = dzis or _dzis()
    out = []
    eif = d.get("entryIntoForce") or ""
    if eif and eif > dzis:
        out.append(f"UWAGA: akt jeszcze NIE WSZEDŁ W ŻYCIE — wejście w życie {eif} (dziś {dzis}). Status API "
                   f"„{d.get('status', '')}” znaczy tylko, że akt nie został uchylony; do tego dnia jego przepisy nie obowiązują.")
    komentarz = " ".join(str(d.get("comments") or "").split())
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
        if (ogloszono and ogloszono > stan_prawny) or (wejscie and wejscie > stan_prawny):
            out.append(r)
    return out


_MAKS_DOPYTAN = 10


def _nowelizacje_po_tj(refs, path, strict=False):
    """Nowelizacje po tekście jednolitym: własna kategoria t.j. UZUPEŁNIONA o „Akty zmieniające"
    aktu bazowego po `legalStatusDate` t.j. (Sejm nie synchronizuje obu list — audyt 2026-08:
    k.p.c. t.j. 2026/468 wykazywał 2 z 5, k.s.h. 2024/18 — 1 z 4). Zwraca (linie, uwagi).

    Każda pozycja ma daty opisane jednoznacznie: data aktu / ogłoszono / wejście w życie zmiany.
    Awaria dopytań: w strict → VerificationUnknown, inaczej uwaga o możliwej niekompletności.
    """
    wlasne = _lista(refs, "nowelizacje po tekście jednolit")
    base = _akt_bazowy(refs)
    if not (wlasne or base):
        return [], []
    uwagi = []
    stan_prawny = ""
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
            if stan_prawny:
                for r in _zmiany_bazowe_po(base["ELI"], stan_prawny):
                    act = r["act"]
                    data_aktu, ogloszono = _daty_aktu(act)
                    poz = pozycje.setdefault(act.get("ELI"), {"act": act, "data_aktu": data_aktu,
                                                              "ogloszono": ogloszono, "wejscie": "", "etykieta": ""})
                    poz["wejscie"], poz["etykieta"] = r.get("date") or "", "wejście w życie zmiany"
                zrodla.append(f"„Akty zmieniające\" aktu bazowego {_akt_opis(base)} ogłoszone lub "
                              f"wchodzące w życie po {stan_prawny}")
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
        linie.append(f"  - {_akt_opis(act)}  {act.get('title', '')}".rstrip() + "  (" + ", ".join(daty) + ")")
        if poz.get("uwagi"):
            linie.append(f"      uwagi: {poz['uwagi']}")
    if linie:
        naglowek = (f"UWAGA: po tym tekście jednolitym" + (f" (stan prawny na {stan_prawny})" if stan_prawny else "")
                    + f" odnotowano zmiany ({len(linie) - sum(1 for l in linie if l.startswith('      uwagi'))})"
                    " — sprawdź ich wejście w życie" + (f" [źródła: {'; '.join(zrodla)}]" if zrodla else "") + ":")
        linie.insert(0, naglowek)
    return linie, uwagi


def _ostrzezenia(refs, path=None, strict=False):
    """Ostrzeżenia o aktualności (lista linii) na podstawie odniesień aktu."""
    out = []
    tj = _tj_acts(refs)
    if tj:
        a = tj[0]
        out.append(f"UWAGA: ten akt ma TEKST JEDNOLITY — cytuj z najnowszego: "
                   f"{a.get('displayAddress') or a.get('ELI', '')} (ELI {a.get('ELI', '')}).")
    linie, uwagi = _nowelizacje_po_tj(refs, path, strict)
    return out + uwagi + linie


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


def act_path(sig_parts):
    """Zwraca ścieżkę bazową aktu, akceptując różne formy sygnatury."""
    s = " ".join(sig_parts).strip()
    compact = re.sub(r"\s+", "", s)
    # ISAP address, np. WDU20240000018 (W + DU/MP + 9–12 cyfr)
    m = re.match(r"^(W?)(DU|MP)(\d{9,12})$", compact, re.I)
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


def cmd_szukaj(a):
    if not (a.fraza or a.haslo):
        sys.exit("Podaj frazę tytułu (np. szukaj \"Kodeks cywilny\") albo --haslo.")
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
        # zero trafień = komunikat + kod wyjścia ≠ 0 (także z --json) — pusty JSON wyglądałby jak
        # „sprawdzone, nic nie ma", a to tylko brak dopasowania TEJ frazy/filtrów
        sys.exit(f"Brak wyników (totalCount={total}, offset {a.offset}) dla: fraza={a.fraza!r}, typ={a.typ!r}, "
                 f"rok={a.rok!r}, haslo={a.haslo!r}. To NIE dowodzi, że aktu nie ma — spróbuj krótszej frazy "
                 "z tytułu, bez --typ/--rok, albo --haslo.")
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
    if "uchylon" in (d.get("status") or "").lower():
        try:
            refs = _get(path + "/references", soft=True)
            uchylajace = _lista(refs, "akty uchylające") if isinstance(refs, dict) else []
        except VerificationUnknown:
            uchylajace = None
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
        print(f"  Stan prawny na: {d['legalStatusDate']}   (tekst jednolity oddaje stan na ten dzień)")
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


def _ocr_strona(pdf_path, nr, klucz):
    """OCR jednej strony (nr od 1) → tekst; wynik zapamiętany na dysku (ten sam PDF = ten sam wynik)."""
    plik = os.path.join(_ocr_katalog(), f"{klucz}-{nr}-{_OCR_DPI}.txt")
    try:
        with open(plik, encoding="utf-8") as f:
            return f.read()
    except OSError:
        pass
    with tempfile.TemporaryDirectory() as tmp:
        obraz = os.path.join(tmp, "s")
        subprocess.run(["pdftoppm", "-f", str(nr), "-l", str(nr), "-r", str(_OCR_DPI), "-gray", "-png",
                        "-singlefile", pdf_path, obraz], capture_output=True, timeout=180, check=True)
        r = subprocess.run(["tesseract", obraz + ".png", "-", "-l", "pol", "--psm", "1"],
                           capture_output=True, timeout=300, check=True)
    # tesseract czyta „§" jako „$" (aktów prawnych nie pisze się w dolarach): „$ 2", „$2." → „§ 2", „§2."
    tekst = re.sub(r"\$(?=\s?\d)", "§", r.stdout.decode("utf-8", "replace"))
    try:
        os.makedirs(_ocr_katalog(), exist_ok=True)
        with open(plik, "w", encoding="utf-8") as f:
            f.write(tekst)
    except OSError:
        pass        # brak zapisu (piaskownica) nie przeszkadza — następnym razem OCR od nowa
    return tekst


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
    raw, notki, info_pdf = pdf_do_tekstu_z_notkami(data, zeszyt=zeszyt)
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
                for i, t in odczytane.items():
                    kawalki[i] = t.replace("\f", "")
                raw = "\f".join(kawalki)
                info["ocr_strony"] = (len(odczytane), len(strony))
                puste_idx = [i for i in puste_idx if i not in odczytane]
        else:
            info["ocr_pominiete"] = (len(puste_idx), len(puste_idx) * _OCR_SEK_NA_STRONE)
    puste = len(puste_idx)
    if raw and puste:
        info["puste_strony"] = (puste, len(strony))
    if raw and zeszyt:
        raw = pdf_zeszyt_do_aktu(raw, rok, meta.get("pos"))
        if rok < 2000:
            info["ocr"] = True      # warstwa tekstowa zeszytów z lat 90. to OCR skanu
    txt = pdf_layout_do_tekstu(raw, info, notki) if raw else ""
    if not txt:
        rada = (f"; odczyt OCR: tekst {label} --ocr (ok. {info['ocr_pominiete'][1] // 60 + 1} min)"
                if info.get("ocr_pominiete") else "")
        return "", url, "pdftotext nie zwrócił tekstu (PDF bez warstwy tekstowej albo błąd konwersji)" + rada
    return txt, url, ""


def cmd_tekst(a):
    strict = getattr(a, "strict", False)
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
    else:
        ostrz = _ostrzezenia(refs, path, strict) if isinstance(refs, dict) else []
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
        with open(a.pdf, "wb") as f:
            f.write(data)
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
    print(f"# {label} — tekst ({zrodlo}; do dosłownego cytatu zweryfikuj z PDF urzędowym)\n")
    for w in ostrz:
        print(w)
    if ostrz:
        print()
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            goly = re.sub(r"(?i)^art\.?\s*", "", a.fragment.strip()) or "N"
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w tekście aktu ({len(txt)} znaków).\n"
                     "UWAGA: to NIE dowodzi, że przepisu nie ma — zanim tak napiszesz, sprawdź samym "
                     f"numerem (--fragment \"{goly}\"), słowem kluczowym z treści "
                     "albo pobierz pełny tekst bez --fragment.")
        # tryb nagłówkowy zawiódł → poniżej trafienia pełnotekstowe, więc mogą to być ODESŁANIA
        if re.match(r"(?i)^art\.?\s*\d", a.fragment.strip()) and not _hity_naglowka(txt, a.fragment):
            print(f"UWAGA: nie znalazłem NAGŁÓWKA {a.fragment!r} w tym akcie — poniżej trafienia "
                  "pełnotekstowe; sprawdź, czy to sam przepis, czy tylko odesłanie do niego.\n")
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
        for w in kontekst + _ostrzezenie_przyszle("\n".join(dodatkowe + [txt[s:e] for s, e in spans]), ujednolicony):
            print(w)
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(txt[s:e].strip())
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
        sys.exit(f"Brak struktury dla tego aktu ({label}) — API udostępnia /struct głównie dla tekstów "
                 f"jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają. "
                 f"Spis jednostek odczytasz z tekstu: tekst {label} | grep -n \"^Art\\.\"")
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
        print(f"(nic nie pasuje do filtra {a.filtr!r})")


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
    t.add_argument("--fragment", help='wytnij tylko jednostki z frazą, np. "art. 299" albo "przedawnienie"')
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

    a = ap.parse_args()
    try:
        a.func(a)
    except VerificationUnknown as e:
        _nie_zweryfikowano("danych w API ELI", e)


if __name__ == "__main__":
    main()
