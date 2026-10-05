# Historia zmian

## Niewydane

## 2.2.0 — 2026-10-05

### Modele i dokumentacja

- Skille są optymalizowane i testowane pod modele Claude Fable 5.1 i Claude Opus 5.5: oba przeszły ten sam zestaw 10 zadań z pułapkami wykrytymi w tym wydaniu z wynikiem 10/10 (wybór skilla, ostrzeżenia, brak cytowania z pamięci, poprawność) — opis w `docs/test-modeli-2026-10.md` i w README.
- README, instrukcja Claude Desktop (osiem skilli), opisy pluginów i AGENTS.md zaktualizowane do stanu 2.2.0; raport audytu z sierpnia ma notę o stanie punktów z „Luk”.

### ELI — tekst ujednolicony i przepisy, które jeszcze nie obowiązują

- `tekst` dla kodeksów czytanych z PDF tekstu ujednoliconego Kancelarii Sejmu (m.in. k.p.c., k.k., u.k.s.c., Konstytucja) nie wkleja już notek z marginesu w treść przepisów. Dotąd `tekst DU 2026 468 --fragment "art. 461"` zwracał zdanie przetykane notką „…stosunku § 1 1 w art. 461 wejdzie w życie z pracy…". Notka wychodzi teraz osobno jako `[margines: Nowe brzmienie … wejdzie w życie z dn. 5.11.2026 r. (Dz. U. … poz. 1046).]`, a brzmienie obecne `[ … ]` i przyszłe `< … >` są osobnymi akapitami.
- Nagłówek takiego tekstu mówi, że to nieurzędowy tekst ujednolicony z datą wydruku i listą „Opracowano na podstawie”. Dotąd był podpisany samą datą stanu prawnego t.j., a lista „zmian po t.j.” kazała nanosić ręcznie zmiany, które w tekście już są. Pozycje tej listy są teraz oznaczone jako uwzględnione albo spoza podstawy tekstu.
- Przed tekstem i fragmentem pojawia się ostrzeżenie „PRZEPISY, KTÓRE JESZCZE NIE OBOWIĄZUJĄ” z datami — z notek tekstu ujednoliconego i z przypisów t.j. „wejdzie w życie z dniem …” (np. k.c. art. 860 § 3–4 od 1.11.2028), także gdy fragment leży wewnątrz dodanego działu (k.p.c. dział IVFA od 28.10.2026). `--fragment` znajduje artykuły dodane lub zmienione w nawiasach (`<Art. 477⁶ᵃ.`).
- `meta` i `tekst` ostrzegają, gdy akt jeszcze nie wszedł w życie (np. DU 2026/1046 — status API „obowiązujący”, wejście w życie 5.11.2026) albo gdy część przepisów wchodzi w życie później według „Uwag”. `meta` podaje urzędową formę cytatu („Dz. U. z 2001 r. Nr 112, poz. 1198”) i, przy akcie uchylonym, akt uchylający.

- Notki o przyszłym brzmieniu są oddawane w całości także w liczbie mnogiej („wejdą w życie”, „utracą moc”), na dole strony, przy tabelach i gdy dwie notki na siebie nachodzą, i przypinane do artykułu, którego dotyczą. W przeglądzie wszystkich 186 obowiązujących tekstów ujednoliconych bez HTML poprawnie oddanych jest 1119 z 1120 notek (pierwsza wersja poprawki: 814), a w żadnym akcie notka nie wpada w treść przepisu (było 47 aktów, m.in. Ordynacja podatkowa, k.p.k., k.k.s., p.p.s.a.).
- Notka „wejdzie w życie z dniem określonym w komunikacie” daje ostrzeżenie „termin wejścia w życie nieznany”; przypis przy nagłówku rozdziału (np. k.wyb. rozdział 11b) ostrzega przy każdym artykule tego rozdziału.
- Indeks górny sklejony w PDF jest rozpoznawany (art. 13¹ nie udaje już „Art. 131.”), a zapis z `struktura` („art. 7_1”) działa w `--fragment`; `struktura` podaje gotową postać „art. 7(1)”. Lista „Opracowano na podstawie” nie gubi pozycji po kropce w środku, a rozstrzelony tytuł („M IN I S TR A F IN AN SÓ W”) jest sklejany.

