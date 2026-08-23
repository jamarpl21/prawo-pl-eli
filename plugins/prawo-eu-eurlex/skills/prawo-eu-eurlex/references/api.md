# CELLAR/EUR-Lex — ściąga API (dla zapytań spoza komend eurlex.py)

Wszystko publiczne, bez klucza. Read-only.

## Endpointy

- **SPARQL**: `https://publications.europa.eu/webapi/rdf/sparql`
  POST/GET z `query=` + `format=application/sparql-results+json` (też XML/CSV).
- **CELLAR REST (treść)**: URI zasobu ma postać
  `http://publications.europa.eu/resource/celex/{CELEX}`, ale żądanie sieciowe wysyłaj przez
  `https://publications.europa.eu/resource/celex/{CELEX}`. `eurlex.py` podnosi schemat do HTTPS
  tuż przed pobraniem, także dla URL manifestacji zwróconych przez SPARQL **oraz w celach
  przekierowań** — CELLAR odpowiada 303 z `Location: http://…` nawet na żądanie https, więc
  samo podniesienie URL wejściowego nie wystarcza (treść przyszłaby czystym HTTP). Używa nagłówków
  `Accept: application/xhtml+xml` i `Accept-Language: pol|eng|…` (kod 3-literowy, małymi).
  PDF NIE działa przez negocjację — pobierz URL manifestacji przez SPARQL
  (`cdm:manifestation_manifests_expression`, `cdm:manifestation_type` zaczynający się od `pdf`)
  i doklej `/DOC_1`.
- **Struktura XHTML** (do cięcia fragmentów): artykuł = `div.eli-subdivision#art_N`; akt bazowy —
  formuła końcowa i podpisy w `div.oj-final` (`p` „Sporządzono w…", `div.oj-signatory`), przypisy
  po `hr.oj-note` jako `p.oj-note`; wersja skonsolidowana — formuła w `div#fnp_1`, przypisy jako
  `p.footnote`, znaczniki zmian `p.modref` (▼B/▼M1), załączniki `div#anx_I` (`p.title-annex-1`).
  `eurlex.py` wstawia przed `oj-signatory`/`oj-note`/`footnote` znak granicy (U+001F), usuwany
  przed wydrukiem.
- **ELI URI**: `http://data.europa.eu/eli/reg/2016/679/oj` → przekierowanie na EUR-Lex.

## Ontologia CDM (prefiks `cdm: <http://publications.europa.eu/ontology/cdm#>`)

Najużyteczniejsze właściwości (zweryfikowane):

- `cdm:resource_legal_id_celex` — numer CELEX (literal `xsd:string`); klucz wyszukiwania.
- `cdm:work_has_resource-type` — typ aktu: URI `…/resource-type/REG|DIR|DEC|…`
- `cdm:work_date_document` (data aktu), `cdm:resource_legal_date_signature`,
  `cdm:resource_legal_date_entry-into-force` — bywa KILKA wartości: wejście w życie ORAZ daty
  rozpoczęcia stosowania (RODO: 2016-05-24, 2018-05-25; AI Act: 5 dat). **CDM nie ma osobnej
  właściwości „data stosowania"** ani opisu poszczególnych dat (EUR-Lex pokazuje je z komentarzem
  tylko na stronie notatki) — sprawdzone na 32016R0679/32024R1689 przez
  `SELECT ?p ?o { ?w ?p ?o FILTER(CONTAINS(STR(?p),"date")||CONTAINS(STR(?p),"applic")) }`.
  Po nowelizacji CELLAR NIE aktualizuje tych dat w akcie bazowym (32024R1689 dalej ma 2027-08-02,
  choć 32026R1744 zmienił art. 113). `cdm:resource_legal_date_end-of-validity` (9999-12-31 =
  bezterminowo). `cdm:resource_legal_date_deadline` — inne terminy z aktu (przeglądy, sprawozdania),
  nie stosowanie.
- `cdm:directive_date_transposition` — termin(y) transpozycji dyrektywy (32019L1937: 2021-12-17,
  2023-12-17; 31995L0046: 1998-10-24). Brak w CDM `resource_legal_date_transposition`.
- `cdm:resource_legal_in-force` — "1"/"true" = obowiązuje.
- `cdm:resource_legal_eli` — URI ELI.
- wersja skonsolidowana (typ `CONS_TEXT`, klasa `cdm:act_consolidated`):
  `cdm:act_consolidated_date` = „stan na" (to samo co `work_date_document` i
  `resource_legal_date_entry-into-force` tej pracy — NIE daty aktu!),
  `cdm:act_consolidated_based_on_resource_legal` → akt bazowy,
  `cdm:act_consolidated_consolidates_resource_legal` → akty ujęte w konsolidacji (bazowy +
  zmieniające + sprostowania). Konsolidacja nie ma `resource_legal_in-force` — status czytaj z aktu
  bazowego.
- tytuły per język: `?exp cdm:expression_belongs_to_work ?w . ?exp cdm:expression_uses_language
  <…/authority/language/POL> . ?exp cdm:expression_title ?title`
- relacje (kierunek ma znaczenie): `cdm:resource_legal_amends_resource_legal` (X zmienia W —
  nowelizacje szukaj ODWROTNIE: `?x …amends… ?w`), `cdm:resource_legal_corrects_resource_legal`
  (sprostowania), `cdm:resource_legal_based_on_resource_legal` (podstawa traktatowa),
  `cdm:resource_legal_repeals_resource_legal` (X uchyla W; 32016R0679 → 31995L0046; „uchylony
  przez" = odwrotnie `?x …repeals… ?w`), `cdm:resource_legal_implicitly_repeals_resource_legal`
  (uchylenie dorozumiane; 32016R0679 → 32003R1882), `cdm:resource_legal_proposes_to_amend_resource_legal`
  (projekty COM w toku — sektor 5; eurlex.py ich nie listuje).

## Numery CELEX

`[sektor][rok][litera typu][numer]`, np. `32016R0679`:
- sektory: 1 = traktaty (`12012E/TXT` TFUE, `12012P/TXT` Karta), 2 = umowy międzynarodowe,
  3 = legislacja (R = rozporządzenie, L = dyrektywa, D = decyzja), 5 = prace przygotowawcze
  (COM…), 6 = orzecznictwo TSUE (CJ = wyroki), 0 = **wersje skonsolidowane**.
- wersja skonsolidowana: `0` + rok/litera/numer aktu bazowego + `-YYYYMMDD` (stan na dzień),
  np. `02016R0679-20160504`. Lista: SPARQL `FILTER(STRSTARTS(STR(?celex), "02016R0679-"))`.
  CELLAR/EUR-Lex listują wszystkie wersje, ale treści wersji ZASTĄPIONYCH bywają wycofane
  (REST 404, np. `02024R1689-20240712`, `02019L1937-20191126` — zwykle pierwsza, tożsama z aktem
  bazowym); nowsze wersje pośrednie zwykle są serwowane.
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
