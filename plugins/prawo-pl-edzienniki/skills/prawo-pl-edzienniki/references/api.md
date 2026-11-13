# API ELI wojewódzkich dzienników urzędowych — referencja

Każde z 16 województw prowadzi własny **e-Dziennik** (oprogramowanie ABC PRO) z API zgodnym z ELI —
ten sam wzorzec co `api.sejm.gov.pl/eli`, ale **na osobnym hoście** i z okrojonym zestawem endpointów.
Prefiks ścieżki: **`/api/eli`**. Bez klucza, wszystko GET (read-only). Zakres: akty prawa miejscowego
(uchwały rad gmin/powiatów/sejmików, rozporządzenia wojewody, zarządzenia, obwieszczenia, wyroki WSA
dot. aktów miejscowych). Obsługa: `scripts/edzienniki.py`.

## Hosty (zweryfikowane 2026-07)

| Kod | Województwo | Host | Publisher |
|---|---|---|---|
| DS | dolnośląskie | edzienniki.duw.pl | POL_WOJ_DS |
| KP | kujawsko-pomorskie | edzienniki.bydgoszcz.uw.gov.pl | POL_WOJ_KP |
| LB | lubelskie | edziennik.lublin.uw.gov.pl | POL_WOJ_LB |
| LS | lubuskie | dzienniki.luw.pl | POL_WOJ_LS |
| LD | łódzkie | dziennik.lodzkie.eu | POL_WOJ_LD |
| MP | małopolskie | edziennik.malopolska.uw.gov.pl | POL_WOJ_MP |
| MZ | mazowieckie | edziennik.mazowieckie.pl | POL_WOJ_MZ |
| OP | opolskie | duwo.opole.uw.gov.pl | POL_WOJ_OP |
| PK | podkarpackie | edziennik.rzeszow.uw.gov.pl | POL_WOJ_PK |
| PL | podlaskie | edziennik.bialystok.uw.gov.pl | POL_WOJ_PL |
| PM | pomorskie | edziennik.gdansk.uw.gov.pl | POL_WOJ_PM |
| SL | śląskie | dzienniki.slask.eu | POL_WOJ_SL |
| SK | świętokrzyskie | edziennik.kielce.uw.gov.pl | POL_WOJ_SK |
| WM | warmińsko-mazurskie | edzienniki.olsztyn.uw.gov.pl | POL_WOJ_WM |
| WP | wielkopolskie | edziennik.poznan.uw.gov.pl | POL_WOJ_WP |
| ZP | zachodniopomorskie | e-dziennik.szczecin.uw.gov.pl | POL_WOJ_ZP |

## Endpointy (identyczne na każdym hoście)

