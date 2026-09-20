# Historia zmian

## Niewydane

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
