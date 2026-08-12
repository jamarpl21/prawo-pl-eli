---
name: prawo-pl-edzienniki
version: 1.6.3
description: >-
  Odpytuje API ELI 16 WOJEWÓDZKICH DZIENNIKÓW URZĘDOWYCH — PRAWO MIEJSCOWE: uchwały rad gmin,
  powiatów i sejmików województw, rozporządzenia i zarządzenia wojewody, akty prawa miejscowego.
  Używaj przy KAŻDYM pytaniu o prawo konkretnej gminy/powiatu/województwa: „uchwała rady
  gminy/miasta…", „miejscowy plan zagospodarowania przestrzennego (MPZP)", „podatek od
  nieruchomości / opłata targowa / opłata za śmieci w gminie X", „statut gminy/powiatu",
  „strefa płatnego parkowania", „regulamin utrzymania czystości", „uchwała krajobrazowa",
  „sieć szkół", „rozporządzenie wojewody". Wyszukiwanie po tytule i roczniku, metadane,
  pełna treść aktu i urzędowy PDF. Read-only, bez klucza. Prawo KRAJOWE (ustawy, Dz.U./M.P.)
  → skill prawo-pl-eli. Polish LOCAL law from the 16 voivodeship official journals (ELI APIs).
---

# Prawo miejscowe z API wojewódzkich dzienników urzędowych

Skill do **prawa miejscowego**: aktów publikowanych w **wojewódzkich dziennikach urzędowych** —
uchwał rad gmin/powiatów/sejmików, rozporządzeń i zarządzeń wojewody, porozumień, obwieszczeń
(w tym wyroków WSA uchylających akty miejscowe). Tych aktów NIE ma w Dz.U./M.P. — każde z 16
województw prowadzi własny e-Dziennik z API zgodnym z ELI (ten sam wzorzec co API Sejmu, ale na
osobnym hoście). Używaj go, gdy pytanie dotyczy **konkretnej gminy, powiatu lub województwa**:
podatki i opłaty lokalne, plany miejscowe (MPZP), statuty, organizacja szkół, strefy parkowania.

## Podział ról (który skill do czego)

1. **Prawo miejscowe (ta gmina/powiat/województwo) → ten skill.**
2. **Prawo krajowe → prawo-pl-eli** (ustawy, rozporządzenia, kodeksy z Dz.U./M.P.). Upoważnienie
   ustawowe do uchwały (np. art. 5 u.p.o.l. dla stawek podatku od nieruchomości) cytuj z ELI.
3. **Kontrola sądowa aktów miejscowych** (skargi na uchwały, rozstrzygnięcia nadzorcze) →
   orzecznictwo WSA/NSA w skillu **prawo-pl-cbosa**.
4. **Delegując research subagentowi**, wpisz: „akty prawa miejscowego pobieraj przez
   `scripts/edzienniki.py` (skill prawo-pl-edzienniki); ustawy przez `scripts/eli.py` (prawo-pl-eli)".

## Narzędzie

Wszystko robi helper `scripts/edzienniki.py` (tylko biblioteka standardowa Pythona — bez instalacji).
Skrypt leży **obok tego pliku SKILL.md** (`<katalog skilla>/scripts/edzienniki.py`) — NIE zakładaj, że
to `~/.claude/skills/prawo-pl-edzienniki` (skill zainstalowany jako plugin leży w katalogu pluginów;
w Claude Code: `${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-edzienniki`). Gdy nie znasz ścieżki, ustal ją:

```
EDZ=$(find "$HOME/.claude" "$HOME/.agents" /mnt /sessions -maxdepth 10 -name edzienniki.py -path "*prawo-pl-edzienniki*" 2>/dev/null | head -1)
[ -n "$EDZ" ] || { curl -fsSL https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/main/plugins/prawo-pl-edzienniki/skills/prawo-pl-edzienniki/scripts/edzienniki.py -o /tmp/edzienniki.py && EDZ=/tmp/edzienniki.py; }
python3 "$EDZ" <komenda> [...]
```

(W przykładach niżej `python3 scripts/edzienniki.py` oznacza `python3 "$EDZ"`, jeśli nie jesteś w katalogu skilla.)

### Komendy

- **dzienniki** — lista 16 dzienników z kodami i hostami; z `--woj` roczniki i liczba aktów:
  `python3 scripts/edzienniki.py dzienniki --woj DS`
  Kody = sufiks publishera ELI: `DS` dolnośląskie, `KP` kujawsko-pomorskie, `LB` lubelskie,
  `LS` lubuskie, `LD` łódzkie, `MP` małopolskie, `MZ` mazowieckie, `OP` opolskie,
  `PK` podkarpackie, `PL` podlaskie, `PM` pomorskie, `SL` śląskie, `SK` świętokrzyskie,
  `WM` warmińsko-mazurskie, `WP` wielkopolskie, `ZP` zachodniopomorskie (można też podać nazwę).
