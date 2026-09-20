---
name: prawo-pl-orzeczenia-ms
metadata:
  version: "2.1.0"
description: >-
  Pobiera orzeczenia sądów powszechnych (SA/SO/SR) bezpośrednio z Portalu Orzeczeń
  Ministerstwa Sprawiedliwości (orzeczenia.ms.gov.pl). Używaj, gdy użytkownik wskazuje
  portal MS lub sądu powszechnego, potrzebuje najnowszych publikacji, weryfikacji
  cytatu u źródła, urzędowego PDF albo orzeczenia brakującego w SAOS.
  Wyszukiwanie po frazie, sygnaturze, sądzie i datach; metryka, pełna treść,
  powołane przepisy i RSS. Nie obejmuje SN/TK/KIO, NSA/WSA ani treści ustaw.
---

# Portal Orzeczeń Sądów Powszechnych MS

Źródło pierwotne: https://orzeczenia.ms.gov.pl. Publikuje wybór zanonimizowanych
orzeczeń SA/SO/SR. Brak wyniku nie dowodzi braku wyroku. SAOS pozostaje wygodnym
agregatem do ogólnego researchu; ten skill służy dostępowi do źródła i nowszych publikacji.

## Uruchomienie

Helper `scripts/orzeczenia_ms.py` obok tego SKILL.md wymaga tylko Python 3.8+ i sieci.
Korzysta z HTTPS, walidacji certyfikatu i odstępu ≥0,6 s między żądaniami w procesie.
Nie wymaga klucza, instalacji bibliotek ani przeglądarki. Uruchamiaj helper z tego pakietu:

```
# 1) Claude Code: przy ładowaniu skilla podstawia ${CLAUDE_PLUGIN_ROOT} (pełna ścieżka poniżej).
MS="${CLAUDE_PLUGIN_ROOT}/skills/prawo-pl-orzeczenia-ms/scripts/orzeczenia_ms.py"
# 2) Codex / Claude Desktop / instalacja ręczna: katalog TEGO pliku SKILL.md (Claude Code podaje go
#    jako „Base directory for this skill”, Codex w liście skilli) — podstaw go zamiast <katalog skilla>.
[ -f "$MS" ] || MS="<katalog skilla>/scripts/orzeczenia_ms.py"
# 3) Piaskownica (Cowork, czat z code execution): katalog pluginu bywa NIEWIDOCZNY dla powłoki — wtedy
#    pobierz DOKŁADNIE tę wersję helpera (tag = wersja z nagłówka tego pliku). Suma SHA-256 jest
#    sprawdzana w kodzie przed zapisem i przed każdym uruchomieniem; niezgodna = helper nie startuje.
[ -f "$MS" ] || MS=$(python3 - <<'EOF'
import hashlib, os, sys, urllib.request
WERSJA, SHA256 = "2.1.0", "d1d64a6f945b6d434ad8cb8311fdc6885ec3ad54b94973e92cd6abde33f4efd5"
URL = f"https://raw.githubusercontent.com/jamarpl21/prawo-pl-eli/v{WERSJA}/plugins/prawo-pl-orzeczenia-ms/skills/prawo-pl-orzeczenia-ms/scripts/orzeczenia_ms.py"
p = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"prawo-pl-orzeczenia-ms-{WERSJA}", "orzeczenia_ms.py")
try:
    dane = open(p, "rb").read() if os.path.exists(p) else urllib.request.urlopen(URL, timeout=30).read()
except Exception as e:
    sys.exit(f"BŁĄD: nie udało się pobrać helpera ({e}). Bez helpera NIE cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
if hashlib.sha256(dane).hexdigest() != SHA256:
    os.path.exists(p) and os.remove(p)
    sys.exit("BŁĄD: suma SHA-256 helpera nie zgadza się z SKILL.md — helper NIE zostanie uruchomiony. Nie cytuj prawa z pamięci ani z portali — poinformuj użytkownika.")
os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(dane); print(p)
EOF
) && [ -f "$MS" ] || exit 1
python3 "$MS" <komenda> [...]
```

W przykładach `python3 "$MS"` wskazuje na helper ustalony powyżej.

## Użycie

```bash
python3 "$MS" szukaj "dobra osobiste" --od 2021-01-01 --do 2021-12-31 --json
python3 "$MS" sygnatura I ACa 1410/23 --json
python3 "$MS" szukaj --sad 15050500 --sort datapublikacji --strona 1
python3 "$MS" orzeczenie 152010000000503_I_C_002715_2021_Uz_2021-09-29_001 --json
python3 "$MS" metryka <ID-lub-link-centralnego-portalu>
python3 "$MS" przepisy <ID>
python3 "$MS" pdf <ID> --plik /tmp/orzeczenie.pdf
python3 "$MS" rss --limit 10 --json
python3 "$MS" rss --sad 15050000 --limit 5
```

