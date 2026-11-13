# Znane problemy wpływające na rzetelność danych

Stan na wydanie **2.5.0** (13.11.2026). Lista obejmuje tylko to, co może sprawić, że wynik narzędzia
jest niepełny, mylący albo niesprawdzony. Nie znamy przypadku, w którym silnik zmyśla dane — każdy
punkt niżej to albo wada odczytu, albo błąd w samym źródle urzędowym, albo obszar, którego nie dało się
porównać z niezależnym źródłem.

Zasady prowadzenia listy: punkt znika dopiero po naprawie potwierdzonej testem i wpisem w `CHANGELOG.md`;
nowy znany problem trafia tu w tym samym wydaniu, w którym go wykryto. Historia napraw jest w
`CHANGELOG.md`, pełny stan punktów audytu — w nocie na początku `docs/audyt-merytoryczny-2026-08.md`.
Ogólne zastrzeżenia co do źródeł (baza wtórna SAOS, brak API CBOSA, zakres rejestru umów) opisuje
README, sekcja „Ważne zastrzeżenia”.

## 1. Wynik może być błędny bez ostrzeżenia

Tu narzędzie nie sygnalizuje problemu — przed zacytowaniem porównaj wynik z urzędowym PDF.

| Silnik | Problem | Przykład | Co zrobić |
|---|---|---|---|
| `eli.py` (tekst z PDF) | Odsyłacz do przypisu stojący za indeksem górnym nie jest rozpoznawany; treść przypisów zostaje wklejona w przepis. | Kodeks pracy, Dz.U. 2026 poz. 1245, art. 302¹ § 7 — przypisy 60–62 w treści przepisu. | Porównaj przepis z PDF (`pdf`). |
| `eli.py` (tekst z PDF) | Odsyłacz oddzielony w PDF spacją od słowa zostaje w tekście jako „N)”. Sam przypis jest wtedy wypisany z etykietą „bez odsyłacza w treści tej strony” (6 takich przypisów na 23 sprawdzonych tekstach jednolitych z 2026 r.). | — | Cyfrę z nawiasem na końcu zdania traktuj jako możliwy odsyłacz, nie część przepisu. |
| `eli.py` (tekst z PDF) | Data z marginesu strony (adnotacja o wejściu zmiany w życie) bywa doklejona do treści przypisu. | Dz.U. 2019 poz. 1500 — „18.01.2021” przy przypisie. | Porównaj przypis z PDF. |
| `eli.py` (tekst z PDF) | Strony z podwójną warstwą tekstu dają tekst przemieszany; przypisy trafiają w treść. | Dz.U. 2025 poz. 615 (29 przypisów w treści przepisów). | Dla tego aktu cytuj wyłącznie z PDF. |
| `eli.py` (tabele w PDF) | Wielowierszowe komórki tabel bywają przemieszane; objaśnienia pod tabelą nie są łączone z odsyłaczami w komórkach. Tabele porównano z PDF wiersz po wierszu tylko w 3 przypadkach. | — | Dane liczbowe z tabel zawsze sprawdź w PDF. |
| `eli.py` (OCR, akty z lat 1990–1999) | Przy składzie wielołamowym kolejność łamów bywa zła i część paragrafów ginie. | Dz.U. 1994 poz. 73. | Skany sprzed 2000 r. cytuj po porównaniu z obrazem strony. |
| `edzienniki.py` (tabele) | Pusta komórka tabeli znika bez śladu; wielowierszowe nagłówki bywają przemieszane (wartości i ich kolejność są poprawne). | — | Przy tabelach stawek sprawdź nagłówki w PDF. |
| `rejestrumow.py` | Kwoty są bez waluty i bez informacji netto/brutto — API rejestru ich nie podaje (interfejs rejestru dopisuje „zł” do każdej kwoty). | — | Nie zakładaj, że kwota jest brutto. |
| `cbosa.py` do wersji 2.4.0 | `sygnatura` bez numeru wydziału wskazywała pierwszą z kilku różnych spraw jako „pełną treść”; `--strona` za końcem wyników dawała „zweryfikowane zero”. Naprawione w 2.5.0. | `sygnatura "SA/Wa 1/20"` — 8 różnych spraw. | Zaktualizuj do 2.5.0. |

## 2. Błąd leży w źródle urzędowym

Narzędzie pokazuje dane zgodnie ze źródłem; tam, gdzie to możliwe, dodaje ostrzeżenie.

