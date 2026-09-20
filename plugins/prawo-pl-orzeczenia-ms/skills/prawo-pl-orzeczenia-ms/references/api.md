# Kontrakt publicznych stron MS (sprawdzony 2026-09-20)

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
przelicza. Kod sądu można odczytać z linku RSS danego sądu; dla pozostałych filtrów
ustaw pole w formularzu i odczytaj wynikowy URL. Nie zgaduj kodów. Nieznana wartość
w formularzu może zostać po cichu pominięta przez portal.

Sort: `score`, `data` (data orzeczenia), `datapublikacji`, `istotnosc`;
kierunek `ascending`/`descending`; strona od 1, po 10 wyników.
Wynik: `.big_number`, `#results .single_result`, tytuł/link w `h4`, data i sąd
w `.title`. Zero trafień ma osobny `.alert`: „Nie znaleziono żadnego wyniku pasującego
do zapytania.” Brak rozpoznanej struktury to UNKNOWN, nie zero.

## Orzeczenie

`ID` pochodzi z linku wyniku lub RSS (nie wolno konstruować go z samej sygnatury).

- `/details/$N/<ID>`: `#content dl` z parami `dt/dd`: Sygnatura, Sąd, Data orzeczenia,
  Data publikacji, Wydział, Przewodniczący itd. Daty po polsku normalizowane do ISO.
- `/content/$N/<ID>`: tekst wewnątrz `#content .single_result`, bez nawigacji/stopki.
- `/regulations/$N/<ID>`: `#regulations li`, niekoniecznie wszystkie przepisy z treści.
- `/similardocs/$N/<ID>`: link do podobnych orzeczeń; na testowanym dokumencie HTTP 400,
  dlatego helper zwraca tylko odnośnik i nie obiecuje działającego pobierania.
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

## RSS

Lista kanałów: `/rss/courts` (według sądów) i `/rss/themephrases` (według haseł).
Kanał ogólny: `/rsscontent/Orzecznictwo$0020s$0105d$00f3w$0020powszechnych`.
Kanał sądu: `/rsscontent/<8-cyfrowy-kod>`. RSS 2.0: `channel/item`,
`title`, `link`, `pubDate`. W teście kanał ogólny zawierał 100 wpisów;
helper nie zakłada nieograniczonej historii ani nie obiecuje pełnej synchronizacji.
HTTP w linkach centralnego portalu jest podnoszone do HTTPS przed odczytem.
Obce hosty i przekierowania poza centralny portal są odrzucane.

Podportale sądów używają podobnej aplikacji, ale ta wersja helpera czyta wyłącznie
portal centralny. Przy linku do podportalu znajdź orzeczenie po sygnaturze w centrali;
nie przenoś automatycznie ID między hostami bez potwierdzenia.
