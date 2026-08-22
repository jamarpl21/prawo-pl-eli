---
name: prawo-pl-rejestr-umow
version: 1.6.6
description: >-
  Odpytuje publiczne API Centralnego Rejestru Umów JSFP (rejestrumow.gov.pl) — jawny
  rejestr umów zawieranych od 1.07.2026 przez jednostki sektora finansów publicznych:
  urzędy, gminy, szpitale, uczelnie, sądy. Używaj przy pytaniach „jakie umowy zawarł
  urząd/gmina/szpital X", „z kim i na ile ma umowę…", „kto dostał zlecenie od…", „ile
  jednostka płaci za…", przy due diligence kontrahenta z sektora publicznego, analizie
  wydatków publicznych i kontroli obywatelskiej. Wyszukiwanie po przedmiocie umowy,
  nazwie/REGON/NIP zamawiającego i wykonawcy, województwie, dacie i wartości; pełne
  szczegóły umowy (strony z adresami, aneksy, wyłączenia jawności). Read-only, bez
  klucza. Podstawa prawna (art. 34a–34b ustawy o finansach publicznych) → prawo-pl-eli;
  ogłoszenia o zamówieniach publicznych (BZP/TED) poza zakresem. Public contracts
  register of Polish public finance sector entities (JSFP) from its official API.
---

# Umowy jednostek sektora finansów publicznych z Centralnego Rejestru Umów

Skill do **jawnego rejestru umów sektora publicznego**. Sięga do publicznego API
**Centralnego Rejestru Umów JSFP** (`https://rejestrumow.gov.pl`, uruchomionego
1.07.2026 na podstawie art. 34a ustawy o finansach publicznych): umowy zawierane przez
jednostki sektora finansów publicznych — urzędy, gminy, powiaty, szpitale, uczelnie,
sądy, instytucje kultury — z pełnymi danymi stron (nazwa, NIP/REGON, adres), przedmiotem,
wartością, okresem obowiązywania i zmianami (aneksy, rozwiązania, wyłączenia jawności).
Używaj go, gdy pytanie dotyczy tego, **z kim i na co jednostka publiczna zawarła umowę**.

## Podział ról (który skill do czego)

1. **Umowy JSFP (kto, z kim, na co, za ile) → ten skill.** Zamawiający i wykonawcy,
   kwoty, aneksy, wyłączenia jawności.
2. **Treść przepisów → prawo-pl-eli** (ustawa o finansach publicznych — art. 34a–34b,
   Prawo zamówień publicznych, u.d.i.p.).
3. **Orzecznictwo** o jawności umów / dostępie do informacji publicznej →
   **prawo-pl-cbosa** (sądy administracyjne), **prawo-pl-saos** (SN, sądy powszechne).
4. **Delegując research subagentowi**, wpisz: „umowy JSFP pobieraj przez
   `scripts/rejestrumow.py` (skill prawo-pl-rejestr-umow); treść przepisów przez
   `scripts/eli.py` (prawo-pl-eli)".

## Narzędzie

Wszystko robi helper `scripts/rejestrumow.py` (tylko biblioteka standardowa Pythona — bez
instalacji). Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/rejestrumow.py`)
— NIE zakładaj, że to `~/.claude/skills/prawo-pl-rejestr-umow` (skill zainstalowany jako plugin
leży w katalogu pluginów; w Claude Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-rejestr-umow`).
Uruchamiaj wyłącznie helper z bieżącego pakietu:

```
# Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
REJ="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-rejestr-umow/scripts/rejestrumow.py"
# Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
# jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$REJ" ] || REJ="<katalog skilla>/scripts/rejestrumow.py"
[ -f "$REJ" ] || { echo "BŁĄD: brak helpera obok SKILL.md: $REJ" >&2; exit 1; }
python3 "$REJ" <komenda> [...]
```

Nie pobieraj helpera z sieci i nie szukaj go przez `find` po katalogach użytkownika ani systemu.

(W przykładach niżej `python3 scripts/rejestrumow.py` oznacza `python3 "$REJ"`, jeśli nie
jesteś w katalogu skilla.)

### Komendy

- **najnowsze** — ostatnio opublikowane umowy:
  `python3 scripts/rejestrumow.py najnowsze --limit 10`
- **szukaj** — wyszukiwarka umów (filtry można łączyć dowolnie):
  `python3 scripts/rejestrumow.py szukaj "remont drogi" --woj dolnośląskie --sort priceDesc`
  `python3 scripts/rejestrumow.py szukaj --jsfp "urząd gminy" --od 2026-07-01 --wartosc-od 100000`
  `python3 scripts/rejestrumow.py szukaj --wykonawca "NAZWA SPÓŁKI"` (albo `--wykonawca-nip`)
  Fraza szuka w **przedmiocie umowy**; `--jsfp/--regon/--nip` dotyczą zamawiającego,
  `--wykonawca*` drugiej strony. Daty `RRRR-MM-DD` (`--od/--do` = zawarcie,
  `--pub-od/--pub-do` = publikacja), `--status Aktywna|Nieaktywna` (dokładnie tak),
  `--sort` (m.in. `priceDesc`, `publicationDateDesc`, `executionDateAsc`), `--limit N`
  (maks. 50), `--strona N`. Zaawansowane: `--zapytanie '<json>'` — surowe body z pełnym
  dostępem do wszystkich sekcji filtrów (`references/api.md`).
