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
                                 tekst aktu (text.html → czysty tekst); --fragment wycina tylko jednostki
                                 z frazą (np. jeden artykuł); --pdf zapisuje urzędowy PDF
  struktura <sygnatura...> [--filtr F] [--poziom N]   spis jednostek redakcyjnych aktu (z /struct)
  odniesienia <sygnatura...>     nowelizacje, tekst jednolity, podstawa prawna
  tj <sygnatura...>              znajduje AKTUALNY TEKST JEDNOLITY dla aktu i podaje jego sygnaturę
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "1.6.4"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://api.sejm.gov.pl/eli"


def _get(path, params=None, soft=False):
    """GET z jednym ponowieniem na błąd przejściowy. soft=True: zamiast wyjścia zwraca None."""
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": f"eli-skill/{__version__}", "Accept": "application/json, text/html"})
    raw, ctype = None, ""
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                ctype = r.headers.get("Content-Type", "")
                raw = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if soft:
                return None
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 1:
                time.sleep(2); continue
            if soft:
                return None
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    if raw is not None and "Request Rejected" in raw and "rejected" in raw.lower():
        # zapora (WAF) api.sejm.gov.pl potrafi odrzucać wybrane URL-e (m.in. /text.html/{tree})
        if soft:
            return None
        sys.exit(f"BŁĄD: zapora api.sejm.gov.pl odrzuciła żądanie (Request Rejected): {url}\n"
                 "Spróbuj ponownie; do pojedynczego artykułu użyj: tekst <syg> --fragment \"art. N\".")
    if "json" in ctype:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": f"eli-skill/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:
        sys.exit(f"BŁĄD pobierania: {url} ({e})")


