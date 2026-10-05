#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do API ELI WOJEWÓDZKICH DZIENNIKÓW URZĘDOWYCH (16 dzienników, prawo miejscowe).
Tylko biblioteka standardowa Pythona (urllib/json/re/ssl) — brak zależności pip; do pełnego tekstu
aktu używa zewnętrznego `pdftotext` (poppler), gdy jest na PATH — bez niego tekst tylko z text.html
(zwykle TYLKO 1. strona aktu) albo z PDF przez --pdf.
Operacje WYŁĄCZNIE read-only (GET), bez klucza.

Każde województwo prowadzi własny e-Dziennik z API zgodnym z ELI (ten sam wzorzec co
api.sejm.gov.pl/eli, prefiks /api/eli), ale na WŁASNYM hoście — silnik zna tabelę 16 hostów.
Zakres: akty prawa MIEJSCOWEGO (uchwały rad gmin/powiatów/sejmików, rozporządzenia wojewody,
zarządzenia). Prawo krajowe (Dz.U./M.P.) → skill prawo-pl-eli (scripts/eli.py) — treść ustaw
i rozporządzeń krajowych zawsze stamtąd.

Komendy:
  dzienniki [--woj W]                 lista dzienników / roczniki i liczba aktów województwa
  szukaj --woj W ["<fraza tytułu>"] [--rok RRRR] [--limit N] [--strona N]
  akt <woj> <rok> <poz>               metadane aktu + powiązania (sprostowania, uchylenia,
                                      rozstrzygnięcia nadzorcze) z rejestru dziennika
  tekst <woj> <rok> <poz> [--fragment "<fraza>|§ N|art. N"] [--pdf ŚCIEŻKA]
                                      treść z PDF + ostrzeżenia z powiązań (nieważność, sprostowania)
Globalnie: --json  (zrzut surowego JSON zamiast podsumowania)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować jego kompletności:
                      niepełna lista rocznika, tekst tylko z 1. strony, uszkodzony tekst; w `tekst`
                      także: brak rejestru powiązań, akt nieważny w całości, fragment z unieważnionej
                      jednostki)
"""
import sys, json, re, time, argparse, base64, os, shutil, socket, ssl, subprocess, tempfile
import urllib.request, urllib.parse, urllib.error
from html.parser import HTMLParser

__version__ = "2.2.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)

# kod → (województwo, host, publisher ELI). Kody = sufiks publishera (POL_WOJ_XX).
WOJEWODZTWA = {
    "DS": ("dolnośląskie", "edzienniki.duw.pl", "POL_WOJ_DS"),
    "KP": ("kujawsko-pomorskie", "edzienniki.bydgoszcz.uw.gov.pl", "POL_WOJ_KP"),
    "LB": ("lubelskie", "edziennik.lublin.uw.gov.pl", "POL_WOJ_LB"),
    "LS": ("lubuskie", "dzienniki.luw.pl", "POL_WOJ_LS"),
    "LD": ("łódzkie", "dziennik.lodzkie.eu", "POL_WOJ_LD"),
    "MP": ("małopolskie", "edziennik.malopolska.uw.gov.pl", "POL_WOJ_MP"),
    "MZ": ("mazowieckie", "edziennik.mazowieckie.pl", "POL_WOJ_MZ"),
    "OP": ("opolskie", "duwo.opole.uw.gov.pl", "POL_WOJ_OP"),
    "PK": ("podkarpackie", "edziennik.rzeszow.uw.gov.pl", "POL_WOJ_PK"),
    "PL": ("podlaskie", "edziennik.bialystok.uw.gov.pl", "POL_WOJ_PL"),
    "PM": ("pomorskie", "edziennik.gdansk.uw.gov.pl", "POL_WOJ_PM"),
    "SL": ("śląskie", "dzienniki.slask.eu", "POL_WOJ_SL"),
    "SK": ("świętokrzyskie", "edziennik.kielce.uw.gov.pl", "POL_WOJ_SK"),
    "WM": ("warmińsko-mazurskie", "edzienniki.olsztyn.uw.gov.pl", "POL_WOJ_WM"),
    "WP": ("wielkopolskie", "edziennik.poznan.uw.gov.pl", "POL_WOJ_WP"),
    "ZP": ("zachodniopomorskie", "e-dziennik.szczecin.uw.gov.pl", "POL_WOJ_ZP"),
}

CONTENT_HOSTS = tuple(h for _, h, _ in WOJEWODZTWA.values())


def _wymus_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed) for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi HTTP na hostach dzienników, a obce cele HTTP odrzuca (jak w pozostałych silnikach)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # host śląski przekierowuje nieistniejący rekord (np. pozycję techniczną) na względny
        # „404_notfound", który po doklejeniu do ścieżki kończy się HTTP 400 — traktujemy to jako 404
        if urllib.parse.urlsplit(newurl).path.rstrip("/").endswith("404_notfound"):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found (przekierowanie na 404_notfound)",
                                         headers, fp)
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)


def _otworz(req, timeout, context):
    """Opener z kontrolą przekierowań i (opcjonalnie) kontekstem SSL z dociągniętym pośrednim."""
    handlers = [_PrzekierowaniaHttps()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


# edziennik.mazowieckie.pl (MZ) stoi za filtrem antybotowym (Akamai), który żądania „nieprzeglądarkowe"
# przetrzymuje bez odpowiedzi (timeout — także z polskiego IP, więc to NIE jest blokada geograficzna).
# Zweryfikowane 2026-10-05: odrzucane jest żądanie BEZ nagłówka Accept-Language oraz żądanie z adresem
# URL w User-Agent („+https://…"); User-Agent z samą nazwą i wersją silnika + Accept-Language przechodzą.
_UA = f"Mozilla/5.0 (compatible; prawo-pl-eli-edzienniki/{__version__})"
_ACCEPT_LANGUAGE = "pl-PL,pl;q=0.9"


def _ascii(s):
    return s.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _woj(s):
    """Kod (DS) albo nazwa województwa ('dolnośląskie', 'dolnoslaskie', prefiks) → (kod, nazwa, host, publisher).
    Prefiks pasujący do KILKU województw ('ma' → małopolskie i mazowieckie) = błąd z listą kandydatów,
    nigdy cichy wybór pierwszego."""
    if not s:
        sys.exit("Podaj województwo: --woj <kod|nazwa>, np. --woj DS albo --woj dolnośląskie.\n"
                 "Kody: " + ", ".join(f"{k}={v[0]}" for k, v in sorted(WOJEWODZTWA.items())))
    kod = s.strip().upper()
    if kod in WOJEWODZTWA:
        return (kod,) + WOJEWODZTWA[kod]
    szukane = _ascii(s.strip())
    kandydaci = [(k, v) for k, v in WOJEWODZTWA.items() if _ascii(v[0]).startswith(szukane)]
    if len(kandydaci) == 1:
        k, (nazwa, host, pub) = kandydaci[0]
        return k, nazwa, host, pub
    if kandydaci:
        sys.exit(f"Niejednoznaczne województwo {s!r} — pasuje: "
                 + ", ".join(f"{k}={v[0]}" for k, v in kandydaci) + ". Podaj kod albo pełną nazwę.")
    sys.exit(f"Nieznane województwo: {s!r}. Kody: "
             + ", ".join(f"{k}={v[0]}" for k, v in sorted(WOJEWODZTWA.items())))


def _norm(obj):
    """Rekurencyjnie znormalizuj klucze JSON do lowercase — 4 z 16 hostów (starsze wdrożenia
    ABC PRO) zwracają klucze PascalCase (Title/Items) zamiast camelCase (title/items)."""
    if isinstance(obj, dict):
        return {k.lower(): _norm(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_norm(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------------------------
# TLS: część hostów (MP edziennik.malopolska.uw.gov.pl, LS dzienniki.luw.pl, LD dziennik.lodzkie.eu)
# wysyła NIEPEŁNY łańcuch certyfikatów (sam liść, bez pośredniego Certum/home.pl). Przeglądarki
# i curl dociągają pośredni przez AIA (Authority Information Access → „CA Issuers"); urllib nie.
# Robimy to samo, wyłącznie biblioteką standardową: po CERTIFICATE_VERIFY_FAILED czytamy liść
# (połączenie bez weryfikacji służy TYLKO do odczytu certyfikatu — żadna treść nie jest nim pobierana),
# wyciągamy z DER adres http „CA Issuers", pobieramy pośredni, dokładamy do DOMYŚLNYCH CA i ponawiamy
# z PEŁNĄ weryfikacją. Nigdy nie pobieramy treści przez CERT_NONE.
# ---------------------------------------------------------------------------------------------
_SSL_CTX = None                      # kontekst z doładowanym pośrednim (wspólny w procesie)
_OSTATNI_BLAD = None                 # przyczyna ostatniego błędu wywołania soft (do komunikatów wyżej)
_OID_CA_ISSUERS = b"\x06\x08\x2b\x06\x01\x05\x05\x07\x30\x02"   # id-ad-caIssuers 1.3.6.1.5.5.7.48.2


def _aia_urls(der):
    """Adresy http „CA Issuers" z rozszerzenia AIA certyfikatu (DER): po OID caIssuers następuje
    GeneralName uniformResourceIdentifier (tag 0x86) + długość DER + URL."""
    urls = []
    for m in re.finditer(re.escape(_OID_CA_ISSUERS) + rb"\x86", der):
        i = m.end()
        if i >= len(der):
            continue
        ln = der[i]; i += 1
        if ln & 0x80:
            n = ln & 0x7f
            ln = int.from_bytes(der[i:i + n], "big"); i += n
        u = der[i:i + ln]
        if u.startswith(b"http://") and re.search(rb"\.(crt|cer|der|p7c)$", u, re.I):
            urls.append(u.decode("ascii", "replace"))
    return urls


def _der_do_pem(dane):
    if dane.lstrip().startswith(b"-----BEGIN"):
        return dane.decode("ascii", "replace")
    b = base64.b64encode(dane).decode("ascii")
    return ("-----BEGIN CERTIFICATE-----\n" + "\n".join(b[i:i + 64] for i in range(0, len(b), 64))
            + "\n-----END CERTIFICATE-----\n")


def _lisc_der(host):
    """Certyfikat liścia serwera w DER — połączenie bez weryfikacji, tylko handshake + odczyt certu."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=15) as s, \
            ctx.wrap_socket(s, server_hostname=host) as ss:
        return ss.getpeercert(binary_form=True)


