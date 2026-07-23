---
name: prawo-pl-saos
version: 1.6.0
description: >-
  Odpytuje PUBLICZNE API SAOS (saos.org.pl) — bazę polskiego ORZECZNICTWA: wyroki, postanowienia
  i uchwały Sądu Najwyższego (SN), Trybunału Konstytucyjnego (TK), sądów powszechnych (SA/SO/SR)
  i Krajowej Izby Odwoławczej (KIO). Używaj, gdy potrzebujesz ORZECZEŃ, a nie treści przepisu:
  „orzecznictwo SN/TK do art. X", „jak sądy interpretują/stosują…", „linia orzecznicza", „znajdź
  wyrok o sygnaturze…", „uchwała SN sygn. …", przy ANALIZIE i PISANIU pism procesowych (poszukiwanie
  podstawy w judykaturze). Treść przepisu bierz z ELI (skill prawo-pl-eli), tu szukasz JAK go stosują.
  Filtruj po sądzie, sygnaturze, sędzim, powołanym przepisie i dacie; zwraca pełne uzasadnienia,
  powołane przepisy i powołane orzeczenia. UWAGA: SAOS to baza WTÓRNA (agregat) — sądy administracyjne
  (NSA/WSA) są w niej praktycznie nieobecne (dla nich: skill prawo-pl-cbosa). Polish case-law from the
  public SAOS API.
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
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-saos`). Gdy nie znasz ścieżki, najpierw ją ustal:

```
SAOS=$(find "$HOME/.claude" "$HOME/.agents" /mnt /sessions -maxdepth 10 -name saos.py -path "*prawo-pl-saos*" 2>/dev/null | head -1)
[ -n "$SAOS" ] || { curl -fsSL https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/main/plugins/prawo-pl-saos/skills/prawo-pl-saos/scripts/saos.py -o /tmp/saos.py && SAOS=/tmp/saos.py; }
python3 "$SAOS" <komenda> [...]
```

(W przykładach niżej `python3 scripts/saos.py` oznacza `python3 "$SAOS"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **szukaj** — znajdź orzeczenia po kryteriach:
  `python3 scripts/saos.py szukaj "rękojmia wiary publicznej" --sad SN --limit 5`
  Opcje: `--sad SN|TK|powszechne|admin|KIO`, `--sygnatura "III CSK 203/09"`, `--przepis "Kodeks cywilny"`
  (powołany przepis/akt), `--sedzia "Górski"`, `--haslo "nienależne świadczenie"`,
  `--typ wyrok|postanowienie|uchwala|zarzadzenie|uzasadnienie`, `--od 2020-01-01`, `--do 2024-12-31`,
  `--limit N` (1–100), `--strona N` (od 0).
- **orzeczenie** — pełne orzeczenie po ID (z listy `szukaj`):
  `python3 scripts/saos.py orzeczenie 76341`
  Pokazuje metadane, **powołane przepisy** i **powołane orzeczenia** (z ID do dalszego skoku) oraz treść
  uzasadnienia. Do długich uzasadnień: `--fragment "rękojmia"` (wycina okna wokół frazy).
- **sygnatura** — szybkie odszukanie po numerze sprawy:
  `python3 scripts/saos.py sygnatura III CSK 203/09`
- każda komenda przyjmuje `--json` (surowa odpowiedź API; podawaj PRZED komendą: `saos.py --json szukaj ...`).

Typowy przepływ: `szukaj` (zawęź `--sad`/`--przepis`/`--haslo`) → wybierz ID → `orzeczenie <id>`
→ w razie potrzeby skacz po `referencedCourtCases` do powołanych orzeczeń.

## Zasady (ważne — dlaczego)

1. **SAOS to baza WTÓRNA (agregat), nie źródło urzędowe.** Orzeczenia są jawne, ale do **dosłownego cytatu**
   i pewności zweryfikuj w portalu właściwego sądu (link „Źródło oryginalne" w `orzeczenie`). Zawsze podawaj
   **sygnaturę + sąd + datę** (np. „wyrok SN z 9.04.2010, III CSK 203/09").
2. **Brak sądów administracyjnych.** NSA/WSA są w SAOS praktycznie nieobecne (`--sad admin` zwykle zwraca 0).
   Orzecznictwo administracyjne pobieraj skillem **prawo-pl-cbosa** (baza CBOSA, `scripts/cbosa.py`).
3. **Świeżość bywa opóźniona.** Najnowsze orzeczenia mogą jeszcze nie być w bazie — przy sprawie na konkretną
   datę zaznacz „wg SAOS na dzień X — do potwierdzenia" i sprawdź portal sądu.
4. **Nie myl orzeczenia z przepisem.** Po znalezieniu orzeczenia, brzmienie powołanego przepisu i jego
   aktualność potwierdź w ELI (`prawo-pl-eli`) — orzeczenie mogło zapaść na starszym stanie prawnym.
5. Pełna lista endpointów i parametrów: `references/api.md`.

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
   cytatu zweryfikuj w portalu sądu".