- `--fragment "§ N"` i `--fragment "art. N § M"` wycinają dokładnie jeden paragraf. Dotąd w rozporządzeniach fragment zaczynał się w środku wyrazu albo od końca poprzedniego paragrafu (np. `tekst DU 2017 1692 --fragment "§ 4"` dawał 1609 słów zamiast 301); w aktach z artykułami narzędzie pokazuje wszystkie trafienia z artykułem, w którym leżą.
- Na tekście jednolitym z obwieszczeniem Marszałka lista nowelizacji jest porównywana z obwieszczeniem: zmiany ujęte w t.j. są oznaczone „UWZGLĘDNIONA”, a nieobjęte — „NIE objęta t.j.”. Dotąd np. k.p. (DU 2026/1245) podawał nowelizację DU 2026/1046 jako zmianę „po tekście jednolitym”, choć t.j. ją już obejmuje. Przypisy odsyłające do innych przypisów są rozwijane, więc przy dwóch brzmieniach przepisu (art. 94³ k.p.) widać, które obowiązuje dziś.
- `--strict` blokuje tekst, w którym brakuje treści stron PDF bez warstwy tekstowej; w ścieżce HTML tytuł aktu nie jest sklejany („Ministra Zdrowiaz dnia”), znika artefakt „Pokaż całość” i podwójne przypisy.

### ELI — akty Dz.U. i M.P. 2000–2011 z PDF (PR #14, PolskiAgentW)

- `tekst` dla aktów Dz.U. i M.P. 2000–2011 bez HTML (M.P. z tych lat nie ma HTML wcale) zwraca tekst samego aktu, łam po łamie, z poprawnymi polskimi literami. Dotąd łamy się mieszały („ustawy z dnia § 4. 1. Minimalna norma”), litery były zniekształcone („Za∏àcznik”), więc `--fragment` nie znajdował fraz z polskimi znakami, a tekst zawierał sąsiednie akty z tych samych stron zeszytu (`tekst DU 2003 991` zaczynał się od załącznika do poz. 990).
- Po recenzji: tabele nie są rozrywane na łamy (stawki zostają przy pozycjach), koniec aktu jest rozpoznawany po tytule następnego aktu (komórka tabeli z liczbą nie ucina już aktu), zniekształcone litery są rozpoznawane po treści strony (także DU 2010 poz. 1), ze znaku wodnego www.rcl.gov.pl nie zostają resztki, a ostatni akt zeszytu nie zawiera stopki wydawniczej z ceną i ISSN.
- `tekst` dla ogłoszonych aktów Dz.U. i M.P. z lat 1990–1999 bez HTML zwraca sam akt, łam po łamie, bez nagłówków stron, spisu treści zeszytu, sąsiednich aktów i ogłoszeń wydawcy; wynik ostrzega, że warstwa tekstowa tych PDF to OCR, więc liczby, daty i kwoty trzeba sprawdzić w PDF. Na 24 aktach z lat 90. porównanych z HTML odsetek słów we właściwej kolejności wzrósł z 0,58 do 0,90, bez pogorszenia w żadnym akcie.
- W zeszytach 2000–2011 spis treści pierwszej strony nie psuje już podziału na łamy, a akt zaczynający się od sygnatury („Sygn. akt”) jest poprawnie wycinany.
- Strony-skany bez warstwy tekstowej są odczytywane przez OCR, gdy w systemie jest `tesseract` z językiem polskim: do 10 stron samodzielnie, dłuższe skany z `tekst … --ocr` (ok. 7 s na stronę; wynik zapamiętywany na dysku), `--bez-ocr` wyłącza OCR. Tekst z OCR ma wyraźne ostrzeżenie, że litery, cyfry, daty i kwoty mogą być przekłamane, a `--strict` go blokuje. Np. DU 1992 poz. 413 (sam skan) dotąd kończyło się błędem „pdftotext nie zwrócił tekstu”.
- Zeszyty 2000–2011: strony z reklamą wydawcy nie są doklejane do ostatniego przepisu aktu (DU 2010/1128, 2010/20), indeks górny jest odczytywany (art. 222¹ zamiast „art. 2221”), a wyraz łamany z powtórzonym dywizem jest scalany („Środkowo-Wschodniej”).
- Przypisy z dołu łamu w zeszytach 1990–2011 trafiają pod akapit, którego dotyczą, zamiast w środek tekstu (na 109 losowych zeszytach przypisy bez treści: 372 → 135), a podpis dosunięty do krawędzi lewego łamu nie przestawia już kolejności łamów.
- `tekst` ostrzega, ile stron PDF nie ma warstwy tekstowej (np. DU 2010 poz. 1: 563 z 565 stron to skany) — tej treści w wyniku nie ma.
- Na 25 losowych aktach Dz.U. 2000–2011, porównanych z ich wersją HTML, średni odsetek słów we właściwej kolejności wzrósł z ok. 0,49 do ok. 0,80, bez pogorszenia w żadnym akcie.