def _ctx_z_aia(host):
    """Kontekst SSL = domyślne CA + pośredni pobrany z AIA liścia hosta; None, gdy się nie udało."""
    try:
        urls = _aia_urls(_lisc_der(host))
        if not urls:
            return None
        ctx = _SSL_CTX or ssl.create_default_context()
        # Pośredni pochodzi z adresu podanego przez NIEZWERYFIKOWANY liść, więc nie może stać się
        # kotwicą zaufania: bez PARTIAL_CHAIN OpenSSL wymaga, by łańcuch kończył się na
        # samopodpisanym korzeniu z domyślnego magazynu — pobrany certyfikat służy tylko do
        # zbudowania łańcucha (Python ≥3.10 włącza PARTIAL_CHAIN domyślnie).
        ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        for u in urls[:3]:
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": _UA}),
                                        timeout=15) as r:
                ctx.load_verify_locations(cadata=_der_do_pem(r.read()))
        return ctx
    except Exception:  # noqa: BLE001 — brak pośredniego = komunikat o niepełnym łańcuchu wyżej
        return None


def _blad_certyfikatu(e):
    r = getattr(e, "reason", e)
    return isinstance(r, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def _rodzaj_bledu_tls(e):
    """Przyczyna CERTIFICATE_VERIFY_FAILED → 'wygasl' | 'lancuch' | 'inny'. Kod OpenSSL (verify_code)
    ustawia warstwa C; gdy go brak, rozpoznajemy po treści komunikatu. Tylko 'lancuch' (brak
    pośredniego) da się naprawić po stronie klienta — dociągnięciem pośredniego przez AIA."""
    r = getattr(e, "reason", e)
    kod = getattr(r, "verify_code", None)
    tekst = f"{getattr(r, 'verify_message', '') or ''} {e}".lower()
    if kod == 10 or "certificate has expired" in tekst:
        return "wygasl"
    if kod in (2, 20, 21) or "local issuer" in tekst or "unable to get issuer" in tekst \
            or "unable to verify the first certificate" in tekst:
        return "lancuch"
    return "inny"


def _waznosc_certyfikatu(der):
    """(notBefore, notAfter) z DER certyfikatu — pierwsze dwie daty UTCTime/GeneralizedTime to pole
    validity. Best-effort: None, gdy nie da się odczytać."""
    daty = []
    for m in re.finditer(rb"\x17\x0d(\d{12})Z|\x18\x0f(\d{14})Z", der):
        s = (m.group(1) or m.group(2)).decode()
        if m.group(1):
            s = ("19" if int(s[:2]) >= 50 else "20") + s
        daty.append(f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]} UTC")
        if len(daty) == 2:
            return tuple(daty)
    return None


def _komunikat_tls(url, host, e):
    """Prawdziwa diagnoza błędu weryfikacji certyfikatu (wygaśnięcie ≠ brak pośredniego ≠ inne)."""
    r = getattr(e, "reason", e)
    powod = getattr(r, "verify_message", None) or str(e)
    rodzaj = _rodzaj_bledu_tls(e)
    if rodzaj == "wygasl":
        try:
            waznosc = _waznosc_certyfikatu(_lisc_der(host))
        except Exception:  # noqa: BLE001 — data ważności to tylko uzupełnienie diagnozy
            waznosc = None
        do = f" (ważny do {waznosc[1]})" if waznosc else ""
        return (f"BŁĄD TLS: {url}\n"
                f"Certyfikat serwera {host} WYGASŁ{do} — {powod}. To błąd po stronie serwera (certyfikat "
                "nie został odnowiony), nie niepełny łańcuch ani blokada geograficzna; dokładanie certyfikatów "
                "(SSL_CERT_FILE) nic tu nie da. Silnik nie wyłącza weryfikacji TLS — spróbuj później albo "
                f"otwórz UI w przeglądarce (ona też ostrzeże o wygasłym certyfikacie): https://{host}/")
    if rodzaj == "lancuch":
        return (f"BŁĄD TLS: {url}\n"
                f"Serwer {host} wysyła niepełny łańcuch certyfikatów po stronie serwera (brak "
                "certyfikatu pośredniego) i nie udało się go dociągnąć automatycznie przez AIA "
                f"({powod}). Host jest osiągalny — to nie jest blokada geograficzna. Obejście: "
                "SSL_CERT_FILE z dołożonym certyfikatem pośrednim (Certum/home.pl) albo UI: "
                f"https://{host}/")
    return (f"BŁĄD TLS: {url}\n"
            f"Weryfikacja certyfikatu serwera {host} nie powiodła się: {powod}. Silnik nie wyłącza "
            f"weryfikacji TLS — sprawdź akt w przeglądarce: https://{host}/")


def _fetch(url, host, raw=False, timeout=25, soft=False, brak_none=False):
    """GET url → bajty. soft=True: każdy błąd → None (wywołania pomocnicze, best-effort);
    brak_none=True: HTTP 404 → None (wywołujący sam wyjaśnia brak, np. pozycję techniczną);
    domyślnie błąd = komunikat i exit ≠ 0. Obsługuje AIA (niepełny łańcuch TLS) i 1 ponowienie."""
    global _SSL_CTX, _OSTATNI_BLAD
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/octet-stream" if raw else "application/json, text/html;q=0.9",
        "Accept-Language": _ACCEPT_LANGUAGE})
    aia_probowane = False
    attempt = 0
    while True:
        attempt += 1
        try:
            with _otworz(req, timeout, _SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt == 1:
                time.sleep(2); continue
            if soft or (brak_none and e.code == 404):
                _OSTATNI_BLAD = f"HTTP {e.code}"
                return None
            if e.code == 404:
                sys.exit(f"BŁĄD HTTP 404 (nie znaleziono): {url}")
            if e.code == 403:
                sys.exit(f"BŁĄD HTTP 403: {url}\n"
                         f"Host {host} odrzucił żądanie (WAF) — sprawdź akt w przeglądarce: https://{host}/")
            sys.exit(f"BŁĄD HTTP {e.code}: {url}")
        except Exception as e:  # noqa: BLE001
            if _blad_certyfikatu(e):
                # AIA pomaga WYŁĄCZNIE przy braku pośredniego — przy wygasłym certyfikacie nie próbujemy
                if not aia_probowane and _rodzaj_bledu_tls(e) == "lancuch":
                    aia_probowane = True
                    ctx = _ctx_z_aia(host)
                    if ctx is not None:
                        _SSL_CTX = ctx
                        continue
                if soft:
                    _OSTATNI_BLAD = _komunikat_tls(url, host, e).split("\n", 1)[-1].split(" — ")[0]
                    return None
                sys.exit(_komunikat_tls(url, host, e))
            if attempt == 1:
                time.sleep(2); continue
            if soft:
                _OSTATNI_BLAD = f"błąd sieci: {e}"
                return None
            filtr = ("\nUwaga: edziennik.mazowieckie.pl stoi za filtrem antybotowym (Akamai), który żądania "
                     "klientów nieprzeglądarkowych przetrzymuje bez odpowiedzi (timeout) — także z polskiego IP, "
                     "to NIE jest blokada geograficzna. Silnik wysyła nagłówki, które filtr przepuszczał "
                     "(zweryfikowane 2026-10-05); jeśli mimo to jest timeout, filtr zmienił reguły — otwórz akt "
                     "w przeglądarce i pobierz PDF ręcznie: https://edziennik.mazowieckie.pl/"
                     if host == "edziennik.mazowieckie.pl" else
                     f"\nUwaga: host {host} bywa chwilowo niedostępny — sprawdź w przeglądarce: https://{host}/")
            sys.exit(f"BŁĄD sieci: {url} ({e}){filtr}")


def _get(host, path, params=None, raw=False, brak_none=False):
    """GET https://{host}/api/eli{path}. Nagłówki: User-Agent silnika + Accept + Accept-Language (WAF na
    duwo.opole.uw.gov.pl odrzuca gołe klienty, filtr Akamai na edziennik.mazowieckie.pl — żądania bez
    Accept-Language). brak_none=True: HTTP 404 → None zamiast komunikatu."""
    url = f"https://{host}/api/eli{path}"
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "", False)})
        if q:
            url += "?" + q
    # cały rocznik (3–7 tys. aktów) i PDF potrzebują dłuższego timeoutu niż pojedynczy rekord
    duze = raw or re.fullmatch(r"/acts/[^/]+/\d+", path) is not None
    dane = _fetch(url, host, raw=raw, timeout=90 if duze else 25, brak_none=brak_none)
    if dane is None or raw:
        return dane
    tekst = dane.decode("utf-8", "replace")
    try:
        return _norm(json.loads(tekst))
    except json.JSONDecodeError:
        return tekst


