#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO repozytorium prawa UE: CELLAR/EUR-Lex Urzędu Publikacji UE.
SPARQL (wyszukiwanie, metadane, relacje) + REST (teksty aktów). Tylko biblioteka standardowa
Pythona — brak zależności pip. Operacje WYŁĄCZNIE read-only. Bez rejestracji i klucza API.

Komendy:
  szukaj "<fraza>" [--typ REG|DIR|DEC] [--rok R] [--jezyk pol] [--obowiazujace] [--limit N]
  meta <CELEX>                   metadane aktu (tytuł, typ, daty wejścia w życie / stosowania,
                                 termin transpozycji dyrektywy, czy obowiązuje, ELI); na wersji
                                 skonsolidowanej: data „stan na" + daty AKTU BAZOWEGO
  tekst <CELEX> [--jezyk pol] [--fragment "art. 6"] [--pdf ŚCIEŻKA]
                                 tekst aktu z CELLAR (XHTML/HTML → czysty tekst; akt tylko w PDF —
                                 z urzędowego PDF przez `pdftotext -layout`, jeśli jest w PATH);
                                 --fragment wycina tylko jednostki z frazą; --pdf zapisuje urzędowy
                                 PDF; na akcie bazowym ostrzega o sprostowaniach w danym języku
  skonsolidowany <CELEX>         wersje skonsolidowane aktu (odpowiednik tekstu jednolitego)
  odniesienia <CELEX>            nowelizacje, sprostowania, uchylenia (w obie strony), podstawa prawna
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności)