def _expect_dict(d, what):
    if not isinstance(d, dict):
        sys.exit(f"BŁĄD: API zwróciło nieoczekiwaną odpowiedź ({what}) — spróbuj ponownie za chwilę.")
    return d


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.out.append("\n")
        # Indeks górny artykułu (np. "Art. 21<sup>1</sup>.") rozdzielamy spacją, żeby
        # "Art. 21 1." było odróżnialne od "Art. 211." — inaczej art. 21¹ i art. 211
        # sklejają się do tego samego napisu i _fragmenty zwraca oba (znany bug).
        if tag == "sup":
            self.out.append(" ")
        # Treść przypisu (dymek przy odsyłaczu) API wstawia INLINE, w środku przepisu:
        # „Art. 66c 6)Dodany przez art. 3…" albo „…wyrażą na to zgodę. 20)Zdanie trzecie dodane…".
        # Bez oznaczenia nie da się odróżnić normy od komentarza redakcyjnego, więc przypis
        # wychodzi do własnej linii z etykietą. Etykieta (a nie samo „\n") jest konieczna:
        # 7 przypisów w k.p.c. zaczyna się od „Art. 598…"/„Tytuł działu…" i na początku linii
        # udawałoby nagłówek jednostki (_GRANICE), tnąc fragment w losowym miejscu.
        if tag == "span" and "tooltip-text" in dict(attrs).get("class", ""):
            self.out.append("\n[przypis] ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _Stripper()
    p.feed(html)
    # API ELI używa twardych spacji (NBSP), np. "Art.\xa0299." — normalizuj, żeby frazy były wyszukiwalne
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


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


def _ostrzezenia(refs):
    """Ostrzeżenia o aktualności (lista linii) na podstawie odniesień aktu."""
    out = []
    tj = _tj_acts(refs)
    if tj:
        a = tj[0]
        out.append(f"UWAGA: ten akt ma TEKST JEDNOLITY — cytuj z najnowszego: "
                   f"{a.get('displayAddress') or a.get('ELI', '')} (ELI {a.get('ELI', '')}).")
    key = next((k for k in refs if k.lower().startswith("nowelizacje po tekście jednolit")), None)
    if key:
        items = refs[key] if isinstance(refs[key], list) else [refs[key]]
        out.append(f"UWAGA: po tym tekście jednolitym odnotowano zmiany ({len(items)}) — sprawdź ich wejście w życie:")
        out += [_fmt_ref(r) for r in items]
    return out


def _tj_z_tekstem(path, refs):
    """Najnowszy tekst jednolity z NIEPUSTYM text.html, pomijając akt bieżący.

    API potrafi zwrócić 200 i 0 bajtów dla text.html świeżego tekstu jednolitego (HTML pojawia
    się z opóźnieniem względem PDF) — wtedy czytamy poprzedni t.j. zamiast zostawiać użytkownika
    bez oficjalnego źródła. Zwraca (akt, tekst) albo None.
    """
    biezacy_eli = path[len("/acts/"):]
    tj = _tj_acts(refs)
    if not tj:
        # akt sam jest t.j. — pełną listę tekstów jednolitych mają odniesienia aktu BAZOWEGO
        base_key = next((k for k in refs if k.lower().startswith("tekst jednolity dla aktu")), None)
        items = (refs[base_key] if isinstance(refs[base_key], list) else [refs[base_key]]) if base_key else []
        base = next((r.get("act") for r in items if isinstance(r, dict) and isinstance(r.get("act"), dict)), None)
        if base and base.get("ELI"):
            base_refs = _get(f"/acts/{base['ELI']}/references", soft=True)
            tj = _tj_acts(base_refs) if isinstance(base_refs, dict) else []
    for act in tj:
        eli_id = act.get("ELI")
        if not eli_id or eli_id == biezacy_eli:
            continue
        html = _get(f"/acts/{eli_id}/text.html", soft=True)
        txt = html_to_text(html) if isinstance(html, str) else ""
        if txt:
            return act, txt
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
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    d = _expect_dict(d, "wyniki wyszukiwania")
    items = d.get("items", [])
    print(f"Znaleziono: {d.get('count', '?')} (pokazuję {len(items)}, offset {a.offset})\n")
    for it in items:
        print(f"  {it.get('address','')}  [{it.get('status','')}]")
        print(f"    {it.get('title','').strip()[:160]}")
        if it.get("ELI"):
            print(f"    ELI: {it['ELI']}")
        print()


def cmd_meta(a):
    path, label = act_path(a.sygnatura)
    d = _get(path)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    d = _expect_dict(d, "metadane aktu")
    print(f"Akt: {label}")
    print(f"  Tytuł:   {d.get('title','').strip()}")
    print(f"  Adres:   {d.get('displayAddress','')}")
    print(f"  Typ:     {d.get('type','')}")
    print(f"  Status:  {d.get('status','')}  (inForce={d.get('inForce','')})")
    print(f"  Ogłoszono: {d.get('announcementDate','')}   WEJŚCIE W ŻYCIE: {d.get('entryIntoForce') or '—'}"
          f"   Stan prawny na: {d.get('legalStatusDate','')}")
    if d.get("validFrom") and d.get("validFrom") != d.get("entryIntoForce"):
        print(f"  Obowiązuje od: {d['validFrom']}")
    if d.get("keywordsNames"):
        print(f"  Hasła:   {', '.join(d['keywordsNames'])}")
    print(f"  ELI:     {d.get('ELI','')}")
    texts = d.get("texts", [])
    if texts:
        print("  Dostępne teksty (type/fileName):")
        for t in texts:
            print(f"    - {t.get('type','?')}: {t.get('fileName','')}")
    print(f"  Tekst HTML: {BASE}{path}/text.html")
    if "tekst jednolity" in (d.get("status") or "").lower():
        print(f"  → Akt ma tekst jednolity — ustal aktualny: python3 {sys.argv[0]} tj {label}")


def cmd_tekst(a):
    path, label = act_path(a.sygnatura)
    refs = _get(path + "/references", soft=True)
    ostrz = _ostrzezenia(refs) if isinstance(refs, dict) else []
    if a.pdf:
        # pobierz urzędowy PDF (preferuj tekst jednolity, typ 'U'/'T', inaczej oryginał 'O')
        meta = _expect_dict(_get(path), "metadane aktu")
        texts = meta.get("texts", [])
        pick = None
        for code in ("U", "T", "O", "H"):
            pick = next((t for t in texts if t.get("type") == code and t.get("fileName", "").lower().endswith(".pdf")), None)
            if pick:
                break
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
    if not txt:
        fb = _tj_z_tekstem(path, refs if isinstance(refs, dict) else {})
        if not fb:
            sys.exit(f"BŁĄD: text.html dla {label} jest PUSTE w API (HTML bywa dodawany z opóźnieniem) "
                     f"i nie znalazłem innego tekstu jednolitego z tekstem. "
                     f"Pobierz urzędowy PDF: tekst {label} --pdf plik.pdf")
        act, txt = fb
        addr = act.get("displayAddress") or act.get("ELI", "")
        sig = (act.get("ELI") or "").replace("/", " ")
        ostrz = [f"UWAGA: text.html dla {label} jest PUSTE w API — poniżej tekst z innego t.j.: {addr}.",
                 f"Nałóż zmiany pomiędzy nimi: odniesienia {sig} (sekcja „Nowelizacje po tekście jednolitym\");"
                 f" do dosłownego cytatu: tekst {label} --pdf plik.pdf"] + ostrz
        label = f"{label} (tekst z: {addr})"
    print(f"# {label} — tekst (z text.html; HTML→tekst, do dosłownego cytatu zweryfikuj z PDF urzędowym)\n")
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


def cmd_struktura(a):
    path, label = act_path(a.sygnatura)
    d = _get(path + "/struct")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    nodes = d if isinstance(d, list) else [d] if isinstance(d, dict) else None
    if not nodes:
        sys.exit("Brak struktury dla tego aktu (API udostępnia /struct głównie dla tekstów jednolitych i starszych aktów).")
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


def _fmt_ref(ref):
    if not isinstance(ref, dict):
        return f"  - {ref}"
    act = ref.get("act") if isinstance(ref.get("act"), dict) else None
    if act:
        head = act.get("displayAddress") or act.get("ELI", "") or ""
        line = f"  - {head}  {act.get('title','')}".rstrip()
    else:
        line = f"  - {ref.get('displayAddress') or ref.get('ELI','') or ref}"
    extra = []
    if ref.get("date"):
        extra.append(f"data {ref['date']}")
    if ref.get("art"):
        extra.append(f"art. {ref['art']}")
    if extra:
        line += "  (" + ", ".join(extra) + ")"
    return line


def cmd_odniesienia(a):
    path, label = act_path(a.sygnatura)
    d = _get(path + "/references")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Odniesienia dla: {label}\n")
    if not isinstance(d, dict):
        print(d); return
    for kind, lst in d.items():
        items = lst if isinstance(lst, list) else [lst]
        print(f"## {kind}  ({len(items)})")
        for ref in items:
            print(_fmt_ref(ref))
        print()


def cmd_tj(a):
    path, label = act_path(a.sygnatura)
    d = _get(path + "/references")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    d = _expect_dict(d, "odniesienia aktu")
    # akt bazowy: kategoria 'Inf. o tekście jednolitym' listuje obwieszczenia z t.j.
    tj = _tj_acts(d)
    if tj:
        print(f"TEKSTY JEDNOLITE dla {label} (najnowszy pierwszy):")
        for i, act in enumerate(tj):
            marker = "  ← AKTUALNY" if i == 0 else ""
            print(f"  - {act.get('displayAddress') or act.get('ELI','')}  [{act.get('status','')}]{marker}")
        eli = tj[0].get("ELI", "")
        if eli:
            sig = eli.replace("/", " ")
            print(f"\nDalej: python3 {sys.argv[0]} tekst {sig} --fragment \"art. N\"")
            print(f"oraz:  python3 {sys.argv[0]} odniesienia {sig}   (sekcja „Nowelizacje po tekście jednolitym\")")
        return
    # akt sam jest tekstem jednolitym: kategoria 'Tekst jednolity dla aktu' wskazuje akt BAZOWY
    base_key = next((k for k in d if k.lower().startswith("tekst jednolity dla aktu")), None)
    if base_key:
        items = d[base_key] if isinstance(d[base_key], list) else [d[base_key]]
        base = next((r.get("act") for r in items if isinstance(r, dict) and isinstance(r.get("act"), dict)), None)
        print(f"{label} SAM JEST tekstem jednolitym" + (f" (dla: {base.get('displayAddress','')} — {base.get('title','')[:90]})" if base else "") + ".")
        for w in _ostrzezenia(d):
            print(w)
        # sprawdź na akcie bazowym, czy nie ma już NOWSZEGO tekstu jednolitego
        m = re.match(r"^/acts/(DU|MP)/(\d+)/(\d+)$", path)
        if base and base.get("ELI") and m:
            base_refs = _get(f"/acts/{base['ELI']}/references", soft=True)
            newer = _tj_acts(base_refs) if isinstance(base_refs, dict) else []
            if newer and _eli_rok_poz(newer[0]) > (int(m.group(2)), int(m.group(3))):
                print(f"UWAGA: istnieje NOWSZY tekst jednolity: {newer[0].get('displayAddress') or newer[0].get('ELI','')} — cytuj z niego.")
        return
    print(f"Dla {label} brak tekstu jednolitego w odniesieniach — akt może nie mieć t.j. (cytuj z aktu, "
          f"ale sprawdź „Akty zmieniające\" w: odniesienia {label}).")


def main():
    ap = argparse.ArgumentParser(description="API ELI Sejmu (read-only). Źródło pierwotne prawa polskiego.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
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

    # --json działa też PO komendzie (modele piszą flagi właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
