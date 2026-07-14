# API Portalu Orzeczeń UODO — referencja endpointów

Baza: `https://orzeczenia.uodo.gov.pl/api`  ·  Dokumentacja: `https://orzeczenia.uodo.gov.pl/api-doc/`
(OpenAPI 3.1: `/api-doc/schemas/openapi.yml`)  ·  Instrukcja portalu: `/manual/`
Wszystko **GET** (read-only), bez klucza (spec deklaruje basicAuth na wyszukiwaniu, ale endpointy
działają anonimowo — silnik obsługuje 401/403 czytelnym komunikatem, gdyby to się zmieniło).
Portal uruchomiony w 2025 r.; publikuje decyzje Prezesa UODO i powiązane orzeczenia sądów.

## Endpointy (zweryfikowane na żywo, 2026-07)

| Ścieżka | Zwraca |
|---|---|
| `/documents/search` | mapa typów dokumentów i ich indeksów wyszukiwawczych |
| `/documents/search/PublicDocument` | lista dokumentów (najnowsze) |
| `/documents/search/PublicDocument/{timespan}` | jw., zawężona do zakresu dat publikacji |
| `/documents/search/PublicDocument/{timespan}/{warunek}` | jw., z JEDNYM warunkiem filtrującym |
| `/documents/events/{id}` | pełne metadane po wewnętrznym `id` |
| `/documents/public/items/{refid}/meta.json` | **metadane po URN** (refid) |
| `/documents/public/items/{refid}:0/{content}.{format}` | **treść części 0**: `content` ∈ `meta units body title toc summary dates refs pub`; `format` ∈ `txt json html xml md`; parametry `lang=pl`, `date=RRRR-MM-DD` |

## Parametry wyszukiwania (query string)

`from` (od którego wyniku, domyślnie 0), `count` (ile, domyślnie 100), `order` (`-id` = od
najnowszych — tak sortuje silnik), `fields` (lista pól przez przecinek, np.
`id,refid,refname,title,dates,kind`; `*` = pełny obiekt indeksu).

## `{timespan}` i `{warunek}`

- `timespan` = `RRRR-MM-DD,RRRR-MM-DD` po datach PUBLIKACJI; każda strona może być pusta
  (`,` = wszystko; `2026-01-01,` = od; `,2026-06-30` = do). Skróty względne: `1M,` `1Y,`.
- `warunek` = `indeks:operator:wartość`. Operatory: `lt gt le ge eq ne in notin glob notglob regex`.
- Indeksy `PublicDocument`: `id time mtime kind refid refname status publicator_country
  publicator_type publicator_subtype publicator_year date_announcement date_publication
  keywords title_pl content_pl`.

**Zweryfikowane pułapki:**
- do pełnego tekstu (`content_pl`) używaj **`regex`, nie `glob`** — glob dopasowuje całą wartość
  i na wielolinijkowej treści zawodzi (`content_pl:glob:*biometri*` → 0; `content_pl:regex:biometr.*` → trafienia);
- regex działa **bez rozróżniania wielkości liter**;
- **łańcuch warunków** (`/{warunek1}/{warunek2}`) zwraca 200, ale drugi warunek bywa ignorowany —
  silnik stosuje JEDEN warunek na zapytanie i uprzedza na stderr;
- `fields=keywords`/`publicator_year` potrafią zwrócić `null` (płaskie nazwy indeksów ≠ ścieżki
  w meta JSON) — po pełne dane sięgnij po `meta.json` konkretnego dokumentu;
- eksport CSV istnieje tylko jako przycisk UI — w API go nie ma (silnik: `--json`).

## Identyfikatory

- **Sygnatura** (refname): `DKN.5131.9.2025`, `DKE.561.1.2026`, `ZSOŚS.440.259.2019`.
- **URN** (refid): `urn:ndoc:gov:pl:uodo:{rok}:{sygnatura_bez_roku}` — małe litery,
  kropki→podkreślenia, polskie znaki transliterowane do ASCII
  (`DKN.5131.9.2025` → `urn:ndoc:gov:pl:uodo:2025:dkn_5131_9`;
  `ZSOŚS.440.259.2019` → `…:2019:zsoss_440_259`). Rok = ostatni 4-cyfrowy człon sygnatury.
- Wewnętrzne `id`: `PublicDocument-RRRRMMDD-000000-000-{32hex}` (data = ogłoszenie).

## Kształt metadanych (`meta.json`)

`type version id time languages name{pl} title{pl} refid refname kind publication{status
inforce version pubid} publicator{type subtype name country year extids[]} dates[{date use
type status}] parts entities[]` — `dates[].use` ∈ `announcement | publication | validation`;
`publication.status` ∈ `final | nonfinal` (decyzja prawomocna/nieprawomocna w rozumieniu portalu).

## Mapowanie komend `uodo.py` → API

`najnowsze`→`/search/PublicDocument/,` + `order=-id`; `szukaj FRAZA`→warunek
`content_pl:regex:FRAZA`, `--tytul`→`title_pl:regex:…`, `--warunek`→przekazany wprost,
`--od/--do`→`{timespan}`, `--limit/--strona`→`count/from`; `decyzja`→`meta.json` + `body.txt`
(część `:0`, `lang=pl`).

## Wskazówki

- Wyniki wyszukiwania zawierają żądane `fields` — tytuł to opis redakcyjny przedmiotu decyzji
  (bywa bardzo długi); kwoty kar i podstawy prawne bierz z `body.txt`.
- Portal publikuje też starsze decyzje z opóźnieniem (sygnatury 2019–2024 pojawiają się
  sukcesywnie) — brak w portalu ≠ decyzja nie istnieje.
- Bądź uprzejmy dla serwera: ≤2 zapytania/s (limitów nie udokumentowano).
- Dane publiczne; przy cytowaniu podawaj sygnaturę i datę decyzji.
