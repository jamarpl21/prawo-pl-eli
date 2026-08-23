#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helper do CBOSA — Centralnej Bazy Orzeczeń Sądów Administracyjnych (https://orzeczenia.nsa.gov.pl).
Tylko biblioteka standardowa Pythona (urllib/re/json) — brak zależności pip.
Operacje WYŁĄCZNIE read-only (publiczne strony, bez logowania).

CBOSA to urzędowa baza orzecznictwa SĄDÓW ADMINISTRACYJNYCH: NSA i 16 WSA (~2,4 mln orzeczeń
od 2004 r., wybrane starsze). CBOSA NIE MA oficjalnego API — ten silnik czyta publiczne strony
HTML wyszukiwarki (/cbo/search) i orzeczeń (/doc/{id}) z throttlingiem >=0,5 s między żądaniami.
Zmiana układu stron CBOSA może wymagać aktualizacji silnika. Orzecznictwo SN/TK/sądów
powszechnych/KIO bierz z SAOS (skill prawo-pl-saos); treść przepisów z ELI (prawo-pl-eli).

Komendy:
  szukaj ["<fraza>"] [--sad NSA|"WSA Warszawa"] [--sygnatura S] [--rodzaj wyrok|postanowienie|uchwala]
         [--symbol 6119] [--sedzia N] [--od RRRR-MM-DD] [--do RRRR-MM-DD] [--strona N]
  orzeczenie <doc_id> [--fragment "<fraza>"]   pełne orzeczenie: metadane, sentencja, uzasadnienie
  sygnatura <sygnatura...>                      znajdź orzeczenie po sygnaturze
Globalnie: --json  (zrzut sparsowanych danych jako JSON zamiast podsumowania; działa przed
                    komendą i po niej)
           --strict  (blokuje wynik, gdy nie udało się zweryfikować aktualności lub kompletności:
                      TLS niezweryfikowany, nierozpoznana strona, orzeczenie NIEPRAWOMOCNE albo
                      bez oznaczenia prawomocności na stronie CBOSA)

