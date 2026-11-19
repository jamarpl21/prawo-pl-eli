---
name: prawo-pl-eureka
version: 2.6.0
description: >-
  Odpytuje publiczne API systemu EUREKA Ministerstwa Finansów (eureka.mf.gov.pl) — bazę
  INTERPRETACJI PODATKOWYCH: interpretacje indywidualne Dyrektora KIS i ogólne Ministra Finansów
  (z ich zmianami), interpretacje JST, objaśnienia podatkowe, WIS, WIA, pisma i wytyczne MF.
  Używaj przy KAŻDYM pytaniu o praktykę organów podatkowych: „czy jest interpretacja w sprawie…",
  „jak KIS interpretuje art. X ustawy o VAT/PIT/CIT", „interpretacja indywidualna sygn. …",
  „objaśnienia podatkowe do…", przy wnioskach o interpretację, opiniach podatkowych i pismach
  do organów. Wyszukiwanie pełnotekstowe, po kategorii, dacie wydania i sygnaturze; pełna treść
  po ID lub sygnaturze ze STANEM PRAWNYM (uchylona wyrokiem / zmieniona / wygaszona). Read-only,
  bez klucza. Treść przepisów → prawo-pl-eli; wyroki WSA/NSA ze skarg na interpretacje →
  prawo-pl-cbosa. Polish tax rulings (EUREKA, Ministry of Finance).
---

# Interpretacje podatkowe z bazy EUREKA (Ministerstwo Finansów)

Skill do **praktyki interpretacyjnej organów podatkowych**. Sięga do publicznego API systemu
**EUREKA** (`https://eureka.mf.gov.pl`). Ma ponad 527 tys. interpretacji indywidualnych i ogólnych
(odczyt 2026-11-17): interpretacje indywidualne od 2007 r. (Dyrektor KIS, wcześniej dyrektorzy izb skarbowych),
interpretacje ogólne i ich zmiany, zmiany interpretacji indywidualnych przez Szefa KAS,
interpretacje JST, objaśnienia podatkowe, WIS, WIA, pisma i wytyczne MF.

## Podział ról (który skill do czego)

1. **Interpretacje, objaśnienia, WIS/WIA → ten skill.** Z pełną treścią i stanem prawnym.
2. **Treść przepisu podatkowego → prawo-pl-eli** (ustawy o VAT, PIT, CIT, Ordynacja podatkowa).
   Interpretacja cytuje przepis w brzmieniu z dnia wydania — aktualne brzmienie sprawdź w ELI.
3. **Wyroki WSA/NSA ze skarg na interpretacje → prawo-pl-cbosa.** EUREKA opisuje uchylenie
   w komentarzu (sygnatura wyroku), ale nie ma treści wyroku. `tresc` drukuje gotowe
   `cbosa.py sygnatura "<sygnatura wyroku>"`.
4. **Uchwały podatkowe gmin (stawki podatku od nieruchomości, opłaty) → prawo-pl-edzienniki.**
5. **Delegując research subagentowi**, wpisz: „interpretacje podatkowe pobieraj przez
   `scripts/eureka.py` (skill prawo-pl-eureka); treść przepisu przez `scripts/eli.py` (prawo-pl-eli)".

## Narzędzie

Wszystko robi helper `scripts/eureka.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/eureka.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-eureka` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-eureka`). Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
EUREKA="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-eureka/scripts/eureka.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$EUREKA" ] || EUREKA="<katalog skilla>/scripts/eureka.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$EUREKA" ] || EUREKA=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.6.0", "92aa8e596481d4b9071497cd83ee0bbd02b3e2206430453307ed2bf6e478f5ff"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-pl-eureka/skills/prawo-pl-eureka/scripts/eureka.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-pl-eureka-{WERSJA}", "eureka.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj interpretacji z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj interpretacji z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$EUREKA" ] || exit 1
python3 "$EUREKA" <komenda> [...]
```

Nie szukaj helpera przez `find` po katalogach użytkownika ani systemu i nie pobieraj go z żadnego innego źródła niż przypięty tag z sumą wyżej (żadnego `main`, żadnej innej wersji z cache) — inna wersja helpera to inne wyniki.

