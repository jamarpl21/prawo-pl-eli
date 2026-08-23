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
                                 tekst aktu z CELLAR (XHTML → czysty tekst); --fragment wycina
                                 tylko jednostki z frazą; --pdf zapisuje urzędowy PDF
  skonsolidowany <CELEX>         wersje skonsolidowane aktu (odpowiednik tekstu jednolitego)
  odniesienia <CELEX>            nowelizacje, sprostowania, uchylenia (w obie strony), podstawa prawna
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności)

CELEX np.: 32016R0679 (RODO), 02016R0679-20160504 (wersja skonsolidowana), reg/2016/679 (ELI).
"""
import sys, json, re, time, argparse, textwrap, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.0.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR = "http://publications.europa.eu/resource/celex/"
CDM = "http://publications.europa.eu/ontology/cdm#"
LANG_AUTH = "http://publications.europa.eu/resource/authority/language/"
TYPE_AUTH = "http://publications.europa.eu/resource/authority/resource-type/"
XSD_STR = "http://www.w3.org/2001/XMLSchema#string"
CONTENT_HOSTS = ("publications.europa.eu", "data.europa.eu")

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


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "div", "hr"):
            klasy = (dict(attrs).get("class") or "").split()
            if _KLASY_GRANIC.intersection(klasy):
                self.out.append("\n" + GRANICA + "\n")
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
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
    return t.replace(GRANICA + "\n", "").replace(GRANICA, "")


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
            + GRANICA + r"|Sporządzono w\b|Done at\b|Geschehen zu\b|Fait à\b|"
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
                   "do urzędowego cytatu wskaż akt bazowy + zmiany.")
        if kons and kons[0] > celex:
            if blokuj:
                sys.exit(f"BŁĄD: istnieje nowsza wersja skonsolidowana: {kons[0]}. "
                         "Tryb strict blokuje starszą wersję (jej treść i stan na dzień).")
            out.append(f"UWAGA: istnieje NOWSZA wersja skonsolidowana: {kons[0]} — używaj jej.")
    elif kons:
        if blokuj:
            sys.exit(f"BŁĄD: akt ma wersje skonsolidowane — aktualny stan prawny to {kons[0]}. "
                     f"Tryb strict blokuje treść aktu bazowego; do analizy: tekst {kons[0]} "
                     "(wersja skonsolidowana jest dokumentacyjna — do urzędowego cytatu wskaż akt "
                     "bazowy + zmiany; bez --strict tekst aktu bazowego jest dostępny).")
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


_META_POLA = ("type", "date", "inf", "eli", "eiv", "eov", "trans", "kons_data", "baza", "sklad", "title")


def _meta_wiersze(celex, lang):
    """Metadane pracy (work) o danym CELEX — surowe wiersze SPARQL.

    Daty: cdm:resource_legal_date_entry-into-force trzyma ZARÓWNO wejście w życie, jak i daty
    rozpoczęcia stosowania (CELLAR ich nie rozróżnia — osobnej właściwości „data stosowania"
    w CDM nie ma); cdm:directive_date_transposition — termin(y) transpozycji dyrektywy.
    Wersja skonsolidowana (sektor 0): cdm:act_consolidated_date = „stan na",
    cdm:act_consolidated_based_on_resource_legal = akt bazowy,
    cdm:act_consolidated_consolidates_resource_legal = akty ujęte w konsolidacji."""
    return _sparql(f"""PREFIX cdm: <{CDM}>
