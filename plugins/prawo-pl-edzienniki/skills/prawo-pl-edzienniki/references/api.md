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
| `/api/eli/acts/{publisher}/{rok}/{poz}/text.pdf` | urzędowy PDF |
| `/api/eli/acts/{publisher}/{rok}/{poz}/text.html` | tekst HTML |

Pola aktu: `address` (`POL_WOJ_DS202613299`→ w silniku rok/poz), `publisher year volume pos type
title displayAddress promulgation announcementDate textPDF textHTML changeDate entryIntoForce
validFrom repealDate expirationDate legalStatusDate inForce releasedBy[] keywords[] status`.

## Różnice vs API ELI Sejmu — PUŁAPKI (zweryfikowane na żywo)

1. **BRAK `/acts/search`** — segment `search` trafia w trasę `{publisher}` i zwraca (HTTP 200!)
   generyczny obiekt `code: "WDU_D"`. Wyszukiwanie = pobranie rocznika + filtr lokalny.
2. **Serwerowe filtry są IGNOROWANE** — parametry `title`, `type`, `keyword` na listingu rocznika
   nie zawężają wyników (zwracają pełny rocznik). `edzienniki.py` pobiera cały rocznik jednym
   żądaniem (serwer nie ogranicza `limit` — rocznik ~3–6 tys. aktów) i filtruje tytuły lokalnie
   (podłańcuch, bez rozróżniania diakrytyków).
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
8. Metadane są uboższe niż w Sejmowym ELI: brak `references` (nowelizacje), `texts`, słowników —
   powiązania między aktami miejscowymi trzeba ustalać z treści.

## Mapowanie komend `edzienniki.py`

`dzienniki [--woj W]`→`/acts`; `szukaj --woj W [FRAZA] [--rok R]`→`/acts/{pub}/{rok}?limit=100000`
+ filtr lokalny (bez `--rok`: do 3 najnowszych roczników); `akt W R P`→`/acts/{pub}/{r}/{p}`;
`tekst W R P [--fragment F] [--pdf PLIK]`→`…/text.html` (tekst) / `…/text.pdf` (plik).

## Wskazówki

- Centralny portal `eli.gov.pl` agreguje dzienniki (w tym Dz.U./M.P.), ale maszynowy dostęp
  pozostaje per-host — stąd tabela wyżej. Dokument wdrożeniowy: `api.sejm.gov.pl/implementing_eli_pl.html`.
- Do dosłownego cytatu używaj urzędowego PDF (`tekst … --pdf`), jak przy Dz.U.
- Prawo krajowe (ustawy, rozporządzenia) — **zawsze** skill **prawo-pl-eli** (Dz.U./M.P.),
  nie dzienniki wojewódzkie; tu jest wyłącznie prawo miejscowe.
