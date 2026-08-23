# API Centralnego Rejestru Umów JSFP — referencja endpointów

Baza: `https://rejestrumow.gov.pl/api-dp/v1`  ·  Frontend: `https://rejestrumow.gov.pl`
(Angular SPA; endpointy wyciągnięte z bundli JS aplikacji — **brak publicznego
Swaggera**: `api-dp/v3/api-docs` i `api-dp/swagger-ui` zwracają 401).
Wszystko **read-only**, bez klucza i bez ciasteczek (serwis stoi za CDN Imperva/Incapsula,
ale zwykłe żądania z dowolnym User-Agentem przechodzą). Rejestr jest jawny (art. 34a
ust. 11 u.f.p.); informacje udostępniane są do ponownego wykorzystywania bezpłatnie
(art. 34b ust. 7 u.f.p.); dane wprowadzają i administrują nimi kierownicy JSFP.

## Endpointy (zweryfikowane na żywo, 2026-07; sekcje filtrów i okno ponownie 2026-08-23)

| Ścieżka | Metoda | Zwraca |
|---|---|---|
| `/agreements/search?offset=&limit=&sortKey=` | POST (JSON) | lista umów wg filtrów z body (`{}` = wszystkie) |
| `/agreement/{idUmowy}` | GET | pełne szczegóły umowy (UUID) |
| `/dictionary?name={nazwa}` | GET | słownik kodów (uwaga: wariant ścieżkowy `/dictionary/{nazwa}` → 401) |

