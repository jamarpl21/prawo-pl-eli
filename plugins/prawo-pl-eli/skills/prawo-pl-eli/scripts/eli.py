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
import sys, json, re, time, argparse, shutil, subprocess, tempfile, os
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "1.7.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
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
_PDF_NAGLOWEK = re.compile(r"^\s*(©\s*Kancelaria Sejmu|(Dziennik Ustaw|Monitor Polski)\s+–\s*\d+\s*–)")
_PDF_STOPKA = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|–?\s*\d{1,4}\s*–?)\s*$")
_PDF_PRZYPIS = re.compile(r"^(\d{1,3})\)(?:\s+(.*))?$")
# glued odsyłacz do przypisu: „§ 1.3)", „(uchylony)5)", „chemicznych2)" — cyfry + „)" sklejone
# z poprzedzającym znakiem, który nie jest spacją, cyfrą ani nawiasem otwierającym
_PDF_ODSYLACZ = re.compile(r"(?<=[^\s\d\[(„\"'«])(\d{1,3})\)")
_PDF_FRAZY_PRZYPISU = ("przez art.", "weszła w życie", "wszedł w życie", "wchodzi w życie", "Dodany ",
                       "W brzmieniu", "Uchylony", "Ze zmianą", "Zmiany tekstu", "odnośniku", "Obecnie",
                       "kieruje działem", "wdraża dyrektyw")
# początek NOWEGO akapitu (jednostki redakcyjnej) — tylko to nie jest doklejane do poprzedniej linii
_PDF_NOWY_AKAPIT = re.compile(
    r"^[„\"]?(Art\.\s*\d|§\s*\d|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|Księga\s|KSIĘGA\s|"
    r"Oddział\s|ODDZIAŁ\s|Załącznik|Część\s|CZĘŚĆ\s|Preambuła|PREAMBUŁA|\d+[a-z]?\)\s|[a-z]\)\s|–\s|\d+[a-z]?\.\s)")
# jednostka na LEWYM marginesie (pkt/lit./tiret mają wysunięty numer; „Art."/„§" bywają niewcięte)
_PDF_JEDNOSTKA = re.compile(
    r"^(Art\.\s*\d|§\s*\d|\d+[a-z]?\)\s|[a-z]\)\s|–\s|Rozdział\s|ROZDZIAŁ\s|Dział\s|DZIAŁ\s|Tytuł\s|TYTUŁ\s|"
    r"Księga\s|KSIĘGA\s|Oddział\s|Załącznik)")
_PDF_WCIECIE_NAGLOWKA = 16   # krótki wiersz wcięty co najmniej tyle = wyśrodkowany nagłówek (DZIAŁ IV)
_PDF_MAKS_NAGLOWEK = 70


def pdftotext_dostepny():
    return shutil.which("pdftotext") is not None


