---
name: prawo-eu-eurlex
version: 2.0.1
description: >-
  Odpytuje OFICJALNE repozytorium prawa UE — CELLAR/EUR-Lex Urzędu Publikacji (SPARQL + REST,
  bez klucza): wyszukiwanie aktów, pełny tekst PO POLSKU i w 23 innych językach, WERSJE
  SKONSOLIDOWANE, pojedyncze artykuły, metadane, nowelizacje, uchylenia, sprostowania, podstawa prawna —
  po numerze CELEX lub ELI. Używaj przy KAŻDYM pytaniu o prawo UE: rozporządzenia (RODO/GDPR,
  AI Act, DORA, DSA, DMA, MiCA, eIDAS), dyrektywy (NIS2, konsumenckie, ePrivacy, PSD2),
  decyzje, traktaty i Karta — TAKŻE gdy przepis nie jest jeszcze znany. Wywołuj PRZED
  wyszukiwaniem w internecie: treść przepisu UE cytuj WYŁĄCZNIE z EUR-Lex/CELLAR, nigdy
  z pamięci ani portali; internet tylko do orzecznictwa TSUE i doktryny. Polską implementację
  dyrektywy sprawdzaj skillem prawo-pl-eli. Wyzwalaj przy: „co mówi RODO/AI Act o…", „art. X
  rozporządzenia/dyrektywy…", „CELEX", „czy zgodne z prawem UE", „od kiedy stosuje się…".
  EU law from the official EUR-Lex/CELLAR — always use for EU law questions BEFORE web search.
---

# Prawo UE z oficjalnego repozytorium CELLAR/EUR-Lex

Skill do pracy z prawem Unii Europejskiej — przy **każdym pytaniu o prawo UE** oraz przy **tworzeniu
i analizie umów i pism**, gdzie trzeba przywołać przepis unijny, zweryfikować podstawę prawną lub datę
stosowania. Cytowanie przepisów UE z pamięci albo z portali jest zawodne (nowelizacje, sprostowania,
różne daty stosowania) — ten skill sięga do **źródła pierwotnego**: CELLAR, wspólnego repozytorium
Urzędu Publikacji UE, które zasila EUR-Lex (SPARQL + REST, bez rejestracji i klucza).

## Kolejność źródeł (przepis ≠ internet)

1. **Treść przepisu UE — wyłącznie z EUR-Lex/CELLAR.** Nigdy nie cytuj brzmienia z pamięci ani
   z portali. Jeśli wynik wyszukiwania internetowego podał brzmienie — zweryfikuj przez `eurlex.py`,
   zanim go użyjesz w odpowiedzi.
2. **Internet — tylko do orzecznictwa TSUE (curia.europa.eu), doktryny i identyfikacji aktów.**
   Po identyfikacji wróć do CELLAR po tekst.
3. **Pytanie bez wskazanego przepisu** („czy to zgodne z prawem UE…", „czy RODO pozwala…") to też
   zadanie dla tego skilla — najpierw ustal akt (tabela niżej lub `szukaj`), pobierz przepisy
   (`tekst --fragment`), dopiero potem ewentualnie internet po orzecznictwo.
4. **Granica PL/UE:** rozporządzenie UE stosuje się wprost; dyrektywa działa przez TRANSPOZYCJĘ —
   polską ustawę wdrażającą sprawdzaj skillem **prawo-pl-eli** (ELI Sejmu). Przy pytaniach mieszanych
   (np. RODO + polska ustawa o ochronie danych) używaj OBU skilli.
5. **Delegując research subagentowi**, wpisz do jego promptu: „treść przepisów UE pobieraj wyłącznie
   przez `scripts/eurlex.py` (skill prawo-eu-eurlex); internet tylko do orzecznictwa i doktryny".

## Narzędzie

