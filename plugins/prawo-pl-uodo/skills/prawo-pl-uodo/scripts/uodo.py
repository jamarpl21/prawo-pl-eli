#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do OFICJALNEGO API Portalu Orzeczeń UODO (https://orzeczenia.uodo.gov.pl/api-doc/).
Tylko biblioteka standardowa Pythona (urllib/json/re/html.parser) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (GET), bez klucza i bez autoryzacji.

Portal (od 2025 r.) publikuje DECYZJE PREZESA UODO (kary za naruszenia RODO, upomnienia, nakazy)
oraz rekordy powiązane (orzeczenia sądów, akty prawne) BEZ treści — z samymi metadanymi.
Identyfikatory: sygnatura (np. DKN.5131.9.2025) i URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9).
Treść RODO bierz z EUR-Lex (skill prawo-eu-eurlex); wyroki WSA/NSA ze skarg na decyzje UODO
— z CBOSA (prawo-pl-cbosa). Portal zapisuje kontrolę sądową decyzji w meta.json (dates[] z
use=repealed/defended/trial + refid wyroku) — silnik ją pokazuje, a --strict blokuje decyzje uchylone.

Komendy:
  najnowsze [--limit N]                          ostatnio WYDANE dokumenty (API sortuje po dacie decyzji)
  szukaj ["<fraza>"] [--tytul F] [--od RRRR-MM-DD] [--do RRRR-MM-DD] (po dacie DECYZJI = announcement)
         [--pub-od RRRR-MM-DD] [--pub-do RRRR-MM-DD] (po dacie publikacji w portalu)
         [--warunek "indeks:operator:wartość"] [--limit N] [--strona N]
  decyzja <sygnatura|URN> [--fragment "<fraza>"]  metadane + kontrola sądowa + pełna treść decyzji
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (decyzja: blokuje wynik bez treści albo UCHYLONY przez sąd; na listach nie działa)
"""
import sys, json, re, time, argparse, urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.0.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://orzeczenia.uodo.gov.pl/api"
CONTENT_HOSTS = ("orzeczenia.uodo.gov.pl",)
POLA = "id,refid,refname,title,dates,kind,parts,publication"  # domyślne pola listy wyników
URN_UODO = "urn:ndoc:gov:pl:uodo:"          # decyzje Prezesa UODO (jedyne rekordy z treścią)
DATY_PODSTAWOWE = ("announcement", "publication", "validation")
ZNACZENIE_USE = {                           # dates[].use poza datami podstawowymi = kontrola sądowa
    "repealed": "UCHYLONA (w całości lub w części)",
    "defended": "utrzymana (oddalono skargę)",
    "trial": "w toku (skarga rozpoznawana)",
    "other": "inne",
}
STATUS_PL = {"final": "prawomocna wg portalu", "nonfinal": "NIEPRAWOMOCNA",
             "repealed": "UCHYLONA", "published": "rekord niebędący decyzją"}
HEDGE_BRAK = ("UWAGA: brak w portalu ≠ nieistnienie decyzji (portal działa od 2025 r., starsze decyzje "
              "dodawane sukcesywnie) — sprawdź wyszukiwarkę na https://uodo.gov.pl i zaznacz to w odpowiedzi.")


def _wymus_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed)
                    for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi HTTP na hostach treści UODO, a obce cele HTTP odrzuca."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)


_opener = urllib.request.build_opener(_PrzekierowaniaHttps())


def _get(path, params=None, raw=False, brak_ok=False):
    """GET z jednym ponowieniem na błąd przejściowy. raw=True: zwróć tekst (body.txt/html).
    brak_ok=True: HTTP 404 zwraca None zamiast kończyć program."""
    url = BASE + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": f"prawo-pl-uodo/{__version__} (+https://github.com/jamarpl21/prawo-pl-eli)",
        "Accept": "text/plain" if raw else "application/json"})
    for attempt in (1, 2):
        try:
            with _opener.open(req, timeout=40) as r:
                tresc = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if e.code == 404:
                if brak_ok:
                    return None
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}\n"
                         "Sprawdź sygnaturę/URN — format np. DKN.5131.9.2025 albo "
                         f"urn:ndoc:gov:pl:uodo:2025:dkn_5131_9.\n{HEDGE_BRAK}")
            if e.code in (401, 403):
                sys.exit(f"BŁĄD HTTP {e.code}: {url}\n"
                         "API UODO zaczęło wymagać autoryzacji na tym endpointcie — sprawdź "
                         "https://orzeczenia.uodo.gov.pl/api-doc/ (dotąd działało bez klucza).")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                time.sleep(2); continue
            sys.exit(f"BŁĄD sieci: {url} ({e})")
    if raw:
        return tresc
    try:
        return json.loads(tresc)
    except json.JSONDecodeError:
        sys.exit(f"BŁĄD: API UODO zwróciło nie-JSON dla {url} — spróbuj ponownie za chwilę.")


def _get_opcjonalnie(path, params=None, raw=False):
    """GET danych pomocniczych: każda awaria (404, sieć, 5xx) → None, bez przerywania programu."""
    try:
        return _get(path, params, raw=raw, brak_ok=True)
    except SystemExit:
        return None


def _pl(v):
    """Pole wielojęzyczne ({'pl': …}) albo zwykły string → tekst."""
    if isinstance(v, dict):
        return v.get("pl") or next(iter(v.values()), "")
    return v or ""


def _expect_list(d, what):
    if not isinstance(d, list):
        sys.exit(f"BŁĄD: API UODO zwróciło nieoczekiwaną odpowiedź ({what}).")
    return d


def _expect_dict(d, what):
    if not isinstance(d, dict):
        sys.exit(f"BŁĄD: API UODO zwróciło nieoczekiwaną odpowiedź ({what}).")
    return d


def _daty(item):
    """Lista dates[] → {use: data} dla dat podstawowych (announcement = data decyzji,
    publication = publikacja w portalu, validation). Wpisy sądowe: _kontrola_sadowa()."""
    out = {}
    for d in item.get("dates") or []:
        out[d.get("use", "")] = d.get("date", "")
    return out


def _sygnatura_z_urn(refid):
    """URN orzeczenia → sygnatura: urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030 → 'II SA/Wa 1030/19',
    urn:ndoc:court:pl:sa:2021:iii_osk_3945 → 'III OSK 3945/21', TSUE …:c_621 → 'C-621/22'.
    Gdy nie umiemy odtworzyć — zwraca URN bez zmian."""
    refid = (refid or "").strip()
    m = re.fullmatch(r"urn:ndoc:court:pl:(sa|sp):(\d{4}):([^:]+)", refid.lower())
    if m:
        rok, reszta = m.group(2), re.sub(r"_p(_\d{8})?$", "", m.group(3))
        czl = reszta.split("_")
        if len(czl) >= 3 and czl[-1].isdigit():
            baza, _, miasto = czl[1].partition("-")
            baza = {"aca": "ACa", "pa": "Pa", "ca": "Ca"}.get(baza, baza.upper())
            if miasto:
                baza += "/" + miasto[:1].upper() + miasto[1:]
            return f"{czl[0].upper()} {baza} {czl[-1]}/{rok[2:]}"
    m = re.fullmatch(r"urn:ndoc:court:eu:tsue:(\d{4}):c_(\d+)", refid.lower())
    if m:
        return f"C-{m.group(2)}/{m.group(1)[2:]}"
    return refid


def _kontrola_sadowa(item):
    """Wpisy dates[] o use=repealed/defended/trial/other z refid orzeczenia — historia kontroli
    sądowej decyzji, którą portal zapisuje w meta.json (a body/status jej nie odzwierciedlają)."""
    out = []
    for d in item.get("dates") or []:
        use = d.get("use", "")
        refid = d.get("refid") or ""
        if use in DATY_PODSTAWOWE or (not refid and use not in ("repealed", "defended", "trial")):
            continue
        out.append({"date": d.get("date", ""), "use": use,
                    "znaczenie": ZNACZENIE_USE.get(use, use), "refid": refid,
                    "sygnatura": _sygnatura_z_urn(refid) if refid else "", "sad": ""})
    return out


def _uchylona(meta, kontrola):
    """Decyzja uchylona (w całości lub w części): status 'repealed' ALBO jakikolwiek wpis use=repealed
    — portal zostawia status 'final' przy częściowym uchyleniu (np. samej kary)."""
    return (meta.get("publication") or {}).get("status") == "repealed" or \
        any(k["use"] == "repealed" for k in kontrola)


def _czy_decyzja(refid):
    return not refid or refid.lower().startswith(URN_UODO)


def _wskazowka_zrodla(refid, refname):
    """Rekordy powiązane (bez treści w portalu UODO) → gdzie jest ich treść."""
    r = (refid or "").lower()
    if r.startswith("urn:ndoc:court:pl:"):
        return f'(orzeczenie sądu — bez treści w portalu UODO; treść: prawo-pl-cbosa sygnatura "{refname}")'
    if r.startswith("urn:ndoc:court:eu:tsue:"):
        return f"(wyrok TSUE — bez treści w portalu UODO; treść: prawo-eu-eurlex, CELEX {refname})"
    if r.startswith("urn:ndoc:pro:pl:durp:"):
        return f"(akt prawny — bez treści w portalu UODO; treść: prawo-pl-eli, {refname})"
    if r.startswith("urn:ndoc:pro:eu:ojol:"):
        return f"(akt prawa UE — bez treści w portalu UODO; treść: prawo-eu-eurlex, CELEX {refname})"
    if r.startswith("urn:ndoc:gov:eu:edpb:"):
        return f"(wytyczne EROD {refname} — bez treści w portalu UODO; źródło: edpb.europa.eu)"
    return f"(rekord powiązany {refid} — bez treści w portalu UODO)"


def _status_opis(status):
    return f"{status or '?'}" + (f" ({STATUS_PL[status]})" if status in STATUS_PL else "")


def _refid(s):
    """Sygnatura (DKN.5131.9.2025) albo URN → URN. Rok = ostatni 4-cyfrowy człon sygnatury.
    URN-y portalu są w ASCII — polskie znaki sygnatur transliterujemy (ZSOŚS → zsoss).
    Sygnatura sądowa (II SA/Wa 1030/19, III OSK 377/23) → URN rekordu powiązanego
    (urn:ndoc:court:pl:sa:2019:ii_sa-wa_1030) — taki rekord nie ma treści w portalu."""
    s = s.strip()
    if s.lower().startswith("urn:"):
        return s.lower()
    m = re.fullmatch(r"([IVXLivxl]+)\s+([A-Za-z]+)(?:/([A-Za-zĄ-ż]+))?\s+(\d+)/(\d{2}|\d{4})", s)
    if m:
        wydz, rodzaj, miasto, nr, rr = m.groups()
        rok = rr if len(rr) == 4 else ("20" if int(rr) < 50 else "19") + rr
        typ = "sa" if rodzaj.lower() in ("sa", "sab", "osk", "oz", "ops", "gsk", "gz", "gps",
                                         "fsk", "fz", "fps", "nsa") else "sp"
        czlon = rodzaj.lower() + (f"-{miasto.lower()}" if miasto else "")
        return f"urn:ndoc:court:pl:{typ}:{rok}:{wydz.lower()}_{czlon}_{nr}"
    czlony = [c for c in re.split(r"[.\s/]+", s) if c]
    rok = next((c for c in reversed(czlony) if re.fullmatch(r"(19|20)\d{2}", c)), None)
    if not rok or len(czlony) < 2:
        sys.exit(f"Nie umiem zbudować URN z {s!r}. Podaj sygnaturę (np. DKN.5131.9.2025) "
                 "albo pełny URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9).")
    reszta = "_".join(c.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
                      for c in czlony if c != rok)
    return f"urn:ndoc:gov:pl:uodo:{rok}:{reszta}"


def _timespan(od, do):
    return f"{od or ''},{do or ''}"


def _szukaj(timespan, warunek=None, limit=10, strona=0):
    """GET /documents/search/PublicDocument/{timespan}[/{warunek}] — lista dokumentów.
    timespan filtruje po dacie DECYZJI/orzeczenia (announcement), NIE po dacie publikacji."""
    sciezka = f"/documents/search/PublicDocument/{urllib.parse.quote(timespan, safe=',')}"
    if warunek:
        sciezka += "/" + urllib.parse.quote(warunek, safe=":,()")
    return _get(sciezka, {"order": "-id", "from": max(0, strona) * limit,
                          "count": max(1, min(limit, 100)), "fields": POLA})


def _wiersz(item):
    refname = item.get("refname") or item.get("refid") or "?"
    refid = item.get("refid") or ""
    daty = _daty(item)
    status = (item.get("publication") or {}).get("status", "")
    tytul = re.sub(r"\s+", " ", _pl(item.get("title"))).strip()
    print(f"  [{refname}]  (decyzja/orzeczenie {daty.get('announcement', '?')}, publikacja "
          f"{daty.get('publication', '—')})  {item.get('kind', '')}  status: {_status_opis(status)}")
    if tytul:
        print(f"    {tytul[:400]}{'…' if len(tytul) > 400 else ''}")
    if _czy_decyzja(refid):
        kontrola = _kontrola_sadowa(item)
        if kontrola:
            print("    kontrola sądowa: " + "; ".join(
                f"{k['znaczenie']} — {k['sygnatura'] or k['use']} ({k['date']})" for k in kontrola)
                + " → treść wyroku: prawo-pl-cbosa")
        print(f"    → decyzja {refname}")
    else:
        print(f"    {_wskazowka_zrodla(refid, refname)}")
    print()


def cmd_najnowsze(a):
    d = _expect_list(_szukaj(_timespan(None, None), limit=a.limit), "najnowsze dokumenty")
    if not d:
        sys.exit("Brak wyników — spróbuj ponownie za chwilę.")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    print(f"Ostatnio WYDANE dokumenty w portalu orzeczeń UODO ({len(d)}) — API sortuje po dacie "
          "decyzji/orzeczenia (announcement), NIE po dacie publikacji w portalu\n"
          "(ostatnio opublikowane, także starsze decyzje: szukaj --pub-od RRRR-MM-DD).\n")
    for it in d:
        _wiersz(it)


def _w_zakresie(data, od, do):
    return bool(data) and (not od or data >= od) and (not do or data <= do)


def cmd_szukaj(a):
    warunki = []
    if a.warunek:
        warunki.append(a.warunek)
    if a.fraza:
        warunki.append(f"content_pl:regex:{a.fraza}")
    if a.tytul:
        warunki.append(f"title_pl:regex:{a.tytul}")
    pub_od, pub_do = a.pub_od, a.pub_do
    if not warunki and not (a.od or a.do or pub_od or pub_do):
        sys.exit("Podaj kryterium: frazę (pełnotekstowo) albo --tytul / --warunek / zakres dat "
                 "--od/--do (data decyzji) lub --pub-od/--pub-do (data publikacji).")
    pub_serwer = False
    if pub_od and not warunki:   # jeden warunek na zapytanie — wolny → filtr publikacji po stronie API
        warunki.append(f"date_publication:ge:{pub_od}"); pub_serwer = True
    if len(warunki) > 1:
        print(f"UWAGA: API UODO stosuje JEDEN warunek na zapytanie — używam: {warunki[0]!r} "
              f"(pomijam: {', '.join(repr(w) for w in warunki[1:])}).\n", file=sys.stderr)
    do = a.do
    if pub_do and (not do or pub_do < do):
        do = pub_do  # data decyzji ≤ data publikacji — bezpieczne zawężenie okna API
    d = _szukaj(_timespan(a.od, do), warunki[0] if warunki else None, a.limit, a.strona)
    d = _expect_list(d, "wyniki wyszukiwania")
    pobrane = len(d)
    if pub_od or pub_do:
        d = [it for it in d if _w_zakresie(_daty(it).get("publication"), pub_od, pub_do)]
    if not d:
        if pobrane:
            sys.exit(f"Brak wyników po filtrze daty publikacji ({pub_od or '…'}–{pub_do or '…'}) "
                     f"spośród {pobrane} pobranych rekordów tej strony. Filtr publikacji działa "
                     "po stronie klienta (API przyjmuje jeden warunek) — sprawdź kolejne strony "
                     f"(--strona {a.strona + 1}) albo zawęź --od/--do po dacie decyzji. "
                     "To NIE dowód, że takich decyzji nie ma.")
        sys.exit("Brak wyników. Fraza działa jak regex (bez rozróżniania wielkości liter) i szuka "
                 "DOSŁOWNIE — a tytuły i treści są po polsku ODMIENIONE ('nałożenie kary "
                 "pieniężnej', nie 'kara pieniężna'). Podaj RDZEŃ bez końcówki: 'pieniężn', "
                 "'biometr', 'monitoring'. To NIE dowód, że takich decyzji nie ma.")
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    filtry = []
    if a.od or do:
        filtry.append(f"filtr po dacie decyzji (announcement): {a.od or '…'}–{do or '…'}")
    if pub_od or pub_do:
        filtry.append(f"filtr po dacie publikacji: {pub_od or '…'}–{pub_do or '…'} "
                      + ("(od: po stronie API; do: po stronie klienta)" if pub_serwer
                         else f"(po stronie klienta, tylko w obrębie {pobrane} pobranych rekordów)"))
    print(f"Znaleziono: {len(d)}  (strona {a.strona}, po {a.limit}; sortowanie API od najnowszych "
          "po dacie decyzji)" + ("\n  " + "; ".join(filtry) if filtry else "") + "\n")
    for it in d:
        _wiersz(it)
    if pobrane == a.limit:
        print(f"Kolejna strona: --strona {a.strona + 1}")


class _TekstZHtml(HTMLParser):
    """body.html portalu → tekst. Zachowuje numerację list (<dt>: '1.', 'a)'), odnośniki przypisów
    [n] w tekście (<sup>) i JEDEN blok przypisów (<div class="glosses">) — body.txt API gubi
    numerację i odnośniki, a przypisy dubluje."""
    BLOKI = {"div", "dd", "dt", "h1", "h2", "h3", "h4", "p", "li", "tr", "br", "table", "ul", "ol"}

    NAGLOWKI = ("h1", "h2", "h3", "h4")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.linie, self._buf, self._etykieta = [], [], None
        self._dl = []                   # stos <dl>: True = lista zagnieżdżona (wcina), False = first_level
        self._w_dt = self._w_h = False
        self._div = 0                   # głębokość <div>; przypisy od poziomu _glosy
        self._glosy = None

    @property
    def _wciecie(self):
        return sum(1 for zagniezdzona in self._dl if zagniezdzona)

    def _flush(self):
        tekst = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        self._buf = []
        if not tekst:
            return
        if self._w_dt:
            self._etykieta = tekst
            return
        if self._w_h:
            self.linie += ["", tekst, ""]
            return
        if self._etykieta:
            sep = "" if self._etykieta in ("„", "\"", "«", "»") else " "
            tekst, self._etykieta = self._etykieta + sep + tekst, None
        self.linie.append(("" if self._glosy is not None else "  " * self._wciecie) + tekst)

    def _granica(self, tag):
        """Granica bloku: wewnątrz nagłówka tylko odstęp (numer + tytuł w jednej linii)."""
        if tag in self.BLOKI:
            if self._w_h and tag not in self.NAGLOWKI:
                self._buf.append(" ")
            else:
                self._flush()

    def handle_starttag(self, tag, attrs):
        self._granica(tag)
        klasa = dict(attrs).get("class") or ""
        if tag == "div":
            self._div += 1
            if "glosses" in klasa and self._glosy is None:
                self._glosy = self._div
                self.linie += ["", "Przypisy:"]
        elif tag == "dl":
            self._dl.append("first_level" not in klasa)
        elif tag == "dt":
            self._w_dt = True
        elif tag in self.NAGLOWKI:
            self._w_h = True

    def handle_endtag(self, tag):
        self._granica(tag)
        if tag == "div":
            if self._glosy is not None and self._div <= self._glosy:
                self._glosy = None
            self._div = max(0, self._div - 1)
        elif tag == "dl" and self._dl:
            self._dl.pop()
        elif tag == "dt":
            self._w_dt = False
        elif tag in self.NAGLOWKI:
            self._w_h = False

    def handle_data(self, data):
        self._buf.append(data)

    def tekst(self):
        self._flush()
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self.linie)).strip()


def _tekst_z_html(html):
    p = _TekstZHtml()
    p.feed(html or "")
    p.close()
    return p.tekst()


def _tresc(refid):
    """Treść części 0 decyzji: z body.html (numeracja list, odnośniki i pojedynczy blok
    przypisów), awaryjnie body.txt API. Zwraca (tekst, źródło)."""
    czesc = urllib.parse.quote(refid + ":0", safe=":")
    html = _get_opcjonalnie(f"/documents/public/items/{czesc}/body.html", {"lang": "pl"}, raw=True)
    if html:
        txt = _tekst_z_html(html)
        if txt:
            return txt, "body.html"
    txt = _get(f"/documents/public/items/{czesc}/body.txt", params={"lang": "pl"}, raw=True)
    return (txt or "").strip(), "body.txt"


def _rozwin_kontrole(kontrola):
    """Dla wpisów kontroli sądowej dociąga meta.json orzeczenia (sygnatura i sąd z portalu);
    bez odpowiedzi zostaje sygnatura odtworzona z URN."""
    for k in kontrola:
        if not k["refid"]:
            continue
        m = _get_opcjonalnie(f"/documents/public/items/{urllib.parse.quote(k['refid'], safe=':')}/meta.json")
        if isinstance(m, dict):
            k["sygnatura"] = m.get("refname") or k["sygnatura"]
            k["sad"] = re.sub(r"\s+", " ", _pl(m.get("name"))).strip()
    return kontrola


def _wyjasnij_rekord_powiazany(meta, refid):
    refname = meta.get("refname") or refid
    daty = _daty(meta)
    tytul = re.sub(r"\s+", " ", _pl(meta.get("title"))).strip()
    sys.exit(f"To NIE jest decyzja Prezesa UODO, lecz rekord powiązany bez treści w portalu: "
             f"{_pl(meta.get('name')) or meta.get('kind', '?')} {refname} "
             f"(data orzeczenia/aktu: {daty.get('announcement', '?')}; URN {meta.get('refid', refid)}).\n"
             + (f"Początek sentencji/tezy z metadanych: {tytul[:400]}{'…' if len(tytul) > 400 else ''}\n"
                if tytul else "")
             + f"Pełna treść: {_wskazowka_zrodla(meta.get('refid', refid), refname)}")


def cmd_decyzja(a):
    refid = _refid(a.id)
    meta = _get(f"/documents/public/items/{urllib.parse.quote(refid, safe=':')}/meta.json", brak_ok=True)
    if meta is None:
        if not _czy_decyzja(refid):
            sys.exit(f"Nie znaleziono w portalu UODO rekordu {a.id} (URN {refid}) — to sygnatura "
                     "orzeczenia sądu, a portal UODO i tak nie ma treści wyroków. Treść: "
                     f'prawo-pl-cbosa sygnatura "{a.id}".')
        sys.exit(f"Nie znaleziono w portalu orzeczeń UODO: {a.id} (URN {refid}).\n"
                 "Sprawdź sygnaturę — format np. DKN.5131.9.2025 albo URN "
                 f"urn:ndoc:gov:pl:uodo:2025:dkn_5131_9.\n{HEDGE_BRAK}")
    meta = _expect_dict(meta, "metadane decyzji")
    if not _czy_decyzja(meta.get("refid") or refid) or meta.get("parts") == 0:
        _wyjasnij_rekord_powiazany(meta, refid)
    kontrola = _rozwin_kontrole(_kontrola_sadowa(meta))
    uchylona = _uchylona(meta, kontrola)
    txt, zrodlo = _tresc(refid)
    refname = meta.get("refname", a.id)
    pub = meta.get("publication") or {}
    uchylajace = [k for k in kontrola if k["use"] == "repealed"]
    if getattr(a, "strict", False):
        if uchylona:
            wyroki = ", ".join(f"{k['sygnatura']} z {k['date']}" for k in uchylajace) or "wg statusu portalu"
            sys.exit(f"BŁĄD: tryb strict blokuje decyzję {refname} — UCHYLONA przez sąd (w całości "
                     f"lub w części): {wyroki}. Treść decyzji nie odzwierciedla stanu prawnego; zakres "
                     "uchylenia ustal w sentencji wyroku: "
                     + "; ".join(f'prawo-pl-cbosa sygnatura "{k["sygnatura"]}"' for k in uchylajace)
                     + ". Bez --strict decyzja wyświetli się z ostrzeżeniem.")
        if not txt:
            sys.exit(f"BŁĄD: tryb strict blokuje decyzję {refname} bez zweryfikowanej pełnej treści.")
    if a.json:
        meta["_body"] = txt
        meta["_tresc_zrodlo"] = zrodlo
        meta["_kontrola_sadowa"] = kontrola
        meta["_uchylona"] = uchylona
        print(json.dumps(meta, ensure_ascii=False, indent=2)); return
    daty = _daty(meta)
    if uchylona:
        wyroki = ", ".join(f"{k['sygnatura']} z {k['date']}" for k in uchylajace) or "(brak wpisu wyroku w meta)"
        print(f"!!! DECYZJA UCHYLONA PRZEZ SĄD (w całości lub w części) — sprawdź zakres w wyroku {wyroki}: "
              + ("; ".join(f'prawo-pl-cbosa sygnatura "{k["sygnatura"]}"' for k in uchylajace)
                 or "prawo-pl-cbosa") + " !!!\n")
    print(f"# {_pl(meta.get('name')) or refname}   [{refname}]")
    print(f"  URN:        {meta.get('refid', refid)}")
    inforce = "tak" if pub.get("inforce") else "nie/brak danych"
    print(f"  Rodzaj:     {meta.get('kind', '?')}   status: {_status_opis(pub.get('status'))}"
          f"   publication.inforce wg API: {inforce}"
          + (" (pole nie odzwierciedla uchylenia)" if uchylona else ""))
    print(f"  Daty:       decyzja (ogłoszenie) {daty.get('announcement', '?')}, publikacja w portalu "
          f"{daty.get('publication', '?')}, walidacja {daty.get('validation', '—')}")
    print(f"  Portal:     https://orzeczenia.uodo.gov.pl (API: {BASE}/documents/public/items/{refid}/meta.json)")
    if pub.get("status") == "nonfinal":
        print("  UWAGA:      decyzja NIEPRAWOMOCNA (status nonfinal) — przysługuje skarga do WSA; "
              "cytuj z zastrzeżeniem (kontrola sądowa: prawo-pl-cbosa).")
    if kontrola:
        print("\n## Kontrola sądowa (wg meta.json portalu; treść wyroków: skill prawo-pl-cbosa)")
        for k in kontrola:
            opis = f"{k['sad']}, " if k["sad"] else ""
            print(f"  {k['date']}  {k['znaczenie']}  {opis}{k['sygnatura'] or k['refid'] or '?'}"
                  + (f'  → prawo-pl-cbosa sygnatura "{k["sygnatura"]}"' if k["sygnatura"] else ""))
        print("  (zakres uchylenia — cała decyzja czy tylko niektóre punkty, np. kara — wynika "
              "WYŁĄCZNIE z sentencji wyroku; kolejne orzeczenia, np. NSA, odnoszą się do wcześniejszego "
              "wyroku; poniżej jest tekst PIERWOTNY decyzji.)")
    tytul = _pl(meta.get("title"))
    if tytul:
        print(f"\n## Przedmiot\n{tytul.strip()}")
    print(f"\n## Treść decyzji ({len(txt)} znaków, źródło: {zrodlo})")
    if not txt:
        print("(brak treści w API — zob. portal wyżej)")
        return
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w treści ({len(txt)} znaków). Tekst jest "
                     "po polsku odmieniony — podaj RDZEŃ bez końcówki ('pieniężn' zamiast 'kara "
                     "pieniężna'). To NIE dowód, że decyzja o tym nie mówi.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            print(txt[s:e].strip())
        print(f"\n(okna: {len(spans)} — pominięto resztę; pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długa decyzja {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Okna tekstu wokół wystąpień frazy (bez rozróżniania wielkości liter) — jak w saos.py."""
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


