#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO repozytorium prawa UE: CELLAR/EUR-Lex Urzędu Publikacji UE.
SPARQL (wyszukiwanie, metadane, relacje) + REST (teksty aktów). Tylko biblioteka standardowa
Pythona — brak zależności pip. Operacje WYŁĄCZNIE read-only. Bez rejestracji i klucza API.

Komendy:
  szukaj "<fraza>" [--typ REG|DIR|DEC] [--rok R] [--jezyk pol] [--obowiazujace] [--limit N]
  meta <CELEX>                   metadane aktu (tytuł, typ, daty, czy obowiązuje, ELI)
  tekst <CELEX> [--jezyk pol] [--fragment "art. 6"] [--pdf ŚCIEŻKA]
                                 tekst aktu z CELLAR (XHTML → czysty tekst); --fragment wycina
                                 tylko jednostki z frazą; --pdf zapisuje urzędowy PDF
  skonsolidowany <CELEX>         wersje skonsolidowane aktu (odpowiednik tekstu jednolitego)
  odniesienia <CELEX>            nowelizacje, sprostowania, podstawa prawna
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności)

CELEX np.: 32016R0679 (RODO), 02016R0679-20160504 (wersja skonsolidowana), reg/2016/679 (ELI).
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "1.6.6"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
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


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"):
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
    # EUR-Lex używa twardych spacji (NBSP) — normalizuj, żeby frazy były wyszukiwalne
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# granice jednostek redakcyjnych w aktach UE (nagłówki na początku linii; PL/EN/DE)
_GRANICE = (r"(?m)^(Artykuł\s+\d|Article\s+\d|Artikel\s+\d|ROZDZIAŁ\s|CHAPTER\s|KAPITEL\s|"
            r"SEKCJA\s|Sekcja\s|SECTION\s|TYTUŁ\s|TITLE\s|ZAŁĄCZNIK|ANNEX|ANHANG|PREAMBUŁA)")


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
            rf"(?m)^(?:Artykuł|Article|Artikel)\s+{n}(?![0-9a-z])", txt)]
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


def _ostrzezenia_konsolidacja(celex, strict=False):
    """Ostrzeżenia o wersjach skonsolidowanych dla aktu/wersji (lista linii).

    To informacja POBOCZNA przy tekście/metadanych — awaria SPARQL nie może odebrać
    użytkownikowi treści głównej, więc UNKNOWN staje się tu głośnym ostrzeżeniem
    (pełną weryfikację wymusza komenda skonsolidowany, gdzie to treść główna)."""
    try:
        kons = _konsolidacje(celex)
    except VerificationUnknown as e:
        if strict:
            raise
        return [f"UWAGA: nie udało się zweryfikować, czy akt {celex} ma wersje skonsolidowane "
                f"({e}) — sprawdź komendą: skonsolidowany {celex}, zanim zacytujesz."]
    out = []
    if celex.startswith("0"):
        out.append("UWAGA: wersja skonsolidowana ma charakter DOKUMENTACYJNY (nie jest autentyczna) — "
                   "do urzędowego cytatu wskaż akt bazowy + zmiany.")
        if kons and kons[0] > celex:
            out.append(f"UWAGA: istnieje NOWSZA wersja skonsolidowana: {kons[0]} — używaj jej.")
    elif kons:
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
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    if not rows:
        print(f"Brak wyników dla {a.fraza!r} (język {lang}). Szukanie idzie po TYTULE i dopasowuje "
              "DOSŁOWNIE, a tytuły są odmienione ('ochrony danych osobowych', nie 'dane osobowe') — "
              "podaj RDZEŃ albo formę z tytułu ('osobow', 'danych osobowych'). Dalej nic? Spróbuj "
              "--jezyk eng albo bez --typ/--rok. To NIE dowód, że aktu nie ma."); return
    print(f"Wyniki (pokazuję {len(rows)}, najnowsze pierwsze):\n")
    for b in rows:
        inf = _v(b, "inf")
        status = "obowiązuje" if inf in ("1", "true") else ("nie obowiązuje" if inf in ("0", "false") else "—")
        print(f"  {_v(b, 'celex')}  ({_v(b, 'date')})  [{status}]")
        print(f"    {_v(b, 'title')[:160]}")
        print()


