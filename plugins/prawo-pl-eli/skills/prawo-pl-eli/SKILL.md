---
name: prawo-pl-eli
version: 1.0.1
description: >-
  Odpytuje OFICJALNE API ELI Sejmu (api.sejm.gov.pl/eli) — źródło pierwotne prawa polskiego
  (Dziennik Ustaw, Monitor Polski): wyszukiwanie aktów, pełny tekst i TEKST JEDNOLITY, metadane,
  nowelizacje i podstawę prawną, po sygnaturze Dz.U./M.P. Używaj ZAWSZE, gdy trzeba ustalić lub
  zacytować treść polskiego przepisu (ustawa, rozporządzenie, kodeks), sprawdzić sygnaturę Dz.U.,
  aktualny tekst jednolity, czy akt OBOWIĄZUJE albo czy był NOWELIZOWANY — zamiast cytować przepisy
  z pamięci. Wyzwalaj również przy: „co mówi ustawa o…", „art. X ustawy…", „tekst jednolity",
  „Dz.U. {rok} poz. {nr}", „czy ten przepis nadal obowiązuje", weryfikacja podstawy prawnej pisma
  procesowego, sprawdzenie polskiego aktu prawnego.
---

# Prawo polskie z oficjalnego API ELI Sejmu

Cytowanie polskich przepisów z pamięci jest zawodne (zmiany, nowelizacje, błędne sygnatury). Ten skill
sięga do **źródła pierwotnego** — oficjalnego API ELI Sejmu (`https://api.sejm.gov.pl/eli`), które udostępnia
Dziennik Ustaw (DU) i Monitor Polski (MP): treść aktów, teksty jednolite, metadane i powiązania. Używaj go,
zanim podasz brzmienie przepisu, sygnaturę albo stwierdzisz, że coś „obowiązuje".

## Narzędzie

Wszystko robi helper `scripts/eli.py` (tylko biblioteka standardowa Pythona — bez instalacji). Uruchamiaj:

```
python3 scripts/eli.py <komenda> [...]
```

Sygnaturę można podać w wielu formach: `DU 2000 1037`, `DU/2024/18`, `"Dz.U. 2024 poz. 18"`, `WDU20240000018`, albo `DU/2000/1037` (ELI).

### Komendy

- **szukaj** — znajdź akt po tytule/typie/roku:
  `python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa --limit 5`
  (opcje: `--typ`, `--rok`, `--wyd DU|MP`, `--obowiazujace`, `--limit`)
- **meta** — metadane aktu (tytuł, typ, status, czy obowiązuje, hasła, ELI, dostępne pliki tekstu):
  `python3 scripts/eli.py meta DU 2000 1037`
- **tekst** — treść aktu (z `text.html` → czysty tekst); `--pdf ŚCIEŻKA` zapisuje urzędowy PDF (preferuje tekst jednolity):
  `python3 scripts/eli.py tekst DU 2024 18` · `python3 scripts/eli.py tekst DU 2024 18 --pdf /tmp/ksh.pdf`
- **odniesienia** — powiązania: nowelizacje, podstawa prawna, tekst jednolity:
  `python3 scripts/eli.py odniesienia DU 2024 18`
- **tj** — znajdź TEKST JEDNOLITY dla danego aktu (z odniesień):
  `python3 scripts/eli.py tj DU 2000 1037`
- każda komenda przyjmuje `--json` (surowa odpowiedź API do dalszego przetwarzania).

## Zasady (ważne — dlaczego)

1. **Najpierw znajdź właściwy akt, potem cytuj.** Typowy przepływ: `szukaj` → ustal sygnaturę → `tj` (czy jest nowszy tekst jednolity) → `tekst` na tekście jednolitym. Cytowanie starej wersji to częsty błąd.
2. **Sprawdź NOWELIZACJE i ich WEJŚCIE W ŻYCIE** (najczęstsze źródło błędu). `meta`/`odniesienia` pokazują zmiany; „Nowelizacje po tekście jednolitym" oznacza, że nawet tekst jednolity bywa już nieaktualny (przykład: k.s.h. — t.j. Dz.U. 2024 poz. 18, a pod nim „Nowelizacje po tekście jednolitym" wskazują Dz.U. 2024 poz. 96 — wyrok TK K 29/23; sam kodeks bywa dalej nowelizowany, np. Dz.U. 2026 poz. 176, czego API może jeszcze nie pokazywać w odniesieniach do t.j.). Zanim powiesz „przepis brzmi…":
   - **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** Sprawdź w nowelizacji artykuł „wchodzi w życie" — możliwe vacatio legis oraz RÓŻNE daty dla różnych jednostek redakcyjnych — i odnieś do **DATY zdarzenia/sprawy**, której dotyczy zagadnienie (przepis sprzed/po zmianie). Tekst jednolity oddaje stan na `legalStatusDate`; nowsze zmiany trzeba nałożyć ręcznie i sprawdzić, czy już obowiązują.
   - **Baza może NIE mieć najświeższej zmiany.** Brak nowelizacji w API ≠ pewność, że jej nie ma (indeksacja bywa opóźniona). Przy sprawie na konkretną datę zweryfikuj dodatkowo (np. najnowsze pozycje `dziennikustaw.gov.pl`, proces legislacyjny), a w odpowiedzi zaznacz: „stan prawny na dzień X wg ELI — do potwierdzenia".
3. **Do DOSŁOWNEGO cytatu na potrzeby pisma/sądu** używaj urzędowego PDF (`tekst … --pdf`), bo `text.html` po konwersji bywa zlepiony; HTML jest świetny do szybkiego odczytu i lokalizacji jednostki redakcyjnej.
4. **Zawsze podawaj sygnaturę Dz.U./M.P. i ELI** przy cytacie (np. „art. 299 § 1 k.s.h., Dz.U. 2024 poz. 18"). To pozwala odbiorcy zweryfikować źródło.
5. Pełna lista endpointów i parametrów: `references/api.md` (czytaj, gdy potrzebujesz zapytania spoza powyższych komend — np. struktura aktu, słowniki typów/haseł, listowanie roczników).

## Przykładowy przepływ

Pytanie: „co dokładnie mówi art. 299 § 1 Kodeksu spółek handlowych (odpowiedzialność członków zarządu sp. z o.o. za zobowiązania spółki) i czy to aktualne?"
1. `szukaj "Kodeks spółek handlowych" --typ Ustawa` → akt bazowy ELI `DU/2000/1037` (akt posiada tekst jednolity).
2. `tj DU 2000 1037` → najnowszy tekst jednolity `DU/2024/18` (Dz.U. 2024 poz. 18).
3. `odniesienia DU 2024 18` → sprawdź „Nowelizacje po tekście jednolitym" (tu: Dz.U. 2024 poz. 96 — wyrok TK K 29/23). Nowsze zmiany kodeksu (np. Dz.U. 2026 poz. 176) mogą nie być jeszcze wpięte pod t.j. — dopytaj `szukaj`iem i odnieś do daty sprawy.
4. `tekst DU 2024 18` (lub `--pdf`) → odczytaj/zacytuj art. 299 § 1; podaj sygnaturę i zaznacz datę stanu prawnego.