def _get_rejestr(host, rok, poz):
    """Rejestr dziennika (backend UI Angulara, ten sam host, bez klucza):
    GET https://{host}/api/legalact?year={rok}&journal=0&position={poz} → ActDate, PublicationDate,
    ActStatus{IsInvalid,IsPartialInvalid,Description}, ActRelations[{RelationType, Description,
    LegalActsRelated[{Year,Position,LegalActType,ActDate,CaseNumber,Description}]}].
    Best-effort: każdy błąd → None (ostrzeżenie, nie blokada)."""
    url = f"https://{host}/api/legalact?" + urllib.parse.urlencode(
        {"year": rok, "journal": 0, "position": poz})
    dane = _fetch(url, host, timeout=25, soft=True)
    if not dane:
        return None
    try:
        d = _norm(json.loads(dane.decode("utf-8", "replace")))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) and ("actrelations" in d or "actstatus" in d) else None


def _data(v):
    """Data ISO z godziną → sama data; sentinel 0001-01-01 → None."""
    if not v or str(v).startswith("0001-01-01"):
        return None
    return str(v)[:10]


def _data_rejestru(v):
    """Data z rejestru dziennika: „01.01.2026" (BindingDateFrom na hoście śląskim) albo ISO → RRRR-MM-DD."""
    m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*", str(v or ""))
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return _data(v) if re.match(r"\d{4}-\d{2}-\d{2}", str(v or "")) else None


def _cytat(kod, rok, poz):
    """Urzędowa forma cytatu: „Dz. Urz. Woj. Dolnośląskiego z 2026 r. poz. 584" (skrót nazwy dziennika
    jak w polu ShortName z /api/eli/acts, np. „Dz. Urz. Woj. Kujawsko-Pomorskiego"). displayAddress z API
    („DZ. URZ. WOJ. 2026.584") NIE zawiera województwa albo podaje niejednolity skrót (LB „LUB.", LS „LUB")."""
    nazwa = WOJEWODZTWA[kod][0]
    dop = nazwa[:-2] + "iego" if nazwa.endswith("ie") else nazwa
    dop = "-".join(cz[:1].upper() + cz[1:] for cz in dop.split("-"))
    return f"Dz. Urz. Woj. {dop} z {rok} r. poz. {poz}"


def _pozycja_techniczna(host, kod, rok, poz, rej=None):
    """Kończy komunikatem, gdy rejestr dziennika oznacza pozycję jako techniczną (IsTechnicalPosition —
    pod numerem nie opublikowano aktu; host śląski). Zwraca rejestr (albo None) w pozostałych przypadkach."""
    if rej is None:
        rej = _get_rejestr(host, rok, poz)
    if isinstance(rej, dict) and rej.get("istechnicalposition"):
        opis = re.sub(r"\s+", " ", rej.get("title") or "").strip()
        sys.exit(f"Pozycja {rok}/{poz} dziennika {kod} jest POZYCJĄ TECHNICZNĄ — nie opublikowano pod nią "
                 f"żadnego aktu (rejestr dziennika: „{opis or 'IsTechnicalPosition'}”). To nie jest błąd "
                 f"silnika ani akt usunięty; sprawdź sąsiednie pozycje: szukaj --woj {kod} --rok {rok}.")
    return rej


# --- Powiązania z rejestru dziennika → ocena wiarygodności tekstu -----------------------------
# Kierunek relacji wynika z OPISU, nie z RelationType (ten sam typ po obu stronach): akt objęty
# relacją ma opis bierny („Ma rozstrzygnięcie nadzorcze (nieważność w części)", „Ma sprostowanie",
# „Uchylany przez", „Jest zmieniany przez"), akt, który ją wywołuje — czynny („Uchyla", „Zmienia",
# „Jest sprostowaniem dla", „Jest rozstrzygnięciem nadzorczym dla"). ActStatus.IsInvalid NIE znaczy
# „nieważny" (bywa true dla „Akt uchylony") — rozstrzyga opis statusu i relacji.
def _rodzaj_relacji(opis):
    o = (opis or "").lower()
    if not (o.startswith("ma ") or " przez" in o):
        return "czynne"
    if "nieważn" in o:
        return "niewaznosc_czesc" if ("w części" in o or "częściow" in o) else "niewaznosc_calosc"
    if "sprostowan" in o:
        return "sprostowanie"
    if "uchyl" in o:
        return "uchylenie"
    if "zmienian" in o:
        return "zmiana"
    return "inne"


def _jednostka_z_tytulu(tytul):
    """Zakres nieważności z tytułu rozstrzygnięcia/uchwały RIO → (opis, [jednostki główne]) albo (None, []).
    „…stwierdzające nieważność § 6 ust. 4 we fragmencie „wyniki pracy placówki” uchwały…" →
    („§ 6 ust. 4 we fragmencie „wyniki pracy placówki”", ["§ 6"]); „…nieważność § 3 ust. 1 i ust. 2 oraz
    § 9 ust. 1 i ust. 4 załącznika do uchwały…" → (…, ["§ 3", "§ 9"]). Tytuł bez oznaczenia jednostki
    („stwierdzające częściową nieważność uchwały…") → (None, []) — zakres trzeba ustalić z treści."""
    t = re.sub(r"\s+", " ", tytul or "")
    m = re.search(r"nieważno\w*\s+(.*?)\s*(?:\bdo\s+)?(?=\b(?:uchwał|zarządzen|rozporządzen)|$)", t, re.I)
    seg = m.group(1).strip() if m else ""
    if not re.match(r"(§|art\.)", seg, re.I):   # „nieważność uchwały … w części dotyczącej § 3"
        m = re.search(r"w częś\w+\s+(?:dotyczącej|obejmującej|w zakresie)\s+((?:§|art\.).*?)"
                      r"(?=[,;]|\s+(?:z\s+dnia|ze\s+względu|z\s+powodu)\b|$)", t, re.I)
        seg = m.group(1).strip() if m else ""
    glowne = []
    for g in re.finditer(r"(§|art\.)\s*(\d+[a-z]?)", seg, re.I):
        j = ("§ " if g.group(1) == "§" else "art. ") + g.group(2)
        if j not in glowne:
            glowne.append(j)
    return (seg.rstrip(" .,;"), glowne) if glowne else (None, [])


def _wpisy_powiazan(rej):
    """ActRelations → lista słowników {rodzaj, opis, typ, rok, poz, adres, data, sygnatura, tytul, zakres, jednostki}."""
    wpisy = []
    for rel in (rej or {}).get("actrelations") or []:
        if not isinstance(rel, dict):
            continue
        opis = rel.get("description") or rel.get("relationtype") or "powiązanie"
        rodzaj = _rodzaj_relacji(opis)
        powiazane = [la for la in rel.get("legalactsrelated") or [] if isinstance(la, dict)]
        if not powiazane:
            wpisy.append({"rodzaj": rodzaj, "opis": opis, "typ": None, "rok": None, "poz": None})
        for la in powiazane:
            wpisy.append({"rodzaj": rodzaj, "opis": opis, "typ": la.get("legalacttype") or "akt",
                          "rok": la.get("year"), "poz": la.get("position"),
                          "adres": la.get("description") or f"{la.get('year')}/{la.get('position')}",
                          "data": _data(la.get("actdate")), "sygnatura": la.get("casenumber") or None,
                          "tytul": None, "zakres": None, "jednostki": []})
    return wpisy


def _dociagnij_tytuly(host, wpisy, maks=5):
    """Tytuły aktów powiązanych (rozstrzygnięć, uchwał RIO, sprostowań) z ich rejestru — relacja sama
    tytułu nie podaje, a to on mówi, KTÓRĄ jednostkę unieważniono. Tylko relacje bierne istotne dla
    treści (nieważność, sprostowanie, inne), bez list zmian/uchyleń; najwyżej `maks` zapytań."""
    n = 0
    for w in wpisy:
        if w["rodzaj"] not in ("niewaznosc_calosc", "niewaznosc_czesc", "sprostowanie", "inne") or not w.get("poz"):
            continue
        if n >= maks:
            break
        n += 1
        r = _get_rejestr(host, w["rok"], w["poz"])
        if isinstance(r, dict) and r.get("title"):
            w["tytul"] = re.sub(r"\s+", " ", r["title"]).strip()
            if w["rodzaj"].startswith("niewaznosc"):
                w["zakres"], w["jednostki"] = _jednostka_z_tytulu(w["tytul"])
    return wpisy


def _status_rejestru(rej, status_eli=""):
    """→ 'niewaznosc_calosc' | 'niewaznosc_czesc' | 'uchylony' | None z opisu statusu (rejestr albo ELI)."""
    ast = (rej or {}).get("actstatus") or {}
    opisy = [(ast.get("description") or "") if isinstance(ast, dict) else "", status_eli or ""]
    for o in (x.lower() for x in opisy):
        if "nieważn" in o:
            return "niewaznosc_czesc" if "częściow" in o or "w części" in o else "niewaznosc_calosc"
        if "uchylon" in o:
            return "uchylony"
    if isinstance(ast, dict) and ast.get("ispartialinvalid") and not ast.get("isinvalid"):
        return "niewaznosc_czesc"
    return None