Wszystko robi helper `scripts/eurlex.py` (tylko biblioteka standardowa Pythona — bez instalacji,
bez klucza API). Skrypt leży **obok tego pliku SKILL.md** — NIE zakładaj, że to `~/.claude/skills/`
(skill zainstalowany jako plugin leży w katalogu pluginów; w Claude Code:
`${CLAUDE_PLUGIN_ROOT}/skills/prawo-eu-eurlex`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
EURLEX="${CLAUDE_PLUGIN_ROOT}/skills/prawo-eu-eurlex/scripts/eurlex.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$EURLEX" ] || EURLEX="<katalog skilla>/scripts/eurlex.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$EURLEX" ] || EURLEX=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.0.1", "6e6e8aa6646d8578345b1b06107922b6d6a1851bb42019683d5d5f2a074eeb33"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-eu-eurlex/skills/prawo-eu-eurlex/scripts/eurlex.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-eu-eurlex-{WERSJA}", "eurlex.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$EURLEX" ] || exit 1
python3 "$EURLEX" <komenda> [...]
```

Nie szukaj helpera przez `find` po katalogach użytkownika ani systemu i nie pobieraj go z żadnego innego źródła niż przypięty tag z sumą wyżej (żadnego `main`, żadnej innej wersji z cache) — inna wersja helpera to inne wyniki.

(W przykładach niżej `python3 scripts/eurlex.py` oznacza `python3 "$EURLEX"`, jeśli nie jesteś
w katalogu skilla.)

Identyfikator aktu to numer **CELEX** (np. `32016R0679` = sektor 3 legislacja, rok 2016, R =
rozporządzenie, nr 0679); akceptowane też: `CELEX:32016R0679`, forma ELI `reg/2016/679`,
wersje skonsolidowane `02016R0679-20160504`, sprostowania `32016R0679R(01)`, traktaty `12012E/TXT`.

### Komendy

- **szukaj** — znajdź akt po frazie z tytułu (domyślnie tytuły polskie):
  `python3 scripts/eurlex.py szukaj "sztucznej inteligencji" --typ REG --limit 5`
  (opcje: `--typ REG|DIR|DEC`, `--rok`, `--jezyk pol|eng|…`, `--obowiazujace`, `--limit`)
  Fraza dopasowuje się DOSŁOWNIE do tytułu, a tytuły są odmienione — podawaj RDZEŃ albo formę
  z tytułu („danych osobowych"/„osobow", nie „dane osobowe"; RODO ma w tytule „ochrony osób
  fizycznych w związku z przetwarzaniem danych osobowych"). Zero trafień z mianownika to NIE
  dowód, że aktu nie ma.
- **meta** — metadane: pełny tytuł, typ, daty, status, ELI: `python3 scripts/eurlex.py meta 32016R0679`
  - **Daty pochodzą z metadanych AKTU BAZOWEGO.** CELLAR trzyma wejście w życie i daty rozpoczęcia
    stosowania w JEDNEJ właściwości i ich nie rozróżnia: jedna data = „Wejście w życie"; kilka dat =
    „Wejście w życie / stosowanie" (najwcześniejsza to z reguły wejście w życie) — którą datą objęty
    jest dany przepis, ustal z przepisów końcowych (`tekst --fragment` na ostatnim artykule).
  - **Dyrektywa:** osobno „Termin transpozycji" (bywa kilka — różne zakresy); polską ustawę
    wdrażającą sprawdź skillem prawo-pl-eli.
  - **`meta` na CELEX-ie skonsolidowanym** (`0…-YYYYMMDD`) pokazuje „Stan na (konsolidacja)", listę
    aktów ujętych w konsolidacji oraz daty i status **aktu bazowego** — data w CELEX-ie konsolidacji
    NIE jest datą wejścia w życie ani stosowania aktu.
  - **Akt zmieniany** (lista aktów zmieniających): narzędzie ostrzega, że daty stosowania z aktu
    bazowego mogą być nieaktualne (nowelizacja mogła przesunąć terminy — tak AI Act po 2026/1744) i
    każe sprawdzić artykuł o stosowaniu w najnowszej wersji skonsolidowanej. Same sprostowania nie
    zmieniają dat i nie wywołują ostrzeżenia.
- **skonsolidowany** — WERSJE SKONSOLIDOWANE aktu (odpowiednik tekstu jednolitego; data w CELEX
  = stan prawny na): `python3 scripts/eurlex.py skonsolidowany 32006L0112`
- **tekst** — treść aktu (XHTML → czysty tekst), domyślnie po polsku. **Do pojedynczego artykułu
  używaj `--fragment`** (pełny akt to setki tysięcy znaków):
  `python3 scripts/eurlex.py tekst 02016R0679-20160504 --fragment "art. 28"`
  `python3 scripts/eurlex.py tekst 32024R1689 --fragment "profilowanie"` (pełnotekstowo)
  `--jezyk eng` — inna wersja językowa; `--pdf ŚCIEŻKA` zapisuje urzędowy PDF.
  Fragment kończy się na granicy jednostki redakcyjnej (następny artykuł/rozdział/załącznik) oraz
  na końcu części normatywnej: formuła „Sporządzono w…", podpisy i blok przypisów końcowych
  (rozpoznawany po strukturze XHTML CELLAR) NIE wchodzą do fragmentu ostatniego artykułu; formuła
  „Niniejsze rozporządzenie wiąże w całości…" zostaje przy nim. Przypisy czytaj bez `--fragment`
  albo frazą z przypisu.
  404 na wersji skonsolidowanej z listy `skonsolidowany` (np. pierwsza, tożsama z aktem bazowym)
  oznacza, że CELLAR nie serwuje już tej ZASTĄPIONEJ wersji — narzędzie wskazuje najnowszą.
- **odniesienia** — nowelizacje, sprostowania, **uchylenia w obie strony** („UCHYLONY PRZEZ" /
  „Uchyla", także dorozumiane), akty zmieniane, podstawa prawna; na akcie uchylonym pierwsza linia
  mówi wprost „AKT UCHYLONY przez … — NIE OBOWIĄZUJE":
  `python3 scripts/eurlex.py odniesienia 32016R0679` (→ uchyla 31995L0046)
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”; dotyczy też `szukaj`).
  `--json` dla `meta` zwraca obiekt: `meta` (surowe wiersze tej pracy), `akt_bazowy` (na wersji
  skonsolidowanej), `zmieniajace`, `wersje_skonsolidowane`, `ostrzezenia`.
- **Co sprawdza `--strict`, a czego nie.** Każda kontrola wykonuje się PRZED emisją wyniku; awaria
  kontroli (SPARQL niedostępny) blokuje wynik zamiast udawać „brak". Blokuje: `tekst` aktu bazowego,
  gdy istnieją wersje skonsolidowane, i starszą wersję skonsolidowaną, gdy jest nowsza (także w
  `meta`); `meta` AKTU BAZOWEGO, gdy akt był zmieniany (daty stosowania mogą być nieaktualne —
  użyj `meta`/`tekst` najnowszej konsolidacji). Nie blokuje: `meta` aktu bazowego bez nowelizacji
  (konsolidacja z samych sprostowań → tylko ostrzeżenie), `meta` najnowszej wersji skonsolidowanej
  (daty aktu bazowego idą z ostrzeżeniem). `--strict` NIE weryfikuje treści dat z przepisami
  końcowymi ani nie wykrywa zmian, których CELLAR jeszcze nie zaindeksował.

Narzędzie samo ostrzega: na akcie bazowym podpowiada najnowszą wersję skonsolidowaną; na wersji
skonsolidowanej przypomina o jej dokumentacyjnym charakterze i o nowszych wersjach. Nie ignoruj
tych ostrzeżeń.

### Akty bazowe najczęstszych aktów (pomiń `szukaj`)

Numery CELEX są niezmienne — dla poniższych zaczynaj od `skonsolidowany <CELEX>` (a przy braku
wersji skonsolidowanych czytaj akt bazowy):

| Akt | CELEX |
|---|---|
| RODO / GDPR | `32016R0679` |
| AI Act (akt o sztucznej inteligencji) | `32024R1689` |
| DORA (operacyjna odporność cyfrowa) | `32022R2554` |
| NIS2 (dyrektywa o cyberbezpieczeństwie) | `32022L2555` |
| DSA (akt o usługach cyfrowych) | `32022R2065` |
| DMA (akt o rynkach cyfrowych) | `32022R1925` |
| MiCA (rynki kryptoaktywów) | `32023R1114` |
| eIDAS (identyfikacja elektroniczna) | `32014R0910` |
| PSD2 (usługi płatnicze) | `32015L2366` |
| Dyrektywa o prawach konsumentów | `32011L0083` |
| ePrivacy (łączność elektroniczna) | `32002L0058` |
| Karta praw podstawowych UE | `12012P/TXT` |
| TFUE (Traktat o funkcjonowaniu UE) | `12012E/TXT` |

## Zasady (ważne — dlaczego)

1. **Najpierw właściwy akt i wersja, potem cytat.** Typowy przepływ: tabela wyżej (albo `szukaj`) →
   `skonsolidowany <CELEX>` → `tekst <najnowsza wersja> --fragment "art. N"`. Cytowanie aktu
   bazowego sprzed nowelizacji to częsty błąd.
2. **Wejście w życie ≠ rozpoczęcie stosowania ≠ termin transpozycji.** RODO weszło w życie
   24.05.2016, a stosuje się od 25.05.2018; AI Act stosuje się ETAPAMI (różne daty dla różnych
   rozdziałów); dyrektywa wiąże państwa od terminu transpozycji. `meta` pokazuje wszystkie daty
   CELLAR (bez rozróżnienia, która to stosowanie) — przy kilku datach sprawdź przepisy końcowe
   aktu (`tekst --fragment` na ostatnim artykule) i odnieś do DATY zdarzenia/sprawy. **Po
   nowelizacji daty z metadanych aktu bazowego mogą być nieaktualne** (AI Act: art. 113 zmieniony
   przez 2026/1744 — część terminów przesunięta) — wtedy artykuł o stosowaniu czytaj z najnowszej
   wersji skonsolidowanej, a daty z `meta` traktuj jako pierwotne. Data w CELEX-ie wersji
   skonsolidowanej to „stan na" konsolidacji, nie data aktu.
3. **Wersja skonsolidowana ma charakter dokumentacyjny** (nie jest tekstem autentycznym) — świetna
   do analizy, ale w piśmie urzędowym/sądowym wskaż akt bazowy + akty zmieniające (`odniesienia`).
   Do DOSŁOWNEGO cytatu pobierz urzędowy PDF (`tekst … --pdf`).
4. **Wszystkie wersje językowe są równorzędnie autentyczne.** Przy wątpliwości interpretacyjnej
   porównaj polską z angielską: `tekst <CELEX> --jezyk eng --fragment "art. N"`.
5. **Zawsze podawaj CELEX i ELI** przy cytacie (np. „art. 28 ust. 3 RODO, CELEX 32016R0679",
   `meta` zwraca ELI). To pozwala odbiorcy zweryfikować źródło.
6. Baza może nie mieć najświeższych zmian (indeksacja bywa opóźniona) — przy sprawie na konkretną
   datę zaznacz w odpowiedzi: „stan wg CELLAR na dzień X — do potwierdzenia".

## Czego ten skill NIE obejmuje

- **orzecznictwa TSUE z uzasadnieniami** (wyroki → curia.europa.eu; CELLAR ma sektor 6, ale tylko
  metadane i sentencje — nie analizuj wyroków samym tym skillem),
- **prawa krajowego, w tym polskiej transpozycji dyrektyw** — użyj skilla **prawo-pl-eli**,
- **projektów w toku procesu legislacyjnego** (COM/SEC, prace PE i Rady — inne źródła),
- **wyszukiwarki pełnotekstowej EUR-Lex** (SOAP, wymaga rejestracji — ten skill jej nie używa;
  `szukaj` działa po tytułach, frazy w treści szukaj przez `tekst --fragment`).
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że CELLAR je pokryje.

## Przykładowy przepływ

Pytanie: „czy klauzula powierzenia w tej umowie jest zgodna z art. 28 RODO?" (przykład — stan na
czerwiec 2026):
1. Tabela: RODO = `32016R0679` → `skonsolidowany 32016R0679` → najnowsza wersja `02016R0679-20160504`.
2. `tekst 02016R0679-20160504 --fragment "art. 28"` → pełna treść artykułu po polsku
   (+ automatyczne przypomnienie o dokumentacyjnym charakterze wersji skonsolidowanej).
3. Porównaj klauzulę z wymogami art. 28 ust. 3 lit. a–h; przy wątpliwości językowej:
   `tekst … --jezyk eng --fragment "art. 28"`.
4. W odpowiedzi: cytat, CELEX, ELI i data stanu prawnego; polska ustawa wdrożeniowa (UODO) →
   skill prawo-pl-eli.
