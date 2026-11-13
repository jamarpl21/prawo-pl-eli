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
| `sygnatura` | np. `II FSK 2870/18` — dopasowanie **po członach**, nie całości: `SA/Wa 1/20` łapie `I SA/Wa 1/20` … `VIII SA/Wa 1/20` (8 różnych spraw), `SA/Sz` z `doDaty=2003-12-31` — 622 trafienia (na 1. stronie wyroki NSA oz. w Szczecinie); `II SA/Wa 1/20` nie łapie `VIII SA/Wa 1/20`, a `SA/Sz 833/03` — tylko orzeczenie NSA oz. (sprawdzone 13.11.2026) |
| `sad` | `dowolny` \| **PEŁNA nazwa**: `Naczelny Sąd Administracyjny`, `Wojewódzki Sąd Administracyjny w Warszawie`, … (`we Wrocławiu`, `w Gorzowie Wlkp.`); też historyczne `NSA oz. w …` |
| `rodzaj` | `dowolny` \| `Wyrok` \| `Postanowienie` \| `Uchwała` |
| `symbole` | symbol sprawy, np. `6119`; `647` = sprawy związane z ochroną danych osobowych |
| `rodzaj_organu` | rodzaj skarżonego organu — CBOSA dopasowuje **fragment** nazwy ze słownika (`GET /cbo/servlet/slownik?sl=rodzaj_organu&naz=*&sad=dowolny`). Sprawy Prezesa UODO mają przeważnie wartość `Generalny Inspektor Ochrony Danych Osobowych` (nieliczne: `Prezes Urzędu Ochrony Danych Osobowych`) — fragment `Ochrony Danych Osobowych` łapie obie (sprawdzone 2026-10-05) |
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
  zapytania** — CBOSA w ogóle nie szukało). `Błąd przy szukaniu orzeczeń` to **awaria serwera**
  (2026-10-05: to samo zapytanie po kilku minutach dało 297 trafień), nie odrzucone zapytanie.
  Strona bez licznika i bez `warning` = przeciążenie serwera/strona błędu; ponawiać warto tylko przy
  awarii serwera i takiej stronie.
- **daty na liście:** jedyna data orzeczenia to ta w tekście linku (`… z 2021-02-10`); daty w snippecie
  stoją przy powołanych przepisach (np. `t.j. 2020-05-25`) i nie dotyczą orzeczenia. Daty publikacji
  lista nie podaje.
- `GET /cbo/find?p=N` za końcem wyników (`N` > liczby stron) nie zawiera żadnej pozycji — silnik zgłasza
  to kodem 2 z licznikiem ze strony 1, nie jako „zweryfikowane zero”.

## Strona orzeczenia: `GET /doc/{DOC_ID}`

- `<TITLE>II FSK 2870/18 - Wyrok NSA z 2021-02-10</TITLE>` (sygnatura = część przed ` - `)
- metadane — pary komórek: `<td class="info-list-label">Etykieta</td><td class="info-list-value">wartość</td>`.
  Etykiety: `Data orzeczenia`, `Data wpływu`, `Sąd`, `Sędziowie` (z funkcją `/przewodniczący sprawozdawca/`),
  `Symbol z opisem`, `Hasła tematyczne`, `Sygn. powiązane` (linki `/doc/…`), `Skarżony organ`,
  `Treść wyniku`, `Powołane przepisy` (Dz.U. + artykuły + tytuł aktu), rzadziej `Publikacja w u.z.o.`
  (publikacja w urzędowym zbiorze, np. „ONSAiWSA z 2009 r. nr 2, poz.19” — uchwała II FPS 6/08)
  i `Info. o glosach`. `Treść wyniku` bywa pusta (starsze orzeczenia, np. `SA/Sz 833/03`, `OSK 150/04`)
  albo z artefaktami źródła (`*Oddalono skargę`, `Wyłączono sędziego _bezWO`) — silnik ich nie poprawia.
- **daty:** strona ma WYŁĄCZNIE dwie daty — `Data orzeczenia` i `Data wpływu` (wpływ sprawy do sądu).
  Nie ma daty publikacji ani udostępnienia w CBOSA, daty uprawomocnienia ani daty sporządzenia
  uzasadnienia (sprawdzone 13.11.2026 na 39 stronach z lat 2003–2026: NSA, NSA oz., WSA w Warszawie,
  Białymstoku, Gdańsku, Krakowie i we Wrocławiu; wyroki, postanowienia, uchwała). Silnik w tekście
  drukuje tylko `Data orzeczenia`; `Data wpływu` jest w JSON (`metadane`).