CELEX np.: 32016R0679 (RODO), 02016R0679-20160504 (wersja skonsolidowana), reg/2016/679 (ELI).
"""
import sys, os, json, re, time, argparse, shutil, subprocess, tempfile, textwrap
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.1.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "http://publications.europa.eu/resource/celex/"
CDM = "http://publications.europa.eu/ontology/cdm#"
LANG_AUTH = "http://publications.europa.eu/resource/authority/language/"
TYPE_AUTH = "http://publications.europa.eu/resource/authority/resource-type/"
XSD_STR = "http://www.w3.org/2001/XMLSchema#string"
CONTENT_HOSTS = ("publications.europa.eu", "data.europa.eu")

# Urzędowy cytat = akt bazowy w brzmieniu z Dz.U. UE ŁĄCZNIE ze sprostowaniami i aktami zmieniającymi.
# Sam „akt bazowy + zmiany" to tekst NIESPROSTOWANY (RODO art. 4 pkt 1 w PL: „informacje" zamiast
# „wszelkie informacje" — sprostowanie 32016R0679R(02)).
CYTAT_URZEDOWY = ("akt bazowy (Dz.U. UE) razem ze SPROSTOWANIAMI i aktami zmieniającymi "
                  "(lista: odniesienia <CELEX>)")

JEZYKI = {"pl": "POL", "pol": "POL", "en": "ENG", "eng": "ENG", "de": "DEU", "deu": "DEU",
          "fr": "FRA", "fra": "FRA", "es": "SPA", "spa": "SPA", "it": "ITA", "ita": "ITA",
          "cs": "CES", "ces": "CES", "sk": "SLK", "slk": "SLK", "nl": "NLD", "nld": "NLD",
          "pt": "POR", "por": "POR", "uk": "UKR", "ukr": "UKR"}


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


def _nie_zweryfikowano(co, blad):
    sys.exit(f"BŁĄD: nie udało się zweryfikować {co} ({blad}). "
             "Spróbuj ponownie za chwilę.")


def _lang(j):
    code = JEZYKI.get((j or "pol").lower())
    if code:
        return code
    if re.match(r"^[A-Za-z]{3}$", j or ""):
        return j.upper()
    sys.exit(f"Nieznany język: {j!r}. Użyj np. pol, eng, deu, fra (kod 3-literowy).")


def _wymus_https(url):
    """Podnosi http:// do https:// dla adresów, z których realnie pobieramy treść.

    CELLAR identyfikuje zasoby URI w formie ``http://`` i tak też zwraca adresy
    manifestacji w wynikach SPARQL — to poprawne jako *nazwa* zasobu, ale jako
    *transport* oznacza pobieranie tekstu aktu prawnego kanałem bez szyfrowania
    i bez uwierzytelnienia serwera. Treść trafia stąd do cytatów prawnych, więc
    podmiana w tranzycie jest realnym ryzykiem, nie teoretycznym.

    Podnosimy schemat wyłącznie tuż przed żądaniem; stałe URI przestrzeni nazw
    (CDM, LANG_AUTH, TYPE_AUTH, XSD_STR) zostają nietknięte, bo zmiana ich
    postaci zerwałaby dopasowanie w zapytaniach SPARQL.
    """
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed)
                    for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        # Zmieniamy wyłącznie schemat. Oryginalna pisownia hosta, user-info, port,
        # ścieżka, parametry, query i fragment pozostają bajt w bajt bez zmian.
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi przekierowania HTTP dla dozwolonych hostów treści, inne odrzuca.

    CELLAR odpowiada 303 z Location w formie http:// nawet na żądanie https. Bez
    kontroli celu przekierowania treść aktu mogłaby popłynąć czystym HTTP."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)


_opener = urllib.request.build_opener(_PrzekierowaniaHttps())


def _http(url, data=None, headers=None, timeout=60):
    """GET/POST z jednym ponowieniem na błąd przejściowy. Zwraca (bytes, content-type)."""
    url = _wymus_https(url)
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": f"eurlex-skill/{__version__}", **(headers or {})})
    for attempt in (1, 2):
        try:
            with _opener.open(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if e.code == 300:
                sys.exit(f"BŁĄD: CELLAR zwrócił 300 (wiele wariantów) dla {url} — "
                         "spróbuj --pdf albo inny --jezyk.")
            if e.code == 404:
                sys.exit(f"BŁĄD: nie znaleziono zasobu (404): {url}\n"
                         "Sprawdź numer CELEX (szukaj \"<fraza>\") i czy istnieje wersja w tym języku.")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e})")


def _sparql(query, soft=False):
    """Zapytanie SPARQL zwracające listę bindingów.

    Pusta lista oznacza VERIFIED_ABSENT. Przy soft=True błąd ma osobny stan
    UNKNOWN (VerificationUnknown), nigdy None/pustą listę.
    """
    data = urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"}).encode()
    try:
        raw, _ = _http(SPARQL, data=data, headers={"Accept": "application/sparql-results+json"})
        return json.loads(raw.decode("utf-8", "replace"))["results"]["bindings"]
    except SystemExit as e:
        if soft:
            raise VerificationUnknown(str(e)) from e
        raise
    except Exception as e:
        if soft:
            raise VerificationUnknown(f"nieoczekiwana odpowiedź SPARQL: {e}") from e
        sys.exit(f"BŁĄD: nieoczekiwana odpowiedź SPARQL ({e}) — spróbuj ponownie za chwilę.")


def _v(b, key):
    return b.get(key, {}).get("value", "")


# Znak granicy STRUKTURALNEJ (ASCII unit separator): _Stripper wstawia go w osobnej linii przed
# blokiem podpisów i przed każdym przypisem końcowym, bo w tekście bez znaczników nie da się
# odróżnić przypisu „(1) Dz.U. …" od punktu „(1) …" aktu zmieniającego (EN). Klasy XHTML CELLAR:
# akt bazowy — div.oj-signatory (podpisy), hr/p.oj-note (przypisy); wersja skonsolidowana —
# p.footnote. Znak jest usuwany przed wydrukiem (_bez_granic), nigdy nie trafia do cytatu.
GRANICA = "\x1f"
_KLASY_GRANIC = frozenset(("oj-signatory", "oj-note", "footnote"))
# Znak początku KOLEJNEGO DOKUMENTU w tym samym wydaniu Dz.U. (ASCII record separator): XHTML CELLAR
# oddziela akt od dokumentu dołączonego (umowa, protokół do decyzji o podpisaniu/zawarciu) przez
# <hr class="oj-doc-sep"/>. Bez niego „art. 2" decyzji 2006/370/WE i „Artykuł 2" załączonej Umowy
# wychodziły jako dwa wystąpienia tego samego aktu. Też usuwany przed wydrukiem (_bez_granic).
DOKUMENT = "\x1e"


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "title"):  # <title> starych HTML: „EUR-Lex - 31995L0046 - PL"
            self.skip += 1
        if tag in ("p", "div", "hr"):
            klasy = (dict(attrs).get("class") or "").split()
            if tag == "hr" and "oj-doc-sep" in klasy:
                self.out.append("\n" + DOKUMENT + "\n")
            if _KLASY_GRANIC.intersection(klasy):
                self.out.append("\n" + GRANICA + "\n")
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "title") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    """XHTML → czysty tekst; znaki GRANICA zostają (granice podpisów/przypisów dla --fragment)."""
    p = _Stripper()
    p.feed(html)
    # EUR-Lex używa twardych spacji (NBSP) — normalizuj, żeby frazy były wyszukiwalne
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _bez_granic(t):
    """Usuwa znaki granicy strukturalnej przed wydrukiem."""
    for znak in (GRANICA, DOKUMENT):
        t = t.replace(znak + "\n", "").replace(znak, "")
    return t


# Granice jednostek redakcyjnych w aktach UE (nagłówki na początku linii; PL/EN/DE/FR) oraz
# granice KOŃCA części normatywnej: formuła „Sporządzono w …", podpisy „W imieniu Parlamentu/
# Rady/Komisji", blok przypisów (znak GRANICA z XHTML; zapasowo linia „(1) Dz.U./OJ/ABl."),
# załączniki. Bez nich fragment OSTATNIEGO artykułu ciągnął za sobą podpisy i wszystkie
# przypisy aktu (RODO art. 99: 21 przypisów; AI Act art. 113: 58).
# Nagłówek artykułu to CAŁA linia („Artykuł 113"); linia zaczynająca się odesłaniem („Article 6(1)
# and the corresponding obligations… shall apply from 2 August 2027") nagłówkiem NIE jest — bez
# kotwicy końca linii ucinała fragment art. 113 AI Act (EN) w połowie lit. c).
_GRANICE = (r"(?m)^((?:Artykuł|Article|Artikel)\s+\d+[a-z]*\s*$|ROZDZIAŁ\s|CHAPTER\s|KAPITEL\s|"
            r"SEKCJA\s|Sekcja\s|SECTION\s|TYTUŁ\s|TITLE\s|ZAŁĄCZNIK|ANNEX|ANHANG|PREAMBUŁA|"
            + GRANICA + "|" + DOKUMENT + r"|Sporządzono w\b|Done at\b|Geschehen zu\b|Fait à\b|"
            r"W imieniu (?:Parlamentu|Rady|Komisji)|For the (?:European Parliament|Council|Commission)|"
            r"Im Namen (?:des|der)\b|Par le (?:Parlement|Conseil)|\(1\)\s+(?:Dz\.U\.|OJ\s|ABl\.|JO\s))")


def _fragmenty(txt, fraza, maks=8):
    """Spany (start, end) fragmentów z frazą, docięte do granic jednostek redakcyjnych.

    Fraza "art. 6" / "artykuł 6" / "article 6" trafia w NAGŁÓWEK artykułu (w aktach UE:
    "Artykuł 6" w osobnej linii), nie w odesłania; inna fraza działa pełnotekstowo.
    """
    bounds = [m.start() for m in re.finditer(_GRANICE, txt)]
    m = re.match(r"(?i)^art(?:\.|ykuł|icle|ikel)?\s*(\d+[a-z]*)\.?$", fraza.strip())
    if m:
        n = m.group(1)
        hits = [h.start() for h in re.finditer(
            rf"(?m)^(?:Artykuł|Article|Artikel)\s+{n}\s*$", txt)]
    else:
        low, f = txt.lower(), fraza.lower()
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


_RE_ZALACZNIK = re.compile(r"(?m)^(?:ZAŁĄCZNIK|ANNEX|ANHANG|ANNEXE)\b[^\n]*")


def _naglowek_po(txt, poz, maks=160):
    """Pierwsze 1–2 niepuste linie tekstu od pozycji poz (tytuł dokumentu/załącznika), bez znaków granic."""
    linie = []
    for l in txt[poz:poz + 2000].split("\n"):
        l = l.strip().strip(GRANICA + DOKUMENT).strip()
        if l:
            linie.append(l)
        if len(linie) == 2:
            break
    t = " ".join(linie)
    return t if len(t) <= maks else t[:maks - 1].rstrip() + "…"


def _poza_aktem(txt, start):
    """Fragment spoza części normatywnej aktu → opis miejsca (do nagłówka „[w …]"), inaczej None.

    Dokument dołączony (znak DOKUMENT z <hr class="oj-doc-sep"/>: umowa, protokół) i załącznik
    (nagłówek „ZAŁĄCZNIK…" na początku linii) — liczy się NAJBLIŻSZY poprzedzający. Bez tego drugie
    trafienie „art. 2" w decyzji o podpisaniu umowy wyglądało jak kolejny przepis decyzji."""
    dok = txt.rfind(DOKUMENT, 0, start + 1)
    zal = None
    for m in _RE_ZALACZNIK.finditer(txt):
        if m.start() > start:
            break
        zal = m
    if zal is not None and zal.start() > dok:
        return "w załączniku: " + _naglowek_po(txt, zal.start())
    if dok != -1:
        return "w dokumencie dołączonym do aktu (to NIE przepis samego aktu): " + _naglowek_po(txt, dok + 1)
    return None


def celex_norm(parts):
    """Normalizuje identyfikator do numeru CELEX, akceptując różne formy."""
    s = " ".join(parts).strip()
    s = re.sub(r"(?i)^celex:?\s*", "", s).strip()
    # forma ELI: [http://data.europa.eu/eli/]reg|dir|dec/2016/679[/oj]
    m = re.search(r"(?i)\b(reg|dir|dec)[a-z_]*/(\d{4})/(\d+)", s)
    if m and not re.match(r"^\d", s):
        lit = {"reg": "R", "dir": "L", "dec": "D"}[m.group(1).lower()[:3]]
        return f"3{m.group(2)}{lit}{int(m.group(3)):04d}"
    c = s.replace(" ", "").upper()
    # sektor (1 cyfra) + rok (4 cyfry) + litera typu + numer | /TXT (traktaty, Karta)
    if re.match(r"^[0-9]\d{4}[A-Z]{1,2}(?:\d+(?:\(\d{2}\))?(?:R\(\d{2}\))?(?:-\d{8})?|/TXT)$", c):
        return c
    sys.exit(f"Nie rozpoznano CELEX: {s!r}. Przykłady: 32016R0679, CELEX:32024R1689, "
             "02016R0679-20160504 (skonsolidowany), reg/2016/679 (ELI).")


def _konsolidacje(celex):
    """Lista CELEX-ów wersji skonsolidowanych albo VERIFIED_ABSENT jako [].

    VerificationUnknown jest przekazywany do wywołującego, bez zamiany na [].
    """
    base = celex.split("-")[0]
    base = base if base.startswith("0") else "0" + base[1:]
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?celex WHERE {{
  ?w cdm:resource_legal_id_celex ?celex .
  FILTER(STRSTARTS(STR(?celex), "{base}-"))
}} ORDER BY DESC(?celex) LIMIT 100""", soft=True)
    return [c for c in (_v(b, "celex") for b in rows) if re.search(r"-\d{8}$", c)]


def _ostrzezenia_konsolidacja(celex, strict=False, tresc=True, kons=None):
    """Ostrzeżenia o wersjach skonsolidowanych dla aktu/wersji (lista linii).

    To informacja POBOCZNA przy tekście/metadanych — awaria SPARQL nie może odebrać
    użytkownikowi treści głównej, więc UNKNOWN staje się tu głośnym ostrzeżeniem
    (pełną weryfikację wymusza komenda skonsolidowany, gdzie to treść główna).

    strict: awaria kontroli → VerificationUnknown (wywołujący blokuje wynik).
    tresc:  wynik komendy to TREŚĆ aktu — w strict wykryta nowsza wersja skonsolidowana
            blokuje starszą treść. Metadane (daty, obowiązywanie, tytuł) aktu bazowego nie
            są „nieaktualne" przez to, że istnieje konsolidacja — tam tylko ostrzegamy.
    kons:   gotowa lista z _konsolidacje (wywołujący już ją pobrał); None = pobierz tutaj."""
    if kons is None:
        try:
            kons = _konsolidacje(celex)
        except VerificationUnknown as e:
            if strict:
                raise
            return [f"UWAGA: nie udało się zweryfikować, czy akt {celex} ma wersje skonsolidowane "
                    f"({e}) — sprawdź komendą: skonsolidowany {celex}, zanim zacytujesz."]
    out = []
    blokuj = strict and tresc
    if celex.startswith("0"):
        out.append("UWAGA: wersja skonsolidowana ma charakter DOKUMENTACYJNY (nie jest autentyczna) — "
                   f"do urzędowego cytatu wskaż {CYTAT_URZEDOWY}.")
        if kons and kons[0] > celex:
            if blokuj:
                sys.exit(f"BŁĄD: istnieje nowsza wersja skonsolidowana: {kons[0]}. "
                         "Tryb strict blokuje starszą wersję (jej treść i stan na dzień).")
            out.append(f"UWAGA: istnieje NOWSZA wersja skonsolidowana: {kons[0]} — używaj jej.")
    elif kons:
        if blokuj:
            sys.exit(f"BŁĄD: akt ma wersje skonsolidowane — aktualny stan prawny to {kons[0]}. "
                     f"Tryb strict blokuje treść aktu bazowego; do analizy: tekst {kons[0]} "
                     f"(wersja skonsolidowana jest dokumentacyjna — do urzędowego cytatu wskaż {CYTAT_URZEDOWY}; "
                     "bez --strict tekst aktu bazowego jest dostępny).")
        out.append(f"UWAGA: akt ma wersje skonsolidowane — do analizy aktualnego stanu użyj najnowszej: "
                   f"{kons[0]} (pełna lista: skonsolidowany {celex}).")
    return out


def cmd_szukaj(a):
    if not a.fraza:
        sys.exit('Podaj frazę tytułu, np. szukaj "sztucznej inteligencji" --typ REG')
    lang = _lang(a.jezyk)
    fraza = a.fraza.lower().replace('"', "").replace("\\", "")
    pat, filt = [], [f'FILTER(CONTAINS(LCASE(STR(?title)), "{fraza}"))']
    if a.typ:
        pat.append(f"?w cdm:work_has_resource-type <{TYPE_AUTH}{a.typ.upper()}> .")
    if a.rok:
        filt.append(f'FILTER(STRSTARTS(STR(?date), "{a.rok}"))')
    if a.obowiazujace:
        pat.append("?w cdm:resource_legal_in-force ?inf2 .")
        filt.append('FILTER(STR(?inf2) IN ("1", "true"))')
    q = f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?celex ?date ?title ?inf WHERE {{
  ?w cdm:resource_legal_id_celex ?celex .
  ?w cdm:work_date_document ?date .
  {' '.join(pat)}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  ?exp cdm:expression_belongs_to_work ?w .
  ?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> .
  ?exp cdm:expression_title ?title .
  {' '.join(filt)}
}} ORDER BY DESC(?date) LIMIT {a.limit}"""
    rows = _sparql(q)
    if not rows:
        # zero trafień = komunikat + kod wyjścia ≠ 0 TAKŻE z --json: puste „[]" wyglądałoby jak
        # „sprawdzone, nic nie ma", a szukanie po tytule z odmienioną frazą niczego nie dowodzi
        sys.exit(f"Brak wyników dla {a.fraza!r} (język {lang}). Szukanie idzie po TYTULE i dopasowuje "
                 "DOSŁOWNIE, a tytuły są odmienione ('ochrony danych osobowych', nie 'dane osobowe') — "
                 "podaj RDZEŃ albo formę z tytułu ('osobow', 'danych osobowych'). Dalej nic? Spróbuj "
                 "--jezyk eng albo bez --typ/--rok. To NIE dowód, że aktu nie ma.")
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    print(f"Wyniki (pokazuję {len(rows)}, najnowsze pierwsze):\n")
    for b in rows:
        inf = _v(b, "inf")
        status = "obowiązuje" if inf in ("1", "true") else ("nie obowiązuje" if inf in ("0", "false") else "—")
        print(f"  {_v(b, 'celex')}  ({_v(b, 'date')})  [{status}]")
        print(f"    {_v(b, 'title')[:160]}")
        print()


_META_POLA = ("type", "date", "inf", "eli", "eiv", "eov", "trans", "deadline", "kons_data", "baza", "sklad",
              "title")


def _meta_wiersze(celex, lang):
    """Metadane pracy (work) o danym CELEX — surowe wiersze SPARQL.

    Daty: cdm:resource_legal_date_entry-into-force trzyma ZARÓWNO wejście w życie, jak i daty
    rozpoczęcia stosowania (osobnej właściwości „data stosowania" w CDM nie ma — rodzaj daty jest
    tylko w adnotacji, zob. _adnotacje_dat); cdm:directive_date_transposition — termin(y)
    transpozycji dyrektywy; cdm:resource_legal_date_deadline — inne terminy z aktu (w starszych
    dyrektywach także termin transpozycji, gdy brak directive_date_transposition: 32002L0012);
    cdm:resource_legal_date_end-of-validity — bywa KILKA wartości: 9999-12-31 (akt bez daty końca)
    obok dat CZĘŚCIOWEGO upływu ważności (32009R0470: 2022-01-27 = art. 30).
    Wersja skonsolidowana (sektor 0): cdm:act_consolidated_date = „stan na",
    cdm:act_consolidated_based_on_resource_legal = akt bazowy,
    cdm:act_consolidated_consolidates_resource_legal = akty ujęte w konsolidacji."""
    return _sparql(f"""PREFIX cdm: <{CDM}>
SELECT ?type ?date ?inf ?eli ?eiv ?eov ?trans ?deadline ?kons_data ?baza ?sklad ?title WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?w cdm:work_has_resource-type ?type }}
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  OPTIONAL {{ ?w cdm:resource_legal_eli ?eli }}
  OPTIONAL {{ ?w cdm:resource_legal_date_entry-into-force ?eiv }}
  OPTIONAL {{ ?w cdm:resource_legal_date_end-of-validity ?eov }}
  OPTIONAL {{ ?w cdm:directive_date_transposition ?trans }}
  OPTIONAL {{ ?w cdm:resource_legal_date_deadline ?deadline }}
  OPTIONAL {{ ?w cdm:act_consolidated_date ?kons_data }}
  OPTIONAL {{ ?w cdm:act_consolidated_based_on_resource_legal ?b . ?b cdm:resource_legal_id_celex ?baza }}
  OPTIONAL {{ ?w cdm:act_consolidated_consolidates_resource_legal ?s . ?s cdm:resource_legal_id_celex ?sklad }}
  OPTIONAL {{ ?exp cdm:expression_belongs_to_work ?w .
              ?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> .
              ?exp cdm:expression_title ?title }}
}}""")


def _zbierz(rows, pola=_META_POLA):
    return {k: sorted({_v(b, k) for b in rows if _v(b, k)}) for k in pola}


ANNOT = "http://publications.europa.eu/ontology/annotation#"
_WLASCIWOSCI_DAT = {"eiv": "resource_legal_date_entry-into-force",
                    "eov": "resource_legal_date_end-of-validity",
                    "trans": "directive_date_transposition",
                    "deadline": "resource_legal_date_deadline"}
# Kody tabel autorytatywnych Urzędu Publikacji (fd_330 koniec ważności, fd_335 daty, fd_361
# transpozycja) używane w annot:comment_on_date / annot:type_of_date — etykiety PL z tych tabel
# (skos:prefLabel), część skrócona. Nieznany kod zostaje wypisany dosłownie.
_KODY_DAT = {
    "FIN/VAL/PART": "częściowy upływ terminu ważności", "FIN/VAL": "upływ terminu ważności",
    "AI/PAR": "uchylony w sposób dorozumiany przez", "A/PAR": "uchylony przez",
    "AR/PAR": "uchylony i zastąpiony przez", "R/PAR": "zastąpiony przez", "REMPLPART": "częściowe zastąpienie",
    "P/PAR": "przedłużenie ważności do", "PI/PAR": "przedłużony w sposób dorozumiany do",
    "VOID": "uznany za nieważny na mocy", "CADUC": "nieaktualny",
    "EV": "wejście w życie", "DATEFF": "data wejścia w życie", "MA": "stosowanie",
    "MA/PART": "częściowe stosowanie", "MA/PROV": "tymczasowe stosowanie", "APPLICATION": "stosowanie",
    "PE": "staje się skuteczny", "PE/PART": "staje się częściowo skuteczny",
    "ADOPTION": "przyjęcie", "NOTIF": "notyfikacja", "B-19.12": "przegląd",
    "DATPUB": "data publikacji", "DATNOT": "data notyfikacji", "DATSIG": "data podpisania",
    "DATDOC": "data dokumentu", "DATRAT": "data ratyfikacji",
    "AU+TARD": "najpóźniej", "AU+TOT": "najwcześniej", "V": "patrz", "ET": "i", "SAUF": "za wyjątkiem",
    "AP": "po", "APRES": "po", "ART": "art.", "ARTUNIQUE": "artykuł", "ANN": "załącznik",
    "CONSID": "motyw", "PT": "część", "ECHEL": "różne daty", "DATE": "data", "TEXTE": "tekst",
    "TIT": "tytuł", "TITRE": "tytuł", "JO": "Dz.U.", "P": "str.", "AN": "rok", "ANNEE": "rok", "ANS": "lata",
    "ANNEES": "lata", "MOIS": "miesiąc", "MOISS": "miesiące", "DM": "miesiąca", "EXERC": "rok budżetowy",
    "EXERCICE": "rok budżetowy", "DEB/PER/REF": "początek okresu odniesienia",
    "FIN/PER/REF": "koniec okresu odniesienia", "PER/REF": "okres odniesienia",
    "FIN/PER/TRANSIT": "koniec okresu przejściowego", "FIN/PROGRAMME": "koniec programu",
    "FIN/MANDAT": "wygaśnięcie mandatu", "FIN/ACTU": "nie dotyczy", "ACT/DEF": "do czasu przyjęcia "
    "ostatecznych środków", "YD": "przyjęty przez", "YDP": "częściowo przyjęty przez", "RT": "wycofany",
    "RJ": "odrzucony", "DE/PAR": "wypowiedziany w drodze", "TAC/REC/PER": "odnowienie za milczącą zgodą na",
}
# kody o różnym znaczeniu w różnych tabelach: (tabela, kod) → etykieta
_KODY_DAT_TABELA = {("fd_330", "L"): "związany z"}
_RE_KOD_DATY = re.compile(r"\{([^|{}]+)\|([^{}]*)\}")


def _opis_daty(surowy):
    """Komentarz CELLAR do daty („{FIN/VAL/PART|…fd_330/…} {ART|…} 30 {AI/PAR|…} 32019R0006") →
    „częściowy upływ terminu ważności art. 30 uchylony w sposób dorozumiany przez 32019R0006"."""
    def kod(m):
        k, uri = m.group(1).strip(), m.group(2)
        if "/celex/" in uri:
            return k
        tabela = re.search(r"/authority/(fd_\d+)/", uri)
        return _KODY_DAT_TABELA.get((tabela.group(1) if tabela else "", k)) or _KODY_DAT.get(k, k)
    return re.sub(r"\s+", " ", _RE_KOD_DATY.sub(kod, surowy)).strip()


def _adnotacje_dat(celex):
    """Adnotacje OWL do dat aktu (owl:Axiom: annot:comment_on_date, annot:type_of_date) →
    {(pole, data): {"komentarz": [opis…], "typ": [opis…], "surowe": [literał…]}}; pole ∈ eiv/eov/
    trans/deadline. To z nich EUR-Lex buduje „Częściowy upływ terminu ważności Art. 30" i „Wejście
    w życie"/„Stosowanie". Awaria → VerificationUnknown (adnotacje są opisem, nie podstawą statusu)."""
    wart = " ".join(f"cdm:{w}" for w in _WLASCIWOSCI_DAT.values())
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX annot: <{ANNOT}>
SELECT DISTINCT ?p ?d ?ap ?ao WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  VALUES ?p {{ {wart} }}
  ?w ?p ?d .
  ?ax owl:annotatedSource ?w ; owl:annotatedProperty ?p ; owl:annotatedTarget ?d ; ?ap ?ao .
  FILTER(?ap IN (annot:comment_on_date, annot:type_of_date))
}} LIMIT 500""", soft=True)
    pola = {CDM + w: k for k, w in _WLASCIWOSCI_DAT.items()}
    out = {}
    for b in rows:
        pole, d, ap, ao = pola.get(_v(b, "p")), _v(b, "d"), _v(b, "ap"), _v(b, "ao")
        if not (pole and d and ao) or not ap.startswith(ANNOT):
            continue  # obce wiersze (np. podmiana _sparql w testach) — ignoruj
        wpis = out.setdefault((pole, d), {"komentarz": [], "typ": [], "surowe": []})
        klucz = "typ" if ap.endswith("type_of_date") else "komentarz"
        if _opis_daty(ao) not in wpis[klucz]:
            wpis[klucz].append(_opis_daty(ao))
            wpis["surowe"].append(ao)
    return out


def _koniec_obowiazywania(eov, adnot=None):
    """Daty końca obowiązywania (cdm:resource_legal_date_end-of-validity, bywa KILKA) →
    (koniec, częściowe): koniec = data końca obowiązywania AKTU („9999-12-31" = bez daty końca,
    None = brak danych), częściowe = daty częściowego upływu ważności (wybrane przepisy).

    CELLAR trzyma obie rzeczy w jednej właściwości: 32009R0470 ma 2022-01-27 (częściowy upływ:
    art. 30) i 9999-12-31 — EUR-Lex: „Date of end of validity: No end date". Reguła: jest
    9999-12-31 → akt bez daty końca, pozostałe daty = częściowe; inaczej daty z adnotacją
    FIN/VAL/PART = częściowe, a z pozostałych najpóźniejsza = koniec aktu."""
    daty = sorted(set(d for d in eov if d))
    if not daty:
        return None, []
    if "9999-12-31" in daty:
        return "9999-12-31", [d for d in daty if d != "9999-12-31"]
    adnot = adnot or {}
    czesc = [d for d in daty if any("FIN/VAL/PART" in x for x in adnot.get(("eov", d), {}).get("surowe", []))]
    reszta = [d for d in daty if d not in czesc]
    if not reszta:
        return None, czesc
    return reszta[-1], sorted(czesc + reszta[:-1])


def _opis_z_adnotacji(adnot, pole, d):
    """Opis daty z adnotacji („stosowanie (patrz art. 99)") albo ''."""
    w = (adnot or {}).get((pole, d))
    if not w:
        return ""
    typ, kom = "; ".join(w["typ"]), "; ".join(w["komentarz"])
    return f"{typ} ({kom})" if typ and kom else (typ or kom)


def _zmieniajace(celex):
    """CELEX-y aktów ZMIENIAJĄCYCH dany akt (?x cdm:resource_legal_amends_resource_legal ?w).

    [] = VERIFIED_ABSENT (sprostowania to osobna relacja — nie zmieniają dat stosowania);
    awaria → VerificationUnknown (nigdy pusta lista)."""
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?c2 WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  ?x cdm:resource_legal_amends_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
}} ORDER BY ?c2 LIMIT 200""", soft=True)
    return [_v(b, "c2") for b in rows if _v(b, "c2")]


def _status(zb):
    inf = zb["inf"][0] if zb["inf"] else ""
    return "OBOWIĄZUJE" if inf in ("1", "true") else ("NIE OBOWIĄZUJE" if inf in ("0", "false") else "—")


def _drukuj_daty(zb, wc="  ", adnot=None, celex=""):
    """Daty aktu (bazowego) z uczciwymi etykietami: jedna data = wejście w życie; kilka dat =
    wejście w życie + daty stosowania (rodzaj każdej — z adnotacji CELLAR, gdy są); dyrektywa =
    termin transpozycji; koniec obowiązywania AKTU oddzielony od częściowego upływu ważności.
    adnot = _adnotacje_dat() aktu albo None (niepobrane — wtedy bez opisów dat)."""
    typy = {t.rsplit("/", 1)[-1] for t in zb["type"]}
    wc2 = wc + "  "

    def opisy(pole, daty):
        for d in daty:
            o = _opis_z_adnotacji(adnot, pole, d)
            if o:
                print(f"{wc2}{d}: {o}")

    if zb["date"]:
        print(f"{wc}Data aktu: {zb['date'][0]}")
    eiv = zb["eiv"]
    if len(eiv) == 1:
        o = _opis_z_adnotacji(adnot, "eiv", eiv[0])
        o = re.sub(r"^wejście w życie(?: \((.*)\))?$", lambda m: m.group(1) or "", o)
        print(f"{wc}Wejście w życie: {eiv[0]}" + (f"  ({o})" if o else ""))
    elif eiv:
        print(f"{wc}Wejście w życie / stosowanie: {', '.join(eiv)}")
        opisy("eiv", eiv)
        if not all((adnot or {}).get(("eiv", d), {}).get("typ") for d in eiv):
            print(f"{wc}  (kilka dat — CELLAR nie opisuje, która to wejście w życie, a która rozpoczęcie "
                  "stosowania; najwcześniejsza to z reguły wejście w życie — sprawdź przepisy końcowe aktu!)")
        else:
            print(f"{wc}  (rodzaj dat wg adnotacji CELLAR — zakres każdej sprawdź w przepisach końcowych aktu)")
    dl = zb.get("deadline") or []
    if zb["trans"]:
        print(f"{wc}Termin transpozycji: {', '.join(zb['trans'])}"
              + ("   (kilka terminów — różne zakresy; sprawdź przepis o transpozycji)"
                 if len(zb["trans"]) > 1 else ""))
        opisy("trans", zb["trans"])
        print(f"{wc}  (dyrektywa działa przez transpozycję — polską ustawę wdrażającą sprawdź "
              "skillem prawo-pl-eli)")
    elif "DIR" in typy and dl:
        # starsze dyrektywy (32002L0012): termin transpozycji tylko jako ogólny „deadline" — EUR-Lex
        # pokazuje go jako „Deadline: 20/09/2003; Najpóźniej Patrz Art. 3.1", nie jako transpozycję
        print(f"{wc}Termin transpozycji: CELLAR nie ma osobnego pola; podaje TERMIN (deadline): {', '.join(dl)}"
              " — potwierdź w przepisie wskazanym niżej, czy to termin transpozycji")
        opisy("deadline", dl)
        print(f"{wc}  (dyrektywa działa przez transpozycję — polską ustawę wdrażającą sprawdź "
              "skillem prawo-pl-eli)")
        dl = []
    elif "DIR" in typy:
        print(f"{wc}Termin transpozycji: brak w CELLAR — sprawdź przepis o transpozycji w tekście dyrektywy")
    if dl:
        print(f"{wc}Inne terminy z aktu (deadline — przeglądy, sprawozdania, okresy przejściowe): {', '.join(dl)}")
        opisy("deadline", dl)
    koniec, czesc = _koniec_obowiazywania(zb["eov"], adnot)
    if koniec and koniec != "9999-12-31":
        o = _opis_z_adnotacji(adnot, "eov", koniec)
        print(f"{wc}Koniec obowiązywania: {koniec}" + (f"  ({o})" if o else ""))
    elif czesc:
        print(f"{wc}Koniec obowiązywania aktu: " + ("brak daty końca (CELLAR: 9999-12-31)" if koniec
                                                    else "brak w CELLAR"))
    for d in czesc:
        o = re.sub(r"^częściowy upływ terminu ważności\s*", "", _opis_z_adnotacji(adnot, "eov", d))
        if not re.search(r"\b(art\.|artykuł|załącznik|motyw|część)", o):
            o = (o + "; " if o else "") + ("CELLAR nie podaje, których przepisów dotyczy — sprawdź"
                                          + (f" odniesienia {celex} i" if celex else "") + " stronę aktu w EUR-Lex")
        print(f"{wc}Częściowy upływ ważności: {d} — {o}")
    print(f"{wc}Status:  {_status(zb)}")


def _ostrzezenie_zmiany(akt, zmiany, najnowsza):
    """Akt był zmieniany → daty stosowania z metadanych aktu bazowego mogą być nieaktualne."""
    cel = (f"w najnowszej wersji skonsolidowanej {najnowsza} (tekst {najnowsza} --fragment \"art. N\")"
           if najnowsza else f"w aktach zmieniających (odniesienia {akt})")
    return (f"UWAGA: akt {akt} był zmieniany ({', '.join(zmiany)}) — daty wejścia w życie / stosowania "
            f"pochodzą z metadanych AKTU BAZOWEGO i mogą być nieaktualne; sprawdź art. o stosowaniu {cel}.")


def cmd_meta(a):
    celex = celex_norm(a.celex)
    lang = _lang(a.jezyk)
    strict = getattr(a, "strict", False)
    rows = _meta_wiersze(celex, lang)
    if not rows:
        sys.exit(f"Nie znaleziono aktu o CELEX {celex} w CELLAR.")
    zb = _zbierz(rows)
    # wersja skonsolidowana: jej daty to „stan na" konsolidacji, NIE daty aktu — daty czytamy z aktu bazowego
    kons_wersja = bool(zb["kons_data"]) or bool(re.match(r"^0.*-\d{8}$", celex))
    baza = zb["baza"][0] if zb["baza"] else (None if not kons_wersja else "3" + celex[1:].split("-")[0])
    zb_baza = _zbierz(_meta_wiersze(baza, lang)) if kons_wersja else None
    akt = baza if kons_wersja else celex
    # --- weryfikacja PRZED emisją: wersje skonsolidowane + akty zmieniające -----------------
    kons = None
    try:
        kons = _konsolidacje(celex)
    except VerificationUnknown:
        if strict:
            raise
    try:
        zmiany, zmiany_uwaga = _zmieniajace(akt), None
    except VerificationUnknown as e:
        if strict:
            raise
        zmiany, zmiany_uwaga = [], (f"UWAGA: nie udało się zweryfikować, czy akt {akt} był zmieniany "
                                    f"({e}) — sprawdź: odniesienia {akt}, zanim powołasz się na daty.")
    try:
        sprost, sprost_uwaga = _sprostowania(akt, lang), None
    except VerificationUnknown as e:
        if strict:
            raise
        sprost, sprost_uwaga = [], (f"UWAGA: nie udało się zweryfikować, czy akt {akt} ma sprostowania "
                                    f"w języku {lang.lower()} ({e}) — sprawdź: odniesienia {akt}.")
    try:
        # opisy dat (rodzaj daty, częściowy upływ ważności i jego zakres) — tylko opis: awaria nie
        # blokuje także w --strict (rozdział „koniec aktu / częściowy upływ" działa bez adnotacji)
        adnot, adnot_uwaga = _adnotacje_dat(akt), None
    except VerificationUnknown as e:
        adnot, adnot_uwaga = None, (f"UWAGA: nie udało się pobrać opisów dat z CELLAR ({e}) — daty bez "
                                    "rodzaju i zakresu (np. którego przepisu dotyczy częściowy upływ ważności).")
    najnowsza = kons[0] if kons else None
    if strict and zmiany and not kons_wersja:
        # Daty aktu bazowego po nowelizacji mogą być nieaktualne (AI Act art. 113 po 32026R1744),
        # a CELLAR nie aktualizuje ich w metadanych aktu bazowego — strict nie może ich przepuścić.
        # Same sprostowania nie blokują (nie zmieniają dat). Na wersji skonsolidowanej daty aktu
        # bazowego idą z ostrzeżeniem — konsolidacja to właśnie miejsce, gdzie czyta się art. o stosowaniu.
        sys.exit(f"BŁĄD: akt {celex} był zmieniany ({', '.join(zmiany)}) — daty wejścia w życie / "
                 "stosowania z metadanych aktu bazowego mogą być nieaktualne. Tryb strict blokuje metadane "
                 "aktu bazowego; do analizy: "
                 + (f"meta {najnowsza} oraz art. o stosowaniu: tekst {najnowsza} --fragment \"art. N\""
                    if najnowsza else f"odniesienia {celex} (akty zmieniające)")
                 + " (bez --strict metadane aktu bazowego są dostępne z ostrzeżeniem).")
    # konsolidacje: akt bazowy — tylko ostrzeżenie (tresc=False); wersja skonsolidowana w strict —
    # nowsza wersja blokuje (stan „na dzień" starszej wersji jest z definicji zastąpiony)
    ostrz = _ostrzezenia_konsolidacja(celex, strict, tresc=kons_wersja, kons=kons)
    if zmiany:
        ostrz.append(_ostrzezenie_zmiany(akt, zmiany, najnowsza))
    if zmiany_uwaga:
        ostrz.append(zmiany_uwaga)
    if sprost_uwaga:
        ostrz.append(sprost_uwaga)
    if adnot_uwaga:
        ostrz.append(adnot_uwaga)
    if sprost and kons_wersja:
        ostrz.extend(_ostrzezenia_sprostowan(celex, lang, sprost, kons=kons))
    elif sprost:
        # metadane aktu bazowego nie są przez sprostowanie nieaktualne (strict nie blokuje), ale
        # TREŚĆ z Dz.U. jest niesprostowana — informujemy, gdzie czytać brzmienie poprawione
        cel = _gdzie_sprostowane(sprost, kons)
        ostrz.append(f"UWAGA: akt ma SPROSTOWANIA w języku {lang.lower()}: "
                     + ", ".join(x["celex"] + (f" ({x['data']})" if x["data"] else "") for x in sprost)
                     + " — tekst aktu bazowego ich nie uwzględnia; "
                     + (f"brzmienie poprawione: tekst {cel} (wersja skonsolidowana)" if cel
                        else f"treść: tekst {sprost[0]['celex']}")
                     + "; zakres poprawek: tekst " + celex + " --fragment \"art. N\".")
    if a.json:
        # "meta" = surowe wiersze SPARQL tej pracy (na wersji skonsolidowanej: eiv/date = „stan na");
        # "akt_bazowy" = zebrane metadane aktu bazowego; "zmieniajace" = CELEX-y nowelizacji;
        # "ostrzezenia" = te same linie, które widzi człowiek
        koniec, czesc = _koniec_obowiazywania((zb_baza if kons_wersja else zb)["eov"], adnot)
        out = {"celex": celex, "wersja_skonsolidowana": kons_wersja, "meta": rows,
               "zmieniajace": zmiany, "sprostowania": sprost, "wersje_skonsolidowane": kons,
               "koniec_obowiazywania": {"akt": koniec, "czesciowy_uplyw": czesc},
               "opisy_dat": None if adnot is None else [
                   {"pole": k[0], "data": k[1], "typ": v["typ"], "komentarz": v["komentarz"],
                    "surowe": v["surowe"]} for k, v in sorted(adnot.items())],
               "ostrzezenia": ostrz}
        if kons_wersja:
            out["akt_bazowy"] = {"celex": baza, "meta": zb_baza}
        print(json.dumps(out, ensure_ascii=False, indent=2)); return
    # --- emisja -------------------------------------------------------------------------------
    print(f"Akt: CELEX {celex}")
    if zb["title"]:
        print(textwrap.fill(zb["title"][0], width=100, initial_indent="  Tytuł:   ",
                            subsequent_indent="           "))
    if zb["type"]:
        print(f"  Typ:     {', '.join(t.rsplit('/', 1)[-1] for t in zb['type'])}"
              + ("  (wersja skonsolidowana — dokumentacyjna)" if kons_wersja else ""))
    if kons_wersja:
        print(f"  Stan na (konsolidacja): {zb['kons_data'][0] if zb['kons_data'] else celex.rsplit('-', 1)[-1]}")
        if zb["sklad"]:
            print(f"  Uwzględnia: {', '.join(zb['sklad'])}")
        if zb["eli"]:
            print(f"  ELI:     {zb['eli'][0]}")
        print(f"  Akt bazowy: CELEX {baza}" + ("" if zb_baza["type"] or zb_baza["date"] else "  (brak w CELLAR)"))
        _drukuj_daty(zb_baza, wc="    ", adnot=adnot, celex=baza)
        if zb_baza["eli"]:
            print(f"    ELI:     {zb_baza['eli'][0]}")
    else:
        _drukuj_daty(zb, adnot=adnot, celex=celex)
        if zb["eli"]:
            print(f"  ELI:     {zb['eli'][0]}")
    print(f"  Tekst:   python3 {sys.argv[0]} tekst {celex} --jezyk pol --fragment \"art. N\"")
    for w in ostrz:
        print(w)


def cmd_skonsolidowany(a):
    celex = celex_norm(a.celex)
    try:
        kons = _konsolidacje(celex)
    except VerificationUnknown as e:
        _nie_zweryfikowano(f"wersji skonsolidowanych dla {celex}", e)
    if a.json:
        print(json.dumps(kons, ensure_ascii=False, indent=2)); return
    if not kons:
        print(f"Nie znaleziono wersji skonsolidowanych dla {celex}. "
              "Brak konsolidacji nie potwierdza braku zmian aktu. "
              f"Przed cytowaniem sprawdź zmiany i sprostowania: odniesienia {celex}.")
        return
    print(f"WERSJE SKONSOLIDOWANE dla {celex} (najnowsza pierwsza; data w CELEX = stan na):")
    for i, c in enumerate(kons):
        print(f"  - {c}{'  ← AKTUALNA' if i == 0 else ''}")
    print("\nUWAGA: wersja skonsolidowana ma charakter dokumentacyjny — do urzędowego cytatu "
          f"wskaż {CYTAT_URZEDOWY}.")
    print(f"Dalej: python3 {sys.argv[0]} tekst {kons[0]} --jezyk pol --fragment \"art. N\"")


# Tekst: XHTML (akty od ok. 2014 r., wersje skonsolidowane) ALBO HTML (starsze akty, np. 95/46/WE,
# e-Privacy 2002/58/WE — w CELLAR mają wyłącznie manifestację „html"). Samo Accept: application/xhtml+xml
# dawało dla nich 404, choć tekst istnieje; z q-wagą CELLAR wybiera XHTML, a gdy go nie ma — HTML.
AKCEPT_TEKST = "application/xhtml+xml, text/html;q=0.9"
_TYPY_TEKSTU = ("xhtml", "html")
# kolejność wyboru PDF: archiwalne PDF/A przed zwykłym PDF (wszystkie to ten sam urzędowy dokument)
_PDF_PREF = ("pdfa2a", "pdfa1a", "pdfa1b", "pdfa", "pdf")


def _manifestacje(celex, lang=None):
    """Manifestacje (format) i ich elementy (pliki DOC_n) pracy o danym CELEX — wiersze SPARQL
    z polami l (język), mtype, man, item.

    Numer pliku NIE jest stały: PDF e-Privacy 32002L0058 (pol) to …0018.02/DOC_2, PDF wersji
    skonsolidowanej 02002L0058-20091219 (pol) — …0017.04/DOC_2, a RODO (pol) — DOC_1. Adres pliku
    bierzemy więc z cdm:item_belongs_to_manifestation, a nie doklejamy „/DOC_1".
    [] = pracy nie ma w CELLAR (VERIFIED_ABSENT); praca bez manifestacji w języku = wiersz bez mtype.
    Awaria → VerificationUnknown."""
    jez = (f"?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> . BIND(\"{lang}\" AS ?l)" if lang else
           f"?exp cdm:expression_uses_language ?lang . BIND(STRAFTER(STR(?lang), \"language/\") AS ?l)")
    return _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?l ?mtype ?man ?item WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?exp cdm:expression_belongs_to_work ?w . {jez}
              ?man cdm:manifestation_manifests_expression ?exp .
              ?man cdm:manifestation_type ?mtype .
              OPTIONAL {{ ?item cdm:item_belongs_to_manifestation ?man }} }}
}} LIMIT 3000""", soft=True)