- **szukaj** — akty województwa po frazie z TYTUŁU (nazwa gminy, przedmiot uchwały):
  `python3 scripts/edzienniki.py szukaj --woj DS "plan zagospodarowania" --rok 2026 --limit 5`
  Filtr jest LOKALNY (API dzienników ignoruje filtry serwerowe — silnik pobiera rocznik i sam
  filtruje; bez rozróżniania diakrytyków). Bez `--rok`: do 3 najnowszych roczników. W tytule
  uchwał zwykle jest nazwa organu — szukaj po nazwie gminy: `szukaj --woj MP "Kraków"`.
  UWAGA: wynik to JEDNA strona (domyślnie 10 NAJNOWSZYCH pozycji z trafień) — gdy nagłówek
  liczy więcej trafień, obejrzyj resztę przez `--strona 2..N` albo `--limit <liczba trafień>`.
  „Nie ma w pierwszej dziesiątce" NIE znaczy „akt nie istnieje" — przed wnioskiem o braku
  aktu przejrzyj WSZYSTKIE trafienia (najprościej: `--limit` ≥ liczba trafień z nagłówka).
- **akt** — metadane (typ, organ, daty, status, hasła, linki PDF/HTML):
  `python3 scripts/edzienniki.py akt DS 2026 3299`
- **tekst** — treść aktu; `--fragment` wycina okna wokół frazy; `--pdf` zapisuje urzędowy PDF:
  `python3 scripts/edzienniki.py tekst DS 2026 3299 --fragment "§ 2"`
- każda komenda przyjmuje `--json` (surowa odpowiedź API; podawaj PRZED komendą).

Typowy przepływ: ustal województwo → `szukaj --woj <kod> "<gmina lub przedmiot>"` →
`akt <woj> <rok> <poz>` → `tekst … --fragment` albo `--pdf` do dosłownego cytatu.

## Zasady (ważne — dlaczego)

1. **Sygnatura aktu miejscowego** = dziennik + rocznik + pozycja (np. „Dz. Urz. Woj. Doln.
   z 2026 r. poz. 3299") — podawaj ją przy cytacie razem z organem i datą uchwały.
2. **Sprawdzaj status i wejście w życie w TREŚCI aktu** — pola `inForce`/`entryIntoForce` w API
   bywają niewypełnione; akty miejscowe wchodzą w życie zwykle 14 dni od ogłoszenia (art. 4
   ustawy o ogłaszaniu aktów normatywnych), ale uchwały podatkowe od 1 stycznia itd.
3. **Uchwała może być uchylona** rozstrzygnięciem nadzorczym wojewody albo wyrokiem WSA — przy
   sprawie spornej sprawdź orzecznictwo (skill prawo-pl-cbosa) i późniejsze pozycje dziennika.
4. **Do dosłownego cytatu używaj urzędowego PDF** (`tekst … --pdf`) — `text.html` po konwersji
   bywa zlepiony.
5. **mazowieckie (MZ)** bywa nieosiągalne spoza Polski (CDN) — silnik zgłosi to czytelnie;
   wtedy wskaż użytkownikowi UI: https://edziennik.mazowieckie.pl/
6. Pełna tabela hostów, endpointy i pułapki API: `references/api.md`.

## Czego ten skill NIE obejmuje

- **prawa krajowego** (ustawy, rozporządzenia, kodeksy → skill **prawo-pl-eli**, Dz.U./M.P.),
- **dzienników resortowych** (ministerstw) i Dziennika Urzędowego UE (→ **prawo-eu-eurlex**),
- **orzecznictwa** (kontrola aktów miejscowych → **prawo-pl-cbosa**; SN/TK → **prawo-pl-saos**),
- uchwał NIEpublikowanych w dzienniku (część uchwał „zwykłych" jest tylko w BIP gminy — powiedz
  to wprost i wskaż BIP, nie udawaj, że dziennik je pokryje).

## Przykładowy przepływ

Pytanie: „jaka jest stawka podatku od nieruchomości w Ząbkowicach Śląskich na 2026 r.?"
1. Województwo: dolnośląskie → kod `DS`.
2. `szukaj --woj DS "Ząbkowic Śląskich podatku od nieruchomości" --rok 2025` (uchwały podatkowe
   na 2026 r. są ogłaszane pod koniec 2025 r.; jeśli pusto — sama nazwa gminy).
3. `akt DS 2025 <poz>` → metadane; `tekst DS 2025 <poz> --fragment "od gruntów"` → stawki.
4. (opcjonalnie) upoważnienie ustawowe: skill prawo-pl-eli, art. 5 ustawy o podatkach i opłatach
   lokalnych. W odpowiedzi: stawka + „Dz. Urz. Woj. Doln. z 2025 r. poz. X" + data uchwały.
