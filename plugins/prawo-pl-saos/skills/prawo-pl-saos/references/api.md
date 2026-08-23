# API SAOS — referencja endpointów

Baza: `https://www.saos.org.pl/api`  ·  Dokumentacja: `https://www.saos.org.pl/help/index.php/dokumentacja-api`
Wszystko **GET** (read-only), bez klucza i bez autoryzacji. SAOS to baza **wtórna** (agregat orzeczeń jawnych:
SN, TK, sądy powszechne, KIO; sądy administracyjne — praktycznie brak). Dane: ICM UW / Fundacja ePaństwo.

## Granice zbiorów (sprawdzone na żywo 2026-08-23)

`/search/judgments?courtType=X&pageSize=1&sortingField=JUDGMENT_DATE&sortingDirection=DESC` → najnowsze orzeczenie:

| courtType | najnowsze orzeczenie w SAOS | totalResults | nowsze orzeczenia |
|---|---|---|---|
| `SUPREME` (SN) | **2016-06-22** (III KK 195/16, id 245360) | 38 081 | sn.pl/orzecznictwo |
| `CONSTITUTIONAL_TRIBUNAL` (TK) | **2015-12-09** (Ts 266/14, K 35/15) | 9 503 | ipo.trybunal.gov.pl |
| `NATIONAL_APPEAL_CHAMBER` (KIO) | **2018-09-06** (KIO 1711/18, id 354890) | 22 168 | orzeczenia.uzp.gov.pl |
| `COMMON` | na bieżąco (z opóźnieniem) | — | orzeczenia.ms.gov.pl |

Zbiory SN/TK/KIO nie są zasilane od tych dat; rocznik granicy jest pokryty tylko **do dnia** (np. SN: brak
uchwał III CZP 56/16 z 26.10.2016 i III CZP 81/16 z 8.12.2016, które istnieją na sn.pl). `saos.py` trzyma te
daty w `ZASIEG`, porównuje `--od/--do` do dnia i przed blokadą `--strict` / przy zerze trafień potwierdza
granicę tym samym zapytaniem (`_granica_zbioru`, cache w procesie).

## Wydajność i okna serwisowe

`/search/judgments` bywa bardzo wolne (40–60 s na zapytanie, stan z 2026-08-22/23); `/judgments/{id}` jest
szybkie. W nocy SAOS zwraca HTTP 200 ze stroną HTML „Przerwa techniczna" zamiast JSON — helper traktuje to
jako stan UNKNOWN (błąd, kod ≠ 0), nigdy jako zero trafień. Limit na żądanie w helperze: 90 s, 2 próby.

## Endpointy

| Ścieżka | Zwraca |
|---|---|
| `/search/judgments` | wyszukiwarka orzeczeń (parametry niżej) — zwraca `items[]` + `info.totalResults` |
| `/judgments/{id}` | **pełne orzeczenie** (JSON pod kluczem `data`) |
| `/dump/judgments` | zrzut masowy (zakres dat, paginacja) — do bulk, nie do pojedynczych zapytań |
| `/dump/courts`, `/dump/commonCourts`, `/dump/scChambers` | słowniki sądów / izb SN |
| `/dump/enrichments` | dane wzbogacające |

## Parametry `/search/judgments` (z `queryTemplate` żywego API, 2026-06)

Tekst/identyfikacja: `all` (pełnotekstowo), `legalBase`, `referencedRegulation` (powołany przepis/akt —
**luźne dopasowanie pełnotekstowe** w polu `referencedRegulations`: `art. 415` zwraca orzeczenia do art. 415
k.c., k.p.c. i k.p.k.; nawet `Kodeks cywilny art. 415` nie eliminuje innych aktów — sprawdź akt w
`/judgments/{id}`), `lawJournalEntryCode` (`rok/pozycja`), `judgeName`, `caseNumber`, `keywords`.
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