def _nr_doc(item):
    m = re.search(r"/DOC_(\d+)$", item)
    return int(m.group(1)) if m else 10 ** 6


def _elementy(rows, typy, lang=None):
    """Adresy plików (posortowane DOC_n) PIERWSZEJ manifestacji z `typy` (w kolejności preferencji)."""
    rows = [b for b in rows if (lang is None or _v(b, "l") == lang) and _v(b, "mtype") in typy]
    for t in typy:
        manif = sorted({_v(b, "man") for b in rows if _v(b, "mtype") == t})
        for man in manif:
            items = sorted({_v(b, "item") for b in rows if _v(b, "man") == man and _v(b, "item")},
                           key=_nr_doc)
            if items:
                return t, man, items
        if manif:
            return t, manif[0], []
    return None, None, []


def _pdf_url(celex, lang):
    """(URL pliku PDF, [pozostałe pliki tej manifestacji]) albo (None, []) gdy PDF w tym języku brak.

    Adres pliku pochodzi z SPARQL (element manifestacji), nie z doklejonego „/DOC_1" — ten dawał 404
    m.in. dla 31995L0046 i 32002L0058 (PDF = DOC_2). Gdy CELLAR nie poda elementu, zostaje
    negocjacja treści (Accept: application/pdf), a dopiero potem konwencja „/DOC_1"."""
    rows = _manifestacje(celex, lang)
    pdfy = sorted({_v(b, "mtype") for b in rows if _v(b, "mtype").startswith("pdf")},
                  key=lambda t: (_PDF_PREF.index(t) if t in _PDF_PREF else len(_PDF_PREF), t))
    if not pdfy:
        return None, []
    _, man, items = _elementy(rows, tuple(pdfy))
    if items:
        return items[0], items[1:]
    return None, [man + "/DOC_1"]