def cmd_meta(a):
    celex = celex_norm(a.celex)
    lang = _lang(a.jezyk)
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT ?type ?date ?inf ?eli ?eiv ?eov ?title WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  OPTIONAL {{ ?w cdm:work_has_resource-type ?type }}
  OPTIONAL {{ ?w cdm:work_date_document ?date }}
  OPTIONAL {{ ?w cdm:resource_legal_in-force ?inf }}
  OPTIONAL {{ ?w cdm:resource_legal_eli ?eli }}
  OPTIONAL {{ ?w cdm:resource_legal_date_entry-into-force ?eiv }}
  OPTIONAL {{ ?w cdm:resource_legal_date_end-of-validity ?eov }}
  OPTIONAL {{ ?exp cdm:expression_belongs_to_work ?w .
              ?exp cdm:expression_uses_language <{LANG_AUTH}{lang}> .
              ?exp cdm:expression_title ?title }}
}}""")
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    if not rows:
        sys.exit(f"Nie znaleziono aktu o CELEX {celex} w CELLAR.")
    zb = {k: sorted({_v(b, k) for b in rows if _v(b, k)}) for k in
          ("type", "date", "inf", "eli", "eiv", "eov", "title")}
    print(f"Akt: CELEX {celex}")
    if zb["title"]:
        print(f"  Tytuł:   {zb['title'][0][:300]}")
    if zb["type"]:
        print(f"  Typ:     {', '.join(t.rsplit('/', 1)[-1] for t in zb['type'])}")
    if zb["date"]:
        print(f"  Data aktu: {zb['date'][0]}")
    if zb["eiv"]:
        print(f"  WEJŚCIE W ŻYCIE / STOSOWANIE: {', '.join(zb['eiv'])}"
              + ("   (kilka dat — sprawdź przepisy końcowe aktu!)" if len(zb["eiv"]) > 1 else ""))
    if zb["eov"] and zb["eov"][0] != "9999-12-31":
        print(f"  Koniec obowiązywania: {zb['eov'][0]}")
    inf = zb["inf"][0] if zb["inf"] else ""
    print(f"  Status:  {'OBOWIĄZUJE' if inf in ('1', 'true') else ('NIE OBOWIĄZUJE' if inf in ('0', 'false') else '—')}")
    if zb["eli"]:
        print(f"  ELI:     {zb['eli'][0]}")
    print(f"  Tekst:   python3 {sys.argv[0]} tekst {celex} --jezyk pol --fragment \"art. N\"")
    for w in _ostrzezenia_konsolidacja(celex, getattr(a, "strict", False)):
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
        for w in _ostrzezenia_konsolidacja(celex, getattr(a, "strict", False)):
            print(w)
        return
    raw, _ = _http(url, headers={"Accept": "application/xhtml+xml", "Accept-Language": lang3})
    txt = html_to_text(raw.decode("utf-8", "replace"))
    if not txt:
        sys.exit(f"Pusty tekst XHTML dla {celex} (język {lang3}) — spróbuj --pdf albo inny --jezyk.")
    print(f"# CELEX {celex} ({lang3}) — tekst z CELLAR (XHTML→tekst; do dosłownego cytatu zweryfikuj z PDF)\n")
    ostrz = _ostrzezenia_konsolidacja(celex, getattr(a, "strict", False))
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
            print(txt[s:e].strip())
        print(f"\n(fragmenty: {len(spans)} — pominięto resztę aktu; pełny tekst: bez --fragment)")
        return
    if len(txt) > 60000:
        print(f"(UWAGA: pełny tekst ma {len(txt)} znaków — do pojedynczego przepisu użyj --fragment \"art. N\")\n")
    print(txt)


def cmd_odniesienia(a):
    celex = celex_norm(a.celex)
    rows = _sparql(f"""PREFIX cdm: <{CDM}>
SELECT DISTINCT ?kier ?c2 WHERE {{
  ?w cdm:resource_legal_id_celex "{celex}"^^<{XSD_STR}> .
  {{ ?x cdm:resource_legal_amends_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("Nowelizacje (akty zmieniające ten akt)" AS ?kier) }}
  UNION
  {{ ?x cdm:resource_legal_corrects_resource_legal ?w . ?x cdm:resource_legal_id_celex ?c2 .
     BIND("Sprostowania" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_based_on_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("Podstawa prawna (traktatowa)" AS ?kier) }}
  UNION
  {{ ?w cdm:resource_legal_amends_resource_legal ?o . ?o cdm:resource_legal_id_celex ?c2 .
     BIND("Zmienia (akty zmieniane przez ten akt)" AS ?kier) }}
}} ORDER BY ?kier DESC(?c2) LIMIT 300""")
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2)); return
    print(f"Odniesienia dla: CELEX {celex}\n")
    if not rows:
        print("Brak odnotowanych relacji w CELLAR (albo zły CELEX — sprawdź: meta).")
        return
    grupy = {}
    for b in rows:
        grupy.setdefault(_v(b, "kier"), []).append(_v(b, "c2"))
    for kier, lst in grupy.items():
        print(f"## {kier}  ({len(lst)})")
        for c in lst:
            print(f"  - {c}")
        print()
    if any(k.startswith("Nowelizacje") for k in grupy):
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
