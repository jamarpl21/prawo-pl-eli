# Kontrakt publicznych stron MS (sprawdzony 2026-09-20, uzupełniany do 2026-11-13)

Portal: https://orzeczenia.ms.gov.pl; publiczny HTML aplikacji Tapestry 5.3.8 / 3.1.3,
nie udokumentowane API JSON. Helper używa `urllib`, HTTPS i `User-Agent: curl/8.7.1`.
Bez cookies, zależności pip i headless browser. Nie należy wnioskować o usunięciu F5/TSPD.

## Wyszukiwanie

`GET /search/advanced/<15 pól>/<sort>/<kierunek>/<strona>`.
Puste pole = `$N`. ASCII litery/cyfry oraz `-_.` bez zmian; pozostałe znaki kodowane
jako `$` + cztery małe cyfry hex jednostki UTF-16 (np. spacja `$0020`, `/` `$002f`,
`ś` `$015b`, znaki spoza BMP jako dwie jednostki). Nie stosować zwykłego `%20`.

Mapowanie zweryfikowane przekierowaniami po wysłaniu publicznego formularza wyszukiwarki:

| Pole (od 1) | Znaczenie | Przykład |
|---|---|---|
| 1 | szukane słowa | dobra osobiste |
| 2 | sygnatura | I ACa 1410/23 |
| 3 | kod sądu | 15050500 = SO w Białymstoku |
| 4 | kod typu wydziału | 03 = Cywilny |
| 5 | kod apelacji | 1505 = białostocka |
| 6 | kod okręgu | 150505 = białostocki |
| 7 | nierozpoznane / nieużywane przez helper | zawsze `$N` |
| 8, 9 | data orzeczenia od/do | 2023-09-18 |
| 10 | sędzia | nazwisko |
| 11 | funkcja sędziego | CHAIRMAN, RAPPORTEUR, REASONS_AUTHOR, JUDGE |
| 12 | hasło karne albo cywilne | Dobra osobiste |
| 13 | podstawa prawna | art. 23 |
| 14 | tylko tezowane | `*` → `$002a` |
| 15 | minimalna istotność | 1 |

Pole formularza „Z dnia” ustawia jednocześnie 8 i 9. Pola hasła karnego/cywilnego
zajmują ten sam slot 12. Kody wyszukiwarki to nie nazwy z `<option>` — serwer je
przelicza. Kod sądu = kod kanału z `/rss/courts` (377 sądów; ponownie sprawdzone 2026-10-05
wysłaniem formularza: SO w Białymstoku → 15050500, SA w Białymstoku → 15050000,
SR dla Warszawy-Śródmieścia → 15450530). Kody typu wydziału (formularz, 2026-10-05):
03 Cywilny, 06 Karny, 15 Pracy, 21 Pracy i Ubezpieczeń Społecznych, 27 Gospodarczy;
apelacja warszawska 1545. Dla pozostałych filtrów ustaw pole w formularzu i odczytaj
wynikowy URL. Nie zgaduj kodów. Nieznana wartość w formularzu może zostać po cichu pominięta.

Słowa w polu 1 są łączone koniunkcją z odmianą (2026-10-05: „dobra” 133 751, „osobiste”
108 666, „dobra osobiste” 59 138); fraza w cudzysłowie (`$0022…$0022`) to dokładna fraza
(9 576). Zapytanie bez żadnego kryterium zwraca sam formularz bez listy i bez `.big_number`
— helper odrzuca je przed wysłaniem.

Sort: `score`, `data` (data orzeczenia), `datapublikacji`, `istotnosc`;
kierunek `ascending`/`descending`; strona od 1, po 10 wyników.
Wynik: `.big_number`, `#results .single_result`, tytuł/link w `h4`, data i sąd
w `.title`. Zero trafień ma osobny `.alert`: „Nie znaleziono żadnego wyniku pasującego
do zapytania.” Brak rozpoznanej struktury to UNKNOWN, nie zero. Linki wyników mogą mieć
frazę w ścieżce (`/details/<fraza>/<ID>`); ID to ostatni segment. W `.title` pozycji bywa
`<p>Orzeczenie nieprawomocne</p>` (→ `oznaczenie_nieprawomocne`). Strona za ostatnią
(np. 500 przy 3192 trafieniach) ma `.big_number`, ale pustą listę — helper zgłasza błąd zakresu.

### Stronicowanie: kolejność remisów jest niestabilna (pomiar 2026-11-13)