def _pobierz_pdf(celex, lang, lang3):
    """Pobiera urzędowy PDF; zwraca (bajty, źródło, pozostałe pliki manifestacji)."""
    try:
        pdf_url, reszta = _pdf_url(celex, lang)
    except VerificationUnknown as e:
        _nie_zweryfikowano(f"manifestacji PDF dla {celex} w języku {lang3}", e)
    if pdf_url:
        kandydaci, inne = [(pdf_url, None)], reszta
    elif reszta:  # manifestacja PDF bez elementów w SPARQL: negocjacja, potem konwencja /DOC_1
        kandydaci = [(CELLAR + urllib.parse.quote(celex, safe="/"),
                      {"Accept": "application/pdf", "Accept-Language": lang3}), (reszta[0], None)]
        inne = []
    else:
        sys.exit(f"Brak manifestacji PDF dla {celex} w języku {lang3} (CELLAR jej nie ma) — "
                 "spróbuj inny --jezyk albo tekst bez --pdf.")
    blad = None
    for url, naglowki in kandydaci:
        try:
            data, _ = _http(url, headers=naglowki)
        except SystemExit as e:
            blad = e
            continue
        if not data.startswith(b"%PDF"):
            blad = SystemExit(f"BŁĄD: {url} nie zwrócił pliku PDF (brak sygnatury %PDF).")
            continue
        return data, _wymus_https(url), inne
    raise blad


