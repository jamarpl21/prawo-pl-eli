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
Zapytanie bez `title` (np. `publisher=MP&year=2026`) API przyjmuje (2026-11-13: 1139 pozycji). `keyword` to hasło
ze słownika `/keywords` (2477 haseł): porównanie bez względu na wielkość liter, ale z polskimi znakami
(„Sygnaliści" → 2, „sygnalisci" → 0); hasło spoza słownika daje `totalCount` 0 bez żadnego błędu — helper
sprawdza wtedy słownik i zgłasza zły argument (kod 2). Odpowiedź jest sortowana tak, że akt bazowy bywa na końcu
(„Kodeks karny", `type=Ustawa`: 101 trafień, DU/1997/553 na pozycji 92); `sortBy` przyjmuje tylko `publisher`,
`position`, `title`, `change`. `limit` powyżej 500 API obcina do 500.

## Pola metadanych aktu (`/acts/{pub}/{year}/{pos}`)

`title`, `type`, `status`, `inForce` (`IN_FORCE`/…), `keywords`/`keywordsNames`, `references`, `ELI`,
`address` (ISAP), `displayAddress` (np. „Dz.U. 2024 poz. 18"), `volume`, `texts` (lista plików),
`textHTML`/`textPDF` (bool), `changeDate` (ostatnia zmiana rekordu w bazie — NIE data prawna).

### Daty (zweryfikowane na żywym API i nagłówkach PDF Dz.U., 2026-08)
| Pole | Znaczenie | Przykład DU/2024/928 |
|---|---|---|
| `announcementDate` | **data AKTU** = wydania/podpisania, czyli „z dnia …" w tytule (ISAP: „Data wydania") — NIE ogłoszenie | 2024-06-14 |
| `promulgation` | **data OGŁOSZENIA** w Dz.U./M.P. (nagłówek PDF „Warszawa, dnia …"; ISAP: „Data ogłoszenia"); od niej liczy się vacatio legis; bywa `null` dla starych pozycji (Konstytucja DU/1997/483) | 2024-06-24 |
| `entryIntoForce` | wejście w życie aktu (ogólna reguła); `null` dla obwieszczeń z t.j. — ale też dla ustaw i rozporządzeń z terminem „określonym w komunikacie / decyzji Komisji Europejskiej" (DU/2026/1218, DU/2025/267, DU/2024/1688; termin stoi tylko w `comments`) i dla części aktów z datą tylko w przepisie o wejściu w życie (DU/2025/845, DU/2024/635, DU/2017/1972) — helper ostrzega (próba 2026-11-13) | 2024-09-25 |
| `comments` | uwagi ISAP — najczęściej **rozłożone wejście w życie** („art. 5 ust. 4 … wchodzą w życie z dniem 25 grudnia 2024 r.") | jw. |
| `legalStatusDate` | **stan prawny t.j.** — obwieszczenie mówi albo „z uwzględnieniem stanu prawnego na dzień X", albo „zmian … z przepisów ogłoszonych przed dniem X" (wtedy akt ogłoszony w dniu X NIE jest w t.j.). Pomiar 2026-11-13, 15 t.j.: 9 z datą w PDF — 8 zgodnych, k.p.c. DU/2026/468: API 2026-03-25, obwieszczenie „24 marca 2026 r."; brak pola: DU/2023/2631, DU/2026/1055 (obwieszczenie bez daty), DU/1998/787, DU/2005/2143, DU/2010/1623 (PDF: „przed dniem 5 listopada 2010 r."). Helper liczy zmiany „po t.j." od ogłoszenia ≥ X lub wejścia w życie > X, bez pola — od daty poprzedniego t.j., a w `tekst` porównuje X z obwieszczeniem | DU/2026/795 → 2026-05-19 |
| `validFrom` | obowiązywanie (gdy różne od wejścia w życie) | — |
Helper `meta` drukuje je jako „Data aktu" / „Ogłoszono" / „WEJŚCIE W ŻYCIE" / „Stan prawny na" / „Uwagi".
M.P. z różną datą aktu i ogłoszenia (pomiar 2026-11-13, 11 pozycji): `promulgation` zgodne z nagłówkiem PDF
„Warszawa, dnia …" w 5 z 5 pozycji od 2012 r. (MP/2015/1306, 2020/1213, 2026/500, 1137, 1138) i w 3 z 3 sprawdzalnych
z lat 2000–2011 (MP/2003/500 — data aktu 2003-03-10, ogłoszono 2003-07-15, nagłówek zeszytu Nr 36; MP/2009/1039
i 2011/1198 — data podpisu elektronicznego zeszytu); PDF pozycji sprzed 2012 r. to wycinek zeszytu, zwykle bez
nagłówka z datą (MP/2003/937 — nie do sprawdzenia). M.P. z lat 90. nie ma `promulgation` w API (MP/1995/400,
1999/300) — helper pisze „— (brak w API)".
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
stronach — braków 23 → 7), ale w załączniku tylko z najwyżej 3 stron DALEJ (formularze i tabele mają własne
przypisy „1)", „2)"; dawniej odsyłacz z formularza dostawał przypis 1 z pierwszej strony aktu); podpis dosunięty do prawej krawędzi lewego łamu nie przestawia kolejności łamów (blok zamyka tylko
wiersz wyśrodkowany na stronie: numer pozycji, tytuł). Strony bez warstwy tekstowej (skany) są zgłaszane
(„N z M stron…"), a gdy jest `tesseract` (język `pol`) i `pdftoppm` — odczytywane OCR: do 10 stron same, więcej
z `--ocr` (300 dpi, `--psm 1`, ok. 7 s/stronę, równolegle; „$" przed cyfrą → „§"; wynik w
`$XDG_CACHE_HOME/prawo-pl-eli/ocr` lub `~/.cache/prawo-pl-eli/ocr`). Tekst z OCR jest oznaczony jako niepewny,
`--strict` go blokuje — tak samo jak tekst, w którym zostały strony bez warstwy tekstowej (niekompletny, np. z
`--bez-ocr`). Nagłówek paragrafu odczytany przez OCR jako „8 N." / „8N." jest poprawiany na „§", gdy potwierdza go
ciąg numeracji; wariant ze śmieciem przed numerem, przecinkiem albo „3"/„5" zamiast „§" („„8 12,", „. 817.", „5 2,",
„3 16.") — tylko gdy tuż przed nim stoi § N−1, a tuż za nim § N+1.

**Odsyłacze do przypisów w PDF z warstwą tekstową (od 2012 r. i teksty T/U).** `-layout` nie odróżnia odsyłacza
(indeksu górnego) od cyfr tekstu, więc helper bierze go ze współrzędnych `-bbox`: osobne słowo „N)" (albo „N" + „)"),
niższe od słowa tuż z lewej i podniesione — pdftotext zaczyna nowe słowo przy zmianie wielkości czcionki. Taki
odsyłacz jest usuwany dokładnie co do cyfr („0,01" + „2)" → „0,01", a nie „0," z przypisem „012)"; „art. 92" +
„10)" → „art. 92"), także gdy `-layout` wydrukował go w osobnym wierszu nad wierszem, do którego należy (k.c. art.
179: „(utracił moc)2)") albo gdy dwa stoją obok siebie („§ 1.6) 7)", „Art. 1.2) 3)" — drugi tej samej wysokości co
pierwszy, przerwa do 12 pt w wierszu justowanym). Cyfry i „)" w jednym słowie bbox z poprzedzającym
znakiem („SpO2),", „(E561)", „(WGS84)") to tekst — zostają. Gdy współrzędne milczą (zeszyty 1990–2011, OCR), działa
dawna reguła kształtu. Gdy `pdftotext -bbox` nie dał tylu stron co `-layout` (awaria), oznaczanie jest pomijane
(też indeksów górnych), a `tekst` ostrzega „UWAGA: pdftotext -bbox nie zwrócił współrzędnych…" i `--strict` blokuje
(kod 2) — dawna reguła kształtu zjada cyfry liczby przed przypisem („152,61" + „3)" → „152,"); zeszyty 1990–1999
(OCR) współrzędnych nie używają, więc tam ostrzeżenia nie ma. Przypis spod tekstu strony, do którego żaden odsyłacz
nie trafił, nie ginie, ale wychodzi jako `[przypis N) — bez odsyłacza w treści tej strony; nie przypisuj go do
sąsiedniego przepisu] …` przed najbliższą jednostką (artykuł, paragraf, ustęp, nagłówek) tej albo następnej strony,
a nie w środku wyliczenia; ostrzeżenia o przypisach fragmentu (wyrok TK, przepisy, które jeszcze nie obowiązują,
przypis przy nagłówku rozdziału) podają go osobno jako „przypis nieprzypisany do jednostki". Na stronach obwieszczenia
(przed „Załącznik do obwieszczenia") punkt wyliczenia na dole strony („5) art. 48 ustawy … który stanowi: „Art. 48.
Ustawa wchodzi w życie…”") nie jest brany za blok przypisów, gdy zaczyna się małą literą albo „§" i nie ma
potwierdzonego odsyłacza — zostaje wierszem „» " obwieszczenia. Blok przypisów pod co najmniej dwoma pustymi wierszami, którego numery mają odsyłacze
potwierdzone współrzędnymi, jest rozpoznawany także wtedy, gdy zaczyna się dokończeniem przypisu z poprzedniej
strony albo ma puste wiersze w środku (k.p.c. art. 49–50: przypisy o wyrokach TK P 13/19 i P 10/19 trafiały do
art. 50 § 2). Znak U+FEFF przed nagłówkiem strony („Dziennik Ustaw –2– Poz. …") nie blokuje już jego usunięcia.

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
- **„Orzeczenie TK"** — wyroki TK (i obwieszczenia Prezesa TK z lat 90.) DOTYCZĄCE aktu, bez `date` i bez numerów
  artykułów; obejmuje też wyroki „zgodny" i wyroki o innych aktach powołujących ten akt (k.c.: 25 pozycji, m.in.
  P 46/09 o art. 2 ustawy zmieniającej k.c.). Wyrok, który coś zmienił, jest też w **„Akty zmieniające"** z `date`
  = dniem skutku, także odroczonego (SK 109/20: ogłoszony 2023-12-28, `date` 2024-12-29 = przypis t.j. „Z dniem
  29 grudnia 2024 r."); w k.c. 7 z 25 wyroków ma taką pozycję. Sam wyrok (`DU/2021/175`) ma „Akty zmienione"
  z tą samą datą i „Orzeczenie TK dla aktu". Treść sentencji jest w `text.html` wyroku — wszystkie wyroki TK
  w Dz.U. 1997–2024 mają HTML (0 bez HTML w 434 pozycjach z 8 roczników 1997–2019); od 2025 r. w Dz.U. nie ma żadnego wyroku TK.
- **„Akty uchylone"**, **„Odesłania"**.

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

Akt **bez** tekstu jednolitego ma tylko własne „Akty zmieniające": jego `text.html` i PDF ogłoszony to brzmienie
pierwotne (np. DU/2006/550 — 4 zmiany, ostatnia DU/2026/1340), a PDF typu U, jeśli jest, to tekst ujednolicony
z listą „Opracowano na podstawie" (DU/2025/1840).

**Które artykuły zmienia nowelizacja — API tego nie podaje** (pole `art` w odniesieniach jest puste). Helper
odczytuje to z treści aktu zmieniającego: przepis „W ustawie z dnia … (Dz. U. …)" rozpoznaje po PIERWSZEJ
pozycji cytatu (akt albo jego t.j.; dalsze pozycje to jego nowelizacje — DU 1996/646 art. 2 zmienia k.c.,
a w cytacie ma 1993 Nr 17 poz. 78 = ustawę o planowaniu rodziny), a bez cytatu — po dacie i tytule z granicą
wyrazu (k.k., k.k.w. i k.p.k. są „z dnia 6 czerwca 1997 r."); polecenia czyta poza cudzysłowami „…”. Pomiar
2026-11-13: 26 par akt zmieniający → akt bazowy (1995–2009 i 2021 z HTML, 2026 z PDF) — 124 z 124 artykułów
zgodnie z ręcznym odczytem treści, 0 fałszywych. Ustawy z lat 1991–2010 mają HTML (sprawdzone 6 roczników);
tekstu z OCR helper do tej analizy nie używa.

## Wskazówki

- Aby ustalić AKTUALNY stan przepisu: akt bazowy → `references` „Inf. o tekście jednolitym" (weź najnowszy) → na nim sprawdź „Nowelizacje po tekście jednolitym" ORAZ „Akty zmieniające" aktu bazowego po `legalStatusDate` t.j. Komendy `tj`/`tekst` robią to automatycznie. Kontrola krzyżowa (2026-11-13): wszystkie 130 ustaw ogłoszonych od 2026-03-16, ich „Akty zmienione" porównane z „Akty zmieniające" k.c., k.p.c. i k.p.k. — 10 z 10 pozycji zgodnych w obie strony (po 2026-05-19 nic nie zmieniło k.c.).
- `struct` pokazuje układ aktu i id jednostek, ale `text.html/{tree}` jest blokowany przez WAF dla artykułów — pojedynczy przepis pobieraj przez `tekst --fragment "art. N"` (lokalnie wycina z pełnego tekstu). `/struct` jest tylko dla aktów z HTML (teksty jednolite od 2025 r. go nie mają). Porównanie z PDF tych samych t.j. (13.11.2026, 7 aktów: k.p.c. DU 2024/1568, k.s.h. 2024/18, k.p.a. 2024/572, k.r.o. 2023/2809, DU 2024/499, 2023/2111, 2023/2190): liczba ksiąg, tytułów, rozdziałów, oddziałów i artykułów zgodna, działów — poza jednym. Błędy danych w k.p.c. DU 2024/1568: art. 15 ma w HTML i `/struct` nagłówek „Art. 1522." (w PDF „Art. 15."), a w `/struct` brak „DZIAŁ IIda" (art. 795¹¹ᵃ i nast. — w PDF jest). Artykuły uchylone zbiorczo („Art. 596–609.") mają w `/struct` jeden węzeł.
- W HTML API tabela, wzór albo rysunek bywają zastąpione samym odsyłaczem „patrz oryginał" (link do PDF) — 6 z 30 losowych aktów Dz.U. 2024 z HTML (np. t.j. DU 2023/2190: załącznik z dietami zagranicznymi). `tekst` to zgłasza, a `--strict` blokuje.
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
- `/struct` istnieje głównie dla tekstów jednolitych i starszych aktów; świeżo ogłoszone pozycje często go nie mają (HTTP 404 — helper `struktura` zgłasza „Brak struktury…" z kodem 1, gdy metadane aktu istnieją, a z kodem 2, gdy nie ma i ich — 404 na `/struct` wygląda tak samo w obu przypadkach).
- Kody wyjścia helpera: 0 — wynik; 1 — zweryfikowany brak (zero trafień, frazy nie ma w pobranym tekście, filtr `struktura` bez trafień); 2 — błąd sieci/HTTP, nieoczekiwana odpowiedź, zła sygnatura lub argument, blokada `--strict`, nieustalona aktualność, nieprzewidziany błąd.
- Helper trzyma w pamięci udane GET-y bez parametrów w obrębie jednego uruchomienia (odniesienia aktu bazowego są potrzebne dwa razy), żeby nie drażnić zapory powtórzonymi żądaniami.
