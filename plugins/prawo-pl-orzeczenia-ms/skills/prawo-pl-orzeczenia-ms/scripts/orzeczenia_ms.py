#!/usr/bin/env python3
"""Publiczny Portal Orzeczeń MS: read-only HTML/RSS, wyłącznie stdlib."""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

__version__ = "2.1.0"
SKILL_VERSION = __version__
BASE = "https://orzeczenia.ms.gov.pl"
USER_AGENT = "curl/8.7.1"  # sprawdzone 2026-09-20; inne UA uruchamiają F5/TSPD
NOTE = "Portal publikuje wybór orzeczeń; brak wyniku nie dowodzi braku wyroku."
MONTHS = "stycznia lutego marca kwietnia maja czerwca lipca sierpnia września października listopada grudnia".split()


class Unknown(RuntimeError):
    """Nie można wiarygodnie odczytać odpowiedzi dostawcy (UNKNOWN)."""


class Empty(RuntimeError):
    """Rozpoznana, poprawna odpowiedź bez wyników."""


def safe_url(url):
    url = urllib.parse.urljoin(BASE + "/", url)
    p = urllib.parse.urlsplit(url)
    if p.hostname != "orzeczenia.ms.gov.pl" or p.username or p.password or p.port not in (None, 80, 443):
        raise Unknown("Link prowadzi poza centralny portal MS.")
    if p.scheme not in ("http", "https"):
        raise Unknown("Nieobsługiwany protokół linku.")
    return urllib.parse.urlunsplit(("https", "orzeczenia.ms.gov.pl", p.path, p.query, ""))


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


