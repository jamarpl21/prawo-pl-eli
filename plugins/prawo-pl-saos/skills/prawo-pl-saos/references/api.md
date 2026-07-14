# API SAOS — referencja endpointów

Baza: `https://www.saos.org.pl/api`  ·  Dokumentacja: `https://www.saos.org.pl/help/index.php/dokumentacja-api`
Wszystko **GET** (read-only), bez klucza i bez autoryzacji. SAOS to baza **wtórna** (agregat orzeczeń jawnych:
SN, TK, sądy powszechne, KIO; sądy administracyjne — praktycznie brak). Dane: ICM UW / Fundacja ePaństwo.

## Endpointy

| Ścieżka | Zwraca |
|---|---|
| `/search/judgments` | wyszukiwarka orzeczeń (parametry niżej) — zwraca `items[]` + `info.totalResults` |
| `/judgments/{id}` | **pełne orzeczenie** (JSON pod kluczem `data`) |
| `/dump/judgments` | zrzut masowy (zakres dat, paginacja) — do bulk, nie do pojedynczych zapytań |
| `/dump/courts`, `/dump/commonCourts`, `/dump/scChambers` | słowniki sądów / izb SN |
| `/dump/enrichments` | dane wzbogacające |

## Parametry `/search/judgments` (z `queryTemplate` żywego API, 2026-06)

Tekst/identyfikacja: `all` (pełnotekstowo), `legalBase`, `referencedRegulation` (powołany przepis/akt),
`lawJournalEntryCode` (`rok/pozycja`), `judgeName`, `caseNumber`, `keywords`.
Sąd: `courtType` ∈ `COMMON | SUPREME | CONSTITUTIONAL_TRIBUNAL | NATIONAL_APPEAL_CHAMBER | ADMINISTRATIVE`;
powszechne: `ccCourtType` (`APPEAL|REGIONAL|DISTRICT`), `ccCourtId/Code/Name`, `ccDivisionId/Code/Name`,
`ccIncludeDependentCourtJudgments`; SN: `scPersonnelType`, `scJudgmentForm`, `scChamberId/Name`, `scDivisionId/Name`.
Rodzaj: `judgmentTypes` ∈ `DECISION | RESOLUTION | SENTENCE | REGULATION | REASONS`.
Daty: `judgmentDateFrom`, `judgmentDateTo` (`yyyy-MM-dd`).
Paginacja/sort: `pageSize` (1–100), `pageNumber` (od 0), `sortingField`
(`DATABASE_ID | JUDGMENT_DATE | REFERENCING_JUDGMENTS_COUNT | …`), `sortingDirection` (`ASC|DESC`).

Mapowanie komend `saos.py` → parametry: `--sad`→`courtType`, `--sygnatura`→`caseNumber`,
`--przepis`→`referencedRegulation`, `--sedzia`→`judgeName`, `--haslo`→`keywords`, `--typ`→`judgmentTypes`,
`--od/--do`→`judgmentDateFrom/To`, `--limit`→`pageSize`, `--strona`→`pageNumber`.

## Pole `items[]` (wynik wyszukiwania)

`id`, `href`, `courtType`, `courtCases[].caseNumber`, `judgmentType`, `judgmentDate`,
`judges[].name` (+`specialRoles`), `keywords[]`, `judgmentForm` (np. „wyrok SN"), `textContent`
(snippet z podświetleniem `<em>`), `division` (zależne od sądu — niżej).

## Pole `data` (pełne orzeczenie `/judgments/{id}`)

`id`, `courtType`, `courtCases[]`, `judgmentType`, `judgmentDate`, `judges[]`, `summary`, `textContent`
(pełna treść), `legalBases[]`, **`referencedRegulations[]`** (`text` = gotowy opis aktu + powołane artykuły,
oraz `journalTitle/journalYear/journalNo/journalEntry`), **`referencedCourtCases[]`** (`caseNumber`,
`judgmentIds[]` = ID w SAOS do skoku, `generated`), `keywords[]`, `source` (`code`, `judgmentUrl` =
link do oryginału, `publicationDate`), `judgmentForm`, `division`, `chambers[]`, `receiptDate`,
`meansOfAppeal`, `judgmentResult`, `lowerCourtJudgments[]`.

### Pole `division` (opis sądu — różne kształty)

- **Sądy powszechne (COMMON):** `division.court.name` (np. „Sąd Apelacyjny w Krakowie") + `division.name`
  (wydział, np. „I Wydział Cywilny").
- **SN (SUPREME):** w wyszukiwaniu `division.chambers[].name` (lista izb), w szczególe `division.chamber.name`
  (jedna izba, np. „Izba Cywilna") + `division.name` (wydział).
Helper `_court_label()` w `saos.py` obsługuje oba kształty.

## Wskazówki

- **Most z ELI:** najpierw ustal akt/artykuł w ELI (skill prawo-pl-eli), potem `--przepis "<nazwa aktu>"`
  lub `lawJournalEntryCode` zawęża do orzeczeń powołujących ten przepis.
- `textContent` w wynikach to fragment z `<em>…</em>` wokół trafienia; helper czyści HTML do tekstu.
- **Sądy administracyjne**: `courtType=ADMINISTRATIVE` zwykle zwraca `totalResults: 0` — użyj skilla
  **prawo-pl-cbosa** (baza CBOSA, `https://orzeczenia.nsa.gov.pl`).
- Do dosłownego cytatu w piśmie/sądzie korzystaj z `source.judgmentUrl` (oryginał), bo SAOS to agregat.
- Licencja danych: orzeczenia jawne, udostępniane publicznie; przy reużyciu podawaj źródło (SAOS) i sygnaturę.