| Źródło | Problem | Przykład | Jak reaguje narzędzie |
|---|---|---|---|
| CBOSA | Oznaczenie prawomocności jest niekonsekwentne: 1 z 5 sprawdzonych wyroków WSA uchylonych przez NSA ma „orzeczenie prawomocne”. | II SA/Wa 1341/22 (uchylony wyrokiem NSA III OSK 3311/23). | Od 2.5.0 UWAGA przy każdym orzeczeniu WSA oznaczonym jako prawomocne, z którym CBOSA wiąże wyrok NSA. Ostrzeżenie pojawia się też wtedy, gdy NSA oddalił skargę kasacyjną — wynik NSA trzeba sprawdzić samemu. `--strict` tego nie blokuje. |
| CBOSA | Pole „Sygn. powiązane” bywa błędne. | II SA/Wa 546/22 (skarga na decyzję UODO) jest powiązany z II SA/Wa 546/21 — inną sprawą. | Brak osobnego ostrzeżenia; historię sprawy sprawdzaj po stronie skarżącej i przedmiocie. |
| CBOSA | Nie publikuje części postanowień WSA (np. o zawieszeniu postępowania, o odmowie wstrzymania wykonania); sygnatura prowadzi wtedy do późniejszego wyroku w tej sprawie. | II SA/Wa 285/24, II SA/Wa 774/24. | Brak ostrzeżenia. |
| CBOSA | Numery i dni decyzji organów są zanonimizowane; wyszukiwanie po numerze decyzji daje zero także wtedy, gdy wyrok istnieje. | `szukaj "DKN.5130.3114.2020"` → zero, choć NSA III OSK 1251/22 cytuje ten numer. | Komunikat o anonimizacji i kod 2 (nie „zweryfikowany brak”). |
| Portal orzeczeń UODO | Nie ma części wyroków sądów administracyjnych: w 2 z 15 sprawdzonych decyzji brakuje orzeczenia, które jest w CBOSA. | DKN.5131.1.2024 — brak wyroku WSA II SA/Wa 1728/24 uchylającego karę 4 053 173 zł; DKN.5130.3114.2020 — brak wyroku NSA III OSK 1251/22. | `decyzja` pokazuje decyzję jako nieprawomocną, ostrzega, że brak wpisu nie oznacza braku wyroku, i podaje gotowe zapytania CBOSA. |
| Portal orzeczeń UODO | Przy części wpisów podaje tylko „rozpatrzenie” albo „uprawomocnienie”, bez rozstrzygnięcia. | ZSOŚS.440.8.2018 (wynik zna CBOSA: NSA III OSK 2986/21). | Status „NIEUSTALONY” zamiast zgadywania. |
| API ELI Sejmu | Błędy w danych struktury aktu. | k.p.c., Dz.U. 2024 poz. 1568: art. 15 zapisany jako „Art. 1522.”, brak „DZIAŁ IIda”. | Podpowiedź przy art. 15; brakującego działu narzędzie nie odtwarza. |
| API ELI Sejmu | Brak `/struct` dla tekstów jednolitych bez wersji HTML (wszystkie z lat 2025–2026). | — | `struktura` dla tych aktów nie jest porównywalna z niezależnym źródłem. |

## 3. Niesprawdzone — rzetelność nieznana

Dla tych obszarów nie udało się wykonać porównania z niezależnym źródłem urzędowym.

- **SAOS:** daty orzeczeń Sądu Najwyższego (strona sn.pl stoi za filtrem antybotowym — świadomie bez
  zapytań) i Trybunału Konstytucyjnego oraz oznaczenie prawomocności.
- **E-dzienniki:** dziennik mazowiecki — jedyny z 16 bez porównania z PDF (w dniu kontroli HTTP 503).
- **EUR-Lex:** brak porównania ze stroną eur-lex.europa.eu (odpowiada automatom wyzwaniem antybotowym);
  źródłem porównania były urzędowe PDF z CELLAR.
- **ELI:** „Stan prawny na” w nieurzędowych tekstach ujednoliconych Kancelarii Sejmu (nie mają
  obwieszczenia, z którym można by je porównać).
- **ELI:** osobne ostrzeżenia dla przypisu bez ustalonego odsyłacza (wyrok TK, zmiana, brzmienie przyszłe)
  sprawdzają tylko testy offline — w próbie na żywo nie było takiego przypisu o wyroku TK.
- **ELI:** próg odstępu, po którym dwa sąsiednie odsyłacze do przypisów są rozdzielane (12 pt), opiera się
  na dwóch zmierzonych parach, obu w k.p.c.
- **UODO → CBOSA:** stanu trzech decyzji nie dało się rozstrzygnąć w CBOSA — DKN.5131.3.2021,
  DKN.5130.4179.2020, DKN.5131.5.2024.

## 4. Dostępność źródeł

Te sytuacje nie dają fałszywych danych — narzędzie kończy się błędem (kod wyjścia 2), nie „brakiem
wyników” — ale mogą uniemożliwić sprawdzenie.

- **CBOSA (orzeczenia.nsa.gov.pl)** bywa niestabilna: 13.11.2026 serwer po kilkudniowej awarii
  dwukrotnie w ciągu godziny zrywał połączenia na kilkanaście sekund.
- **Dziennik dolnośląski** odpowiadał po 66 s na rekord, przy limicie 25 s w narzędziu — `akt DS` kończy
  się wtedy błędem przekroczenia czasu.
- **`pdftotext` bez współrzędnych słów** (stara wersja albo błąd narzędzia): `eli.py` ostrzega, że
  odsyłacze do przypisów nie zostały rozpoznane, a `--strict` blokuje wynik.
