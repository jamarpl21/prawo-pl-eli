---
name: prawo-pl-saos
version: 2.0.1
description: >-
  Odpytuje PUBLICZNE API SAOS (saos.org.pl) — bazę polskiego ORZECZNICTWA: wyroki, postanowienia
  i uchwały Sądu Najwyższego (SN), Trybunału Konstytucyjnego (TK), sądów powszechnych (SA/SO/SR)
  i Krajowej Izby Odwoławczej (KIO). Używaj, gdy potrzebujesz ORZECZEŃ, a nie treści przepisu:
  „orzecznictwo SN/TK do art. X", „jak sądy interpretują/stosują…", „linia orzecznicza", „znajdź
  wyrok o sygnaturze…", „uchwała SN sygn. …", przy ANALIZIE i PISANIU pism procesowych (poszukiwanie
  podstawy w judykaturze). Treść przepisu bierz z ELI (skill prawo-pl-eli), tu szukasz JAK go stosują.
  Filtruj po sądzie, sygnaturze, sędzim, powołanym przepisie i dacie; zwraca pełne uzasadnienia,
  powołane przepisy i powołane orzeczenia. UWAGA: SAOS to baza WTÓRNA (agregat) — sądy administracyjne
  (NSA/WSA) są w niej praktycznie nieobecne (dla nich: skill prawo-pl-cbosa), a zbiory SN (do 22.06.2016),
  TK (do 9.12.2015) i KIO (do 6.09.2018) są zamknięte; sądy powszechne idą na bieżąco. Polish case-law
  from the public SAOS API.
---

# Polskie orzecznictwo z publicznego API SAOS

Skill do **orzecznictwa** (judykatury) polskiego: wyroków, postanowień i uchwał. Sięga do publicznego API
**SAOS** (System Analizy Orzeczeń Sądowych, `https://www.saos.org.pl/api`) — agregatu orzeczeń jawnych:
**SN, TK, sądy powszechne (SA/SO/SR), KIO**. Używaj go zawsze, gdy pytanie dotyczy tego, **jak sądy
stosują/interpretują przepis**, gdy trzeba znaleźć **konkretny wyrok po sygnaturze**, ustalić **linię
orzeczniczą** albo poprzeć argument w piśmie procesowym judykaturą.

## Podział ról (przepis ≠ orzeczenie)

1. **Treść przepisu — z ELI**, nie z SAOS. Brzmienie polskiej ustawy/kodeksu cytuj przez skill
   **prawo-pl-eli** (`scripts/eli.py`). SAOS daje orzeczenia, nie autorytatywny tekst przepisu.
2. **JAK sądy stosują przepis — z SAOS.** „Co mówi orzecznictwo o art. 299 k.s.h.", „czy SN dopuszcza…",
   „linia orzecznicza w sprawie…", „znajdź wyrok sygn. III CSK 203/09" — to zadania dla tego skilla.
3. **Most ELI → SAOS:** najpierw ustal przepis w ELI, potem `szukaj --przepis "..."` w SAOS, by znaleźć
   orzeczenia powołujące ten przepis.
4. **Delegując research subagentowi**, wpisz: „orzecznictwo polskie pobieraj przez `scripts/saos.py`
   (skill prawo-pl-saos); treść przepisów przez `scripts/eli.py` (prawo-pl-eli)".

## Narzędzie

Wszystko robi helper `scripts/saos.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/saos.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-saos` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-saos`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
SAOS="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-saos/scripts/saos.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$SAOS" ] || SAOS="<katalog skilla>/scripts/saos.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$SAOS" ] || SAOS=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.0.1", "71f87c1eda5e2d8a6084ac8226d3a9513a74564062a937248816040850d7d655"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-pl-saos/skills/prawo-pl-saos/scripts/saos.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-pl-saos-{WERSJA}", "saos.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$SAOS" ] || exit 1
python3 "$SAOS" <komenda> [...]
```

Nie szukaj helpera przez `find` po katalogach użytkownika ani systemu i nie pobieraj go z żadnego innego źródła niż przypięty tag z sumą wyżej (żadnego `main`, żadnej innej wersji z cache) — inna wersja helpera to inne wyniki.