class Client:
    def __init__(self):
        self.opener = urllib.request.build_opener(Redirect())
        self.last = 0

    def get(self, url, pdf=False):
        url = safe_url(url)
        time.sleep(max(0, .6 - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with self.opener.open(req, timeout=40) as response:
                data = response.read(20_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise Unknown(f"Błąd dostępu do portalu: {e}") from e
        if len(data) > 20_000_000:
            raise Unknown("Odpowiedź przekracza limit 20 MB.")
        if pdf:
            if not data.startswith(b"%PDF-"):
                raise Unknown("Eksport nie zwrócił PDF (możliwa blokada F5/TSPD).")
            return data
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise Unknown("Odpowiedź nie jest tekstem UTF-8.") from e
        if re.search(r"/TSPD/|<title>\s*Połączenie odrzucone|<title>\s*Request Rejected", text, re.I):
            raise Unknown("Portal odrzucił dostęp (F5/TSPD). Ponów później lub otwórz portal w przeglądarce.")
        return text


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def all(self, tag=None, cls=None, ident=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get("class", "").split()) and (ident is None or child.attrs.get("id") == ident):
                    yield child
                yield from child.all(tag, cls, ident)

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        text = "".join(c.text() if isinstance(c, Node) else c for c in self.children)
        if self.tag == "sup":
            text = text.translate(str.maketrans("0123456789+-=()", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾"))
        if self.tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "li", "dt", "dd", "tr", "blockquote", "br"}:
            return "\n" + text + "\n"
        if self.tag in {"td", "th"}:
            return text + "\t"
        return text

    def plain(self):
        return "\n".join(re.sub(r"[\t \xa0]+", " ", line).strip() for line in self.text().splitlines() if line.strip())


class DOM(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, text):
        self.stack[-1].children.append(text)


def page(text):
    root = DOM(text).root
    titles = list(root.all("title"))
    if not titles or "Portal Orzeczeń Sądów Powszechnych" not in titles[0].plain():
        raise Unknown("Nierozpoznana strona portalu; nie jest to potwierdzone zero wyników.")
    return root


def one(root, **kwargs):
    nodes = list(root.all(**kwargs))
    if len(nodes) != 1:
        raise Unknown(f"Zmieniona lub niekompletna struktura strony: {kwargs}.")
    return nodes[0]


def slot(value):
    if value is None or value == "":
        return "$N"
    out = []
    for c in str(value):
        if c.isascii() and (c.isalnum() or c in "-_."):
            out.append(c)
        else:
            raw = c.encode("utf-16-be")
            out.extend("$" + raw[i:i+2].hex() for i in range(0, len(raw), 2))
    return "".join(out)


def date_arg(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("Data musi mieć format RRRR-MM-DD.")
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e


def polish_date(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dt.date.fromisoformat(value).isoformat()
    parts = value.split()
    try:
        day, month, year = parts
        return dt.date(int(year), MONTHS.index(month) + 1, int(day)).isoformat()
    except (ValueError, TypeError) as e:
        raise Unknown(f"Nierozpoznana data z portalu: {value!r}.") from e


def doc_id(value):
    if value.startswith(("https://", "http://")):
        try:
            p = urllib.parse.urlsplit(safe_url(value))
        except Unknown as e:
            raise ValueError(str(e)) from e
        if not re.match(r"^/(details|content|regulations|similardocs)/", p.path):
            raise ValueError("Oczekiwano linku do orzeczenia.")
        value = p.path.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[0-9]{15}_[A-Za-z0-9_-]+", value):
        raise ValueError("Niepoprawny identyfikator orzeczenia (weź ID z wyszukiwarki lub RSS).")
    return value


def doc_url(kind, ident):
    return f"{BASE}/{kind}/$N/{doc_id(ident)}"


def finality(node):
    # Portal renderuje komunikat także jako tło CSS, a nie tekst HTML:
    # custom.css: .single_result.invalid -> img/nieprawomocny.png
    # Napis na obrazie potwierdzono 2026-09-20: „ORZECZENIE NIEPRAWOMOCNE”.
    if any("invalid" in n.attrs.get("class", "").split() for n in node.all(cls="single_result")):
        return False
    notices = node.plain() + " " + " ".join(n.attrs.get("title", "") for n in node.all())
    if re.search(r"orzeczenie\s+nieprawomocne", notices, re.I):
        return False
    if re.search(r"orzeczenie\s+prawomocne", notices, re.I):
        return True
    return None


def search_url(args):
    values = [None] * 15
    for index, key in [(0, "fraza"), (1, "sygnatura"), (2, "sad"), (3, "wydzial"),
                       (4, "apelacja"), (5, "okreg"), (7, "od"), (8, "do"),
                       (9, "sedzia"), (10, "funkcja"), (11, "haslo"),
                       (12, "przepis"), (14, "istotnosc")]:
        values[index] = getattr(args, key, None)
    if getattr(args, "tezowane", False):
        values[13] = "*"
    if args.od and args.do and args.od > args.do:
        raise ValueError("Data od nie może być późniejsza niż data do.")
    return BASE + "/search/advanced/" + "/".join([slot(v) for v in values] + [args.sort, args.kierunek, str(args.strona)])


def parse_search(text, url, number=1):
    root = page(text)
    alerts = list(root.all(cls="alert"))
    if any("Nie znaleziono żadnego wyniku pasującego do zapytania." in " ".join(n.plain().split()) for n in alerts):
        if list(root.all(cls="big_number")) or list(root.all(cls="single_result")):
            raise Unknown("Sprzeczna odpowiedź wyszukiwarki.")
        raise Empty(f"Portal nie znalazł wyników na stronie {number}. {NOTE}")
    count = one(root, cls="big_number").plain()
    if not re.fullmatch(r"[\d\s]+", count):
        raise Unknown("Brak rozpoznanej liczby trafień.")
    total = int(re.sub(r"\s", "", count))
    section = one(root, ident="results")
    items = []
    for result in section.all(cls="single_result"):
        heading = one(result, tag="h4")
        link = one(heading, tag="a")
        ident = doc_id(link.attrs.get("href", "").rsplit("/", 1)[-1])
        title = one(result, cls="title")
        paragraphs = [n.plain() for n in title.all("p")]
        date = re.search(r"Data orzeczenia:\s*(\d{4}-\d{2}-\d{2})", title.plain())
        publication = re.search(r"Data publikacji:\s*(\d{4}-\d{2}-\d{2})", title.plain())
        if len(paragraphs) < 2 or not date or not link.plain():
            raise Unknown("Niekompletny rekord wyszukiwania.")
        excerpts = list(result.all("blockquote"))
        items.append(dict(id=ident, sygnatura=link.plain(), sad=paragraphs[1], typ=paragraphs[0],
                          data_orzeczenia=polish_date(date[1]), data_publikacji=polish_date(publication[1]) if publication else None,
                          url=doc_url("details", ident), fragment=excerpts[0].plain() if excerpts else ""))
    expected = max(0, min(10, total - (number - 1) * 10))
    if len(items) != expected:
        raise Unknown(f"Niekompletna lista: oczekiwano {expected}, odczytano {len(items)}.")
    if not items:
        raise Empty(f"Brak wyników na stronie {number} (łącznie {total}). {NOTE}")
    return dict(zrodlo=url, liczba_wynikow=total, strona=number, wyniki=items, uwagi=[NOTE])


def parse_meta(text, ident):
    root = page(text)
    container = one(root, ident="content")
    if not any(n.attrs.get("href", "").endswith("/" + ident) for n in container.all("a")):
        raise Unknown("Metryka dotyczy innego orzeczenia.")
    dl = one(container, tag="dl")
    fields = {}
    label = None
    for n in dl.children:
        if not isinstance(n, Node):
            continue
        if n.tag == "dt":
            label = n.plain().rstrip(":")
        elif n.tag == "dd" and label:
            fields[label] = (fields[label] + "; " if label in fields else "") + n.plain()
    if any(not fields.get(k) for k in ("Sygnatura", "Sąd", "Data orzeczenia")):
        raise Unknown("Niekompletna metryka orzeczenia.")
    # Wyklucz treść cytowanych wyroków; status czytamy wyłącznie na stronie metryki.
    status = finality(container)
    return dict(id=ident, sygnatura=fields["Sygnatura"], sad=fields["Sąd"],
                data_orzeczenia=polish_date(fields["Data orzeczenia"]),
                data_publikacji=polish_date(fields["Data publikacji"]) if fields.get("Data publikacji") else None,
                prawomocne=status, metryka=fields, url=doc_url("details", ident),
                podobne_url=doc_url("similardocs", ident),
                uwagi=[NOTE] + (["Portal oznacza orzeczenie jako nieprawomocne."] if status is False else
                               ["Portal nie potwierdza prawomocności tego orzeczenia."] if status is None else []))


def parse_content(text, ident):
    root = page(text)
    container = one(root, ident="content")
    content = one(container, cls="single_result").plain()
    if len(content) < 80:
        raise Unknown("Brak pełnej treści orzeczenia.")
    links = [safe_url(n.attrs["href"]) for n in container.all("a") if n.attrs.get("href", "").startswith("/content.pdffile/")]
    # Wiążemy stronę treści z żądanym ID zamiast ufać statusowi HTTP 200.
    if not any(n.attrs.get("href", "").endswith("/" + ident) for n in container.all("a")):
        raise Unknown("Strona treści dotyczy innego orzeczenia.")
    return dict(tresc=content, url_tresci=doc_url("content", ident), pdf_url=links[0] if links else None)


def parse_regulations(text):
    root = page(text)
    regs = one(root, ident="regulations")
    return [dict(tytul=n.plain(), linki=[a.attrs.get("href") for a in n.all("a")]) for n in regs.all("li")]


def parse_rss(text, url, limit):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise Unknown("Nieobsługiwana deklaracja w XML kanału RSS.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise Unknown("Odpowiedź nie jest poprawnym RSS.") from e
    channel = root.find("channel")
    if root.tag != "rss" or channel is None or not channel.findtext("title"):
        raise Unknown("Nierozpoznany kanał RSS.")
    items = []
    for entry in channel.findall("item"):
        title, link = entry.findtext("title"), entry.findtext("link")
        if not title or not link or not entry.findtext("pubDate"):
            raise Unknown("Niekompletny wpis RSS.")
        link = safe_url(link)
        ident = doc_id(link)
        items.append(dict(id=ident, tytul=title, url=doc_url("details", ident), data_publikacji=entry.findtext("pubDate")))
    if not items:
        raise Empty(f"Kanał RSS nie zawiera pozycji. {NOTE}")
    return dict(zrodlo=url, wyniki=items[:limit], liczba_w_kanale=len(items),
                uwagi=[NOTE, "RSS jest ograniczonym oknem publikacji, nie pełnym archiwum. Data RSS nie jest datą wyroku."])


def positive(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("Wartość musi być dodatnia.")
    return n


def code_arg(length):
    def check(value):
        if not re.fullmatch(r"\d{" + str(length) + r"}", value):
            raise argparse.ArgumentTypeError(f"Kod portalu musi mieć {length} cyfr (nie nazwę).")
        return value
    return check


def parser():
    flags = argparse.ArgumentParser(add_help=False)
    flags.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    flags.add_argument("--strict", action="store_true", default=argparse.SUPPRESS)
    p = argparse.ArgumentParser(description=__doc__, parents=[flags])
    p.add_argument("--version", action="version", version=__version__)
    subs = p.add_subparsers(dest="command", required=True)
    for command in ("szukaj", "sygnatura"):
        sub = subs.add_parser(command, parents=[flags])
        if command == "szukaj":
            sub.add_argument("fraza", nargs="?", default="")
            sub.add_argument("--sygnatura")
        else:
            sub.add_argument("sygnatura", nargs="+")
        sub.add_argument("--od", type=date_arg)
        sub.add_argument("--do", type=date_arg)
        sub.add_argument("--sedzia")
        sub.add_argument("--funkcja", choices=["CHAIRMAN", "RAPPORTEUR", "REASONS_AUTHOR", "JUDGE"])
        sub.add_argument("--sad", type=code_arg(8), help="Kod sądu z portalu, np. 15050500")
        sub.add_argument("--wydzial", type=code_arg(2), help="Kod typu wydziału, np. 03 = Cywilny")
        sub.add_argument("--apelacja", type=code_arg(4), help="Kod apelacji, np. 1505")
        sub.add_argument("--okreg", type=code_arg(6), help="Kod okręgu, np. 150505")
        sub.add_argument("--haslo", help="Dokładne hasło tematyczne z portalu")
        sub.add_argument("--przepis", help="Pole Podstawa prawna portalu; nie weryfikuje brzmienia przepisu")
        sub.add_argument("--tezowane", action="store_true")
        sub.add_argument("--istotnosc", type=int, choices=range(0, 6))
        sub.add_argument("--strona", type=positive, default=1)
        sub.add_argument("--sort", choices=["score", "data", "datapublikacji", "istotnosc"], default="score")
        sub.add_argument("--kierunek", choices=["ascending", "descending"], default="descending")
    for command in ("metryka", "orzeczenie", "przepisy", "pdf"):
        sub = subs.add_parser(command, parents=[flags])
        sub.add_argument("id", type=doc_id)
        if command == "pdf":
            sub.add_argument("--plik", type=Path, required=True)
    sub = subs.add_parser("rss", parents=[flags])
    sub.add_argument("--sad", help="Kod sądu z /rss/courts, np. 15050000; domyślnie kanał ogólny")
    sub.add_argument("--limit", type=positive, default=10)
    return p


def run(args, client):
    if args.command in ("szukaj", "sygnatura"):
        if args.command == "sygnatura":
            args.sygnatura = " ".join(args.sygnatura)
        url = search_url(args)
        return parse_search(client.get(url), url, args.strona)
    if args.command == "rss":
        channel = args.sad or "Orzecznictwo sądów powszechnych"
        if args.sad and not re.fullmatch(r"\d{8}", args.sad):
            raise ValueError("Kod kanału sądu musi mieć 8 cyfr; lista: " + BASE + "/rss/courts")
        url = BASE + "/rsscontent/" + slot(channel)
        return parse_rss(client.get(url), url, args.limit)
    ident = args.id
    result = parse_meta(client.get(doc_url("details", ident)), ident)
    if getattr(args, "strict", False) and result["prawomocne"] is not True:
        raise Unknown("--strict: portal nie potwierdza prawomocności (albo oznacza orzeczenie jako nieprawomocne).")
    if args.command == "przepisy":
        result["powolane_przepisy"] = parse_regulations(client.get(doc_url("regulations", ident)))
        result["uwagi"].append("Lista powołanych przepisów portalu może być niepełna; brzmienie sprawdź w ELI.")
    if args.command in ("orzeczenie", "pdf"):
        result.update(parse_content(client.get(doc_url("content", ident)), ident))
    if args.command == "pdf":
        if not result["pdf_url"]:
            raise Unknown("Portal nie udostępnił linku do PDF.")
        data = client.get(result["pdf_url"], pdf=True)
        # Nie nadpisuj istniejącego pliku ani nie zapisuj HTML wyzwania jako PDF.
        with args.plik.open("xb") as f:
            f.write(data)
        result.pop("tresc")
        result["plik"] = str(args.plik.resolve())
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = run(args, Client())
        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            for warning in result.get("uwagi", []):
                print("UWAGA: " + warning, file=sys.stderr)
            print(json.dumps({k: v for k, v in result.items() if k not in ("uwagi", "tresc")}, ensure_ascii=False, indent=2))
            if "tresc" in result:
                print("\n" + result["tresc"])
        return 0
    except Empty as e:
        print("BRAK WYNIKÓW: " + str(e), file=sys.stderr)
        return 1
    except (Unknown, ValueError, OSError) as e:
        print("BŁĄD (UNKNOWN): " + str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
