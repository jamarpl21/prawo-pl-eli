# gibek-skills: prawo-pl-eli + prawo-eu-eurlex + prawo-pl-saos

[![CI](https://github.com/jamarpl21/prawo-pl-eli/actions/workflows/release.yml/badge.svg)](https://github.com/jamarpl21/prawo-pl-eli/actions/workflows/release.yml)
[![Release](https://img.shields.io/github/v/release/jamarpl21/prawo-pl-eli)](https://github.com/jamarpl21/prawo-pl-eli/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Prawo polskie i unijne oraz orzecznictwo z OFICJALNYCH źródeł — zamiast cytowania z pamięci.**
*Cross-tool agent skills (Claude Code + OpenAI Codex) reading Polish primary law from the official
Sejm ELI API, EU law from the Publications Office CELLAR/EUR-Lex, and Polish case-law from the SAOS API.*

Repo zawiera trzy bliźniacze pluginy/skille (wspólny marketplace `gibek-skills`, wersjonowane razem):

| Plugin / skill | Źródło | Zakres |
|---|---|---|
| **prawo-pl-eli** | [API ELI Sejmu](https://api.sejm.gov.pl/eli) | prawo polskie: Dz.U./M.P., teksty jednolite, kodeksy |
| **prawo-eu-eurlex** | [CELLAR/EUR-Lex](https://publications.europa.eu/webapi/rdf/sparql) | prawo UE: rozporządzenia, dyrektywy, wersje skonsolidowane (CELEX) |
| **prawo-pl-saos** | [API SAOS](https://www.saos.org.pl/api) | polskie orzecznictwo: SN, TK, sądy powszechne, KIO |

## prawo-pl-eli

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

## prawo-eu-eurlex

Ten sam wzorzec dla **prawa Unii Europejskiej**: silnik `eurlex.py` odpytuje **CELLAR** — wspólne
repozytorium Urzędu Publikacji UE zasilające EUR-Lex (SPARQL + REST, bez rejestracji i klucza) — i pozwala:

- wyszukać akt po polskim (lub innym) tytule: `szukaj "sztucznej inteligencji" --typ REG`,
- pobrać metadane z **datami wejścia w życie i stosowania** (`meta 32016R0679` — RODO pokaże obie!),
- znaleźć **WERSJE SKONSOLIDOWANE** (odpowiednik tekstu jednolitego): `skonsolidowany 32006L0112`,
- wyciąć **pojedynczy artykuł po polsku** (lub w 23 innych językach):
  `tekst 02016R0679-20160504 --fragment "art. 28"`, `--pdf` zapisuje urzędowy PDF,
- sprawdzić **nowelizacje, sprostowania i podstawę traktatową**: `odniesienia 32016R0679`.

Identyfikatorem jest numer **CELEX** (`32016R0679`), akceptowana też forma ELI (`reg/2016/679`).
Skille odsyłają do siebie nawzajem: dyrektywa UE → transpozycja → polska ustawa (prawo-pl-eli);
polski przepis wdrażający → akt źródłowy UE (prawo-eu-eurlex).

## prawo-pl-saos

Trzeci skill domyka komplet: **orzecznictwo** (judykatura). Silnik `saos.py` odpytuje publiczne
[API SAOS](https://www.saos.org.pl/api) (System Analizy Orzeczeń Sądowych) — agregat orzeczeń jawnych:
**SN, TK, sądy powszechne (SA/SO/SR), KIO** — i pozwala:

- znaleźć orzeczenia po frazie, **sygnaturze**, sądzie, sędzim, **powołanym przepisie** i dacie:
  `szukaj "odpowiedzialność członka zarządu" --przepis "Kodeks spółek handlowych" --sad SN`,
- pobrać **pełne orzeczenie** po ID: teza/uzasadnienie, **powołane przepisy** i **powołane orzeczenia**
  (z ID do dalszego skoku po linii orzeczniczej): `orzeczenie 76341`,
- odszukać wyrok po numerze sprawy: `sygnatura III CSK 203/09`,
- wyciąć fragment długiego uzasadnienia wokół frazy: `orzeczenie 76341 --fragment "rękojmia"`.

Podział ról: **treść przepisu → prawo-pl-eli (ELI)**, **jak sądy go stosują → prawo-pl-saos (SAOS)**.
Most między nimi: ustal akt w ELI, potem `szukaj --przepis "<akt>"` w SAOS. **Uwaga:** SAOS to baza
**wtórna** (agregat) — sądy administracyjne (NSA/WSA) są w niej praktycznie nieobecne (dla nich:
[CBOSA](https://orzeczenia.nsa.gov.pl), bez API), a do dosłownego cytatu warto zajrzeć do portalu sądu.

Wszystkie skille są w otwartym standardzie **[Agent Skills](https://agentskills.io)** (`SKILL.md`), więc działają w
**Claude Code** i **OpenAI Codex**. Silniki (`scripts/eli.py`, `scripts/eurlex.py`, `scripts/saos.py`) to
czysty Python (tylko stdlib), wszystko **read-only**.

## Wymagania

- Python 3.8+ (tylko stdlib; brak `pip install`)
- dostęp do internetu (`api.sejm.gov.pl`, `publications.europa.eu`, `www.saos.org.pl`)

## Instalacja

### Claude Code

```
/plugin marketplace add jamarpl21/prawo-pl-eli
/plugin install prawo-pl-eli@gibek-skills
/plugin install prawo-eu-eurlex@gibek-skills
/plugin install prawo-pl-saos@gibek-skills
```

Aktualizacje: `/plugin marketplace update`.

### OpenAI Codex

```
codex plugin marketplace add jamarpl21/prawo-pl-eli
codex plugin add prawo-pl-eli@gibek-skills
codex plugin add prawo-eu-eurlex@gibek-skills
codex plugin add prawo-pl-saos@gibek-skills
```

Aktualizacje: `codex plugin marketplace upgrade`.

### Ręcznie / dev (oba narzędzia)

Sklonuj repo i podlinkuj sam katalog skilla (otwarty standard Agent Skills):

```bash
git clone https://github.com/jamarpl21/prawo-pl-eli
for s in prawo-pl-eli prawo-eu-eurlex prawo-pl-saos; do
  SKILL="$PWD/prawo-pl-eli/plugins/$s/skills/$s"
  ln -s "$SKILL" ~/.claude/skills/$s    # Claude Code
  ln -s "$SKILL" ~/.agents/skills/$s    # OpenAI Codex
done
```

### Paczka ZIP (offline / pojedyncza sesja)

Każdy tag `v*` publikuje po jednym zipie na plugin w GitHub Releases
(`prawo-pl-eli-<wersja>.zip`, `prawo-eu-eurlex-<wersja>.zip`, `prawo-pl-saos-<wersja>.zip`):

```bash
claude --plugin-dir ./prawo-pl-saos-v1.4.0.zip
# albo zdalnie, bez pobierania:
claude --plugin-url https://github.com/jamarpl21/prawo-pl-eli/releases/download/v1.4.0/prawo-pl-saos-v1.4.0.zip
```

## Użycie jako samodzielne CLI (bez żadnego LLM-a)

```bash
cd plugins/prawo-pl-eli/skills/prawo-pl-eli && python3 scripts/eli.py <komenda> [...]
cd plugins/prawo-eu-eurlex/skills/prawo-eu-eurlex && python3 scripts/eurlex.py <komenda> [...]
cd plugins/prawo-pl-saos/skills/prawo-pl-saos && python3 scripts/saos.py <komenda> [...]
```

### eli.py (prawo polskie)

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

### eurlex.py (prawo UE)

| Komenda | Opis | Przykład |
|---|---|---|
| `szukaj` | znajdź akt po frazie z tytułu (domyślnie PL) | `szukaj "sztucznej inteligencji" --typ REG` |
| `meta` | metadane (typ, wejście w życie / stosowanie, status, ELI) | `meta 32016R0679` |
| `skonsolidowany` | wersje skonsolidowane (odpowiednik t.j.) | `skonsolidowany 32006L0112` |
| `odniesienia` | nowelizacje, sprostowania, podstawa traktatowa | `odniesienia 32016R0679` |
| `tekst` | treść aktu (domyślnie PL); `--fragment` wycina artykuł; `--jezyk`, `--pdf` | `tekst 02016R0679-20160504 --fragment "art. 28"` |

### saos.py (orzecznictwo polskie)

| Komenda | Opis | Przykład |
|---|---|---|
| `szukaj` | znajdź orzeczenia po frazie/sądzie/sygnaturze/przepisie/dacie | `szukaj "rękojmia" --sad SN --przepis "Kodeks cywilny" --limit 5` |
| `orzeczenie` | pełne orzeczenie po ID (teza, powołane przepisy i orzeczenia, treść); `--fragment` | `orzeczenie 76341 --fragment "rękojmia"` |
| `sygnatura` | szybkie odszukanie po numerze sprawy | `sygnatura III CSK 203/09` |

`--sad`: `SN | TK | powszechne | admin | KIO`. SAOS to baza **wtórna** — `--sad admin` zwykle zwraca 0
(orzecznictwo administracyjne: [CBOSA](https://orzeczenia.nsa.gov.pl), bez API). Każda komenda przyjmuje `--json`.

### Przykładowe przepływy

> „co dokładnie mówi art. 299 § 1 Kodeksu spółek handlowych i czy to aktualne?"

```bash
python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa   # → akt bazowy DU/2000/1037
python3 scripts/eli.py tj DU 2000 1037                                  # → AKTUALNY tekst jednolity DU/2024/18
python3 scripts/eli.py tekst DU 2024 18 --fragment "art. 299"           # → sam art. 299 + ostrzeżenie
                                                                        #   o nowelizacjach po t.j.
```

> „czy klauzula powierzenia jest zgodna z art. 28 RODO?"

```bash
python3 scripts/eurlex.py skonsolidowany 32016R0679                     # → 02016R0679-20160504 (AKTUALNA)
python3 scripts/eurlex.py tekst 02016R0679-20160504 --fragment "art. 28"  # → art. 28 po polsku
```

> „jak SN podchodzi do odpowiedzialności członka zarządu z art. 299 k.s.h.?"

```bash
python3 scripts/saos.py szukaj "odpowiedzialność członka zarządu" \
        --przepis "Kodeks spółek handlowych" --sad SN --limit 5         # → lista orzeczeń SN z ID
python3 scripts/saos.py orzeczenie <id>                                 # → teza + powołane przepisy/orzeczenia
```

## Struktura

```
.claude-plugin/marketplace.json          # marketplace dla Claude Code (trzy pluginy)
.agents/plugins/marketplace.json         # marketplace dla Codex (Claude czyta .claude-plugin/)
plugins/<plugin>/                        # prawo-pl-eli | prawo-eu-eurlex | prawo-pl-saos — identyczny układ
├── .claude-plugin/plugin.json           # manifest pluginu — Claude
├── .codex-plugin/plugin.json            # manifest pluginu — Codex ("skills": "./skills/")
└── skills/<plugin>/                      # Agent Skills — WSPÓLNE dla obu narzędzi
    ├── SKILL.md
    ├── scripts/eli.py | eurlex.py | saos.py   # silnik (stdlib, read-only)
    └── references/api.md                 # referencja endpointów źródła
tools/validate.py                        # walidator manifestów wszystkich pluginów (używany w CI)
tools/test_eli.py, test_eurlex.py, test_saos.py  # testy jednostkowe silników, offline (używane w CI)
.github/workflows/release.yml            # GitHub Actions: walidacja + testy + ZIP-y release na tagu v*
```

## GitHub Actions (deploy)

- **push / PR** → `tools/validate.py` waliduje manifesty WSZYSTKICH pluginów (Claude + Codex), oba marketplace'y
  i frontmattery `SKILL.md`; `tools/test_*.py` testują silniki (offline, bez sieci).
- **tag `v*`** → build po jednym zipie na plugin + GitHub Release z paczkami
  (instalowalnymi przez `claude --plugin-dir` / `--plugin-url`).

## Wersjonowanie

Wszystkie pluginy są wersjonowane **razem (lockstep)** — jedna wersja (obecnie **1.4.0**) zadeklarowana
we wszystkich miejscach, identyczna; `tools/validate.py` wymusza to w CI:

- `plugins/<plugin>/.claude-plugin/plugin.json` i `.codex-plugin/plugin.json` (pole `version`) — wszystkie pluginy,
- wpisy wszystkich pluginów w obu marketplace'ach (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`),
- frontmattery `SKILL.md` (pole `version`),
- `scripts/eli.py`, `scripts/eurlex.py` i `scripts/saos.py` (`__version__`; CLI: `--version`).

Wydanie nowej wersji: bump `version` we wszystkich powyższych → `python3 tools/validate.py &&
for t in tools/test_*.py; do python3 $t; done` → `git tag v1.x.y` → `git push --tags`.

## Ważne zastrzeżenia

- **To nie jest porada prawna.** Narzędzie pomaga dotrzeć do treści i sygnatury aktu / orzeczenia —
  interpretacja należy do prawnika.
- **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** Sprawdzaj `wejście w życie`/vacatio legis i odnoś przepis do **daty
  zdarzenia/sprawy**. Tekst jednolity oddaje stan na `legalStatusDate`; nowsze zmiany trzeba nałożyć ręcznie.
- **Indeksacja bywa opóźniona** — brak nowelizacji w API ≠ pewność, że jej nie ma. Przy sprawie na
  konkretną datę zweryfikuj dodatkowo (np. `dziennikustaw.gov.pl`, proces legislacyjny).
- **Wersja skonsolidowana EUR-Lex ma charakter dokumentacyjny** (nie jest tekstem autentycznym) —
  w piśmie urzędowym wskaż akt bazowy + akty zmieniające.
- **SAOS to baza wtórna (agregat orzeczeń jawnych)** — nie zawiera sądów administracyjnych (NSA/WSA → CBOSA),
  świeżość bywa opóźniona, a do dosłownego cytatu zweryfikuj orzeczenie w portalu właściwego sądu.
- Projekt nieoficjalny; korzysta z publicznych API Kancelarii Sejmu, Urzędu Publikacji UE i SAOS (ICM UW / Fundacja ePaństwo).

## Licencja

MIT — zobacz [`LICENSE`](LICENSE).
