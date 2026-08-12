---
name: prawo-pl-uodo
version: 1.6.3
description: >-
  Odpytuje OFICJALNE API Portalu Orzeczeń UODO (orzeczenia.uodo.gov.pl) — decyzje Prezesa
  Urzędu Ochrony Danych Osobowych: kary pieniężne za naruszenia RODO, upomnienia, nakazy,
  oraz powiązane orzeczenia sądów. Używaj przy pytaniach o praktykę polskiego organu ochrony
  danych: „czy UODO karał za…", „decyzja UODO w sprawie…", „jak UODO interpretuje art. X RODO",
  „jaka kara za brak analizy ryzyka/zgłoszenia naruszenia", przy DPIA, analizie naruszeń
  ochrony danych, audytach RODO i pismach do UODO. Wyszukiwanie pełnotekstowe i po
  tytule/dacie; pełna treść decyzji po sygnaturze (np. DKN.5131.9.2025). Read-only, bez
  klucza. Treść RODO → skill prawo-eu-eurlex; wyroki WSA/NSA ze skarg na decyzje UODO →
  prawo-pl-cbosa. Decisions of the Polish DPA (UODO) from its official public API.
---

# Decyzje Prezesa UODO z oficjalnego API portalu orzeczeń

Skill do **praktyki decyzyjnej polskiego organu ochrony danych** (Prezesa UODO). Sięga do
oficjalnego API **Portalu Orzeczeń UODO** (`https://orzeczenia.uodo.gov.pl`, uruchomionego
w 2025 r.): decyzje administracyjne Prezesa UODO — kary pieniężne, upomnienia, nakazy — oraz
powiązane orzeczenia sądów w tych sprawach. Używaj go, gdy pytanie dotyczy tego, **jak UODO
stosuje RODO w praktyce**: wysokość kar, przesłanki upomnienia, wymagania wobec analizy ryzyka,
zgłaszania naruszeń, powierzenia przetwarzania.

## Podział ról (który skill do czego)

1. **Decyzje Prezesa UODO → ten skill.** Kary, upomnienia, nakazy, umorzenia — z pełną treścią.
2. **Treść RODO → prawo-eu-eurlex** (rozporządzenie 2016/679, CELEX 32016R0679); polska ustawa
   o ochronie danych osobowych → **prawo-pl-eli** (Dz.U.).
3. **Wyroki WSA/NSA ze skarg na decyzje UODO → prawo-pl-cbosa** (sądową kontrolę decyzji
   znajdziesz w CBOSA; tu bywają tylko powiązane rekordy).
4. **Delegując research subagentowi**, wpisz: „decyzje UODO pobieraj przez `scripts/uodo.py`
   (skill prawo-pl-uodo); treść RODO przez `scripts/eurlex.py` (prawo-eu-eurlex)".

## Narzędzie