class _TylkoPdf(SystemExit):
    """Akt jest w CELLAR w tym języku TYLKO jako PDF (bez HTML/XHTML). Podklasa SystemExit: kto nie
    obsługuje ścieżki PDF (np. _zakres_sprostowania), dostaje dotychczasowy komunikat z --pdf."""


# --- tekst z urzędowego PDF (akt bez HTML/XHTML w danym języku) -----------------------------------
# Starsze akty w językach państw z 2004 r. i później (polskie wydanie specjalne Dz.Urz. UE, np.
# 32004R0883, 32004L0037, 31994R0114) CELLAR ma TYLKO jako PDF. `tekst` czyta wtedy ten PDF przez
# `pdftotext -layout` (poppler; opcjonalny — bez niego zostaje odesłanie do --pdf), tak jak skill
# prawo-pl-eli dla aktów bez text.html. Strona Dz.Urz. UE jest złożona w DWÓCH ŁAMACH, a -layout
# stawia je obok siebie — bez rozdzielenia łamów sklejanie wierszy mieszałoby motywy i artykuły.
# Nagłówki stron: wydanie specjalne („72  PL  Dziennik Urzędowy Unii Europejskiej  05/t. 5”),
# numer CELEX nad aktem i nagłówek pierwotnego Dz.Urz. („30.4.2004  DZIENNIK URZĘDOWY UNII
# EUROPEJSKIEJ  L 166/1”) — tylko na samej górze strony.
_PDF_NAGLOWEK = re.compile(r"(?i)(?:Dziennik\s+Urzędowy|Official\s+Journal|Amtsblatt|Journal\s+officiel)\s+"
                           r"(?:Unii|Wspólnot|of\s+the\s+European|der\s+Europäischen|de\s+l.Union|des\s+Communautés)")
_PDF_CELEX = re.compile(r"^\s*\d{5}[A-Z]{1,2}\d{4}(?:R\(\d+\))?\s*$")   # także sprostowanie …R(06)
_PDF_STOPKA = re.compile(r"^\s*\d{1,4}\s*$")
# przypis z dołu łamu: „(1) Dz.U. L 387 z 31.12.1992, str. 1.” — od niego do końca łamu są przypisy
_PDF_PRZYPIS = re.compile(r"^\(\d{1,3}\)\s+(?:Dz\.\s?U\.|OJ\b|ABl\.|JO\b)")
_PDF_RYNNA_WOLNE = 0.5   # rynna: tyle niepustych wierszy strony ma w niej spację…
_PDF_RYNNA_OBA = 0.15    # …a tyle ma tekst po obu jej stronach
_PDF_MIN_LAM = 0.35      # wiersz tylko na lewo od rynny, zaczynający się dalej = wyśrodkowany (zostaje)
_PDF_TABELA = 0.3        # tyle wierszy bloku z ≥2 przerwami ≥3 spacji = tabela, nie dwa łamy
_PDF_WCIECIE_NAGLOWKA = 8    # wyśrodkowany nagłówek/tytuł: wcięty co najmniej tyle w łamie,
_PDF_MAKS_NAGLOWEK = 70      # …nie dłuższy, z marginesami po obu stronach podobnymi, nie od małej litery
_PDF_SRODEK = "\x1e"         # znacznik wiersza wyśrodkowanego (usuwany w _pdf_akapity)
_PDF_KONIEC_ZDANIA = re.compile(r"[.;:!?)”\"]$")
_PDF_ARTYKUL = re.compile(r"(?:Artykuł|Article|Artikel)\s+\d+[a-z]*")
# początek nowego akapitu w łamie (inaczej wiersz to kontynuacja poprzedniego)
_PDF_NOWY_AKAPIT = re.compile(
    r"^(?:\(\d+[a-z]?\)\s|\d+[a-z]?\.(?:\s|$)|[a-z]{1,4}\)\s|[—–-]\s|(?:Artykuł|Article|Artikel)\s+\d"
    r"|ROZDZIAŁ|TYTUŁ|SEKCJA|ZAŁĄCZNIK|CHAPTER|TITLE|ANNEX|uwzględniając\b|a także mając\b|stanowiąc\b"
    r"|PRZYJMUJE\b|Sporządzono w\b|W imieniu\b|Having regard\b|Whereas\b|Done at\b|For the\b"
    r"|[A-ZĄĆĘŁŃÓŚŹŻ][A-ZĄĆĘŁŃÓŚŹŻ ]{5,}[,:]?$)")   # wiersz wersalikami: „KOMISJA WSPÓLNOT EUROPEJSKICH,”


def pdftotext_dostepny():
    return shutil.which("pdftotext") is not None


