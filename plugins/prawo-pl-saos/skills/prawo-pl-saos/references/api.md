# API SAOS — referencja endpointów

Baza: `https://www.saos.org.pl/api`  ·  Dokumentacja: `https://www.saos.org.pl/help/index.php/dokumentacja-api`
Wszystko **GET** (read-only), bez klucza i bez autoryzacji. SAOS to baza **wtórna** (agregat orzeczeń jawnych:
SN, TK, sądy powszechne, KIO; sądy administracyjne — praktycznie brak). Dane: ICM UW / Fundacja ePaństwo.

## Granice zbiorów (sprawdzone na żywo 2026-08-23)

`/search/judgments?courtType=X&pageSize=1&sortingField=JUDGMENT_DATE&sortingDirection=DESC` → najnowsze orzeczenie:

| courtType | najnowsze orzeczenie w SAOS | totalResults | nowsze orzeczenia |
|---|---|---|---|
| `SUPREME` (SN) | **2016-06-22** (III KK 195/16, id 245360) | 38 081 | sn.pl/pl/wyszukiwarka-orzeczen |
| `CONSTITUTIONAL_TRIBUNAL` (TK) | **2015-12-09** (Ts 266/14, K 35/15) | 9 503 | ipo.trybunal.gov.pl |
| `NATIONAL_APPEAL_CHAMBER` (KIO) | **2018-09-06** (KIO 1711/18, id 354890) | 22 168 | orzeczenia.uzp.gov.pl |
| `COMMON` | na bieżąco (z opóźnieniem) | — | orzeczenia.ms.gov.pl |

Zbiory SN/TK/KIO nie są zasilane od tych dat; rocznik granicy jest pokryty tylko **do dnia** (np. SN: brak
uchwał III CZP 56/16 z 26.10.2016 i III CZP 81/16 z 8.12.2016, które istnieją na sn.pl). `saos.py` trzyma te
daty w `ZASIEG`, porównuje `--od/--do` do dnia i przed blokadą `--strict` / przy zerze trafień potwierdza
granicę tym samym zapytaniem (`_granica_zbioru`, cache w procesie).

## Wydajność i okna serwisowe

`/search/judgments` bywa bardzo wolne (40–60 s na zapytanie, stan z 2026-08-22/23); `/judgments/{id}` jest
szybkie. W czasie przerwy technicznej SAOS zwraca HTTP 200 ze stroną HTML „Przerwa techniczna" zamiast JSON —
helper traktuje to jako stan UNKNOWN (błąd, kod ≠ 0), nigdy jako zero trafień, i w komunikacie wskazuje źródła
niezależne od SAOS (portal MS, CBOSA, wyszukiwarka SN). Limit na żądanie w helperze: 90 s, 2 próby.

## Endpointy

| Ścieżka | Zwraca |
|---|---|
| `/search/judgments` | wyszukiwarka orzeczeń (parametry niżej) — zwraca `items[]` + `info.totalResults` |
| `/judgments/{id}` | **pełne orzeczenie** (JSON pod kluczem `data`) |
| `/dump/judgments` | zrzut masowy (zakres dat, paginacja) — do bulk, nie do pojedynczych zapytań |
| `/dump/courts`, `/dump/commonCourts`, `/dump/scChambers` | słowniki sądów / izb SN |
| `/dump/enrichments` | dane wzbogacające |
| `https://www.saos.org.pl/keywords/COMMON/<początek hasła>` (poza `/api`) | słownik haseł tematycznych sądów powszechnych — podpowiadacz wyszukiwarki SAOS: `[{"id", "phrase"}]`, dopasowanie od POCZĄTKU hasła, bez rozróżniania wielkości liter (`…/zdrowiu` → `[]`, `…/przestępstwo przeciwko zdrowiu` → 1 hasło). Dla `SUPREME`, `CONSTITUTIONAL_TRIBUNAL`, `NATIONAL_APPEAL_CHAMBER`, `ADMINISTRATIVE` zawsze `[]`. Sprawdzone 2026-11-13 |

## Parametry `/search/judgments` (z `queryTemplate` żywego API, 2026-06)

Tekst/identyfikacja: `all` (pełnotekstowo), `legalBase`, `referencedRegulation` (powołany przepis/akt —
**luźne dopasowanie pełnotekstowe** w polu `referencedRegulations`: `art. 415` zwraca orzeczenia do art. 415
k.c., k.p.c. i k.p.k.; nawet `Kodeks cywilny art. 415` nie eliminuje innych aktów — sprawdź akt w
`/judgments/{id}`), `lawJournalEntryCode` (`rok/pozycja`), `judgeName`, `caseNumber`, `keywords`.
Sąd: `courtType` ∈ `COMMON | SUPREME | CONSTITUTIONAL_TRIBUNAL | NATIONAL_APPEAL_CHAMBER | ADMINISTRATIVE`;
powszechne: `ccCourtType` (`APPEAL|REGIONAL|DISTRICT`), `ccCourtId/Code/Name`, `ccDivisionId/Code/Name`,
`ccIncludeDependentCourtJudgments`; SN: `scPersonnelType`, `scJudgmentForm`, `scChamberId/Name`, `scDivisionId/Name`.
Rodzaj: `judgmentTypes` ∈ `DECISION | RESOLUTION | SENTENCE | REGULATION | REASONS`.