### Rejestr umów

- Gdy API ogranicza liczbę zapytań (HTTP 429), narzędzie samo czeka i ponawia zapytanie (do 60 s) zamiast od razu kończyć błędem.
- Kwoty są pokazywane bez „zł”, a `umowa` mówi wprost, że rejestr nie podaje waluty ani tego, czy kwota jest netto czy brutto — dotąd dopisek „zł” sugerował złotówki także przy umowach zagranicznych.

### E-dzienniki urzędowe województw

- `tekst` ostrzega o stwierdzonej nieważności aktu (z tytułem rozstrzygnięcia nadzorczego lub uchwały RIO i unieważnionym paragrafem), o sprostowaniach, uchyleniach i zmianach. Dotąd `tekst DS 2026 584 --fragment "wyniki pracy placówki"` podawał unieważniony § 6 ust. 4 bez słowa, także z `--strict`. Teraz `--strict` blokuje akt nieważny w całości, fragment z unieważnionej jednostki i sytuację, gdy nie da się pobrać powiązań; nieważność w części i sprostowanie dają głośne ostrzeżenie.
- `akt` podaje zakres nieważności z tytułu rozstrzygnięcia (np. „§ 6 ust. 4”), urzędową formę cytatu („Dz. Urz. Woj. Dolnośląskiego z 2026 r. poz. 584”) i datę wejścia w życie ze źródłem, gdy rejestr ją podaje.
- Dziennik mazowiecki znów działa z helpera — przyczyną był filtr antybotowy serwera, nie blokada ruchu spoza Polski, jak twierdził komunikat.
- Błędy TLS są nazywane trafnie: dla dziennika pomorskiego „certyfikat WYGASŁ (ważny do 2026-10-01)” zamiast mylącej porady o brakującym certyfikacie pośrednim.
- `tekst --fragment "§ N"` nie łapie wiersza numeracji kolumn tabeli w załączniku („§ 1. 2. 3. 4.”), a paragraf z załącznika jest oznaczony jako trafienie w załączniku.
- Nagłówki dzienników lubuskiego i warmińsko-mazurskiego z PDF są odczytywane poprawnie („Gorzów Wielkopolski, dnia …”), a pozycje techniczne dziennika śląskiego nie trafiają już do wyników jako puste wiersze.

### EUR-Lex

- `tekst` i `--pdf` aktu bazowego wymieniają sprostowania w danym języku (CELEX, data, poprawione artykuły) i wskazują wersję skonsolidowaną z poprawnym brzmieniem; `--fragment` na sprostowanym artykule ostrzega, że brzmienie jest niesprostowane, a `--strict` blokuje taki tekst. Dotąd `tekst 32016R0679 --fragment "art. 4"` podawał bez ostrzeżenia brzmienie RODO sprzed sprostowania („informacje” zamiast „wszelkie informacje”; podobnie art. 10 i art. 82 ust. 2).
- `tekst` i `--pdf` działają dla starszych aktów, np. dyrektywy 95/46/WE i obowiązującej dyrektywy e-Privacy 2002/58/WE, oraz dla PDF wersji skonsolidowanych. Dotąd kończyły się błędem 404 z sugestią złego numeru CELEX; teraz komunikat mówi, czego brakuje (wersji językowej albo HTML).
- Wskazówka do urzędowego cytatu obejmuje sprostowania, nie tylko „akt bazowy + zmiany”.
- `meta` odróżnia koniec obowiązywania aktu od częściowego upływu ważności (np. rozporządzenie 470/2009: dotąd „Koniec obowiązywania: 2022-01-27” przy statusie „OBOWIĄZUJE”, teraz brak daty końca i częściowy upływ ważności art. 30), opisuje rodzaj dat (wejście w życie / stosowanie) i podaje termin transpozycji także wtedy, gdy CELLAR zapisuje go jako ogólny termin (dyrektywa 2002/12: 20.09.2003, art. 3.1).
- `odniesienia` nie podaje już każdej pozycji dwa razy (rozporządzenie 470/2009: sprostowania 2 zamiast 4, uchylenia dorozumiane 134 zamiast 268), a `--fragment` oznacza trafienie w umowie dołączonej do aktu albo w załączniku, zamiast doklejać je jak kolejny artykuł aktu.
- Akty, które w danym języku są tylko w PDF (np. polskie wydanie specjalne, rozporządzenie 883/2004), `tekst` i `--fragment` czytają z urzędowego PDF przez pdftotext, z rozdzielonymi łamami i przypisami na końcu; dotąd narzędzie odsyłało tylko do `--pdf`.