def _odnosnik(kod, w):
    """„Rozstrzygnięcie nadzorcze nr X z 2026-02-25 (DZ. URZ. WOJ. 2026.1099; tekst DS 2026 1099)"."""
    czesci = [w.get("typ") or "akt"]
    if w.get("sygnatura"):
        czesci.append(f"nr {w['sygnatura']}")
    if w.get("data"):
        czesci.append(f"z {w['data']}")
    gdzie = f"{w.get('adres')}; tekst {kod} {w['rok']} {w['poz']}" if w.get("poz") else (w.get("adres") or "")
    return f"{' '.join(czesci)} ({gdzie})"


def _ocena_powiazan(kod, rej, status_eli="", wpisy=None):
    """Ostrzeżenia o brzmieniu tekstu z powiązań → (ostrzeżenia[], blokujące_w_strict[], wpisy_nieważności_częściowej[]).
    Blokuje (strict) tylko nieważność W CAŁOŚCI — akt nie ma mocy prawnej; nieważność w części,
    sprostowanie, uchylenie i zmiany = głośne ostrzeżenie (reszta aktu jest prawem, tekst bywa potrzebny)."""
    ostrz, blok = [], []
    if wpisy is None:
        wpisy = _wpisy_powiazan(rej)
    st = _status_rejestru(rej, status_eli)
    calosc = [w for w in wpisy if w["rodzaj"] == "niewaznosc_calosc"]
    czesc = [w for w in wpisy if w["rodzaj"] == "niewaznosc_czesc"]
    if calosc or st == "niewaznosc_calosc":
        zrodla = "; ".join(_odnosnik(kod, w) + (f": „{w['tytul']}”" if w.get("tytul") else "") for w in calosc)
        blok.append("AKT NIEWAŻNY W CAŁOŚCI (stwierdzona nieważność"
                    + (f" — {zrodla}" if zrodla else " — status w rejestrze dziennika") + ") — ten tekst nie ma "
                    "mocy prawnej, nie cytuj go jako obowiązującego")
    elif czesc or st == "niewaznosc_czesc":
        for w in czesc:
            zakres = (f"unieważniona jednostka: {w['zakres']}" if w.get("zakres") else
                      "zakres nieważności nie wynika z tytułu — sprawdź treść: "
                      f"tekst {kod} {w['rok']} {w['poz']}" if w.get("poz") else "zakres nieznany")
            ostrz.append("NIEWAŻNOŚĆ W CZĘŚCI — " + _odnosnik(kod, w)
                         + (f": „{w['tytul']}”" if w.get("tytul") else "") + f"; {zakres}. Tekst niżej to "
                         "brzmienie z ogłoszenia, RAZEM z unieważnioną częścią")
        if not czesc:
            ostrz.append("NIEWAŻNOŚĆ W CZĘŚCI (status w rejestrze dziennika) — rozstrzygnięcia/wyroku nie wskazano "
                         "w powiązaniach; zakres sprawdź w późniejszych pozycjach dziennika i orzecznictwie "
                         "(prawo-pl-cbosa)")
    for w in wpisy:
        if w["rodzaj"] == "sprostowanie":
            ostrz.append("SPROSTOWANIE — " + _odnosnik(kod, w) + (f": „{w['tytul']}”" if w.get("tytul") else "")
                         + ". Tekst niżej to brzmienie z ogłoszenia BEZ sprostowania — przed cytatem sprawdź "
                         "treść obwieszczenia")
        elif w["rodzaj"] == "uchylenie":
            ostrz.append("AKT UCHYLONY przez " + _odnosnik(kod, w) + " — tekst historyczny")
        elif w["rodzaj"] == "inne":
            ostrz.append(f"{w['opis']}: " + _odnosnik(kod, w) + (f": „{w['tytul']}”" if w.get("tytul") else ""))
    zmiany = [w for w in wpisy if w["rodzaj"] == "zmiana"]
    if zmiany:
        ostrz.append(f"akt ZMIENIANY przez {len(zmiany)} akt(y) ("
                     + ", ".join(f"{w['rok']}/{w['poz']}" for w in zmiany[:8]) + ("…" if len(zmiany) > 8 else "")
                     + ") — tekst niżej to brzmienie PIERWOTNE, nie tekst jednolity")
    if st == "uchylony" and not any(w["rodzaj"] == "uchylenie" for w in wpisy):
        ostrz.append("AKT UCHYLONY (status w rejestrze dziennika) — tekst historyczny")
    return ostrz, blok, czesc


def _daty(d, lista=False):
    """→ (data_aktu, data_ogłoszenia). SEMANTYKA PÓL RÓŻNI SIĘ MIĘDZY ENDPOINTAMI (zweryfikowane
    2026-08 na DS/PM/PL/MP):
      • /acts/{pub}/{rok}/{poz} (akt): announcementDate = data AKTU („z dnia …"),
        promulgation = data OGŁOSZENIA w dzienniku (ze znacznikiem czasu) — jak w ELI Sejmu;
      • /acts/{pub}/{rok} (lista rocznika): ODWROTNIE — promulgation = data aktu,
        announcementDate = znacznik czasu ogłoszenia.
    Zabezpieczenie: ogłoszenie nie może poprzedzać daty aktu — gdy wychodzi wcześniej, pola są
    zamienione (host o innej konwencji) i zamieniamy je z powrotem."""
    a, p = _data(d.get("announcementdate")), _data(d.get("promulgation"))
    akt, ogl = (p, a) if lista else (a, p)
    if akt and ogl and ogl < akt:
        akt, ogl = ogl, akt
    return akt, ogl


_SUPER = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip, self.sup = [], 0, 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "sup":
            self.sup += 1
        if tag in ("p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4"):
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "sup" and self.sup:
            self.sup -= 1

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data.translate(_SUPER) if self.sup else data)


def _scal_indeksy(t):
    """text.html z dzienników emituje indeks górny jako OSOBNY akapit z samą cyfrą przed linią z „m":
    „…czasie\\n2\\nzakończono … od 1 m powierzchni" → „… od 1 m² powierzchni"."""
    return re.sub(r"\n([23])\s*\n([^\n]*?\d+ ?m)(?=[ ,.;)])",
                  lambda m: "\n" + m.group(2) + m.group(1).translate(_SUPER), t)


def html_to_text(html):
    p = _Stripper()
    p.feed(html)
    t = "".join(p.out).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n[ \t]+", "\n", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return _scal_indeksy(t).strip()


# ---------------------------------------------------------------------------------------------
# Tekst z urzędowego PDF (pdftotext -layout) — text.html na hostach dzienników zawiera zwykle
# TYLKO 1. STRONĘ aktu (DS, PM, PL, MP — zweryfikowane 2026-08), a na PL bywa uszkodzony (U+FFFD,
# brak „§", zlepione wyrazy). Pełny i wiarygodny tekst = PDF.
# ---------------------------------------------------------------------------------------------
def _pdftotext_dostepny():
    return shutil.which("pdftotext")


def _pdftotext(dane):
    """PDF (bajty) → surowy tekst `pdftotext -layout` (strony rozdzielone \\f) albo None."""
    exe = _pdftotext_dostepny()
    if not exe:
        return None
    fd, sciezka = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(dane)
        r = subprocess.run([exe, "-layout", "-enc", "UTF-8", sciezka, "-"],
                           capture_output=True, timeout=180)
    except Exception:  # noqa: BLE001 — pdftotext padł/timeout → jak brak narzędzia
        return None
    finally:
        try:
            os.unlink(sciezka)
        except OSError:
            pass
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace")


_RE_POZ = re.compile(r"^\s*Poz\.\s*(\d+)")
_RE_NAGLOWEK_STRONY = re.compile(r"^\s*Dziennik Urzędowy Województwa\b.*[–-]\s*\d+\s*[–-]")
_RE_PODPIS_EDZ = re.compile(r"^\s*(Podpisany przez\b|Data:\s*\d|Rodzaj:|Miejsce:)")
_RE_STOPKA_ID = re.compile(r"^\s*Id:\s*[0-9A-Za-z-]{6,}\.?\s*(Podpisany|Projekt|Uchwalony|Przyjęty)?\.?"
                           r"\s*(Strona\s+\d+(\s+z\s+\d+)?)?\s*$")
_RE_STRONA = re.compile(r"^\s*Strona\s+\d+(\s+z\s+\d+)?\s*$")
# miejscowość bywa wielowyrazowa („Gorzów Wielkopolski", „Zielona Góra"), a po „dnia" host
# warmińsko-mazurski wstawia dzień tygodnia („Olsztyn, dnia czwartek, 8 stycznia 2026 r.") — dzień
# tygodnia pomijamy; tylko [ \t], by nie wciągnąć wiersza „WOJEWÓDZTWA …" z linii wyżej
_RE_DNIA = re.compile(r"([A-ZĄĆĘŁŃÓŚŹŻ][\w-]*(?:[ \t]+[A-ZĄĆĘŁŃÓŚŹŻ][\w-]*)*),[ \t]*dnia[ \t]+"
                      r"(?:(?:poniedziałek|wtorek|środa|czwartek|piątek|sobota|niedziela),?[ \t]+)?"
                      r"(\d{1,2}[ \t]+\w+[ \t]+\d{4})[ \t]*r\.")
