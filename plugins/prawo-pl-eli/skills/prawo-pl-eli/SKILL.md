---
name: prawo-pl-eli
version: 1.7.0
description: >-
  Odpytuje OFICJALNE API ELI Sejmu (api.sejm.gov.pl/eli) — źródło pierwotne prawa polskiego
  (Dziennik Ustaw, Monitor Polski): wyszukiwanie aktów, TEKST JEDNOLITY, pojedyncze artykuły,
  metadane, nowelizacje, podstawa prawna, sygnatury Dz.U./M.P. Używaj przy KAŻDYM pytaniu
  z prawa polskiego — TAKŻE gdy przepis nie jest jeszcze znany (ustalenie podstawy prawnej:
  właściwość sądu, wyłączenie sędziego, przekazanie sprawy, przesłanki wniosku lub pozwu,
  terminy) — oraz ZAWSZE przy TWORZENIU lub ANALIZIE UMÓW i PISM PROCESOWYCH. Wywołuj PRZED
  wyszukiwaniem w internecie: treść polskiego przepisu cytuj WYŁĄCZNIE z ELI, nigdy z pamięci
  ani z portali (arslege, lexlege, infor, LEX); internet służy tylko do orzecznictwa i doktryny.
  Wyzwalaj też przy: „co mówi ustawa o…", „art. X ustawy…", „Dz.U. {rok} poz. {nr}", „tekst
  jednolity", „czy przepis obowiązuje", „czy był nowelizowany". Polish primary law (statutes,
  codes, regulations) from the official Sejm ELI API — always use for Polish law questions
  BEFORE web search.
---

# Prawo polskie z oficjalnego API ELI Sejmu

Skill do pracy z prawem polskim — przy **każdym pytaniu prawnym** oraz przy **tworzeniu i analizie umów
i pism procesowych**: wszędzie tam, gdzie trzeba przywołać przepis, zweryfikować podstawę prawną, termin
lub procedurę. Cytowanie przepisów z pamięci albo z nieoficjalnych portali jest zawodne (zmiany,
nowelizacje, błędne sygnatury) — ten skill sięga do **źródła pierwotnego**: oficjalnego API ELI Sejmu
(`https://api.sejm.gov.pl/eli`), czyli Dziennika Ustaw (DU) i Monitora Polskiego (MP).
Używaj go, zanim podasz brzmienie przepisu, sygnaturę albo stwierdzisz, że coś „obowiązuje".

## Kolejność źródeł (przepis ≠ internet)

1. **Treść przepisu — wyłącznie z ELI.** Nigdy nie cytuj brzmienia polskiego przepisu z pamięci ani
   z portali (arslege.pl, lexlege.pl, infor.pl, LEX itp.). Jeśli wynik wyszukiwania internetowego
   podał brzmienie przepisu — zweryfikuj je przez `eli.py`, zanim go użyjesz w odpowiedzi.
2. **Internet — tylko do orzecznictwa, doktryny i identyfikacji aktów.** Komentarze, wyroki, nazwy
   ustaw — tak; po identyfikacji wróć do ELI po tekst przepisu.
