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
  nie istnieje” (nieznane ID, tytuł „Błąd - Portal Orzeczeń…”). 2026-10-05 „Błąd danych”
  zwracała zakładka „Treść” (także w przeglądarce i na podportalach sądów) dla 9 z 14
  sprawdzonych dokumentów — wszystkie świeżo opublikowane i bez zakładek „Powołane przepisy”/
  „Orzeczenia podobne” w metryce; dla nich `/regulations` daje 200 z pustym `<ol id="regulations">`.
  Dalsza weryfikacja 2026-10-05 (ok. 45 dokumentów, curl): treść zwraca każdy dokument opublikowany
  do 2026-09-23, „Błąd danych” — każdy opublikowany od 2026-09-24, niezależnie od sądu; to samo na
  podportalach sądów. Zaplecze `https://apiorzeczenia.wroclaw.sa.gov.pl/ncourt-api/judgement/details?id=<ID>`
  oddaje dla nich metrykę (XML), a SAOS (który stamtąd importuje) — pustą treść. Awaria po stronie MS.
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
