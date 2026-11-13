# Znane problemy wpływające na rzetelność danych

Stan na wydanie **2.6.0** (13.11.2026). Lista obejmuje tylko to, co może sprawić, że wynik narzędzia
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
| `eli.py` (tabele w PDF) | Część tabel z przemieszanymi komórkami nie daje ostrzeżenia (wykrywanie — sekcja 2). Chodzi o nagłówek tabeli z tekstem wyśrodkowanym w kilku kolumnach, komórki wyśrodkowane w pionie (wartość w środkowym wierszu nazwy) i tabelę bez kolumny „Lp.”, w której zawija się pierwsza kolumna. Na 35 stronach tabel bez alarmu, porównanych wiersz po wierszu z obrazem strony, takie przemieszanie było na 3. Objaśnienia pod tabelą nie są łączone z odsyłaczami w komórkach — przy odsyłaczu stoi „treści przypisu nie odnaleziono” (DU 2021/1960: 379 takich odsyłaczy). | Dz.U. 2021 poz. 1960, s. 11 i 40 PDF; Dz.U. 2026 poz. 1252, s. 37 PDF. | Dane liczbowe z tabel zawsze sprawdź w PDF. |
| `eli.py` (OCR, zeszyty z lat 1990–2011) | Zła kolejność łamów albo przemieszane łamy bez luki w numeracji jednostek (np. wewnątrz jednego paragrafu, w tekście bez „§”/„Art.”) nie dają ostrzeżenia. Luki i odwrócenia numeracji są wykrywane (sekcja 2). | Dz.U. 1994 poz. 73 (kolejność łamów na s. 4 naprawiona). | Skany sprzed 2000 r. cytuj po porównaniu z obrazem strony. |
| `eli.py` (tekst z PDF) | Okruchy pionowego znaku wodnego „www.rcl.gov.pl” zostają w tekście jako osobne akapity („l.g”, „ov”, „w.”). Usuwane są tylko w zeszytach z lat 2010–2011. W próbie występowały w 4 PDF z lat 2013–2016. | M.P. 2013 poz. 200; M.P. 2016 poz. 450; Dz.U. 2014 poz. 900; Dz.U. 2015 poz. 2213. | Pomiń jedno-, dwuliterowe akapity z liter „w r c l g o v p .”. |
| `eli.py` (tabele w PDF) | Wcięty wiersz komórki tabeli bywa osobnym akapitem, a wyraz przeniesiony na granicy wierszy zostaje rozcięty („– wy- ⏎ łącznie wywar”, „nie- ⏎ sklasyfikowane”). Wyliczenie w komórce bywa bez spacji po numerze („1)arachidów”). Kolejność słów jest zachowana. | VAT, Dz.U. 2017 poz. 1221, s. 307 i 309 PDF; Dz.U. 2020 poz. 106, s. 321 PDF. | Szukając słowa w tabeli, uwzględnij podział wyrazu. |
| `edzienniki.py` (tabele) | Pusta komórka tabeli znika bez śladu; wielowierszowe nagłówki bywają przemieszane (wartości i ich kolejność są poprawne). | — | Przy tabelach stawek sprawdź nagłówki w PDF. |
| `rejestrumow.py` | Kwoty są bez waluty i bez informacji netto/brutto — API rejestru ich nie podaje (interfejs rejestru dopisuje „zł” do każdej kwoty). | — | Nie zakładaj, że kwota jest brutto. |
| `eli.py` do wersji 2.5.0 | Tekst z PDF nie ostrzegał o żadnym z miejsc opisanych w sekcji 2; w k.p. (Dz.U. 2026 poz. 1245) treść przypisów 60–62 stała w art. 302¹ § 7, a w tekstach ujednoliconych z lat 2018–2022 data ze stopki rozcinała zdania. Naprawione w 2.6.0. | Dz.U. 2026 poz. 1245, art. 302¹; Dz.U. 2019 poz. 1500. | Zaktualizuj do 2.6.0. |
| `cbosa.py` do wersji 2.4.0 | `sygnatura` bez numeru wydziału wskazywała pierwszą z kilku różnych spraw jako „pełną treść”; `--strona` za końcem wyników dawała „zweryfikowane zero”. Naprawione w 2.5.0. | `sygnatura "SA/Wa 1/20"` — 8 różnych spraw. | Zaktualizuj do 2.5.0. |

## 2. Wykrywane — narzędzie ostrzega

Tu wynik bywa błędny, ale narzędzie to wykrywa: wypisuje `UWAGA: … miejsca odczytane z PDF niepewnie` ze stroną PDF
i artykułem, a `--strict` blokuje wynik (kod 2). Z `--fragment` ostrzeżenie i blokada dotyczą tylko miejsc w wypisanym
fragmencie (obszar — tabela, strona, luka w numeracji — liczy się, gdy zachodzi na fragment). Na 73 tekstach z PDF
ostrzeżenie o przypisach i dacie wskazuje 4 miejsca w 3 aktach (przed naprawą ukrytej warstwy tekstu i dat z urwaną notką: 38 w 6 aktach, wszystkie prawdziwe). Na 88 tekstach (73 i 15 nowych z
tabelami i z lat 1992–1998) ostrzeżenia o obszarach wskazały 65 obszarów w 16 aktach: 44 tabele, 13 stron z ukrytą
warstwą, 5 notek marginesu, 3 luki w numeracji; fałszywy alarm: 1 (tabela M.P. 2023/1279 s. 11).