### Orzecznictwo — SAOS, CBOSA, UODO, Portal Orzeczeń MS

- SAOS: `sygnatura` podaje nazwę sądu i wydział przy każdym trafieniu i ostrzega, gdy ta sama sygnatura dotyczy różnych spraw w różnych sądach. Dotąd `sygnatura "I ACa 100/13"` pokazywała cztery wyroki czterech sądów apelacyjnych jako „sąd powszechny” i wskazywała jeden z nich jako „Pełna treść”.
- Portal Orzeczeń MS i SAOS: od 24.09.2026 portal MS (także portale poszczególnych sądów) nie wydaje treści żadnego nowo opublikowanego orzeczenia — zwraca stronę „Błąd danych”, a SAOS, który importuje z tego samego zaplecza, ma te orzeczenia bez tekstu (sprawdzone 5.10.2026 na ok. 45 dokumentach). Oba silniki mówią to wprost: treść jest niedostępna u źródła i nie wolno jej odtwarzać z fragmentu wyszukiwarki; dotąd komunikat sugerował jedynie „ponów później”.
- SAOS: przy dokumencie typu „uzasadnienie” narzędzie ostrzega, że podana data to data uzasadnienia, nie wyroku (np. VI Ka 1622/25: 8.06 zamiast 20.05.2026), wskazuje prawdopodobną datę wyroku z nagłówka treści i datę publikacji.
- UODO → CBOSA: przy decyzji nieprawomocnej `decyzja` podaje gotowe zapytania CBOSA, które znajdują wyrok sądu administracyjnego (DKN.5131.1.2025 → WSA II SA/Wa 837/25, uchylenie kary 27 mln zł), pokazuje częściową prawomocność („w zakresie punktu 1)”) i zastrzeżenie przy polu „inforce”. Dotąd zalecane wyszukiwanie po numerze decyzji dawało „zweryfikowane zero”, bo CBOSA anonimizuje numery decyzji.
- UODO: decyzja uchylona przez WSA, a potem — po wyroku NSA — podana przez portal jako prawomocna w całości, nie jest już opisywana jako „UCHYLONA”, tylko z ostrzeżeniem o przebiegu sprawy (np. DKN.5131.49.2021: NSA III OSK 251/24 uchylił wyrok WSA i oddalił skargę); częściowe uprawomocnienie po uchyleniu samej kary uchylenia nie znosi.
- UODO: prawomocność jest ustalana z historii portalu („Uprawomocnienie”), a nie z pola API, którego portal nie aktualizuje — dotąd np. DS.523.5648.2023 była podawana jako nieprawomocna, choć portal pokazuje ją jako prawomocną od 31.07.2025. Gdy sąd rozpatrywał sprawę, a portal nie zna wyniku, status to „NIEUSTALONY” z ostrzeżeniem, że decyzja mogła zostać uchylona, a `--strict` ją blokuje — dotąd DKN.5131.32.2023 była „prawomocna”, choć WSA (II SA/Wa 285/24) uchylił ją 2.03.2026. Jako pierwsze zapytanie narzędzie podaje sygnaturę sprawy WSA, a zapytania po treści znajdują wyrok na pierwszej stronie wyników.
- SAOS: link „Źródło urzędowe” dla orzeczeń SN prowadzi do orzeczenia w nowej wyszukiwarce sn.pl zamiast do strony błędu 404.
- CBOSA: błąd połączenia jest diagnozowany trafnie — blokada środowiska (proxy, np. w piaskownicy), brak sieci albo awaria serwera CBOSA (z godziną sprawdzenia i poradą, kiedy ponowić). Dotąd każdy błąd był opisywany jako „serwer CBOSA ucina połączenia”, przez co modele zgłaszały, że „CBOSA nie działa”, także gdy winne było środowisko. Dokumentacja zabrania równoległych zapytań i obchodzenia helpera własnym pobieraniem stron.
- CBOSA: strona „Błąd przy szukaniu orzeczeń”, którą wyszukiwarka zwraca w czasie awarii serwera, jest zgłaszana jako awaria z radą ponowienia tego samego zapytania, a nie jako „CBOSA odrzuciło zapytanie” (model uznawał wtedy poprawne zapytanie za błędne).
- CBOSA: nowa opcja `--organ` (np. `--organ UODO`); zapytanie o numer decyzji organu nie kończy się już „zweryfikowanym zerem”, tylko ostrzeżeniem o anonimizacji i podpowiedzią skutecznego zapytania.
- Portal Orzeczeń MS: pole metryki „Data uprawomocnienia” jest odczytywane, więc `--strict` nie blokuje już orzeczeń, które portal podaje jako prawomocne.
- Portal Orzeczeń MS (pierwszy audyt merytoryczny, 14 orzeczeń SA/SO/SR porównanych z portalem i PDF): `rss --sad <kod sądu>` znów działa dla sądów, których kanały prowadzą do podportali; przerwane połączenie, strona błędu portalu ani nieprzewidziany błąd nie udają już „braku wyników” (kod 2 zamiast 1), a strona spoza zakresu wyników i puste zapytanie dają jasny komunikat.
- Portal Orzeczeń MS: gdy portal nie udostępnia treści świeżego orzeczenia („Błąd danych”), dla nieznanego ID albo przy pustej liście przepisów narzędzie mówi wprost, co się stało. Treść i metryka nie mają sztucznych łamań linii, indeksy górne są w jednej linii, metryka podaje typ dokumentu i istotność oraz ostrzega, gdy „data orzeczenia” to data uzasadnienia lub zarządzenia; wyniki wyszukiwania pokazują oznaczenie „nieprawomocne” i ostrzegają przed tą samą sygnaturą w różnych sądach.