# początek jednostki redakcyjnej NA POCZĄTKU LINII („§ 1." / „Art. 5." z kropką — odwołania w zdaniu
# „§ 3 ust. 2" kropki nie mają) oraz punkt/litera/ustęp/tiret, rozdział, załącznik
_RE_JEDNOSTKA = re.compile(r"^(§\s*\d+[a-z]?\.|Art\.\s*\d+[a-z]?\.|\d+\.\s|\d+\)\s|[a-z]\)\s|[–-]\s|•\s|"
                           r"Rozdział\s+\S|ROZDZIAŁ\s+\S|DZIAŁ\s+\S|Dział\s+\S|Załącznik\b|ZAŁĄCZNIK\b)")


def _czysc_pdf(surowy):
    """Tekst z pdftotext -layout → tekst aktu: bez nagłówka dziennika (1. strona: „DZIENNIK
    URZĘDOWY … Poz. N" + kolumna e-podpisu), bez nagłówków kolejnych stron („Dziennik Urzędowy
    Województwa … – N – Poz. X"), bez stopek Legislatora („Id: …. Podpisany", „Strona N");
    zawinięte linie scalone w akapity (jednostka redakcyjna zaczyna linię → działa --fragment "§ N").
    → (tekst, liczba_stron, nagłówek_dziennika np. „Kraków, dnia 16 grudnia 2025 r., poz. 7877")."""
    strony = surowy.split("\f")
    if strony and not strony[-1].strip():
        strony.pop()
    linie, naglowek = [], None
    for nr, strona in enumerate(strony):
        ls = strona.split("\n")
        if nr == 0:
            for i, l in enumerate(ls[:15]):
                m = _RE_POZ.match(l)
                if m:
                    m2 = _RE_DNIA.search("\n".join(ls[:i]))
                    naglowek = (f"{m2.group(1)}, dnia {m2.group(2)} r., " if m2 else "") + f"poz. {m.group(1)}"
                    ls = ls[i + 1:]
                    break
            ls = [l for j, l in enumerate(ls) if not (j < 8 and _RE_PODPIS_EDZ.match(l))]
        else:
            for i, l in enumerate(ls[:3]):
                if _RE_NAGLOWEK_STRONY.match(l):
                    del ls[i]
                    break
        for l in ls:
            if _RE_STOPKA_ID.match(l) or _RE_STRONA.match(l):
                continue
            linie.append(l)
    return _scal_linie(linie), len(strony), naglowek


def _scal_linie(linie):
    """Linie -layout → akapity. Nowy akapit: pusta linia przed, początek jednostki redakcyjnej,
    linia wycentrowana/wyrównana do prawej (wcięcie ≥ 16: tytuły, podpisy) albo wiersz tabeli
    (≥ 3 spacje między komórkami); pozostałe linie doklejamy do poprzedniej spacją."""
    akapity, pusta = [], True
    for l in linie:
        surowa = l.rstrip()
        if not surowa.strip():
            pusta = True
            continue
        wciecie = len(surowa) - len(surowa.lstrip(" "))
        s = re.sub(r"[ \t]{3,}", "  ", surowa.strip())
        s = re.sub(r"(?<=\S)[ \t](?=\S)", " ", s)
        tabela = "  " in s
        if pusta or not akapity or wciecie >= 16 or tabela or _RE_JEDNOSTKA.match(s):
            akapity.append(s)
        else:
            akapity[-1] += " " + s
        pusta = False
    return "\n".join(akapity).strip()


_RE_MARKER = re.compile(r"(?mi)^\s*(§\s*\d+|art\.\s*\d+)")


def _ocena_tekstu(txt, zrodlo):
    """Sprawdzenie wiarygodności tekstu → (ostrzeżenia[], blokujące_w_strict[]).
    zrodlo: 'pdf' (pdftotext) albo 'html' (text.html)."""
    ostrz, blok = [], []
    n_fffd = txt.count("�")
    if n_fffd:
        blok.append(f"tekst zawiera {n_fffd} znaków zastępczych U+FFFD (uszkodzona konwersja po stronie "
                    "serwera) — cytuj WYŁĄCZNIE z urzędowego PDF")
    if not _RE_MARKER.search(txt):
        if zrodlo == "html" or len(txt.strip()) < 300:
            blok.append("brak oznaczeń jednostek redakcyjnych (§/Art.) — tekst niepełny albo uszkodzony")
        else:
            ostrz.append("brak oznaczeń jednostek redakcyjnych (§/Art.) — akt narracyjny (obwieszczenie, "
                         "rozstrzygnięcie) albo niepełna ekstrakcja; porównaj z PDF")
    if zrodlo == "html":
        blok.append("text.html zawiera zwykle tylko PIERWSZĄ STRONĘ aktu — to NIE jest pełny tekst")
    return ostrz, blok


# Reszta linii po „§ 1." złożona z samych numerów („2.  3.  4.") — wiersz numeracji kolumn tabeli.
# Co najmniej DWA numery: „§ 1. 1." (pierwszy ustęp w osobnej linii) to prawdziwy paragraf.
_RE_NAGLOWEK_KOLUMN = re.compile(r"(?:[ \t]+\d+\.){2,}[ \t]*$", re.M)
_RE_ZALACZNIK_EDZ = re.compile(r"(?m)^[ \t]*(?:Załącznik|ZAŁĄCZNIK)\b[^\n]*")