**`keywords` (`--haslo`)** to DOKŁADNE hasło (bez rozróżniania wielkości liter), nie fraza: „przestępstwo przeciwko
zdrowiu” → 1882, „przestępstwa przeciwko zdrowiu” → 256, „przestępstwo” → 583 (osobne hasła). Hasła mają TYLKO
sądy powszechne: 0 ze 100 najnowszych orzeczeń SN/TK/KIO i 0 ze 100 najstarszych SN ma `keywords`, a
`keywords=odszkodowanie|umowa o pracę|zamówienia publiczne` z `courtType` SN/TK/KIO daje 0 (dla COMMON:
20 032 / 354 / 303). Słownik jest zgodny z danymi: każde ze 100 haseł z 300 orzeczeń sądów powszechnych jest
w słowniku `/keywords/COMMON/…` (2026-11-13). `saos.py` przy zerze z `--haslo` sprawdza słownik: hasło spoza
słownika albo `--haslo` dla SN/TK/KIO → kod 2 (zero wynika z hasła, nie z braku orzeczeń).
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

- **`judgmentDate` przy `judgmentType=REASONS`** bywa datą uzasadnienia, a bywa datą wyroku. Data uzasadnienia:
  547117, VI Ka 1622/25: `2026-06-08`, a treść zaczyna się „Warszawa, dnia 20 maja 2026 r." (data wyroku;
  sprawdzone 2026-10-05). Data wyroku: 449645, VI Ka 468/21: `2021-10-22` = „Warszawa, dnia 22 października
  2021 r.” w nagłówku, a portal MS datuje to uzasadnienie na 2021-11-09; 546528, II C 1470/25: `2026-04-15` —
  tak samo jak wyrok tej sprawy (546527), portal MS: 2026-05-11. Pomiar 2026-11-13 (22 uzasadnienia porównane
  z Portalem Orzeczeń MS, rozstrzygane nagłówkiem treści i wyrokiem tej sprawy w SAOS): 12 — data uzasadnienia
  (wyrok sprawy ma wcześniejszą datę), 6 — data wyroku, 4 — nie do ustalenia (brak treści, nagłówka i wyroku
  w SAOS). Z 6 dat wyroku 5 jest WCZEŚNIEJSZYCH niż data dokumentu w portalu MS, a 1 (I C 401/20, wyrok
  i uzasadnienie w jednym dokumencie) jest jej równa i równa dacie z nagłówka. Rozstrzyga data w
  identyfikatorze portalu MS z `source.judgmentUrl` (`…_Uz_2021-11-09_001` = data dokumentu w portalu)
  i nagłówek treści.
  Pozostałe typy w tej próbie zgodne z portalem: wyrok 6/6, postanowienie 2/2, „zarządzenie, uzasadnienie”
  (`REGULATION`) 6/6; „wyrok z uzasadnieniem” (`SENTENCE`) 2/3 — w trzecim (I C 293/24) SAOS ma datę wyroku
  z sentencji (2024-10-28), a lista portalu MS datę uzasadnienia (2024-11-12).
- **`source.publicationDate`** sądów powszechnych = „Data publikacji” w Portalu Orzeczeń MS w 39/39 orzeczeń
  próby 2026-11-13. KIO i TK mają `publicationDate` puste, SN — dzień po orzeczeniu (III KK 195/16: 2016-06-23).
  Daty orzeczeń KIO zgodne z wyszukiwarką UZP w 8/8 porównanych (KIO 1564/18 i 7 wyroków z 26–27.02.2018;
  KIO 1711/18, 730/18, 1432/18 i 258/18 wyszukiwarka UZP nie zwraca).