def _pdftotext_layout(pdf_bytes):
    """`pdftotext -layout` na bajtach PDF → tekst (pusty napis przy awarii)."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            tmp = f.name
        r = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", tmp, "-"],
                           capture_output=True, timeout=180)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _pdf_bez_naglowka(lines):
    """Usuwa nagłówki z góry strony (Dz.Urz. UE, wydanie specjalne, CELEX) i numer strony z dołu."""
    out = list(lines)
    for i, l in enumerate(out):
        if not l.strip():
            continue
        if _PDF_NAGLOWEK.search(l) or _PDF_CELEX.match(l):
            out[i] = ""
            continue
        break
    while out and (not out[-1].strip() or _PDF_STOPKA.match(out[-1])):
        out.pop()
    return out


def _pdf_przypisy(lam):
    """Wiersze łamu → (treść, przypisy): przypisy to ogon łamu od pierwszego „(N) Dz.U. …”.
    Przypisy w dwóch łamach w jednym wierszu („(1) Dz.U. …      (6) Dz.U. …”) — lewe, potem prawe."""
    for i, l in enumerate(lam):
        if _PDF_PRZYPIS.match(l.strip()):
            ogon = lam[i:]
            m = [re.search(r" {3,}(?=\(\d{1,3}\)\s)", l.strip()) for l in ogon]
            if any(m):
                lewe = [l.strip()[:x.start()] if x else l.strip() for l, x in zip(ogon, m)]
                prawe = [l.strip()[x.end():] for l, x in zip(ogon, m) if x]
                ogon = lewe + prawe
            return lam[:i], ogon
    return lam, []


def _pdf_oznacz_srodek(ws, szer=None):
    """Oznacza wiersze wyśrodkowane w łamie o szerokości `szer` (tytuły artykułów, „Definicje”).
    Kontynuacja zagnieżdżonego punktu też bywa głęboko wcięta, ale sięga prawego marginesu — stąd
    warunek podobnych marginesów. `szer=None`: wiersz na całą szerokość strony (tytuł aktu,
    nagłówek nad łamami) — tu szerokości strony nie znamy pewnie (tabele), wystarczy wcięcie."""
    out = []
    for l in ws:
        t = l.strip()
        wc = len(l) - len(l.lstrip(" "))
        prawy = (szer - len(l.rstrip())) if szer is not None else wc
        srodek = (t and wc >= _PDF_WCIECIE_NAGLOWKA and len(t) <= _PDF_MAKS_NAGLOWEK
                  and abs(wc - prawy) <= max(8, 0.35 * max(wc, prawy))
                  and not t[0].islower() and not t.endswith((",", ";")))
        out.append(_PDF_SRODEK + l if srodek else l)
    return out


def _pdf_lamy(lines):
    """Wiersze strony z `-layout` → (wiersze w kolejności czytania, wiersze przypisów).

    Rynna = kolumna znaków, w której (z sąsiednią) większość wierszy ma spację, a część ma tekst po
    obu stronach. Wiersze przez nią przechodzące (tytuł aktu, akapit na całą szerokość, tabela)
    zostają na miejscu i dzielą stronę na bloki; każdy blok to lewy łam, potem prawy, bez wcięcia.
    Strona bez rynny wraca bez zmian. Uproszczona wersja `_pdf_lamy` ze skilla prawo-pl-eli."""
    niepuste = [l for l in lines if l.strip()]
    if len(niepuste) < 8:
        return _pdf_przypisy(_pdf_oznacz_srodek(lines))
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
        return _pdf_przypisy(_pdf_oznacz_srodek(lines))

    def bez_wciecia(ws):
        wc = min((len(l) - len(l.lstrip(" ")) for l in ws if l.strip()), default=0)
        return [l[wc:].rstrip() for l in ws]
    out, przypisy, blok = [], [], []

    def zamknij():
        # Wcięte wiersze tylko lewego łamu po ostatnim wierszu z prawym łamem, oddzielone ≥2 pustymi
        # wierszami („PRZYJMUJE NINIEJSZE ROZPORZĄDZENIE:” pod motywami w dwóch łamach), stoją POD oboma
        # łamami. Dalszy ciąg lewego łamu zaczyna się na marginesie, więc zostaje w łamie.
        ostatni = max((i for i, l in enumerate(blok) if l[rynna:].strip()), default=None)
        pod = []
        if ostatni is not None:
            k = ostatni + 1
            while k < len(blok) and not blok[k].strip():
                k += 1
            if k - ostatni - 1 >= 2 and k < len(blok) and \
                    len(blok[k]) - len(blok[k].lstrip(" ")) >= _PDF_WCIECIE_NAGLOWKA:
                pod = blok[k:]
                del blok[k:]
        tresc = [l.strip() for l in blok if l.strip()]
        tabela = sum(len(re.findall(r"\S {3,}(?=\S)", l)) >= 2 for l in tresc) >= _PDF_TABELA * len(tresc)
        if not tresc or tabela:
            out.extend(blok)
        else:
            for lam in (bez_wciecia([l[:rynna] for l in blok]), bez_wciecia([" " * rynna + l[rynna:] for l in blok])):
                t, p = _pdf_przypisy(_pdf_oznacz_srodek(lam, max((len(l) for l in lam), default=0)))
                out.extend(t)
                przypisy.extend(p)
        blok.clear()
        out.extend(_pdf_oznacz_srodek(pod))
    for l in lines:
        dzieli = not l.strip() or wolne(l, rynna) and (
            l[rynna:].strip() or len(l) - len(l.lstrip(" ")) < _PDF_MIN_LAM * szer)
        if dzieli:
            blok.append(l)
        else:
            zamknij()
            out.extend(_pdf_oznacz_srodek([l]))
    zamknij()
    t, p = _pdf_przypisy(out)   # przypisy pod tekstem na całą szerokość strony
    return t, przypisy + p


def _doklej(a, b):
    """Łączy wiersz zawinięty w PDF: „zabez-" + „pieczenia" → „zabezpieczenia" (dzielenie wyrazów),
    inaczej przez spację. Heurystyka: myślnik na końcu + mała litera na początku następnego wiersza."""
    if not a:
        return b
    if a.endswith(("-", "\xad")) and b[:1].islower():
        return a[:-1] + b
    return a + " " + b


def _pdf_akapity(lines):
    """Wiersze w kolejności czytania → akapity: zawinięte wiersze sklejone, nagłówki osobno.

    Pusty wiersz w łamie bywa artefaktem -layout (wiersz, w którym tekst ma tylko DRUGI łam), więc
    kończy akapit tylko wtedy, gdy ten kończy się jak zdanie, a następny wiersz nie zaczyna się małą
    literą; zawsze — przed znacznikiem jednostki („a)”, „1.”, „(5)”, „Artykuł N”)."""
    akapity, akapit, poprz_osobny, przerwa = [], None, False, False
    for l in lines:
        srodek = l.startswith(_PDF_SRODEK)
        l = l.lstrip(_PDF_SRODEK)
        t = l.strip()
        if not t:
            przerwa = True
            continue
        tabela = len(re.findall(r"\S {3,}(?=\S)", t)) >= 2
        if not tabela:
            t = re.sub(r" {2,}", " ", t)
        osobny = srodek or tabela or bool(_PDF_ARTYKUL.fullmatch(t))
        nowy = (akapit is None or osobny or poprz_osobny or _PDF_NOWY_AKAPIT.match(t)
                or przerwa and _PDF_KONIEC_ZDANIA.search(akapit) and not t[0].islower())
        if nowy:
            if akapit is not None:
                akapity.append(akapit)
            akapit = t
        else:
            akapit = _doklej(akapit, t)
        poprz_osobny, przerwa = osobny, False
    if akapit is not None:
        akapity.append(akapit)
    return akapity


def pdf_do_tekstu(raw):
    """Tekst z `pdftotext -layout` (PDF Dz.Urz. UE) → (tekst, liczba stron bez warstwy tekstowej, stron).

    Strony bez nagłówków i numerów, łamy rozdzielone, wiersze sklejone w akapity; „Artykuł N” stoi
    w osobnej linii (jak w ścieżce XHTML — tego szuka --fragment). Przypisy z dołu łamów trafiają
    na koniec, za znak GRANICA — fragment artykułu nie ciągnie ich ze sobą."""
    strony = raw.split("\f")
    if strony and not strony[-1].strip():
        strony = strony[:-1]
    wiersze, przypisy, puste = [], [], 0
    for strona in strony:
        lines = _pdf_bez_naglowka(strona.split("\n"))
        if len(re.sub(r"[\W\d_]", "", "\n".join(lines))) < 20:
            puste += 1
        tresc, przyp = _pdf_lamy(lines)
        wiersze.extend(tresc)
        przypisy.extend(przyp)
    out = []
    for a in _pdf_akapity(wiersze):
        if _PDF_ARTYKUL.fullmatch(a) or re.match(r"(?:ROZDZIAŁ|TYTUŁ|ZAŁĄCZNIK|CHAPTER|TITLE|ANNEX)\b", a):
            out.append("")
        out.append(a)
    p = _pdf_akapity(przypisy)
    if p:
        out += ["", GRANICA, "Przypisy (z dołu stron PDF):"] + p
    return "\n".join(out).strip(), puste, len(strony)


def _tekst_z_pdf(celex, lang, lang3, brak_html):
    """Tekst aktu z jego urzędowego PDF, gdy CELLAR nie ma HTML/XHTML w tym języku.

    Zwraca (tekst, źródło PDF, pozostałe pliki manifestacji, linie ostrzeżeń). Bez pdftotext
    kończy dotychczasowym komunikatem `brak_html` (odesłanie do --pdf) z podpowiedzią instalacji."""
    if not pdftotext_dostepny():
        sys.exit(f"{brak_html.code} Tekst z tego PDF silnik wyekstrahuje sam, gdy w PATH będzie pdftotext "
                 "(poppler: brew install poppler / apt install poppler-utils).")
    try:
        data, zrodlo, inne = _pobierz_pdf(celex, lang, lang3)
    except SystemExit as e:
        sys.exit(f"{brak_html.code}\nNie udało się też pobrać tego PDF do ekstrakcji tekstu: {e.code}")
    raw = _pdftotext_layout(data)
    txt, puste, stron = pdf_do_tekstu(raw) if raw.strip() else ("", 0, 0)
    if not _bez_granic(txt).strip():
        sys.exit(f"BŁĄD: akt {celex} jest w CELLAR w języku {lang3} tylko jako PDF, a pdftotext nie zwrócił "
                 f"z niego tekstu (PDF bez warstwy tekstowej — skan — albo błąd konwersji). Pobierz PDF: "
                 f"tekst {celex} --jezyk {lang3} --pdf plik.pdf (źródło: {zrodlo})")
    uwagi = [f"EURLEX_TEXT_SOURCE_PDF={zrodlo}",
             f"UWAGA: CELLAR nie ma wersji HTML/XHTML aktu {celex} w języku {lang3} — poniżej tekst "
             "WYEKSTRAHOWANY z urzędowego PDF (Dz.Urz. UE). Rozdzielenie łamów, sklejanie wierszy i dzielonych "
             "wyrazów oraz usunięcie nagłówków stron są automatyczne (tabele bywają rozsypane); przypisy z dołu "
             "stron są zebrane na końcu. "
             f"Do dosłownego cytatu: tekst {celex} --jezyk {lang3} --pdf plik.pdf"]
    if inne:
        uwagi.append(f"UWAGA: PDF tego aktu ma kilka plików — tekst pochodzi z pierwszego; pozostałe: {', '.join(inne)}")
    if puste:
        uwagi.append(f"UWAGA: {puste} z {stron} stron tego PDF nie ma warstwy tekstowej (skan albo strona pusta) "
                     f"— ich treści NIE MA poniżej. Sprawdź PDF: tekst {celex} --jezyk {lang3} --pdf plik.pdf")
    return txt, zrodlo, inne, uwagi


def _pobierz_tekst(celex, lang3):
    """Bajty XHTML/HTML aktu w danym języku.

    Najpierw negocjacja treści (XHTML, a gdy go brak — HTML). Gdy CELLAR odpowie 404/300 na akt
    spoza wersji skonsolidowanych, adresy plików bierzemy z SPARQL (manifestacja xhtml/html →
    elementy DOC_n) i sklejamy je; jeśli takiej manifestacji nie ma, komunikat mówi, CO jest
    dostępne (inne języki, sam PDF), a „sprawdź numer CELEX" tylko wtedy, gdy aktu nie ma w metadanych."""
    url = CELLAR + urllib.parse.quote(celex, safe="/")
    try:
        raw, _ = _http(url, headers={"Accept": AKCEPT_TEKST, "Accept-Language": lang3})
        return raw
    except SystemExit as e:
        kod = "404" if "(404)" in str(e) else ("300" if "zwrócił 300" in str(e) else None)
        if not kod or re.match(r"^0.*-\d{8}$", celex):
            raise
    lang = lang3.upper()
    try:
        rows = _manifestacje(celex)
    except VerificationUnknown as e:
        sys.exit(f"BŁĄD: CELLAR nie zwrócił tekstu {celex} w języku {lang3} ({kod}), a nie udało się "
                 f"sprawdzić w metadanych, czy akt istnieje ({e}) — sprawdź: meta {celex}.")
    if not rows:
        sys.exit(f"BŁĄD: nie znaleziono aktu {celex} w CELLAR ({kod}; brak także w metadanych). "
                 "Sprawdź numer CELEX (szukaj \"<fraza>\").")
    _, _, items = _elementy(rows, _TYPY_TEKSTU, lang)
    if items:
        return b"\n".join(_http(i)[0] for i in items)
    w_jezyku = sorted({_v(b, "mtype") for b in rows if _v(b, "l") == lang and _v(b, "mtype")})
    z_tekstem = sorted({_v(b, "l").lower() for b in rows if _v(b, "mtype") in _TYPY_TEKSTU})
    if any(t.startswith("pdf") for t in w_jezyku):
        raise _TylkoPdf(f"BŁĄD: akt {celex} istnieje w CELLAR, ale w języku {lang3} nie ma wersji HTML/XHTML "
                        f"(formaty: {', '.join(w_jezyku)}) — pobierz urzędowy PDF: tekst {celex} --jezyk {lang3} "
                        "--pdf plik.pdf" + (f"; tekst HTML jest w: {', '.join(z_tekstem)}" if z_tekstem else "") + ".")
    if z_tekstem:
        sys.exit(f"BŁĄD: akt {celex} istnieje w CELLAR, ale nie ma tekstu w języku {lang3} — "
                 f"tekst HTML/XHTML jest w: {', '.join(z_tekstem)} (użyj --jezyk).")
    sys.exit(f"BŁĄD: akt {celex} istnieje w CELLAR, ale nie udostępnia tekstu HTML/XHTML w żadnym języku "
             f"— spróbuj --pdf albo sprawdź: meta {celex}.")


