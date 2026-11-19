# API EUREKA (Ministerstwo Finansów) — referencja endpointów

Baza: `https://eureka.mf.gov.pl/api/public/v1`  ·  Interfejs: `https://eureka.mf.gov.pl`
(podgląd dokumentu: `/informacje/podglad/{ID}`).

**Status API:** MF NIE publikuje dokumentacji tego API. Jest to backend, z którego korzysta sama
strona EUREKA (Angular, `environment.api = "/api/public/v1"`); endpointy ustalono z kodu strony
i sprawdzono na żywo 2026-11-17. Bez klucza i bez logowania. Kształt odpowiedzi może się zmienić
bez zapowiedzi — silnik sprawdza go przy każdym wywołaniu i przy niezgodności kończy kodem 2.

## Endpointy (zweryfikowane 2026-11-17)

| Metoda i ścieżka | Zwraca |
|---|---|
| `POST /wyszukiwarka/informacje?size=N&page=P&sort=POLE,KIERUNEK` | wyniki wyszukiwania (body niżej) |
| `GET /informacje/{ID}` | dokument: `{id, versionId, nazwa, dokument: {fields: [{key, value}]}}` |
| `GET /informacje/{ID}/eksport/PDF` | urzędowy eksport PDF (metryka + treść) |
| `GET /informacje/pliki/{ID}/{versionId}/{fileName}` | załącznik (starsze interpretacje: skan PDF) |
| `GET /informacje/{ID}/metadane` | definicje metadanych szablonu (bez wartości) |
| `GET /pozycje-slownika/wyszukiwarka?sid=S&isFirstLevel=true&size=N&page=P&sort=kolejnosc,asc` | pozycje słownika `S` |
| `GET /parametry-wyszukiwarki/all` | lista pól filtrowalnych (kod metadanej, typ, ID słownika) |
| `GET /metadane/views` | wszystkie kody metadanych |
| `GET /wyszukiwarka/sugestie/{fraza}` | podpowiedzi fraz |

Nieistniejące ID: `GET /informacje/{ID}` → HTTP 404 z `errors[].errorCode = NOT_FOUND`.

## Body wyszukiwarki

```json
{"filter": {"KATEGORIA_INFORMACJI": [1], "DT_WYD_start": "2026-01-01", "DT_WYD_end": "2026-06-30"},
 "columns": ["ID_INFORMACJI", "SYG", "DT_WYD", "TEZA", "KATEGORIA_INFORMACJI", "STATUS_INFORMACJI", "KOMENTARZE_BIP"],
 "searchQuery": "waluta wirtualna", "searchInFullPhrase": false, "searchInContent": false,
 "searchInSynonyms": false, "warunkiDodatkowe": []}
```

Odpowiedź: `{"results": [{KOD: wartość…}], "totalHits": N, "facetedResults": [], "elasticDocumentId": …}`.
Wyniki zawierają tylko pola z `columns`; pola słownikowe wracają jako **etykiety tekstowe** (lista albo
string — silnik normalizuje), np. `"AUTOR": ["Dyrektor Krajowej Informacji Skarbowej"]`. Silnik pobiera
nimi etykiety metadanych dokumentu (filtr `ID_INFORMACJI`), bo `GET /informacje/{ID}` zwraca same ID.

### Filtry (sprawdzone)

| Klucz `filter` | Wartość | Uwagi |
|---|---|---|
| `KATEGORIA_INFORMACJI` | **lista** ID, np. `[1, 3]` | liczba albo string bez listy → HTTP 500 |
| `STATUS_INFORMACJI` | lista ID | 27 Aktualna, 29 Nieaktualna/Zmieniona/Wygaszona/Archiwalna, 30 Nieaktualna |
| `KOMENTARZE_BIP` | lista ID | słownik `sid=10` (np. 58 = uchylona wyrokiem WSA) |
| `SYG` | string | dopasowanie **fragmentu**, **rozróżnia wielkość liter** (`itpp1/…` → 0, `ITPP1/…` → 1) |
| `ID_INFORMACJI` | string | dokładnie jeden dokument |
| `INFORMACJA_ZMIENIANA` | string ID | dokumenty, które ZMIENIAJĄ wskazany (odwrotne powiązanie) |
| `DT_WYD_start` / `DT_WYD_end` | `RRRR-MM-DD` | data wydania, oba końce włącznie |

Słowniki: kategorie `sid=1`, status `sid=3`, autor/organ `sid=4`, słowa kluczowe `sid=5`, komentarze
BIP `sid=10`, sądy `sid=17`, zagadnienia `sid=18`, przepisy `sid=19`, organ JST `sid=32`.

### Fraza i flagi

