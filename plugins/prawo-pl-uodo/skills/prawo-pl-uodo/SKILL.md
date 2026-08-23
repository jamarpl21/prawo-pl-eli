---
name: prawo-pl-uodo
version: 2.0.0
description: >-
  Odpytuje OFICJALNE API Portalu Orzeczeń UODO (orzeczenia.uodo.gov.pl) — decyzje Prezesa
  Urzędu Ochrony Danych Osobowych: kary pieniężne za naruszenia RODO, upomnienia, nakazy,
  oraz powiązane orzeczenia sądów. Używaj przy pytaniach o praktykę polskiego organu ochrony
  danych: „czy UODO karał za…", „decyzja UODO w sprawie…", „jak UODO interpretuje art. X RODO",
  „jaka kara za brak analizy ryzyka/zgłoszenia naruszenia", przy DPIA, analizie naruszeń
  ochrony danych, audytach RODO i pismach do UODO. Wyszukiwanie pełnotekstowe i po
  tytule/dacie decyzji lub publikacji; pełna treść decyzji po sygnaturze (np. DKN.5131.9.2025)
  wraz z historią kontroli sądowej (uchylona / utrzymana / w toku). Read-only, bez klucza.
  Treść RODO → skill prawo-eu-eurlex; wyroki WSA/NSA ze skarg na decyzje UODO →
  prawo-pl-cbosa. Decisions of the Polish DPA (UODO) from its official public API.
---

# Decyzje Prezesa UODO z oficjalnego API portalu orzeczeń

Skill do **praktyki decyzyjnej polskiego organu ochrony danych** (Prezesa UODO). Sięga do
oficjalnego API **Portalu Orzeczeń UODO** (`https://orzeczenia.uodo.gov.pl`, uruchomionego
w 2025 r.): decyzje administracyjne Prezesa UODO — kary pieniężne, upomnienia, nakazy — oraz
powiązane orzeczenia sądów w tych sprawach. Używaj go, gdy pytanie dotyczy tego, **jak UODO
stosuje RODO w praktyce**: wysokość kar, przesłanki upomnienia, wymagania wobec analizy ryzyka,
zgłaszania naruszeń, powierzenia przetwarzania.

## Podział ról (który skill do czego)

1. **Decyzje Prezesa UODO → ten skill.** Kary, upomnienia, nakazy, umorzenia — z pełną treścią.
2. **Treść RODO → prawo-eu-eurlex** (rozporządzenie 2016/679, CELEX 32016R0679); polska ustawa
   o ochronie danych osobowych → **prawo-pl-eli** (Dz.U.).
3. **Wyroki WSA/NSA ze skarg na decyzje UODO → prawo-pl-cbosa.** Portal UODO zna FAKT kontroli
   sądowej (`decyzja` drukuje blok „Kontrola sądowa": data, uchylona / utrzymana / w toku, sygnatura
   wyroku), ale NIE ma treści wyroków — zakres uchylenia (cała decyzja czy np. sama kara) czytaj
   w CBOSA: `prawo-pl-cbosa sygnatura "<sygnatura wyroku>"`. Rekordy wyroków w listach portalu
   (ok. 200 z 700) są bez treści — silnik nie oferuje dla nich `decyzja`, tylko odsyła do CBOSA.
4. **Delegując research subagentowi**, wpisz: „decyzje UODO pobieraj przez `scripts/uodo.py`
   (skill prawo-pl-uodo); treść RODO przez `scripts/eurlex.py` (prawo-eu-eurlex)".

## Narzędzie

Wszystko robi helper `scripts/uodo.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/uodo.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-uodo` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-uodo`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
UODO="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-uodo/scripts/uodo.py"
# Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
# jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$UODO" ] || UODO="<katalog skilla>/scripts/uodo.py"
[ -f "$UODO" ] || { echo "BŁĄD: brak helpera obok SKILL.md: $UODO" >&2; exit 1; }
python3 "$UODO" <komenda> [...]
```