- **prawomocność** — JEDYNA komórka z zagnieżdżoną tabelą: wartość `Data orzeczenia` to
  `<table …><tr><td>2018-06-06</td><td style="… font-style: italic;">orzeczenie nieprawomocne</td></tr></table>`
  (albo `orzeczenie prawomocne`; bywa **puste** `&nbsp;` — zweryfikowane 2026-08 na postanowieniu
  o wstrzymaniu wykonania `VI SA/Wa 707/26`, doc 1A8B8B8130). Ogólny regex metadanych czyta z niej tylko
  datę; oznaczenie parsuje osobno `_prawomocnosc()` → JSON `prawomocne: true|false|null` i `prawomocnosc`
  (`"orzeczenie prawomocne"` / `"orzeczenie nieprawomocne"` / `""` = pole puste / `null` = pola nie znaleziono).
  Zweryfikowane na żywo: 109C7D1883 (WSA, nieprawomocne — uchylony przez NSA w II FSK 2870/18),
  8889489BE0 i DC7DA8E6E8 (prawomocne), F438A0BAA7 (uchwała NSA, prawomocne).
  **Strona wyników wyszukiwarki oznaczenia nie ma** (jedyne „prawomocn” w `raw_search` to treść snippetów).
  **Oznaczenie bywa niekonsekwentne:** wyrok WSA `II SA/Wa 1341/22` ma „orzeczenie prawomocne”, choć NSA
  (`III OSK 3311/23`) uchylił go wraz z decyzją; `II SA/Wa 2559/19`, `II SA/Wa 863/23`, `II SA/Wa 1050/19`
  i `II SA/Wa 3024/21` w tej samej sytuacji są „nieprawomocne” (audyt 13.11.2026). Dlatego wyrok WSA
  z powiązanym wyrokiem NSA dostaje UWAGA także przy „prawomocne” (tylko orzeczenia WSA — tytuł strony
  z „WSA”; nie NSA oz. sprzed 2004 r. z sygnaturą `SA/Sz …`).
- **nieistniejący doc_id:** `GET /doc/{id}` → HTTP 410 (Gone); silnik: błąd identyfikatora, kod 2.
- sekcje treści: `<div class="lista-label">Sentencja</div><span class="info-list-value-uzasadnienie">…</span>`
  — analogicznie `Tezy` (jeśli są) i `Uzasadnienie`. Sekcji `Uzasadnienie` **może w ogóle nie być**
  (np. `II SA/Wa 2559/19` — postanowienie o wyłączeniu sędziego, doc 6E19266905; `II SA/Wa 622/26` —
  wyrok z 2026-10-05, tylko sentencja): `--fragment` kończy się wtedy kodem 2, nie „brakiem frazy”.

## Tryb `--strict` — co sprawdza, czego nie

