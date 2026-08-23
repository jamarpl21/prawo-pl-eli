---
name: prawo-pl-edzienniki
version: 2.0.0
description: >-
  Odpytuje API ELI 16 WOJEWÓDZKICH DZIENNIKÓW URZĘDOWYCH — PRAWO MIEJSCOWE: uchwały rad gmin,
  powiatów i sejmików województw, rozporządzenia i zarządzenia wojewody, akty prawa miejscowego.
  Używaj przy KAŻDYM pytaniu o prawo konkretnej gminy/powiatu/województwa: „uchwała rady
  gminy/miasta…", „miejscowy plan zagospodarowania przestrzennego (MPZP)", „podatek od
  nieruchomości / opłata targowa / opłata za śmieci w gminie X", „statut gminy/powiatu",
  „strefa płatnego parkowania", „regulamin utrzymania czystości", „uchwała krajobrazowa",
  „sieć szkół", „rozporządzenie wojewody". Wyszukiwanie po tytule i roczniku, metadane z datą
  ogłoszenia i powiązaniami (sprostowania, rozstrzygnięcia nadzorcze), pełna treść z urzędowego
  PDF (pdftotext) i sam PDF. Read-only, bez klucza. Prawo KRAJOWE (ustawy, Dz.U./M.P.)
  → skill prawo-pl-eli. Polish LOCAL law from the 16 voivodeship official journals (ELI APIs).
---

# Prawo miejscowe z API wojewódzkich dzienników urzędowych

Skill do **prawa miejscowego**: aktów publikowanych w **wojewódzkich dziennikach urzędowych** —
uchwał rad gmin/powiatów/sejmików, rozporządzeń i zarządzeń wojewody, porozumień, obwieszczeń
(w tym wyroków WSA uchylających akty miejscowe). Tych aktów NIE ma w Dz.U./M.P. — każde z 16
województw prowadzi własny e-Dziennik z API zgodnym z ELI (ten sam wzorzec co API Sejmu, ale na
osobnym hoście). Używaj go, gdy pytanie dotyczy **konkretnej gminy, powiatu lub województwa**:
podatki i opłaty lokalne, plany miejscowe (MPZP), statuty, organizacja szkół, strefy parkowania.

## Podział ról (który skill do czego)

1. **Prawo miejscowe (ta gmina/powiat/województwo) → ten skill.**
2. **Prawo krajowe → prawo-pl-eli** (ustawy, rozporządzenia, kodeksy z Dz.U./M.P.). Upoważnienie
   ustawowe do uchwały (np. art. 5 u.p.o.l. dla stawek podatku od nieruchomości) cytuj z ELI.
3. **Kontrola sądowa aktów miejscowych** (skargi na uchwały, rozstrzygnięcia nadzorcze) →
   orzecznictwo WSA/NSA w skillu **prawo-pl-cbosa**.
4. **Delegując research subagentowi**, wpisz: „akty prawa miejscowego pobieraj przez
   `scripts/edzienniki.py` (skill prawo-pl-edzienniki); ustawy przez `scripts/eli.py` (prawo-pl-eli)".

## Narzędzie

