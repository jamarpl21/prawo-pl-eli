# prawo-pl-eli

**Polskie prawo z OFICJALNEGO API ELI Sejmu — zamiast cytowania przepisów z pamięci.**
*Cross-tool agent skill (Claude Code + OpenAI Codex) that reads Polish primary law straight from the official Sejm ELI API.*

Cytowanie polskich przepisów „z głowy" jest zawodne: ustawy są nowelizowane, sygnatury się mylą, a model
potrafi podać brzmienie sprzed kilku zmian. Ten skill sięga do **źródła pierwotnego** — oficjalnego
[API ELI Sejmu](https://api.sejm.gov.pl/eli) (Dziennik Ustaw i Monitor Polski) — i pozwala:

- wyszukać akt po tytule / typie / roku / haśle przedmiotowym,
- pobrać metadane (w tym **datę wejścia w życie**), pełny tekst i **TEKST JEDNOLITY**,
- wyciąć **pojedynczy artykuł** (`tekst --fragment "art. 299"`) — pod cytat w umowie lub piśmie procesowym,
- sprawdzić **nowelizacje**, podstawę prawną i czy akt **nadal obowiązuje** (narzędzie samo ostrzega
  przed cytowaniem nieaktualnej wersji),
- zacytować przepis z poprawną sygnaturą `Dz.U.`/`M.P.` i identyfikatorem ELI.

Pomyślany jako wsparcie pracy nad **umowami i pismami procesowymi** (tworzenie i analiza) — wszędzie tam,
gdzie trzeba odwołać się do przepisów i procedur prawa polskiego.

Skill jest w otwartym standardzie **[Agent Skills](https://agentskills.io)** (`SKILL.md`), więc działa w
**Claude Code** i **OpenAI Codex**. Silnik (`scripts/eli.py`) to czysty Python (tylko stdlib), wszystko
**read-only** (same `GET`).

## Wymagania

- Python 3.8+ (tylko stdlib; brak `pip install`)
- dostęp do internetu (`https://api.sejm.gov.pl/eli`)

## Instalacja

### Claude Code

```
/plugin marketplace add jamarpl21/prawo-pl-eli
/plugin install prawo-pl-eli@gibek-skills
```

Aktualizacje: `/plugin marketplace update`.

### OpenAI Codex

```
codex plugin marketplace add jamarpl21/prawo-pl-eli
codex plugin add prawo-pl-eli@gibek-skills
```

Aktualizacje: `codex plugin marketplace upgrade`.

### Ręcznie / dev (oba narzędzia)

Sklonuj repo i podlinkuj sam katalog skilla (otwarty standard Agent Skills):

```bash
git clone https://github.com/jamarpl21/prawo-pl-eli
SKILL="$PWD/prawo-pl-eli/plugins/prawo-pl-eli/skills/prawo-pl-eli"
ln -s "$SKILL" ~/.claude/skills/prawo-pl-eli    # Claude Code
ln -s "$SKILL" ~/.agents/skills/prawo-pl-eli    # OpenAI Codex
```

### Paczka ZIP (offline / pojedyncza sesja)

Każdy tag `v*` publikuje `prawo-pl-eli-<wersja>.zip` w GitHub Releases (artefakt budowany przez GitHub Actions):

```bash
claude --plugin-dir ./prawo-pl-eli-v1.1.0.zip
# albo zdalnie, bez pobierania:
claude --plugin-url https://github.com/jamarpl21/prawo-pl-eli/releases/download/v1.1.0/prawo-pl-eli-v1.1.0.zip
```

## Użycie jako samodzielne CLI (bez żadnego LLM-a)

```bash
cd plugins/prawo-pl-eli/skills/prawo-pl-eli
python3 scripts/eli.py <komenda> [...]
```

| Komenda | Opis | Przykład |
|---|---|---|
| `szukaj` | znajdź akt po tytule/typie/roku/haśle | `szukaj "Kodeks spółek handlowych" --typ Ustawa --limit 5` |
| `meta` | metadane aktu (status, wejście w życie, pliki tekstu) | `meta DU 2000 1037` |
| `tj` | znajdź AKTUALNY tekst jednolity (najnowszy oznaczony) | `tj DU 2000 1037` |
| `odniesienia` | nowelizacje, podstawa prawna, tekst jednolity | `odniesienia DU 2024 18` |
| `tekst` | treść aktu; `--fragment` wycina pojedynczy artykuł; `--pdf` zapisuje urzędowy PDF | `tekst DU 2024 18 --fragment "art. 299"` |
| `struktura` | spis jednostek redakcyjnych aktu | `struktura DU 2024 18 --filtr "Art. 299"` |

Komendy `tekst` i `tj` same ostrzegają, gdy cytujesz z nieaktualnej wersji (istnieje nowszy tekst
jednolity / są nowelizacje po t.j.).

Każda komenda przyjmuje `--json` (surowa odpowiedź API). Sygnaturę można podać w wielu formach:
`DU 2000 1037`, `DU/2024/18`, `"Dz.U. 2024 poz. 18"`, `WDU20240000018`, albo `DU/2000/1037` (ELI).

### Przykładowy przepływ

> „co dokładnie mówi art. 299 § 1 Kodeksu spółek handlowych i czy to aktualne?"

```bash
python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa   # → akt bazowy DU/2000/1037
python3 scripts/eli.py tj DU 2000 1037                                  # → AKTUALNY tekst jednolity DU/2024/18
python3 scripts/eli.py tekst DU 2024 18 --fragment "art. 299"           # → sam art. 299 + ostrzeżenie
                                                                        #   o nowelizacjach po t.j.
```

## Struktura

```
.claude-plugin/marketplace.json          # marketplace dla Claude Code
.agents/plugins/marketplace.json         # marketplace dla Codex (Claude czyta .claude-plugin/)
plugins/prawo-pl-eli/
├── .claude-plugin/plugin.json           # manifest pluginu — Claude
├── .codex-plugin/plugin.json            # manifest pluginu — Codex ("skills": "./skills/")
└── skills/prawo-pl-eli/                  # Agent Skills — WSPÓLNE dla obu narzędzi
    ├── SKILL.md
    ├── scripts/eli.py                    # silnik (stdlib, read-only)
    └── references/api.md                 # referencja endpointów API ELI
tools/validate.py                        # walidator manifestów (używany w CI)
tools/test_eli.py                        # testy jednostkowe silnika, offline (używane w CI)
.github/workflows/release.yml            # GitHub Actions: walidacja + testy + ZIP release na tagu v*
```

## GitHub Actions (deploy)

- **push / PR** → `tools/validate.py` waliduje oba manifesty pluginu (Claude + Codex), oba marketplace'y i
  frontmatter `SKILL.md`; `tools/test_eli.py` testuje silnik (offline, bez sieci).
- **tag `v*`** → build `prawo-pl-eli-<tag>.zip` (zawartość pluginu) + GitHub Release z paczką
  (instalowalną przez `claude --plugin-dir` / `--plugin-url`).

## Wersjonowanie

Wersja pluginu (obecnie **1.1.0**) jest zadeklarowana w sześciu miejscach i musi być wszędzie identyczna —
`tools/validate.py` wymusza to w CI:

- `plugins/prawo-pl-eli/.claude-plugin/plugin.json` i `.codex-plugin/plugin.json` (pole `version`),
- wpis pluginu w obu marketplace'ach (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`),
- frontmatter `SKILL.md` (pole `version`),
- `scripts/eli.py` (`__version__`; CLI: `python3 scripts/eli.py --version`).

Wydanie nowej wersji: bump `version` we wszystkich powyższych → `python3 tools/validate.py &&
python3 tools/test_eli.py` → `git tag v1.1.1` → `git push --tags`.

## Ważne zastrzeżenia

- **To nie jest porada prawna.** Narzędzie pomaga dotrzeć do treści i sygnatury aktu — interpretacja
  należy do prawnika.
- **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** Sprawdzaj `wejście w życie`/vacatio legis i odnoś przepis do **daty
  zdarzenia/sprawy**. Tekst jednolity oddaje stan na `legalStatusDate`; nowsze zmiany trzeba nałożyć ręcznie.
- **Indeksacja bywa opóźniona** — brak nowelizacji w API ≠ pewność, że jej nie ma. Przy sprawie na
  konkretną datę zweryfikuj dodatkowo (np. `dziennikustaw.gov.pl`, proces legislacyjny).
- Projekt nieoficjalny; korzysta z publicznego, oficjalnego API Kancelarii Sejmu.

## Licencja

MIT — zobacz [`LICENSE`](LICENSE).