Wszystko robi helper `scripts/uodo.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/uodo.py`) — NIE zakładaj, że to
`~/.claude/skills/prawo-pl-uodo` (skill zainstalowany jako plugin leży w katalogu pluginów; w Claude
Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-uodo`). Gdy nie znasz ścieżki, najpierw ją ustal:

```
UODO=$(find "$HOME/.claude" "$HOME/.agents" /mnt /sessions -maxdepth 10 -name uodo.py -path "*prawo-pl-uodo*" 2>/dev/null | head -1)
[ -n "$UODO" ] || { curl -fsSL https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/main/plugins/prawo-pl-uodo/skills/prawo-pl-uodo/scripts/uodo.py -o /tmp/uodo.py && UODO=/tmp/uodo.py; }
python3 "$UODO" <komenda> [...]
```

(W przykładach niżej `python3 scripts/uodo.py` oznacza `python3 "$UODO"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **najnowsze** — ostatnio opublikowane dokumenty:
  `python3 scripts/uodo.py najnowsze --limit 10`
- **szukaj** — wyszukiwanie pełnotekstowe / po tytule / po dacie publikacji:
  `python3 scripts/uodo.py szukaj "biometr" --limit 5`
  `python3 scripts/uodo.py szukaj --tytul "kara pieniężna" --od 2026-01-01`
  Fraza działa jak **regex bez rozróżniania wielkości liter** — szukaj RDZENIA słowa
  („biometr" znajdzie „biometryczne/biometrii"). API stosuje **jeden warunek na zapytanie**
  (fraza ALBO tytuł; zakres dat można łączyć z warunkiem). Zaawansowane: `--warunek
  "indeks:operator:wartość"` (indeksy i operatory: `references/api.md`). `--limit N`, `--strona N`.
- **decyzja** — pełna decyzja po sygnaturze albo URN:
  `python3 scripts/uodo.py decyzja DKN.5131.9.2025`
  Pokazuje metadane (status, daty ogłoszenia/publikacji), przedmiot i pełną treść.
  Do długich decyzji: `--fragment "kara pieniężna"` (wycina okna wokół frazy).
- każda komenda przyjmuje `--json` (surowa odpowiedź API; podawaj PRZED komendą).

Typowy przepływ: `szukaj "<rdzeń frazy>"` → wybierz sygnaturę → `decyzja <sygnatura>`
→ do sądowej kontroli tej decyzji: skill prawo-pl-cbosa (`szukaj "<sygnatura decyzji>"`).

## Zasady (ważne — dlaczego)

1. **Portal jest młody (od 2025 r.)** — publikowane są nowe decyzje oraz sukcesywnie starsze
   (sygnatury sprzed 2025 pojawiają się z opóźnieniem). Brak decyzji w portalu ≠ jej nieistnienie;
   starsze decyzje bywają tylko na uodo.gov.pl — zaznacz to w odpowiedzi.
2. **Decyzja nieprawomocna ≠ ostateczna wykładnia.** Sprawdzaj pole `status` (final/nonfinal)
   i dopisz zastrzeżenie; od decyzji przysługuje skarga do WSA (kontrola: skill prawo-pl-cbosa).
3. **Zawsze podawaj sygnaturę + datę** (np. „decyzja Prezesa UODO z 7.08.2025, DKN.5131.9.2025")
   — sygnatura jest stabilnym identyfikatorem (URN w API).
4. **Kwoty kar i stan prawny weryfikuj w treści decyzji**, nie w tytule — tytuł to opis redakcyjny.
5. Pełna lista endpointów, indeksów i operatorów: `references/api.md`.

## Czego ten skill NIE obejmuje

- **treści RODO i innych przepisów** (RODO → **prawo-eu-eurlex**; polskie ustawy → **prawo-pl-eli**),
- **wyroków sądów administracyjnych** ze skarg na decyzje UODO (→ **prawo-pl-cbosa**),
- **orzeczeń SN/TK/sądów powszechnych** (→ **prawo-pl-saos**),
- decyzji innych organów (UOKiK, KNF), wytycznych EROD (te bierz z edpb.europa.eu — internet).
Jeśli zagadnienie wymaga tych źródeł — powiedz to wprost, nie udawaj, że portal UODO je pokryje.

## Przykładowy przepływ

Pytanie: „czy UODO nakładał kary za brak zgłoszenia naruszenia ochrony danych i jak je uzasadnia?"
1. `szukaj "zgłoszen" --tytul "kara"` → jeśli pusto: `szukaj "art. 33"` (jeden warunek na raz).
2. `decyzja <sygnatura> --fragment "art. 33"` → argumentacja organu wokół obowiązku zgłoszenia.
3. (opcjonalnie) treść art. 33 RODO: skill prawo-eu-eurlex
   (`tekst 02016R0679-20160504 --fragment "art. 33"`).
4. (opcjonalnie) kontrola sądowa: skill prawo-pl-cbosa, `szukaj "<sygnatura decyzji>"`.
5. W odpowiedzi: sygnatura + data + status (nieprawomocna?) + kwota kary z treści decyzji.