SELECT ?type ?date ?inf ?eli ?eiv ?eov ?trans ?kons_data ?baza ?sklad ?title WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?w cdm:work_has_resource-type ?type }}
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  OPTIONAL {{ ?w cdm:resource_legal_eli ?eli }}
  OPTIONAL {{ ?w cdm:resource_legal_date_entry-into-force ?eiv }}
  OPTIONAL {{ ?w cdm:resource_legal_date_end-of-validity ?eov }}
  OPTIONAL {{ ?w cdm:directive_date_transposition ?trans }}
  OPTIONAL {{ ?w cdm:act_consolidated_date ?kons_data }}
  OPTIONAL {{ ?w cdm:act_consolidated_based_on_resource_legal ?b . ?b cdm:resource_legal_id_celex ?baza }}
  OPTIONAL {{ ?w cdm:act_consolidated_consolidates_resource_legal ?s . ?s cdm:resource_legal_id_celex ?sklad }}
  OPTIONAL {{ ?exp cdm:expression_belongs_to_work ?w .
              ?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> .
              ?exp cdm:expression_title ?title }}
}}""")


def _zbierz(rows, pola=_META_POLA):
    return {k: sorted({_v(b, k) for b in rows if _v(b, k)}) for k in pola}


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


def _drukuj_daty(zb, wc="  "):
    """Daty aktu (bazowego) z uczciwymi etykietami: jedna data = wejście w życie; kilka dat =
    wejście w życie + daty stosowania, których CELLAR nie rozróżnia; dyrektywa = termin transpozycji."""
    typy = {t.rsplit("/", 1)[-1] for t in zb["type"]}
    if zb["date"]:
        print(f"{wc}Data aktu: {zb['date'][0]}")
    eiv = zb["eiv"]
    if len(eiv) == 1:
        print(f"{wc}Wejście w życie: {eiv[0]}")
    elif eiv:
        print(f"{wc}Wejście w życie / stosowanie: {', '.join(eiv)}")
        print(f"{wc}  (kilka dat — CELLAR nie opisuje, która to wejście w życie, a która rozpoczęcie "
              "stosowania; najwcześniejsza to z reguły wejście w życie — sprawdź przepisy końcowe aktu!)")
    if zb["trans"]:
        print(f"{wc}Termin transpozycji: {', '.join(zb['trans'])}"
              + ("   (kilka terminów — różne zakresy; sprawdź przepis o transpozycji)"
                 if len(zb["trans"]) > 1 else ""))
        print(f"{wc}  (dyrektywa działa przez transpozycję — polską ustawę wdrażającą sprawdź "
              "skillem prawo-pl-eli)")
    elif "DIR" in typy:
        print(f"{wc}Termin transpozycji: brak w CELLAR — sprawdź przepis o transpozycji w tekście dyrektywy")
    if zb["eov"] and zb["eov"][0] != "9999-12-31":
        print(f"{wc}Koniec obowiązywania: {zb['eov'][0]}")
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
    if a.json:
        # "meta" = surowe wiersze SPARQL tej pracy (na wersji skonsolidowanej: eiv/date = „stan na");
        # "akt_bazowy" = zebrane metadane aktu bazowego; "zmieniajace" = CELEX-y nowelizacji;
        # "ostrzezenia" = te same linie, które widzi człowiek
        out = {"celex": celex, "wersja_skonsolidowana": kons_wersja, "meta": rows,
               "zmieniajace": zmiany, "wersje_skonsolidowane": kons, "ostrzezenia": ostrz}
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
        _drukuj_daty(zb_baza, wc="    ")
        if zb_baza["eli"]:
            print(f"    ELI:     {zb_baza['eli'][0]}")
    else:
        _drukuj_daty(zb)
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
        print(f"Brak wersji skonsolidowanych dla {celex} — akt nie był zmieniany "
              f"(cytuj z aktu bazowego: tekst {celex}); sprawdź sprostowania: odniesienia {celex}.")
        return
    print(f"WERSJE SKONSOLIDOWANE dla {celex} (najnowsza pierwsza; data w CELEX = stan na):")
    for i, c in enumerate(kons):
        print(f"  - {c}{'  ← AKTUALNA' if i == 0 else ''}")
    print("\nUWAGA: wersja skonsolidowana ma charakter dokumentacyjny — do urzędowego cytatu "
          "wskaż akt bazowy + zmiany.")
    print(f"Dalej: python3 {sys.argv[0]} tekst {kons[0]} --jezyk pol --fragment \"art. N\"")


def _pdf_url(celex, lang):
    """URL manifestacji PDF danej wersji językowej (negocjacja Accept: application/pdf nie działa w CELLAR)."""
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT ?man ?mtype WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  ?exp cdm:expression_belongs_to_work ?w .
  ?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> .
  ?man cdm:manifestation_manifests_expression ?exp .
  ?man cdm:manifestation_type ?mtype .
  FILTER(STRSTARTS(STR(?mtype), "pdf"))
}} LIMIT 5""", soft=True)
    pdfy = sorted(rows, key=lambda b: _v(b, "mtype"))  # pdfa1a/pdfa2a przed zwykłym pdf
    return (_v(pdfy[0], "man") + "/DOC_1") if pdfy else None


