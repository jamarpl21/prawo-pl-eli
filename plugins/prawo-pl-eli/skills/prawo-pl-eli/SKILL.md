---
name: prawo-pl-eli
version: 1.1.0
description: >-
  Odpytuje OFICJALNE API ELI Sejmu (api.sejm.gov.pl/eli) — źródło pierwotne prawa polskiego
  (Dziennik Ustaw, Monitor Polski): wyszukiwanie aktów, pełny tekst i TEKST JEDNOLITY, pojedyncze
  artykuły, metadane, nowelizacje i podstawę prawną, po sygnaturze Dz.U./M.P. Używaj ZAWSZE przy
  TWORZENIU lub ANALIZIE UMÓW i PISM PROCESOWYCH oraz gdy trzeba ustalić lub zacytować treść
  polskiego przepisu (ustawa, rozporządzenie, kodeks), sprawdzić sygnaturę Dz.U., aktualny tekst
  jednolity, czy akt OBOWIĄZUJE albo czy był NOWELIZOWANY — zamiast cytować przepisy z pamięci.
  Wyzwalaj również przy: „co mówi ustawa o…", „art. X ustawy…", „tekst jednolity", „Dz.U. {rok}
  poz. {nr}", „czy ten przepis nadal obowiązuje", weryfikacja podstawy prawnej umowy lub pisma
  procesowego, zgodność klauzuli umownej z przepisami, terminy ustawowe (przedawnienie, zawity).
  Polish primary law (statutes, codes, regulations) from the official Sejm ELI API — use when
  drafting or reviewing contracts and court filings governed by Polish law.
---

# Prawo polskie z oficjalnego API ELI Sejmu

Skill do pracy z prawem polskim przy **tworzeniu i analizie umów oraz pism procesowych** — wszędzie tam,
gdzie trzeba przywołać przepis, zweryfikować podstawę prawną, termin lub procedurę. Cytowanie przepisów
z pamięci jest zawodne (zmiany, nowelizacje, błędne sygnatury) — ten skill sięga do **źródła pierwotnego**:
oficjalnego API ELI Sejmu (`https://api.sejm.gov.pl/eli`), czyli Dziennika Ustaw (DU) i Monitora Polskiego (MP).
Używaj go, zanim podasz brzmienie przepisu, sygnaturę albo stwierdzisz, że coś „obowiązuje".

## Narzędzie

Wszystko robi helper `scripts/eli.py` (tylko biblioteka standardowa Pythona — bez instalacji). Uruchamiaj:

```
python3 scripts/eli.py <komenda> [...]
```

Sygnaturę można podać w wielu formach: `DU 2000 1037`, `DU/2024/18`, `"Dz.U. 2024 poz. 18"`,
`"Dz.U. 1997 nr 78 poz. 483"`, `WDU20240000018`, albo `DU/2000/1037` (ELI).

### Komendy

- **szukaj** — znajdź akt po tytule/typie/roku/haśle:
  `python3 scripts/eli.py szukaj "Kodeks spółek handlowych" --typ Ustawa --limit 5`
  (opcje: `--typ`, `--rok`, `--wyd DU|MP`, `--haslo`, `--obowiazujace`, `--limit`, `--offset`)
- **meta** — metadane aktu (tytuł, typ, status, **wejście w życie**, czy obowiązuje, hasła, ELI, pliki tekstu):
  `python3 scripts/eli.py meta DU 2000 1037`
- **tj** — znajdź AKTUALNY TEKST JEDNOLITY dla aktu (posortowane, najnowszy oznaczony; na starym t.j. ostrzega o nowszym):
  `python3 scripts/eli.py tj DU 2000 1037`
- **tekst** — treść aktu (z `text.html` → czysty tekst). **Do pojedynczego przepisu używaj `--fragment`**
  (wycina tylko jednostki z frazą — pełny kodeks to setki tysięcy znaków):
  `python3 scripts/eli.py tekst DU 2024 18 --fragment "art. 299"` (trafia w nagłówek artykułu, nie w odesłania)
  `python3 scripts/eli.py tekst DU 2024 18 --fragment "przedawnienie"` (wyszukiwanie pełnotekstowe)
  `--pdf ŚCIEŻKA` zapisuje urzędowy PDF (preferuje tekst jednolity). Artykuły z indeksem górnym są
  w tekście sklejone: art. 299¹ → `--fragment "art. 2991"`.
- **struktura** — spis jednostek redakcyjnych (tytuły/działy/rozdziały/artykuły):
  `python3 scripts/eli.py struktura DU 2024 18 --filtr "Art. 299"` (opcje: `--filtr`, `--poziom N`)