Każda strona to osobne zapytanie, a portal nie ustala kolejności pozycji o równym kluczu
sortowania — między zapytaniami zmieniają miejsca, więc kolejne strony powtarzają pozycje,
a innych nie ma na żadnej stronie. Pomiar: wszystkie strony, liczba unikalnych ID / trafienia.

| Zapytanie | `--sort`, kierunek | Unikalne ID |
|---|---|---:|
| SR w Giżycku (`--sad 15051510 --wydzial 06`), orzeczenia z 2024 r., 72 trafienia, najwyżej 3 orzeczenia z tą samą datą | score (2 przebiegi) | 60, 50 / 72 |
| | istotnosc | 55 / 72 |
| | datapublikacji | 60 / 72 |
| | data, descending (2 przebiegi) | 71, 71 / 72 |
| | data, ascending (2 przebiegi) | 72, 72 / 72 |
| Apelacja warszawska (`--apelacja 1545`), jeden dzień (`--od` = `--do` = 2025-06-10), 28 trafień, wszystkie z tą samą datą | data, descending (2) | 24, 20 / 28 |
| | data, ascending (2) | 20, 18 / 28 |
| | score | 20 / 28 |

W Giżycku `data descending` dwa razy powtórzyło tę samą pozycję (data 2024-07-31) na stronach 2 i 3 —
remis daty na granicy stron. Żadne sortowanie nie jest więc stabilne; `data` gubi najmniej, gdy mało
orzeczeń ma tę samą datę, a przy wielu remisach daty jest tak samo zawodne jak `score`. Wcześniejszy
pomiar (zapytanie z frazą, 12 trafień): 12/12 — `score` różnicował pozycje. W zapytaniu jednodniowym
suma ID z dwóch pierwszych przebiegów (24 i 20 unikalnych) dała komplet 28/28 — jedna obserwacja,
nie gwarancja.
Helper przy `liczba_wynikow` > 10 (bez `--wszystkie`) dodaje uwagę, która odsyła do `--wszystkie`.

### Pełna lista: `--wszystkie` (od 2026-11-13)

Opcja `szukaj`/`sygnatura`, domyślnie wyłączona. Mechanizm:

1. Pierwsze zapytanie (strona 1, `--sort data`, kierunek z `--kierunek`) ustala `liczba_wynikow`.
   Powyżej **200 trafień** helper kończy się błędem (kod 2) po tym jednym zapytaniu — z liczbą
   trafień, limitem i wskazówką zawężenia (`--sad`, `--wydzial`, krótszy zakres `--od/--do`).
   Limity (200 trafień, 60 zapytań na wywołanie) to stałe w kodzie (ochrona portalu), bez flagi do
   ich podniesienia.
2. Runda = strony 1…⌈N/10⌉; pozycje łączone po `id`. Gdy unikalnych jest dokładnie N, helper kończy
   od razu (także w środku rundy). Inaczej kolejna runda — dopóki starcza **budżetu 60 zapytań**
   (odstępy ≥0,6 s, bez równoległości). Liczbę rund wyznacza więc rozmiar zapytania: 20 stron = 3
   rundy, 8 stron = 7 pełnych rund i część ósmej, 3 strony = do 20 rund; ostatnia runda bywa niepełna.
   Pobierane są tylko pozycje listy (bez metryk i treści). W wersji 2.3.0 było stałe 3 rundy, co przy
   zapytaniu jednodniowym (3 strony) zużywało 9 z 60 zapytań i raz skończyło się błędem przy 26 z 28.
3. Zatrzymanie bez kompletu → kod 2 (UNKNOWN), niepełnej listy helper nie wypisuje. Powody:
   **3 rundy z rzędu bez nowej pozycji** albo wyczerpany budżet 60 zapytań. Komunikat podaje faktycznie
   wykonane rundy i zapytania (z ponowieniami) oraz liczbę zebranych/oczekiwanych, np. „po 4 rundach
   (12 zapytań) zebrano 24 z 25 unikalnych pozycji … (3 rundy z rzędu bez nowej pozycji)”.
   Dlaczego 3: w pomiarze runda bez postępu, po której następna dała komplet, się zdarza (Giżycko:
   71, 71, 72), więc 1 to za mało; zatrzymanie chroni budżet, gdy portal podaje liczbę trafień większą
   niż liczba pozycji, które oddaje — helper traci wtedy 3 rundy ponad te, które zebrały wszystko,
   co portal oddaje (np. 3 strony: zwykle 4 rundy = 12 zapytań), a nie cały budżet 60.