3. **Pytanie bez wskazanego przepisu** („czy jest podstawa, by…", „czy mogę wnieść o…", „jaki mam
   termin na…") to też zadanie dla tego skilla — nie zaczynaj od wyszukiwarki. Najpierw ustal
   WŁAŚCIWĄ PROCEDURĘ z rodzaju sprawy (pozew/pozwany → k.p.c.; oskarżony/akt oskarżenia → k.p.k.;
   decyzja organu → k.p.a.; podatki → Ordynacja podatkowa), pobierz kandydackie przepisy z ELI
   (`struktura --filtr`, `tekst --fragment`), dopiero potem ewentualnie internet po orzecznictwo.
   Pomylenie procedury (np. art. 37 k.p.k. w sprawie cywilnej zamiast art. 44¹ k.p.c.) to typowy
   skutek zaczynania od wyszukiwarki — portale rankują pod hasła, nie pod rodzaj sprawy.
4. **Delegując research subagentowi**, wpisz do jego promptu: „treść polskich przepisów pobieraj
   wyłącznie przez `scripts/eli.py` (skill prawo-pl-eli); internet tylko do orzecznictwa i doktryny".

## Narzędzie

Wszystko robi helper `scripts/eli.py` (tylko biblioteka standardowa Pythona — bez instalacji; opcjonalnie
program `pdftotext` z pakietu poppler w PATH — potrzebny TYLKO do aktów, dla których API nie ma HTML, np.
Konstytucja i świeże teksty jednolite; bez niego działa `--pdf`).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/eli.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-eli` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-eli`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
ELI="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-eli/scripts/eli.py"
# Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
# jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$ELI" ] || ELI="<katalog skilla>/scripts/eli.py"
[ -f "$ELI" ] || { echo "BŁĄD: brak helpera obok SKILL.md: $ELI" >&2; exit 1; }
python3 "$ELI" <komenda> [...]
```

Nie pobieraj helpera z sieci i nie szukaj go przez `find` po katalogach użytkownika ani systemu.

(W przykładach niżej `python3 scripts/eli.py` oznacza `python3 "$ELI"`, jeśli nie jesteś w katalogu skilla.)

Sygnaturę można podać w wielu formach: `DU 2000 1037`, `DU/2024/18`, `"Dz.U. 2024 poz. 18"`,
`"Dz.U. 1997 nr 78 poz. 483"`, `WDU20240000018`, albo `DU/2000/1037` (ELI).

### Komendy

- **szukaj** — znajdź akt po tytule/typie/roku/haśle:
  `python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa --limit 5`
  (opcje: `--typ`, `--rok`, `--wyd DU|MP`, `--haslo`, `--obowiazujace`, `--limit`, `--offset`)
- **meta** — metadane aktu (tytuł, typ, status, daty, czy obowiązuje, hasła, ELI, pliki tekstu):
  `python3 scripts/eli.py meta DU 2000 1037`
  Daty są rozdzielone, bo API ma dwa różne pola: **„Data aktu"** (`announcementDate` = data wydania,
  „z dnia …" w tytule) i **„Ogłoszono"** (`promulgation` = publikacja w Dz.U./M.P. — od niej liczy się
  vacatio legis); dalej **„WEJŚCIE W ŻYCIE"**, **„Stan prawny na"** (`legalStatusDate`, tylko t.j.) i
  **„Uwagi"** (`comments` — np. „art. 5 ust. 4 … wchodzą w życie z dniem 25 grudnia 2024 r.", czyli RÓŻNE
  daty wejścia w życie dla różnych jednostek; czytaj je zawsze).
- **tj** — znajdź AKTUALNY TEKST JEDNOLITY dla aktu (posortowane, najnowszy oznaczony; na starym t.j. ostrzega o nowszym):
  `python3 scripts/eli.py tj DU 2000 1037`
- **tekst** — treść aktu (z `text.html` → czysty tekst; gdy API nie ma HTML dla aktu — `textHTML=false`,
  np. Konstytucja, k.c. DU 2026 795 — z jego WŁASNEGO urzędowego PDF przez `pdftotext -layout`, z nagłówkiem
  „tekst z urzędowego PDF przez pdftotext" i linią `ELI_TEXT_SOURCE_PDF=…`). **Do pojedynczego przepisu używaj `--fragment`**
  (wycina tylko jednostki z frazą — pełny kodeks to setki tysięcy znaków):
  `python3 scripts/eli.py tekst DU 2024 18 --fragment "art. 299"` (trafia w nagłówek artykułu, nie w odesłania)
  `python3 scripts/eli.py tekst DU 2024 18 --fragment "przedawnienie"` (wyszukiwanie pełnotekstowe)
  `--pdf ŚCIEŻKA` zapisuje urzędowy PDF (preferuje tekst jednolity). Indeks górny podawaj w nawiasie
  albo unicodem: art. 299¹ → `--fragment "art. 299(1)"` lub `"art. 299¹"`; sufiks literowy normalnie:
  `"art. 66c"`. Nagłówki przepisów niedawno dodanych lub zmienionych mają w tekście jednolitym
  odsyłacz do przypisu („Art. 66c 6)Dodany przez…") — `--fragment` to obsługuje.
  **„Nie znaleziono frazy" NIE znaczy, że przepisu nie ma w akcie** (zwłaszcza przy nietypowym
  oznaczeniu jednostki): sprawdź jeszcze samym numerem (`--fragment "66c"`) albo słowem z treści,
  zanim napiszesz, że przepis nie istnieje. Gdy nagłówka nie ma, narzędzie samo pokazuje trafienia
  pełnotekstowe z ostrzeżeniem — mogą to być odesłania z innych przepisów, nie sam przepis.
- **struktura** — spis jednostek redakcyjnych (tytuły/działy/rozdziały/artykuły):
  `python3 scripts/eli.py struktura DU 2024 18 --filtr "Art. 299"` (opcje: `--filtr`, `--poziom N`)
- **odniesienia** — powiązania: nowelizacje, podstawa prawna, tekst jednolity, akty wykonawcze:
  `python3 scripts/eli.py odniesienia DU 2024 18`
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”); `szukaj` pokazuje
  `totalCount` z API („Znaleziono: 58 (pokazuję 10, offset 0)") i jak stronicować (`--offset`/`--limit`) —
  akt bazowy bywa na KOŃCU listy, za nowelizacjami.

**Co sprawdza `--strict`** (wszystko PRZED wypisaniem czegokolwiek; niepowodzenie = komunikat, kod ≠ 0,
pusty stdout): (1) istnieje nowszy t.j. niż podany → blokada; (2) odniesień lub aktu bazowego nie dało się
pobrać (awaria sieci/zapory) → blokada; (3) `text.html` puste i tekst pochodziłby ze STARSZEGO t.j. albo
`pdftotext` zawiódł → blokada (tekst z własnego urzędowego PDF aktu PRZECHODZI); (4) listy nowelizacji
nie dało się uzupełnić z aktu bazowego → blokada. **Czego `--strict` NIE sprawdza:** czy przepis został
zmieniony PO stanie prawnym t.j. — to wiesz tylko z sekcji „Nowelizacje po tekście jednolitym" (czytaj ją
i sprawdzaj „wejście w życie zmiany" względem daty sprawy); nie sprawdza też obcięcia listy `szukaj`
(to jest jawnie wypisane) ani opóźnień indeksacji API.

Narzędzie samo ostrzega: `tekst` na akcie, który ma tekst jednolity, każe cytować z najnowszego t.j.;
na tekście jednolitym wypisuje „Nowelizacje po tekście jednolitym" — własną listę t.j. UZUPEŁNIONĄ o
„Akty zmieniające" aktu bazowego ogłoszone lub wchodzące w życie po `legalStatusDate` t.j. (Sejm nie
synchronizuje obu list; każda pozycja ma osobno „data aktu", „ogłoszono", „wejście w życie zmiany") —
a na STARSZYM t.j. (np. z pamięci) — „NIEAKTUALNY tekst jednolity, istnieje NOWSZY" (sprawdza to na akcie
bazowym). Gdy `text.html` jest puste w API (`textHTML=false`), narzędzie czyta WŁASNY urzędowy PDF aktu
(`pdftotext`); tylko bez `pdftotext` sięga po najnowszy STARSZY t.j. z HTML — wtedy nagłówek mówi
„NIEAKTUALNE BRZMIENIE MOŻLIWE", wymienia pominięte t.j. i wypisuje INLINE zmiany aktu bazowego po stanie
prawnym tego starszego t.j., które trzeba nałożyć samemu. Nie ignoruj tych ostrzeżeń.

### Akty bazowe głównych kodeksów (pomiń `szukaj`)

Sygnatury aktów bazowych są niezmienne — dla poniższych zaczynaj od razu od `tj <sygnatura>`,
potem `tekst <t.j.> --fragment "art. N"` (dwie komendy zamiast trzech):

| Akt | Sygnatura bazowa |
|---|---|
| Konstytucja RP (tekst wprost, bez `tj`; API nie ma HTML — `tekst … --fragment "Art. 45"` czyta urzędowy PDF przez `pdftotext`, bez niego tylko `--pdf`) | `DU 1997 483` |
| Kodeks cywilny (k.c.) | `DU 1964 93` |
| Kodeks postępowania cywilnego (k.p.c.) | `DU 1964 296` |
| Kodeks rodzinny i opiekuńczy (k.r.o.) | `DU 1964 59` |
| Kodeks karny (k.k.) | `DU 1997 553` |
| Kodeks postępowania karnego (k.p.k.) | `DU 1997 555` |
| Kodeks wykroczeń (k.w.) | `DU 1971 114` |
| Kodeks postępowania administracyjnego (k.p.a.) | `DU 1960 168` |
| Kodeks pracy (k.p.) | `DU 1974 141` |
| Kodeks spółek handlowych (k.s.h.) | `DU 2000 1037` |
| Ordynacja podatkowa | `DU 1997 926` |

## Zasady (ważne — dlaczego)

1. **Najpierw znajdź właściwy akt, potem cytuj.** Typowy przepływ: `szukaj` → ustal sygnaturę → `tj`
   (aktualny tekst jednolity) → `tekst <t.j.> --fragment "art. N"`. Dla kodeksów z tabeli wyżej pomiń
   `szukaj` i zacznij od `tj`. Cytowanie starej wersji to częsty błąd.
2. **Sprawdź NOWELIZACJE i ich WEJŚCIE W ŻYCIE** (najczęstsze źródło błędu). `meta` pokazuje „Ogłoszono"
   (publikacja w Dz.U. — od niej liczy się vacatio legis; NIE myl z „Data aktu"), „WEJŚCIE W ŻYCIE" i
   „Uwagi" (różne daty dla różnych jednostek); `odniesienia` — zmiany; „Nowelizacje po tekście jednolitym"
   oznacza, że nawet t.j. bywa już nieaktualny. Zanim powiesz „przepis brzmi…":
   - **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** W nowelizacji sprawdź artykuł „wchodzi w życie" — możliwe vacatio
     legis oraz RÓŻNE daty dla różnych jednostek redakcyjnych — i odnieś do **DATY zdarzenia/sprawy**
     (przepis sprzed/po zmianie). Tekst jednolity oddaje stan na `legalStatusDate`; pozycje z listy
     „Nowelizacje po tekście jednolitym" z „wejście w życie zmiany" po tej dacie trzeba nałożyć ręcznie
     i sprawdzić, czy już obowiązują — `--strict` tego NIE robi za Ciebie.
   - **Baza może NIE mieć najświeższej zmiany.** Brak nowelizacji w API ≠ pewność, że jej nie ma
     (indeksacja bywa opóźniona). Przy sprawie na konkretną datę zweryfikuj dodatkowo (np. najnowsze
     pozycje `dziennikustaw.gov.pl`, proces legislacyjny), a w odpowiedzi zaznacz:
     „stan prawny na dzień X wg ELI — do potwierdzenia".
3. **NIGDY nie twierdź, że przepisu nie ma, na podstawie pustego `--fragment`.** Pusty wynik to brak
   dopasowania frazy, nie dowód nieistnienia przepisu — a najczęściej zawodzi przy przepisach ŚWIEŻO
   dodanych lub zmienionych (nietypowe oznaczenie jednostki, odsyłacz do przypisu, indeks górny).
   Zanim postawisz twierdzenie negatywne o prawie, przeszukaj pełny tekst aktu liniowo:
   `python3 scripts/eli.py tekst <akt> | grep -n -i "<fraza>"` — i dopiero zero trafień tam jest
   podstawą do „brak takiego przepisu w tym akcie". Sprawdź też, czy pytasz o WŁAŚCIWY akt: sankcja
   za naruszenie obowiązku zwykle stoi w k.w./k.k., a nie w ustawie, która ten obowiązek nakłada.
4. **Do DOSŁOWNEGO cytatu w umowie/piśmie/sądzie** używaj urzędowego PDF (`tekst … --pdf`), bo
   `text.html` po konwersji bywa zlepiony, a tekst z PDF ma automatycznie sklejone wiersze i dzielone
   wyrazy; `--fragment` jest świetny do szybkiego odczytu i analizy.
   Linia zaczynająca się od `[przypis N)]` to **komentarz redakcyjny tekstu jednolitego** (kiedy przepis
   dodano lub zmieniono, z jaką datą wejścia w życie), a NIE treść normy — nigdy jej nie cytuj jako
   przepisu; sam numer odsyłacza nie wchodzi już w numer jednostki („§ 1.", „pkt 2)").
5. **Zawsze podawaj sygnaturę Dz.U./M.P. i ELI** przy cytacie (np. „art. 299 § 1 k.s.h., Dz.U. 2024
   poz. 18"). To pozwala odbiorcy zweryfikować źródło.
6. Pełna lista endpointów i parametrów: `references/api.md` (czytaj przy zapytaniach spoza powyższych
   komend — np. słowniki typów/haseł, listowanie roczników, akty zmieniające w okresie).

## Czego ten skill NIE obejmuje

- **prawa UE** (rozporządzenia, dyrektywy — użyj skilla **prawo-eu-eurlex**, EUR-Lex/CELLAR),
  **prawa MIEJSCOWEGO** (uchwały gmin/powiatów, dzienniki wojewódzkie → skill
  **prawo-pl-edzienniki**) i **dzienników resortowych**,
- **orzecznictwa sądów** (SN/TK/sądy powszechne/KIO → skill **prawo-pl-saos**; sądy
  administracyjne NSA/WSA → skill **prawo-pl-cbosa**; decyzje UODO → **prawo-pl-uodo**;
  TSUE → **prawo-eu-eurlex**; w Dz.U. są tylko wyroki TK i to jako pozycje dziennika),
- **projektów ustaw** w toku procesu legislacyjnego (to inne API Sejmu),
- treści umów stron, KRS, ksiąg wieczystych.
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że ELI je pokryje.

## Przykładowy przepływ

Pytanie (np. przy analizie pozwu przeciwko członkowi zarządu sp. z o.o.): „co dokładnie mówi art. 299 § 1
Kodeksu spółek handlowych i czy to aktualne?" (przykład — stan na czerwiec 2026):
1. `szukaj "Kodeks spółek handlowych" --typ Ustawa` → akt bazowy ELI `DU/2000/1037`.
2. `tj DU 2000 1037` → najnowszy tekst jednolity `DU/2024/18` (Dz.U. 2024 poz. 18), oznaczony „AKTUALNY".
3. `tekst DU 2024 18 --fragment "art. 299"` → treść artykułu + automatyczne ostrzeżenie o nowelizacjach
   po t.j. (tu 4: Dz.U. 2024 poz. 96 — wyrok TK K 29/23, oraz z aktu bazowego Dz.U. 2026 poz. 176, 187,
   644 z „wejście w życie zmiany" 2027–2030). Odnieś każdą do daty sprawy; indeksacja API bywa opóźniona,
   więc przy sprawie na konkretną datę dopytaj jeszcze `szukaj`iem.
4. W odpowiedzi: zacytuj przepis, podaj sygnaturę i datę stanu prawnego.