def _w_zalaczniku(txt, start, maks=120):
    """Fragment leży w załączniku (po linii „Załącznik…" na początku linii) → jej treść, inaczej None.
    Uchwała i jej załącznik (statut, regulamin, tabela) mają własne „§ 1" — trafienia trzeba odróżnić."""
    ost = None
    for m in _RE_ZALACZNIK_EDZ.finditer(txt):
        if m.start() > start:
            break
        ost = m
    if ost is None:
        return None
    t = ost.group(0).strip()
    return t if len(t) <= maks else t[:maks - 1].rstrip() + "…"


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Fragmenty wokół frazy → lista (start, end).
    • fraza = oznaczenie jednostki („§ 4", „§ 4.", „art. 7") → CAŁA jednostka: od jej nagłówka na
      początku linii do następnej jednostki tego samego typu (każde wystąpienie — np. § 1 uchwały
      i § 1 statutu w załączniku);
    • inna fraza → okno ~600 znaków rozszerzone do granic linii/akapitu (nigdy w pół słowa)."""
    f = fraza.strip()
    if not f:
        return []
    m = re.fullmatch(r"(§|art\.?)\s*(\d+[a-z]?)\.?", f, re.I)
    if m:
        nr = re.escape(m.group(2))
        # [ \t]*, nie \s*: \s przechodził przez koniec linii, więc samotny „§" (nagłówek kolumny
        # „§" w tabeli klasyfikacji budżetowej) + następna linia „1.  2.  3.  4." udawały „§ 1."
        if m.group(1) == "§":
            naglowek = re.compile(rf"(?m)^[ \t]*§[ \t]*{nr}\.(?![0-9])")
            nastepna = re.compile(r"(?m)^[ \t]*(§[ \t]*\d+[a-z]?\.(?![0-9])|Załącznik\b|ZAŁĄCZNIK\b)")
        else:
            naglowek = re.compile(rf"(?mi)^[ \t]*art\.[ \t]*{nr}\.(?![0-9])")
            nastepna = re.compile(r"(?mi)^[ \t]*(art\.[ \t]*\d+[a-z]?\.(?![0-9])|Załącznik\b)")
        spans = []
        for mm in naglowek.finditer(txt):
            if _RE_NAGLOWEK_KOLUMN.match(txt, mm.end()):
                continue  # „§ 1.  2.  3.  4." — numery kolumn tabeli, nie paragraf
            n = nastepna.search(txt, mm.end())
            while n and _RE_NAGLOWEK_KOLUMN.match(txt, n.end()):
                n = nastepna.search(txt, n.end())
            spans.append((mm.start(), n.start() if n else len(txt)))
            if len(spans) >= maks:
                break
        if spans:
            return spans
    low, fl = txt.lower(), f.lower()
    spans, p = [], low.find(fl)
    while p != -1 and len(spans) < maks:
        start = max(0, p - okno // 3)
        end = min(len(txt), p + len(fl) + okno)
        start = txt.rfind("\n", 0, start) + 1
        k = txt.find("\n", end)
        end = len(txt) if k == -1 else k
        if spans and start <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
        p = low.find(fl, p + len(fl))
    return spans


def _stronicuj(trafienia, limit, strona):
    """Okno paginacji na liście PRZEFILTROWANYCH trafień (nigdy na surowej odpowiedzi API):
    → (wycinek, indeks startu, liczba stron). Gwarantuje, że strony 1..N pokrywają cały zbiór."""
    strony = max(1, -(-len(trafienia) // limit))
    start = (strona - 1) * limit
    return trafienia[start:start + limit], start, strony


def _roczniki(host, publisher):
    """GET /acts → wpis publishera: lista lat + liczba aktów."""
    d = _get(host, "/acts")
    if isinstance(d, list):
        for wpis in d:
            if wpis.get("code") == publisher:
                return wpis
    return None


def _rocznik(host, pub, rok):
    """Cały rocznik → (odpowiedź, brakuje). BEZ parametru limit — serwer zwraca pełny rocznik.
    NIE używamy limit=100000: backend ABC PRO serwuje dla tej wartości NIEAKTUALNĄ kopię listy
    (PM 2026: 3149 z 3330 aktów, stare statusy) przy świeżym totalCount. Gdy lista krótsza niż
    totalCount → ponowienie z innym limitem; jeśli nadal krótsza → brakuje > 0 (wynik niepełny)."""
    d = _get(host, f"/acts/{pub}/{rok}")
    if not isinstance(d, dict):
        return d, 0
    items = d.get("items") or []
    total = int(d.get("totalcount") or 0)
    if total and len(items) < total:
        d2 = _get(host, f"/acts/{pub}/{rok}", {"limit": total + 500})
        if isinstance(d2, dict) and len(d2.get("items") or []) > len(items):
            d, items = d2, d2.get("items") or []
    return d, (max(0, total - len(items)) if total else 0)


def cmd_dzienniki(a):
    if not a.woj:
        if a.json:
            print(json.dumps({k: {"wojewodztwo": v[0], "host": v[1], "publisher": v[2]}
                              for k, v in WOJEWODZTWA.items()}, ensure_ascii=False, indent=2)); return
        print("Wojewódzkie dzienniki urzędowe (API ELI na hoście każdego województwa):\n")
        for k, (nazwa, host, pub) in sorted(WOJEWODZTWA.items()):
            print(f"  {k}  {nazwa:22s}  https://{host}/  ({pub})")
        print("\nRoczniki województwa: dzienniki --woj <kod>;  wyszukiwanie: szukaj --woj <kod> \"<fraza>\"")
        return
    kod, nazwa, host, pub = _woj(a.woj)
    wpis = _roczniki(host, pub)
    if a.json:
        print(json.dumps(wpis, ensure_ascii=False, indent=2)); return
    if not wpis:
        sys.exit(f"Host {host} nie zwrócił wpisu publishera {pub} — sprawdź https://{host}/")
    lata = wpis.get("years") or []
    print(f"# Dziennik Urzędowy — województwo {nazwa}  ({pub}, https://{host}/)")
    print(f"  Aktów łącznie: {wpis.get('deedscount') or wpis.get('count') or '?'}")
    if lata:
        print(f"  Roczniki: {lata[0]}–{lata[-1]}" if len(lata) > 1 else f"  Rocznik: {lata[0]}")


def cmd_szukaj(a):
    kod, nazwa, host, pub = _woj(a.woj)
    strict = getattr(a, "strict", False)
    if a.limit < 1 or a.strona < 1:
        sys.exit("--limit i --strona muszą być ≥ 1.")
    if a.rok:
        lata = [a.rok]
    else:
        wpis = _roczniki(host, pub)
        lata = sorted((wpis or {}).get("years") or [], reverse=True)[:3]
        if not lata:
            sys.exit(f"Nie udało się pobrać roczników z {host} — podaj --rok RRRR.")
    # API dzienników IGNORUJE serwerowe filtry (title/type/keyword) — pobieramy cały rocznik
    # jednym żądaniem (bez limitu; serwer nie stronicuje) i filtrujemy lokalnie po tytule.
    fraza = _ascii(a.fraza) if a.fraza else None
    zebrane, total_info, pominiete, niepelne, techniczne = [], {}, [], {}, {}
    for rok in lata:
        d, brakuje = _rocznik(host, pub, rok)
        if not isinstance(d, dict):
            # rocznik bez listy aktów = wynik NIEKOMPLETNY: strict blokuje, domyślnie głośno ostrzegamy
            if strict:
                sys.exit(f"BŁĄD: {host} zwrócił nieoczekiwaną odpowiedź dla rocznika {rok} — tryb strict "
                         "nie zwróci niekompletnego wyniku. Spróbuj ponownie albo zawęź: --rok RRRR.")
            pominiete.append(rok)
            continue
        items = d.get("items") or []
        total = int(d.get("totalcount") or 0) or len(items)
        if brakuje:
            if strict:
                sys.exit(f"BŁĄD: lista rocznika {rok} z {host} jest NIEPEŁNA ({len(items)} z {total} aktów, "
                         f"brakuje {brakuje} najnowszych) — tryb strict nie zwróci niekompletnego wyniku. "
                         "Ponów za chwilę albo sprawdź najnowsze pozycje przez akt/tekst.")
            niepelne[rok] = (len(items), total)
        # pozycje techniczne (host śląski: IsTechnicalPosition — pod numerem nie opublikowano aktu) przychodzą
        # jako puste wpisy (pos 0, title null); totalCount je liczy, więc kompletność sprawdzamy na surowej
        # liście wyżej, a z trafień je usuwamy
        akty = [it for it in items if it.get("pos") and it.get("title")]
        if len(akty) < len(items):
            techniczne[rok] = len(items) - len(akty)
        trafione = [it for it in akty if not fraza or fraza in _ascii(it.get("title") or "")]
        najnowsza = max((it.get("pos") or 0) for it in items) if items else 0
        total_info[rok] = {"trafien": len(trafione), "aktow": total, "pobrano": len(items),
                           "najnowsza_poz": najnowsza, "pelna": not brakuje,
                           "pozycje_techniczne": techniczne.get(rok, 0)}
        zebrane.extend(sorted(trafione, key=lambda it: it.get("pos") or 0, reverse=True))
        # bez frazy trafienia = cały rocznik — dalsze roczniki pobieramy tylko, gdy okno strony
        # tego wymaga; Z frazą pobieramy wszystkie żądane roczniki, by licznik trafień był pełny
        if not fraza and len(zebrane) >= a.strona * a.limit:
            break
    przeszukane = list(total_info)
    nieprzeszukane = [r for r in lata if r not in przeszukane and r not in pominiete]
    okno, start, strony = _stronicuj(zebrane, a.limit, a.strona)
    if not zebrane:  # zweryfikowane zero — także z --json komunikat, nie pusty JSON
        sys.exit(f"Brak wyników w {nazwa} ({', '.join(map(str, przeszukane or lata))}). Filtr frazy działa po "
                 "TYTULE aktu (podłańcuch, bez diakrytyków) — spróbuj krótszej frazy albo innego "
                 "roku (--rok)." + (f"\nUWAGA: rocznik(i) {', '.join(map(str, pominiete))} POMINIĘTE "
                                    f"({host} zwrócił nieoczekiwaną odpowiedź) — to NIE jest pełne zero."
                                    if pominiete else "")
                 + (f"\nUWAGA: lista rocznika {', '.join(map(str, niepelne))} NIEPEŁNA — to NIE jest pełne zero."
                    if niepelne else ""))
    if not okno:
        sys.exit(f"Strona {a.strona} poza zakresem: {len(zebrane)} trafień = {strony} "
                 f"stron(y) przy --limit {a.limit}.")
    # status w wierszu pochodzi z listy rocznika; w strict weryfikujemy go w rekordzie aktu
    # (do 20 wyświetlanych wierszy — sekwencyjnie, oszczędnie dla hosta)
    zweryfikowane = {}
    if strict:
        for it in okno[:20]:
            rec = _get(host, f"/acts/{pub}/{it.get('year')}/{it.get('pos')}")
            if isinstance(rec, dict) and rec.get("title"):
                zweryfikowane[(it.get("year"), it.get("pos"))] = rec.get("status") or ""
    if a.json:
        for it in okno:
            k = (it.get("year"), it.get("pos"))
            if k in zweryfikowane:
                it["status_zweryfikowany"] = zweryfikowane[k]
        print(json.dumps({"wojewodztwo": nazwa, "publisher": pub, "lata": lata,
                          "roczniki_przeszukane": przeszukane, "roczniki_nieprzeszukane": nieprzeszukane,
                          "roczniki_pominiete": pominiete,
                          "roczniki_niepelne": {str(r): {"pobrano": n, "aktow": t} for r, (n, t) in niepelne.items()},
                          "total": {str(r): f"{v['trafien']}/{v['aktow']}" for r, v in total_info.items()},
                          "roczniki": {str(r): v for r, v in total_info.items()},
                          "trafien": len(zebrane), "strona": a.strona,
                          "stron": strony, "items": okno},
                         ensure_ascii=False, indent=2)); return
    if pominiete:
        print(f"UWAGA: rocznik(i) {', '.join(map(str, pominiete))} POMINIĘTE — {host} zwrócił nieoczekiwaną "
              "odpowiedź; wynik może być niekompletny (ponów albo podaj --rok RRRR).")
    for r, (n, t) in niepelne.items():
        print(f"UWAGA: lista rocznika {r} NIEPEŁNA — {host} zwrócił {n} z {t} aktów (brakuje {t - n} "
              "NAJNOWSZYCH pozycji, statusy mogą być nieaktualne). Wynik NIE jest kompletny — ponów za chwilę "
              "albo sprawdź najnowsze pozycje bezpośrednio (akt/tekst).")
    laty = (f"{przeszukane[-1]}–{przeszukane[0]}" if len(przeszukane) > 1 else str(przeszukane[0]))
    opis = ", ".join(
        f"{r}: {v['trafien']}/{v['aktow']}" + ("" if v["pelna"] else f" (pobrano {v['pobrano']} — NIEPEŁNA)")
        for r, v in total_info.items())
    print(f"Województwo {nazwa}, przeszukane roczniki: {laty} (trafienia/akty wg lat: {opis})")
    if techniczne:
        print("  (pominięto pozycje techniczne bez aktu — "
              + ", ".join(f"{r}: {n}" for r, n in techniczne.items())
              + "; liczba aktów z nagłówka API je obejmuje)")
    if nieprzeszukane:
        print(f"  (roczniki {', '.join(map(str, nieprzeszukane))} NIE przeszukane — okno strony wypełnił "
              f"rocznik {przeszukane[-1]}; starsze: --rok RRRR)")
    print()
    for it in okno:
        adres = it.get("displayaddress") or f"{pub} {it.get('year')} poz. {it.get('pos')}"
        tytul = re.sub(r"\s+", " ", it.get("title") or "").strip()
        status = it.get("status") or ""
        data_aktu, ogl = _daty(it, lista=True)
        print(f"  [{it.get('year')}/{it.get('pos')}]  {adres}")
        print(f"    {it.get('type', '?')}: {tytul[:250]}{'…' if len(tytul) > 250 else ''}")
        k = (it.get("year"), it.get("pos"))
        if k in zweryfikowane:
            st_v = zweryfikowane[k]
            etykieta = "status (zweryfikowany w rekordzie aktu)"
            if st_v and st_v.lower() != status.lower():
                status = f"{st_v}  [lista rocznika podawała: {status or '—'}]"
            elif st_v:
                status = st_v
        else:
            etykieta = "status (wg listy rocznika)"
        print(f"    {etykieta}: {status or '—'}  · data aktu: {data_aktu or '?'}  · ogłoszono: {ogl or '?'}")
        print()
    if strony > 1:
        print(f"Pokazano {start + 1}–{start + len(okno)} z {len(zebrane)} trafień (roczniki {laty}) — reszta: "
              f"--strona <1..{strony}> (po {a.limit} na stronę) albo --limit {len(zebrane)}.")
    pierwsze = okno[0]
    print(f"Metadane/tekst: akt {kod} {pierwsze.get('year')} {pierwsze.get('pos')}  "
          f"/ tekst {kod} {pierwsze.get('year')} {pierwsze.get('pos')}")


def _powiazania(wpisy, kod):
    """Wpisy powiązań (z dociągniętymi tytułami) → lista wierszy tekstu dla `akt`."""
    wiersze = []
    for w in wpisy:
        if w.get("poz") is None and w.get("typ") is None:
            wiersze.append(w["opis"])
            continue
        czesci = [w.get("typ") or "akt"]
        if w.get("sygnatura"):
            czesci.append(f"nr {w['sygnatura']}")
        if w.get("data"):
            czesci.append(f"z {w['data']}")
        wiersz = f"{w['opis']}: {' '.join(czesci)} → {w.get('adres')}  [rok {w.get('rok')} poz. {w.get('poz')}]"
        if w.get("tytul"):
            wiersz += f"\n        tytuł: {w['tytul']}"
        if w.get("zakres"):
            wiersz += f"\n        zakres nieważności (z tytułu): {w['zakres']}"
        elif w["rodzaj"] == "niewaznosc_czesc" and w.get("tytul"):
            wiersz += (f"\n        zakres nieważności: nie wynika z tytułu — sprawdź treść: "
                       f"tekst {kod} {w.get('rok')} {w.get('poz')}")
        wiersze.append(wiersz)
    return wiersze


def _wejscie_w_zycie(d, rej):
    """Data wejścia w życie z opisem źródła. ELI podaje na hostach dzienników wartownik 0001-01-01 (=brak);
    rejestr dziennika bywa wypełniony (BindingDateFrom, np. host śląski „01.01.2026")."""
    eli = _data(d.get("entryintoforce")) or _data(d.get("validfrom"))
    if eli:
        return f"{eli} (metadane ELI)"
    rejestr = _data_rejestru((rej or {}).get("bindingdatefrom"))
    if rejestr:
        return f"{rejestr} (rejestr dziennika, pole BindingDateFrom — potwierdź w treści aktu)"
    return ("brak w metadanych (ELI: pusta wartość 0001-01-01) — ustal z treści aktu (przepis o wejściu "
            "w życie; zwykle 14 dni od ogłoszenia)")


def cmd_akt(a):
    kod, nazwa, host, pub = _woj(a.woj)
    d = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}", brak_none=True)
    rej = None
    if not isinstance(d, dict) or not d.get("title"):
        _pozycja_techniczna(host, kod, a.rok, a.poz)
        sys.exit(f"Nie znaleziono aktu {pub}/{a.rok}/{a.poz} na {host}.")
    rej = _get_rejestr(host, a.rok, a.poz)   # best-effort: powiązania + status z rejestru dziennika
    wpisy = _dociagnij_tytuly(host, _wpisy_powiazan(rej)) if rej is not None else []
    if a.json:
        if rej is not None:
            d["_rejestr_dziennika"] = {"actstatus": rej.get("actstatus"), "actrelations": rej.get("actrelations"),
                                       "actdate": rej.get("actdate"), "publicationdate": rej.get("publicationdate"),
                                       "bindingdatefrom": rej.get("bindingdatefrom")}
            d["_powiazania"] = wpisy
        else:
            d["_rejestr_dziennika"] = None
        d["_cytat"] = _cytat(kod, a.rok, a.poz)
        print(json.dumps(d, ensure_ascii=False, indent=2)); return
    tytul = re.sub(r"\s+", " ", d.get("title") or "").strip()
    data_aktu, ogl = _daty(d)
    print(f"# {_cytat(kod, a.rok, a.poz)}")
    print(f"  Cytat:    {_cytat(kod, a.rok, a.poz)}  (urzędowa forma; adres w API: "
          f"{d.get('displayaddress') or f'{pub} {a.rok} poz. {a.poz}'})")
    print(f"  Typ:      {d.get('type', '?')}")
    print(f"  Tytuł:    {tytul}")
    print(f"  Organ:    {', '.join(d.get('releasedby') or []) or '—'}")
    print(f"  Data aktu: {data_aktu or '?'}   Ogłoszony (publikacja w dzienniku): {ogl or '?'}")
    print(f"  Wejście w życie: {_wejscie_w_zycie(d, rej)}")
    if rej is not None and _data(rej.get("publicationdate")) and ogl and _data(rej.get("publicationdate")) != ogl:
        print(f"  (rejestr dziennika podaje datę publikacji {_data(rej.get('publicationdate'))} — rozbieżność z ELI)")
    # pola inForce/entryIntoForce bywają na hostach wojewódzkich niewypełnione — pokazujemy status
    st = d.get("status") or ""
    if st:
        print(f"  Status:   {st}")
    if rej is not None:
        ast = rej.get("actstatus") or {}
        opis = (ast.get("description") or "").strip() if isinstance(ast, dict) else ""
        if opis and opis.lower() != st.lower():
            print(f"  Status (rejestr dziennika): {opis}")
        if _status_rejestru(rej, st) == "niewaznosc_czesc" or any(w["rodzaj"] == "niewaznosc_czesc" for w in wpisy):
            zakresy = [w["zakres"] for w in wpisy if w["rodzaj"] == "niewaznosc_czesc" and w.get("zakres")]
            print("  UWAGA: częściowa nieważność aktu (rejestr dziennika)"
                  + (f" — unieważniono: {'; '.join(zakresy)}" if zakresy else
                     " — zakres w rozstrzygnięciu/wyroku niżej"))
    uch = _data(d.get("repealdate")) or _data(d.get("expirationdate"))
    if uch:
        print(f"  Uchylenie/wygaśnięcie: {uch}")
    kw = d.get("keywords") or []
    if kw:
        print(f"  Hasła:    {', '.join(kw)}")
    if rej is None:
        print(f"  Powiązania: nie udało się pobrać rejestru dziennika (https://{host}/api/legalact"
              + (f"; przyczyna: {_OSTATNI_BLAD}" if _OSTATNI_BLAD else "") + ") — "
              "sprostowania/uchylenia/rozstrzygnięcia nadzorcze sprawdź w późniejszych pozycjach dziennika")
    else:
        pw = _powiazania(wpisy, kod)
        if pw:
            print("  Powiązania (rejestr dziennika — sprostowania, uchylenia, rozstrzygnięcia nadzorcze):")
            for w in pw:
                print(f"    - {w}")
        else:
            print("  Powiązania: brak w rejestrze dziennika (stan na dziś — sprostowania i rozstrzygnięcia "
                  "pojawiają się pod późniejszymi pozycjami)")
    print(f"  ELI:      https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}")
    if d.get("texthtml"):
        print(f"  Tekst HTML: https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}/text.html  (zwykle tylko 1. strona)")
    if d.get("textpdf"):
        print(f"  Tekst PDF:  https://{host}/api/eli/acts/{pub}/{a.rok}/{a.poz}/text.pdf")
    print(f"\nTreść: tekst {kod} {a.rok} {a.poz}  [--fragment \"<fraza>|§ N\"] [--pdf plik.pdf]")


def _nakladanie(spans_a, spans_b):
    return any(s1 < e2 and s2 < e1 for s1, e1 in spans_a for s2, e2 in spans_b)


def cmd_tekst(a):
    kod, nazwa, host, pub = _woj(a.woj)
    strict = getattr(a, "strict", False)
    # rejestr dziennika PRZED treścią (jak w `akt`): pozycja techniczna, nieważność, sprostowania, zmiany
    rej = _pozycja_techniczna(host, kod, a.rok, a.poz)
    if rej is None:
        wpisy, pow_ostrz, pow_blok, czesc = [], [], [], []
        brak_rejestru = (f"nie udało się pobrać powiązań z rejestru dziennika (https://{host}/api/legalact"
                         + (f"; przyczyna: {_OSTATNI_BLAD}" if _OSTATNI_BLAD else "") + ") — "
                         "NIESPRAWDZONE: nieważność (rozstrzygnięcia nadzorcze, uchwały RIO, wyroki), sprostowania, "
                         "uchylenia")
    else:
        brak_rejestru = None
        wpisy = _dociagnij_tytuly(host, _wpisy_powiazan(rej))
        pow_ostrz, pow_blok, czesc = _ocena_powiazan(kod, rej, wpisy=wpisy)
    if a.pdf:
        dane = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}/text.pdf", raw=True)
        if not dane.startswith(b"%PDF"):
            sys.exit(f"Host {host} nie zwrócił PDF dla {pub}/{a.rok}/{a.poz} — sprawdź: akt {kod} {a.rok} {a.poz}")
        with open(a.pdf, "wb") as f:
            f.write(dane)
        print(f"Zapisano urzędowy PDF: {a.pdf} ({len(dane)} bajtów)")
        for u in pow_blok + pow_ostrz + ([brak_rejestru] if brak_rejestru else []):
            print(f"UWAGA: {u}")
        return
    # strict: brak rejestru = wynik niezweryfikowany; nieważność w całości = tekst bez mocy prawnej
    if strict and brak_rejestru:
        sys.exit(f"BŁĄD (strict): {brak_rejestru}. Tryb strict nie zwróci tekstu bez sprawdzenia, czy akt nie "
                 f"został unieważniony albo sprostowany — ponów za chwilę albo sprawdź: akt {kod} {a.rok} {a.poz}.")
    if strict and pow_blok:
        sys.exit("BŁĄD (strict): " + "; ".join(pow_blok)
                 + f".\nMetadane i powiązania: akt {kod} {a.rok} {a.poz}; urzędowy PDF jako dokument historyczny: "
                 f"tekst {kod} {a.rok} {a.poz} --pdf akt.pdf")
    txt, zrodlo, strony, naglowek, uwagi = None, None, 0, None, []
    if _pdftotext_dostepny():
        dane = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}/text.pdf", raw=True)
        if dane.startswith(b"%PDF"):
            surowy = _pdftotext(dane)
            if surowy is not None:
                txt, strony, naglowek = _czysc_pdf(surowy)
                zrodlo = "pdf"
            else:
                uwagi.append("pdftotext nie przetworzył PDF — używam text.html")
        else:
            uwagi.append("host nie zwrócił PDF — używam text.html")
    else:
        uwagi.append("brak `pdftotext` (poppler) na PATH — używam text.html; zalecane: zainstaluj poppler "
                     "(macOS: brew install poppler; Debian/Ubuntu: apt install poppler-utils)")
    if txt is None:
        surowe = _get(host, f"/acts/{pub}/{a.rok}/{a.poz}/text.html", raw=True).decode("utf-8", "replace")
        txt = html_to_text(surowe)
        zrodlo = "html"
    if not txt.strip():
        sys.exit(f"Pusty tekst ({zrodlo}) dla {pub}/{a.rok}/{a.poz} — pobierz PDF: tekst {kod} {a.rok} {a.poz} --pdf akt.pdf")
    ostrz, blok = _ocena_tekstu(txt, zrodlo)
    if strict and blok:
        sys.exit("BŁĄD (strict): tekst NIEZWERYFIKOWANY — " + "; ".join(blok)
                 + f".\nPobierz urzędowy PDF: tekst {kod} {a.rok} {a.poz} --pdf akt.pdf"
                 + ("" if zrodlo == "pdf" else " (z `pdftotext` na PATH silnik czyta PDF sam)."))
    spans = _fragmenty(txt, a.fragment) if a.fragment else None
    # fragment w jednostce objętej nieważnością częściową (zakres ustalony z tytułu rozstrzygnięcia)
    # (konserwatywnie: „§ N" obejmuje każde wystąpienie — także § N załącznika)
    trafione_niewazne = []
    if spans:
        for w in czesc:
            jedn = [j for j in w.get("jednostki") or [] if _nakladanie(spans, _fragmenty(txt, j))]
            if jedn:
                trafione_niewazne.append((w, jedn))
    if strict and trafione_niewazne:
        sys.exit("BŁĄD (strict): wypisany fragment leży w jednostce objętej stwierdzeniem nieważności — "
                 + "; ".join(f"{', '.join(j)} (unieważniono: {w['zakres']}; " + _odnosnik(kod, w) + ")"
                             for w, j in trafione_niewazne)
                 + ". Tryb strict nie zwróci unieważnionego brzmienia jako tekstu aktu.")
    uwagi_pow = pow_blok + pow_ostrz + ([brak_rejestru] if brak_rejestru else [])
    if trafione_niewazne:
        uwagi_pow.append("wypisany fragment leży w " + ", ".join(", ".join(j) for _, j in trafione_niewazne)
                         + " — jednostce objętej stwierdzeniem nieważności (unieważniono: "
                         + "; ".join(w["zakres"] for w, _ in trafione_niewazne) + ")")
    if a.json:
        print(json.dumps({"publisher": pub, "rok": a.rok, "poz": a.poz, "cytat": _cytat(kod, a.rok, a.poz),
                          "zrodlo": zrodlo, "strony": strony,
                          "naglowek_dziennika": naglowek, "uwagi": uwagi + ostrz,
                          "powiazania": wpisy if rej is not None else None, "uwagi_powiazan": uwagi_pow,
                          "niezweryfikowany": blok, "tekst": txt},
                         ensure_ascii=False, indent=2)); return
    if zrodlo == "pdf":
        print(f"# {pub} {a.rok} poz. {a.poz}  (tekst z urzędowego PDF przez pdftotext, {strony} str., "
              f"{len(txt)} znaków{'; nagłówek dziennika: ' + naglowek if naglowek else ''})")
    else:
        print(f"# {pub} {a.rok} poz. {a.poz}  (tekst z text.html, {len(txt)} znaków)")
        print(f"UWAGA: text.html zawiera zwykle tylko PIERWSZĄ STRONĘ aktu — to NIE jest pełny tekst; "
              f"pobierz PDF: tekst {kod} {a.rok} {a.poz} --pdf akt.pdf")
    print(f"Cytat: {_cytat(kod, a.rok, a.poz)}")
    for u in uwagi_pow:
        print(f"UWAGA: {u}")
    for u in uwagi + ostrz + [b for b in blok if not b.startswith("text.html")]:
        print(f"UWAGA: {u}")
    print()
    if a.fragment:
        if not spans:
            if zrodlo == "pdf":
                sys.exit(f"Nie znaleziono frazy {a.fragment!r} w tekście PDF ({strony} str., {len(txt)} znaków). "
                         "Spróbuj inną frazą.")
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w text.html ({len(txt)} znaków) — to zwykle TYLKO "
                     f"1. strona aktu, treść jest dłuższa; sprawdź PDF: tekst {kod} {a.rok} {a.poz} --pdf akt.pdf")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            zal = _w_zalaczniku(txt, s)
            if zal:
                print(f"[w załączniku: {zal}]\n")
            print(txt[s:e].strip())
        jednostka = re.fullmatch(r"(§|art\.?)\s*\d+[a-z]?\.?", a.fragment.strip(), re.I)
        if jednostka and spans and re.match(r"[ \t]*(§|[Aa]rt\.)", txt[spans[0][0]:spans[0][0] + 6]):
            print(f"\n(jednostka {a.fragment.strip()}: {len(spans)} wystąpień, każde w całości do następnej "
                  "jednostki — pominięto resztę; pełna treść: bez --fragment)")
        else:
            print(f"\n(okna: {len(spans)}, rozszerzone do granic akapitu — pominięto resztę; "
                  "pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długi akt {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\" albo \"§ N\")\n")
    print(txt)


