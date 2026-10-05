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
załącznika oznaczone „» "). Ogłoszony PDF (typ O) aktu Dz.U. i M.P. 1990–2011 to strony całego zeszytu w dwóch
łamach: helper czyta łamy po kolei, wycina akt od wiersza z numerem jego pozycji do numeru następnej, usuwa
nagłówki „Dziennik Ustaw Nr N — S — Poz. X"/„Monitor Polski Nr N — S — Poz. X", winietę i spis treści pierwszej
strony zeszytu („TREŚĆ: Poz.: …") oraz znak wodny www.rcl.gov.pl (2010–2011), a w 2000–2009 poprawia
polskie litery (fonty „…PL" w kodach Mac CE opisanych jako Mac Roman: „Za∏àcznik" → „Załącznik" — rozpoznawane po
treści strony, nie po roku: DU 2010 poz. 1 też je ma); tabele zostają w całości, stopka wydawnicza zeszytu
(„Egzemplarze bieżące…", „Wydawca:", ISSN, cena) nie trafia do ostatniego aktu — tak samo ogłoszenie wydawcy
przed nią („CENTRUM OBSŁUGI KANCELARII PREZESA RADY MINISTRÓW / WYDZIAŁ WYDAWNICTW I POLIGRAFII proponuje … Pełna
oferta", do dwóch stron przed stroną z ISSN; DU 2010 poz. 20 i 1128 — tam strona-reklama jest skanem, a po OCR jest
pomijana i nie liczy się jako tekst z OCR). Indeks górny w zeszycie 2000–2011 (`-layout` skleja „222" i podniesione
„1" w „2221") jest rozpoznawany z `-bbox` i zamieniany w miejscu na „222¹" (ta sama długość — łamy się nie
przesuwają), w wyniku „art. 222 1" jak w HTML; w OCR z lat 90. — nie. Wiersz z powtórzonym dywizem przy łamaniu
(„Środkowo-" + „-Wschodniej") jest sklejany w „Środkowo-Wschodniej". Przypisy pod kreską z dołu
łamu („———————" + „1) Minister Kultury kieruje…") wychodzą jako `[przypis 1)] …` pod akapitem z odsyłaczem, a nie
między łamami — także gdy kreska jest grafiką, a nie tekstem (DU 2010 poz. 1128 s. 1: kolejne „N)" na marginesie
łamu, odsyłacze sklejone z tekstem strony i fraza przypisu); treść przypisu, którego nie ma na stronie odsyłacza
ani obok, jest brana z innej strony, gdy jest tam jednoznaczna (t.j. k.p. DU 2026/1245: „odnośnik 46" na wielu
stronach — braków 23 → 7); podpis dosunięty do prawej krawędzi lewego łamu nie przestawia kolejności łamów (blok zamyka tylko
wiersz wyśrodkowany na stronie: numer pozycji, tytuł). Strony bez warstwy tekstowej (skany) są zgłaszane
(„N z M stron…"), a gdy jest `tesseract` (język `pol`) i `pdftoppm` — odczytywane OCR: do 10 stron same, więcej
z `--ocr` (300 dpi, `--psm 1`, ok. 7 s/stronę, równolegle; „$" przed cyfrą → „§"; wynik w
`$XDG_CACHE_HOME/prawo-pl-eli/ocr` lub `~/.cache/prawo-pl-eli/ocr`). Tekst z OCR jest oznaczony jako niepewny,
`--strict` go blokuje — tak samo jak tekst, w którym zostały strony bez warstwy tekstowej (niekompletny, np. z
`--bez-ocr`).

**Lata 1990–1999.** PDF zeszytu to skan z warstwą tekstową z OCR (`tekst` dopisuje to do nagłówka wyniku).
Helper rozpoznaje nagłówki stron zniekształcone przez OCR, z myślnikami ASCII albo bez nich („Dziennik Ustaw Nr 55
-   2134 -   Poz. 351 i 352", „Dzienn ik Ustaw Nr 98 ~ 3089 ~ Poz. 602", nagłówek rozbity na dwa wiersze),
łamy jednego aktu przesunięte o kilka znaków względem rynny strony, numer pozycji z okruchami OCR („463 .") lub
z jedną błędnie odczytaną cyfrą w numerze NASTĘPNEJ pozycji („600" zamiast „500"), sygnaturę pod numerem
(„Sygn. akt", „Rej . 184/94") i ogłoszenia wydawcy przed stopką („Pojedyncze egzemplarze… można nabywać",
„Uprzejmie informujemy…"). Pomiar na próbie 80 aktów z 1990–1999 (34 z HTML w API): w 24 aktach z HTML, które
nie są w całości skanem, odsetek słów HTML we właściwej kolejności 0,58 → 0,90, precyzja 0,34 → 0,88; w próbie
25 aktów z 2000–2011 żaden nie wypadł gorzej. NIE naprawia: przekłamań OCR
w literach i cyfrach („TRYBUNAtU", „1O") ani wyrazów rozbitych spacją („sk ładu", „zm ieniaj ące") — liczby,
daty i kwoty sprawdzaj w PDF; ok. 7% stron to skany bez warstwy tekstowej (5% aktów w całości — wtedy `tekst`
nie ma czego wypisać bez OCR — patrz wyżej `--ocr`, albo `--pdf`); część PDF-ów obejmuje tylko pierwsze strony zeszytu z aktem, więc koniec
aktu bywa poza plikiem; gdy numer pozycji SAMEGO aktu jest źle odczytany, wynik obejmuje cały PDF (z sąsiednimi
aktami), jak przed zmianą. PDF-y typu U i T (wybierane przed O) nie przechodzą przez tę ścieżkę.

PDF typu U (tekst ujednolicony Kancelarii Sejmu, nieurzędowy) to t.j. z WPISANYMI późniejszymi zmianami: notka
„Opracowano na podstawie: t.j. Dz. U. z 2026 r. poz. 468, 473, 830, 1003, 1046." na 1. stronie, brzmienie
zastępowane w `[ … ]`, brzmienie przyszłe w `< … >` i notka na prawym marginesie „Nowe brzmienie … wejdzie
w życie z dn. 5.11.2026 r. (Dz. U. … poz. …)". Helper czyta współrzędne słów (`pdftotext -bbox`): margines to
słowa za prawą krawędzią justowanego tekstu (wyznaczoną z wierszy prozy, więc tabela jej nie przesuwa), notka to
„pudełko" wierszy o wspólnym lewym brzegu (dwie nachodzące na siebie notki mają różne brzegi), także w liczbie
mnogiej („wejdą w życie", „utracą moc"), z ogonem bez frazy („(ust. 1 … wszedł w życie)") i do samego dołu strony
(data wydruku obok nie wchodzi do notki). Strona jest przycinana tuż przed kolumną notek (notka nie wpada
w przepis), notka wychodzi jako `[margines: …]` pod akapitem, przy którym stoi — a gdy wymienia artykuł („Dodany
art. 100a", „§ 2 w art. 125 1") albo rozdział/dział, którego akapit jest na tej samej stronie gdzie indziej, pod
tym artykułem/nagłówkiem. Indeks górny, który `-layout` skleja z liczbą („Art. 131." = art. 13¹), jest
rozpoznawany po współrzędnych (mniejszy, podniesiony) i wychodzi jak w HTML: „Art. 13 1.". Helper podaje
„Opracowano na podstawie" (także z kropką zamiast przecinka w liście: „poz. 13, 426. 737, 912.") i datę wydruku
w nagłówku, oznacza pozycje listy nowelizacji jako uwzględnione / spoza listy i ostrzega o brzmieniu, które
wejdzie w życie po dniu dzisiejszym — także bez „dn." („z 1.01.2027 r.") i bez daty („z dniem określonym
w komunikacie…" → „termin wejścia w życie nieznany — określi komunikat", traktowane jak przyszłe). Bez `pdftotext` helper sięga po najnowszy STARSZY t.j. z HTML — z nagłówkiem
„NIEAKTUALNE BRZMIENIE MOŻLIWE" i listą zmian aktu bazowego po jego `legalStatusDate`; `--strict` to blokuje.
Rozstrzelony tytuł („M IN I S TR A F IN AN SÓ W…", MP 2025 726) jest sklejany wg współrzędnych z zachowaniem
granic wyrazów („MINISTRA FINANSÓW I GOSPODARKI").

### Kody `type` w `texts[]`
- `H` — HTML (`text.html`)
- `O` — tekst ogłoszony / oryginał (PDF)
- `I` — tekst ogłoszony (skan/obraz, PDF)
- `T` — tekst jednolity (PDF)
- `U` — tekst ujednolicony Kancelarii Sejmu (PDF, nieurzędowy): t.j. + późniejsze zmiany, także przyszłe
  (`[obecne]` / `<przyszłe>` + data na marginesie) ← najaktualniejszy; do cytatu na dziś bierz brzmienie z `[ … ]`

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
  nowelizacji (maks. 10 żądań). Pozycje tej listy helper porównuje z OBWIESZCZENIEM z początku tekstu t.j. (PDF T
  i `text.html`): pkt 1 „z uwzględnieniem zmian wprowadzonych: 1) ustawą z dnia … (Dz. U. poz. 1046)" (cytat bez
  roku = rok aktu z „z dnia …") oraz „przepisów ogłoszonych przed dniem …" → „[UWZGLĘDNIONA w tym t.j.]" — t.j.
  obejmuje też zmiany, które wejdą w życie później (wtedy podaje oba brzmienia z przypisami „W tym brzmieniu
  obowiązuje do wejścia w życie zmiany, o której mowa w odnośniku N"); pkt 2 „nie obejmuje: … zmian wprowadzonych …"
  → „[NIE objęta tym t.j.]" (pozycje „art. N ustawy …" w pkt 2 to przepisy przejściowe, nie zmiany).
- **„Podstawa prawna" / „Podstawa prawna z art."** — delegacje/podstawy.

## Wskazówki

- Aby ustalić AKTUALNY stan przepisu: akt bazowy → `references` „Inf. o tekście jednolitym" (weź najnowszy) → na nim sprawdź „Nowelizacje po tekście jednolitym" ORAZ „Akty zmieniające" aktu bazowego po `legalStatusDate` t.j. Komendy `tj`/`tekst` robią to automatycznie.
- `struct` pokazuje układ aktu i id jednostek, ale `text.html/{tree}` jest blokowany przez WAF dla artykułów — pojedynczy przepis pobieraj przez `tekst --fragment "art. N"` (lokalnie wycina z pełnego tekstu).
- Tekst z `text.html` zawiera twarde spacje (NBSP) — helper normalizuje je do zwykłych spacji. Indeks górny siedzi w `<sup>`, więc po konwersji jest odspacjowany (art. 299¹ → „Art. 299 1."); w `--fragment` podawaj go jako `"art. 299(1)"` albo `"art. 299¹"`.
- W nagłówku przepisu niedawno dodanego lub zmienionego stoi ODSYŁACZ DO PRZYPISU (też w `<sup>`), a treść przypisu API wstawia INLINE — w surowym HTML wygląda to tak: „Art. 66c 6)Dodany przez art. 3 pkt 2 ustawy… . Kto uporczywie…". Kropka artykułu stoi dopiero za przypisem, więc nagłówek ≠ „Art. N." — `--fragment` to obsługuje (`_KONIEC_ART` w `eli.py`).
- `tekst --fragment "§ N"` trafia w nagłówek paragrafu („§ 4." w HTML, „Art. 25. § 1." albo „§ 2." na początku
  wiersza w tekście z PDF) i tnie do następnego paragrafu, artykułu lub jednostki wyższej; „art. N § M" — paragraf
  w artykule. Paragrafy w cudzysłowie otwartym przed nimi (nowe brzmienie w akcie zmieniającym: w HTML „„" stoi
  w osobnym wierszu) nie są nagłówkami tego aktu. W akcie z artykułami „§ N" ma wiele trafień — każde z artykułem.
- Przypisy fragmentu odsyłające do innych przypisów („…ustawy, o której mowa w odnośniku 6") helper rozwija (do 4
  kroków): datę wejścia w życie bierze z przypisu docelowego, przypis „W tym brzmieniu obowiązuje do wejścia w życie
  zmiany…" opisuje jako brzmienie obowiązujące DO tej daty, a przypisy spoza fragmentu wypisuje pod nim.
- Blok `<div class="gloss-section">` pod treścią `text.html` powtarza przypisy z odsyłaczy (`<DIV CLASS="gloss"
  ID="gloss-0:3:"><div>3)</div><div>treść</div>`) — helper go pomija (przypis bez odsyłacza w treści wychodzi jako
  `[przypis N)]`); `<button>` „Pokaż całość" nie jest treścią; trzy `<span class="head-…">` w `<h1>` (rodzaj, data,
  tytuł) wychodzą w osobnych wierszach.
- Odsyłacz do przypisu siedzi w `<a class="gloss-link tooltip"><sup>N)</sup><span class="tooltip-text">…</span></a>` WEWNĄTRZ numeru jednostki (`<h3>2<a…><sup>1)</sup>…</a>)</h3>` = pkt 2 z przypisem 1; `§ 1<a…><sup>12)</sup>…</a>.`). Helper NIE przepisuje numeru odsyłacza do tekstu (wychodziło „2 1)", „a 2)", „§ 1 12)" — cyfra przypisu wchodziła w numer jednostki), a treść przypisu wynosi do osobnej linii `[przypis N)] …` za najbliższą granicą bloku, bo inaczej komentarz redakcyjny („Dodany przez…", „W tym brzmieniu obowiązuje do…") jest nieodróżnialny od normy; etykieta jest konieczna także dlatego, że część przypisów zaczyna się od „Art. 598…" / „Tytuł działu…" i na początku linii udawałaby nagłówek jednostki. Indeks górny artykułu (goły `<sup>` poza odsyłaczem) nadal dostaje spację („Art. 449 1." ≠ „Art. 4491."). **Linia `[przypis N)]` to jedyny fragment wyniku, którego NIE ma w urzędowym tekście** — nie cytuj jej jako przepisu.
- Adres ISAP (`WDU{rok}{tom}{poz}` / `WMP...`) i ELI (`DU/{rok}/{poz}`) są równoważnymi identyfikatorami — helper przyjmuje obie formy.
- `/struct` istnieje głównie dla tekstów jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają (HTTP 404 — helper `struktura` zgłasza „Brak struktury…" z kodem ≠ 0, nie surowy błąd HTTP).
- Helper trzyma w pamięci udane GET-y bez parametrów w obrębie jednego uruchomienia (odniesienia aktu bazowego są potrzebne dwa razy), żeby nie drażnić zapory powtórzonymi żądaniami.
