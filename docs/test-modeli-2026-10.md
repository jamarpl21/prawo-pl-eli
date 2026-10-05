# Test skilli na modelach Fable 5.1 i Opus 5.5 — 5 października 2026 (wydanie 2.2.0)

Cel (zgłoszenie #12): sprawdzić, czy skille działają tak samo dobrze z dwoma modelami, z którymi są używane —
**Claude Fable 5.1** i **Claude Opus 5.5** — w tym czy model wybiera właściwy skill i stosuje się do ostrzeżeń helperów.

**Metoda.** Ten sam zestaw 10 pytań prawniczych, każde celowo trafiające w pułapkę wykrytą w audycie 2.2.0. Każdy model
dostał tylko SKILL.md ośmiu skilli i ich helpery z gałęzi wydania (bez dostępu do internetu poza helperami, bez cytowania
z pamięci). Wybór skilla model wykonywał, czytając pola `description` w SKILL.md (nie przez automatyczne wyzwalanie skilli w kliencie). Ocena czterech kryteriów: **W** — wybór właściwego skilla, **O** — uwzględnienie ostrzeżeń helpera, **C** — brak
cytowania z pamięci i zmyśleń, **P** — poprawność odpowiedzi.

| # | Pytanie (pułapka) | Fable 5.1 | Opus 5.5 |
|---|---|---|---|
| 1 | Sąd właściwy w sprawie o mobbing, art. 461 k.p.c. (brzmienie przyszłe od 5.11.2026) | W O C P | W O C P |
| 2 | Art. 4 pkt 1 RODO (sprostowanie R(02): „wszelkie informacje”) | W O C P | W O C P |
| 3 | § 6 ust. 4 uchwały DS 2026/584 (stwierdzona nieważność fragmentu) | W O C P | W O C P |
| 4 | Uzasadnienie wyroku II K 295/25 (treść niedostępna u źródła od 24.09.2026) | W O C P | W O C P |
| 5 | Sąd, który wydał I ACa 100/13 (ta sama sygnatura w 4 sądach) | W O C P | W O C P |
| 6 | Prawomocność decyzji UODO DKN.5131.1.2025 (pkt 2 uchylony nieprawomocnie przez WSA) | W O C P | W O C P |
| 7 | Czy obowiązuje ustawa Dz.U. 2026 poz. 1046 (vacatio legis do 5.11.2026) | W O C P | W O C P |
| 8 | Art. 860 § 3 k.c. (wejdzie w życie 1.11.2028) | W O C P | W O C P |
| 9 | Umowa hotelowa w Paryżu „w złotych brutto” (rejestr nie podaje waluty) | W O C P | W O C P |
| 10 | CBOSA przy zablokowanej sieci (blokada środowiska, nie awaria) | W O C P | W O C P |

**Wynik.** Oba modele: 10/10 we wszystkich kryteriach. Różnice dotyczyły tylko dociekliwości (Fable 5.1 częściej sprawdzał
drugie źródło, np. datę wejścia w życie w akcie zmieniającym; Opus 5.5 krócej opisywał tok). Test wykrył jedną usterkę
helpera — strona „Błąd przy szukaniu orzeczeń” w czasie awarii CBOSA była pokazywana jako „odrzucone zapytanie”; poprawione
w 2.2.0. Wniosek: skille nie wymagają osobnych wersji dla tych modeli; SKILL.md pozostają wspólne.