- `szukaj`: fraza, `--sygnatura`, `--od`/`--do` (RRRR-MM-DD; data **orzeczenia**),
  `--sedzia`, `--funkcja CHAIRMAN|RAPPORTEUR|REASONS_AUTHOR|JUDGE`,
  `--sad` (8 cyfr), `--wydzial` (2 cyfry), `--apelacja` (4), `--okreg` (6),
  `--haslo` (dokładne hasło tematyczne), `--przepis` (pole Podstawa prawna),
  `--tezowane`, `--istotnosc 0..5`. Kody są wewnętrznymi identyfikatorami portalu,
  nie nazwami sądów; nie zgaduj ich. Przykłady i mapowanie: [references/api.md](references/api.md).
- Strona od **1**, po 10 rekordów. `--sort score|data|datapublikacji|istotnosc`,
  `--kierunek ascending|descending`. Dla konkretnego dnia ustaw `--od` i `--do` na tę samą datę.
- `orzeczenie` pobiera metrykę i pełny tekst; nie zastępuj uzasadnienia fragmentem z wyszukiwarki.
  `przepisy` pokazuje osobną listę portalu (może być niepełna). `pdf` pobiera eksport urzędowy
  i nie nadpisuje istniejącego pliku. Link do podobnych orzeczeń jest w `podobne_url`;
  jego dostępność nie jest gwarantowana (w teście 2026-09-20 endpoint zwrócił HTTP 400).
- `rss`: ograniczone okno nowych publikacji, nie pełne archiwum; `--sad` to kod kanału
  z https://orzeczenia.ms.gov.pl/rss/courts. Data RSS jest datą publikacji, nie wyroku.
- Wszystkie komendy przyjmują `--json` i `--strict`, przed lub po komendzie.
  JSON ma pola `uwagi`; nie pomijaj ich w odpowiedzi. Kod 0 = wynik, 1 = rozpoznany brak
  wyników, 2 = błąd/UNKNOWN. Przy błędzie stdout pozostaje pusty także z `--json`.

## Weryfikacja i cytowanie

Podawaj **sygnaturę, sąd, datę orzeczenia i link `url` do metryki**. Data publikacji
jest osobnym polem. Nie wyciągaj daty wyroku z ID dokumentu, opisu HTML ani daty RSS.
Tekst orzeczenia zachowuje akapity i indeksy górne w numerach przepisów.

`prawomocne` to `true`/`false` przy komunikacie na stronie metryki. Wartość `false`
zwracana jest też dla `.single_result.invalid`: oficjalny arkusz CSS wyświetla wtedy
obraz „ORZECZENIE NIEPRAWOMOCNE” (`nieprawomocny.png`, zweryfikowano 20.09.2026).
Brak komunikatu lub tego oznaczenia to `null`, nie domniemanie prawomocności.
`--strict` dla `metryka`, `orzeczenie`, `przepisy` i `pdf` blokuje wynik, jeśli portal
nie potwierdza prawomocności. Zwykły tryb udostępnia treść z ostrzeżeniem.
Dla wyszukiwania i RSS kontrolowana jest struktura odpowiedzi, ale nie prawomocność
poszczególnych pozycji — trzeba pobrać ich metryki. To nie jest ocena trafności orzeczenia
ani dowód, że lista portalu zawiera całość orzecznictwa.

Treść i aktualność przepisów sprawdzaj w `prawo-pl-eli`; NSA/WSA w `prawo-pl-cbosa`.
SN/TK/KIO nie należą do tego portalu — skorzystaj z właściwego źródła urzędowego lub
`prawo-pl-saos`, z uwzględnieniem ograniczeń dat zbiorów SAOS.

## Dostęp F5/TSPD

20.09.2026 potwierdzono dostęp przez `urllib` z `User-Agent: curl/8.7.1`, bez cookies
ani JavaScriptu. Domyślny Python był odrzucany, a UA Chrome zwracał wyzwanie TSPD.
To obserwacja z testowanej sieci, nie obietnica dostępu z każdego IP. Gdy reguły się
zmienią, helper zwraca UNKNOWN; nie traktuj tego jako braku orzeczenia. Można ponowić
później lub skorzystać z przeglądarki, ale nie przedstawiaj niepobranej treści jako zweryfikowanej.