| Ścieżka | Zwraca |
|---|---|
| `/api/eli/acts` | lista publisherów hosta: `[{code, shortName, name, years[], deedsCount}]` |
| `/api/eli/acts/{publisher}/{rok}?limit=&offset=` | rocznik: `{items[], offset, count, totalCount}` |
| `/api/eli/acts/{publisher}/{rok}/{poz}` | metadane aktu (pola niżej) |
| `/api/eli/acts/{publisher}/{rok}/{poz}/text.pdf` | urzędowy PDF (pełny tekst — jedyne wiarygodne źródło treści) |
| `/api/eli/acts/{publisher}/{rok}/{poz}/text.html` | tekst HTML — **zwykle tylko 1. strona PDF** (patrz pułapka 9) |
| `/api/legalact?year={rok}&journal=0&position={poz}` | **rejestr dziennika** (backend UI, poza `/api/eli`): `ActDate`, `PublicationDate`, `ActStatus{IsInvalid, IsPartialInvalid, Description}`, `ActRelations[{RelationType, Description, LegalActsRelated[{Year, Position, LegalActType, ActDate, CaseNumber, Description}]}]` — powiązania: `Uchyla`, `JestSprostowaniemDla`/„Ma sprostowanie", `FullDecision`/„Ma rozstrzygnięcie nadzorcze (nieważność w całości)", `PartialDecision`/„… (nieważność w części)", `Zmienia`/„Jest zmieniany przez"; także `Title`, `IsTechnicalPosition`, `BindingDateFrom` (SL: „01.01.2026"). Używany przez `akt` (best-effort) i `tekst` (z `--strict`: brak = blokada). |

Pola aktu: `address` (`POL_WOJ_DS202613299`→ w silniku rok/poz), `publisher year volume pos type
title displayAddress promulgation announcementDate textPDF textHTML changeDate entryIntoForce
validFrom repealDate expirationDate legalStatusDate inForce releasedBy[] keywords[] status`.

### Semantyka dat — RÓŻNA między endpointami (zweryfikowane 2026-08 na DS, PM, PL, MP)

| Endpoint | `announcementDate` | `promulgation` |
|---|---|---|
| `/acts/{pub}/{rok}/{poz}` (rekord aktu) | **data AKTU** („z dnia …", godzina 00:00) | **data OGŁOSZENIA** w dzienniku (ze znacznikiem czasu) — jak w ELI Sejmu |
| `/acts/{pub}/{rok}` (lista rocznika) | **data OGŁOSZENIA** (znacznik czasu) | **data AKTU** |

Przykład DS 2026/3654: rekord → `announcementDate 2026-08-11`, `promulgation 2026-08-13T10:46`;
lista → `promulgation 2026-08-11`, `announcementDate 2026-08-13T10:46`; rejestr → `ActDate 2026-08-11`,
`PublicationDate 2026-08-13T10:46`; nagłówek PDF „Wrocław, dnia 13 sierpnia 2026 r.". Silnik (`_daty`)
mapuje pola wg endpointu i pilnuje, by ogłoszenie nie poprzedzało daty aktu. Vacatio legis (14 dni)
liczy się od daty OGŁOSZENIA — `akt` pokazuje obie daty osobno („Data aktu" / „Ogłoszony").

## Różnice vs API ELI Sejmu — PUŁAPKI (zweryfikowane na żywo)

1. **BRAK `/acts/search`** — segment `search` trafia w trasę `{publisher}` i zwraca (HTTP 200!)
   generyczny obiekt `code: "WDU_D"`. Wyszukiwanie = pobranie rocznika + filtr lokalny.
2. **Serwerowe filtry są IGNOROWANE** — parametry `title`, `type`, `keyword` na listingu rocznika
   nie zawężają wyników (zwracają pełny rocznik). `edzienniki.py` pobiera cały rocznik jednym
   żądaniem **bez parametru `limit`** (serwer zwraca wtedy pełny rocznik, ~3–7 tys. aktów) i filtruje
   tytuły lokalnie (podłańcuch, bez rozróżniania diakrytyków).
   **NIE używaj `?limit=100000`** — backend ABC PRO serwuje dla tej wartości NIEAKTUALNĄ kopię listy
   przy świeżym `totalCount` (2026-08-22: PM 3149 z 3330 pozycji, brak aktów z ostatnich 3 tygodni,
   unieważniony MPZP nadal „obowiązujący"; DS 3709/3739; PL 3173/3271). `?limit=5000`, `?limit=99999`
   i brak limitu zwracają komplet. Silnik porównuje `len(items)` z `totalCount`, ponawia z innym
   limitem, a gdy lista nadal jest krótsza — ostrzega (domyślnie) albo blokuje (`--strict`).
   Ponowny pomiar 2026-11-13: kopia pod `limit=100000` jest odświeżana (PM: 3149 pozycji w sierpniu,
   4369 teraz), ale z opóźnieniem kilku dni — nie jest ani trwała, ani aktualna. PM: 4369 z 4492 (kopia
   kończy się na pozycji ogłoszonej 9.11 o 15:53, brak 123 ogłoszonych 10–12.11; 8 nieaktualnych
   statusów, np. 2026/3964 „obowiązujący” zamiast „stwierdzono nieważność aktu”), PL: 4473 z 4501 (koniec
   na 10.11; 2026/3937 „obowiązujący” zamiast „akt uchylony”), KP: komplet. Lista bez
   limitu była na wszystkich trzech hostach pełna (`len(items) = totalCount`, najwyższa pozycja = najnowsza:
   rejestr nie zna pozycji max+1…max+3). Silnik `limit=100000` nie używa.
3. **Nieznany publisher → HTTP 200** z obiektem domyślnym (nigdy 404) — silnik sprawdza `code`.
4. **4 hosty zwracają klucze PascalCase** (`Items/Title/TotalCount` — starsze wdrożenia: KP, LS,
   LD, PL) — silnik normalizuje klucze do lowercase.
5. **Daty** ISO z godziną (`2026-07-10T00:00:00`); wartownik `0001-01-01T00:00:00` = brak danych.
   Pola `inForce`/`entryIntoForce` bywają **niewypełnione** (inForce=0 przy status „obowiązujący")
   — nie wnioskuj z nich o obowiązywaniu; miarodajny jest `status` + treść aktu.
6. **WAF na duwo.opole.uw.gov.pl** — odrzuca (403) gołe klienty HTTP; silnik wysyła nagłówki
   jak przeglądarka + `Accept: application/json`.
7. **edziennik.mazowieckie.pl** — za filtrem antybotowym Akamai, który żądania klientów
   nieprzeglądarkowych przetrzymuje bez odpowiedzi (TCP i TLS wstają, HTTP milczy → timeout) — **także
   z polskiego IP**, więc to NIE jest blokada geograficzna (2026-10-05: przeglądarka 200, curl/urllib
   z domyślnymi nagłówkami timeout, z polskiego IP). Zweryfikowane reguły: żądanie bez
   `Accept-Language` albo z adresem URL w `User-Agent` („+https://…") jest przetrzymywane; curl
   z domyślnym UA dostaje po HTTP/2 `INTERNAL_ERROR`. Silnik wysyła `User-Agent` z samą nazwą/wersją
   + `Accept` + `Accept-Language: pl-PL,pl;q=0.9` i przechodzi (urllib, HTTP/1.1). Jeśli filtr zmieni
   reguły, komunikat silnika mówi o filtrze (nie o geoblokadzie); obejście: UI w przeglądarce
   i ręczne pobranie PDF — https://edziennik.mazowieckie.pl/
   2026-11-13 host MZ odpowiadał HTTP 503 (wcześniej 504), a curl także na stronę główną kończył się
   `HTTP/2 INTERNAL_ERROR` — awaria serwera. Każdy błąd 5xx po ponowieniu silnik opisuje jako awarię
   po stronie serwera dziennika (nie brak aktu), z radą ponowienia i linkiem do UI; kod wyjścia 2.
8. Metadane ELI są uboższe niż w Sejmowym ELI: brak `references` (nowelizacje), `texts`, słowników.
   Powiązania (sprostowania, uchylenia, rozstrzygnięcia nadzorcze, częściowa nieważność) daje za to
   **rejestr dziennika** `GET /api/legalact?year=&journal=0&position=` (ten sam host, bez klucza,
   działa też na starszych wdrożeniach) — `akt` pobiera go best-effort i drukuje „Powiązania:".
   Przykład MP 2025/7877 → `JestSprostowaniemDla` → DZ. URZ. WOJ. 2026.446 (obwieszczenie o sprostowaniu
   stawki 4,63 zł); PM 2026/3104 → `FullDecision` → 2026.3258 (rozstrzygnięcie nadzorcze, nieważność).
   **Kierunek relacji wynika z `Description`, nie z `RelationType`** (ten sam typ po obu stronach: DS
   2026/584 „Ma rozstrzygnięcie nadzorcze (nieważność w części)" ↔ DS 2026/1099 „Jest rozstrzygnięciem
   nadzorczym dla …"; „Uchyla" ↔ „Uchylany przez"; „Zmienia" ↔ „Jest zmieniany przez").
   **`ActStatus.IsInvalid` NIE znaczy „nieważny"** — bywa `true` dla „Akt uchylony" (DS 2018/5364);
   rozstrzyga `ActStatus.Description` i opisy relacji. Relacja NIE podaje tytułu aktu powiązanego —
   a to tytuł rozstrzygnięcia mówi, którą jednostkę unieważniono („stwierdzające nieważność § 6 ust. 4
   we fragmencie …"; uchwały RIO bywają ogólne: WP 2025/9932 „stwierdzające częściową nieważność" —
   § 2 dopiero w treści). Silnik dociąga tytuł z `/api/legalact` aktu powiązanego (≤ 5 zapytań, tylko
   nieważność/sprostowania). `tekst` pobiera rejestr przed treścią i ostrzega/blokuje (patrz SKILL.md).
9. **`text.html` = zwykle tylko PIERWSZA STRONA PDF** (DS, PM, PL, MP — zweryfikowane 2026-08: MP 2025/7877
   kończy się w pół § 1, bez stawki za budowle i § 2–4; DS 2026/3654 bez 5-stronicowego statutu).
   Na hoście **podlaskim** (PL) `text.html` jest dodatkowo uszkodzony (80+ znaków U+FFFD, brak „§",
   zlepione/pominięte wyrazy). Metadane nie podają liczby stron. Jedyne wiarygodne źródło treści to
   `text.pdf` — silnik czyta go przez `pdftotext -layout` (poppler; brak na PATH → `text.html`
   z głośnym ostrzeżeniem, `--strict` blokuje), usuwa nagłówek dziennika („DZIENNIK URZĘDOWY … Poz. N"
   + kolumnę e-podpisu), nagłówki kolejnych stron („Dziennik Urzędowy Województwa … – N – Poz. X"),
   stopki Legislatora („Id: …. Podpisany", „Strona N") i scala zawinięte linie, tak by jednostki
   („§ 1.", „1)", „a)") zaczynały linię. Wykrywa U+FFFD i brak oznaczeń jednostek.
10. **Niepełny łańcuch TLS** — `edziennik.malopolska.uw.gov.pl` (MP), `dzienniki.luw.pl` (LS)
    i `dziennik.lodzkie.eu` (LD) wysyłają sam certyfikat liścia bez pośredniego (Certum DV TLS G2 R39,
    home.pl DV TLS G2 R35). Dla LS i LD sprawdzone 2026-11-13 (Python 3.15 / OpenSSL 4.1, bez
    `SSL_CERT_FILE`): zwykły kontekst → kod 20 „unable to get local issuer certificate”; po dociągnięciu
    pośredniego z AIA połączenie przechodzi z `CERT_REQUIRED`, `check_hostname` i bez PARTIAL_CHAIN, a `akt`
    i `tekst` działają bez ręcznego pakietu CA. Systemowy Python 3.9 macOS (LibreSSL 2.8.3) łączy się z LS
    i LD już zwykłym kontekstem (bez AIA; przyczyny nie ustalano). Przeglądarki/curl dociągają pośredni przez AIA; urllib kończy
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. Silnik robi to samo biblioteką
    standardową: czyta liść (połączenie bez weryfikacji służy WYŁĄCZNIE do odczytu certyfikatu), wyciąga
    z DER adres „CA Issuers" (OID 1.3.6.1.5.5.7.48.2), pobiera pośredni, dokłada do domyślnych CA
    (`load_verify_locations(cadata=…)`) i ponawia z PEŁNĄ weryfikacją. Treść nigdy nie jest pobierana
    bez weryfikacji; gdy dociągnięcie zawiedzie, komunikat mówi o niepełnym łańcuchu (nie o geoblokadzie).
    Pobrany pośredni nie może być kotwicą zaufania: silnik zdejmuje flagę `X509_V_FLAG_PARTIAL_CHAIN`
    (0x80000 w OpenSSL i LibreSSL), którą `ssl.create_default_context()` włącza domyślnie od Pythona 3.13 —
    na 3.8/3.9 (bez stałej `ssl.VERIFY_X509_PARTIAL_CHAIN`) przez wartość liczbową.
    AIA próbowane jest TYLKO przy braku pośredniego (kod OpenSSL 20/21/2). **Wygasły certyfikat**
    (kod 10 — `edziennik.gdansk.uw.gov.pl` (PM): notAfter 2026-10-01 11:05:26 UTC; odnowiony —
    2026-11-13 ważny do 2027-04-16) silnik rozpoznaje
    osobno i podaje datę ważności odczytaną z certyfikatu — to błąd serwera, którego klient nie naprawi
    (SSL_CERT_FILE nic nie da); weryfikacji TLS silnik nie wyłącza. Inne błędy weryfikacji — komunikat
    z powodem OpenSSL.
12. **Pozycje techniczne** (host śląski, `IsTechnicalPosition: true` w rejestrze — „Z przyczyn
    technicznych pod tym numerem pozycji nie został opublikowany żaden akt prawny"): lista rocznika
    zawiera je jako puste wpisy (`pos 0`, `title null`, liczone w `totalCount` — SL 2026: 25 z 5949),
    a rekord ELI przekierowuje (302) na względne `404_notfound` (urllib → HTTP 400), `text.pdf` → 406.
    Silnik pomija je na liście (z licznikiem), traktuje przekierowanie jako 404 i w `akt`/`tekst`
    podaje komunikat o pozycji technicznej.
13. **`displayAddress`** („DZ. URZ. WOJ. 2026.584") zwykle NIE zawiera województwa, a gdy zawiera —
    skrót jest niejednolity (LB „LUB.", LS „LUB", SL „SLA"). Urzędowa forma cytatu = `ShortName` z
    `/api/eli/acts` („Dz. Urz. Woj. Dolnośląskiego") + „z {rok} r. poz. {poz}" — silnik ją wylicza
    (`akt`: „Cytat:"). `entryIntoForce`/`validFrom` = wartownik 0001-01-01 na wszystkich hostach
    (2026-10-05); `BindingDateFrom` rejestru bywa wypełniony (SL 2026/100: „01.01.2026").
14. **Nagłówek dziennika w PDF**: miejscowość bywa wielowyrazowa („Gorzów Wielkopolski, dnia …"),
    a host WM wstawia dzień tygodnia („Olsztyn, dnia czwartek, 8 stycznia 2026 r.") — silnik
    obsługuje oba warianty (dzień tygodnia pomija).
11. **Indeks górny w `text.html`** bywa osobnym akapitem z samą cyfrą przed linią z „m" („2" / „… od 1 m
    powierzchni") — silnik scala to do „m²"; `<sup>` renderuje w linii. W tekście z PDF silnik podaje
    jednostkę tak, jak zapisano ją w warstwie tekstowej PDF: „m²” (SK, WM; PL 7× „m²” i 1× „m2” w tym
    samym akcie), „m2” (KP, LB, LD, LS, MP, OP, PK, PM, SL, ZP) albo „m 2” (WP) — 14 uchwał podatkowych
    z 14 hostów, 2026-11-13, liczba wystąpień identyczna z `pdftotext` bez `-layout`; cyfra nie trafia do
    osobnej linii ani do sąsiedniej kwoty.
15. **Stawki w prawej kolumnie** (lista „opis | stawka”, np. SK 2026/3601): `pdftotext -layout` stawia
    kwotę na wysokości środka wiersza, często między liniami opisu litery. Silnik przenosi taką kwotę
    na koniec akapitu jej litery („a) … od 1 m² powierzchni  1,20 zł”); wiersze prawdziwych tabel
    (kilka komórek albo wiersz pod nagłówkiem tabeli) zostawia bez zmian. Pusta komórka tabeli znika bez
    śladu („15  595,00  1348,00” przy pustej kolumnie „Mniej niż”, PM 2026/4486) — przy cytowaniu tabeli
    porównaj wiersz z PDF.
16. **Nieistniejąca pozycja**: większość hostów → HTTP 404; KP → HTTP 204 bez treści (rejestr: „Nie
    znaleziono pozycji”), np. luka w numeracji KP 2026/3013. `akt` traktuje oba jako „nie znaleziono aktu”
    (kod 2); pusta odpowiedź nie jest mylona z „nieoczekiwaną odpowiedzią”.
17. **Kody województw** to sufiksy publishera ELI, nie dawne kody ISO 3166-2: `LB` = lubelskie
    (POL_WOJ_LB), a dawny ISO PL-LB = lubuskie (tu `LS`). Silnik przy `LB` przypomina o tym na stderr,
    a dla ISO `LU`, `MA`, `PD`, `WN` podaje właściwy kod w komunikacie błędu.
18. **Host ZP** (`e-dziennik.szczecin.uw.gov.pl`) — w sierpniu 2026 nieosiągalny po TCP; 2026-11-13
    działa (DNS 80.51.129.231, TCP 443, TLS przechodzi zwykłym kontekstem bez AIA, lista rocznika 2026,
    `akt`, `tekst`). Gdy host nie odpowiada, silnik podaje ogólny komunikat o błędzie sieci z adresem UI.
19. **Strony bez warstwy tekstowej** — plan miejscowy WP 2026/8451 (16 str.): strony 9–15 to same obrazy
    (rysunek planu i zeskanowany załącznik tekstowy — rozstrzygnięcie o sposobie rozpatrzenia uwag);
    `pdftotext` zwraca z nich tylko nagłówek strony. `tekst` wymienia takie strony w `UWAGA:` i w JSON
    (`strony_bez_tekstu`); brak frazy przy takich stronach = kod 2. Pozostałe 26 PDF-ów próby z 13.11.2026:
    bez takich stron (obraz na 1. stronie to godło, tekst jest).

## Mapowanie komend `edzienniki.py`

`dzienniki [--woj W]`→`/acts`; `szukaj --woj W [FRAZA] [--rok R]`→`/acts/{pub}/{rok}` (bez limitu;
przy `len(items) < totalCount` ponowienie z `limit=totalCount+500`) + filtr lokalny (bez `--rok`: do
3 najnowszych roczników — nagłówek podaje faktycznie przeszukane); `--limit`/`--strona` stronicują
listę PRZEFILTROWANYCH trafień (strony 1..N pokrywają wszystkie policzone trafienia, stopka
podaje zakres i N); z `--strict` status wyświetlanych wierszy (≤ 20) z `/acts/{pub}/{r}/{p}`;
`akt W R P`→`/acts/{pub}/{r}/{p}` + `/api/legalact?year=R&journal=0&position=P` (powiązania, best-effort)
+ `/api/legalact` aktów powiązanych (tytuły rozstrzygnięć/sprostowań, ≤ 5);
`tekst W R P [--fragment F] [--pdf PLIK]`→`/api/legalact` (powiązania, pozycja techniczna) + `…/text.pdf`
+ `pdftotext -layout` (tekst; bez pdftotext:
`…/text.html` z ostrzeżeniem) / `…/text.pdf` (plik — `--pdf` nie nadpisuje istniejącego, tryb „xb”). `--fragment "§ N"`/`"art. N"` = cała jednostka
do następnej (nagłówek jednostki w JEDNEJ linii: samotny „§" kolumny tabeli + wiersz „1. 2. 3. 4." to
nie § 1; trafienie po linii „Załącznik…" oznaczone `[w załączniku: …]`); inna fraza = okno ~600 znaków
rozszerzone do granic akapitu.

## Wskazówki

- Centralny portal `eli.gov.pl` agreguje dzienniki (w tym Dz.U./M.P.), ale maszynowy dostęp
  pozostaje per-host — stąd tabela wyżej. Dokument wdrożeniowy: `api.sejm.gov.pl/implementing_eli_pl.html`.
- Do dosłownego cytatu używaj tekstu z PDF (`tekst` z `pdftotext`, nagłówek „tekst z urzędowego PDF")
  albo samego PDF (`tekst … --pdf`), jak przy Dz.U. — nigdy z `text.html` (1. strona, bywa uszkodzony).
- Prawo krajowe (ustawy, rozporządzenia) — **zawsze** skill **prawo-pl-eli** (Dz.U./M.P.),
  nie dzienniki wojewódzkie; tu jest wyłącznie prawo miejscowe.

## Kody wyjścia `edzienniki.py`

`0` = jest wynik; `1` = ZWERYFIKOWANY brak wyników (wszystkie żądane roczniki pobrane w całości i zero
trafień; fraza `--fragment` nieobecna w pełnym, nieuszkodzonym tekście z PDF); `2` = wszystko inne:
błąd sieci/HTTP (w tym 5xx — awaria serwera dziennika)/TLS, nieoczekiwana odpowiedź, zły argument,
nieistniejąca lub techniczna pozycja, blokada `--strict`, niepełny albo pominięty rocznik, brak frazy
w `text.html` (1. strona) lub w tekście z U+FFFD, nieprzewidziany wyjątek. Komunikat idzie na stderr.
