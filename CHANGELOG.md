# Historia zmian

## Niewydane

- ELI: `tekst` dla aktów Dz.U. i M.P. 2000–2011 bez HTML (czytanych z ogłoszonego PDF; M.P. z tych lat nie ma HTML wcale) zwraca tekst samego aktu, łam po łamie. Dotąd `pdftotext -layout` stawiał dwa łamy obok siebie i sklejanie wierszy je mieszało („ustawy z dnia § 4. 1. Minimalna norma"), a tekst zawierał też akty wydrukowane na tych samych stronach zeszytu (`tekst DU 2003 991` zaczynał się od załącznika do poz. 990).
- ELI: w aktach Dz.U. i M.P. 2000–2009 z PDF polskie litery są poprawne („Załącznik", „rozporządzenia" zamiast „Za∏àcznik", „rozporzàdzenia"), więc `--fragment` znajduje frazy z polskimi literami. Usuwane są też nagłówki stron zeszytu („Dziennik Ustaw Nr 105 — 7006 — Poz. 990 i 991") i znak wodny www.rcl.gov.pl (2010–2011).
- Na 58 losowych aktach Dz.U. 2000–2011 z HTML (wzorzec) odsetek słów HTML we właściwej kolejności (najdłuższy wspólny podciąg słów) wzrósł z 0,405 do 0,891, a udział słów wyniku w tym podciągu z 0,335 do 0,856. Na 12 losowych aktach M.P. 2000–2011 (bez HTML, więc bez tej miary) znaki „∏", „Ê", „˝" itp. znikły we wszystkich 9 aktach 2000–2008, które je miały, a akt został wycięty we wszystkich 12. Akty z PDF tekstu ujednoliconego (typ U/T) i lata spoza 2000–2011 są czytane jak dotąd.
- Dodano 8 testów na skróconym zeszycie Dz.U. 2003 Nr 105.

## 2.1.0 — 2026-09-20

- Dodano ósmy plugin `prawo-pl-orzeczenia-ms`: wyszukiwanie orzeczeń SA/SO/SR bezpośrednio w Portalu Orzeczeń MS, metryki, pełne uzasadnienia, powołane przepisy, eksport PDF i kanały RSS.
- Dostęp do portalu MS działa przez standardową bibliotekę Pythona z przetestowanym User-Agentem, bez `agent-browser`, JavaScriptu i dodatkowych bibliotek. Blokada F5/TSPD lub niekompletna odpowiedź jest zgłaszana jako błąd, a nie brak orzeczeń.
- Nowy silnik oddziela datę wyroku od daty publikacji, zachowuje indeksy górne i rozpoznaje tekstowe oraz graficzne oznaczenie nieprawomocności i sygnalizuje nieustalony status; `--strict` blokuje pobieranie dokumentu bez potwierdzenia prawomocności.
- Udokumentowano filtry wyszukiwarki, ograniczony zakres RSS oraz dobór źródła MS/SAOS/CBOSA; dodano plugin do obu marketplace’ów i walidacji wydania.
- Dodano 29 testów nowego silnika, opartych na odpowiedziach portalu i przypadkach błędnych; przed wydaniem zweryfikowano także wyszukiwanie, pobieranie treści, PDF i RSS na żywo.

## 2.0.3 — 2026-09-20

- Dodano kontakt przez LinkedIn w polu `author.url` wszystkich siedmiu pluginów, w manifestach Claude i Codex oraz wpisach autorów w marketplace Claude.
- Dodano ten changelog i stałe instrukcje jego prowadzenia w `AGENTS.md`.
- Opisy kolejnych wydań GitHub Release są pobierane z changelogu. Brak wpisu dla wydawanej wersji blokuje publikację.

## 2.0.2 — 2026-09-20

- ELI: tryb `--strict` blokuje wynik, gdy odpowiedź endpointu odniesień ma nieprawidłową strukturę. W zwykłym trybie tekstowi towarzyszy ostrzeżenie o niezweryfikowanej aktualności.
- ELI: odpowiedzi JSON i inne dane niebędące tekstem z endpointu HTML nie są już wyświetlane jako treść aktu.
- EUR-Lex: brak znalezionych konsolidacji nie jest już przedstawiany jako dowód, że akt nie był zmieniany; komunikat kieruje do sprawdzenia zmian i sprostowań.
- Rejestr umów: komendy `najnowsze` i `szukaj` sprawdzają strukturę odpowiedzi. Obiekt błędu ani pusta pierwsza strona przy dodatniej liczbie trafień nie są traktowane jako poprawny wynik, także w trybie JSON.
- Dodano testy regresyjne powyższych przypadków.
- Dodano kontakt z Krzysztofem Gibkiem przez LinkedIn w README.
