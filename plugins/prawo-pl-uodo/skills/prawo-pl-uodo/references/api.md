# API Portalu Orzeczeń UODO — referencja endpointów

Baza: `https://orzeczenia.uodo.gov.pl/api`  ·  Dokumentacja: `https://orzeczenia.uodo.gov.pl/api-doc/`
(OpenAPI 3.1: `/api-doc/schemas/openapi.yml`)  ·  Instrukcja portalu: `/manual/`
Wszystko **GET** (read-only), bez klucza (spec deklaruje basicAuth na wyszukiwaniu, ale endpointy
działają anonimowo — silnik obsługuje 401/403 czytelnym komunikatem, gdyby to się zmieniło).
Portal uruchomiony w 2025 r.; publikuje decyzje Prezesa UODO (z treścią) oraz rekordy powiązane —
orzeczenia sądów, akty prawne, wytyczne EROD — **bez treści** (same metadane; ok. 200 z 700 rekordów indeksu).

## Endpointy (zweryfikowane na żywo, 2026-07)

| Ścieżka | Zwraca |
|---|---|
| `/documents/search` | mapa typów dokumentów i ich indeksów wyszukiwawczych |
| `/documents/search/PublicDocument` | lista dokumentów (najnowsze) |
| `/documents/search/PublicDocument/{timespan}` | jw., zawężona do zakresu dat **ogłoszenia** (`date_announcement` = data decyzji/orzeczenia) — NIE publikacji |
| `/documents/search/PublicDocument/{timespan}/{warunek}` | jw., z JEDNYM warunkiem filtrującym |
| `/documents/events/{id}` | pełne metadane po wewnętrznym `id` |
| `/documents/public/items/{refid}/meta.json` | **metadane po URN** (refid) |
| `/documents/public/items/{refid}:0/{content}.{format}` | **treść części 0**: `content` ∈ `meta units body title toc summary dates refs pub`; `format` ∈ `txt json html xml md`; parametry `lang=pl`, `date=RRRR-MM-DD` |

## Parametry wyszukiwania (query string)

`from` (od którego wyniku, domyślnie 0), `count` (ile, domyślnie 100), `order` (`-id` = od
najnowszych — tak sortuje silnik), `fields` (lista pól przez przecinek, np.
`id,refid,refname,title,dates,kind`; `*` = pełny obiekt indeksu).

## `{timespan}` i `{warunek}`

- `timespan` = `RRRR-MM-DD,RRRR-MM-DD` po dacie **OGŁOSZENIA** (`dates[].use=announcement` — data
  wydania decyzji / orzeczenia), NIE po dacie publikacji w portalu (zweryfikowane 2026-08:
  `2026-07-01,` → 0 wyników, choć w lipcu 2026 opublikowano kilka decyzji wydanych wcześniej); każda
  strona może być pusta (`,` = wszystko; `2026-01-01,` = od; `,2026-06-30` = do). Skróty: `1M,` `1Y,`.
  Filtr po dacie publikacji: warunek `date_publication:ge:RRRR-MM-DD` (działa; silnik: `--pub-od`);
  górną granicę (`--pub-do`) silnik sprawdza po stronie klienta i zawęża `timespan` do niej
  (ogłoszenie ≤ publikacja).
- `warunek` = `indeks:operator:wartość`. Operatory: `lt gt le ge eq ne in notin glob notglob regex`.
- Indeksy `PublicDocument`: `id time mtime kind refid refname status publicator_country
  publicator_type publicator_subtype publicator_year date_announcement date_publication
  keywords title_pl content_pl`.

**Zweryfikowane pułapki:**
- do pełnego tekstu (`content_pl`) używaj **`regex`, nie `glob`** — glob dopasowuje całą wartość
  i na wielolinijkowej treści zawodzi (`content_pl:glob:*biometri*` → 0; `content_pl:regex:biometr.*` → trafienia);
- regex działa **bez rozróżniania wielkości liter**;
- `order=-id` sortuje po `id`, które koduje datę OGŁOSZENIA — „najnowsze" = ostatnio wydane, nie
  ostatnio opublikowane (starsze decyzje dopisywane do portalu lądują głęboko w liście);
- **łańcuch warunków** (`/{warunek1}/{warunek2}`) zwraca 200, ale drugi warunek bywa ignorowany —
  silnik stosuje JEDEN warunek na zapytanie i uprzedza na stderr;
- `fields=keywords`/`publicator_year` potrafią zwrócić `null` (płaskie nazwy indeksów ≠ ścieżki
  w meta JSON) — po pełne dane sięgnij po `meta.json` konkretnego dokumentu;
- eksport CSV istnieje tylko jako przycisk UI — w API go nie ma (silnik: `--json`).

## Identyfikatory

- **Sygnatura** (refname): `DKN.5131.9.2025`, `DKE.561.1.2026`, `ZSOŚS.440.259.2019`.
- **Rekordy powiązane** (bez treści, `parts: 0`, `publication.status: published`): orzeczenia sądów
  `urn:ndoc:court:pl:sa:{rok}:{wydział}_{rodzaj}[-{miasto}]_{nr}` (`…:2019:ii_sa-wa_1030` = II SA/Wa 1030/19,
  `…:2021:iii_osk_3945` = III OSK 3945/21; bywa sufiks `_p`/`_p_RRRRMMDD`, miasto z diakrytykiem `sa-łd`),
  sądy powszechne `court:pl:sp:…`, TSUE `urn:ndoc:court:eu:tsue:{rok}:c_{nr}`, Dz.U. `urn:ndoc:pro:pl:durp:{rok}:{poz}`,
  akty UE `urn:ndoc:pro:eu:ojol:…`, EROD `urn:ndoc:gov:eu:edpb:…`. Ich `meta.json` istnieje (HTTP 200),
  `body.*` → 404. Treść wyroków WSA/NSA: skill prawo-pl-cbosa (`sygnatura "<sygn>"`).