(W przykładach niżej `python3 scripts/saos.py` oznacza `python3 "$SAOS"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **szukaj** — znajdź orzeczenia po kryteriach:
  `python3 scripts/saos.py szukaj "rękojmia wiary publicznej" --sad SN --limit 5`
  Opcje: `--sad SN|TK|powszechne|admin|KIO`, `--sygnatura "III CSK 203/09"`, `--przepis "Kodeks cywilny"`
  (powołany przepis/akt — **luźne dopasowanie pełnotekstowe**: `--przepis "art. 415"` trafia art. 415 k.c.,
  k.p.c. i k.p.k. jednakowo; dopisz nazwę aktu, np. `"Kodeks cywilny art. 415"`, i sprawdź akt w
  `orzeczenie <id>`), `--sedzia "Górski"`, `--haslo "nienależne świadczenie"`,
  `--typ wyrok|postanowienie|uchwala|zarzadzenie|uzasadnienie`, `--od 2020-01-01`, `--do 2024-12-31`
  (także `RRRR` / `RRRR-MM`), `--limit N` (1–100), `--strona N` (od 0).
- **orzeczenie** — pełne orzeczenie po ID (z listy `szukaj`):
  `python3 scripts/saos.py orzeczenie 76341`
  Pokazuje metadane, **powołane przepisy** i **powołane orzeczenia** (z ID do dalszego skoku) oraz treść
  uzasadnienia. Do długich uzasadnień: `--fragment "rękojmia"` (okna wokół frazy, dosunięte do granic
  zdań/słów, z „…" na ucięciu). Typ orzeczenia zawsze po polsku (wyrok / postanowienie / uchwała /
  zarządzenie / uzasadnienie). „Powołane przepisy" to **automatyczna ekstrakcja SAOS — bywa niepełna**
  (KIO 1564/18 nie ma w niej ŻADNEGO przepisu Pzp, o które chodzi w sentencji) i bywa uszkodzona
  (`art. 4793647945`, `Nr 0`, `art. 2oraz` — silnik oznacza takie wpisy „wpis SAOS prawdopodobnie
  uszkodzony"); przepisy rozstrzygnięcia czytaj z sentencji.
- **sygnatura** — szybkie odszukanie po numerze sprawy:
  `python3 scripts/saos.py sygnatura III CSK 203/09`
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”); przy `--sad SN|TK|KIO`
  komunikat zawiera granicę zbioru do dnia i to, czy Twój zakres leży poza nią. Z `--json` i wynikami
  ostrzeżenie o granicy idzie na stderr (stdout to czysty JSON).
  **Co sprawdza `--strict`:** (1) zakres dat dla SN/TK/KIO nie może wychodzić poza koniec zbioru
  (porównanie do dnia; brak `--do` = blokada; przed blokadą silnik potwierdza granicę na żywo jednym
  zapytaniem o najnowsze orzeczenie — gdyby SAOS wznowił zasilanie, granica się przesunie), (2) odpowiedź
  API musi być kompletnym wynikiem (`items` + `totalResults`) albo pełnym orzeczeniem — inaczej błąd,
  (3) żadna z tych kontroli nie przepuszcza „pustego" wyniku jako sukcesu. `--strict` NIE sprawdza
  prawdziwości treści ani kompletności listy powołanych przepisów.
- Czas: `/search/judgments` bywa bardzo wolne (40–60 s); limit na żądanie to 90 s, 2 próby. Przekroczenie
  czasu lub „Przerwa techniczna" (nocne okno serwisowe SAOS) to `BŁĄD sieci` / `przerwa techniczna`
  z kodem ≠ 0 — nigdy fałszywe zero trafień. Wtedy ponów później, nie wnioskuj „brak orzecznictwa".

Typowy przepływ: `szukaj` (zawęź `--sad`/`--przepis`/`--haslo`) → wybierz ID → `orzeczenie <id>`
→ w razie potrzeby skacz po `referencedCourtCases` do powołanych orzeczeń.

## Zasady (ważne — dlaczego)

1. **SAOS to baza WTÓRNA (agregat), nie źródło urzędowe.** Orzeczenia są jawne, ale do **dosłownego cytatu**
   i pewności zweryfikuj w portalu właściwego sądu. Link `source.judgmentUrl` z SAOS dla **SN/TK/KIO jest
   martwy albo ogólny** (sprawdzone 2026-08-23: `sn.pl/…/Baza_orzeczen` → 404, `otk.trybunal.gov.pl`
   i `ftp.uzp.gov.pl` nieosiągalne) — silnik pokazuje go tylko jako „link z metadanych SAOS" i NIE jest to
   ścieżka weryfikacji. Zamiast tego `orzeczenie` drukuje **źródło urzędowe**: dla SN wzorzec adresu PDF
   `https://www.sn.pl/sites/orzecznictwo/Orzeczenia3/<SYGN z „/"→„-", spacje %20>.pdf` (np.
   `II%20KK%2056-16.pdf`; zweryfikowany `curl -I` na II KK 56/16, I CSK 364/15, III CZP 17/15 — traktuj jako
   wzorzec „sprawdź", nie gwarancję; uzasadnienie bywa pod `…-1.pdf`), dla TK wyszukiwarkę OTK
   (`ipo.trybunal.gov.pl/ipo/`), dla KIO wyszukiwarkę UZP (`orzeczenia.uzp.gov.pl/Home/Search`). Linki
   sądów powszechnych (`apiorzeczenia.*.sa.gov.pl`) działają. Zawsze podawaj **sygnaturę + sąd + datę**
   (np. „wyrok SN z 9.04.2010, III CSK 203/09").
2. **Brak sądów administracyjnych.** NSA/WSA są w SAOS praktycznie nieobecne (`--sad admin` zwykle zwraca 0).
   Orzecznictwo administracyjne pobieraj skillem **prawo-pl-cbosa** (baza CBOSA, `scripts/cbosa.py`).
3. **Zbiory SN, TK i KIO są ZAMKNIĘTE — granice DO DNIA, sprawdzone na żywo 2026-08-23** (najnowsze
   orzeczenie w SAOS, sortowanie po dacie malejąco): **SN kończy się 22.06.2016** (III KK 195/16),
   **TK 9.12.2015** (K 35/15, Ts 266/14), **KIO 6.09.2018** (KIO 1711/18); sądy powszechne idą na bieżąco.
   Rocznik granicy NIE jest pokryty w całości: uchwała SN III CZP 81/16 z 8.12.2016 i wyrok KIO 2577/18
   z 27.12.2018 istnieją, a w SAOS ich nie ma — dlatego silnik porównuje `--od/--do` z granicą do dnia,
   `--strict` blokuje zakres sięgający poza nią (podpowiada `--do 2016-06-22` itd.), a `sygnatura` dla
   numeru z rocznika granicy mówi „może być późniejsze niż koniec zbioru", nie „nie ma". Zero trafień
   z nowszą datą NIE znaczy, że orzecznictwa nie ma. Nowsze bierz z portalu SN (`sn.pl/orzecznictwo`),
   OTK (`ipo.trybunal.gov.pl`) albo UZP (`orzeczenia.uzp.gov.pl`) i oznacz jako źródło spoza SAOS.
4. **Świeżość bywa opóźniona.** Najnowsze orzeczenia sądów powszechnych mogą jeszcze nie być w bazie — przy
   sprawie na konkretną datę zaznacz „wg SAOS na dzień X — do potwierdzenia" i sprawdź portal sądu.
5. **Nie myl orzeczenia z przepisem.** Po znalezieniu orzeczenia, brzmienie powołanego przepisu i jego
   aktualność potwierdź w ELI (`prawo-pl-eli`) — orzeczenie mogło zapaść na starszym stanie prawnym.
6. **Indeksy górne w numerach przepisów.** W tekstach **SN/TK/KIO SAOS spłaszcza indeksy górne**:
   art. 417¹ k.c. → „art. 4171 k.c.", art. 398¹⁴ → „39814", art. 479⁴⁵ → „47945" (w surowym API nie ma
   `<sup>`, więc silnik nie może tego odtworzyć — drukuje stałą notę „UWAGA: SAOS spłaszcza indeksy górne…").
   **Nigdy nie cytuj „art. 4171 k.c."** — numerację sprawdź w źródle urzędowym albo w ELI. W tekstach
   sądów powszechnych `<sup>` jest zachowane i silnik renderuje je w linii: `art. 556¹ § 1 k.c.`.
7. **`--przepis` jest luźne, a lista „Powołane przepisy" niepełna.** Filtr `referencedRegulation` to
   dopasowanie pełnotekstowe w polu powołanych przepisów (bez rozróżnienia aktu), a samo pole to automatyczna
   ekstrakcja SAOS z lukami — trafienia przez `--przepis` traktuj jako kandydatów, nie jako „wszystkie
   orzeczenia do art. X". Uzupełnij wyszukiwanie frazą (`szukaj "art. 415 k.c."`) i `--haslo`.
8. Pełna lista endpointów i parametrów: `references/api.md`.

## Czego ten skill NIE obejmuje

- **treści przepisów** (ustawy/kodeksy → skill **prawo-pl-eli**, ELI Sejmu),
- **prawa UE i orzecznictwa TSUE** (→ skill **prawo-eu-eurlex**, EUR-Lex/CELLAR),
- **sądów administracyjnych** (NSA/WSA — w SAOS ich nie ma; → skill **prawo-pl-cbosa**),
- **decyzji Prezesa UODO** (→ skill **prawo-pl-uodo**),
- pism stron, akt sprawy, KRS, ksiąg wieczystych.
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że SAOS je pokryje.

## Przykładowy przepływ

Pytanie (przy analizie pozwu z art. 299 k.s.h.): „jak SN podchodzi do odpowiedzialności członka zarządu
za zobowiązania spółki — znajdź orzecznictwo".
1. (opcjonalnie) ELI: `tj DU 2000 1037` → brzmienie art. 299 k.s.h. (skill prawo-pl-eli).
2. `szukaj "odpowiedzialność członka zarządu" --przepis "Kodeks spółek handlowych" --sad SN --limit 5`
   → lista orzeczeń SN z sygnaturami i ID.
3. `orzeczenie <id>` → teza/uzasadnienie + powołane przepisy + powołane orzeczenia (skok po linii orzeczniczej).
4. W odpowiedzi: zacytuj tezę, podaj sygnaturę + sąd + datę, zaznacz „baza SAOS (wtórna) — do dosłownego
   cytatu zweryfikuj w portalu sądu" (dla SN: wzorzec PDF z `orzeczenie`), a dla SN/TK/KIO nie przepisuj
   spłaszczonych numerów przepisów (art. 4171 → art. 417¹) i pamiętaj, że zbiór SN kończy się 22.06.2016.