| Silnik | Problem | Przykład | Jak reaguje narzędzie |
|---|---|---|---|
| `eli.py` (tekst z PDF) | Treść przypisu w treści przepisu albo przypis bez treści. Do wersji 2.5.0 tak było w k.p. (Dz.U. 2026 poz. 1245, art. 302¹ § 7: przypisy 60–62 w § 7). Przyczyna: kropka za indeksem górnym („Art. 302¹.⁶²⁾”) jest w PDF osobnym słowem, więc odsyłacz 62 nie był potwierdzony współrzędnymi, a bez tego blok przypisów 60–62 nie był rozpoznawany. Naprawione razem z kilkoma innymi układami bloku przypisów. W Dz.U. 2025 poz. 615 (28 przypisów w treści) przyczyną była ukryta druga kopia przypisu (osadzony PDF) i tabele obrócone o 90° — naprawione (32 → 0 miejsc). Zostały: przypis powtórzony z dalekiej strony, zeszyty z lat 2000–2011 i odsyłacze w komórkach tabel do objaśnień pod tabelą. | VAT, Dz.U. 2017 poz. 1221, s. 302 PDF (przypis 7 przy załączniku nr 3, treść na s. 77); Dz.U. 2010 poz. 1128 (przypis 4 pod podpisem). | „„N) …” w treści przepisu (s. … PDF, art. …)” — przy wierszu „N) Zmiany tekstu…”, „N) Dodany przez…” itp.; „przypis N) bez treści (s. … PDF, art. …)” — przy odsyłaczu. |
| `eli.py` (tekst z PDF) | Przypis bez ustalonego odsyłacza: odsyłacz mógł zostać w treści jako „N)” i udawać punkt wyliczenia. Do wersji 2.5.0 dotyczyło to też odsyłacza oddzielonego w `-layout` spacją, której w PDF nie ma (DU 2026/1473: „…Straży Pożarnej 15)”), i pary „1), 2)”. Naprawione: na 73 tekstach takich przypisów było 80, są 2, a po naprawie stron obróconych w Dz.U. 2025 poz. 615 — 0. Trzeci odsyłacz z rzędu („1), 2), 3)”) też jest rozpoznawany (test syntetyczny; w próbie nie wystąpił). | W próbie 88 tekstów: 0; w 32 aktach kontroli niezależnej: 1 (Dz.U. 2022 poz. 2495, przypis 1, s. 1 PDF — nieporównany z PDF). | „przypis N) bez odsyłacza (s. … PDF, art. …)”. |
| `eli.py` (tekst z PDF) | Data ze stopki strony w treści: akapit, który jest samą datą, czasem rozcina zdanie. Do wersji 2.5.0 dotyczyło to każdej strony tekstów ujednoliconych ze stopką w zapisie „18.01.2021”, czyli PDF wygenerowanych od 04.2018 do 01.2022 (np. Dz.U. 2019 poz. 1500, Dz.U. 2020 poz. 920). Data rozcinała zdania, doklejała się do ostatniego przypisu strony albo zasłaniała blok przypisów. Naprawione dla dat, które powtarzają się na końcu stron, i dla daty wydruku z urwaną notką marginesu pod nią (data znika, notka idzie do „[margines: …]” i daje ostrzeżenie — wiersz niżej). Został akt jednostronicowy. Data w komórce tabeli w środku strony nie daje już alarmu (test syntetyczny). | Dz.U. 2020 poz. 2374 (jedna strona). | „samodzielna data „…” (s. … PDF, art. …)”. |
| `eli.py` (tabele w PDF) | Tabela, w której w jednym wierszu zawija się kilka komórek: `-layout` czyta wiersz po wierszu strony i przeplata słowa kolumn („13 bez względu Wyroby medyczne… na CN medycznych…”). Kolejności nie naprawiono — bez linii tabeli z grafiki PDF nie da się odróżnić dalszego ciągu komórki od nowego wiersza. Na 41 obszarach porównanych z obrazem strony zgodnych wierszy było 1663 z 2904. Część przemieszań nie jest wykrywana (sekcja 1). | VAT, Dz.U. 2026 poz. 1263, s. 414–418 PDF; Dz.U. 2025 poz. 615, s. 6–50 PDF; Dz.U. 1998 poz. 1012. To samo ostrzeżenie dostaje strona zeszytu z dwoma łamami, których silnik nie rozdzielił — zdania obu łamów są wtedy przeplecione (Dz.U. 2002 poz. 1000, s. 1 PDF). | „tabela (s. … PDF, załącznik) — komórki zawinięte na kilka wierszy są czytane wiersz po wierszu strony…”. |
| `eli.py` (tekst z PDF) | Ukryta warstwa tekstu: strona to osadzony PDF z własnymi nagłówkami i przypisami, przycięty ramką; `pdftotext` daje obie kopie. Zdublowane przypisy są usuwane (naprawione), zdublowane nagłówki — nie: `-layout` przeplata je („WYKAZ ŚWIADCZEŃ WYKAZ ŚWIADCZEŃ…”). | Dz.U. 2025 poz. 615 (11 stron); Dz.U. 2021 poz. 1960, s. 5 PDF; M.P. 2023 poz. 1279, s. 2 PDF. | „strona z ukrytą warstwą tekstu (s. … PDF) — wiersz „…” jest w PDF dwa razy…”. |
| `eli.py` (tekst z PDF) | Notka marginesu tekstu ujednoliconego, której nie dało się odciąć od treści (obok kolumny notek stoi szeroki przypis): słowa notki są wtrącone w tekst. Także notka urwana na dole strony pod datą wydruku. | Objaśnienie 1 pod tabelą VAT, Dz.U. 2026 poz. 1263, s. 414 PDF („Dz. Urz. poz. UE1270). L 48”); s. 429, 442; s. 25 i Dz.U. 2022 poz. 1138, s. 47 (notki urwane). | „strona z notką na marginesie, której nie dało się odciąć od treści (s. … PDF)”. |
| `eli.py` (OCR, zeszyty z lat 1990–2011) | Luka albo odwrócenie w numeracji art./§: OCR zgubił albo przekręcił nagłówek („§ '0.”), przesunął znak „§”, łamy są przemieszane. | Dz.U. 1994 poz. 73 (§ 10 wklejony w § 9 ust. 2); Dz.U. 1996 poz. 78 (§ 34); Dz.U. 2004 poz. 1453 (łamy). | „§ 10 brak w odczycie (między § 9 a § 11)”, „§ 40 po § 47 — kolejność jednostek…”. |

## 3. Błąd leży w źródle urzędowym

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

## 4. Niesprawdzone — rzetelność nieznana

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
- **ELI:** reguła dokończenia przypisu z poprzedniej strony (wcięty blok małą literą na dole strony bez odsyłaczy) może
  przenieść do przypisu wcięty blok treści z dołu strony — poza próbą 73 tekstów nie sprawdzono.
- **ELI:** z 6 przypadków „odsyłacz po spacji” opisanych w wydaniu 2.4.0 ponownie sprawdzono tylko 1 (DU 2026/1473).
- **ELI (tabele):** zgodności wierszy nie zmierzono dla tabel bez pionowych linii (metoda porównania z obrazem strony
  ich nie dzieli na komórki) — m.in. tabele załączników VAT w tekstach ujednoliconych z 2026 r.
- **ELI (strony obrócone):** przeliczanie współrzędnych strony obróconej o 90° sprawdzono tylko na jednym PDF
  (Dz.U. 2025 poz. 615, 24 strony, tekst czytany od dołu do góry); obrót w drugą stronę ma tylko test syntetyczny.
- **ELI (ukryta warstwa):** wykrywanie sprawdzono na 3 PDF z lat 2021–2025 składanych w ten sam sposób (InDesign z
  osadzonym PDF); inne sposoby ukrywania tekstu (biały tekst, warstwy opcjonalne) nie były w próbie.
- **ELI (numeracja):** luki w numeracji sprawdzane są tylko w zeszytach 1990–2011 i w PDF odczytanych przez OCR w co
  najmniej połowie stron, z pominięciem aktów zmieniających; w tekstach jednolitych i ujednoliconych oraz w aktach
  zmieniających — nie.
- **ELI (tabele):** obszar tabeli obejmuje kolejne strony z tabelą, więc fragment tekstu między dwiema tabelami na
  sąsiednich stronach też dostaje ostrzeżenie (i blokadę `--strict`); na 13 z 256 zmierzonych stron w obszarach tabel
  nie było niezgodnego wiersza — nie badano, czy przemieszanie jest tam poza wierszami tabeli.
- **UODO → CBOSA:** stanu trzech decyzji nie dało się rozstrzygnąć w CBOSA — DKN.5131.3.2021,
  DKN.5130.4179.2020, DKN.5131.5.2024.

## 5. Dostępność źródeł

Te sytuacje nie dają fałszywych danych — narzędzie kończy się błędem (kod wyjścia 2), nie „brakiem
wyników” — ale mogą uniemożliwić sprawdzenie.

- **CBOSA (orzeczenia.nsa.gov.pl)** bywa niestabilna: 13.11.2026 serwer po kilkudniowej awarii
  dwukrotnie w ciągu godziny zrywał połączenia na kilkanaście sekund.
- **Dziennik dolnośląski** odpowiadał po 66 s na rekord, przy limicie 25 s w narzędziu — `akt DS` kończy
  się wtedy błędem przekroczenia czasu.
- **`pdftotext` bez współrzędnych słów** (stara wersja albo błąd narzędzia): `eli.py` ostrzega, że
  odsyłacze do przypisów nie zostały rozpoznane, a `--strict` blokuje wynik.
