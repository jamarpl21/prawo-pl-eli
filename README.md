# gibek-skills: prawo polskie i unijne + orzecznictwo (6 skilli)

[![CI](https://github.com/jamarpl21/prawo-pl-eli/actions/workflows/release.yml/badge.svg)](https://github.com/jamarpl21/prawo-pl-eli/actions/workflows/release.yml)
[![Release](https://img.shields.io/github/v/release/jamarpl21/prawo-pl-eli)](https://github.com/jamarpl21/prawo-pl-eli/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Prawo polskie i unijne oraz orzecznictwo z OFICJALNYCH źródeł — zamiast cytowania z pamięci.**
*Cross-tool agent skills (Claude Code + OpenAI Codex): Polish primary law (Sejm ELI API), local law
(voivodeship journals), EU law (CELLAR/EUR-Lex), Polish case-law (SAOS), administrative courts
case-law (CBOSA) and Polish DPA decisions (UODO).*

Repo zawiera sześć bliźniaczych pluginów/skilli (wspólny marketplace `gibek-skills`, wersjonowane razem):

| Plugin / skill | Źródło | Zakres |
|---|---|---|
| **prawo-pl-eli** | [API ELI Sejmu](https://api.sejm.gov.pl/eli) | prawo polskie: Dz.U./M.P., teksty jednolite, kodeksy |
| **prawo-pl-edzienniki** | API ELI 16 dzienników wojewódzkich (np. [edzienniki.duw.pl](https://edzienniki.duw.pl)) | prawo miejscowe: uchwały gmin/powiatów/sejmików, akty wojewody |
| **prawo-eu-eurlex** | [CELLAR/EUR-Lex](https://publications.europa.eu/webapi/rdf/sparql) | prawo UE: rozporządzenia, dyrektywy, wersje skonsolidowane (CELEX) |
| **prawo-pl-saos** | [API SAOS](https://www.saos.org.pl/api) | polskie orzecznictwo: SN, TK, sądy powszechne, KIO |
| **prawo-pl-cbosa** | [CBOSA](https://orzeczenia.nsa.gov.pl) (brak API — scraping) | orzecznictwo sądów administracyjnych: NSA + 16 WSA |
| **prawo-pl-uodo** | [API Portalu Orzeczeń UODO](https://orzeczenia.uodo.gov.pl/api-doc/) | decyzje Prezesa UODO (RODO): kary, upomnienia, nakazy |

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

## prawo-pl-edzienniki

Ten sam wzorzec dla **prawa miejscowego**: uchwały rad gmin/powiatów/sejmików (podatki i opłaty
lokalne, plany miejscowe MPZP, statuty, organizacja szkół), rozporządzenia i zarządzenia wojewody.
Tych aktów nie ma w Dz.U./M.P. — publikuje je 16 **wojewódzkich dzienników urzędowych**, każdy
z własnym API zgodnym z ELI. Silnik `edzienniki.py` zna tabelę hostów i pozwala:

- listować dzienniki i roczniki: `dzienniki --woj DS`,
- szukać aktów po tytule (nazwa gminy, przedmiot uchwały): `szukaj --woj DS "plan zagospodarowania" --rok 2026`
  (API dzienników ignoruje filtry serwerowe — silnik pobiera rocznik i filtruje lokalnie),
- pobrać metadane i treść: `akt DS 2026 3299`, `tekst DS 2026 3299 --fragment "§ 2"`, `--pdf plan.pdf`.

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
**prawo-pl-cbosa** niżej), a do dosłownego cytatu warto zajrzeć do portalu sądu.

## prawo-pl-cbosa

Czwarty skill wypełnia największą lukę: **orzecznictwo sądów administracyjnych** (NSA + 16 WSA) —
podatki, skargi na decyzje organów, interpretacje, k.p.a., prawo budowlane. Źródłem jest **CBOSA**
([orzeczenia.nsa.gov.pl](https://orzeczenia.nsa.gov.pl), ~2,4 mln orzeczeń od 2004 r.). CBOSA **nie ma
oficjalnego API**, więc silnik `cbosa.py` czyta publiczne strony HTML (read-only, z throttlingiem
≥0,5 s i awaryjną obsługą niekompletnego łańcucha SSL) — i pozwala:

- szukać po frazie, **sygnaturze**, sądzie, **symbolu sprawy**, sędzim i dacie:
  `szukaj "odpowiedzialność członków zarządu" --sad NSA --od 2024-01-01`,
- pobrać **pełne orzeczenie** (sentencja + uzasadnienie + powołane przepisy): `orzeczenie 8889489BE0`,
- skakać między instancjami po **sygnaturach powiązanych** (WSA ↔ NSA tej samej sprawy),
- wyciąć fragment długiego uzasadnienia: `orzeczenie <doc_id> --fragment "art. 116"`.

## prawo-pl-uodo

Piąty skill: **decyzje Prezesa UODO** (kary za naruszenia RODO, upomnienia, nakazy) z **oficjalnego
API** Portalu Orzeczeń UODO ([orzeczenia.uodo.gov.pl](https://orzeczenia.uodo.gov.pl), od 2025 r.,
bez klucza). Silnik `uodo.py` pozwala:

- przeglądać najnowsze decyzje: `najnowsze --limit 10`,
- szukać pełnotekstowo (regex, bez rozróżniania wielkości liter): `szukaj "biometr"`,
  po tytule i dacie publikacji: `szukaj --tytul "kara" --od 2026-01-01`,
- pobrać **pełną treść decyzji** po sygnaturze: `decyzja DKN.5131.9.2025 --fragment "art. 33"`.

Komplet RODO: treść rozporządzenia → **prawo-eu-eurlex**, decyzje organu → **prawo-pl-uodo**,
sądowa kontrola decyzji (WSA/NSA) → **prawo-pl-cbosa**.

Wszystkie skille są w otwartym standardzie **[Agent Skills](https://agentskills.io)** (`SKILL.md`), więc działają w
**Claude Code** i **OpenAI Codex**. Silniki (`scripts/eli.py`, `scripts/edzienniki.py`, `scripts/eurlex.py`,
`scripts/saos.py`, `scripts/cbosa.py`, `scripts/uodo.py`) to czysty Python (tylko stdlib), wszystko **read-only**.

## Wymagania

- Python 3.8+ (tylko stdlib; brak `pip install`)
- dostęp do internetu (`api.sejm.gov.pl`, hosty e-dzienników wojewódzkich, `publications.europa.eu`,
  `www.saos.org.pl`, `orzeczenia.nsa.gov.pl`, `orzeczenia.uodo.gov.pl`)

## Instalacja

### Claude Code

```
/plugin marketplace add jamarpl21/prawo-pl-eli
/plugin install prawo-pl-eli@gibek-skills
/plugin install prawo-pl-edzienniki@gibek-skills
/plugin install prawo-eu-eurlex@gibek-skills
/plugin install prawo-pl-saos@gibek-skills
/plugin install prawo-pl-cbosa@gibek-skills
/plugin install prawo-pl-uodo@gibek-skills
```

Aktualizacje: `/plugin marketplace update`.

### OpenAI Codex

```
codex plugin marketplace add jamarpl21/prawo-pl-eli
codex plugin add prawo-pl-eli@gibek-skills
codex plugin add prawo-pl-edzienniki@gibek-skills
codex plugin add prawo-eu-eurlex@gibek-skills
codex plugin add prawo-pl-saos@gibek-skills
codex plugin add prawo-pl-cbosa@gibek-skills
codex plugin add prawo-pl-uodo@gibek-skills
```

Aktualizacje: `codex plugin marketplace upgrade`.

### Ręcznie / dev (oba narzędzia)

Sklonuj repo i podlinkuj sam katalog skilla (otwarty standard Agent Skills):

```bash
git clone https://github.com/jamarpl21/prawo-pl-eli
for s in prawo-pl-eli prawo-pl-edzienniki prawo-eu-eurlex prawo-pl-saos prawo-pl-cbosa prawo-pl-uodo; do
  SKILL="$PWD/prawo-pl-eli/plugins/$s/skills/$s"
  ln -s "$SKILL" ~/.claude/skills/$s    # Claude Code
  ln -s "$SKILL" ~/.agents/skills/$s    # OpenAI Codex
done
```

### Paczka ZIP (offline / pojedyncza sesja)

Każdy tag `v*` publikuje po jednym zipie na plugin w GitHub Releases
(`prawo-pl-eli-<wersja>.zip`, `prawo-pl-edzienniki-<wersja>.zip`, `prawo-eu-eurlex-<wersja>.zip`,
`prawo-pl-saos-<wersja>.zip`, `prawo-pl-cbosa-<wersja>.zip`, `prawo-pl-uodo-<wersja>.zip`):

```bash
claude --plugin-dir ./prawo-pl-saos-v1.5.0.zip
# albo zdalnie, bez pobierania:
claude --plugin-url https://github.com/jamarpl21/prawo-pl-eli/releases/download/v1.5.0/prawo-pl-saos-v1.5.0.zip
```

## Użycie jako samodzielne CLI (bez żadnego LLM-a)

```bash
cd plugins/prawo-pl-eli/skills/prawo-pl-eli && python3 scripts/eli.py <komenda> [...]
cd plugins/prawo-pl-edzienniki/skills/prawo-pl-edzienniki && python3 scripts/edzienniki.py <komenda> [...]
cd plugins/prawo-eu-eurlex/skills/prawo-eu-eurlex && python3 scripts/eurlex.py <komenda> [...]
cd plugins/prawo-pl-saos/skills/prawo-pl-saos && python3 scripts/saos.py <komenda> [...]
cd plugins/prawo-pl-cbosa/skills/prawo-pl-cbosa && python3 scripts/cbosa.py <komenda> [...]
cd plugins/prawo-pl-uodo/skills/prawo-pl-uodo && python3 scripts/uodo.py <komenda> [...]
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

### edzienniki.py (prawo miejscowe — dzienniki wojewódzkie)

| Komenda | Opis | Przykład |
|---|---|---|
| `dzienniki` | lista 16 dzienników; z `--woj` roczniki i liczba aktów | `dzienniki --woj DS` |
| `szukaj` | akty województwa po frazie z tytułu (filtr lokalny) | `szukaj --woj DS "plan zagospodarowania" --rok 2026` |
| `akt` | metadane aktu (typ, organ, status, linki PDF/HTML) | `akt DS 2026 3299` |
| `tekst` | treść aktu; `--fragment` wycina okna; `--pdf` zapisuje urzędowy PDF | `tekst DS 2026 3299 --fragment "§ 2"` |

Kody województw = sufiks publishera ELI (`DS`=dolnośląskie, `MZ`=mazowieckie, `SL`=śląskie…);
można też podać nazwę (`--woj lodzkie`). API dzienników ignoruje filtry serwerowe — silnik pobiera
rocznik i filtruje tytuły lokalnie.

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
(orzecznictwo administracyjne: skill **prawo-pl-cbosa** niżej). Każda komenda przyjmuje `--json`.

### cbosa.py (orzecznictwo sądów administracyjnych — NSA/WSA)

| Komenda | Opis | Przykład |
|---|---|---|
| `szukaj` | orzeczenia po frazie/sądzie/sygnaturze/symbolu/sędzim/dacie | `szukaj "odpowiedzialność członków zarządu" --sad NSA --od 2024-01-01` |
| `orzeczenie` | pełne orzeczenie po doc_id (metadane, sentencja, uzasadnienie, powołane przepisy); `--fragment` | `orzeczenie 8889489BE0 --fragment "art. 116"` |
| `sygnatura` | szybkie odszukanie po sygnaturze | `sygnatura II FSK 2870/18` |

`--sad`: `NSA` albo `"WSA <miasto>"` (16 miast). CBOSA nie ma API — silnik czyta publiczne strony
HTML z throttlingiem ≥0,5 s (zmiana układu stron może wymagać aktualizacji). Wielkość strony
wyników: stałe 10 (`--strona N`).

### uodo.py (decyzje Prezesa UODO — RODO)

| Komenda | Opis | Przykład |
|---|---|---|
| `najnowsze` | ostatnio opublikowane dokumenty | `najnowsze --limit 10` |
| `szukaj` | pełnotekstowo (regex) / po tytule / dacie publikacji | `szukaj "biometr" --od 2026-01-01` |
| `decyzja` | pełna treść decyzji po sygnaturze albo URN; `--fragment` | `decyzja DKN.5131.9.2025 --fragment "art. 33"` |

API stosuje jeden warunek filtrujący na zapytanie (fraza ALBO tytuł); zaawansowane filtry:
`--warunek "indeks:operator:wartość"`.

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

> „jak NSA podchodzi do odpowiedzialności członka zarządu za zaległości podatkowe (art. 116 o.p.)?"

```bash
python3 scripts/cbosa.py szukaj "odpowiedzialność członka zarządu" \
        --sad NSA --od 2024-01-01                                       # → lista wyroków NSA z doc_id
python3 scripts/cbosa.py orzeczenie <doc_id> --fragment "art. 116"      # → uzasadnienie wokół przepisu
```

> „czy UODO karał za brak zgłoszenia naruszenia i jak to uzasadnia?"

```bash
python3 scripts/uodo.py szukaj "art. 33"                                # → decyzje z sygnaturami
python3 scripts/uodo.py decyzja <sygnatura> --fragment "art. 33"        # → argumentacja organu
```

## Struktura

```
.claude-plugin/marketplace.json          # marketplace dla Claude Code (pięć pluginów)
.agents/plugins/marketplace.json         # marketplace dla Codex (Claude czyta .claude-plugin/)
plugins/<plugin>/                        # prawo-pl-eli | prawo-pl-edzienniki | prawo-eu-eurlex | prawo-pl-saos | prawo-pl-cbosa | prawo-pl-uodo
├── .claude-plugin/plugin.json           # manifest pluginu — Claude
├── .codex-plugin/plugin.json            # manifest pluginu — Codex ("skills": "./skills/")
└── skills/<plugin>/                      # Agent Skills — WSPÓLNE dla obu narzędzi
    ├── SKILL.md
    ├── scripts/<silnik>.py               # eli.py | edzienniki.py | eurlex.py | saos.py | cbosa.py | uodo.py
    └── references/api.md                 # referencja endpointów źródła
tools/validate.py                        # walidator manifestów wszystkich pluginów (używany w CI)
tools/test_*.py                          # testy jednostkowe silników, offline (używane w CI)
.github/workflows/release.yml            # GitHub Actions: walidacja + testy + ZIP-y release na tagu v*
```

## GitHub Actions (deploy)

- **push / PR** → `tools/validate.py` waliduje manifesty WSZYSTKICH pluginów (Claude + Codex), oba marketplace'y
  i frontmattery `SKILL.md`; `tools/test_*.py` testują silniki (offline, bez sieci).
- **tag `v*`** → build po jednym zipie na plugin + GitHub Release z paczkami
  (instalowalnymi przez `claude --plugin-dir` / `--plugin-url`).

## Wersjonowanie

Wszystkie pluginy są wersjonowane **razem (lockstep)** — jedna wersja (obecnie **1.5.0**) zadeklarowana
we wszystkich miejscach, identyczna; `tools/validate.py` wymusza to w CI:

- `plugins/<plugin>/.claude-plugin/plugin.json` i `.codex-plugin/plugin.json` (pole `version`) — wszystkie pluginy,
- wpisy wszystkich pluginów w obu marketplace'ach (`.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json`),
- frontmattery `SKILL.md` (pole `version`),
- silniki: `eli.py`, `edzienniki.py`, `eurlex.py`, `saos.py`, `cbosa.py`, `uodo.py` (`__version__`; CLI: `--version`).

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
- **SAOS to baza wtórna (agregat orzeczeń jawnych)** — nie zawiera sądów administracyjnych (NSA/WSA →
  prawo-pl-cbosa), świeżość bywa opóźniona, a do dosłownego cytatu zweryfikuj orzeczenie w portalu właściwego sądu.
- **CBOSA nie ma API** — skill prawo-pl-cbosa czyta publiczne strony HTML (scraping, read-only, z throttlingiem);
  zmiana układu stron może chwilowo zepsuć parsowanie. Baza ma charakter informacyjno-edukacyjny (anonimizacja).
- **Portal orzeczeń UODO jest młody (2025)** — starsze decyzje pojawiają się sukcesywnie; brak decyzji
  w portalu ≠ jej nieistnienie. Sprawdzaj status (prawomocna/nieprawomocna).
- Projekt nieoficjalny; korzysta z publicznych API/stron Kancelarii Sejmu, urzędów wojewódzkich (e-dzienniki),
  Urzędu Publikacji UE, SAOS (ICM UW / Fundacja ePaństwo), NSA (CBOSA) i UODO.

## Licencja

MIT — zobacz [`LICENSE`](LICENSE).