# --- sprostowania ---------------------------------------------------------------------------------
# Sprostowanie (CELEX z sufiksem R(nn), relacja cdm:resource_legal_corrects_resource_legal) dotyczy
# KONKRETNYCH wersji językowych: RODO ma R(01) (de, et, hu, it), R(02) i R(03) (m.in. pl). Tekst aktu
# bazowego z CELLAR to brzmienie z Dz.U. sprzed sprostowań — w PL art. 4 pkt 1 „informacje" zamiast
# „wszelkie informacje", art. 10, art. 82 ust. 2. Wersja skonsolidowana sprostowanie zawiera
# (cdm:act_consolidated_consolidates_resource_legal wymienia R(nn)).
_MIEJSCE_SPROSTOWANIA = re.compile(
    r"(?im)^(?:on\s+)?(?:strona|strony|str\.|page|pages|seite|seiten)\s+[^,\n]{1,40},\s*([^\n]{1,200}?)\s*:\s*$")
_ART_SPROSTOWANIA = re.compile(r"(?i)\b(?:art\.|artykuł|article|artikel)\s*(\d+[a-z]?)\b")
_MAKS_ZAKRESOW = 8  # ile tekstów sprostowań pobieramy, by ustalić ich zakres


def _celex_bazowy(celex):
    return ("3" + celex[1:].split("-")[0]) if re.match(r"^0.*-\d{8}$", celex) else celex


def _sprostowania(celex, lang):
    """Sprostowania aktu (bazowego) opublikowane w danym języku:
    [{"celex", "data", "konsolidacje": [wersje skonsolidowane, które je wymieniają]}].

    [] = VERIFIED_ABSENT; awaria → VerificationUnknown (nigdy pusta lista). Sprostowania samego
    sprostowania (CELEX z R(nn)) nie szukamy."""
    if "R(" in celex:
        return []
    baza = _celex_bazowy(celex)
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?c2 ?date ?kc WHERE {{
  ?w cdm:resource_legal_id_celex "{baza}"^^<{XSD_STR}> .
  ?x cdm:resource_legal_corrects_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
  FILTER EXISTS {{ ?e cdm:expression_belongs_to_work ?x .
                   ?e cdm:expression_uses_language <{LANG_AUTH}{lang}> }}
  OPTIONAL {{ ?x cdm:work_date_document ?date }}
  OPTIONAL {{ ?k cdm:act_consolidated_consolidates_resource_legal ?x . ?k cdm:resource_legal_id_celex ?kc }}
}} ORDER BY ?c2 LIMIT 500""", soft=True)
    out = {}
    for b in rows:
        c2 = _v(b, "c2")
        if not c2:
            continue
        s = out.setdefault(c2, {"celex": c2, "data": _v(b, "date"), "konsolidacje": []})
        kc = _v(b, "kc")
        if re.search(r"-\d{8}$", kc) and kc not in s["konsolidacje"]:
            s["konsolidacje"].append(kc)
    for s in out.values():
        s["konsolidacje"].sort(reverse=True)
    return [out[c] for c in sorted(out)]


def _zakres_z_tekstu(txt):
    """Miejsca sprostowania z nagłówków „Strona 33, art. 4 ust. 1:" / „Page 33, Article 4(1):".
    Zwraca {"art": [numery artykułów], "inne": [motywy/załączniki/inne miejsca]}."""
    arts, inne = [], set()
    for m in _MIEJSCE_SPROSTOWANIA.finditer(txt):
        miejsce = m.group(1)
        nr = _ART_SPROSTOWANIA.findall(miejsce)
        for n in nr:
            if n not in arts:
                arts.append(n)
        if nr:
            continue
        if re.search(r"(?i)motyw|recital|erwägungsgrund|considérant", miejsce):
            inne.add("motywy")
        elif re.search(r"(?i)załącznik|annex|anhang", miejsce):
            inne.add("załączniki")
        else:
            inne.add("inne miejsca")
    arts.sort(key=lambda n: (int(re.match(r"\d+", n).group()), n))
    return {"art": arts, "inne": sorted(inne)}


def _zakres_sprostowania(celex_r, lang3):
    """Zakres sprostowania z jego tekstu albo None (tekstu nie udało się pobrać — informacja poboczna,
    o istnieniu sprostowania przesądza już SPARQL)."""
    try:
        raw = _pobierz_tekst(celex_r, lang3)
    except (SystemExit, VerificationUnknown):
        return None
    return _zakres_z_tekstu(_bez_granic(html_to_text(raw.decode("utf-8", "replace"))))


def _nr_artykulu(fraza):
    m = re.match(r"(?i)^art(?:\.|ykuł|icle|ikel)?\s*(\d+[a-z]*)\.?$", (fraza or "").strip())
    return m.group(1) if m else None


def _gdzie_sprostowane(sprost, kons=None):
    """Wersja skonsolidowana z brzmieniem po sprostowaniu albo None.

    Nowsze wersje skonsolidowane są kumulatywne, ale w metadanych NIE powtarzają sprostowań już
    ujętych: R(01)–R(04) AI Act wymienia tylko 02024R1689-20240712 (wersja zastąpiona, CELLAR jej nie
    serwuje), a nie 02024R1689-20260727. Wskazujemy więc najnowszą wersję (kons[0]), jeśli jest nie
    starsza od wersji wymieniającej sprostowanie."""
    wymienia = sorted({k for s in sprost for k in s["konsolidacje"]}, reverse=True)
    if not wymienia:
        return None
    return kons[0] if kons and kons[0] > wymienia[0] else wymienia[0]


def _ostrzezenia_sprostowan(celex, lang, sprost, fragment=None, kons=None):
    """Linie ostrzeżeń o sprostowaniach przy TREŚCI aktu (tekst / --pdf)."""
    lang3 = lang.lower()
    if not sprost:
        return []
    if re.match(r"^0.*-\d{8}$", celex):
        # wersja skonsolidowana: ostrzegamy tylko o sprostowaniach, których nie wymienia ani ta, ani
        # żadna wcześniejsza wersja (nowsze wersje nie powtarzają w metadanych sprostowań już ujętych)
        brak = [s for s in sprost if not any(k <= celex for k in s["konsolidacje"])]
        return [f"UWAGA: sprostowanie {s['celex']}" + (f" ({s['data']})" if s["data"] else "")
                + f" w języku {lang3} nie figuruje w składzie tej ani wcześniejszej wersji skonsolidowanej — "
                f"sprawdź jego treść: tekst {s['celex']} --jezyk {lang3}." for s in brak]
    cel = _gdzie_sprostowane(sprost, kons)
    out, trafione = [], []
    nr = _nr_artykulu(fragment)
    linie = []
    for i, s in enumerate(sprost):
        zakres = _zakres_sprostowania(s["celex"], lang3) if i < _MAKS_ZAKRESOW else None
        if zakres is None:
            opis = "zakres nieustalony — sprawdź treść sprostowania"
        else:
            czesci = ([("art. " + ", ".join(zakres["art"]))] if zakres["art"] else []) + zakres["inne"]
            opis = "; ".join(czesci) or "zakres nieustalony — sprawdź treść sprostowania"
            if nr and nr in zakres["art"]:
                trafione.append(s["celex"])
        gdzie = (f" — w składzie wersji skonsolidowanej {', '.join(s['konsolidacje'])}" if s["konsolidacje"]
                 else " — NIE figuruje w żadnej wersji skonsolidowanej")
        linie.append(f"  - {s['celex']}" + (f" ({s['data']})" if s["data"] else "") + f": {opis}{gdzie}")
    if trafione:
        out.append(f"UWAGA: art. {nr} SPROSTOWANO ({', '.join(trafione)}) — poniższe brzmienie aktu bazowego "
                   "jest NIESPROSTOWANE. Poprawne brzmienie: "
                   + (f"tekst {cel} --jezyk {lang3} --fragment \"art. {nr}\" albo " if cel else "")
                   + f"treść sprostowania: tekst {trafione[0]} --jezyk {lang3}.")
    out.append(f"UWAGA: akt ma SPROSTOWANIA w języku {lang3} — tekst aktu bazowego (brzmienie z Dz.U.) "
               "ich NIE uwzględnia:")
    out.extend(linie)
    out.append("  Brzmienie po sprostowaniu: "
               + (f"tekst {cel} --jezyk {lang3} --fragment \"art. N\" (wersja skonsolidowana); " if cel else "")
               + f"treść sprostowania: tekst {sprost[0]['celex']} --jezyk {lang3}.")
    return out


def _kontrole_tresci(celex, lang, strict=False, fragment=None):
    """Ostrzeżenia przy TREŚCI aktu (tekst / --pdf): wersje skonsolidowane + sprostowania.

    Liczone PRZED wydrukiem — w strict blokada nie może nastąpić po wypisaniu treści. Strict blokuje
    tekst aktu bazowego, gdy istnieje sprostowanie w tym języku (tekst jest niesprostowany) — tak samo
    jak blokuje go, gdy istnieje wersja skonsolidowana."""
    lang3 = lang.lower()
    baza = _celex_bazowy(celex)
    try:
        kons = _konsolidacje(celex)
    except VerificationUnknown:
        if strict:
            raise
        kons = None  # _ostrzezenia_konsolidacja ponowi próbę i wypisze ostrzeżenie o awarii
    try:
        sprost = _sprostowania(celex, lang)
        uwaga = None
    except VerificationUnknown as e:
        if strict:
            raise
        sprost, uwaga = [], (f"UWAGA: nie udało się zweryfikować, czy akt {baza} ma sprostowania w języku "
                             f"{lang3} ({e}) — sprawdź: odniesienia {baza}, zanim zacytujesz.")
    if strict and sprost and not re.match(r"^0.*-\d{8}$", celex):
        cel = _gdzie_sprostowane(sprost, kons)
        lista = ", ".join(s["celex"] + (f" z {s['data']}" if s["data"] else "") for s in sprost)
        sys.exit(f"BŁĄD: akt {celex} ma sprostowania w języku {lang3} ({lista}) — tekst aktu bazowego ich NIE "
                 "uwzględnia. Tryb strict blokuje niesprostowany tekst; do analizy: "
                 + (f"tekst {cel} --jezyk {lang3} --fragment \"art. N\" (wersja skonsolidowana ze "
                    "sprostowaniem) albo " if cel else "")
                 + f"tekst {sprost[0]['celex']} --jezyk {lang3} (treść sprostowania). Do urzędowego cytatu "
                 f"wskaż {CYTAT_URZEDOWY}. Bez --strict tekst aktu bazowego jest dostępny z ostrzeżeniem.")
    out = _ostrzezenia_konsolidacja(celex, strict, kons=kons)
    if uwaga:
        out.append(uwaga)
    return out + _ostrzezenia_sprostowan(celex, lang, sprost, fragment, kons)


def cmd_tekst(a):
    celex = celex_norm(a.celex)
    lang = _lang(a.jezyk)
    lang3 = lang.lower()
    strict = getattr(a, "strict", False)
    if a.pdf:
        ostrz = _kontrole_tresci(celex, lang, strict) if strict else None
        data, zrodlo, inne = _pobierz_pdf(celex, lang, lang3)
        with open(a.pdf, "wb") as f:
            f.write(data)
        print(f"Zapisano PDF ({len(data)} B): {a.pdf}\n(źródło: {zrodlo}, język {lang3})")
        if inne:
            print(f"UWAGA: PDF tego aktu ma kilka plików — zapisano pierwszy; pozostałe: {', '.join(inne)}")
        for w in (ostrz if ostrz is not None else _kontrole_tresci(celex, lang)):
            print(w)
        return
    uwagi_pdf = None
    try:
        raw = _pobierz_tekst(celex, lang3)
    except _TylkoPdf as e:
        # akt tylko w PDF: tekst z własnego urzędowego PDF aktu — --strict go przepuszcza (to jest
        # tekst TEGO aktu), ale blokady _kontrole_tresci (sprostowania, konsolidacje) działają dalej
        txt, _, _, uwagi_pdf = _tekst_z_pdf(celex, lang, lang3, e)
    except SystemExit as e:
        if "(404)" in str(e) and re.match(r"^0.*-\d{8}$", celex):
            _wyjasnij_404_konsolidacji(celex, lang3)
        raise
    if uwagi_pdf is None:
        txt = html_to_text(raw.decode("utf-8", "replace"))
        if not _bez_granic(txt).strip():
            sys.exit(f"Pusty tekst XHTML/HTML dla {celex} (język {lang3}) — spróbuj --pdf albo inny --jezyk.")
    ostrz = _kontrole_tresci(celex, lang, strict, a.fragment)
    if uwagi_pdf is not None:
        print(f"# CELEX {celex} ({lang3}) — tekst z urzędowego PDF przez pdftotext -layout "
              "(do dosłownego cytatu zweryfikuj z PDF)\n")
        ostrz = uwagi_pdf + ostrz
    else:
        print(f"# CELEX {celex} ({lang3}) — tekst z CELLAR (XHTML/HTML→tekst; do dosłownego cytatu zweryfikuj z PDF)\n")
    for w in ostrz:
        print(w)
    if ostrz:
        print()
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w tekście aktu ({len(txt)} znaków). "
                     "Spróbuj inną frazą, w innym języku, albo bez --fragment.")
        poza = 0
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            gdzie = _poza_aktem(txt, s)
            if gdzie:
                poza += 1
                print(f"[{gdzie}]\n")
            print(_bez_granic(txt[s:e]).strip())
        print(f"\n(fragmenty: {len(spans)}"
              + (f", w tym {poza} z załącznika / dokumentu dołączonego (oznaczone „[w …]”)" if poza else "")
              + " — pominięto resztę aktu (w tym podpisy i przypisy końcowe); pełny tekst: bez --fragment)")
        return
    txt = _bez_granic(txt)
    if len(txt) > 60000:
        print(f"(UWAGA: pełny tekst ma {len(txt)} znaków — do pojedynczego przepisu użyj --fragment \"art. N\")\n")
    print(txt)


def _wyjasnij_404_konsolidacji(celex, lang3):
    """404 na wersji skonsolidowanej: numer bywa poprawny (figuruje na liście wersji), ale CELLAR
    nie serwuje treści wersji zastąpionych — zwłaszcza pierwszej, tożsamej z aktem bazowym
    (np. 02024R1689-20240712). Nie każ wtedy „sprawdzać numeru CELEX"."""
    baza = "3" + celex[1:].split("-")[0]
    try:
        kons = _konsolidacje(celex)
    except VerificationUnknown as e:
        sys.exit(f"BŁĄD: CELLAR nie udostępnia tekstu wersji skonsolidowanej {celex} (404), a listy wersji "
                 f"nie udało się zweryfikować ({e}) — sprawdź: skonsolidowany {baza}.")
    if celex in kons and kons[0] != celex:
        sys.exit(f"BŁĄD: CELLAR nie udostępnia już tekstu wersji skonsolidowanej {celex} (404). Numer jest "
                 "poprawny i figuruje na liście wersji, ale Urząd Publikacji nie serwuje treści tej "
                 f"ZASTĄPIONEJ wersji (ani w EUR-Lex). Najnowsza wersja: {kons[0]} → "
                 f"tekst {kons[0]} --fragment \"art. N\"; pełna lista: skonsolidowany {baza}; "
                 f"stan pierwotny: tekst {baza}.")
    if kons and kons[0] == celex:
        sys.exit(f"BŁĄD: CELLAR nie udostępnia tekstu najnowszej wersji skonsolidowanej {celex} w języku "
                 f"{lang3} (404) — spróbuj --jezyk eng albo --pdf; akt bazowy: tekst {baza}.")
    sys.exit(f"BŁĄD: CELLAR nie zna wersji skonsolidowanej {celex} (404) — dostępne wersje: "
             f"skonsolidowany {baza}" + (f" (najnowsza: {kons[0]})" if kons else " (brak)") + ".")