4. Każda strona porównuje swoją liczbę trafień z ustaloną N. Inna liczba, pusta strona lub strona
   poza zakresem w trakcie albo więcej różnych ID niż N = lista się zmieniła: zebrane pozycje są
   odrzucane (usunięte orzeczenie mogłoby „dopełnić” licznik za brakujące), a następna runda zaczyna
   od nowa i ustala N ponownie. Runda zakończona takim restartem liczy się jako runda bez nowej pozycji,
   więc lista zmieniająca się w każdej rundzie kończy się po 3 rundach kodem 2 („lista portalu
   zmieniała się w trakcie pobierania”). Wynik po restarcie ma uwagę o zmianie. Zamiany jednej
   pozycji na inną przy niezmienionej liczbie trafień między rundami nie da się wykryć licznikiem.
5. Ponawianie (w wersji 2.3.0 go nie było): stronę, której pobranie przerwał błąd sieci —
   przekroczony czas (40 s), zerwane połączenie, niekompletna odpowiedź (`IncompleteRead`), błąd połączenia — helper ponawia
   **raz**, po przerwie 5 s; obie próby liczą się do budżetu 60 (przy wyczerpanym budżecie bez
   ponowienia). Drugie niepowodzenie tej samej strony → kod 2 bez listy, z numerem strony i stanem
   („Stan: runda 1 (4 zapytania, w tym 1 ponowienie po błędzie sieci) zebrano 20 z 25 …”).
   Bez ponawiania: odpowiedź F5/TSPD, strona błędu portalu (HTTP 400/404), inny błąd HTTP bez strony
   portalu, nierozpoznana lub ucięta strona HTML — to nie są błędy przejściowe sieci. Bez
   `--wszystkie` helper nie ponawia niczego.
6. `--strona` ≠ 1 i jawne `--sort` inne niż `data` są odrzucane przed pierwszym zapytaniem. Kolejność
   portalu nie jest powtarzalna, więc wynik jest sortowany lokalnie: `data_orzeczenia` zgodnie
   z `--kierunek`, przy równej dacie po `id` (w tym samym kierunku).
7. JSON: `zrodlo` (URL strony 1), `liczba_wynikow`, `kompletna: true`, `rundy`, `zapytania` (wszystkie
   próby, z ponowieniami), `ponowienia`, `wyniki`, `uwagi` — bez pola `strona`. Uwagi z pozycji (oznaczenie
   „nieprawomocne”, typ „uzasadnienie”, ta sama sygnatura w wielu sądach) liczone są z całej listy; uwagi o stronicowaniu brak.
   Zero trafień w pierwszym zapytaniu = kod 1, strona błędu portalu = kod 2, jak bez `--wszystkie`.

Pomiar 2026-11-13 (helper 2.3.0 z `--wszystkie`, stałe 3 rundy, bez ponawiania; „rundy/zapytania” do kompletu lub do błędu):

| Zapytanie | Przebieg | Unikalne ID po rundach | Rundy | Zapytania | Komplet |
|---|---|---|---:|---:|---|
| SR w Giżycku, `--sad 15051510 --wydzial 06 --od 2024-01-01 --do 2024-12-31` (72) | 1 | 71, 71, 72 | 3 | 18 | tak |
| | 2 | 72 | 1 | 8 | tak |
| | 3 | 72 | 1 | 8 | tak |
| Apelacja warszawska, `--apelacja 1545 --od 2025-06-10 --do 2025-06-10` (28, jedna data) | 1 | 22, 26, 26 | 3 | 9 | nie (kod 2) |
| | 2 | 18, 28 | 2 | 4 | tak |
| | 3 | 20, 28 | 2 | 5 | tak |
| `--apelacja 1545 --od 2025-06-01 --do 2025-06-30` (331) | 1 | — | — | 1 | błąd limitu (kod 2) |
| `sygnatura I ACa 1410/23` (1); `--sad 15050500 --od 2024-01-01 --do 2024-12-31` (9) | po 1 | 1; 9 | 1 | 1 | tak |

