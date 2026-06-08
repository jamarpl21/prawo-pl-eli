# prawo-pl-eli

**Polskie prawo z OFICJALNEGO API ELI Sejmu — zamiast cytowania przepisów z pamięci.**
*A Claude Code skill (and standalone CLI) that reads Polish primary law straight from the official Sejm ELI API.*

Cytowanie polskich przepisów „z głowy" jest zawodne: ustawy są nowelizowane, sygnatury się mylą, a model
potrafi podać brzmienie sprzed kilku zmian. Ten skill sięga do **źródła pierwotnego** — oficjalnego
[API ELI Sejmu](https://api.sejm.gov.pl/eli) (Dziennik Ustaw i Monitor Polski) — i pozwala:

- wyszukać akt po tytule / typie / roku,
- pobrać metadane, pełny tekst i **TEKST JEDNOLITY**,
- sprawdzić **nowelizacje**, podstawę prawną i czy akt **nadal obowiązuje**,
- zacytować przepis z poprawną sygnaturą `Dz.U.`/`M.P.` i identyfikatorem ELI.

Wszystko **read-only** (same żądania `GET`), bez zależności pip — wyłącznie biblioteka standardowa Pythona.

## Wymagania

- Python 3.8+ (tylko stdlib; brak `pip install`)
- dostęp do internetu (`https://api.sejm.gov.pl/eli`)

## Instalacja jako skill Claude Code

```bash
git clone https://github.com/<twoj-user>/prawo-pl-eli ~/.claude/skills/prawo-pl-eli
```

Po sklonowaniu skill `prawo-pl-eli` jest dostępny w Claude Code — wyzwala się m.in. przy
„co mówi ustawa o…", „art. X ustawy…", „tekst jednolity", „Dz.U. {rok} poz. {nr}",
„czy ten przepis nadal obowiązuje".

## Użycie jako samodzielne CLI (bez Claude Code)

```bash
python3 scripts/eli.py <komenda> [...]
```

| Komenda | Opis | Przykład |
|---|---|---|
| `szukaj` | znajdź akt po tytule/typie/roku | `szukaj "Kodeks spółek handlowych" --typ Ustawa --limit 5` |
| `meta` | metadane aktu (status, czy obowiązuje, pliki tekstu) | `meta DU 2000 1037` |
| `tj` | znajdź TEKST JEDNOLITY dla aktu | `tj DU 2000 1037` |
| `odniesienia` | nowelizacje, podstawa prawna, tekst jednolity | `odniesienia DU 2024 18` |
| `tekst` | treść aktu (HTML→tekst); `--pdf` zapisuje urzędowy PDF | `tekst DU 2024 18 --pdf /tmp/ksh.pdf` |

Każda komenda przyjmuje `--json` (surowa odpowiedź API). Sygnaturę można podać w wielu formach:
`DU 2000 1037`, `DU/2024/18`, `"Dz.U. 2024 poz. 18"`, `WDU20240000018`, albo `DU/2000/1037` (ELI).

### Przykładowy przepływ

> „co dokładnie mówi art. 299 § 1 Kodeksu spółek handlowych i czy to aktualne?"

```bash
python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa   # → akt bazowy DU/2000/1037
python3 scripts/eli.py tj DU 2000 1037                                  # → tekst jednolity DU/2024/18
python3 scripts/eli.py odniesienia DU 2024 18                           # → „Nowelizacje po tekście jednolitym"
python3 scripts/eli.py tekst DU 2024 18                                 # → odczytaj/zacytuj art. 299 § 1
```

## Struktura

```
SKILL.md            # instrukcja skilla (reguły: najpierw znajdź akt, sprawdź nowelizacje, cytuj z PDF)
scripts/eli.py      # helper CLI (stdlib only, read-only GET)
references/api.md   # pełna referencja endpointów API ELI
```

## Ważne zastrzeżenia

- **To nie jest porada prawna.** Narzędzie pomaga dotrzeć do treści i sygnatury aktu — interpretacja
  należy do prawnika.
- **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** Sprawdzaj `wejście w życie`/vacatio legis i odnoś przepis do **daty
  zdarzenia/sprawy**. Tekst jednolity oddaje stan na `legalStatusDate`; nowsze zmiany trzeba nałożyć ręcznie.
- **Indeksacja bywa opóźniona** — brak nowelizacji w API ≠ pewność, że jej nie ma. Przy sprawie na
  konkretną datę zweryfikuj dodatkowo (np. `dziennikustaw.gov.pl`, proces legislacyjny).
- Projekt nieoficjalny; korzysta z publicznego, oficjalnego API Kancelarii Sejmu.

## Licencja

Kod udostępniony na licencji wskazanej w pliku [`LICENSE`](LICENSE).