# kolejność sekcji w odniesieniach: najpierw to, co decyduje o mocy obowiązującej aktu
_KIERUNKI = ("UCHYLONY PRZEZ (akt uchylający ten akt)",
             "Uchylony w sposób dorozumiany przez",
             "Nowelizacje (akty zmieniające ten akt)",
             "Sprostowania",
             "Uchyla (akty uchylone przez ten akt)",
             "Uchyla w sposób dorozumiany (przepisy tracące moc przez ten akt)",
             "Zmienia (akty zmieniane przez ten akt)",
             "Podstawa prawna (traktatowa)")


def cmd_odniesienia(a):
    celex = celex_norm(a.celex)
    # Relacje BEZ statusu aktu: z ?inf/?eov w tym samym zapytaniu akt z kilkoma datami końca
    # (32009R0470: 2022-01-27 + 9999-12-31) dawał iloczyn kartezjański — każda relacja 2× („Sprostowania
    # (4)" zamiast 2, 268 uchyleń dorozumianych zamiast 134), a LIMIT mieścił połowę relacji.
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?kier ?c2 WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  {{ ?x cdm:resource_legal_repeals_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[0]}" AS ?kier) }}
  UNION
  {{ ?x cdm:resource_legal_implicitly_repeals_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[1]}" AS ?kier) }}
  UNION
  {{ ?x cdm:resource_legal_amends_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[2]}" AS ?kier) }}
  UNION
  {{ ?x cdm:resource_legal_corrects_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[3]}" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_repeals_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[4]}" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_implicitly_repeals_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[5]}" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_amends_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[6]}" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_based_on_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("{_KIERUNKI[7]}" AS ?kier) }}
}} ORDER BY ?kier DESC(?c2) LIMIT 1000""")
    widziane, relacje = set(), []
    for b in rows:  # obrona przed duplikatami także przy innym kształcie odpowiedzi
        k = (_v(b, "kier"), _v(b, "c2"))
        if k[1] and k not in widziane:
            widziane.add(k)
            relacje.append(b)
    if len(rows) >= 1000:
        print("UWAGA: CELLAR zwrócił limit 1000 relacji — lista może być niepełna (EUR-Lex: strona aktu).",
              file=sys.stderr)
    rows = relacje
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    print(f"Odniesienia dla: CELEX {celex}\n")
    if not rows:
        print("Brak odnotowanych relacji w CELLAR (albo zły CELEX — sprawdź: meta).")
        return
    grupy = {}
    for b in rows:
        grupy.setdefault(_v(b, "kier"), []).append(_v(b, "c2"))
    try:
        zb = _zbierz(_sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?inf ?eov WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  OPTIONAL {{ ?w cdm:resource_legal_date_end-of-validity ?eov }}
}}""", soft=True), ("inf", "eov"))
    except VerificationUnknown as e:
        zb = {"inf": [], "eov": []}
        print(f"UWAGA: nie udało się pobrać statusu aktu ({e}) — status i daty: meta {celex}.\n")
    uchylony = grupy.get(_KIERUNKI[0], [])
    koniec, czesc = _koniec_obowiazywania(zb["eov"])
    eov = koniec if koniec and koniec != "9999-12-31" else ""
    if uchylony:
        print(f"AKT UCHYLONY przez {', '.join(uchylony)}"
              + (f" (koniec obowiązywania: {eov})" if eov else "")
              + f" — {_status(zb) if zb['inf'] else 'NIE OBOWIĄZUJE'}; aktualny stan prawny czytaj z aktu uchylającego.\n")
    elif _status(zb) == "NIE OBOWIĄZUJE":
        print("AKT NIE OBOWIĄZUJE" + (f" (koniec obowiązywania: {eov})" if eov else "")
              + " — w CELLAR brak relacji „uchylony przez\"; sprawdź: meta.\n")
    if czesc:
        print(f"Częściowy upływ ważności (wybrane przepisy): {', '.join(czesc)} — zakres: meta {celex}; "
              "to NIE koniec obowiązywania aktu.\n")
    for kier in sorted(grupy, key=lambda k: _KIERUNKI.index(k) if k in _KIERUNKI else 99):
        lst = grupy[kier]
        print(f"## {kier}  ({len(lst)})")
        for c in lst:
            print(f"  - {c}")
        print()
    if _KIERUNKI[2] in grupy and not uchylony:
        print(f"Akt był zmieniany → aktualny stan czytaj z wersji skonsolidowanej: skonsolidowany {celex}")


def main():
    ap = argparse.ArgumentParser(
        description="CELLAR/EUR-Lex (read-only, bez klucza). Źródło pierwotne prawa UE.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    ap.add_argument("--strict", action="store_true",
                    help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("szukaj"); s.add_argument("fraza", nargs="?")
    s.add_argument("--typ", help="typ aktu: REG (rozporządzenie), DIR (dyrektywa), DEC (decyzja)")
    s.add_argument("--rok"); s.add_argument("--jezyk", default="pol")
    s.add_argument("--obowiazujace", action="store_true")
    s.add_argument("--limit", type=int, default=10); s.set_defaults(func=cmd_szukaj)

    for name, fn in (("odniesienia", cmd_odniesienia), ("skonsolidowany", cmd_skonsolidowany)):
        p = sub.add_parser(name); p.add_argument("celex", nargs="+"); p.set_defaults(func=fn)

    m = sub.add_parser("meta"); m.add_argument("celex", nargs="+")
    m.add_argument("--jezyk", default="pol"); m.set_defaults(func=cmd_meta)

    t = sub.add_parser("tekst"); t.add_argument("celex", nargs="+")
    t.add_argument("--jezyk", default="pol"); t.add_argument("--pdf")
    t.add_argument("--fragment", help='wytnij tylko jednostki z frazą, np. "art. 6" albo "profilowanie"')
    t.set_defaults(func=cmd_tekst)

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
        _nie_zweryfikowano("danych w EUR-Lex", e)


if __name__ == "__main__":
    main()
