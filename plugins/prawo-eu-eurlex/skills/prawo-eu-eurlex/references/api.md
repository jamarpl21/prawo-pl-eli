# CELLAR/EUR-Lex — ściąga API (dla zapytań spoza komend eurlex.py)

Wszystko publiczne, bez klucza. Read-only.

## Endpointy

- **SPARQL**: `https://publications.europa.eu/webapi/rdf/sparql`
  POST/GET z `query=` + `format=application/sparql-results+json` (też XML/CSV).
- **CELLAR REST (treść)**: URI zasobu ma postać
  `http://publications.europa.eu/resource/celex/{CELEX}`, ale żądanie sieciowe wysyłaj przez
  `https://publications.europa.eu/resource/celex/{CELEX}`. `eurlex.py` podnosi schemat do HTTPS
  tuż przed pobraniem, także dla URL manifestacji zwróconych przez SPARQL. Używa nagłówków
  `Accept: application/xhtml+xml` i `Accept-Language: pol|eng|…` (kod 3-literowy, małymi).
  PDF NIE działa przez negocjację — pobierz URL manifestacji przez SPARQL
  (`cdm:manifestation_manifests_expression`, `cdm:manifestation_type` zaczynający się od `pdf`)
  i doklej `/DOC_1`.
- **ELI URI**: `http://data.europa.eu/eli/reg/2016/679/oj` → przekierowanie na EUR-Lex.

## Ontologia CDM (prefiks `cdm: <http://publications.europa.eu/ontology/cdm#>`)

Najużyteczniejsze właściwości (zweryfikowane):

- `cdm:resource_legal_id_celex` — numer CELEX (literal `xsd:string`); klucz wyszukiwania.
- `cdm:work_has_resource-type` — typ aktu: URI `…/resource-type/REG|DIR|DEC|…`
- `cdm:work_date_document`, `cdm:resource_legal_date_entry-into-force` (bywa KILKA wartości:
  wejście w życie + data stosowania), `cdm:resource_legal_date_end-of-validity` (9999-12-31 = bezterminowo).
- `cdm:resource_legal_in-force` — "1"/"true" = obowiązuje.
- `cdm:resource_legal_eli` — URI ELI.
- tytuły per język: `?exp cdm:expression_belongs_to_work ?w . ?exp cdm:expression_uses_language
  <…/authority/language/POL> . ?exp cdm:expression_title ?title`
- relacje (kierunek ma znaczenie): `cdm:resource_legal_amends_resource_legal` (X zmienia W —
  nowelizacje szukaj ODWROTNIE: `?x …amends… ?w`), `cdm:resource_legal_corrects_resource_legal`
  (sprostowania), `cdm:resource_legal_based_on_resource_legal` (podstawa traktatowa).

## Numery CELEX

`[sektor][rok][litera typu][numer]`, np. `32016R0679`:
- sektory: 1 = traktaty (`12012E/TXT` TFUE, `12012P/TXT` Karta), 2 = umowy międzynarodowe,
  3 = legislacja (R = rozporządzenie, L = dyrektywa, D = decyzja), 5 = prace przygotowawcze
  (COM…), 6 = orzecznictwo TSUE (CJ = wyroki), 0 = **wersje skonsolidowane**.
- wersja skonsolidowana: `0` + rok/litera/numer aktu bazowego + `-YYYYMMDD` (stan na dzień),
  np. `02016R0679-20160504`. Lista: SPARQL `FILTER(STRSTARTS(STR(?celex), "02016R0679-"))`.
- sprostowanie: sufiks `R(nn)`, np. `32016R0679R(02)`.

## Języki (authority codes)

`POL ENG DEU FRA SPA ITA CES SLK NLD POR …` — w `Accept-Language` małymi (`pol`),
w SPARQL pełny URI `…/authority/language/POL`.

## Limity i wydajność

- SPARQL: zapytania z `CONTAINS` po tytułach trwają 1–3 s; zawężaj przez `--typ`/`--rok`.
  Endpoint potrafi zwrócić 5xx pod obciążeniem — ponów po chwili.
- Wyszukiwarka pełnotekstowa EUR-Lex to osobny webservice SOAP (wymaga rejestracji EU Login,
  limit 10 000 wyników od 2026) — eurlex.py jej nie używa.
- Masowe pobrania: bulk download / Data Dump Urzędu Publikacji (nie rób crawl po REST).