(W przykładach niżej `python3 scripts/eureka.py` oznacza `python3 "$EUREKA"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **szukaj** — wyszukiwarka EUREKA (Elasticsearch z odmianą polskich słów):
  `python3 scripts/eureka.py szukaj "waluta wirtualna koszty" --limit 10`
  `python3 scripts/eureka.py szukaj "ulga na złe długi" --dokladnie --od 2024-01-01`
  `python3 scripts/eureka.py szukaj "samochód osobowy leasing" --kategoria indywidualna --sort data`
  `python3 scripts/eureka.py szukaj --od 2026-09-01 --do 2026-09-30 --kategoria ogolna,objasnienia`
  Domyślna kategoria `interpretacje` = indywidualne, ogólne, JST i ich zmiany; `--kategoria wszystko`
  zdejmuje filtr; `wis-wszystkie` / `wia-wszystkie` = WIS/WIA razem ze zmianami i uchyleniami;
  aliasy i ID: komenda `kategorie`. Przy zerze wyników z domyślnym filtrem silnik podpowiada
  `--kategoria wszystko` (domyślny filtr pomija objaśnienia, WIS/WIA i pisma MF). Z frazą wyniki są sortowane po trafności,
  bez frazy po dacie (`--sort data|najstarsze|trafnosc`). `--dokladnie` = słowa obok siebie
  (z 13 tys. trafień robi się kilkadziesiąt), `--tylko-tresc` = bez tezy i metadanych.
  `--sygnatura` filtruje po FRAGMENCIE sygnatury (wielkość liter ma znaczenie, np. `KDIT1.4011`).
  `--aktualne` = status EUREKA „Aktualna” — patrz Zasada 1, ten filtr NIE odsiewa uchylonych.
  Na liście `!!!` oznacza informację uchyloną, zmienioną, wygaszoną albo nieaktualną.
  Wyszukiwarka zwraca najwyżej 10 000 pierwszych pozycji — dalej zawężaj datami.
- **sygnatura** — dokument po sygnaturze (gdy nie ma dokładnej, pokazuje trafienia częściowe —
  to INNE dokumenty; z `--json` uwaga idzie na stderr, stdout zostaje czystym JSON-em):
  `python3 scripts/eureka.py sygnatura 0115-KDIT1.4011.700.2026.1.MR`
  Filtr EUREKA rozróżnia wielkość liter, a sygnatury JST mają litery mieszane (`Fn-II-2.310.1.2026.AB`).
  Gdy sygnatura w podanej postaci i wielkimi literami nie daje dokładnego trafienia, silnik pyta
  o fragment bez liter (także wtedy, gdy wcześniejsze zapytanie dało trafienia częściowe) i porównuje
  sygnatury bez rozróżniania wielkości liter. Pole sygnatury bywa listą kilku sygnatur rozdzielonych
  średnikiem — każda jest porównywana osobno. Kod 2 (niezweryfikowane), nie „brak”: gdy trafień jest
  więcej niż 100, a dokładnej sygnatury wśród nich nie ma, oraz gdy sygnatura z literami nie ma
  fragmentu bez liter z co najmniej 4 cyframi (nie da się zapytać niezależnie od wielkości liter).
- **tresc** — metadane, **blok „Stan”** i pełna treść po ID albo sygnaturze:
  `python3 scripts/eureka.py tresc 718641`
  `python3 scripts/eureka.py tresc 0112-KDIL2-2.4011.893.2025.2.AA --fragment "pośrednictw"`
  `python3 scripts/eureka.py tresc 718641 --pdf interpretacja.pdf` (urzędowy eksport PDF)
  Blok „Stan” pokazuje: status EUREKA, komentarze BIP (uchylona wyrokiem WSA/NSA, zmieniona przez
  Szefa KAS, wygaszona, potwierdzona prawomocnym wyrokiem), opis z sygnaturą wyroku, dokument
  zmieniający (`INFORMACJA_ZMIENIAJACA`) i dokumenty, które wskazują ten jako zmieniany (wyszukiwanie
  odwrotne). Informacja podważona zaczyna się od **„!!! INFORMACJA PODWAŻONA”**. Stan ustala z etykiet
  wyszukiwarki ORAZ z ID w samym dokumencie (komentarz BIP spoza neutralnych, status ≠ Aktualna); gdy
  wyszukiwarka nie zwróci metadanych dokumentu, wynik to kod 2 — nigdy treść bez stanu prawnego. `--fragment` wycina
  okna wokół frazy (podawaj RDZEŃ, np. „pośrednictw”). Daty podaje w czasie polskim (API zwraca UTC).
- **kategorie** — lista kategorii informacji z ID i aliasami do `--kategoria`.
- każda komenda przyjmuje `--json` oraz `--strict`; obie flagi działają przed komendą i po niej.
  **Kontrakt `--strict`:** w `tresc` kończy błędem (bez wyniku, także z `--json`) informację
  uchyloną, zmienioną, wygaszoną, ze statusem innym niż „Aktualna”, wskazaną jako zmieniana przez
  inny dokument, bez treści albo bez statusu (ani w wyszukiwarce, ani w dokumencie). „Potwierdzona
  prawomocnym wyrokiem” przechodzi. W `szukaj`,
  `sygnatura` i `kategorie` `--strict` nic nie zmienia (listy to metadane).
  **Kody wyjścia** (także z `--json`, komunikat na stderr): **0** = wynik; **1** = ZWERYFIKOWANY brak
  (zapytanie poprawne, zero trafień albo frazy `--fragment` nie ma w treści); **2** = wszystko inne —
  sieć/HTTP, zła odpowiedź API, zły argument, nieznane ID, blokada `--strict`, pusta dalsza strona,
  wyjście poza okno 10 000. Przy kodzie 2 NIE pisz, że interpretacji nie ma.

Typowy przepływ: `szukaj "<zagadnienie>"` → wybierz ID → `tresc <ID>` → przeczytaj blok „Stan” →
jeśli jest wyrok: `prawo-pl-cbosa sygnatura "<sygnatura wyroku>"`; jeśli jest zmiana: `tresc <ID zmiany>`
→ treść powołanego przepisu z dnia wydania i dziś: `prawo-pl-eli`.

## Zasady (ważne — dlaczego)

1. **Status „Aktualna” ≠ interpretacja się ostała.** EUREKA nie zawsze zmienia status po uchyleniu
   wyrokiem: 0112-KDIL2-2.4011.893.2025.2.AA ma status „Aktualna”, a komentarz „Interpretacja
   uchylona wyrokiem WSA w Poznaniu 22.07.2026 r., sygn. akt I SA/Po 763/26”. Zawsze czytaj blok
   „Stan” z `tresc`, a przed powołaniem się na interpretację w piśmie użyj `--strict`.
2. **Brak wpisu o uchyleniu to nie gwarancja.** MF dopisuje komentarze z opóźnieniem. W ważnej
   sprawie sprawdź w CBOSA, czy na interpretację wniesiono skargę (`prawo-pl-cbosa`, organ:
   Dyrektor KIS, fraza z sygnatury lub tezy).
3. **Rodzaj dokumentu decyduje o ochronie — zaznacz go w odpowiedzi.** Zastosowanie się do
   interpretacji INDYWIDUALNEJ „nie może szkodzić wnioskodawcy” (art. 14k § 1 Ordynacji podatkowej),
   a do interpretacji OGÓLNEJ — „temu, kto się do niej zastosował” (art. 14k § 2). Dla innego podatnika
   interpretacja indywidualna to argument, nie ochrona; dotyczy też stanu prawnego z dnia wydania.
   Zakres ochrony (w tym dla objaśnień podatkowych) cytuj z ELI (`prawo-pl-eli`, Ordynacja podatkowa
   t.j. Dz.U. 2026 poz. 622), nie z pamięci.
4. **Zawsze podawaj sygnaturę, datę wydania i organ** (np. „interpretacja indywidualna Dyrektora KIS
   z 10.11.2026 r., 0115-KDIT1.4011.700.2026.1.MR”) oraz rozstrzygnięcie (stanowisko prawidłowe /
   nieprawidłowe) z treści, a nie z tezy — teza to opis redakcyjny.
5. **Brak w EUREKA ≠ brak interpretacji** (kod 1 to tylko „nic nie pasuje do tego zapytania”). Szukaj
   rdzeniem i synonimem, bez `--dokladnie`, w szerszym zakresie dat.
6. **API nie ma publicznej dokumentacji MF** — to backend strony EUREKA. Przy kodzie 2 z komunikatem
   o nieoczekiwanej odpowiedzi powiedz to wprost, nie zgaduj treści interpretacji.
7. Pełna lista endpointów, filtrów i pułapek: `references/api.md`.

## Czego ten skill NIE obejmuje

- **treści przepisów** (→ **prawo-pl-eli**; prawo UE, np. dyrektywa VAT → **prawo-eu-eurlex**),
- **wyroków WSA/NSA** (→ **prawo-pl-cbosa**), wyroków TSUE (→ curia.europa.eu),
- **uchwał podatkowych gmin** (→ **prawo-pl-edzienniki**),
- interpretacji niepublikowanych, decyzji wymiarowych, wyników kontroli, rozstrzygnięć w sprawach
  konkretnych podatników poza interpretacjami — te nie są publiczne.
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że EUREKA je pokryje.

## Przykładowy przepływ

Pytanie: „czy przy sprzedaży kryptowalut mogę odliczyć koszty z lat ubiegłych?”
1. `szukaj "waluta wirtualna koszty lat ubiegłych" --kategoria indywidualna` → lista z ID.
2. `tresc <ID> --fragment "art. 22 ust. 14"` → stanowisko organu i rozstrzygnięcie; blok „Stan”.
3. Treść art. 22 ust. 14–16 PIT dziś: skill prawo-pl-eli.
4. Jeśli „Stan” pokazuje wyrok: `prawo-pl-cbosa sygnatura "<wyrok>"` (dlaczego sąd uchylił).
5. W odpowiedzi: rodzaj dokumentu + sygnatura + data + organ + rozstrzygnięcie + stan (uchylona?
   zmieniona?) + zastrzeżenie, że zastosowanie się do interpretacji indywidualnej chroni tylko
   wnioskodawcę (art. 14k § 1 o.p.: „nie może szkodzić wnioskodawcy”).
