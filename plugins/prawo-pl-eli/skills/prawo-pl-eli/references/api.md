# API ELI Sejmu — referencja endpointów

Baza: `https://api.sejm.gov.pl/eli`  ·  Spec (YAML): `https://api.sejm.gov.pl/eli/openapi/`  ·  Swagger UI: `https://api.sejm.gov.pl/eli/openapi/ui/`
Wszystko **GET** (read-only). `{publisher}` = `DU` (Dziennik Ustaw) lub `MP` (Monitor Polski). `{year}` = rok, `{position}` = pozycja.

## Endpointy

| Ścieżka | Zwraca |
|---|---|
| `/` | informacje o API |
| `/acts` | lista wydawców (DU, MP) |
| `/acts/search` | wyszukiwarka aktów (parametry niżej) |
| `/acts/{address}` | metadane po adresie ISAP, np. `/acts/WDU20240000018` |
| `/acts/{publisher}` | lista roczników |
| `/acts/{publisher}/{year}` | lista aktów w roku |
| `/acts/{publisher}/{year}/{position}` | **metadane aktu** (JSON) |
| `/acts/{publisher}/{year}/{position}/text.html` | tekst aktu (HTML) |
| `/acts/{publisher}/{year}/{position}/text.html/{tree}` | tekst pojedynczej jednostki redakcyjnej — **UWAGA: zapora (WAF) Sejmu odrzuca id z myślnikami (czyli wszystkie artykuły) — stan na 2026-06; zamiast tego użyj `tekst --fragment`** |
| `/acts/{publisher}/{year}/{position}/text.pdf` | tekst (PDF, jeśli jednoplikowy) |
| `/acts/{publisher}/{year}/{position}/text/{type}/{fileName}` | konkretny plik tekstu (np. tekst jednolity PDF) |
| `/acts/{publisher}/{year}/{position}/references` | powiązania (nowelizacje, podstawa prawna, tekst jednolity, akty wykonawcze) |
| `/acts/{publisher}/{year}/{position}/struct` | struktura aktu (spis jednostek redakcyjnych → wartości `{tree}`) |
| `/acts/{publisher}/{year}/volumes...` | warianty z numerem tomu (starsze roczniki) |
| `/changes/acts` | akty zmieniające w okresie |
| `/types` `/keywords` `/statuses` `/institutions` `/titles` `/references` | słowniki |

## Parametry `/acts/search` (zweryfikowane / typowe)

