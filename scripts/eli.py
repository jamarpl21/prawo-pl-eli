#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO API ELI Sejmu (https://api.sejm.gov.pl/eli).
Tylko biblioteka standardowa Pythona (urllib/json/re) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET). Źródło pierwotne prawa polskiego: Dziennik Ustaw (DU) i Monitor Polski (MP).

Komendy:
  szukaj "<fraza>" [--typ T] [--rok R] [--wyd DU|MP] [--obowiazujace] [--limit N]
  meta <sygnatura...>            np. meta DU 2024 18  |  meta "Dz.U. 2024 poz. 18"  |  meta WDU20240000018
  tekst <sygnatura...> [--pdf ŚCIEŻKA]   tekst aktu (z text.html → czysty tekst); --pdf zapisuje urzędowy PDF
  odniesienia <sygnatura...>     nowelizacje, tekst jednolity, podstawa prawna
  tj <sygnatura...>              znajduje TEKST JEDNOLITY dla aktu (z odniesień) i podaje jego sygnaturę
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
"""
import sys, json, re, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

BASE = "https://api.sejm.gov.pl/eli"


def _get(path, params=None):
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": "eli-skill/1.0", "Accept": "application/json, text/html"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        sys.exit(f"BŁĄD HTTP {e.code}: {url}")
    except Exception as e:
        sys.exit(f"BŁĄD sieci: {url} ({e})")
    if "json" in ctype:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
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
    p = _Stripper()
    p.feed(html)
    t = "".join(p.out)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


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
    params = {"title": a.fraza, "limit": a.limit, "type": a.typ, "year": a.rok, "publisher": a.wyd}
    if a.obowiazujace:
        params["inForce"] = 1
    d = _get("/acts/search", params)
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    items = d.get("items", []) if isinstance(d, dict) else []
    print(f"Znaleziono: {d.get('count', '?')} (pokazuję {len(items)})\n")
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
    print(f"Akt: {label}")
    print(f"  Tytuł:   {d.get('title','').strip()}")
    print(f"  Typ:     {d.get('type','')}")
    print(f"  Status:  {d.get('status','')}  (inForce={d.get('inForce','')})")
    print(f"  Ogłoszono: {d.get('announcementDate','')}   Stan prawny: {d.get('legalStatusDate','')}")
    if d.get("keywordsNames"):
        print(f"  Hasła:   {', '.join(d['keywordsNames'])}")
    print(f"  ELI:     {d.get('ELI','')}")
    texts = d.get("texts", [])
    if texts:
        print("  Dostępne teksty (type/fileName):")
        for t in texts:
            print(f"    - {t.get('type','?')}: {t.get('fileName','')}")
    print(f"  Tekst HTML: {BASE}{path}/text.html")


def cmd_tekst(a):
    path, label = act_path(a.sygnatura)
    if a.pdf:
        # pobierz urzędowy PDF (preferuj tekst jednolity, typ 'U'/'T', inaczej oryginał 'O')
        meta = _get(path)
        texts = meta.get("texts", []) if isinstance(meta, dict) else []
        pick = None
        for code in ("U", "T", "O", "H"):
            pick = next((t for t in texts if t.get("type") == code and t.get("fileName", "").lower().endswith(".pdf")), None)
            if pick:
                break
        if not pick:
            sys.exit("Brak PDF w metadanych aktu.")
        url = f"{BASE}{path}/text/{pick['type']}/{pick['fileName']}"
        req = urllib.request.Request(url, headers={"User-Agent": "eli-skill/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with open(a.pdf, "wb") as f:
            f.write(data)
        print(f"Zapisano PDF ({len(data)} B): {a.pdf}\n(źródło: {url})")
        return
    html = _get(path + "/text.html")
    if isinstance(html, (dict, list)):
        html = json.dumps(html, ensure_ascii=False)
    print(f"# {label} — tekst (z text.html; ASR/HTML→tekst, do cytatu zweryfikuj z PDF urzędowym)\n")
    print(html_to_text(html))


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
    if not isinstance(d, dict):
        sys.exit("Brak odniesień.")
    key = next((k for k in d if "jednolit" in k.lower()), None)
    if not key:
        print(f"Dla {label} brak wskazanego tekstu jednolitego w odniesieniach — sam akt może już nim być.")
        return
    items = d[key] if isinstance(d[key], list) else [d[key]]
    print(f"TEKST JEDNOLITY dla {label}:")
    for ref in items:
        act = ref.get("act") if isinstance(ref, dict) else None
        if isinstance(act, dict):
            print(f"  - {act.get('displayAddress') or act.get('ELI','')}  {act.get('title','')}".rstrip())
            if act.get("ELI"):
                print(f"    tekst: python3 eli.py tekst {act['ELI'].replace('/', ' ')}")
        else:
            print(_fmt_ref(ref))


def main():
    ap = argparse.ArgumentParser(description="API ELI Sejmu (read-only). Źródło pierwotne prawa polskiego.")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("szukaj"); s.add_argument("fraza"); s.add_argument("--typ"); s.add_argument("--rok")
    s.add_argument("--wyd", default=None, choices=[None, "DU", "MP"]); s.add_argument("--obowiazujace", action="store_true")
    s.add_argument("--limit", type=int, default=10); s.set_defaults(func=cmd_szukaj)

    for name, fn in (("meta", cmd_meta), ("odniesienia", cmd_odniesienia), ("tj", cmd_tj)):
        p = sub.add_parser(name); p.add_argument("sygnatura", nargs="+"); p.set_defaults(func=fn)

    t = sub.add_parser("tekst"); t.add_argument("sygnatura", nargs="+"); t.add_argument("--pdf"); t.set_defaults(func=cmd_tekst)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
