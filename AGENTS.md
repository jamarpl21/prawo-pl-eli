# Instrukcje projektu

## Changelog i wydania

Prowadź `CHANGELOG.md` w głównym katalogu repozytorium. Jest obowiązkowy: CI (`tools/release_notes.py`)
blokuje wydanie bez wpisu dla wydawanej wersji.

- Przy zmianach działania, poprawkach błędów i istotnych zmianach dokumentacji aktualizuj sekcję `Niewydane`; jeśli plik nie istnieje, utwórz go.
- Opisuj po polsku konkretne zmiany i ich skutki dla użytkownika, nie tylko nazwy commitów.
- Przed wydaniem przenieś gotowe wpisy do sekcji z numerem wersji i datą wydania.
- Umieszczaj opis zmian danej wersji również w GitHub Release. Automatyczny link „Full Changelog” do porównania kodu nie zastępuje opisu.
- Sprawdź po publikacji, że opis wydania rzeczywiście zawiera te informacje.
- Wpisy historyczne opieraj na zweryfikowanych zmianach; nie dopisuj niepotwierdzonych informacji.
- Przed wydaniem zaktualizuj README: liczby, przykłady komend, numer wersji w sekcji „Wersjonowanie” i w przykładzie paczki ZIP. Kolejność kroków wydania opisuje README, sekcja „Wersjonowanie”.

## Audyty i nowe ścieżki odczytu źródeł

- Każdy punkt z list „Luki” i „Zalecane kolejne testy” raportu audytu (np. `docs/audyt-merytoryczny-2026-08.md`, sekcja D) wykonaj albo świadomie odrzuć. Oba wyniki wpisz do `CHANGELOG.md`: wykonanie ze skutkiem, odrzucenie z uzasadnieniem. Punkt bez wpisu pozostaje otwarty, a raport ma notę o stanie z listą otwartych punktów.
- Każda nowa ścieżka odczytu źródła (nowy format, nowe API, PDF zamiast HTML, OCR) wymaga testów na próbkach z każdej epoki formatu tego źródła, np. Dz.U. i M.P. z lat 1990–1999 (OCR), 2000–2011 (PDF bez HTML) i od 2012 r. Wynik porównaj z niezależnym źródłem urzędowym i podaj w CHANGELOG liczbę próbek oraz miarę zgodności.

## Repozytorium publiczne

- Nie wpisuj do kodu, dokumentacji, commitów ani opisów na GitHubie nazw prywatnych projektów ani lokalnych ścieżek.