Nie pobieraj helpera z sieci i nie szukaj go przez `find` po katalogach użytkownika ani systemu.

(W przykładach niżej `python3 scripts/uodo.py` oznacza `python3 "$UODO"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **najnowsze** — ostatnio WYDANE dokumenty (API sortuje po dacie decyzji/orzeczenia, nie po
  dacie publikacji w portalu; starsze decyzje dopisane niedawno tu nie trafią):
  `python3 scripts/uodo.py najnowsze --limit 10`
- **szukaj** — wyszukiwanie pełnotekstowe / po tytule / po dacie DECYZJI (`--od/--do`) / po dacie
  PUBLIKACJI w portalu (`--pub-od/--pub-do`):
  `python3 scripts/uodo.py szukaj "biometr" --limit 5`
  `python3 scripts/uodo.py szukaj --tytul "pieniężn" --od 2026-01-01`
  `python3 scripts/uodo.py szukaj --pub-od 2026-01-01 --pub-do 2026-03-31 --limit 100` (co opublikowano w Q1)
  `--od/--do` filtrują po dacie wydania decyzji (tak działa API) — decyzja z 2025 r. opublikowana
  w 2026 r. NIE wpadnie w `--od 2026-01-01`; do „co nowego w portalu" służy `--pub-od`. `--pub-od`
  idzie do API, gdy nie ma innego warunku; z frazą/tytułem filtr publikacji działa tylko w obrębie
  pobranej strony (silnik to wypisuje) — wtedy zwiększ `--limit` i przeglądaj `--strona`.
  Fraza działa jak **regex bez rozróżniania wielkości liter**, dopasowuje DOSŁOWNIE — szukaj
  RDZENIA słowa („biometr" znajdzie „biometryczne/biometrii"). Tytuły to zdania w formie
  ODMIENIONEJ („nałożenie kary pieniężnej…"), więc mianownik („kara pieniężna") nie trafia
  w nic — zero wyników z mianownika to NIE dowód, że takich decyzji nie ma.
  API stosuje **jeden warunek na zapytanie** (fraza ALBO tytuł; zakres dat można łączyć
  z warunkiem). Zaawansowane: `--warunek
  "indeks:operator:wartość"` (indeksy i operatory: `references/api.md`). `--limit N`, `--strona N`.
- **decyzja** — pełna decyzja po sygnaturze albo URN:
  `python3 scripts/uodo.py decyzja DKN.5131.9.2025`
  Pokazuje metadane (status, data decyzji i publikacji), **blok „Kontrola sądowa"** (każdy wyrok
  z meta: data, UCHYLONA / utrzymana / w toku, sygnatura + odsyłacz do prawo-pl-cbosa), przedmiot
  i pełną treść (z `body.html` portalu: numeracja list i przypisy `[1]` jak na stronie portalu).
  Decyzja uchylona (także w części, przy statusie `final`) zaczyna się od nagłówka
  **„DECYZJA UCHYLONA PRZEZ SĄD (w całości lub w części)"** — treść poniżej to tekst PIERWOTNY;
  zakres uchylenia ustal w sentencji wyroku (CBOSA), zanim zacytujesz karę.
  Do długich decyzji: `--fragment "pieniężn"` (wycina okna wokół frazy; też podawaj rdzeń).
  Sygnatura sądowa (`decyzja "III OSK 377/23"`) to rekord bez treści — silnik to wyjaśnia i odsyła do CBOSA.
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  **Kontrakt `--strict`:** w `decyzja` kończy błędem (bez wyniku, także z `--json`) decyzję bez pełnej
  treści ORAZ decyzję z wpisem uchylenia przez sąd (komunikat podaje sygnaturę wyroku i odsyła do
  CBOSA); decyzja NIEPRAWOMOCNA przechodzi z ostrzeżeniem „UWAGA: decyzja NIEPRAWOMOCNA".
  W `najnowsze`/`szukaj` `--strict` nic nie zmienia (listy to metadane, nie ma czego weryfikować).
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”).

Typowy przepływ: `szukaj "<rdzeń frazy>"` → wybierz sygnaturę → `decyzja <sygnatura>`
→ jeśli blok „Kontrola sądowa" pokazuje wyrok: `prawo-pl-cbosa sygnatura "<sygnatura wyroku>"`
(zakres uchylenia / utrzymania); jeśli nie — i tak sprawdź CBOSA (`szukaj "<sygnatura decyzji>"`),
bo portal nie musi znać każdej skargi.

## Zasady (ważne — dlaczego)

1. **Portal jest młody (od 2025 r.)** — publikowane są nowe decyzje oraz sukcesywnie starsze
   (sygnatury 2018–2024 pojawiają się z opóźnieniem). Brak decyzji w portalu ≠ jej nieistnienie
   (silnik to wypisuje przy 404); sprawdź wyszukiwarkę https://uodo.gov.pl (dawne adresy
   `uodo.gov.pl/decyzje/<sygnatura>` już nie działają) — i zaznacz to w odpowiedzi.
2. **Status `final` ≠ kara się ostała; `inforce` ≠ w obrocie.** `status` ∈ final (prawomocna wg
   portalu) / nonfinal (nieprawomocna — dopisz zastrzeżenie; przysługuje skarga do WSA) / repealed
   (uchylona). Kontrolę sądową portal zapisuje w `dates[]` (uchylona / utrzymana / w toku) i przy
   statusie `final` może istnieć wpis uchylenia części decyzji (ZSPR.421.3.2018: kara 943 470 zł
   uchylona przez WSA II SA/Wa 1030/19, status nadal `final`). Pole `inforce` API jest `true` nawet
   dla decyzji uchylonych — nie cytuj go jako „w obrocie". Zanim podasz karę z decyzji z blokiem
   „Kontrola sądowa", przeczytaj sentencję wyroku w CBOSA.
3. **Zawsze podawaj sygnaturę + datę** (np. „decyzja Prezesa UODO z 7.08.2025, DKN.5131.9.2025")
   — sygnatura jest stabilnym identyfikatorem (URN w API).
4. **Kwoty kar i stan prawny weryfikuj w treści decyzji**, nie w tytule — tytuł to opis redakcyjny.
5. Pełna lista endpointów, indeksów i operatorów: `references/api.md`.

## Czego ten skill NIE obejmuje

- **treści RODO i innych przepisów** (RODO → **prawo-eu-eurlex**; polskie ustawy → **prawo-pl-eli**),
- **wyroków sądów administracyjnych** ze skarg na decyzje UODO (→ **prawo-pl-cbosa**),
- **orzeczeń SN/TK/sądów powszechnych** (→ **prawo-pl-saos**),
- decyzji innych organów (UOKiK, KNF), wytycznych EROD (te bierz z edpb.europa.eu — internet).
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że portal UODO je pokryje.

## Przykładowy przepływ

Pytanie: „czy UODO nakładał kary za brak zgłoszenia naruszenia ochrony danych i jak je uzasadnia?"
1. `szukaj "zgłoszen" --tytul "kara"` → jeśli pusto: `szukaj "art. 33"` (jeden warunek na raz).
2. `decyzja <sygnatura> --fragment "art. 33"` → argumentacja organu wokół obowiązku zgłoszenia.
3. (opcjonalnie) treść art. 33 RODO: skill prawo-eu-eurlex
   (`tekst 02016R0679-20160504 --fragment "art. 33"`).
4. Kontrola sądowa: blok „Kontrola sądowa" z `decyzja` → `prawo-pl-cbosa sygnatura "<wyrok>"`
   (zakres uchylenia); bez wpisów w bloku — `prawo-pl-cbosa szukaj "<sygnatura decyzji>"`.
5. W odpowiedzi: sygnatura + data + status (nieprawomocna? uchylona — w jakim zakresie?) + kwota
   kary z treści decyzji, skorygowana o wynik kontroli sądowej.