def cmd_tekst(a):
    celex = celex_norm(a.celex)
    lang = _lang(a.jezyk)
    lang3 = lang.lower()
    url = CELLAR + urllib.parse.quote(celex, safe="/")
    if a.pdf:
        strict = getattr(a, "strict", False)
        ostrz = _ostrzezenia_konsolidacja(celex, True) if strict else None
        try:
            pdf_url = _pdf_url(celex, lang)
        except VerificationUnknown as e:
            _nie_zweryfikowano(f"manifestacji PDF dla {celex} w języku {lang3}", e)
        if not pdf_url:
            sys.exit(f"Brak manifestacji PDF dla {celex} w języku {lang3} — spróbuj inny --jezyk.")
        data, _ = _http(pdf_url)
        with open(a.pdf, "wb") as f:
            f.write(data)
        print(f"Zapisano PDF ({len(data)} B): {a.pdf}\n(źródło: {pdf_url}, język {lang3})")
        for w in (ostrz if ostrz is not None else _ostrzezenia_konsolidacja(celex)):
            print(w)
        return
    try:
        raw, _ = _http(url, headers={"Accept": "application/xhtml+xml", "Accept-Language": lang3})
    except SystemExit as e:
        if "(404)" in str(e) and re.match(r"^0.*-\d{8}$", celex):
            _wyjasnij_404_konsolidacji(celex, lang3)
        raise
    txt = html_to_text(raw.decode("utf-8", "replace"))
    if not _bez_granic(txt).strip():
        sys.exit(f"Pusty tekst XHTML dla {celex} (język {lang3}) — spróbuj --pdf albo inny --jezyk.")
    ostrz = _ostrzezenia_konsolidacja(celex, getattr(a, "strict", False))
    print(f"# CELEX {celex} ({lang3}) — tekst z CELLAR (XHTML→tekst; do dosłownego cytatu zweryfikuj z PDF)\n")
    for w in ostrz:
        print(w)
    if ostrz:
        print()
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w tekście aktu ({len(txt)} znaków). "
                     "Spróbuj inną frazą, w innym języku, albo bez --fragment.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(_bez_granic(txt[s:e]).strip())
        print(f"\n(fragmenty: {len(spans)} — pominięto resztę aktu (w tym podpisy i przypisy końcowe); "
              "pełny tekst: bez --fragment)")
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
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?kier ?c2 ?inf ?eov WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  OPTIONAL {{ ?w cdm:resource_legal_date_end-of-validity ?eov }}
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
}} ORDER BY ?kier DESC(?c2) LIMIT 400""")
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    print(f"Odniesienia dla: CELEX {celex}\n")
    if not rows:
        print("Brak odnotowanych relacji w CELLAR (albo zły CELEX — sprawdź: meta).")
        return
    grupy = {}
    for b in rows:
        grupy.setdefault(_v(b, "kier"), []).append(_v(b, "c2"))
    zb = _zbierz(rows, ("inf", "eov"))
    uchylony = grupy.get(_KIERUNKI[0], [])
    eov = zb["eov"][0] if zb["eov"] and zb["eov"][0] != "9999-12-31" else ""
    if uchylony:
        print(f"AKT UCHYLONY przez {', '.join(uchylony)}"
              + (f" (koniec obowiązywania: {eov})" if eov else "")
              + f" — {_status(zb) if zb['inf'] else 'NIE OBOWIĄZUJE'}; aktualny stan prawny czytaj z aktu uchylającego.\n")
    elif _status(zb) == "NIE OBOWIĄZUJE":
        print("AKT NIE OBOWIĄZUJE" + (f" (koniec obowiązywania: {eov})" if eov else "")
              + " — w CELLAR brak relacji „uchylony przez\"; sprawdź: meta.\n")
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