- **`division.court.name`** (sądy powszechne) jest w `items[]` wyszukiwania — `caseNumber` nie jest unikalny
  między sądami (`I ACa 100/13` → 4 trafienia w 4 sądach apelacyjnych), więc listę trzeba opisywać sądem.

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
  `ftp://ftp.uzp.gov.pl/KIO/Wyroki/2018_1564.pdf` (nieosiągalny). Dawny wzorzec PDF SN
  `sn.pl/sites/orzecznictwo/Orzeczenia3/<sygn>.pdf` obecnie → 301/302 → `sn.pl/404.html` (5/5, 2026-10-05).
  Działa: SN — wyszukiwarka `https://www.sn.pl/pl/wyszukiwarka-orzeczen`; strona orzeczenia
  `…/wyszukiwarka-orzeczen?orzeczenie=<id>`, gdzie `id` zwraca JSON-owe proxy strony:
  `https://www.sn.pl/pl/index.php?option=com_ajax&plugin=snproxy&format=json&task=searchOrzeczenia&sygnatura=<sygn>`
  → `data[0].data[] = {sygnatura_sprawy, data_wydania, forma_orzeczenia, id}` (dopasowanie fragmentem —
  „328/10” zwraca też IV CSK 328/10 — więc `saos.py` wymaga dokładnej sygnatury i daty; awaria → link do
  wyszukiwarki z instrukcją). Działało 2026-10-05; od października 2026 sn.pl stoi za filtrem antybotowym
  (Incapsula) — 2026-11-13 proxy zwróciło HTTP 200 `text/html` ze stroną wyzwania zamiast JSON-a, więc zwykle
  wynikiem jest link do wyszukiwarki z instrukcją. Helper próbuje raz (limit 7 s) i filtra nie obchodzi.
  Wyszukiwarka nie przyjmuje sygnatury w adresie URL. TK
  `https://ipo.trybunal.gov.pl/ipo/`, KIO `https://orzeczenia.uzp.gov.pl/Home/Search`; sądy powszechne
  `apiorzeczenia.*.sa.gov.pl/ncourt-api/judgement/details?id=…` (działa).
- Anonimizacja i treść sądów powszechnych są zgodne z feedem sądu (I C 374/25: różnice tylko w numeracji list).
- **Pokrycie sądów powszechnych.** Próba 2026-11-13: 40 orzeczeń z Portalu Orzeczeń MS (po 10 z publikacji
  12.11.2026, 12–27.05.2026, 15–22.11.2024 i 15–18.11.2021), każde szukane w SAOS po `caseNumber` i dopasowane
  po identyfikatorze portalu w `source.judgmentUrl`: obecnych 39/40, w tym 10/10 opublikowanych w portalu dzień
  wcześniej. Brak: XXVIII C 22637/22 (SO w Warszawie) — SAOS nie zna XXVIII Wydziału Cywilnego tego sądu
  (`/dump/commonCourts` kończy listę wydziałów SO w Warszawie na XXVII; `ccDivisionName=XXVIII Wydział Cywilny`
  → 0, `XXVII Wydział Cywilny Odwoławczy` → 1054). Luka jest więc wydziałowa; ile wydziałów brakuje w całym
  słowniku — niezmierzone. `saos.py` przy zerze odsyła do `orzeczenia_ms.py` gotową komendą.
- **Sądy powszechne bez treści.** Część orzeczeń ma w SAOS `textContent` puste (sama metryka). Próba
  2026-11-13 (200 z 578 orzeczeń z 1.09–10.10.2026): wszystkie 36 z `source.publicationDate` 24.09–9.10.2026
  bez treści (okres awarii Portalu Orzeczeń MS), od publikacji 12.10 wszystkie z treścią; w 36/36
  `source.judgmentUrl` ma parametr `id=` z identyfikatorem portalu (`[0-9]{15}_[A-Za-z0-9_-]+`), a portal MS
  wydaje treść tych dokumentów (3/3, np. II K 295/25 — 16 331 znaków). Helper odsyła do
  `orzeczenia_ms.py orzeczenie <ID>` (skill prawo-pl-orzeczenia-ms), bez ID — do `sygnatura <sygn.>`.
- **Daty z przyszłości.** Np. I ACa 772/13 → `judgmentDate` 3013-12-04, II K 84/16 → 2101-04-14,
  I ACa 1027/23 → 2026-12-16 (2026-11-13). Sortowanie `JUDGMENT_DATE DESC` wynosi je na górę. Helper oznacza
  je na liście i w nagłówku, a zapytanie o granicę zbioru SN/TK/KIO ma `judgmentDateTo=<dziś>` (i odrzuca
  datę z przyszłości), żeby błędny rekord nie przesunął granicy. SN/TK/KIO takich rekordów nie miały
  (2026-11-13: najnowsze 2016-06-22, 2015-12-09, 2018-09-06).

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
  powszechnych `source.judgmentUrl` (albo skill prawo-pl-orzeczenia-ms), dla SN wyszukiwarka sn.pl, dla TK/KIO wyszukiwarki OTK/UZP (wyżej) —
  `source.judgmentUrl` dla SN/TK/KIO nie działa.
- Licencja danych: orzeczenia jawne, udostępniane publicznie; przy reużyciu podawaj źródło (SAOS) i sygnaturę.
