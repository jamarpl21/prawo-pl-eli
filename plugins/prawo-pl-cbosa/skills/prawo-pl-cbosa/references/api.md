# CBOSA — kontrakt HTML (brak oficjalnego API)

Baza: `https://orzeczenia.nsa.gov.pl` — Centralna Baza Orzeczeń Sądów Administracyjnych (NSA + 16 WSA,
~2,4 mln orzeczeń od 2004 r.). **NSA nie udostępnia API ani zrzutów danych** (mimo petycji KIDP z powołaniem
na ustawę o otwartych danych) — poniżej udokumentowany kontrakt de facto: publiczne strony HTML, które czyta
`cbosa.py`. Baza ma charakter informacyjno-edukacyjny; orzeczenia są zanonimizowane. Codzienne okno
serwisowe ok. 21:00. Wszystkie pola i regexy zweryfikowane na żywych stronach (2026-07).

## Wyszukiwanie: `POST /cbo/search`

`Content-Type: application/x-www-form-urlencoded` (UTF-8). Pola formularza (z `/cbo/query`):

| Pole | Wartości / format |
|---|---|
| `wszystkieSlowa` | fraza pełnotekstowa |
| `wystepowanie` | `gdziekolwiek` \| `w sentencji` \| `w tezach` \| `w uzasadnieniu` |
| `odmiana` | `on` (uwzględnij odmianę wyrazów) |
| `sygnatura` | np. `II FSK 2870/18` |
| `sad` | `dowolny` \| **PEŁNA nazwa**: `Naczelny Sąd Administracyjny`, `Wojewódzki Sąd Administracyjny w Warszawie`, … (`we Wrocławiu`, `w Gorzowie Wlkp.`); też historyczne `NSA oz. w …` |
| `rodzaj` | `dowolny` \| `Wyrok` \| `Postanowienie` \| `Uchwała` |
| `symbole` | symbol sprawy, np. `6119` |
| `odDaty`, `doDaty` | `RRRR-MM-DD` — **wyłącznie ten format**; inny (np. `2024`, `31-12-2024`) zwraca formularz z komunikatem „Niepoprawny format daty, podaj RRRR-MM-DD!" i BEZ listy wyników |
| `sedziowie` | nazwisko |
| `funkcja` | `dowolna` \| `przewodniczący` \| `sprawozdawca` \| `autor uzasadnienia` |
| `submit` | `Szukaj` |

**PUŁAPKA 1:** pole `sad` przyjmuje pełny TEKST opcji — wartość spoza listy (np. `0`) po cichu zwraca
0 wyników zamiast błędu. `cbosa.py` mapuje aliasy (`NSA`, `WSA <miasto>`) na pełne nazwy.

**PUŁAPKA 2 (zakres dat otwarty od góry):** wypełnione `odDaty` przy PUSTYM `doDaty` po cichu zwraca
**0 wyników** — puste `doDaty` działa jak górna granica sprzed początku zakresu (zweryfikowane:
`odDaty=2024-01-01, doDaty=""` → 0; `doDaty=2099-12-31` → 421 trafień na tej samej frazie).
Odwrotnie jest poprawnie: samo `doDaty` (z pustym `odDaty`) filtruje „do dnia". `cbosa.py` przy samym
`--od` dostawia `doDaty=2099-12-31` (stała `DO_OTWARTE`).

## Paginacja

POST zwraca stronę 1 + ciasteczka sesji (`Set-Cookie`). Kolejne strony: `GET /cbo/find?p=N`
**z odesłaniem ciasteczek** (stan wyszukiwania trzymany w sesji). Stała wielkość strony: 10 wyników.

## Strona wyników — struktura HTML

- liczba trafień: `Znaleziono <N> orzeczeń, Str. X z Y`
- pozycja główna: `<td class="info-list-value " style="font-size: 12pt…"> <a href="/doc/{DOC_ID}">II FSK 2870/18 - Wyrok NSA z 2021-02-10</a>`
  (`DOC_ID` = 10 znaków hex); po niej komórka `font-size: 10pt` ze snippetem (treść wyniku, symbole,
  skarżony organ, powołane przepisy)
- orzeczenia powiązane (ta sama sprawa w innej instancji): `<span class="powiazane"><a href="/doc/…">…</a></span>`
- **komunikaty formularza:** `<div class="warning">…</div>` na stronie BEZ licznika „Znaleziono N".
  Znane treści: `Nie znaleziono orzeczeń spełniających podany warunek!` (**zweryfikowane zero** —
  zapytanie wykonane, brak trafień) oraz `Niepoprawny format daty, podaj RRRR-MM-DD!` (**błąd
  zapytania** — CBOSA w ogóle nie szukało). Strona bez licznika i bez `warning` = przeciążenie
  serwera/strona błędu; tylko wtedy warto ponawiać.

## Strona orzeczenia: `GET /doc/{DOC_ID}`

- `<TITLE>II FSK 2870/18 - Wyrok NSA z 2021-02-10</TITLE>` (sygnatura = część przed ` - `)
- metadane — pary komórek: `<td class="info-list-label">Etykieta</td><td class="info-list-value">wartość</td>`.
  Etykiety: `Data orzeczenia`, `Data wpływu`, `Sąd`, `Sędziowie` (z funkcją `/przewodniczący sprawozdawca/`),
  `Symbol z opisem`, `Hasła tematyczne`, `Sygn. powiązane` (linki `/doc/…`), `Skarżony organ`,
  `Treść wyniku`, `Powołane przepisy` (Dz.U. + artykuły + tytuł aktu)
- sekcje treści: `<div class="lista-label">Sentencja</div><span class="info-list-value-uzasadnienie">…</span>`
  — analogicznie `Tezy` (jeśli są) i `Uzasadnienie` (bywa nieopublikowane)

## Mapowanie komend `cbosa.py` → pola

`szukaj FRAZA`→`wszystkieSlowa`, `--sad`→`sad` (alias→pełna nazwa), `--sygnatura`→`sygnatura`,
`--rodzaj`→`rodzaj`, `--symbol`→`symbole`, `--sedzia`→`sedziowie`, `--od/--do`→`odDaty/doDaty`
(skróty `RRRR` i `RRRR-MM` silnik uzupełnia do początku okresu dla `--od`, do końca dla `--do`),
`--strona N`→`GET /cbo/find?p=N` (po POST). `sygnatura <S>` = `szukaj` z samym polem `sygnatura`.

## Wskazówki

- **Throttling ≥0,5 s** między żądaniami (wbudowany). Serwer bywa przeciążony i ucina połączenia bez
  odpowiedzi — silnik ponawia z rosnącym odstępem; nie zrównoleglaj zapytań.
- **SSL:** CBOSA serwuje niekompletny łańcuch certyfikatów — na części systemów weryfikacja pada;
  silnik przechodzi wtedy (tylko dla tego hosta) na kontekst bez weryfikacji łańcucha (dane publiczne).
- **Symbole spraw** (pole `symbole`): 4-cyfrowe oznaczenia repertoriów, np. `611x` podatki
  (6112 PIT, 6110 VAT), `6014` prawo budowlane, `6320` pomoc społeczna, `6480` informacja publiczna.
  Pełny wykaz: zarządzenie Prezesa NSA (dostępne na stronach NSA).
- **Powiązane instancje:** wyrok NSA linkuje wyrok WSA tej samej sprawy (i odwrotnie) — pole
  `Sygn. powiązane` z doc_id; tak buduje się pełną historię sprawy.
- Alternatywa dla SN/TK/sądów powszechnych/KIO: API SAOS (skill prawo-pl-saos) — tam CBOSA nie sięga.
