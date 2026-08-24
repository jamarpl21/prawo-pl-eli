---
name: prawo-pl-cbosa
version: 2.0.1
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
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-cbosa`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
CBOSA="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-cbosa/scripts/cbosa.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$CBOSA" ] || CBOSA="<katalog skilla>/scripts/cbosa.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$CBOSA" ] || CBOSA=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.0.1", "e81aeed97349663993a0b188664db1c902b44d7970f7dc16fcb785fa016f06c4"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-pl-cbosa/skills/prawo-pl-cbosa/scripts/cbosa.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-pl-cbosa-{WERSJA}", "cbosa.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$CBOSA" ] || exit 1
python3 "$CBOSA" <komenda> [...]
```

Nie szukaj helpera przez `find` po katalogach użytkownika ani systemu i nie pobieraj go z żadnego innego źródła niż przypięty tag z sumą wyżej (żadnego `main`, żadnej innej wersji z cache) — inna wersja helpera to inne wyniki.

(W przykładach niżej `python3 scripts/cbosa.py` oznacza `python3 "$CBOSA"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **szukaj** — znajdź orzeczenia po kryteriach:
  `python3 scripts/cbosa.py szukaj "odpowiedzialność członków zarządu" --sad NSA --od 2024-01-01`
  Opcje: `--sad NSA | "WSA Warszawa" | "WSA Kraków" …` (16 miast; można też podać pełną nazwę),
  `--sygnatura "II FSK 2870/18"`, `--rodzaj wyrok|postanowienie|uchwala`, `--symbol 6119`
  (symbol sprawy, np. 611x podatki), `--sedzia "Nowak"`, `--od/--do RRRR-MM-DD` (można też sam rok
  `2024` albo `2024-01` — silnik uzupełni do początku/końca okresu),
  `--strona N` (od 1; stała wielkość strony: 10 wyników).
- **orzeczenie** — pełne orzeczenie po `doc_id` (z listy `szukaj`):
  `python3 scripts/cbosa.py orzeczenie 8889489BE0`
  Pokazuje metadane (sąd, sędziowie, symbol, hasła, skarżony organ, **powołane przepisy**,
  sygnatury powiązane z doc_id do skoku), **prawomocność** (linia `Prawomocność:` zaraz po dacie;
  w JSON `prawomocne: true/false/null` + `prawomocnosc` = tekst oznaczenia CBOSA), sentencję
  i uzasadnienie. Do długich uzasadnień: `--fragment "interpretacja"` (wycina okna wokół frazy;
  brzegi okien dopasowane do granic zdań/wyrazów, `…` na brzegu oznacza ucięcie, `[...]` między oknami).
- **sygnatura** — szybkie odszukanie po sygnaturze:
  `python3 scripts/cbosa.py sygnatura II FSK 2870/18`
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”).

### Prawomocność i tryb `--strict`

CBOSA oznacza każde orzeczenie na stronie `/doc/<id>` kursywą **„orzeczenie prawomocne” /
„orzeczenie nieprawomocne”** (w wierszu „Data orzeczenia”); przy części orzeczeń (np. postanowienia
wpadkowe, świeże orzeczenia) pole jest **puste** — CBOSA nic nie twierdzi. Silnik czyta to oznaczenie
i w `orzeczenie` zawsze je pokazuje:

- `Prawomocność: prawomocne` — bez uwag.
- `Prawomocność: NIEPRAWOMOCNE` + głośne **UWAGA** na początku wyniku: orzeczenie mogło zostać
  **uchylone lub zmienione w wyższej instancji** (typowo: wyrok WSA uchylony przez NSA). Zanim je
  zacytujesz, skocz do orzeczenia powiązanego (`Sygn. powiązane` / `↳ powiązane`, WSA ↔ NSA) i sprawdź
  jego „Treść wyniku”. Przykład: `I SA/Bk 226/18` (doc 109C7D1883) jest nieprawomocny — NSA w
  `II FSK 2870/18` (doc 8889489BE0) „uchylił zaskarżony wyrok”.
- `Prawomocność: nieznana` + UWAGA — CBOSA nie podaje (puste pole) albo oznaczenia nie znaleziono
  (zmiana układu strony). Nie traktuj tego jak „prawomocne”.

**Lista wyników `szukaj`/`sygnatura` NIE ma tego oznaczenia** (strona wyników CBOSA go nie niesie) —
flaga jest dostępna wyłącznie przez `orzeczenie <doc_id>`.

`--strict` blokuje wynik (komunikat + kod ≠ 0, nic na stdout także z `--json`), gdy: (1) transport TLS
nie został zweryfikowany, (2) strona orzeczenia nie została rozpoznana / wyszukiwarka nie odpowiedziała
poprawną stroną, (3) orzeczenie jest oznaczone jako **nieprawomocne**, (4) prawomocności na stronie
brak (puste pole / oznaczenie nieznalezione). Bez `--strict` dostaniesz w tych dwóch ostatnich
przypadkach pełny tekst z ostrzeżeniem. `--strict` **nie sprawdza**: czy wyrok NSA jest ostateczny
w sensie wznowienia/skargi nadzwyczajnej, czy przepisy powołane w orzeczeniu nadal obowiązują (→ ELI),
ani prawomocności wyników `szukaj` (brak danych na liście).

Typowy przepływ: `szukaj` (zawęź `--sad`/`--od`/`--symbol`) → wybierz doc_id → `orzeczenie <doc_id>`
(spójrz na `Prawomocność:`) → w razie potrzeby skacz po sygnaturach powiązanych (WSA ↔ NSA w tej samej
sprawie).

## Zasady (ważne — dlaczego)

1. **To scraping, nie API.** CBOSA nie udostępnia API ani zrzutów danych; silnik parsuje publiczny
   HTML. Zmiana układu stron może zepsuć parsowanie — gdy wynik wygląda na obcięty/pusty, zajrzyj
   pod podany link „Źródło" i zgłoś problem. Nie zrównoleglaj zapytań (wbudowany throttling ≥0,5 s;
   serwer bywa przeciążony i ucina połączenia — silnik sam ponawia).
2. **Rozróżniaj trzy komunikaty — tylko jeden znaczy „awaria".**
   „Brak wyników (zweryfikowane zero)" = CBOSA wyszukało i nic nie ma → **zmień zapytanie**
   (krótsza fraza, bez `--sad`, szerszy zakres dat), nie ponawiaj tego samego.
   „CBOSA odrzuciło zapytanie: …" = błąd parametrów (np. formatu daty) → popraw i ponów.
   „BŁĄD: … strona bez listy wyników" albo „BŁĄD sieci" = serwer → ponów za chwilę.
3. **Baza ma charakter informacyjno-edukacyjny** (nie jest urzędowym publikatorem, orzeczenia są
   zanonimizowane). Zawsze podawaj **sygnaturę + sąd + datę** (np. „wyrok NSA z 10.02.2021,
   II FSK 2870/18"); do dosłownego cytatu w piśmie podaj też link do strony orzeczenia.
4. **Dwuinstancyjność:** sprawy WSA i NSA łącz przez pole „Sygn. powiązane" (skarga kasacyjna od
   wyroku WSA → wyrok NSA). Sygnatury: NSA np. `II FSK 2870/18`, WSA np. `I SA/Bk 226/18`.
   Wyrok WSA oznaczony `NIEPRAWOMOCNE` może już nie obowiązywać — najpierw sprawdź wyrok NSA.
5. **Nie myl orzeczenia z przepisem.** Brzmienie i aktualność powołanych przepisów potwierdź w ELI
   (skill prawo-pl-eli) — orzeczenie mogło zapaść na starszym stanie prawnym.
6. **Okno serwisowe:** CBOSA ma codzienną krótką przerwę ok. 21:00 — błędy o tej porze są normalne.
7. **Błąd certyfikatu TLS nie obniża zabezpieczeń automatycznie.** Domyślnie daje `UNKNOWN`.
   Jeżeli operator świadomie akceptuje ryzyko, może dla jednego uruchomienia ustawić
   `CBOSA_INSECURE_TLS=1`. Wynik JSON ma wtedy `transport_tls_verified: false`, a wynik tekstowy
   ostrzeżenie przy treści. Takiej treści nie cytuj bez sprawdzenia w innym źródle.
8. Szczegóły kontraktu HTML (pola formularza, struktura stron): `references/api.md`.

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
