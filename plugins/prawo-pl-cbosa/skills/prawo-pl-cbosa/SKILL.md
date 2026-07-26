---
name: prawo-pl-cbosa
version: 1.6.2
description: >-
  Przeszukuje CBOSA — Centralną Bazę Orzeczeń Sądów Administracyjnych (orzeczenia.nsa.gov.pl):
  wyroki, postanowienia i uchwały NSA oraz 16 WSA (~2,4 mln orzeczeń od 2004 r.). Używaj przy
  KAŻDYM pytaniu o orzecznictwo sądów administracyjnych: skargi na decyzje organów, interpretacje
  podatkowe, „jak NSA/WSA interpretuje…", „wyrok NSA sygn. …", linia orzecznicza w sprawach
  administracyjnych i podatkowych (k.p.a., Ordynacja podatkowa, p.p.s.a., RODO/UODO, prawo
  budowlane, samorządowe, cudzoziemcy). CBOSA NIE MA oficjalnego API — skill czyta publiczne
  strony HTML (read-only, z throttlingiem). Orzecznictwo SN/TK/sądów powszechnych/KIO → skill
  prawo-pl-saos; treść przepisów → prawo-pl-eli; decyzje UODO → prawo-pl-uodo. Polish
  administrative courts (NSA/WSA) case-law from the public CBOSA database.
---

# Orzecznictwo sądów administracyjnych (NSA/WSA) z CBOSA

Skill do **orzecznictwa sądów administracyjnych**: Naczelnego Sądu Administracyjnego (NSA)
i 16 wojewódzkich sądów administracyjnych (WSA). Sięga do **CBOSA** — Centralnej Bazy Orzeczeń
Sądów Administracyjnych (`https://orzeczenia.nsa.gov.pl`, ~2,4 mln orzeczeń od 2004 r., wybrane
starsze). Używaj go zawsze, gdy pytanie dotyczy **skarg na decyzje administracyjne, interpretacji
podatkowych, postępowania przed organami** — czyli tam, gdzie właściwe są sądy administracyjne.

**CBOSA nie ma oficjalnego API** — silnik czyta publiczne strony HTML wyszukiwarki (read-only,
bez logowania, z throttlingiem ≥0,5 s). To najbardziej kompletne i urzędowe źródło orzecznictwa
administracyjnego; SAOS (skill prawo-pl-saos) sądów administracyjnych praktycznie nie ma.

## Podział ról (który skill do czego)

1. **NSA/WSA (administracja, podatki) → ten skill.** Skargi na decyzje, interpretacje
   indywidualne, kary administracyjne, RODO (skargi na decyzje UODO), budowlane, samorząd.
2. **SN/TK/sądy powszechne/KIO → prawo-pl-saos.** Sprawy cywilne, karne, gospodarcze.
3. **Treść przepisu → prawo-pl-eli** (ELI Sejmu). Orzeczenie mogło zapaść na starszym stanie
   prawnym — brzmienie i aktualność przepisu potwierdź w ELI.
4. **Decyzje Prezesa UODO → prawo-pl-uodo**; wyroki WSA/NSA ze skarg na te decyzje → ten skill.
5. **Delegując research subagentowi**, wpisz: „orzecznictwo sądów administracyjnych pobieraj
   przez `scripts/cbosa.py` (skill prawo-pl-cbosa)".

## Narzędzie

Wszystko robi helper `scripts/cbosa.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/cbosa.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-cbosa` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-cbosa`). Gdy nie znasz ścieżki, najpierw ją ustal:

```
CBOSA=$(find "$HOME/.claude" "$HOME/.agents" /mnt /sessions -maxdepth 10 -name cbosa.py -path "*prawo-pl-cbosa*" 2>/dev/null | head -1)
[ -n "$CBOSA" ] || { curl -fsSL https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/main/plugins/prawo-pl-cbosa/skills/prawo-pl-cbosa/scripts/cbosa.py -o /tmp/cbosa.py && CBOSA=/tmp/cbosa.py; }
python3 "$CBOSA" <komenda> [...]
```

(W przykładach niżej `python3 scripts/cbosa.py` oznacza `python3 "$CBOSA"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **szukaj** — znajdź orzeczenia po kryteriach:
  `python3 scripts/cbosa.py szukaj "odpowiedzialność członków zarządu" --sad NSA --od 2024-01-01`
  Opcje: `--sad NSA | "WSA Warszawa" | "WSA Kraków" …` (16 miast; można też podać pełną nazwę),
  `--sygnatura "II FSK 2870/18"`, `--rodzaj wyrok|postanowienie|uchwala`, `--symbol 6119`
  (symbol sprawy, np. 611x podatki), `--sedzia "Nowak"`, `--od/--do RRRR-MM-DD`,
  `--strona N` (od 1; stała wielkość strony: 10 wyników).
