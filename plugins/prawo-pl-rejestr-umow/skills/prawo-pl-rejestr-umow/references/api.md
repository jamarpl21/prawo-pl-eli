# API Centralnego Rejestru Umów JSFP — referencja endpointów

Baza: `https://rejestrumow.gov.pl/api-dp/v1`  ·  Frontend: `https://rejestrumow.gov.pl`
(Angular SPA; endpointy wyciągnięte z bundli JS aplikacji — **brak publicznego
Swaggera**: `api-dp/v3/api-docs` i `api-dp/swagger-ui` zwracają 401).
Wszystko **read-only**, bez klucza i bez ciasteczek (serwis stoi za CDN Imperva/Incapsula,
ale zwykłe żądania z dowolnym User-Agentem przechodzą). Rejestr jest jawny (art. 34a
ust. 11 u.f.p.); informacje udostępniane są do ponownego wykorzystywania bezpłatnie
(art. 34b ust. 7 u.f.p.); dane wprowadzają i administrują nimi kierownicy JSFP.

## Endpointy (zweryfikowane na żywo, 2026-07)

| Ścieżka | Metoda | Zwraca |
|---|---|---|
| `/agreements/search?offset=&limit=&sortKey=` | POST (JSON) | lista umów wg filtrów z body (`{}` = wszystkie) |
| `/agreement/{idUmowy}` | GET | pełne szczegóły umowy (UUID) |
| `/dictionary?name={nazwa}` | GET | słownik kodów (uwaga: wariant ścieżkowy `/dictionary/{nazwa}` → 401) |

Odpowiedź wyszukiwania: `content[] totalElements totalMatchingElements offset limit`.
**`totalElements` jest obcięte do 10 000** (okno wyszukiwania à la Elasticsearch);
realną liczbę trafień pokazuje `totalMatchingElements`. `offset ≥ 10 000` zwraca pustą
listę — pełny przegląd rób wycinkami po `dataPublikacjiOd/Do` lub `dataZawarciaOd/Do`.

## Body wyszukiwania (sekcje → pola)

Sekcje odpowiadają formularzowi UI; wszystkie pola opcjonalne, łączone spójnikiem I (AND).
**Nieznane sekcje i pola są ignorowane PO CICHU** — literówka w nazwie pola = brak filtra,
bez błędu. Daty w filtrach: `RRRR-MM-DD` (odpowiedzi zwracają `DD.MM.RRRR`).

| Sekcja | Pola |
|---|---|
| `menuGlowne` | `nazwa regon nip` (JSFP-zamawiający), `przedmiotUmowy`, `statusUmowy`, `wartoscOd wartoscDo`, `dataZawarciaOd/Do`, `dataPublikacjiOd/Do`, `dataZmianyOd/Do` |
| `jsfp` | `nazwa regon nip wojewodztwo powiat gmina miejscowosc ulica numerNieruchomosci numerLokalu kodPocztowy` |
| `daneUmowy` | `numerUmowy brakNumeruUmowy umowaNaCzasNieoznaczony finansowanaZeSrodkow opisWartosciPrzedmiotu okresLiczbaOd/Do okresJednostka dataZakonczeniaUmowyOd/Do czyWylaczenieJawnosci zakresWylaczenia podstawa organLubOsobaWylaczajaca` |
| `zmianyUmowie` → `zmianyUmowy` | `rodzajZmiany` (KOD słownika, np. `TSU02` — patrz niżej), `dataZmianyOd/Do`, `komentarz`, `czyZmianyDanychUmowy` |
| `inneStronyUmowy` (wykonawca) | `czyKonsorcjum rodzaj kraj regon nazwa nip imie nazwisko ulica numerNieruchomosci numerLokalu wojewodztwo powiat gmina miejscowosc kodPocztowy` |
| `inne` | `dataModyfikacjiOd/Do` |

**Zweryfikowane pułapki:**
- `statusUmowy` przyjmuje **dokładnie** `Aktywna` / `Nieaktywna` (case-sensitive;
  `AKTYWNA` → błąd 500 „Nieoczekiwany błąd”); `wojewodztwo` — dowolna wielkość liter;
- `zmianyUmowy.rodzajZmiany` przyjmuje **kod** słownika (`TSU02`), nie nazwę
  („Aneks do umowy” → 0 wyników);
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
opisWartosciPrzedmiotu}` (opis = kwota słownie), `stronyUmowy[{kraj rodzaj nazwa nip
regon imie nazwisko czyKonsorcjum daneAdresowe{ulica numerNieruchomosci numerLokalu
wojewodztwo powiat gminaMiastoDzielnica miejscowosc kodPocztowy} niejawnoscStrony}]`
(`rodzaj` ∈ JSFP | Przedsiębiorca | Osoba fizyczna), `finansowanaZeSrodkow` (bool —
środki z art. 5 ust. 1 pkt 2–3 u.f.p.), `zmianyUmowy[{rodzajZmiany dataZmiany
komentarz}]`, `dataPublikacji`, `dataModyfikacji`.

Link do umowy w przeglądarce: `https://rejestrumow.gov.pl/umowa/{idUmowy}`.

## Mapowanie komend `rejestrumow.py` → API

`najnowsze` → `{}` + `sortKey=publicationDateDesc`; `szukaj FRAZA` →
`menuGlowne.przedmiotUmowy`, `--jsfp/--regon/--nip` → `menuGlowne.nazwa/regon/nip`,
`--wykonawca(-nip/-regon)` → `inneStronyUmowy.nazwa/nip/regon`,
`--woj/--powiat/--gmina/--miejscowosc` → `jsfp.*`, `--od/--do` → `dataZawarciaOd/Do`,
`--pub-od/--pub-do` → `dataPublikacjiOd/Do`, `--wartosc-od/-do` → `wartoscOd/Do`,
`--status` → `statusUmowy`, `--zapytanie '<json>'` → body przekazane wprost;
`--limit/--strona` → `limit/offset`; `umowa` → `/agreement/{uuid}`;
`slownik` → `/dictionary?name=…`.

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
