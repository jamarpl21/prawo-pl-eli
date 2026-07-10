#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do PUBLICZNEGO API SAOS (System Analizy Orzeczeń Sądowych, https://www.saos.org.pl/api).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET).

SAOS to baza WTÓRNA orzecznictwa polskiego (agregat orzeczeń jawnych): Sąd Najwyższy (SN),
Trybunał Konstytucyjny (TK), sądy powszechne (SA/SO/SR) i Krajowa Izba Odwoławcza (KIO).
Sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — dla nich zob. CBOSA
(https://orzeczenia.nsa.gov.pl, brak API). Treść przepisów bierz z ELI (skill prawo-pl-eli),
nie z SAOS — tu szukasz, JAK sądy stosują przepisy.

Komendy:
  szukaj ["<fraza>"] [--sad SN|TK|powszechne|admin|KIO] [--sygnatura S] [--przepis P]
         [--sedzia N] [--haslo H] [--typ wyrok|postanowienie|uchwala|zarzadzenie|uzasadnienie]
         [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--limit N] [--strona N]
  orzeczenie <id> [--fragment "<fraza>"]   pełne orzeczenie: metadane, powołane przepisy/orzeczenia, treść
  sygnatura <sygnatura...>                  znajdź orzeczenie po numerze sprawy (caseNumber)
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "1.4.2"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://www.saos.org.pl/api"

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


def _get(path, params=None, soft=False):
    """GET z jednym ponowieniem na błąd przejściowy. soft=True: zamiast wyjścia zwraca None."""
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": f"saos-skill/{__version__}", "Accept": "application/json"})
    raw = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8", "replace")
            if "Przerwa techniczna" in raw:
                # SAOS bywa w oknie serwisowym — zwraca stronę HTML zamiast JSON (HTTP 200)
                if attempt == 1:
                    time.sleep(2); continue
                if soft:
                    return None
                sys.exit("BŁĄD: SAOS ma przerwę techniczną (serwis chwilowo niedostępny) — spróbuj ponownie później.")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if soft:
                return None
            if e.code == 404:
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:
            if attempt == 1:
                time.sleep(2); continue
            if soft:
                return None
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


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
    """HTML/snippet → czysty tekst. SAOS używa m.in. <em> w podświetleniach i twardych spacji (NBSP)."""
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
        start = max(0, p - okno // 3)
        end = min(len(txt), p + len(f) + okno)
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        p = low.find(f, p + len(f))
    return spans


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
    """Forma orzeczenia jako tekst. W /search to string ('wyrok SN'), w /judgments/{id} obiekt {'name': …}."""
    f = it.get("judgmentForm")
    if isinstance(f, dict):
        f = f.get("name")
    return f or it.get("judgmentType") or ""


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
    params = {
        "all": a.fraza,
        "caseNumber": a.sygnatura,
        "referencedRegulation": a.przepis,
        "judgeName": a.sedzia,
        "keywords": a.haslo,
        "courtType": ct,
        "judgmentTypes": _jtype(a.typ),
        "judgmentDateFrom": a.od,
        "judgmentDateTo": a.do,
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
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, dict):
        sys.exit("BŁĄD: API SAOS zwróciło nieoczekiwaną odpowiedź — spróbuj ponownie za chwilę.")
    items = d.get("items", [])
    total = (d.get("info") or {}).get("totalResults", "?")
    print(f"Znaleziono: {total}  (pokazuję {len(items)}, strona {params['pageNumber']}, po {params['pageSize']})\n")
    if ct == "ADMINISTRATIVE":
        print("UWAGA: sądy administracyjne (NSA/WSA) są w SAOS praktycznie nieobecne — orzecznictwo "
              "administracyjne sprawdź w CBOSA: https://orzeczenia.nsa.gov.pl (brak API, ręcznie).\n")
    for it in items:
        _wiersz(it)
    if items:
        print(f"Pełna treść: orzeczenie <id>  (np. orzeczenie {items[0].get('id')})")
        if isinstance(total, int) and total > len(items):
            print(f"Kolejna strona: --strona {params['pageNumber'] + 1}")


def cmd_orzeczenie(a):
    d = _get(f"/judgments/{a.id}")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    if not isinstance(d, dict):
        sys.exit("BŁĄD: API SAOS zwróciło nieoczekiwaną odpowiedź.")
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
    src = data.get("source") or {}
    if src.get("judgmentUrl"):
        print(f"  Źródło oryginalne: {src['judgmentUrl']}")
    print(f"  SAOS:   https://www.saos.org.pl/judgments/{data.get('id')}")

    reg = data.get("referencedRegulations") or []
    if reg:
        print(f"\n## Powołane przepisy ({len(reg)})")
        for r in reg[:40]:
            print(f"  - {r.get('text') or r.get('journalTitle', '')}")
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
    if not txt:
        print("(brak treści w API — otwórz źródło oryginalne wyżej)")
        return
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
        print(f"(UWAGA: długie uzasadnienie {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def cmd_sygnatura(a):
    sig = " ".join(a.sygnatura).strip()
    d = _get("/search/judgments", {"caseNumber": sig, "pageSize": 20, "pageNumber": 0,
                                   "sortingField": "JUDGMENT_DATE", "sortingDirection": "DESC"})
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    items = d.get("items", []) if isinstance(d, dict) else []
    total = (d.get("info") or {}).get("totalResults", 0) if isinstance(d, dict) else 0
    if not items:
        sys.exit(f"Nie znaleziono orzeczenia o sygnaturze {sig!r} w SAOS.\n"
                 "SAOS to baza wtórna (nie ma wszystkiego) — sprawdź też portal właściwego sądu "
                 "albo CBOSA dla sądów administracyjnych (https://orzeczenia.nsa.gov.pl).")
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

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