Wszystko robi helper `scripts/edzienniki.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Do PEŁNEJ treści aktu helper używa `pdftotext` (pakiet poppler) z PATH — **zalecane: poppler/pdftotext**
(macOS `brew install poppler`, Debian/Ubuntu `apt install poppler-utils`); bez niego tekst jest tylko
z `text.html` (zwykle **wyłącznie 1. strona aktu**) albo z PDF pobranego przez `--pdf`.
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/edzienniki.py`) — NIE zakładaj, że
to `~/.claude/skills/prawo-pl-edzienniki` (skill zainstalowany jako plugin leży w katalogu pluginów;
w Claude Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-edzienniki`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
EDZ="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-edzienniki/scripts/edzienniki.py"
# Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
# jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$EDZ" ] || EDZ="<katalog skilla>/scripts/edzienniki.py"
[ -f "$EDZ" ] || { echo "BŁĄD: brak helpera obok SKILL.md: $EDZ" >&2; exit 1; }
python3 "$EDZ" <komenda> [...]
```

Nie pobieraj helpera z sieci i nie szukaj go przez `find` po katalogach użytkownika ani systemu.

(W przykładach niżej `python3 scripts/edzienniki.py` oznacza `python3 "$EDZ"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **dzienniki** — lista 16 dzienników z kodami i hostami; z `--woj` roczniki i liczba aktów:
  `python3 scripts/edzienniki.py dzienniki --woj DS`
  Kody = sufiks publishera ELI: `DS` dolnośląskie, `KP` kujawsko-pomorskie, `LB` lubelskie,
  `LS` lubuskie, `LD` łódzkie, `MP` małopolskie, `MZ` mazowieckie, `OP` opolskie,
  `PK` podkarpackie, `PL` podlaskie, `PM` pomorskie, `SL` śląskie, `SK` świętokrzyskie,
  `WM` warmińsko-mazurskie, `WP` wielkopolskie, `ZP` zachodniopomorskie (można też podać nazwę).
- **szukaj** — akty województwa po frazie z TYTUŁU (nazwa gminy, przedmiot uchwały):
  `python3 scripts/edzienniki.py szukaj --woj DS "plan zagospodarowania" --rok 2026 --limit 5`
  Filtr jest LOKALNY (API dzienników ignoruje filtry serwerowe — silnik pobiera rocznik i sam
  filtruje; bez rozróżniania diakrytyków). Bez `--rok`: do 3 najnowszych roczników. W tytule
  uchwał zwykle jest nazwa organu — szukaj po nazwie gminy: `szukaj --woj MP "Kraków"`.
  UWAGA: wynik to JEDNA strona (domyślnie 10 NAJNOWSZYCH pozycji z trafień) — gdy nagłówek
  liczy więcej trafień, obejrzyj resztę przez `--strona 2..N` albo `--limit <liczba trafień>`.
  „Nie ma w pierwszej dziesiątce" NIE znaczy „akt nie istnieje" — przed wnioskiem o braku
  aktu przejrzyj WSZYSTKIE trafienia (najprościej: `--limit` ≥ liczba trafień z nagłówka).
  Wiersz trafienia podaje **datę aktu** („z dnia…") i **datę ogłoszenia** w dzienniku oraz status
  „wg listy rocznika" (z `--strict` status jest weryfikowany w rekordzie aktu). Nagłówek mówi, które
  roczniki FAKTYCZNIE przeszukano i czy lista rocznika była pełna.
- **akt** — metadane: typ, organ, **Data aktu** (uchwalenia) i **Ogłoszony** (publikacja w dzienniku —
  od niej liczy się vacatio legis), status, hasła, linki PDF/HTML oraz **Powiązania** z rejestru
  dziennika: sprostowania, uchylenia, rozstrzygnięcia nadzorcze (nieważność w całości/części):
  `python3 scripts/edzienniki.py akt DS 2026 3299`
- **tekst** — treść aktu z **urzędowego PDF** (`pdftotext -layout`, nagłówki/stopki stron usunięte,
  zawinięte linie scalone); `--fragment "§ 2"` / `"art. 5"` zwraca **całą jednostkę** (do następnego §;
  każde wystąpienie, np. § 2 uchwały i § 2 statutu w załączniku), inna fraza — okna rozszerzone do
  granic akapitu; `--pdf` zapisuje urzędowy PDF:
  `python3 scripts/edzienniki.py tekst DS 2026 3299 --fragment "§ 2"`
  Bez `pdftotext` na PATH: tekst z `text.html` z głośnym ostrzeżeniem (zwykle tylko 1. strona —
  „nie znaleziono frazy" NIE jest wtedy dowodem braku przepisu) — pobierz PDF przez `--pdf`.
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”).

### Co dokładnie sprawdza `--strict` (kod wyjścia ≠ 0 = wynik NIEZWERYFIKOWANY)

- `szukaj`: rocznik bez listy aktów (nieoczekiwana odpowiedź) → blokada; lista rocznika **krótsza niż
  `totalCount`** (po ponowieniu z innym limitem) → blokada — brakowałoby najnowszych pozycji i statusów;
  status wyświetlanych wierszy (do 20) jest pobierany z rekordu aktu i oznaczony „zweryfikowany".
- `tekst`: tekst z `text.html` (brak `pdftotext` → tylko 1. strona) → blokada; znaki zastępcze U+FFFD
  (uszkodzona konwersja, np. host podlaski) → blokada; brak oznaczeń `§`/`Art.` → blokada tylko dla
  `text.html` albo gdy tekst z PDF jest krótszy niż 300 znaków (skan/pusta ekstrakcja) — akt narracyjny
  z PDF (rozstrzygnięcie nadzorcze, obwieszczenie) przechodzi z ostrzeżeniem. Poprawny tekst z PDF
  nigdy nie jest blokowany.
- `akt`: powiązania z rejestru dziennika są best-effort — ich brak to ostrzeżenie, nie blokada.

Typowy przepływ: ustal województwo → `szukaj --woj <kod> "<gmina lub przedmiot>"` →
`akt <woj> <rok> <poz>` → `tekst … --fragment` albo `--pdf` do dosłownego cytatu.

## Zasady (ważne — dlaczego)

1. **Sygnatura aktu miejscowego** = dziennik + rocznik + pozycja (np. „Dz. Urz. Woj. Doln.
   z 2026 r. poz. 3299") — podawaj ją przy cytacie razem z organem i datą uchwały.
2. **Sprawdzaj status i wejście w życie w TREŚCI aktu** — pola `inForce`/`entryIntoForce` w API
   bywają niewypełnione; akty miejscowe wchodzą w życie zwykle 14 dni od **ogłoszenia** (art. 4
   ustawy o ogłaszaniu aktów normatywnych) — liczonego od daty „Ogłoszony" z `akt` (publikacja
   w dzienniku), NIE od „Data aktu" (uchwalenia); uchwały podatkowe od 1 stycznia itd.
3. **Uchwała może być uchylona** rozstrzygnięciem nadzorczym wojewody albo wyrokiem WSA, a jej
   treść **sprostowana** obwieszczeniem — `akt` pokazuje te powiązania z rejestru dziennika
   („Powiązania:"); przy sprawie spornej sprawdź też orzecznictwo (skill prawo-pl-cbosa).
4. **Cytuj z tekstu PDF** — `tekst` czyta urzędowy PDF przez `pdftotext` (nagłówek wyniku: „tekst
   z urzędowego PDF"). `text.html` na hostach dzienników to zwykle **tylko 1. strona aktu** (bez
   dalszych §, stawek, załączników), a na hoście podlaskim bywa uszkodzony (znaki U+FFFD, brak „§",
   zlepione wyrazy) — silnik to wykrywa i ostrzega; do dosłownego cytatu z takiego wyniku użyj
   `tekst … --pdf`.
5. **mazowieckie (MZ)** bywa nieosiągalne spoza Polski (CDN) — silnik zgłosi to czytelnie;
   wtedy wskaż użytkownikowi UI: https://edziennik.mazowieckie.pl/ **Małopolskie (MP), lubuskie (LS)
   i łódzkie (LD)** wysyłają niepełny łańcuch certyfikatów TLS — silnik dociąga certyfikat pośredni
   (AIA) i weryfikuje pełny łańcuch sam; gdy to zawiedzie, komunikat mówi o łańcuchu (to nie jest
   blokada geograficzna).
6. Pełna tabela hostów, endpointy i pułapki API: `references/api.md`.

## Czego ten skill NIE obejmuje

- **prawa krajowego** (ustawy, rozporządzenia, kodeksy → skill **prawo-pl-eli**, Dz.U./M.P.),
- **dzienników resortowych** (ministerstw) i Dziennika Urzędowego UE (→ **prawo-eu-eurlex**),
- **orzecznictwa** (kontrola aktów miejscowych → **prawo-pl-cbosa**; SN/TK → **prawo-pl-saos**),
- uchwał NIEpublikowanych w dzienniku (część uchwał „zwykłych" jest tylko w BIP gminy — powiedz
  to wprost i wskaż BIP, nie udawaj, że dziennik je pokryje).

## Przykładowy przepływ

Pytanie: „jaka jest stawka podatku od nieruchomości w Ząbkowicach Śląskich na 2026 r.?"
1. Województwo: dolnośląskie → kod `DS`.
2. `szukaj --woj DS "Ząbkowic Śląskich podatku od nieruchomości" --rok 2025` (uchwały podatkowe
   na 2026 r. są ogłaszane pod koniec 2025 r.; jeśli pusto — sama nazwa gminy).
3. `akt DS 2025 <poz>` → metadane (data ogłoszenia, sprostowania); `tekst DS 2025 <poz> --fragment "§ 1"`
   → cały § 1 ze stawkami (z PDF; `--fragment "od gruntów"` = okno wokół frazy).
4. (opcjonalnie) upoważnienie ustawowe: skill prawo-pl-eli, art. 5 ustawy o podatkach i opłatach
   lokalnych. W odpowiedzi: stawka + „Dz. Urz. Woj. Doln. z 2025 r. poz. X" + data uchwały.