Prawomocność: strona /doc/{id} niesie w wierszu „Data orzeczenia" kursywę „orzeczenie prawomocne"
/ „orzeczenie nieprawomocne". Silnik ją parsuje (pole JSON `prawomocne`: true/false/null) — lista
wyników wyszukiwarki tego oznaczenia NIE ma, więc flaga jest dostępna tylko przez `orzeczenie`.
"""
import sys, os, json, re, time, argparse, ssl, calendar, html as html_mod
import urllib.request, urllib.parse, urllib.error, http.cookiejar

__version__ = "1.7.0"  # trzymaj w zgodzie z plugin.json (sprawdza tools/validate.py)
BASE = "https://orzeczenia.nsa.gov.pl"
CONTENT_HOSTS = ("orzeczenia.nsa.gov.pl",)


class VerificationUnknown(RuntimeError):
    """Zapytanie nie pozwoliło ustalić, czy dane istnieją."""


def _wymus_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    dozwolony = any(host == allowed or host.endswith("." + allowed)
                    for allowed in CONTENT_HOSTS)
    if parsed.scheme.lower() == "http" and dozwolony:
        return "https" + url[len(parsed.scheme):]
    return url


class _PrzekierowaniaHttps(urllib.request.HTTPRedirectHandler):
    """Podnosi HTTP na hostach treści CBOSA, a obce cele HTTP odrzuca."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        bezpieczny_url = _wymus_https(newurl)
        if urllib.parse.urlsplit(bezpieczny_url).scheme.lower() == "http":
            raise urllib.error.URLError(
                f"odrzucono przekierowanie treści na niezaufany host po HTTP: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, bezpieczny_url)

# Miasta WSA (klucz bez diakrytyków) → końcówka pełnej nazwy z formularza CBOSA.
# Wartości pola 'sad' to PEŁNE nazwy sądów — wartość spoza listy CBOSA po cichu zwraca 0 wyników.
_WSA = {
    "bialystok": "w Białymstoku", "bydgoszcz": "w Bydgoszczy", "gdansk": "w Gdańsku",
    "gliwice": "w Gliwicach", "gorzow": "w Gorzowie Wlkp.", "kielce": "w Kielcach",
    "krakow": "w Krakowie", "lublin": "w Lublinie", "lodz": "w Łodzi",
    "olsztyn": "w Olsztynie", "opole": "w Opolu", "poznan": "w Poznaniu",
    "rzeszow": "w Rzeszowie", "szczecin": "w Szczecinie", "warszawa": "w Warszawie",
    "wroclaw": "we Wrocławiu",
}
_RODZAJE = {"WYROK": "Wyrok", "POSTANOWIENIE": "Postanowienie",
            "UCHWAŁA": "Uchwała", "UCHWALA": "Uchwała"}


def _ascii(s):
    return s.lower().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _sad(s):
    """Alias sądu → pełna nazwa z formularza CBOSA (NSA, 'WSA Warszawa', pełna nazwa)."""
    if not s:
        return "dowolny"
    raw = s.strip()
    if raw.lower() == "dowolny":
        return "dowolny"
    if raw.upper() == "NSA":
        return "Naczelny Sąd Administracyjny"
    low = _ascii(raw)
    if low.startswith(("naczelny", "wojewodzki", "nsa oz", "nsa w warszawie")):
        return raw  # pełna nazwa z formularza CBOSA (także historyczne ośrodki zamiejscowe NSA)
    miasto = re.sub(r"^(wsa|wojewodzki sad administracyjny)?\s*(we?\s+)?", "", low).strip().rstrip(".")
    for klucz, koncowka in _WSA.items():
        if miasto == klucz or miasto.startswith(klucz):
            return "Wojewódzki Sąd Administracyjny " + koncowka
    sys.exit(f"Nieznany sąd: {s!r}. Użyj: NSA albo WSA <miasto> (np. \"WSA Warszawa\"), "
             f"albo pełnej nazwy z formularza CBOSA. Miasta WSA: {', '.join(sorted(_WSA))}.")


def _rodzaj(s):
    if not s:
        return "dowolny"
    r = _RODZAJE.get(s.strip().upper())
    if not r:
        sys.exit(f"Nieznany rodzaj orzeczenia: {s!r}. Użyj: wyrok, postanowienie, uchwala.")
    return r


def _data(s, koniec=False):
    """--od/--do → RRRR-MM-DD. Formularz CBOSA przyjmuje WYŁĄCZNIE ten format (inny zwraca
    stronę błędu bez wyników, nie do odróżnienia od przeciążenia). Skróty RRRR i RRRR-MM
    uzupełniamy do początku okresu (--od) albo jego końca (--do)."""
    if not s:
        return ""
    t = s.strip().replace(".", "-").replace("/", "-")
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", t)
    if not m:
        sys.exit(f"Nieprawidłowa data: {s!r}. Format RRRR-MM-DD (np. 2024-01-31); "
                 "można też podać sam rok (2024) albo rok z miesiącem (2024-01).")
    rok, mies, dzien = m.group(1), m.group(2), m.group(3)
    mm = int(mies) if mies else (12 if koniec else 1)
    if not 1 <= mm <= 12:
        sys.exit(f"Nieprawidłowa data: {s!r} — miesiąc poza zakresem. Format RRRR-MM-DD.")
    ostatni = calendar.monthrange(int(rok), mm)[1]
    dd = int(dzien) if dzien else (ostatni if koniec else 1)
    if not 1 <= dd <= ostatni:
        sys.exit(f"Nieprawidłowa data: {s!r} — nie ma takiego dnia. Format RRRR-MM-DD.")
    return f"{rok}-{mm:02d}-{dd:02d}"


# ── HTTP: throttling, ciasteczka sesji (paginacja), jawny opt-in TLS ────────────────────
_jar = http.cookiejar.CookieJar()
_ostatnie = [0.0]
_transport_tls_verified = True


def _dane_z_transportem(dane):
    """Dodaje do wyniku JSON stan uwierzytelnienia transportu w tym przebiegu."""
    return {**dane, "transport_tls_verified": _transport_tls_verified}


def _uwaga_o_transporcie():
    if _transport_tls_verified:
        return ""
    return ("UWAGA: transport TLS nie został zweryfikowany (CBOSA_INSECURE_TLS=1). "
            "Treść nie jest uwierzytelniona i przed cytowaniem wymaga sprawdzenia "
            "w innym źródle.")


def _drukuj_uwage_o_transporcie():
    uwaga = _uwaga_o_transporcie()
    if uwaga:
        print(uwaga + "\n")


def _z_uwaga_o_transporcie(komunikat):
    uwaga = _uwaga_o_transporcie()
    return f"{uwaga}\n{komunikat}" if uwaga else komunikat


def _sprawdz_transport_strict(a):
    if getattr(a, "strict", False) and not _transport_tls_verified:
        raise VerificationUnknown(
            "tryb strict odrzuca wynik pobrany bez weryfikacji certyfikatu TLS")


def _sprawdz_prawomocnosc_strict(a, d):
    """--strict a prawomocność: CBOSA sama oznacza orzeczenie jako (nie)prawomocne. Orzeczenie
    NIEPRAWOMOCNE mogło zostać uchylone w wyższej instancji — strict nie zwraca takiej treści.
    Brak oznaczenia (None) = aktualność niepotwierdzona — strict też blokuje (jawnie, nie
    jako „awaria": to nie jest błąd przejściowy, więc nie przez VerificationUnknown)."""
    if not getattr(a, "strict", False):
        return
    if d.get("prawomocne") is False:
        sys.exit(_z_uwaga_o_transporcie(
            f"BŁĄD (strict): {d.get('sygnatura') or d['doc_id']} — orzeczenie nieprawomocne — tryb "
            "strict nie zwraca treści, której aktualność nie jest potwierdzona; bez --strict "
            "dostaniesz tekst z ostrzeżeniem. Sprawdź orzeczenie powiązane (WSA ↔ NSA): "
            + (", ".join(f"{p['opis']} [{p['doc_id']}]" for p in d.get("powiazane", []))
               or "brak sygnatur powiązanych na stronie CBOSA") + f". Źródło: {d['url']}"))
    if d.get("prawomocne") is None:
        sys.exit(_z_uwaga_o_transporcie(
            f"BŁĄD (strict): {d.get('sygnatura') or d['doc_id']} — {_opis_braku_oznaczenia(d)} "
            "— tryb strict nie potwierdza aktualności takiej treści; bez --strict dostaniesz tekst "
            f"z adnotacją. Sprawdź w przeglądarce: {d['url']}"))


def _opis_braku_oznaczenia(d):
    if d.get("prawomocnosc") == "":
        return ("CBOSA nie podaje prawomocności tego orzeczenia (pole przy dacie orzeczenia jest "
                "puste — tak bywa przy postanowieniach wpadkowych i świeżych orzeczeniach)")
    return ("na stronie CBOSA nie znaleziono oznaczenia „orzeczenie prawomocne / nieprawomocne” "
            "(zmiana układu strony?)")


def _fetch(path, data=None):
    """GET/POST strony CBOSA (HTML). Throttling >=0,5 s; ponowienia z rosnącym odstępem.
    Zwraca pobrany HTML jako FOUND; UNKNOWN przekazuje przez VerificationUnknown.
    VERIFIED_ABSENT ustala dopiero parser _wyniki z poprawnej strony CBOSA.

    CBOSA miewa kilkunastosekundowe okna, w których ucina połączenia bez odpowiedzi — stąd
    łącznie ~26 s ponawiania (za krótkie okno ponowień = fałszywy raport „skill nie działa").
    CBOSA może serwować łańcuch certyfikatów, którego część systemów nie potrafi zweryfikować.
    Domyślnie taki błąd daje UNKNOWN. Dopiero CBOSA_INSECURE_TLS=1 pozwala ponowić żądanie bez
    weryfikacji; wynik jawnie niesie wtedy stan nieuwierzytelnionego transportu."""
    global _transport_tls_verified
    url = BASE + path
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    headers = {
        "User-Agent": f"Mozilla/5.0 (compatible; prawo-pl-cbosa/{__version__}; "
                      f"+https://github.com/jamarpl21/prawo-pl-eli)",
        "Accept-Language": "pl-PL,pl;q=0.9",
    }
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    # Kontekst jest lokalny dla jednego pobrania. Jawny opt-in nie zatruwa kolejnych
    # wywołań _fetch w tym samym procesie ustawieniem CERT_NONE.
    ssl_ctx = ssl.create_default_context()
    insecure_tls = False
    # odstęp przed kolejną próbą; None = ostatnia zwykła próba (dalej już błąd)
    for odstep in (2, 4, 8, 12, None):
        # Wewnętrzna pętla gwarantuje jedną realną, natychmiastową próbę po zmianie
        # kontekstu, nawet jeśli błąd certyfikatu wystąpił w ostatnim obiegu pętli zewnętrznej.
        while True:
            czekaj = 0.5 - (time.time() - _ostatnie[0])
            if czekaj > 0:
                time.sleep(czekaj)
            _ostatnie[0] = time.time()
            opener = urllib.request.build_opener(
                _PrzekierowaniaHttps(),
                urllib.request.HTTPSHandler(context=ssl_ctx),
                urllib.request.HTTPCookieProcessor(_jar))
            req = urllib.request.Request(url, data=body, headers=headers)
            if insecure_tls:
                _transport_tls_verified = False
            try:
                with opener.open(req, timeout=40) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                if e.code >= 500 and odstep is not None:
                    time.sleep(odstep)
                    break
                if e.code in (404, 410) and path.startswith("/doc/"):
                    # CBOSA odpowiada 410 (Gone) dla nieistniejącego doc_id — zweryfikowany brak,
                    # a nie awaria; "spróbuj ponownie" odsyłałoby użytkownika w nieskończoną pętlę
                    sys.exit(_z_uwaga_o_transporcie(
                        f"Nie znaleziono orzeczenia o id {path[len('/doc/'):]!r} w CBOSA "
                        f"(HTTP {e.code} — zweryfikowany brak). doc_id bierz z komendy "
                        "szukaj albo sygnatura."))
                dopisek = "; CBOSA ma codzienne krótkie okno serwisowe ok. 21:00" if e.code >= 500 else ""
                raise VerificationUnknown(f"HTTP {e.code}: {url}{dopisek}") from e
            except urllib.error.URLError as e:
                if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                    # Domyślnie odmawiamy; obniżenie wymaga jawnej decyzji operatora.
                    if os.environ.get("CBOSA_INSECURE_TLS") != "1":
                        raise VerificationUnknown(
                            f"błąd weryfikacji certyfikatu TLS: {url} ({e.reason}). "
                            "Treść z niezweryfikowanego połączenia nie nadaje się do cytowania. "
                            "Jeśli świadomie akceptujesz to ryzyko (np. znany problem po stronie "
                            "serwera), ustaw CBOSA_INSECURE_TLS=1 dla tego wywołania."
                        ) from e
                    if not insecure_tls:
                        print("UWAGA: CBOSA_INSECURE_TLS=1 — weryfikacja certyfikatu WYŁĄCZONA dla "
                              f"{url}. Treść pobrana tym połączeniem NIE jest uwierzytelniona i nie "
                              "powinna być cytowana bez sprawdzenia w innym źródle.", file=sys.stderr)
                        ssl_ctx = ssl.create_default_context()
                        ssl_ctx.check_hostname, ssl_ctx.verify_mode = False, ssl.CERT_NONE
                        insecure_tls = True
                        continue
                if odstep is not None:
                    time.sleep(odstep)
                    break
                raise VerificationUnknown(f"błąd sieci: {url} ({e}); serwer CBOSA ucina połączenia") from e
            except Exception as e:  # noqa: BLE001
                if odstep is not None:
                    time.sleep(odstep)
                    break
                raise VerificationUnknown(f"błąd sieci: {url} ({e}); serwer CBOSA ucina połączenia") from e
    raise VerificationUnknown(f"nie udało się pobrać {url} po kilku próbach")


# ── Parsowanie HTML (regexy zweryfikowane na żywych stronach CBOSA) ─────────────────────
def _flat(h):
    return re.sub(r"[\r\n\t]", " ", h)


def _text(fragment):
    """Fragment HTML → czysty tekst (akapity <P>/<BR> → nowe linie, encje, NBSP)."""
    t = re.sub(r"(?i)<(?:p|br|div|tr|li)\b[^>]*>", "\n", fragment)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_mod.unescape(t).replace("\xa0", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# Granica zdania: akapit albo .!?;: + odstęp + wielka litera/cudzysłów/nawias. Kropka po skrócie
# („art.", „ust.", „2018 r.", „Dz.U.") ani po pojedynczej literze nie kończy zdania.
_GRANICA = re.compile(r"\n+|[.!?;:]\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ„\"(])")
_SKROTY = {"art", "ust", "pkt", "lit", "poz", "nr", "np", "tj", "tzw", "tzn", "ww", "zw", "sygn",
           "str", "por", "zob", "wg", "itd", "itp", "ok", "ds", "m.in", "dz.u", "dz", "proc",
           "zd", "red", "wyd"}


def _granice_zdan(txt, a, b):
    """Pozycje (koniec granicy) początków zdań/akapitów w txt[a:b]."""
    out = []
    for m in _GRANICA.finditer(txt, a, b):
        if m.group(0)[0] in ".!?":
            przed = re.search(r"(\S+)$", txt[a:m.start()])
            tok = przed.group(1).lower().strip("„\"()[]") if przed else ""
            if (tok in _SKROTY or len(tok) <= 1) and "\n" not in m.group(0):
                continue
        out.append(m.end())
    return out


def _dopasuj_brzegi(txt, start, end, min_start, max_end, luz=150):
    """Przesuwa brzegi okna do granicy zdania (w zapasie `luz` znaków), a gdy jej nie ma — do
    granicy wyrazu. Okno nigdy nie kurczy się poza [min_start, max_end] (pozycje frazy), więc
    fraza zostaje w środku. Bez żadnej granicy (ciągły ciąg znaków) brzeg zostaje jak był."""
    if start > 0:
        limit = min(start + luz, min_start)
        zdania = _granice_zdan(txt, start, limit)
        if zdania:
            start = zdania[0]
        else:
            m = re.compile(r"\s+").search(txt, start, limit)
            if m:
                start = m.end()
    if end < len(txt):
        limit = max(end - luz, max_end)
        zdania = _granice_zdan(txt, limit, end)
        if zdania:
            end = zdania[-1]
        else:
            spacje = [m.start() for m in re.finditer(r"\s", txt[limit:end])]
            if spacje:
                end = limit + spacje[-1]
    return start, end


def _fragmenty(txt, fraza, maks=6, okno=600):
    """Okna tekstu wokół wystąpień frazy (bez rozróżniania wielkości liter) — jak w saos.py.
    Brzegi okien dopasowane do granic zdań/wyrazów (_dopasuj_brzegi), żeby wycinek nie zaczynał
    się ani nie kończył w środku słowa (audyt: „kupno i sprz", „aluty tradycyjne")."""
    low, f = txt.lower(), fraza.lower().strip()
    if not f:
        return []
    spans, p = [], low.find(f)  # (start, end, pierwsza_fraza, koniec_ostatniej_frazy)
    while p != -1 and len(spans) < maks:
        start = max(0, p - okno // 3)
        end = min(len(txt), p + len(f) + okno)
        if spans and start <= spans[-1][1]:
            s0, e0, p0, _ = spans[-1]
            spans[-1] = (s0, max(e0, end), p0, p + len(f))
        else:
            spans.append((start, end, p, p + len(f)))
        p = low.find(f, p + len(f))
    return [_dopasuj_brzegi(txt, s, e, p0, p1) for s, e, p0, p1 in spans]


def _prawomocnosc(flat):
    """Oznaczenie prawomocności ze strony /doc/{id} → (bool|None, tekst etykiety|None).
    CBOSA wstawia je w komórce „Data orzeczenia" jako zagnieżdżoną tabelę:
      <td>2018-06-06</td><td style="… font-style: italic;">orzeczenie nieprawomocne</td>
    — ogólny parser metadanych (_orzeczenie) czyta tylko datę, stąd osobny odczyt tej komórki.
    Wynik (bool|None, etykieta): etykieta = tekst oznaczenia; "" = komórka kursywy jest, ale PUSTA
    (&nbsp; — CBOSA nie podaje prawomocności, zweryfikowane na żywo np. dla postanowienia
    o wstrzymaniu wykonania z 2026-08); None = komórki nie znaleziono (zmiana układu strony?).
    Nie zgadujemy: brak oznaczenia to „nie wiadomo", nie „prawomocne"."""
    m = re.search(r'Data orzeczenia</td>.*?<td[^>]*class="info-list-value"[^>]*>(.*?)</tr>\s*</table>',
                  flat, re.S)
    komorka = m.group(1) if m else ""
    if not komorka:  # awaryjnie: etykieta gdziekolwiek na stronie (poza treścią uzasadnienia)
        tresc = flat.find('class="info-list-value-uzasadnienie"')
        komorka = flat[:tresc] if tresc != -1 else flat
    m = re.search(r"orzeczenie\s+(nie)?prawomocne", komorka, re.I)
    if m:
        return (m.group(1) is None), re.sub(r"\s+", " ", m.group(0)).lower()
    kursywa = re.search(r'<td[^>]*font-style:\s*italic[^>]*>(.*?)</td>', komorka, re.S)
    if kursywa and not _text(kursywa.group(1)).strip():
        return None, ""  # pole prawomocności istnieje, ale CBOSA zostawiła je puste
    return None, None


def _wyniki(strona_html):
    """Lista wyników wyszukiwarki → (liczba trafień, [pozycje], komunikat CBOSA). Pozycja:
    doc_id, opis, snippet, powiazane (True = link z sekcji „orzeczenia powiązane", nie
    samodzielne trafienie).

    Komunikaty formularza CBOSA siedzą w <div class="warning"> i NIE są błędem serwera:
    „Nie znaleziono orzeczeń…" to ZWERYFIKOWANE zero (total=0 — inaczej każde puste
    wyszukiwanie wyglądałoby jak awaria), „Niepoprawny format daty…" to błąd zapytania."""
    flat = _flat(strona_html)
    komunikat = ""
    m = re.search(r'<div class="warning">(.*?)</div>', flat, re.S)
    if m:
        komunikat = re.sub(r"\s+", " ", _text(m.group(1))).strip()
    total = None
    m = re.search(r"Znaleziono\s+([\d\s\xa0]+)\s+orzecze", flat)
    if m:
        total = int(re.sub(r"[\s\xa0]", "", m.group(1)))
    elif _ascii(komunikat).startswith("nie znaleziono orzecze"):
        total = 0
    pozycje = []
    for m in re.finditer(r'<a href="/doc/([A-F0-9]{6,})"[^>]*>(.*?)</a>', flat, re.I):
        opis = re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if not opis:  # ikonki „wszystkie orzeczenia powiązane" itp.
            continue
        przed = flat[:m.start()]
        powiazane = przed.rfind('class="powiazane"') > przed.rfind("font-size: 12pt")
        snippet = ""
        if not powiazane:
            za = flat[m.end():m.end() + 4000]
            nast = re.search(r'<a href="/doc/', za)
            za = za[:nast.start()] if nast else za
            sm = re.search(r'<td class="info-list-value[^"]*"[^>]*font-size: 10pt[^>]*>(.*?)</td>', za, re.S)
            if sm:
                snippet = re.sub(r"\s+", " ", _text(sm.group(1))).strip()
        pozycje.append({"doc_id": m.group(1), "opis": opis, "snippet": snippet, "powiazane": powiazane})
    if total is None and not pozycje and not komunikat:
        raise VerificationUnknown("CBOSA zwróciło stronę bez licznika i listy wyników")
    return total, pozycje, komunikat


def _orzeczenie(strona_html, doc_id):
    """Strona /doc/{id} → dict: tytuł, sygnatura, metadane (tabela), sentencja/tezy/uzasadnienie."""
    flat = _flat(strona_html)
    d = {"doc_id": doc_id, "url": f"{BASE}/doc/{doc_id}"}
    m = re.search(r"<TITLE>([^<]+)</TITLE>", flat, re.I)
    if m:
        d["tytul"] = html_mod.unescape(m.group(1)).strip()
        if " - " in d["tytul"]:
            d["sygnatura"] = d["tytul"].split(" - ")[0].strip()
    meta, powiazane = {}, []
    for m in re.finditer(r'class="info-list-label"[^>]*>(.*?)</td>\s*<td[^>]*class="info-list-value"[^>]*>(.*?)</td>',
                         flat, re.S):
        lab = re.sub(r"\s+", " ", _text(m.group(1))).strip()
        if not lab:
            continue
        meta[lab] = _text(m.group(2))
        if "powiązane" in lab.lower():
            powiazane = [{"doc_id": pm.group(1), "opis": re.sub(r"\s+", " ", _text(pm.group(2))).strip()}
                         for pm in re.finditer(r'<a href="/doc/([A-F0-9]{6,})"[^>]*>(.*?)</a>', m.group(2), re.I)]
    d["metadane"] = meta
    if powiazane:
        d["powiazane"] = powiazane
    if meta:
        # oznaczenie CBOSA „orzeczenie prawomocne / nieprawomocne" (komórka „Data orzeczenia")
        d["prawomocne"], d["prawomocnosc"] = _prawomocnosc(flat)
    for sekcja in ("Tezy", "Sentencja", "Uzasadnienie"):
        m = re.search(r'<div class="lista-label">\s*' + sekcja +
                      r'\s*</div>\s*<span class="info-list-value-uzasadnienie">(.*?)</span>', flat, re.S)
        if m:
            d[sekcja.lower()] = _text(m.group(1))
    return d


def _etykieta_prawomocnosci(d):
    if d.get("prawomocne") is True:
        return "prawomocne (oznaczenie CBOSA „orzeczenie prawomocne”)"
    if d.get("prawomocne") is False:
        return "NIEPRAWOMOCNE (oznaczenie CBOSA „orzeczenie nieprawomocne”) — zob. UWAGA wyżej"
    if d.get("prawomocnosc") == "":
        return "nieznana — CBOSA nie podaje (puste pole prawomocności) — zob. UWAGA wyżej"
    return "nieznana — brak oznaczenia „orzeczenie prawomocne / nieprawomocne” na stronie — zob. UWAGA wyżej"


def _uwaga_o_prawomocnosci(d):
    """Głośne ostrzeżenie do wyniku tekstowego i JSON, gdy CBOSA oznacza orzeczenie jako
    NIEPRAWOMOCNE (mogło zostać uchylone/zmienione w NSA) albo oznaczenia brak."""
    if d.get("prawomocne") is True:
        return ""
    kto = d.get("sygnatura") or d["doc_id"]
    if d.get("prawomocne") is False:
        pow_ = d.get("powiazane") or []
        if pow_:
            dokad = ("sprawdź orzeczenie powiązane (WSA ↔ NSA, pole „Sygn. powiązane”) i jego "
                     "„Treść wyniku”: " + "; ".join(f"{p['opis']} → orzeczenie {p['doc_id']}"
                                                    for p in pow_))
        else:
            dokad = ("CBOSA nie podaje sygnatur powiązanych — sprawdź w CBOSA, czy zapadł wyrok "
                     "NSA ze skargi kasacyjnej w tej sprawie (szukaj --sygnatura / sygnatura)")
        return (f"UWAGA: {kto} jest oznaczone w CBOSA jako ORZECZENIE NIEPRAWOMOCNE — mogło zostać "
                f"uchylone lub zmienione w wyższej instancji i nie musi już obowiązywać. Przed "
                f"cytowaniem {dokad}. Tryb --strict takiej treści nie zwraca.")
    return (f"UWAGA: {kto} — {_opis_braku_oznaczenia(d)} — prawomocność NIEPOTWIERDZONA; "
            f"sprawdź w przeglądarce: {d['url']}. Tryb --strict takiej treści nie zwraca.")


# ── Komendy ──────────────────────────────────────────────────────────────────────────────
DO_OTWARTE = "2099-12-31"  # patrz _formularz: CBOSA nie umie zakresu dat otwartego od góry


def _formularz(a):
    od = _data(getattr(a, "od", None))
    do = _data(getattr(a, "do", None), koniec=True)
    # PUŁAPKA CBOSA: samo `odDaty` (bez `doDaty`) daje ZERO wyników — puste `doDaty` jest czytane
    # jako górna granica sprzed początku zakresu. Odwrotnie (samo `doDaty`) działa poprawnie.
    if od and not do:
        do = DO_OTWARTE
    return {
        "wszystkieSlowa": getattr(a, "fraza", None) or "",
        "wystepowanie": "gdziekolwiek",
        "odmiana": "on",
        "sygnatura": getattr(a, "sygnatura", None) or "",
        "sad": _sad(getattr(a, "sad", None)),
        "rodzaj": _rodzaj(getattr(a, "rodzaj", None)),
        "symbole": getattr(a, "symbol", None) or "",
        "odDaty": od,
        "doDaty": do,
        "sedziowie": getattr(a, "sedzia", None) or "",
        "funkcja": "",
        "submit": "Szukaj",
    }


def _szukaj(form, strona=1):
    """POST wyszukiwania (strona 1, zakłada sesję) + ew. GET kolejnej strony (/cbo/find?p=N)."""
    h = _fetch("/cbo/search", data=form)
    if strona > 1:
        h = _fetch(f"/cbo/find?p={strona}")
    return _wyniki(h)


def _drukuj_liste(total, pozycje, strona):
    glowne = [p for p in pozycje if not p["powiazane"]]
    print(f"Znaleziono: {total if total is not None else '?'}  "
          f"(strona {strona}, po 10 na stronę)\n")
    for p in pozycje:
        if p["powiazane"]:
            print(f"      ↳ powiązane: [{p['doc_id']}]  {p['opis']}")
            continue
        print(f"  [{p['doc_id']}]  {p['opis']}")
        if p["snippet"]:
            print(f"    …{p['snippet'][:220].strip()}…")
    if glowne:
        print(f"\nPełna treść: orzeczenie <doc_id>  (np. orzeczenie {glowne[0]['doc_id']})")
    if total and total > strona * 10:
        print(f"Kolejna strona: --strona {strona + 1}")


def cmd_szukaj(a):
    kryteria = any([a.fraza, a.sygnatura, a.sad, a.rodzaj, a.symbol, a.sedzia, a.od, a.do])
    if not kryteria:
        sys.exit("Podaj kryterium: frazę albo --sad / --sygnatura / --rodzaj / --symbol / --sedzia / zakres dat.")
    strona = max(1, a.strona)
    total, pozycje, komunikat = _szukaj(_formularz(a), strona)
    _sprawdz_transport_strict(a)
    if total is None and not pozycje:
        if komunikat:  # formularz odrzucił zapytanie (np. zła data) — to błąd wejścia, nie serwera
            sys.exit(f"CBOSA odrzuciło zapytanie: {komunikat}\nPopraw parametry i ponów "
                     "(daty w formacie RRRR-MM-DD).")
    if total == 0 or not pozycje:
        sys.exit(_z_uwaga_o_transporcie(
            "Brak wyników (zweryfikowane zero). Uwaga: wyszukiwarka CBOSA wymaga dokładnych "
            "wartości — spróbuj prostszej frazy, bez --sad, albo sprawdź sygnaturę/symbol."))
    if a.json:
        print(json.dumps(_dane_z_transportem(
                         {"total": total, "strona": strona, "komunikat": komunikat,
                          "wyniki": pozycje}),
                         ensure_ascii=False, indent=2)); return
    _drukuj_uwage_o_transporcie()
    _drukuj_liste(total, pozycje, strona)


def cmd_orzeczenie(a):
    doc_id = a.doc_id.strip().upper()
    if not re.fullmatch(r"[A-F0-9]{6,}", doc_id):
        sys.exit(f"Nieprawidłowy doc_id: {a.doc_id!r} (identyfikator ze strony wyników, np. 8889489BE0).")
    d = _orzeczenie(_fetch(f"/doc/{doc_id}"), doc_id)
    _sprawdz_transport_strict(a)
    if not d.get("tytul") and not d.get("metadane"):
        # strona pobrana, ale nierozpoznana (przebudowa HTML? strona błędu z HTTP 200?) —
        # UNKNOWN z podpowiedzią; także --json nie może emitować pozornie poprawnego wyniku
        raise VerificationUnknown(f"nie udało się rozpoznać strony orzeczenia {doc_id} — "
                                  f"sprawdź w przeglądarce: {d['url']}")
    d.setdefault("prawomocne", None)
    d.setdefault("prawomocnosc", None)
    uwaga = _uwaga_o_prawomocnosci(d)
    if uwaga:
        d["uwaga"] = uwaga
    _sprawdz_prawomocnosc_strict(a, d)  # cała weryfikacja PRZED emisją wyniku (także --json)
    if a.json:
        print(json.dumps(_dane_z_transportem(d), ensure_ascii=False, indent=2)); return
    _drukuj_uwage_o_transporcie()
    if uwaga:
        print(uwaga + "\n")
    print(f"# {d.get('tytul', doc_id)}   [{doc_id}]")
    for k in ("Data orzeczenia", "Sąd", "Sędziowie", "Symbol z opisem", "Hasła tematyczne",
              "Skarżony organ", "Treść wyniku", "Powołane przepisy", "Sygn. powiązane"):
        v = d["metadane"].get(k)
        if v:
            v1 = re.sub(r"\s*\n\s*", " | ", v.strip())
            print(f"  {k}: {v1[:500]}")
        if k == "Data orzeczenia":
            print(f"  Prawomocność: {_etykieta_prawomocnosci(d)}")
    print(f"  Źródło: {d['url']}")
    for p in d.get("powiazane", []):
        print(f"  ↳ powiązane: [{p['doc_id']}]  {p['opis']}")
    if d.get("tezy"):
        print(f"\n## Tezy\n{d['tezy']}")
    if d.get("sentencja"):
        print(f"\n## Sentencja\n{d['sentencja']}")
    txt = d.get("uzasadnienie") or ""
    print(f"\n## Uzasadnienie ({len(txt)} znaków)")
    if not txt:
        print("(brak opublikowanego uzasadnienia — zob. stronę źródłową wyżej)")
        return
    if a.fragment:
        spans = _fragmenty(txt, a.fragment)
        if not spans:
            sys.exit(f"Nie znaleziono frazy {a.fragment!r} w uzasadnieniu ({len(txt)} znaków). Spróbuj inną frazą.")
        for i, (s, e) in enumerate(spans):
            if i:
                print("\n[...]\n")
            # „…" na brzegu = w tym miejscu okno ucina tekst (brzeg dopasowany do granicy
            # zdania/wyrazu, więc wycinek nie zaczyna się ani nie kończy w środku słowa)
            print(("…" if s > 0 else "") + txt[s:e].strip() + (" …" if e < len(txt) else ""))
        print(f"\n(okna: {len(spans)} — „…” oznacza ucięcie; pominięto resztę; pełna treść: bez --fragment)")
        return
    if len(txt) > 40000:
        print(f"(UWAGA: długie uzasadnienie {len(txt)} znaków — do wycinka użyj --fragment \"<fraza>\")\n")
    print(txt)


def cmd_sygnatura(a):
    sig = " ".join(a.sygnatura).strip()
    form = {"wszystkieSlowa": "", "wystepowanie": "gdziekolwiek", "odmiana": "on",
            "sygnatura": sig, "sad": "dowolny", "rodzaj": "dowolny", "symbole": "",
            "odDaty": "", "doDaty": "", "sedziowie": "", "funkcja": "", "submit": "Szukaj"}
    total, pozycje, komunikat = _szukaj(form)
    _sprawdz_transport_strict(a)
    if total is None and not pozycje:
        if komunikat:
            sys.exit(f"CBOSA odrzuciło zapytanie: {komunikat}")
    glowne = [p for p in pozycje if not p["powiazane"]]
    if not glowne:
        sys.exit(_z_uwaga_o_transporcie(
            f"Nie znaleziono orzeczenia o sygnaturze {sig!r} w CBOSA.\n"
            "Sygnatury sądów administracyjnych mają formę np. \"II FSK 2870/18\" (NSA) "
            "albo \"I SA/Bk 226/18\" (WSA). SN/TK/sądy powszechne/KIO → skill prawo-pl-saos."))
    if a.json:
        print(json.dumps(_dane_z_transportem(
                         {"total": total, "komunikat": komunikat, "wyniki": pozycje}),
                         ensure_ascii=False, indent=2)); return
    _drukuj_uwage_o_transporcie()
    print(f"Sygnatura {sig!r}: dopasowań {total}\n")
    for p in pozycje:
        prefiks = "  ↳ powiązane: " if p["powiazane"] else "  "
        print(f"{prefiks}[{p['doc_id']}]  {p['opis']}")
    print(f"\nPełna treść: orzeczenie {glowne[0]['doc_id']}")


def main():
    global _transport_tls_verified
    _transport_tls_verified = True
    ap = argparse.ArgumentParser(
        description="CBOSA (read-only, scraping — brak oficjalnego API). "
                    "Orzecznictwo sądów administracyjnych: NSA + 16 WSA.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--json", action="store_true", help="zrzut sparsowanych danych jako JSON")
    ap.add_argument("--strict", action="store_true",
                    help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności "
                         "(TLS, rozpoznana strona, prawomocność orzeczenia)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("szukaj")
    s.add_argument("fraza", nargs="?", default=None)
    s.add_argument("--sad", help='NSA | "WSA <miasto>" (np. "WSA Warszawa") | pełna nazwa z CBOSA')
    s.add_argument("--sygnatura", help='sygnatura sprawy, np. "II FSK 2870/18"')
    s.add_argument("--rodzaj", help="wyrok | postanowienie | uchwala")
    s.add_argument("--symbol", help="symbol sprawy, np. 6119 (podatki), 6320 (pomoc społeczna)")
    s.add_argument("--sedzia", help="nazwisko sędziego")
    s.add_argument("--od", help="data orzeczenia od (RRRR-MM-DD)")
    s.add_argument("--do", help="data orzeczenia do (RRRR-MM-DD)")
    s.add_argument("--strona", type=int, default=1, help="numer strony wyników (od 1, po 10 wyników)")
    s.set_defaults(func=cmd_szukaj)

    o = sub.add_parser("orzeczenie")
    o.add_argument("doc_id", help="identyfikator ze strony wyników, np. 8889489BE0")
    o.add_argument("--fragment", help='wytnij okna wokół frazy w uzasadnieniu (np. "interpretacja")')
    o.set_defaults(func=cmd_orzeczenie)

    sy = sub.add_parser("sygnatura")
    sy.add_argument("sygnatura", nargs="+")
    sy.set_defaults(func=cmd_sygnatura)

    # Flagi globalne działają też PO komendzie (modele piszą je właśnie tam); SUPPRESS sprawia,
    # że brak flagi w subparserze nie kasuje wartości podanej przed komendą
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="zrzut sparsowanych danych jako JSON")
        p.add_argument("--strict", action="store_true", default=argparse.SUPPRESS,
                       help="zakończ błędem, gdy nie udało się zweryfikować aktualności lub kompletności "
                         "(TLS, rozpoznana strona, prawomocność orzeczenia)")

    a = ap.parse_args()
    try:
        a.func(a)
    except VerificationUnknown as e:
        sys.exit(f"BŁĄD: nie udało się zweryfikować danych w CBOSA ({e}). "
                 "Spróbuj ponownie za chwilę.")


if __name__ == "__main__":
    main()