`id`, `courtType`, `courtCases[]`, `judgmentType` (enum `SENTENCE|DECISION|RESOLUTION|REGULATION|REASONS`
— helper tłumaczy na wyrok / postanowienie / uchwała / zarządzenie / uzasadnienie), `judgmentDate`, `judges[]`,
`summary`, `textContent` (pełna treść), `legalBases[]`, **`referencedRegulations[]`** (`text` = gotowy opis
aktu + powołane artykuły, oraz `journalTitle/journalYear/journalNo/journalEntry`), **`referencedCourtCases[]`**
(`caseNumber`, `judgmentIds[]` = ID w SAOS do skoku, `generated`), `keywords[]`, `source` (`code`,
`judgmentUrl`, `publicationDate`), `judgmentForm` (tylko SN, np. „wyrok SN"), `division`, `chambers[]`,
`receiptDate`, `meansOfAppeal`, `judgmentResult`, `lowerCourtJudgments[]`.

### Jakość danych (zweryfikowane 2026-08-22/23 wobec sn.pl / UZP / feedu sądów powszechnych)

- **Indeksy górne.** W `textContent` SN/TK/KIO nie ma `<sup>` — numery są spłaszczone: art. 417¹ → „4171",
  art. 398¹⁴ → „39814", art. 479⁴⁵ → „47945" (I CSK 364/15, III CZP 17/15 vs PDF na sn.pl). Nie da się tego
  odtworzyć z API; helper drukuje stałą notę. Sądy powszechne mają `<sup>\n<!-- -->1</sup>` — helper renderuje
  `art. 556¹ § 1 k.c.` (feed sądu: `art. 556<xSUPx>1</xSUPx>`).
- **`referencedRegulations` jest niepełne i bywa uszkodzone.** KIO 1564/18 (id 354889): lista ma tylko k.p.k.
  i Konstytucję, a ŻADNEGO przepisu Pzp z sentencji (art. 93 ust. 1 pkt 4, art. 7 ust. 1, art. 24 ust. 1 pkt 22).
  Śmieci: `art. 4793647945` (= art. 479³⁶–479⁴⁵), `Dz. U. z 2015 r. Nr 0 poz. 184`, `art. 180 ust. 2oraz`,
  `art. 194 ust. atakże`, `art. n`, przepisy Konstytucji przypisane Konwencji (K 35/15). Helper oznacza takie
  wpisy „(wpis SAOS prawdopodobnie uszkodzony: …)".
- **`source.judgmentUrl` dla SN/TK/KIO jest martwy lub ogólny:** SN → `http://www.sn.pl/orzecznictwo/SitePages/Baza_orzeczen`
  (404, bez sygnatury), TK → `otk.trybunal.gov.pl/…/K_35_15.doc` (host nieosiągalny), KIO →
  `ftp://ftp.uzp.gov.pl/KIO/Wyroki/2018_1564.pdf` (nieosiągalny). Działa: SN
  `https://www.sn.pl/sites/orzecznictwo/Orzeczenia3/<SYGN, „/"→„-", spacje %20>.pdf` (HTTP 200 + application/pdf
  dla II KK 56/16, I CSK 364/15, III CZP 17/15, III CZP 81/16; nieistniejąca sygnatura → 404), TK
  `https://ipo.trybunal.gov.pl/ipo/`, KIO `https://orzeczenia.uzp.gov.pl/Home/Search`; sądy powszechne
  `apiorzeczenia.*.sa.gov.pl/ncourt-api/judgement/details?id=…` (działa).
- Anonimizacja i treść sądów powszechnych są zgodne z feedem sądu (I C 374/25: różnice tylko w numeracji list).

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
- Do dosłownego cytatu w piśmie/sądzie korzystaj ze źródła urzędowego, bo SAOS to agregat: dla sądów
  powszechnych `source.judgmentUrl`, dla SN wzorzec PDF na sn.pl, dla TK/KIO wyszukiwarki OTK/UZP (wyżej) —
  `source.judgmentUrl` dla SN/TK/KIO nie działa.
- Licencja danych: orzeczenia jawne, udostępniane publicznie; przy reużyciu podawaj źródło (SAOS) i sygnaturę.