- **umowa** — pełne szczegóły po idUmowy (UUID z wyników szukania):
  `python3 scripts/rejestrumow.py umowa 958e7d59-057b-4eb4-8f55-e664638f393a`
  Pokazuje numer, status, okres, wartość (liczbą i słownie), strony z NIP/REGON
  i adresami, finansowanie ze środków UE/zagranicznych, aneksy i zmiany, wyłączenia jawności.
- **slownik** — słowniki API: `python3 scripts/rejestrumow.py slownik rodzaje_zmian_umowy`
  (`kraje`, `strony_umowy`, `rodzaje_zmian_umowy`, `podstawy_wylaczenia_jawnosci`,
  `zakres_wylaczenia_jawnosci`).
- każda komenda przyjmuje `--json` oraz `--strict` (blokuje wynik bez zweryfikowanej kompletności); obie flagi działają przed komendą i po niej.
  Zero trafień / nierozpoznana odpowiedź API kończą się komunikatem i kodem wyjścia ≠ 0 — także z `--json`
  (nie dostaniesz pustego JSON-a, który wyglądałby jak „sprawdzone, nic nie ma”).

Typowy przepływ: `szukaj <filtry>` → wybierz idUmowy → `umowa <idUmowy>` → strony,
kwoty, aneksy do odpowiedzi.

## Zasady (ważne — dlaczego)

1. **Rejestr obejmuje TYLKO umowy zawarte od 1.07.2026** (start obowiązku z art. 34a
   u.f.p.). Brak umowy w rejestrze ≠ jej nieistnienie: wcześniejsze umowy, umowy poniżej
   ustawowego progu wartości i wyłączone ustawowo nie trafiają do rejestru — zaznacz to
   w odpowiedzi. Aktualny próg i wyłączenia weryfikuj w art. 34a u.f.p. (prawo-pl-eli).
2. **Dane wpisują kierownicy JSFP ręcznie** — nazwy bywają skracane, z literówkami,
   a daty przekłamane (system blokuje daty wsteczne, co jednostki obchodzą aneksami —
   bywa to opisane w komentarzu zmiany). Szukając podmiotu, preferuj **REGON/NIP** nad
   nazwę; przy nazwie próbuj krótkich rdzeni.
3. **Zawsze podawaj idUmowy + JSFP + datę zawarcia** przy cytowaniu (np. „umowa
   NI.2720.53.2026 Dolnośląskiej Służby Dróg i Kolei z 7.07.2026, idUmowy 958e7d59-…") —
   UUID jest stabilnym identyfikatorem, a link `https://rejestrumow.gov.pl/umowa/<idUmowy>`
   działa w przeglądarce.
4. **Okno wyszukiwania to 10 000 wyników** — przy większej liczbie trafień
   (`totalMatchingElements` pokazuje realną) zawężaj zakresami dat/wartości; silnik
   sam o tym przypomina.
5. **Wartość może być niejawna albo opisowa** — sprawdzaj w szczegółach pola wyłączenia
   jawności (podstawę prawną wyłączenia wskazuje jednostka) i „opis wartości umowy".
6. Pełna lista endpointów, sekcji filtrów i pułapek: `references/api.md`.

## Czego ten skill NIE obejmuje

- **ogłoszeń o zamówieniach publicznych** (przetargi, SWZ, oferty → BZP `ezamowienia.gov.pl`
  / TED — internet); rejestr pokazuje ZAWARTE umowy, nie postępowania,
- **umów sprzed 1.07.2026** oraz umów podmiotów spoza sektora finansów publicznych
  (spółki komunalne/Skarbu Państwa nie są JSFP),
- **treści przepisów** (→ **prawo-pl-eli**) ani **orzecznictwa** o jawności umów
  (→ **prawo-pl-cbosa**, **prawo-pl-saos**),
- **treści samych umów** (PDF-ów) — rejestr zawiera metadane, nie skany; o treść
  wystąp do jednostki w trybie u.d.i.p.
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że rejestr je pokryje.

## Przykładowy przepływ

Pytanie: „na co gmina X wydaje ostatnio najwięcej i kto jest wykonawcą?"
1. `szukaj --jsfp "gmina X" --sort priceDesc --limit 10` → największe umowy z idUmowy
   (jeśli pusto: znajdź REGON gminy i `szukaj --regon <REGON>`).
2. `umowa <idUmowy>` → wykonawca (NIP/REGON, adres), wartość słownie, aneksy.
3. (opcjonalnie) inne umowy tego wykonawcy w całym sektorze:
   `szukaj --wykonawca-nip <NIP>`.
4. (opcjonalnie) podstawa prawna jawności: skill prawo-pl-eli
   (`tekst <u.f.p.> --fragment "art. 34a"`).
5. W odpowiedzi: JSFP + wykonawca + wartość + data zawarcia + idUmowy; dopisz, że rejestr
   obejmuje umowy od 1.07.2026 i dane wpisują jednostki.
