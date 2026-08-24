---
name: prawo-pl-rejestr-umow
version: 2.0.1
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
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
REJ="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-rejestr-umow/scripts/rejestrumow.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$REJ" ] || REJ="<katalog skilla>/scripts/rejestrumow.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$REJ" ] || REJ=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.0.1", "2e68aa08505ed5b99630b661de43a91ebf231323685a182404eb7751c74e96d2"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-pl-rejestr-umow/skills/prawo-pl-rejestr-umow/scripts/rejestrumow.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-pl-rejestr-umow-{WERSJA}", "rejestrumow.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$REJ" ] || exit 1
python3 "$REJ" <komenda> [...]
```

Nie szukaj helpera przez `find` po katalogach użytkownika ani systemu i nie pobieraj go z żadnego innego źródła niż przypięty tag z sumą wyżej (żadnego `main`, żadnej innej wersji z cache) — inna wersja helpera to inne wyniki.

(W przykładach niżej `python3 scripts/rejestrumow.py` oznacza `python3 "$REJ"`, jeśli nie
jesteś w katalogu skilla.)

### Komendy

- **najnowsze** — ostatnio opublikowane umowy:
  `python3 scripts/rejestrumow.py najnowsze --limit 10`
- **szukaj** — wyszukiwarka umów (filtry można łączyć dowolnie):
  `python3 scripts/rejestrumow.py szukaj "remont drogi" --woj dolnośląskie --sort priceDesc`
  `python3 scripts/rejestrumow.py szukaj --jsfp "urząd gminy" --od 2026-07-01 --wartosc-od 100000`
  `python3 scripts/rejestrumow.py szukaj --wykonawca "NAZWA SPÓŁKI"` (albo `--wykonawca-nip`)
  `python3 scripts/rejestrumow.py szukaj --regon 000001301 --rola dowolna` (podmiot po dowolnej stronie)
  `python3 scripts/rejestrumow.py szukaj --zmiana-rodzaj TSU02 --zmiana-od 2026-08-01` (umowy z aneksem)
  Fraza szuka w **przedmiocie umowy**. **Rola podmiotu jest rozłączna** (zweryfikowane:
  Uniwersytet Wrocławski = 455 umów jako zamawiający + 17 jako wykonawca = 472 „dowolnie"):
  `--jsfp/--regon/--nip` = TYLKO zamawiający (JSFP), `--wykonawca/--wykonawca-nip/
  --wykonawca-regon` = TYLKO druga strona (także gdy jest nią inna JSFP), `--rola dowolna`
  = podmiot z `--jsfp/--regon/--nip` po którejkolwiek stronie (`--rola wykonawca` = to samo
  co `--wykonawca*`). Pytanie „ile umów ZAWARŁA jednostka X" → domyślna rola; „wszystkie
  umowy, w których X występuje" → `--rola dowolna`. NIP/REGON podawaj cyframi (kreski silnik
  usuwa; API porównuje dosłownie). `--woj/--powiat/--gmina/--miejscowosc` = adres
  zamawiającego. Zmiany umów: `--zmiana-rodzaj KOD` (kod słownika: `TSU02` aneks, `TSU05`
  rozwiązanie, `TSU06` wypowiedzenie, `TSU07` odstąpienie, `TSU10` wygaśnięcie, `inne`;
  nazwa zamiast kodu → błąd), `--zmiana-od/--zmiana-do` (data zmiany). Daty `RRRR-MM-DD`
  (`--od/--do` = zawarcie, `--pub-od/--pub-do` = publikacja), `--status Aktywna|Nieaktywna`
  (dokładnie tak), `--sort` (m.in. `priceDesc`, `publicationDateDesc`, `executionDateAsc`),
  `--limit N` (maks. 50 — większy jest obcinany z komunikatem), `--strona N` (od 0; silnik
  pisze „Kolejna strona" tylko gdy istnieje, „Ostatnia strona", „poza zakresem" albo „poza
  oknem API"). Zaawansowane: `--zapytanie '<json>'` — surowe body ze wszystkimi sekcjami
  filtrów (`references/api.md`); **API ignoruje nieznane pola po cichu** — zła nazwa pola =
  cały rejestr (364 tys. umów) udający wynik; porównuj total z `najnowsze`.
- **umowa** — pełne szczegóły po idUmowy (UUID z wyników szukania):
  `python3 scripts/rejestrumow.py umowa 958e7d59-057b-4eb4-8f55-e664638f393a`
  Pokazuje numer, status, okres, wartość (liczbą; obok ewentualny „opis wartości" — dowolny
  tekst jednostki, NIE gwarantowana kwota słownie), strony z NIP/REGON i pełnym adresem
  (ulica, kod, miejscowość, gmina/dzielnica, powiat, województwo), finansowanie ze środków
  UE/zagranicznych, aneksy i zmiany, wyłączenia jawności (zakres, podstawa, wyłączający).
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
4. **Okno wyszukiwania to 10 000 wyników** (200 stron po 50) — przy większej liczbie
   trafień (nagłówek „Pasujących umów" pokazuje realną) ogon zbioru jest NIEOSIĄGALNY
   żadną stroną: silnik ostrzega, na ostatniej osiągalnej stronie pisze „okno wyczerpane",
   a `--strona` spoza okna kończy komunikatem „poza oknem API" (exit ≠ 0, także z `--json`).
   Zawężaj zakresami dat/województwem/wartością; `--strict` blokuje zbiór > 10 000 jako
   niekompletny. „Brak wyników" znaczy zweryfikowane zero trafień — strona spoza mniejszego
   zbioru to „poza zakresem (stron: M)", nie zero.
5. **Wartość może być niejawna albo opisowa** — sprawdzaj w szczegółach pola wyłączenia
   jawności (podstawę prawną wyłączenia wskazuje jednostka) i „opis wartości" (wolny tekst;
   bywa kwotą słownie, bywa powtórzeniem przedmiotu, często pusty).
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
1. `szukaj --jsfp "gmina X" --sort priceDesc --limit 10` → największe umowy ZAWARTE przez
   gminę jako zamawiającego (jeśli pusto: znajdź REGON gminy i `szukaj --regon <REGON>`;
   umowy, w których gmina jest wykonawcą dla innej JSFP, pokaże dopiero `--rola dowolna`).
2. `umowa <idUmowy>` → wykonawca (NIP/REGON, adres), opis wartości, aneksy.
3. (opcjonalnie) inne umowy tego wykonawcy w całym sektorze:
   `szukaj --wykonawca-nip <NIP>`; umowy gminy zmienione aneksem: `szukaj --regon <REGON>
   --zmiana-rodzaj TSU02`.
4. (opcjonalnie) podstawa prawna jawności: skill prawo-pl-eli
   (`tekst <u.f.p.> --fragment "art. 34a"`).
5. W odpowiedzi: JSFP + wykonawca + wartość + data zawarcia + idUmowy; dopisz, że rejestr
   obejmuje umowy od 1.07.2026 i dane wpisują jednostki.