def main():
    ap = argparse.ArgumentParser(
        description="Oficjalne API Portalu Orzeczeń UODO (read-only, bez klucza): "
                    "decyzje Prezesa UODO (z treścią i historią kontroli sądowej) oraz rekordy "
                    "powiązane (orzeczenia sądów, akty prawne — same metadane).")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    ap.add_argument("--strict", action="store_true",
                    help="decyzja: zakończ błędem, gdy brak pełnej treści albo decyzja została "
                         "UCHYLONA przez sąd (w całości lub w części); na listach nic nie zmienia")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("najnowsze", help="ostatnio WYDANE dokumenty (API sortuje po dacie decyzji, nie publikacji)")
    n.add_argument("--limit", type=int, default=10, help="ile wyników (1–100)")
    n.set_defaults(func=cmd_najnowsze)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None,
                   help="wyszukiwanie pełnotekstowe (regex, bez rozróżniania wielkości liter)")
    s.add_argument("--tytul", help="fraza w tytule/przedmiocie decyzji (regex)")
    s.add_argument("--warunek", help='surowy warunek API: "indeks:operator:wartość", '
                                     'np. "publicator_subtype:eq:uodo" (operatory: eq ne lt gt le ge in glob regex)')
    s.add_argument("--od", help="data DECYZJI/orzeczenia (announcement) od (RRRR-MM-DD) — tak filtruje API; "
                                "to NIE jest data publikacji w portalu")
    s.add_argument("--do", help="data DECYZJI/orzeczenia (announcement) do (RRRR-MM-DD)")
    s.add_argument("--pub-od", dest="pub_od", help="data PUBLIKACJI w portalu od (RRRR-MM-DD); po stronie API, "
                                                    "gdy nie ma innego warunku, inaczej tylko w obrębie pobranej strony")
    s.add_argument("--pub-do", dest="pub_do", help="data PUBLIKACJI w portalu do (RRRR-MM-DD); filtr po stronie klienta")
    s.add_argument("--limit", type=int, default=10, help="ile wyników (1–100)")
    s.add_argument("--strona", type=int, default=0, help="numer strony (od 0)")
    s.set_defaults(func=cmd_szukaj)

    d = sub.add_parser("decyzja")
    d.add_argument("id", help="sygnatura (DKN.5131.9.2025) albo URN (urn:ndoc:gov:pl:uodo:2025:dkn_5131_9)")
    d.add_argument("--fragment", help='wytnij okna wokół frazy w treści — podawaj RDZEŃ, np. "pieniężn"')
    d.set_defaults(func=cmd_decyzja)

    # Flagi globalne działają też PO komendzie (modele piszą je właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="decyzja: zakończ błędem przy braku treści albo uchyleniu przez sąd")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
