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
Odpowiedź: `{ "count": N, "items": [ { "address": "WDU...", "ELI": "DU/RRRR/PPP", "title": ..., "status": ... }, ... ] }`.

## Pola metadanych aktu (`/acts/{pub}/{year}/{pos}`)

`title`, `type`, `status`, `inForce` (`IN_FORCE`/…), `announcementDate`, `promulgation` (data ogłoszenia
w dzienniku), **`entryIntoForce`** (wejście w życie), `validFrom`, `legalStatusDate` (stan prawny t.j.),
`changeDate`, `keywords`/`keywordsNames`, `references`, `ELI`, `address` (ISAP), `displayAddress`
(np. „Dz.U. 2024 poz. 18"), `volume`, `texts` (lista plików), `textHTML`/`textPDF` (bool).

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
- **„Akty zmieniające"** — nowelizacje aktu; **„Akty zmienione"** — co ten akt nowelizuje.
- **„Akty wykonawcze"** — rozporządzenia wydane na podstawie aktu.
- **„Orzeczenie TK"**, **„Akty uchylone"**, **„Odesłania"**.

Na **tekście jednolitym** (obwieszczenie, np. `DU/2024/18`):
- **„Tekst jednolity dla aktu"** — wskazuje akt BAZOWY, który ten t.j. konsoliduje (kierunek odwrotny niż sugeruje nazwa!).
- **„Nowelizacje po tekście jednolitym"** — zmiany WPROWADZONE PO tym t.j. → sygnał, że t.j. bywa już nieaktualny.
- **„Podstawa prawna" / „Podstawa prawna z art."** — delegacje/podstawy.

## Wskazówki

- Aby ustalić AKTUALNY stan przepisu: akt bazowy → `references` „Inf. o tekście jednolitym" (weź najnowszy) → na nim sprawdź „Nowelizacje po tekście jednolitym". Komenda `tj` robi to automatycznie.
- `struct` pokazuje układ aktu i id jednostek, ale `text.html/{tree}` jest blokowany przez WAF dla artykułów — pojedynczy przepis pobieraj przez `tekst --fragment "art. N"` (lokalnie wycina z pełnego tekstu).
- Tekst z `text.html` zawiera twarde spacje (NBSP) — helper normalizuje je do zwykłych spacji. Indeks górny siedzi w `<sup>`, więc po konwersji jest odspacjowany (art. 299¹ → „Art. 299 1."); w `--fragment` podawaj go jako `"art. 299(1)"` albo `"art. 299¹"`.
- W nagłówku przepisu niedawno dodanego lub zmienionego stoi ODSYŁACZ DO PRZYPISU (też w `<sup>`), a treść przypisu API wstawia INLINE — w surowym HTML wygląda to tak: „Art. 66c 6)Dodany przez art. 3 pkt 2 ustawy… . Kto uporczywie…". Kropka artykułu stoi dopiero za przypisem, więc nagłówek ≠ „Art. N." — `--fragment` to obsługuje (`_KONIEC_ART` w `eli.py`).
- Helper wynosi treść przypisu do osobnej linii z etykietą `[przypis] `, bo inaczej komentarz redakcyjny („Dodany przez…", „W tym brzmieniu obowiązuje do…") jest nieodróżnialny od normy; etykieta jest konieczna także dlatego, że część przypisów zaczyna się od „Art. 598…" / „Tytuł działu…" i na początku linii udawałaby nagłówek jednostki. **Linia `[przypis]` to jedyny fragment wyniku, którego NIE ma w urzędowym tekście** — nie cytuj jej jako przepisu.
- Adres ISAP (`WDU{rok}{tom}{poz}` / `WMP...`) i ELI (`DU/{rok}/{poz}`) są równoważnymi identyfikatorami — helper przyjmuje obie formy.
- `/struct` istnieje głównie dla tekstów jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają.