def pdf_do_tekstu_layout(pdf_bytes):
    """`pdftotext -layout` na bajtach PDF → surowy tekst (pusty napis przy awarii)."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp = f.name
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", tmp, "-"],
                           capture_output=True, timeout=180)
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


def _pdf_strona(page, pierwsza):
    """Jedna strona z `pdftotext -layout` → (linie treści [(wcięcie, tekst)], przypisy {nr: treść}).

    Usuwa nagłówek („©Kancelaria Sejmu  s. 1/119", „Dziennik Ustaw – 22 – Poz. 795"), stopkę
    (znacznik daty „2026-06-22", numer strony), normalizuje margines strony i wydziela blok
    przypisów z dołu strony (po pustej linii; „3)   W brzmieniu ustalonym…" + wcięte kontynuacje).
    """
    lines = page.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    # nagłówek: pierwsze niepuste linie
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines) and _PDF_NAGLOWEK.match(lines[i]):
        i += 1
    lines = lines[i:]
    # stopka: znacznik daty / numer strony na samym dole
    while lines and (not lines[-1].strip() or _PDF_STOPKA.match(lines[-1])):
        lines.pop()
    if pierwsza and any("Opracowano na" in l for l in lines):
        # Pierwsza strona „tekstu ujednoliconego" Kancelarii Sejmu (np. Konstytucja) ma na prawym
        # marginesie notkę „Opracowano na podstawie: Dz. U. …", którą -layout dokleja do linii
        # preambuły — odcinamy wszystko, co zaczyna się w prawej ćwiartce strony
        szer = max(len(l.rstrip()) for l in lines) or 1
        prog = int(szer * 0.75)

        def bez_notki(l):
            if len(l) - len(l.lstrip(" ")) >= prog:
                return ""
            for m in re.finditer(r"\S( {6,})(?=\S)", l):
                if m.end() >= prog:
                    return l[:m.start() + 1]
            return l
        lines = [bez_notki(l) for l in lines]
    niepuste = [l for l in lines if l.strip()]
    if not niepuste:
        return [], {}
    margines = min(len(l) - len(l.lstrip(" ")) for l in niepuste)
    wiersze = [(0, "") if not l.strip() else (len(l) - len(l.lstrip(" ")) - margines, " ".join(l.split()))
               for l in lines]
    # blok przypisów: ostatni blok po pustej linii, zaczynający się od „N)" na marginesie
    przypisy = {}
    k = len(wiersze)
    while k > 0 and wiersze[k - 1][1]:
        k -= 1
    blok = wiersze[k:]
    if k > 0 and blok and blok[0][0] == 0 and _PDF_PRZYPIS.match(blok[0][1]):
        numery = [m.group(1) for w, t in blok if w == 0 for m in [_PDF_PRZYPIS.match(t)] if m]
        tresc_strony = "\n".join(t for w, t in wiersze[:k])
        odsylacze = set(_PDF_ODSYLACZ.findall(tresc_strony))
        tekst_bloku = " ".join(t for w, t in blok)
        if (set(numery) & odsylacze) or any(f in tekst_bloku for f in _PDF_FRAZY_PRZYPISU):
            biezacy = None
            for w, t in blok:
                m = _PDF_PRZYPIS.match(t) if w == 0 else None
                if m:
                    biezacy = m.group(1)
                    przypisy[biezacy] = m.group(2) or ""
                elif biezacy is not None:
                    przypisy[biezacy] = _doklej(przypisy[biezacy], t)
            wiersze = wiersze[:k]
    return [(w, t) for w, t in wiersze if t], przypisy


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
    # indeks górny w PDF: „Art. 68[1]." / „art. 385[1]–385[3]" / „68¹" → jak w ścieżce HTML: „Art. 68 1."
    t = re.sub(r"(?<=\d)\[(\d+[a-z]?)\]", r" \1", t)
    return re.sub(r"(?<=\d)([" + _SUPS + r"]+)", lambda m: " " + m.group(1).translate(_SUP), t)


def pdf_layout_do_tekstu(raw):
    """Tekst z `pdftotext -layout` → tekst w układzie jak z text.html.

    Strony są czyszczone z nagłówków/stopek, zawinięte wiersze są sklejane w akapity (nowy akapit
    zaczyna się od wciętego „Art."/„§"/„N)"/„lit."…, kontynuacja stoi na lewym marginesie),
    a odsyłacze do przypisów („§ 1.3)") znikają z numeracji — treść przypisu wychodzi pod
    akapit jako „[przypis 3)] …", dokładnie jak w ścieżce HTML.
    """
    strony = raw.split("\f")
    akapity = []        # [wcięcie_nagłówka(bool), tekst, {strony}]
    przypisy = {}       # nr strony → {nr przypisu: treść}
    poprzedni_naglowek = False
    for nr, strona in enumerate(strony):
        wiersze, przyp = _pdf_strona(strona, pierwsza=(nr == 0))
        if przyp:
            przypisy[nr] = przyp
        for wciecie, t in wiersze:
            naglowek = wciecie >= _PDF_WCIECIE_NAGLOWKA and len(t) <= _PDF_MAKS_NAGLOWEK
            if naglowek:
                t = _zlacz_rozstrzelone(t)
            nowy = (naglowek or poprzedni_naglowek or not akapity
                    or (wciecie > 0 and _PDF_NOWY_AKAPIT.match(t)) or _PDF_JEDNOSTKA.match(t))
            if nowy:
                akapity.append([naglowek, t, {nr}])
            else:
                akapity[-1][1] = _doklej(akapity[-1][1], t)
                akapity[-1][2].add(nr)
            poprzedni_naglowek = naglowek
    # Tekst jednolity zaczyna się od OBWIESZCZENIA Marszałka Sejmu (z cytowanymi przepisami
    # przejściowymi: „Art. 3. Ustawa wchodzi w życie…"). To nie jest treść aktu, a na początku
    # linii udawałoby nagłówek artykułu — wiersze obwieszczenia dostają znacznik „» ".
    zal = next((i for i, a in enumerate(akapity[:400]) if a[1].lower().startswith("załącznik do obwieszczenia")), None)
    if zal and any("jednolitego tekstu" in a[1] for a in akapity[:zal]):
        for a in akapity[:zal]:
            a[1] = "» " + a[1]
        akapity.insert(0, [True, "[obwieszczenie Marszałka Sejmu sprzed załącznika — wiersze ze znakiem » NIE są treścią aktu]", set()])
    out = []
    for naglowek, t, na_stronach in akapity:
        t = _pdf_normalizuj_indeksy(t)
        odsylacze = []
        t = _PDF_ODSYLACZ.sub(lambda m: odsylacze.append(m.group(1)) or "", t)
        if naglowek or re.match(r"^Art\.\s*\d", t):
            out.append("")
        out.append(t)
        for n in odsylacze:
            tresc = None
            for s in sorted(na_stronach) + [max(na_stronach) + 1, min(na_stronach) - 1]:
                if n in przypisy.get(s, {}):
                    tresc = przypisy[s][n]
                    break
            out.append(f"[przypis {n})] {tresc}" if tresc else f"[przypis {n})] (treści przypisu nie odnaleziono na tej stronie PDF)")
    t = "\n".join(out)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


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
_GRANICE = r"(?m)^(Art\.\s*\d|Tytuł\s|TYTUŁ\s|Dział\s|DZIAŁ\s|Rozdział\s|Oddział\s|Księga\s|KSIĘGA\s|Załącznik)"

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
    # Baza + opcjonalny INDEKS GÓRNY (art. 21¹: unicode "21¹", nawiasowy "21(1)"/"21[1]"/"21^1")
    # + opcjonalny SUFIKS LITEROWY (art. 1a, art. 168e). Rozróżnienie jest istotne, bo w tekście
    # indeks górny ma spację ("Art. 21 1." — patrz _Stripper), a sufiks literowy jest sklejony
    # ("Art. 1a."); dlatego indeks matchujemy z \s+, a literę z \s*.
    m = re.match(
        r"(?i)^art\.?\s*(\d+)"                                  # (1) baza
        r"(?:[\(\[\^]\s*(\d+[a-z]?)\s*[\)\]]?|([" + _SUPS + r"]+))?"  # (2) nawiasowy | (3) unicode indeks
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
        pat = rf"(?m)^Art\.\s*{re.escape(base)}\s+{re.escape(idx)}\s*{re.escape(letter)}{_KONIEC_ART}"
    elif letter:                  # sufiks literowy → sklejony z numerem
        pat = rf"(?m)^Art\.\s*{re.escape(base)}\s*{re.escape(letter)}{_KONIEC_ART}"
    else:
        pat = rf"(?m)^Art\.\s*{re.escape(base)}{_KONIEC_ART}"
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
    print(f"  Typ:     {d.get('type','')}")
    print(f"  Status:  {d.get('status','')}  (inForce={d.get('inForce','')})")
    # announcementDate = data AKTU (wydania/podpisania — „z dnia" w tytule), promulgation = data
    # OGŁOSZENIA w Dz.U./M.P. — vacatio legis liczy się od ogłoszenia (audyt 2026-08: mylono je)
    print(f"  Data aktu: {d.get('announcementDate') or '—'}   (data wydania — „z dnia” w tytule)")
    print(f"  Ogłoszono: {d.get('promulgation') or '— (brak w API)'}   (publikacja w Dz.U./M.P.)")
    print(f"  WEJŚCIE W ŻYCIE: {d.get('entryIntoForce') or '—'}")
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


def _tekst_z_pdf(path, label, meta):
    """Tekst aktu z jego WŁASNEGO urzędowego PDF (pdftotext -layout). Zwraca (tekst, url, błąd)."""
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
    raw = pdf_do_tekstu_layout(data)
    txt = pdf_layout_do_tekstu(raw) if raw else ""
    if not txt:
        return "", url, "pdftotext nie zwrócił tekstu (PDF bez warstwy tekstowej albo błąd konwersji)"
    return txt, url, ""


def cmd_tekst(a):
    strict = getattr(a, "strict", False)
    path, label = act_path(a.sygnatura)
    # odniesienia służą tylko ostrzeżeniom o aktualności — ich awaria nie może odebrać
    # użytkownikowi samego tekstu; zamiast tego tekst dostaje GŁOŚNE ostrzeżenie
    try:
        refs = _get(path + "/references", soft=True)
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
    html = _get(path + "/text.html")
    if isinstance(html, (dict, list)):
        html = json.dumps(html, ensure_ascii=False)
    txt = html_to_text(html if isinstance(html, str) else "")
    zrodlo = "z text.html; HTML→tekst"
    if not txt:
        # textHTML=false (np. k.c. DU 2026 795, Konstytucja DU 1997 483, świeże pozycje): najpierw
        # WŁASNY urzędowy PDF tego aktu — to jest jego tekst, więc --strict go przepuszcza
        meta = _expect_dict(_get(path), "metadane aktu")
        txt, url, pdf_blad = _tekst_z_pdf(path, label, meta)
        if txt:
            zrodlo = "z urzędowego PDF przez pdftotext -layout"
            ostrz = [f"ELI_TEXT_SOURCE_PDF={url}",
                     f"UWAGA: text.html dla {label} jest PUSTE w API (textHTML=false) — poniżej tekst "
                     f"WYEKSTRAHOWANY z urzędowego PDF tego aktu ({meta.get('legalStatusDate') and 'stan prawny na ' + meta['legalStatusDate'] or 'tekst ogłoszony'}). "
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
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(txt[s:e].strip())
        print(f"\n(fragmenty: {len(spans)} — pominięto resztę aktu; pełny tekst: bez --fragment)")
        return
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
    print(f"Struktura: {label}  (frazę z 'title' podaj w: tekst {label} --fragment \"...\")\n")
    wypisane = 0

    def walk(n, depth=0):
        nonlocal wypisane
        line = f"{'  ' * depth}{n.get('id','?')}  [{n.get('type','')}]  {(n.get('title') or '').strip()}"
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
