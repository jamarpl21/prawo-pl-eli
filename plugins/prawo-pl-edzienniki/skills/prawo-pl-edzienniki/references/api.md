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
| `/api/legalact?year={rok}&journal=0&position={poz}` | **rejestr dziennika** (backend UI, poza `/api/eli`): `ActDate`, `PublicationDate`, `ActStatus{IsInvalid, IsPartialInvalid, Description}`, `ActRelations[{RelationType, Description, LegalActsRelated[{Year, Position, LegalActType, ActDate, CaseNumber, Description}]}]` — powiązania: `Uchyla`, `JestSprostowaniemDla`/„Ma sprostowanie", `FullDecision`/„Ma rozstrzygnięcie nadzorcze (nieważność w całości)", częściowa nieważność. Używany przez `akt` (best-effort). |

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
3. **Nieznany publisher → HTTP 200** z obiektem domyślnym (nigdy 404) — silnik sprawdza `code`.
4. **4 hosty zwracają klucze PascalCase** (`Items/Title/TotalCount` — starsze wdrożenia: KP, LS,
   LD, PL) — silnik normalizuje klucze do lowercase.
5. **Daty** ISO z godziną (`2026-07-10T00:00:00`); wartownik `0001-01-01T00:00:00` = brak danych.
   Pola `inForce`/`entryIntoForce` bywają **niewypełnione** (inForce=0 przy status „obowiązujący")
   — nie wnioskuj z nich o obowiązywaniu; miarodajny jest `status` + treść aktu.
6. **WAF na duwo.opole.uw.gov.pl** — odrzuca (403) gołe klienty HTTP; silnik wysyła nagłówki
   jak przeglądarka + `Accept: application/json`.
7. **edziennik.mazowieckie.pl** — za CDN (Akamai); bywa nieosiągalny spoza Polski (TCP wstaje,
   HTTP nie odpowiada). Silnik ma krótki timeout i czytelny komunikat; UI: https://edziennik.mazowieckie.pl/
8. Metadane ELI są uboższe niż w Sejmowym ELI: brak `references` (nowelizacje), `texts`, słowników.
   Powiązania (sprostowania, uchylenia, rozstrzygnięcia nadzorcze, częściowa nieważność) daje za to
   **rejestr dziennika** `GET /api/legalact?year=&journal=0&position=` (ten sam host, bez klucza,
   działa też na starszych wdrożeniach) — `akt` pobiera go best-effort i drukuje „Powiązania:".
   Przykład MP 2025/7877 → `JestSprostowaniemDla` → DZ. URZ. WOJ. 2026.446 (obwieszczenie o sprostowaniu
   stawki 4,63 zł); PM 2026/3104 → `FullDecision` → 2026.3258 (rozstrzygnięcie nadzorcze, nieważność).
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
    home.pl DV TLS G2 R35). Przeglądarki/curl dociągają pośredni przez AIA; urllib kończy
    `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`. Silnik robi to samo biblioteką
    standardową: czyta liść (połączenie bez weryfikacji służy WYŁĄCZNIE do odczytu certyfikatu), wyciąga
    z DER adres „CA Issuers" (OID 1.3.6.1.5.5.7.48.2), pobiera pośredni, dokłada do domyślnych CA
    (`load_verify_locations(cadata=…)`) i ponawia z PEŁNĄ weryfikacją. Treść nigdy nie jest pobierana
    bez weryfikacji; gdy dociągnięcie zawiedzie, komunikat mówi o niepełnym łańcuchu (nie o geoblokadzie).
11. **Indeks górny w `text.html`** bywa osobnym akapitem z samą cyfrą przed linią z „m" („2" / „… od 1 m
    powierzchni") — silnik scala to do „m²"; `<sup>` renderuje w linii.

## Mapowanie komend `edzienniki.py`

`dzienniki [--woj W]`→`/acts`; `szukaj --woj W [FRAZA] [--rok R]`→`/acts/{pub}/{rok}` (bez limitu;
przy `len(items) < totalCount` ponowienie z `limit=totalCount+500`) + filtr lokalny (bez `--rok`: do
3 najnowszych roczników — nagłówek podaje faktycznie przeszukane); `--limit`/`--strona` stronicują
listę PRZEFILTROWANYCH trafień (strony 1..N pokrywają wszystkie policzone trafienia, stopka
podaje zakres i N); z `--strict` status wyświetlanych wierszy (≤ 20) z `/acts/{pub}/{r}/{p}`;
`akt W R P`→`/acts/{pub}/{r}/{p}` + `/api/legalact?year=R&journal=0&position=P` (powiązania, best-effort);
`tekst W R P [--fragment F] [--pdf PLIK]`→`…/text.pdf` + `pdftotext -layout` (tekst; bez pdftotext:
`…/text.html` z ostrzeżeniem) / `…/text.pdf` (plik). `--fragment "§ N"`/`"art. N"` = cała jednostka
do następnej; inna fraza = okno ~600 znaków rozszerzone do granic akapitu.

## Wskazówki

- Centralny portal `eli.gov.pl` agreguje dzienniki (w tym Dz.U./M.P.), ale maszynowy dostęp
  pozostaje per-host — stąd tabela wyżej. Dokument wdrożeniowy: `api.sejm.gov.pl/implementing_eli_pl.html`.
- Do dosłownego cytatu używaj tekstu z PDF (`tekst` z `pdftotext`, nagłówek „tekst z urzędowego PDF")
  albo samego PDF (`tekst … --pdf`), jak przy Dz.U. — nigdy z `text.html` (1. strona, bywa uszkodzony).
- Prawo krajowe (ustawy, rozporządzenia) — **zawsze** skill **prawo-pl-eli** (Dz.U./M.P.),
  nie dzienniki wojewódzkie; tu jest wyłącznie prawo miejscowe.