- **odniesienia** — powiązania: nowelizacje, podstawa prawna, tekst jednolity, akty wykonawcze:
  `python3 scripts/eli.py odniesienia DU 2024 18`
- każda komenda przyjmuje `--json` (surowa odpowiedź API do dalszego przetwarzania).

Narzędzie samo ostrzega: `tekst` na akcie, który ma tekst jednolity, każe cytować z najnowszego t.j.;
na tekście jednolitym wypisuje „Nowelizacje po tekście jednolitym". Nie ignoruj tych ostrzeżeń.

## Zasady (ważne — dlaczego)

1. **Najpierw znajdź właściwy akt, potem cytuj.** Typowy przepływ: `szukaj` → ustal sygnaturę → `tj`
   (aktualny tekst jednolity) → `tekst <t.j.> --fragment "art. N"`. Cytowanie starej wersji to częsty błąd.
2. **Sprawdź NOWELIZACJE i ich WEJŚCIE W ŻYCIE** (najczęstsze źródło błędu). `meta` pokazuje datę wejścia
   w życie, `odniesienia` — zmiany; „Nowelizacje po tekście jednolitym" oznacza, że nawet t.j. bywa już
   nieaktualny. Zanim powiesz „przepis brzmi…":
   - **Akt OGŁOSZONY ≠ OBOWIĄZUJĄCY.** W nowelizacji sprawdź artykuł „wchodzi w życie" — możliwe vacatio
     legis oraz RÓŻNE daty dla różnych jednostek redakcyjnych — i odnieś do **DATY zdarzenia/sprawy**
     (przepis sprzed/po zmianie). Tekst jednolity oddaje stan na `legalStatusDate`; nowsze zmiany trzeba
     nałożyć ręcznie i sprawdzić, czy już obowiązują.
   - **Baza może NIE mieć najświeższej zmiany.** Brak nowelizacji w API ≠ pewność, że jej nie ma
     (indeksacja bywa opóźniona). Przy sprawie na konkretną datę zweryfikuj dodatkowo (np. najnowsze
     pozycje `dziennikustaw.gov.pl`, proces legislacyjny), a w odpowiedzi zaznacz:
     „stan prawny na dzień X wg ELI — do potwierdzenia".
3. **Do DOSŁOWNEGO cytatu w umowie/piśmie/sądzie** używaj urzędowego PDF (`tekst … --pdf`), bo
   `text.html` po konwersji bywa zlepiony; `--fragment` jest świetny do szybkiego odczytu i analizy.
4. **Zawsze podawaj sygnaturę Dz.U./M.P. i ELI** przy cytacie (np. „art. 299 § 1 k.s.h., Dz.U. 2024
   poz. 18"). To pozwala odbiorcy zweryfikować źródło.
5. Pełna lista endpointów i parametrów: `references/api.md` (czytaj przy zapytaniach spoza powyższych
   komend — np. słowniki typów/haseł, listowanie roczników, akty zmieniające w okresie).

## Czego ten skill NIE obejmuje

- **prawa UE** (rozporządzenia, dyrektywy — użyj EUR-Lex), **dzienników wojewódzkich i resortowych**,
- **orzecznictwa sądów** (SN/NSA/TSUE; w Dz.U. są tylko wyroki TK i to jako pozycje dziennika),
- **projektów ustaw** w toku procesu legislacyjnego (to inne API Sejmu),
- treści umów stron, KRS, ksiąg wieczystych.
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że ELI je pokryje.

## Przykładowy przepływ

Pytanie (np. przy analizie pozwu przeciwko członkowi zarządu sp. z o.o.): „co dokładnie mówi art. 299 § 1
Kodeksu spółek handlowych i czy to aktualne?" (przykład — stan na czerwiec 2026):
1. `szukaj "Kodeks spółek handlowych" --typ Ustawa` → akt bazowy ELI `DU/2000/1037`.
2. `tj DU 2000 1037` → najnowszy tekst jednolity `DU/2024/18` (Dz.U. 2024 poz. 18), oznaczony „AKTUALNY".
3. `tekst DU 2024 18 --fragment "art. 299"` → treść artykułu + automatyczne ostrzeżenie o nowelizacjach
   po t.j. (tu: Dz.U. 2024 poz. 96 — wyrok TK K 29/23). Nowsze zmiany kodeksu (np. Dz.U. 2026 poz. 176)
   mogą nie być jeszcze wpięte pod t.j. — dopytaj `szukaj`iem i odnieś do daty sprawy.
4. W odpowiedzi: zacytuj przepis, podaj sygnaturę i datę stanu prawnego.