`title` (fraza w tytule), `type` (np. `Ustawa`, `Rozporządzenie`, `Obwieszczenie`), `year`, `publisher` (`DU`/`MP`),
`inForce` (`1` = obowiązujące), `keyword`, `limit`, `offset`, oraz zakresy dat (np. `dateFrom`/`dateTo`,
`announcementDateFrom`/`announcementDateTo`, `pubDateFrom`/`pubDateTo`). Pełny zestaw — w spec OpenAPI.
Odpowiedź: `{ "count": N, "totalCount": M, "offset": O, "items": [ { "address": "WDU...", "ELI": "DU/RRRR/PPP", "title": ..., "status": ... }, ... ] }`.
**`count` to rozmiar zwróconej strony, `totalCount` — prawdziwa liczba trafień** (zweryfikowane 2026-08:
„Kodeks pracy"/Ustawa → count 10, totalCount 58; akt bazowy DU/1974/141 był na pozycji 58). Helper wypisuje
`totalCount` i podpowiada `--offset`/`--limit`.

## Pola metadanych aktu (`/acts/{pub}/{year}/{pos}`)

`title`, `type`, `status`, `inForce` (`IN_FORCE`/…), `keywords`/`keywordsNames`, `references`, `ELI`,
`address` (ISAP), `displayAddress` (np. „Dz.U. 2024 poz. 18"), `volume`, `texts` (lista plików),
`textHTML`/`textPDF` (bool), `changeDate` (ostatnia zmiana rekordu w bazie — NIE data prawna).

### Daty (zweryfikowane na żywym API i nagłówkach PDF Dz.U., 2026-08)
| Pole | Znaczenie | Przykład DU/2024/928 |
|---|---|---|
| `announcementDate` | **data AKTU** = wydania/podpisania, czyli „z dnia …" w tytule (ISAP: „Data wydania") — NIE ogłoszenie | 2024-06-14 |
| `promulgation` | **data OGŁOSZENIA** w Dz.U./M.P. (nagłówek PDF „Warszawa, dnia …"; ISAP: „Data ogłoszenia"); od niej liczy się vacatio legis; bywa `null` dla starych pozycji (Konstytucja DU/1997/483) | 2024-06-24 |
| `entryIntoForce` | wejście w życie aktu (ogólna reguła); `null` dla obwieszczeń z t.j. | 2024-09-25 |
| `comments` | uwagi ISAP — najczęściej **rozłożone wejście w życie** („art. 5 ust. 4 … wchodzą w życie z dniem 25 grudnia 2024 r.") | jw. |
| `legalStatusDate` | **stan prawny t.j.** („z uwzględnieniem stanu prawnego na dzień …" w obwieszczeniu); zmiany ogłoszone lub wchodzące w życie po tej dacie NIE są w tekście | DU/2026/795 → 2026-05-19 |
| `validFrom` | obowiązywanie (gdy różne od wejścia w życie) | — |
Helper `meta` drukuje je jako „Data aktu" / „Ogłoszono" / „WEJŚCIE W ŻYCIE" / „Stan prawny na" / „Uwagi".
Parametry wyszukiwarki: `announcementDateFrom/To` filtruje po dacie aktu, `pubDateFrom/To` po ogłoszeniu.

### `textHTML=false` — kiedy `text.html` jest puste (HTTP 200, 0 bajtów)
Nie jest to „opóźnienie" — dla części aktów API po prostu nie ma HTML, czasem trwale: Konstytucja DU/1997/483
(akt z 1997 r.), k.c. t.j. DU/2026/795 **i** poprzedni t.j. DU/2025/1071 (HTML dopiero w DU/2024/1061), k.p.c.
t.j. DU/2026/468, świeże pozycje (DU/2026/694). Helper `tekst` czyta wtedy WŁASNY urzędowy PDF aktu (`texts[]`
typ U > T > O) przez `pdftotext -layout` i czyści go (nagłówki „Dziennik Ustaw – N – Poz. X"/„©Kancelaria
Sejmu", stopki z datą, sklejanie zawiniętych wierszy i dzielonych wyrazów, indeks górny `68[1]` → „68 1" jak
w HTML, odsyłacze do przypisów „§ 1.3)" → „§ 1." + linia `[przypis 3)] …` z dołu strony, obwieszczenie sprzed
załącznika oznaczone „» "). Bez `pdftotext` helper sięga po najnowszy STARSZY t.j. z HTML — z nagłówkiem
„NIEAKTUALNE BRZMIENIE MOŻLIWE" i listą zmian aktu bazowego po jego `legalStatusDate`; `--strict` to blokuje.

### Kody `type` w `texts[]`
- `H` — HTML (`text.html`)
- `O` — tekst ogłoszony / oryginał (PDF)
- `I` — tekst ogłoszony (skan/obraz, PDF)
- `T` — tekst jednolity (PDF)
- `U` — tekst ujednolicony / aktualny tekst jednolity (PDF) ← zwykle najlepszy do dosłownego cytatu

Plik pobierasz: `/acts/{pub}/{year}/{pos}/text/{type}/{fileName}` (np. `/text/U/D20240018Lj.pdf`).

## Kategorie w `/references` (zweryfikowane na żywym API, 2026-06)

Każda pozycja to `{ "act": { ELI, displayAddress, title, status, ... }, "date"?, "art"? }`.
Kategorie ZALEŻĄ od rodzaju aktu:

Na **akcie bazowym** (np. ustawa `DU/2000/1037`):
- **„Inf. o tekście jednolitym"** — obwieszczenia z tekstami jednolitymi tego aktu; najnowszy
  (status „obowiązujący") = aktualny, starsze mają status „wygaśnięcie aktu".
- **„Akty zmieniające"** — nowelizacje aktu; pole `date` = **wejście w życie ZMIANY** tego aktu (nie data
  ani ogłoszenie nowelizacji; może różnić się od `entryIntoForce` nowelizacji — np. DU/2026/1003 ma
  `entryIntoForce` 2026-08-11, a zmiana k.p.c. `date` 2026-10-28 zgodnie z jej `comments`); obiekt `act`
  niesie `announcementDate` (data aktu) i `promulgation` (ogłoszono). **„Akty zmienione"** — co ten akt nowelizuje.
- **„Akty wykonawcze"** — rozporządzenia wydane na podstawie aktu.
- **„Orzeczenie TK"**, **„Akty uchylone"**, **„Odesłania"**.

Na **tekście jednolitym** (obwieszczenie, np. `DU/2024/18`):
- **„Tekst jednolity dla aktu"** — wskazuje akt BAZOWY, który ten t.j. konsoliduje (kierunek odwrotny niż sugeruje nazwa!).
- **„Nowelizacje po tekście jednolitym"** — zmiany WPROWADZONE PO tym t.j. → sygnał, że t.j. bywa już nieaktualny.
  Pole `date` = **data aktu** nowelizacji (nie wejście w życie!), bywa pominięte. **Kategoria istnieje TYLKO na
  AKTUALNYM t.j.** (wygasły t.j., np. DU/2024/1061, ma już tylko „Podstawa prawna…" i „Tekst jednolity dla
  aktu") i **nie jest synchronizowana** z „Akty zmieniające" aktu bazowego (2026-08: k.p.c. DU/2026/468 — 2 z 5,
  k.s.h. DU/2024/18 — 1 z 4). Helper uzupełnia ją o „Akty zmieniające" aktu bazowego, których `promulgation`
  lub `date` (wejście w życie zmiany) jest po `legalStatusDate` t.j., deduplikuje po ELI i opisuje każdą datę
  etykietą („data aktu", „ogłoszono", „wejście w życie zmiany"); brakujące wejście w życie dopytuje z metadanych
  nowelizacji (maks. 10 żądań).
- **„Podstawa prawna" / „Podstawa prawna z art."** — delegacje/podstawy.

## Wskazówki

- Aby ustalić AKTUALNY stan przepisu: akt bazowy → `references` „Inf. o tekście jednolitym" (weź najnowszy) → na nim sprawdź „Nowelizacje po tekście jednolitym" ORAZ „Akty zmieniające" aktu bazowego po `legalStatusDate` t.j. Komendy `tj`/`tekst` robią to automatycznie.
- `struct` pokazuje układ aktu i id jednostek, ale `text.html/{tree}` jest blokowany przez WAF dla artykułów — pojedynczy przepis pobieraj przez `tekst --fragment "art. N"` (lokalnie wycina z pełnego tekstu).
- Tekst z `text.html` zawiera twarde spacje (NBSP) — helper normalizuje je do zwykłych spacji. Indeks górny siedzi w `<sup>`, więc po konwersji jest odspacjowany (art. 299¹ → „Art. 299 1."); w `--fragment` podawaj go jako `"art. 299(1)"` albo `"art. 299¹"`.
- W nagłówku przepisu niedawno dodanego lub zmienionego stoi ODSYŁACZ DO PRZYPISU (też w `<sup>`), a treść przypisu API wstawia INLINE — w surowym HTML wygląda to tak: „Art. 66c 6)Dodany przez art. 3 pkt 2 ustawy… . Kto uporczywie…". Kropka artykułu stoi dopiero za przypisem, więc nagłówek ≠ „Art. N." — `--fragment` to obsługuje (`_KONIEC_ART` w `eli.py`).
- Odsyłacz do przypisu siedzi w `<a class="gloss-link tooltip"><sup>N)</sup><span class="tooltip-text">…</span></a>` WEWNĄTRZ numeru jednostki (`<h3>2<a…><sup>1)</sup>…</a>)</h3>` = pkt 2 z przypisem 1; `§ 1<a…><sup>12)</sup>…</a>.`). Helper NIE przepisuje numeru odsyłacza do tekstu (wychodziło „2 1)", „a 2)", „§ 1 12)" — cyfra przypisu wchodziła w numer jednostki), a treść przypisu wynosi do osobnej linii `[przypis N)] …` za najbliższą granicą bloku, bo inaczej komentarz redakcyjny („Dodany przez…", „W tym brzmieniu obowiązuje do…") jest nieodróżnialny od normy; etykieta jest konieczna także dlatego, że część przypisów zaczyna się od „Art. 598…" / „Tytuł działu…" i na początku linii udawałaby nagłówek jednostki. Indeks górny artykułu (goły `<sup>` poza odsyłaczem) nadal dostaje spację („Art. 449 1." ≠ „Art. 4491."). **Linia `[przypis N)]` to jedyny fragment wyniku, którego NIE ma w urzędowym tekście** — nie cytuj jej jako przepisu.
- Adres ISAP (`WDU{rok}{tom}{poz}` / `WMP...`) i ELI (`DU/{rok}/{poz}`) są równoważnymi identyfikatorami — helper przyjmuje obie formy.
- `/struct` istnieje głównie dla tekstów jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają (HTTP 404 — helper `struktura` zgłasza „Brak struktury…" z kodem ≠ 0, nie surowy błąd HTTP).
- Helper trzyma w pamięci udane GET-y bez parametrów w obrębie jednego uruchomienia (odniesienia aktu bazowego są potrzebne dwa razy), żeby nie drażnić zapory powtórzonymi żądaniami.