Odpowiedź wyszukiwania: `content[] totalElements totalMatchingElements offset limit`.
**`totalElements` jest obcięte do 10 000** (okno wyszukiwania à la Elasticsearch);
realną liczbę trafień pokazuje `totalMatchingElements` — ale **tylko przy `offset < 10 000`**:
przy `offset ≥ 10 000` API zwraca pustą listę i ZANIŻA także `totalMatchingElements` do 10 000
(woj. podlaskie: offset 9950 → 50 wierszy i total 11 076; offset 10 000 → `[]` i total 10 000).
Pusta lista ma więc trzy znaczenia: prawdziwe zero (`totalMatchingElements` = 0), strona poza
zbiorem (offset ≥ total) albo strona poza oknem (offset ≥ 10 000 przy total > 10 000) — silnik
odróżnia je w komunikacie („Brak wyników" / „poza zakresem" / „poza oknem API"). Pełny przegląd
zbiorów > 10 000 rób wycinkami po `dataPublikacjiOd/Do` lub `dataZawarciaOd/Do` (tryb `--strict`
blokuje zbiór > 10 000 jako niekompletny). `offset` jest zaokrąglany w dół do wielokrotności
`limit` (offset 455, limit 50 → strona od 450) — silnik wysyła zawsze `strona × limit`.

## Body wyszukiwania (sekcje → pola)

Sekcje odpowiadają formularzowi UI; wszystkie pola opcjonalne, łączone spójnikiem I (AND).
**Nieznane sekcje i pola są ignorowane PO CICHU** — literówka w nazwie pola lub sekcji = brak
filtra, bez błędu: body `{"zmianyUmowie":{"zmianyUmowy":{"rodzajZmiany":"TSU02"}}}` zwraca
364 470 umów (= cały rejestr, jak `{}`), a poprawne `{"zmianyUmowy":{"rodzajZmiany":"TSU02"}}`
→ 1 056. **Zanim uznasz wynik za odfiltrowany, porównaj total z totalem `{}` (komenda
`najnowsze`).** Daty w filtrach: `RRRR-MM-DD` (odpowiedzi zwracają `DD.MM.RRRR`).

**Trzy sekcje stron umowy** (zweryfikowane 2026-08-23 na Uniwersytecie Wrocławskim,
REGON 000001301 / NIP 8960005408; te same liczby dla `nazwa`, `regon` i `nip`):

| Sekcja | Kogo filtruje | Trafień |
|---|---|---|
| `jsfp.nazwa/regon/nip` | **tylko zamawiający** (JSFP) | 455 |
| `inneStronyUmowy.nazwa/regon/nip` | **tylko druga strona** (wykonawca — także gdy jest nią inna JSFP) | 17 |
| `menuGlowne.nazwa/regon/nip` | **dowolna strona** (pole „szukaj" z UI) | 472 = 455 + 17 |

`nip`/`regon` porównywane są dosłownie (`896-000-54-08` → 0 trafień) — podawaj same cyfry;
`nazwa` działa pełnotekstowo, bez rozróżniania wielkości liter.

| Sekcja | Pola |
|---|---|
| `menuGlowne` | `nazwa regon nip` (DOWOLNA strona umowy), `przedmiotUmowy`, `statusUmowy`, `wartoscOd wartoscDo`, `dataZawarciaOd/Do`, `dataPublikacjiOd/Do` (NIE ma tu `dataZmianyOd/Do` — to sekcja `zmianyUmowy`) |
| `jsfp` (zamawiający) | `nazwa regon nip wojewodztwo powiat gmina miejscowosc ulica numerNieruchomosci numerLokalu kodPocztowy` |
| `inneStronyUmowy` (wykonawca) | `czyKonsorcjum rodzaj` (kod `SU01/SU02/SU03`, nie nazwa) `kraj regon nazwa nip imie nazwisko ulica numerNieruchomosci numerLokalu wojewodztwo powiat gmina miejscowosc kodPocztowy` |
| `daneUmowy` | `numerUmowy brakNumeruUmowy umowaNaCzasNieoznaczony finansowanaZeSrodkow opisWartosciPrzedmiotu okresLiczbaOd/Do okresJednostka dataZakonczeniaUmowyOd/Do czyWylaczenieJawnosci zakresWylaczenia` (`SC02/SC03/SC04`) `podstawa organLubOsobaWylaczajaca` |
| `zmianyUmowy` (sekcja NAJWYŻSZEGO poziomu, bez opakowania) | `rodzajZmiany` (KOD słownika, np. `TSU02` → 1 056, `TSU05` → 1 337, `TSU10` → 112 549, `inne` → 19 145; `TSU01` → 0), `dataZmianyOd/Do` (data wpisu zmiany; `2026-09-01` → 0), `czyZmianyDanychUmowy` (bool: `true` = umowa ma JAKĄKOLWIEK zmianę, 133 710; `false` 230 760), `komentarz` (pełnotekstowo) |
| `inne` | `dataModyfikacjiOd/Do` |

**Zweryfikowane pułapki:**
- `statusUmowy` przyjmuje **dokładnie** `Aktywna` / `Nieaktywna` (case-sensitive;
  `AKTYWNA` → błąd 500 „Nieoczekiwany błąd”); `wojewodztwo` — dowolna wielkość liter;
- `zmianyUmowy.rodzajZmiany` przyjmuje **kod** słownika (`TSU02`, wielkość liter obojętna),
  nie nazwę („Aneks do umowy” → 0 wyników); `TSU01` (korekta danych) zawsze 0 — korekty nie
  są publikowane jako zmiany;
- filtry łączą się spójnikiem I także między sekcjami: `jsfp.regon` + `zmianyUmowy.rodzajZmiany`
  = umowy tego zamawiającego z aneksem (UWr + `TSU02` → 0 — UWr nie ma aneksów, nie błąd);
- `przedmiotUmowy` działa pełnotekstowo na przedmiocie umowy (wielowyrazowe frazy OK);
- `limit` jest obcinany serwerowo do **50**; `sortKey` spoza listy → błąd walidacji
  (`Invalid sort key value`);
- nieistniejący `idUmowy` (i nie-UUID) → **HTTP 500**, nie 404 — silnik waliduje UUID
  przed wysłaniem;
- nieznane ścieżki API (`/api`, `/api-dp/v1/cokolwiek`) → **200 z HTML-em SPA** — nie
  traktuj 200 jako sukcesu bez sprawdzenia Content-Type/JSON.

## `sortKey` (pełna lista wartości)

`unitNameAsc/Desc` (nazwa JSFP), `unitVoivodeshipAsc/Desc`, `unitDistrictAsc/Desc`
(powiat), `unitCommuneAsc/Desc` (gmina), `unitCityAsc/Desc` (miejscowość),
`modificationDateAsc/Desc`, `lastChangeDateAsc/Desc` (data przyczyny aktualizacji),
`publicationDateAsc/Desc`, `executionDateAsc/Desc` (zawarcie), `periodAsc/Desc` (okres),
`priceAsc/Desc` (wartość). Bez `sortKey` sortowanie jest niezdefiniowane (stabilne po id).

## Słowniki (`/dictionary?name=…`)

- `kraje` — kody ISO + nazwy PL,
- `strony_umowy` — `SU01` Przedsiębiorca, `SU02` Osoba fizyczna, `SU03` JSFP,
- `rodzaje_zmian_umowy` — `TSU01` Korekta danych, `TSU02` Aneks do umowy, `TSU03` Zmiana
  w zakresie wyłączenia jawności, `TSU04` Zmiana danych strony, `TSU05` Rozwiązanie,
  `TSU06` Wypowiedzenie, `TSU07` Odstąpienie, `TSU10` Wygaśnięcie, `TSU11` Cesja, `inne`,
- `podstawy_wylaczenia_jawnosci`, `zakres_wylaczenia_jawnosci` — `SC02` Dane strony,
  `SC03` Przedmiot umowy, `SC04` Wartość umowy.

## Kształt danych

**Wiersz wyszukiwania:** `idUmowy` (UUID), `nazwa regon` (JSFP), `dataZawarciaUmowy`,
`dataZakonczeniaUmowy` (null = czas nieoznaczony LUB brak danych), `wartoscPrzedmiotuUmowy`,
`przedmiotUmowy`, `statusUmowy`. Wykonawcy NIE ma w wierszu — jest w szczegółach.

**Szczegóły (`/agreement/{id}`):** `idUmowy`, `podstawoweDane{statusUmowy numerUmowy
brakNumeruUmowy dataZawarciaUmowy dataZakonczeniaUmowy}`, `okresObowiazywania{
umowaNaCzasNieoznaczony okres}` („154 dni”), `szczegolyUmowy{przedmiotUmowy
niejawnoscPrzedmiotu wartoscPrzedmiotu niejawnoscWartosciPrzedmiotu
opisWartosciPrzedmiotu}` (**`opisWartosciPrzedmiotu` to dowolny tekst wpisany przez jednostkę
— „opis wartości", np. „zakup środków czystości"; BYWA kwotą słownie, często `null`, także
przy umowach na miliardy — nie traktuj go jak pola kontrolnego kwoty**), `stronyUmowy[{kraj rodzaj nazwa nip
regon imie nazwisko czyKonsorcjum daneAdresowe{ulica numerNieruchomosci numerLokalu
wojewodztwo powiat gminaMiastoDzielnica miejscowosc kodPocztowy} niejawnoscStrony}]`
(`rodzaj` ∈ JSFP | Przedsiębiorca | Osoba fizyczna), `finansowanaZeSrodkow` (bool —
środki z art. 5 ust. 1 pkt 2–3 u.f.p.), `zmianyUmowy[{rodzajZmiany dataZmiany
komentarz}]` (w szczegółach `rodzajZmiany` to NAZWA — „Aneks do umowy"; w filtrze — KOD
`TSU02`), `dataPublikacji`, `dataModyfikacji`. Bloki wyłączenia jawności (`niejawnoscStrony`,
`niejawnoscPrzedmiotu`, `niejawnoscWartosciPrzedmiotu`) mają kształt `{podstawa zakres
organLubOsobaWylaczajaca komentarz}` (każde pole może być `null`; przy niejawnej stronie
`nazwa/nip/regon/daneAdresowe` są puste) — silnik drukuje je jako „zakres: …; podstawa: …;
wyłączający: …; komentarz: …".

Link do umowy w przeglądarce: `https://rejestrumow.gov.pl/umowa/{idUmowy}`.

## Mapowanie komend `rejestrumow.py` → API

`najnowsze` → `{}` + `sortKey=publicationDateDesc`; `szukaj FRAZA` →
`menuGlowne.przedmiotUmowy`; `--jsfp/--regon/--nip` → **`jsfp.nazwa/regon/nip`** (tylko
zamawiający; `--rola dowolna` → `menuGlowne.*` = dowolna strona, `--rola wykonawca` →
`inneStronyUmowy.*`); `--wykonawca(-nip/-regon)` → `inneStronyUmowy.nazwa/nip/regon`;
`--woj/--powiat/--gmina/--miejscowosc` → `jsfp.*` (adres zamawiającego — także przy
`--rola dowolna`); `--od/--do` → `dataZawarciaOd/Do`, `--pub-od/--pub-do` →
`dataPublikacjiOd/Do`, `--wartosc-od/-do` → `wartoscOd/Do`, `--status` → `statusUmowy`;
`--zmiana-rodzaj KOD` → `zmianyUmowy.rodzajZmiany`, `--zmiana-od/--zmiana-do` →
`zmianyUmowy.dataZmianyOd/Do`; `--zapytanie '<json>'` → body przekazane wprost (bez walidacji
nazw pól — patrz „ignorowane po cichu"); `--limit/--strona` → `limit/offset = strona × limit`
(limit > 50 → obcięty do 50 z komunikatem na stderr; `--strona` ≥ 10 000/limit → silnik nie
pyta o tę stronę, pobiera realny total i kończy komunikatem „poza oknem API"); `umowa` →
`/agreement/{uuid}`; `slownik` → `/dictionary?name=…`.

## Wskazówki

- Rejestr ruszył **1.07.2026** (najstarsza `dataZawarciaUmowy` = 01.07.2026) i rośnie
  o kilkanaście tysięcy umów dziennie — wyniki szybko się dezaktualizują, podawaj datę
  sprawdzenia.
- Dane wpisują jednostki (kierownicy JSFP są ich administratorami) — jakość nierówna:
  skróty nazw, literówki, daty obchodzone aneksami („system uniemożliwił wprowadzenie
  daty wstecznej” w komentarzach zmian). Do identyfikacji podmiotów używaj NIP/REGON.
- Bądź uprzejmy dla serwera: ≤2 zapytania/s (limitów nie udokumentowano).
- Osobny portal `jsfp.rejestrumow.gov.pl` służy jednostkom do wprowadzania danych
  (wymaga logowania — poza zakresem skilla).