Przy wielu remisach daty (wszystkie 28 pozycji z jednego dnia) pierwsza runda dawała 18–26 z 28
unikalnych (6 przebiegów, z wariantem przemiennym) i 3 rundy nie zawsze wystarczały — helper 2.3.0
zgłaszał wtedy błąd zamiast niepełnej listy (stąd rundy ograniczone budżetem, punkt 2); zawężenie
(`--sad`, `--wydzial`) zmniejsza liczbę remisów. W Giżycku (przebieg 1) dwie pierwsze rundy dały
po 71 z 72, komplet dała dopiero trzecia (której pozycji brakowało, nie zapisano).
Przemienny kierunek rund (malejąco, rosnąco, malejąco) sprawdzono na zapytaniu jednodniowym:
komplet 1 z 3 przebiegów (26→28 w 2 rundach; 24, 25, 27; 20, 21, 24) wobec 2 z 3 przy tym samym
kierunku — przewagi nie potwierdzono, więc każda runda pyta tak samo. Przypadku remisu na granicy
stron (Giżycko) z przemiennym kierunkiem nie mierzono. Portal odpowiadał tego dnia
nierówno (do 17 s na zapytanie); pięć przebiegów (3 × Giżycko, 2 × SO w Białymstoku) przerwał
błąd zapytania (kod 2, bez listy; zapisane komunikaty: 3 × przekroczenie 40 s, 1 × zerwane połączenie,
jednego nie zapisano); nie wliczono ich do tabeli. Strony F5/TSPD nie było. Helper 2.3.0 nie ponawiał
zapytań po błędzie transportu — stąd ponowienie z punktu 5.

Pomiar 2026-11-13, po zmianie (rundy wg budżetu, ponawianie; 4 przebiegi po kolei, co ok. 75 s):
zapytanie jednodniowe (28) — 2 × komplet w 3 rundach (8 zapytań); Giżycko (72) — komplet w 2
rundach (11 zapytań) i w 1 rundzie (8). Czas przebiegu 5–11 s, bez błędów sieci, więc żadne
ponowienie ani runda ponad trzecią nie były w tym pomiarze potrzebne — te ścieżki sprawdzają testy
offline z mokowanym klientem.

## Orzeczenie

`ID` pochodzi z linku wyniku lub RSS (nie wolno konstruować go z samej sygnatury).

- `/details/$N/<ID>`: `#content dl` z parami `dt/dd`: Sygnatura, Sąd, Data orzeczenia,
  Data publikacji, Data uprawomocnienia, Wydział, Przewodniczący, Sędziowie, Protokolant,
  Hasła tematyczne, Podstawa prawna, Teza, Istotność. Daty po polsku normalizowane do ISO.
  Typ dokumentu jest tylko w nagłówku `<h2>Sygnatura - typ Sąd z RRRR-MM-DD</h2>`.
  „Istotność” to pusty `<span id="relevance">` — liczbę gwiazdek (0–5) portal podaje
  w skrypcie `Tapestry.init({"relevanceInit":[{"isDisabled":true,"relevance":N}]})`.
  Znaki nowej linii w źródle HTML są zwykłymi spacjami (np. `Sygn. akt<strong>\n XXI U
  2364/25</strong>`, `477<sup>\n (\n 14)</sup>`); łamanie linii wynika tylko z elementów blokowych.
- `/content/$N/<ID>`: tekst wewnątrz `#content .single_result`, bez nawigacji/stopki.
- `/regulations/$N/<ID>`: `#regulations li`, niekoniecznie wszystkie przepisy z treści.
- `/similardocs/$N/<ID>`: link do podobnych orzeczeń; na testowanych dokumentach HTTP 400
  (2026-09-20 i 2026-10-05), dlatego helper zwraca tylko odnośnik i nie obiecuje pobierania.
- Strony błędów portalu: HTTP 400 `<h2>Błąd danych</h2>` i HTTP 404 „Strona o podanym adresie
  nie istnieje” (nieznane ID, tytuł „Błąd - Portal Orzeczeń…”). „Błąd danych” na zakładce
  „Treść” helper zgłasza jako UNKNOWN (kod 2) z zaleceniem ponowienia — nigdy jako brak wyników.