- **orzeczenie** — pełne orzeczenie po `doc_id` (z listy `szukaj`):
  `python3 scripts/cbosa.py orzeczenie 8889489BE0`
  Pokazuje metadane (sąd, sędziowie, symbol, hasła, skarżony organ, **powołane przepisy**,
  sygnatury powiązane z doc_id do skoku), sentencję i uzasadnienie. Do długich uzasadnień:
  `--fragment "interpretacja"` (wycina okna wokół frazy).
- **sygnatura** — szybkie odszukanie po sygnaturze:
  `python3 scripts/cbosa.py sygnatura II FSK 2870/18`
- każda komenda przyjmuje `--json` (sparsowane dane jako JSON; podawaj PRZED komendą).

Typowy przepływ: `szukaj` (zawęź `--sad`/`--od`/`--symbol`) → wybierz doc_id → `orzeczenie <doc_id>`
→ w razie potrzeby skacz po sygnaturach powiązanych (WSA ↔ NSA w tej samej sprawie).

## Zasady (ważne — dlaczego)

1. **To scraping, nie API.** CBOSA nie udostępnia API ani zrzutów danych; silnik parsuje publiczny
   HTML. Zmiana układu stron może zepsuć parsowanie — gdy wynik wygląda na obcięty/pusty, zajrzyj
   pod podany link „Źródło" i zgłoś problem. Nie zrównoleglaj zapytań (wbudowany throttling ≥0,5 s;
   serwer bywa przeciążony i ucina połączenia — silnik sam ponawia).
2. **Baza ma charakter informacyjno-edukacyjny** (nie jest urzędowym publikatorem, orzeczenia są
   zanonimizowane). Zawsze podawaj **sygnaturę + sąd + datę** (np. „wyrok NSA z 10.02.2021,
   II FSK 2870/18"); do dosłownego cytatu w piśmie podaj też link do strony orzeczenia.
3. **Dwuinstancyjność:** sprawy WSA i NSA łącz przez pole „Sygn. powiązane" (skarga kasacyjna od
   wyroku WSA → wyrok NSA). Sygnatury: NSA np. `II FSK 2870/18`, WSA np. `I SA/Bk 226/18`.
4. **Nie myl orzeczenia z przepisem.** Brzmienie i aktualność powołanych przepisów potwierdź w ELI
   (skill prawo-pl-eli) — orzeczenie mogło zapaść na starszym stanie prawnym.
5. **Okno serwisowe:** CBOSA ma codzienną krótką przerwę ok. 21:00 — błędy o tej porze są normalne.
6. Szczegóły kontraktu HTML (pola formularza, struktura stron): `references/api.md`.

## Czego ten skill NIE obejmuje

- **treści przepisów** (ustawy/kodeksy → skill **prawo-pl-eli**),
- **orzecznictwa SN, TK, sądów powszechnych i KIO** (→ skill **prawo-pl-saos**),
- **prawa UE i orzecznictwa TSUE** (→ skill **prawo-eu-eurlex**),
- **decyzji Prezesa UODO** (→ skill **prawo-pl-uodo**; tu są tylko WYROKI ze skarg na nie),
- pism stron, akt sprawy (dostępne tylko dla stron postępowania w portalach sądów).
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że CBOSA je pokryje.

## Przykładowy przepływ

Pytanie: „jak NSA podchodzi do odpowiedzialności członka zarządu za zaległości podatkowe spółki
(art. 116 Ordynacji podatkowej)?"
1. (opcjonalnie) ELI: `tekst <t.j. Ordynacji> --fragment "art. 116"` → brzmienie przepisu
   (skill prawo-pl-eli).
2. `szukaj "odpowiedzialność członka zarządu" --sad NSA --od 2024-01-01 --symbol 6116`
   → lista wyroków NSA z sygnaturami i doc_id.
3. `orzeczenie <doc_id> --fragment "art. 116"` → wycinki uzasadnienia wokół przepisu
   + powołane przepisy + sygnatury powiązane (wyrok WSA I instancji).
4. W odpowiedzi: zacytuj tezę, podaj sygnaturę + sąd + datę + link, zaznacz „baza CBOSA
   (informacyjna) — do dosłownego cytatu zweryfikuj na stronie orzeczenia".