| Kontrola | Efekt w `--strict` | Bez `--strict` |
|---|---|---|
| TLS niezweryfikowany (`CBOSA_INSECURE_TLS=1`) | blokada (`VerificationUnknown`) | wynik + adnotacja, JSON `transport_tls_verified:false` |
| strona `/doc/{id}` nierozpoznana / wyszukiwarka bez licznika i listy | blokada (UNKNOWN, „spróbuj ponownie") | to samo — zawsze |
| `orzeczenie`: oznaczenie **nieprawomocne** | blokada: „orzeczenie nieprawomocne — tryb strict nie zwraca treści, której aktualność nie jest potwierdzona; bez --strict dostaniesz tekst z ostrzeżeniem” + sygnatury powiązane | pełny tekst, `Prawomocność: NIEPRAWOMOCNE`, UWAGA na początku, JSON `prawomocne:false` + `uwaga` |
| `orzeczenie`: pole prawomocności puste / nieznalezione | blokada (aktualność niepotwierdzona) | pełny tekst, `Prawomocność: nieznana`, UWAGA, JSON `prawomocne:null` |
| `orzeczenie`: oznaczenie prawomocne | przechodzi (także wyrok WSA z powiązanym wyrokiem NSA — tylko UWAGA) | `Prawomocność: prawomocne`, JSON `prawomocne:true`; przy wyroku WSA z powiązanym wyrokiem NSA UWAGA i JSON `uwaga` |
| `szukaj` / `sygnatura`: prawomocność pozycji | **nie sprawdza** — lista CBOSA nie niesie flagi | j.w.; sprawdź przez `orzeczenie <doc_id>` |

Wszystkie kontrole wykonują się PRZED emisją wyniku (także `--json`): przy blokadzie stdout jest pusty,
komunikat idzie na stderr, kod wyjścia 2. Strict NIE ocenia: ostateczności wyroku NSA (wznowienie, skarga
nadzwyczajna), aktualności powołanych przepisów (→ ELI), ani tego, czy NSA zmienił linię w innej sprawie.

## Kody wyjścia `cbosa.py`

| Kod | Kiedy |
|---|---|
| 0 | wynik wydrukowany |
| 1 | tylko ZWERYFIKOWANY brak: rozpoznana strona „Nie znaleziono orzeczeń…” dla `szukaj` (strona 1) albo `sygnatura`; frazy `--fragment` nie ma w pobranym uzasadnieniu |
| 2 | wszystko inne: błąd sieci/HTTP, awaria serwera, „Błąd przy szukaniu orzeczeń”, strona bez licznika i listy, „CBOSA odrzuciło zapytanie”, licznik > 0 bez pozycji (`--strona` za końcem), zły argument, nieistniejący doc_id (410), zero dla numeru decyzji organu, `--fragment` bez sekcji „Uzasadnienie”, blokada `--strict`, nieprzewidziany wyjątek |

Przy kodzie ≠ 0 stdout jest pusty (także z `--json`), komunikat idzie na stderr. Mechanizm
(`BrakWynikow(SystemExit)` + blok `try/except` w `main()`) jest wspólny z `saos.py` i `uodo.py`.

## Mapowanie komend `cbosa.py` → pola

`szukaj FRAZA`→`wszystkieSlowa`, `--sad`→`sad` (alias→pełna nazwa), `--sygnatura`→`sygnatura`,
`--rodzaj`→`rodzaj`, `--symbol`→`symbole`, `--organ`→`rodzaj_organu` (alias `UODO`/`GIODO`→
`Ochrony Danych Osobowych`), `--sedzia`→`sedziowie`, `--od/--do`→`odDaty/doDaty`
(skróty `RRRR` i `RRRR-MM` silnik uzupełnia do początku okresu dla `--od`, do końca dla `--do`),
`--strona N`→`GET /cbo/find?p=N` (po POST). `sygnatura <S>` = `szukaj` z samym polem `sygnatura`.

## Wskazówki

- **Throttling ≥0,5 s** między żądaniami (wbudowany, w obrębie jednego wywołania). Serwer bywa
  przeciążony i ucina połączenia bez odpowiedzi — silnik ponawia z rosnącym odstępem (~26 s);
  nie zrównoleglaj zapytań (także z kilku agentów naraz).
- **Diagnoza błędów sieci** (`_diagnoza` w silniku): odrzucenie przez proxy („Tunnel connection
  failed: 403”, albo brak połączenia przy ustawionym proxy) = blokada środowiska, bez ponawiania;
  DNS / połączenie odrzucone / sieć nieosiągalna = brak sieci w środowisku (jedna krótka
  ponowna próba); połączenie zerwane po nawiązaniu (RemoteDisconnected, reset, broken pipe, EOF
  w TLS) albo HTTP 5xx = awaria serwera CBOSA (pełne ponawianie, potem „ponów za kilka–kilkanaście
  minut”); timeout = wolny serwer albo niestabilne łącze.
- **SSL:** gdy system nie potrafi zweryfikować łańcucha certyfikatów CBOSA, domyślnym wynikiem jest
  `UNKNOWN`, bez automatycznego obniżenia zabezpieczeń. Jednorazowy, świadomy opt-in
  `CBOSA_INSECURE_TLS=1 python3 scripts/cbosa.py ...` pozwala ponowić żądanie bez weryfikacji TLS.
  Taki wynik ma w JSON pole `transport_tls_verified: false`, a w formacie tekstowym wyraźną
  adnotację przy treści. Nie cytuj jej bez sprawdzenia w innym źródle.
- **Symbole spraw** (pole `symbole`): 4-cyfrowe oznaczenia repertoriów, np. `611x` podatki
  (6112 PIT, 6110 VAT), `6014` prawo budowlane, `6320` pomoc społeczna, `6480` informacja publiczna.
  Pełny wykaz: zarządzenie Prezesa NSA (dostępne na stronach NSA).
- **Anonimizacja numerów decyzji:** CBOSA zastępuje w sentencji i uzasadnieniu numer i dzień
  zaskarżonej decyzji (`decyzję … z dnia [...] marca 2025 r. nr [...]`) — wyszukiwanie po znaku
  sprawy organu (`DKN.5131.1.2025`) daje zero, które NIE dowodzi braku wyroku. Szukaj po
  `rodzaj_organu` + `symbole` + `sad` + `odDaty` (data decyzji) i frazie `"<miesiąc> <rok>"`
  (np. DKN.5131.1.2025 → II SA/Wa 837/25, 1. strona wyników). Numer bywa jednak w treści — NSA
  `III OSK 1251/22` i `III OSK 3311/23` cytują pełne numery decyzji (`DKN.5130.3114.2020.121220`,
  `DKN.5131.51.2021.172158`) — a wyszukiwarka i tak ich nie znajduje (`szukaj "DKN.5130.3114.2020"`
  i z pełnym numerem: zero, 13.11.2026). Stąd kod 2, nie 1.
- **Powiązane instancje:** wyrok NSA linkuje wyrok WSA tej samej sprawy (i odwrotnie) — pole
  `Sygn. powiązane` z doc_id; tak buduje się pełną historię sprawy. Pole bywa błędne: II SA/Wa 546/22
  (skarga na decyzję UODO) i III OSK 377/23 linkują II SA/Wa 546/21 — wyrok w innej sprawie (Policja);
  przed złożeniem historii sprawdź stronę skarżącą i przedmiot (audyt 13.11.2026).
- Alternatywa dla SN/TK/sądów powszechnych/KIO: API SAOS (skill prawo-pl-saos) — tam CBOSA nie sięga.