- Zdarzenie historyczne — awaria treści po 24.09.2026. 2026-10-05 „Błąd danych” zwracała zakładka
  „Treść” (także w przeglądarce i na podportalach sądów) dla 9 z 14 sprawdzonych dokumentów —
  wszystkie świeżo opublikowane i bez zakładek „Powołane przepisy”/„Orzeczenia podobne” w metryce;
  dla nich `/regulations` dawało 200 z pustym `<ol id="regulations">`. Dalsza weryfikacja 2026-10-05
  (ok. 45 dokumentów, curl): treść zwracał każdy dokument opublikowany do 2026-09-23, „Błąd danych” —
  każdy opublikowany od 2026-09-24, niezależnie od sądu; to samo na podportalach sądów. Zaplecze
  `https://apiorzeczenia.wroclaw.sa.gov.pl/ncourt-api/judgement/details?id=<ID>` oddawało dla nich
  metrykę (XML), a SAOS (który stamtąd importuje) — pustą treść. Awaria po stronie MS.
  Ponowny pomiar 2026-11-13 (helper, portal centralny): 59 dokumentów — 4 opublikowane 21–23.09.2026
  i 55 opublikowanych 24.09–12.11.2026 (34 z nich 24.09–9.10, po 2–3 z każdego z 12 dni
  roboczych tego okna) — 59/59 z treścią, 0 × „Błąd danych”. 7 z 59 nie ma w metryce zakładek „Powołane przepisy”/
  „Orzeczenia podobne”, a wszystkie 7 mają treść, więc brak zakładek nie przewiduje już braku
  treści (helper nie dodaje o tym uwagi). Data końca awarii jest nieznana (między 5.10 a 13.11.2026).
  Przeglądarki, podportali sądów i SAOS 2026-11-13 nie sprawdzano ponownie.
- PDF: adres odczytywany z `/content.pdffile/...` na stronie treści; nie budować ścieżki
  do pliku ręcznie. Sprawdzać sygnaturę `%PDF-` przed zapisem.

Przykład: `152010000000503_I_C_002715_2021_Uz_2021-09-29_001`:
I C 2715/21, SO Kraków, data wyroku 2021-09-29, data publikacji 2024-08-22.
Sam tag `meta description` podaje w tym przypadku datę publikacji przy opisie sprawy,
więc nie jest źródłem daty wyroku. Na tym dokumencie portal oznacza nieprawomocność
poprzez `.single_result.invalid`. Potwierdzono źródło tego oznaczenia:
`/assets/3.1.3/ctx/css/custom.css` ustawia tło `../img/nieprawomocny.png`,
a obraz zawiera napis „ORZECZENIE NIEPRAWOMOCNE”. Parser odczytuje tę flagę jako
`false`; brak klasy i brak jawnego komunikatu nadal oznacza `null`, nie `true`.

Metryka bywa też wprost oznaczona polem `<dt>Data uprawomocnienia:</dt><dd>8 czerwca 2026</dd>`
(`154510000003006_VI_Ka_001622_2025_Uz_2026-06-08_001`, sprawdzone 2026-10-05) — parser zwraca
wtedy `prawomocne: true` i `data_uprawomocnienia`. Ten dokument to samo „uzasadnienie” (`<h2>… -
uzasadnienie …`): „Data orzeczenia” 8 czerwca 2026 jest datą uzasadnienia, a wyrok zapadł 20.05.2026.

## RSS

Lista kanałów: `/rss/courts` (według sądów) i `/rss/themephrases` (według haseł).
Kanał ogólny: `/rsscontent/Orzecznictwo$0020s$0105d$00f3w$0020powszechnych`.
Kanał sądu: `/rsscontent/<8-cyfrowy-kod>` (nieznany kod → HTTP 400 „Błąd danych”).
RSS 2.0: `channel/item`, `title`, `link`, `description`, `pubDate`, `author` (= sąd).
2026-10-05 kanał ogólny zawierał 100 wpisów (ok. 1–2 dni publikacji), kanały sądów po 20;
helper nie zakłada nieograniczonej historii ani nie obiecuje pełnej synchronizacji.

Kanały sądów linkują do **podportali** (`http://orzeczenia.<miasto>.<sa|so|sr>.gov.pl/details/$N/<ID>`).
Portal centralny serwuje te same ID (sprawdzone 2026-10-05 dla SA w Białymstoku — 2 wpisy,
SO w Suwałkach, SR w Augustowie: ta sama sygnatura i sąd w `/details/$N/<ID>` centrali).
Helper bierze z linku podportalu wyłącznie ID, zwraca `url` w centrali i `link_zrodlowy`;
podportalu nie pobiera, a metryka centrali i tak sprawdza zgodność ID. Inne hosty są
odrzucane. HTTP w linkach centralnego portalu jest podnoszone do HTTPS przed odczytem;
przekierowania poza centralny portal są odrzucane.