## 2.1.0 — 2026-09-20

- Dodano ósmy plugin `prawo-pl-orzeczenia-ms`: wyszukiwanie orzeczeń SA/SO/SR bezpośrednio w Portalu Orzeczeń MS, metryki, pełne uzasadnienia, powołane przepisy, eksport PDF i kanały RSS.
- Dostęp do portalu MS działa przez standardową bibliotekę Pythona z przetestowanym User-Agentem, bez `agent-browser`, JavaScriptu i dodatkowych bibliotek. Blokada F5/TSPD lub niekompletna odpowiedź jest zgłaszana jako błąd, a nie brak orzeczeń.
- Nowy silnik oddziela datę wyroku od daty publikacji, zachowuje indeksy górne i rozpoznaje tekstowe oraz graficzne oznaczenie nieprawomocności i sygnalizuje nieustalony status; `--strict` blokuje pobieranie dokumentu bez potwierdzenia prawomocności.
- Udokumentowano filtry wyszukiwarki, ograniczony zakres RSS oraz dobór źródła MS/SAOS/CBOSA; dodano plugin do obu marketplace’ów i walidacji wydania.
- Dodano 29 testów nowego silnika, opartych na odpowiedziach portalu i przypadkach błędnych; przed wydaniem zweryfikowano także wyszukiwanie, pobieranie treści, PDF i RSS na żywo.

## 2.0.3 — 2026-09-20

- Dodano kontakt przez LinkedIn w polu `author.url` wszystkich siedmiu pluginów, w manifestach Claude i Codex oraz wpisach autorów w marketplace Claude.
- Dodano ten changelog i stałe instrukcje jego prowadzenia w `AGENTS.md`.
- Opisy kolejnych wydań GitHub Release są pobierane z changelogu. Brak wpisu dla wydawanej wersji blokuje publikację.

## 2.0.2 — 2026-09-20

- ELI: tryb `--strict` blokuje wynik, gdy odpowiedź endpointu odniesień ma nieprawidłową strukturę. W zwykłym trybie tekstowi towarzyszy ostrzeżenie o niezweryfikowanej aktualności.
- ELI: odpowiedzi JSON i inne dane niebędące tekstem z endpointu HTML nie są już wyświetlane jako treść aktu.
- EUR-Lex: brak znalezionych konsolidacji nie jest już przedstawiany jako dowód, że akt nie był zmieniany; komunikat kieruje do sprawdzenia zmian i sprostowań.
- Rejestr umów: komendy `najnowsze` i `szukaj` sprawdzają strukturę odpowiedzi. Obiekt błędu ani pusta pierwsza strona przy dodatniej liczbie trafień nie są traktowane jako poprawny wynik, także w trybie JSON.
- Dodano testy regresyjne powyższych przypadków.
- Dodano kontakt z Krzysztofem Gibkiem przez LinkedIn w README.