- **URN** (refid): `urn:ndoc:gov:pl:uodo:{rok}:{sygnatura_bez_roku}` — małe litery,
  kropki→podkreślenia, polskie znaki transliterowane do ASCII
  (`DKN.5131.9.2025` → `urn:ndoc:gov:pl:uodo:2025:dkn_5131_9`;
  `ZSOŚS.440.259.2019` → `…:2019:zsoss_440_259`). Rok = ostatni 4-cyfrowy człon sygnatury.
- Wewnętrzne `id`: `PublicDocument-RRRRMMDD-000000-000-{32hex}` (data = ogłoszenie).

## Kształt metadanych (`meta.json`)

`type version id time languages name{pl} title{pl} refid refname kind publication{status
inforce version pubid} publicator{type subtype name country year extids[]} dates[{date use
type status scope refid text}] parts resources{} entities[] terms`.

- `publication.status` ∈ `final` (prawomocna wg portalu) | `nonfinal` (nieprawomocna) | `repealed`
  (uchylona — 3 decyzje w indeksie) | `published` (rekordy niebędące decyzjami). Indeks 2026-08:
  final 420, published 200, nonfinal 77, repealed 3.
- `publication.inforce` jest `true` TAKŻE dla decyzji uchylonych (ZSPR.421.2.2019: status `repealed`,
  inforce `true`) — **nie oznacza „w obrocie"**; silnik drukuje je jako „publication.inforce wg API".
- `dates[].use` ∈ `announcement` (data decyzji / orzeczenia) | `publication` (publikacja w portalu) |
  `validation` | **`repealed`** (uchylenie przez sąd — w całości lub w części, `refid` wyroku) |
  **`defended`** (skargę oddalono; `refid` wyroku) | **`trial`** (skarga w toku; `refid` sprawy) |
  `other` (na rekordach sądowych: „Data wpływu", bez refid).
- **Kontrola sądowa jest tylko w `dates[]`** — status `final` nie znaczy, że kara się ostała:
  ZSPR.421.3.2018 ma status `final`, a `dates[]` zawiera `repealed` 2019-12-11 (II SA/Wa 1030/19 —
  uchylony pkt 2 = kara 943 470 zł) i `defended` 2023-09-19 (III OSK 2538/21 — oddalono skargę kasacyjną
  od wyroku WSA). Zakres uchylenia wynika wyłącznie z sentencji wyroku (CBOSA). Silnik: blok
  „Kontrola sądowa", nagłówek „DECYZJA UCHYLONA PRZEZ SĄD", `--strict` blokuje takie decyzje.

## Mapowanie komend `uodo.py` → API

`najnowsze`→`/search/PublicDocument/,` + `order=-id`; `szukaj FRAZA`→warunek
`content_pl:regex:FRAZA`, `--tytul`→`title_pl:regex:…`, `--warunek`→przekazany wprost,
`--od/--do`→`{timespan}` (data OGŁOSZENIA), `--pub-od`→warunek `date_publication:ge:…` (gdy nie ma
innego warunku; inaczej filtr po stronie klienta), `--pub-do`→po stronie klienta + zawężenie `timespan`,
`--limit/--strona`→`count/from`; `decyzja`→`meta.json` + `meta.json` każdego wyroku z `dates[].refid`
(sygnatura i sąd) + `body.html` (część `:0`, `lang=pl`; awaryjnie `body.txt`).

## Wskazówki

- Wyniki wyszukiwania zawierają żądane `fields` — tytuł to opis redakcyjny przedmiotu decyzji
  (bywa bardzo długi; na rekordach sądowych to początek sentencji albo tezy); kwoty kar i podstawy
  prawne bierz z treści.
- **Treść: `body.html`, nie `body.txt`.** `body.txt` API gubi numerację list (`a)` → `a`, `2)` → `2`),
  usuwa odnośniki przypisów `[1]` z tekstu i drukuje każdy przypis DWA razy sklejony. `body.html`
  (ten sam HTML, który renderuje strona portalu `/document/<urn>/content`) ma numerację, odnośniki
  `<sup>[1]</sup>` i jeden blok `<div class="glosses">` — silnik parsuje go (stdlib `html.parser`)
  i tekst jest akapit w akapit zgodny ze stroną portalu (zweryfikowane na DKN.5131.34.2023).
- Portal publikuje też starsze decyzje z opóźnieniem (od 2025 r.; sygnatury 2018–2024 pojawiają się
  sukcesywnie) — brak w portalu ≠ decyzja nie istnieje. Dawne strony `uodo.gov.pl/decyzje/<sygnatura>`
  zwracają HTTP 500 (nie linkuj ich) — szukaj sygnatury w wyszukiwarce https://uodo.gov.pl.
- Bądź uprzejmy dla serwera: ≤2 zapytania/s (limitów nie udokumentowano).
- Dane publiczne; przy cytowaniu podawaj sygnaturę i datę decyzji.