def main():
    ap = argparse.ArgumentParser(
        description="API ELI wojewódzkich dzienników urzędowych (read-only, bez klucza): "
                    "prawo miejscowe 16 województw. Prawo krajowe (Dz.U./M.P.): scripts/eli.py.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut surowego JSON")
    ap.add_argument("--strict", action="store_true",
                    help="zakończ błędem, gdy nie udało się zweryfikować kompletności wyniku")
    sub = ap.add_subparsers(dest="cmd", required=True)

    dz = sub.add_parser("dzienniki")
    dz.add_argument("--woj", help="kod (DS) albo nazwa województwa — pokaże roczniki i liczbę aktów")
    dz.set_defaults(func=cmd_dzienniki)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None,
                   help="fraza z TYTUŁU aktu (podłańcuch, bez rozróżniania diakrytyków)")
    s.add_argument("--woj", required=True, help="kod (DS) albo nazwa województwa")
    s.add_argument("--rok", type=int, help="rocznik (bez --rok: do 3 najnowszych roczników)")
    s.add_argument("--limit", type=int, default=10, help="ile trafień pokazać na stronę")
    s.add_argument("--strona", type=int, default=1,
                   help="strona przefiltrowanych trafień (1..N — N podaje stopka wyniku)")
    s.set_defaults(func=cmd_szukaj)

    ak = sub.add_parser("akt")
    ak.add_argument("woj", help="kod (DS) albo nazwa województwa")
    ak.add_argument("rok", type=int)
    ak.add_argument("poz", type=int)
    ak.set_defaults(func=cmd_akt)

    t = sub.add_parser("tekst")
    t.add_argument("woj", help="kod (DS) albo nazwa województwa")
    t.add_argument("rok", type=int)
    t.add_argument("poz", type=int)
    t.add_argument("--fragment", help='cała jednostka ("§ 3", "art. 5") albo okna wokół frazy')
    t.add_argument("--pdf", help="zapisz urzędowy PDF do pliku")
    t.set_defaults(func=cmd_tekst)

    # Flagi globalne działają też PO komendzie (modele piszą je właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut surowego JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="zakończ błędem, gdy nie udało się zweryfikować kompletności wyniku")

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