- `searchQuery` — wyszukiwanie Elasticsearch z analizą języka polskiego: `kryptowaluty` 831,
  `kryptowalut` 829, `kryptowaluta` 853 trafień (odmiana uwzględniona, wyniki bliskie, nie równe).
- `searchInFullPhrase: true` — słowa obok siebie („samochód osobowy leasing”: 41 zamiast 13 513).
- `searchInContent: true` — tylko treść, bez tezy i metadanych (nieco mniej trafień).
- `searchInSynonyms` — w silniku zawsze `false` (synonimy MF poszerzają wynik w nieprzewidywalny sposób).

### Sortowanie i stronicowanie

- `sort=DT_WYD,DESC` / `DT_WYD,ASC` / `_score,DESC` (trafność) — wszystkie trzy działają.
- `size` do co najmniej 200; silnik ogranicza do 100.
- **Okno 10 000:** `page × size ≥ 10 000` → HTTP 500 (`size=50&page=199` → 200, `page=200` → 500).
  Silnik odrzuca takie zapytanie przed wysłaniem i każe zawęzić je datami.
- Błędny filtr → HTTP 500 z `errors[].errorResult = "Nieoczekiwany błąd systemu, UUID: …"` — to błąd
  zapytania, nie brak wyników.

## Dokument `GET /informacje/{ID}` — pola

| Kod | Znaczenie |
|---|---|
| `SYG` | sygnatura (np. `0115-KDIT1.4011.700.2026.1.MR`; stare: `ITPP1/443-28/07/AW`) |
| `DT_WYD`, `DATA_PUBLIKACJI` | daty jako **znacznik UTC** (`2026-03-08T23:00:00.000Z` = 9 marca w Polsce); wyszukiwarka podaje już datę dzienną |
| `TEZA` | teza / pytanie |
| `TRESC_INTERESARIUSZ` | **pełna treść** (HTML z edytora Word; przypisy jako `<a href="#_ftnN"><sup>[N]</sup></a><sup>)</sup>`) |
| `KATEGORIA_INFORMACJI`, `STATUS_INFORMACJI`, `AUTOR`, `PRZEPISY`, `SLOWA_KLUCZOWE`, `ZAGADNIENIA` | ID słownikowe |
| `KOMENTARZE_BIP`, `KOM_BIP_OPIS` | **stan prawny**: uchylona / zmieniona / wygaszona / potwierdzona (+ opis z sygnaturą wyroku) |
| `INFORMACJA_ZMIENIAJACA` | ID dokumentu, który zmienił ten dokument |
| `INFORMACJA_ZMIENIANA` | ID dokumentu zmienianego przez ten dokument (np. „Zmiana interpretacji indywidualnej”) |
| `ZALACZNIKI` | `[{fileName, fileType, bytes}]` — pobranie: `/informacje/pliki/{ID}/{versionId}/{fileName}` |

## Zweryfikowane pułapki

- **`STATUS_INFORMACJI = Aktualna` nie znaczy, że interpretacja się ostała.**
  0112-KDIL2-2.4011.893.2025.2.AA (ID 682533): status „Aktualna”, a komentarz BIP mówi „Interpretacja
  uchylona wyrokiem WSA w Poznaniu 22.07.2026 r. sygn. akt I SA/Po 763/26”. Spośród 2697 interpretacji
  indywidualnych z komentarzem „uchylona wyrokiem WSA” (ID 58) status „Aktualna” ma 9 (odczyt 2026-11-18).
- `SYG` rozróżnia wielkość liter, a sygnatury JST mają litery mieszane: `Fn-II-2.310.1.2026.AB` → 1,
  `FN-II-…` → 0 (2026-11-18). Fragment bez liter (`2.310.1.2026`) znajduje dokument niezależnie od liter.
- `SYG` bywa listą kilku sygnatur rozdzielonych średnikiem: `COP-13.310.6.2026.GWA; COP-13/31101/1360/GWA/26`.
- Interpretacja zmieniona przez Szefa KAS ma komentarz i `INFORMACJA_ZMIENIAJACA`
  (669739 → 717939); zmiana ma odwrotne `INFORMACJA_ZMIENIANA`. Silnik sprawdza oba kierunki.
- Kategoria w wynikach wraca raz jako string, raz jako lista — silnik normalizuje.
- Pojedyncze rekordy mają datę wydania późniejszą niż dzień odczytu (np. 0114-KDIP1-1.4012.578.2026.2.ESZ:
  `DT_WYD` 2026-11-30, odczyt 2026-11-17) — błąd danych źródła; silnik podaje datę bez korekty.
- Starsze dokumenty (2007–2016) mają treść HTML ORAZ skan PDF w `ZALACZNIKI`; treść HTML zgadza się
  z urzędowym eksportem PDF (pomiar w CHANGELOG).
