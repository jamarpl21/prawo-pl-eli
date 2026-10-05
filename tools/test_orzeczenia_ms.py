#!/usr/bin/env python3
"""Offline regression tests using trimmed public responses captured 2026-09-20."""
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ms', ROOT / 'plugins/prawo-pl-orzeczenia-ms/skills/prawo-pl-orzeczenia-ms/scripts/orzeczenia_ms.py')
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)
FIXTURES = ROOT / 'tools/fixtures/orzeczenia_ms'
ID = '152010000000503_I_C_002715_2021_Uz_2021-09-29_001'
OTHER_ID = '153500000000503_I_ACa_001410_2023_Uz_2023-12-29_001'


def fixture(name):
    return (FIXTURES / name).read_text()


class Parsing(unittest.TestCase):
    def test_real_search(self):
        r = ms.parse_search(fixture('search.html'), 'url')
        self.assertEqual(r['liczba_wynikow'], 1)
        row = r['wyniki'][0]
        self.assertEqual(row['id'], OTHER_ID)
        self.assertEqual(row['sygnatura'], 'I ACa 1410/23')
        self.assertEqual(row['sad'], 'Sąd Apelacyjny w Poznaniu')
        self.assertEqual(row['data_orzeczenia'], '2023-09-18')
        self.assertEqual(row['data_publikacji'], '2024-09-30')

    def test_empty_is_distinct_from_unknown(self):
        with self.assertRaises(ms.Empty):
            ms.parse_search(fixture('empty.html'), 'url')
        for source in ['<title>Request Rejected</title>', '<html>Przerwa techniczna</html>', fixture('search.html').replace('big_number', 'new-layout')]:
            with self.subTest(source=source[:35]), self.assertRaises(ms.Unknown):
                ms.parse_search(source, 'url')

    def test_incomplete_page_not_success(self):
        with self.assertRaises(ms.Unknown):
            ms.parse_search(fixture('search.html').replace('big_number">1<', 'big_number">20<'), 'url')

    def test_page_number_matters(self):
        with self.assertRaises(ms.Unknown):
            ms.parse_search(fixture('search.html'), 'url', number=2)

    def test_meta_dates_do_not_come_from_description_or_id(self):
        r = ms.parse_meta(fixture('details.html'), ID)
        self.assertEqual(r['data_orzeczenia'], '2021-09-29')
        self.assertEqual(r['data_publikacji'], '2024-08-22')
        self.assertEqual(r['sygnatura'], 'I C 2715/21')
        self.assertIs(r['prawomocne'], False)  # official CSS renders ORZECZENIE NIEPRAWOMOCNE

    def test_absence_of_graphic_is_not_proof_of_finality(self):
        source = fixture('details.html').replace('single_result invalid', 'single_result')
        self.assertIsNone(ms.parse_meta(source, ID)['prawomocne'])

    def test_explicit_finality(self):
        for text, expected in [('Orzeczenie nieprawomocne', False), ('Orzeczenie prawomocne', True)]:
            source = fixture('details.html').replace('single_result invalid', 'single_result').replace('<dl>', '<p>' + text + '</p><dl>')
            self.assertIs(ms.parse_meta(source, ID)['prawomocne'], expected)

    def test_missing_metadata_rejected(self):
        source = fixture('details.html').replace('<dt>Sąd:</dt>', '<dt>Organ:</dt>')
        with self.assertRaises(ms.Unknown):
            ms.parse_meta(source, ID)

    def test_wrong_document_rejected(self):
        for name, parser in [('details.html', ms.parse_meta), ('content.html', ms.parse_content)]:
            with self.subTest(name=name), self.assertRaises(ms.Unknown):
                parser(fixture(name), OTHER_ID)

    def test_content_is_full_not_navigation(self):
        r = ms.parse_content(fixture('content.html'), ID)
        self.assertIn('UZASADNIENIE', r['tresc'])
        self.assertIn('oddala powództwo', r['tresc'])
        self.assertNotIn('Kanały RSS', r['tresc'])
        self.assertNotIn('Podmiot udostępniający', r['tresc'])
        self.assertTrue(r['pdf_url'].startswith(ms.BASE + '/content.pdffile/'))

    def test_inline_superscripts_and_paragraphs(self):
        root = ms.DOM('<div><p>art. 417<sup>1</sup> § 2 k.c.</p><p>Drugi &amp; trzeci.</p><script>bad()</script></div>').root
        self.assertEqual(root.plain(), 'art. 417¹ § 2 k.c.\nDrugi & trzeci.')

    def test_regulations(self):
        items = ms.parse_regulations(fixture('regulations.html'), ID)
        self.assertEqual(len(items), 2)
        self.assertIn('Kodeks cywilny', items[1]['tytul'])
        with self.assertRaises(ms.Unknown):
            ms.parse_regulations(fixture('details.html'), ID)
        with self.assertRaises(ms.Unknown):  # lista przepisów innego orzeczenia
            ms.parse_regulations(fixture('regulations.html'), OTHER_ID)

    def test_rss_links_dates_and_limit(self):
        r = ms.parse_rss(fixture('rss.xml'), 'url', 1)
        self.assertEqual(r['liczba_w_kanale'], 2)
        self.assertEqual(len(r['wyniki']), 1)
        self.assertEqual(r['wyniki'][0]['data_publikacji'], '2026-09-17T16:40:32Z')
        self.assertTrue(r['wyniki'][0]['url'].startswith('https://orzeczenia.ms.gov.pl/details/'))

    def test_invalid_rss_fails(self):
        for source in ['<html>error</html>', '<rss><channel/></rss>', '<!DOCTYPE rss><rss/>', fixture('rss.xml').replace('<pubDate>', '<other>').replace('</pubDate>', '</other>')]:
            with self.subTest(source=source[:40]), self.assertRaises(ms.Unknown):
                ms.parse_rss(source, 'url', 2)


class Inputs(unittest.TestCase):
    def test_unicode_encoding_is_utf16_and_no_path_injection(self):
        self.assertEqual(ms.slot('I ACa 1410/23'), 'I$0020ACa$00201410$002f23')
        self.assertEqual(ms.slot('ś😀'), '$015b$d83d$de00')
        self.assertEqual(ms.slot('$N'), '$0024N')
        self.assertEqual(ms.slot(''), '$N')
        self.assertEqual(ms.slot(0), '0')

    def test_filter_contract(self):
        args = ms.parser().parse_args(['szukaj', 'dobra', '--sad', '15050500', '--wydzial', '03', '--apelacja', '1505', '--okreg', '150505', '--od', '2021-01-01', '--do', '2021-12-31', '--sedzia', 'Kowalski', '--funkcja', 'CHAIRMAN', '--haslo', 'Dobra osobiste', '--przepis', 'art. 23', '--tezowane', '--istotnosc', '0'])
        parts = ms.search_url(args).split('/advanced/')[1].split('/')
        self.assertEqual(parts, ['dobra', '$N', '15050500', '03', '1505', '150505', '$N', '2021-01-01', '2021-12-31', 'Kowalski', 'CHAIRMAN', 'Dobra$0020osobiste', 'art.$002023', '$002a', '0', 'score', 'descending', '1'])

    def test_bad_dates_and_range(self):
        for date in ['2026-02-30', '2026-1-1', '2026']:
            with self.assertRaises(Exception):
                ms.date_arg(date)
        args = ms.parser().parse_args(['szukaj', '--od', '2026-09-20', '--do', '2026-09-01'])
        with self.assertRaises(ValueError):
            ms.search_url(args)

    def test_safe_links(self):
        self.assertEqual(ms.safe_url('http://orzeczenia.ms.gov.pl/a'), ms.BASE + '/a')
        for url in ['https://evil.test/a', '//evil.test', 'http://orzeczenia.ms.gov.pl.evil.test/a', 'https://name@orzeczenia.ms.gov.pl/a', 'file:///tmp/a']:
            with self.subTest(url=url), self.assertRaises(ms.Unknown):
                ms.safe_url(url)

    def test_redirects_stay_on_trusted_https(self):
        handler = ms.Redirect()
        req = urllib.request.Request(ms.BASE)
        redir = handler.redirect_request(req, None, 302, '', {}, 'http://orzeczenia.ms.gov.pl/content/x')
        self.assertEqual(redir.full_url, ms.BASE + '/content/x')
        with self.assertRaises(ms.Unknown):
            handler.redirect_request(req, None, 302, '', {}, 'https://evil.test/a')

    def test_document_id_and_url(self):
        self.assertEqual(ms.doc_id(ms.doc_url('details', ID)), ID)
        for value in ['../etc', 'I C 2715/21', ID + '?q=a', 'https://evil.test/details/' + ID]:
            with self.assertRaises(ValueError):
                ms.doc_id(value)


class TransportAndCLI(unittest.TestCase):
    def response(self, payload):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload
        return response

    def test_challenge_and_rejection_http200_fail(self):
        for body in [b'<script src="/TSPD/x">', '<title>Połączenie odrzucone</title>'.encode(), b'<title>Request Rejected</title>']:
            client = ms.Client()
            with patch.object(client.opener, 'open', return_value=self.response(body)), self.assertRaises(ms.Unknown):
                client.get(ms.BASE)

    def test_pdf_signature(self):
        for body in [b'<html>challenge</html>', b'not a pdf']:
            client = ms.Client()
            with patch.object(client.opener, 'open', return_value=self.response(body)), self.assertRaises(ms.Unknown):
                client.get(ms.BASE, pdf=True)
        client = ms.Client()
        with patch.object(client.opener, 'open', return_value=self.response(b'%PDF-1.4\n')):
            self.assertEqual(client.get(ms.BASE, pdf=True), b'%PDF-1.4\n')

    def test_user_agent_and_timeout(self):
        client = ms.Client()
        with patch.object(client.opener, 'open', return_value=self.response(b'text')) as request:
            client.get(ms.BASE)
        self.assertEqual(request.call_args.args[0].get_header('User-agent'), 'curl/8.7.1')
        self.assertEqual(request.call_args.kwargs['timeout'], 40)

    def test_network_failure_is_unknown(self):
        client = ms.Client()
        with patch.object(client.opener, 'open', side_effect=urllib.error.URLError('offline')), self.assertRaises(ms.Unknown):
            client.get(ms.BASE)

    def test_cli_empty_unknown_no_stdout(self):
        for exc, code in [(ms.Empty('zero'), 1), (ms.Unknown('blocked'), 2)]:
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.object(ms, 'run', side_effect=exc), redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(ms.main(['szukaj', '--json']), code)
            self.assertEqual(stdout.getvalue(), '')
            self.assertTrue(stderr.getvalue())

    def test_global_flags_before_and_after_command(self):
        for argv in [['--json', '--strict', 'metryka', ID], ['metryka', ID, '--json', '--strict']]:
            args = ms.parser().parse_args(argv)
            self.assertTrue(args.json)
            self.assertTrue(args.strict)

    def test_strict_blocks_unknown_finality_before_content(self):
        client = unittest.mock.Mock()
        client.get.return_value = fixture('details.html').replace('single_result invalid', 'single_result')
        with self.assertRaises(ms.Unknown):
            ms.run(ms.parser().parse_args(['orzeczenie', ID, '--strict']), client)
        self.assertEqual(client.get.call_count, 1)

    def test_pdf_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = pathlib.Path(tmp) / 'result.pdf'
            output.write_bytes(b'original')
            client = unittest.mock.Mock()
            client.get.side_effect = [fixture('details.html'), fixture('content.html'), b'%PDF-1.4\n']
            with self.assertRaises(FileExistsError):
                ms.run(ms.parser().parse_args(['pdf', ID, '--plik', str(output)]), client)
            self.assertEqual(output.read_bytes(), b'original')

    def test_successful_cli_json_is_parseable_with_warnings(self):
        stdout = io.StringIO()
        with patch.object(ms.Client, 'get', return_value=fixture('details.html')), redirect_stdout(stdout):
            self.assertEqual(ms.main(['metryka', ID, '--json']), 0)
        result = json.loads(stdout.getvalue())
        self.assertIs(result['prawomocne'], False)
        self.assertTrue(result['uwagi'])


FINAL_ID = '154510000003006_VI_Ka_001622_2025_Uz_2026-06-08_001'
# Metryka VI Ka 1622/25 z żywego portalu (2026-10-05), przycięta do kształtu details.html.
DETAILS_FINAL = (
    '<!DOCTYPE html><html><head><title>Szczegóły orzeczenia VI Ka 1622/25 - Portal Orzeczeń Sądów Powszechnych</title></head><body>'
    '<h2>Wyszukiwanie</h2>'
    '<div class="grid9 simple single" id="content"><h2>VI Ka 1622/25 - uzasadnienie Sąd Okręgowy Warszawa-Praga w Warszawie z 2026-06-08</h2>'
    '<ul class="tabs"><li class="active"><a href="/details/$N/' + FINAL_ID + '">Metryka</a></li>'
    '<li class=""><a href="/content/$N/' + FINAL_ID + '">Treść</a></li>'
    '<li class=""><a href="/regulations/$N/' + FINAL_ID + '">Powołane przepisy</a></li>'
    '<li class=""><a href="/similardocs/$N/' + FINAL_ID + '">Orzeczenia podobne</a></li></ul>'
    '<div class="single_wrapper"><div class="single_result"><dl><dt>Tytuł:</dt><dd>Sąd Okręgowy Warszawa-Praga w Warszawie z 2026-06-08</dd>'
    '<dt>Data orzeczenia:</dt><dd>8 czerwca 2026</dd><dt>Data publikacji:</dt><dd>9 czerwca 2026</dd>'
    '<dt>Data uprawomocnienia:</dt><dd>8 czerwca 2026</dd><dt>Sygnatura:</dt><dd>VI Ka 1622/25</dd>'
    '<dt>Sąd:</dt><dd>Sąd Okręgowy Warszawa-Praga w Warszawie</dd><dt>Wydział:</dt><dd>VI Wydział Karny Odwoławczy</dd>'
    '<dt>Hasła tematyczne:</dt><dd>\nWyrok łączny\n</dd><dt>Podstawa prawna:</dt><dd class="nine columns omega">art. 85 kk, art. 4 § 1 kk</dd></dl>'
    '</div></div></div></body></html>')


class FinalityDate(unittest.TestCase):
    """Regresja 2.1.1: pole „Data uprawomocnienia” było pomijane — prawomocne: null i --strict blokował."""

    def test_final_date_sets_finality(self):
        r = ms.parse_meta(DETAILS_FINAL, FINAL_ID)
        self.assertIs(r['prawomocne'], True)
        self.assertEqual(r['data_uprawomocnienia'], '2026-06-08')
        self.assertFalse(any('nie potwierdza' in u for u in r['uwagi']))
        self.assertTrue(any('Data uprawomocnienia' in u for u in r['uwagi']))
        self.assertFalse(any('zakładek' in u for u in r['uwagi']))  # na żywo VI Ka 1622/25 ma 4 zakładki

    def test_reasons_document_date_warning(self):
        r = ms.parse_meta(DETAILS_FINAL, FINAL_ID)
        self.assertEqual(r['typ'], 'uzasadnienie')
        self.assertTrue(any('bywa datą uzasadnienia' in u for u in r['uwagi']))
        # „wyrok z uzasadnieniem” to nie samo uzasadnienie — bez tej uwagi
        self.assertFalse(any('bywa datą uzasadnienia' in u for u in ms.parse_meta(fixture('details.html'), ID)['uwagi']))

    def test_contradiction_keeps_nonfinal(self):
        source = DETAILS_FINAL.replace('class="single_result"', 'class="single_result invalid"')
        r = ms.parse_meta(source, FINAL_ID)
        self.assertIs(r['prawomocne'], False)
        self.assertTrue(any('Sprzeczność' in u for u in r['uwagi']))

    def test_without_field_still_unknown(self):
        source = DETAILS_FINAL.replace('<dt>Data uprawomocnienia:</dt><dd>8 czerwca 2026</dd>', '')
        r = ms.parse_meta(source, FINAL_ID)
        self.assertIsNone(r['prawomocne'])
        self.assertIsNone(r['data_uprawomocnienia'])

    def test_strict_passes_with_final_date(self):
        client = unittest.mock.Mock()
        client.get.return_value = DETAILS_FINAL
        r = ms.run(ms.parser().parse_args(['metryka', FINAL_ID, '--strict']), client)
        self.assertIs(r['prawomocne'], True)



# --- Regresje audytu 2026-10-05 (fixtury przycięte z odpowiedzi portalu z tego dnia) ---
REASONS_ID = '152510000006527_XIII_Ga_000696_2025_Uz_2026-09-25_001'
KEYWORDS_ID = '154505000005127_XVII_AmE_000302_2024_Uz_2026-01-28_001'
XXIU_ID = '154505000006315_XXI_U_002364_2025_Uz_2026-06-17_001'
ACA10_ID = '155000000000503_I_ACa_000010_2026_Uz_2026-04-17_001'
DETAILS_ACA10 = (
    '<!DOCTYPE html><html><head><title>Szczegóły orzeczenia I ACa 10/26 - Portal Orzeczeń Sądów Powszechnych</title></head><body>'
    '<div class="grid9 simple single" id="content"><h2>I ACa 10/26 - zarządzenie, wyrok, uzasadnienie Sąd Apelacyjny we Wrocławiu z 2026-04-07</h2>'
    '<ul class="tabs"><li class="active"><a href="/details/$N/' + ACA10_ID + '">Metryka</a></li>'
    '<li class=""><a href="/content/$N/' + ACA10_ID + '">Treść</a></li></ul><div class="single_wrapper"><div class="single_result"><dl>'
    '<dt>Tytuł:</dt><dd>Sąd Apelacyjny we Wrocławiu z 2026-04-07</dd><dt>Data orzeczenia:</dt><dd>7 kwietnia 2026</dd>'
    '<dt>Data publikacji:</dt><dd>2 października 2026</dd><dt>Data uprawomocnienia:</dt><dd>7 kwietnia 2026</dd>'
    '<dt>Sygnatura:</dt><dd>I ACa 10/26</dd><dt>Sąd:</dt><dd>Sąd Apelacyjny we Wrocławiu</dd>'
    '<dt>Hasła tematyczne:</dt><dd>\nKlauzule abuzywne\n</dd>'
    '<dt>Podstawa prawna:</dt><dd class="nine columns omega">art. 385 <SUP>1</SUP> i nast. kc.</dd></dl></div></div></div></body></html>')


def http_error(code, name):
    return urllib.error.HTTPError(ms.BASE + '/x', code, 'err', {}, io.BytesIO(fixture(name).encode()))


class Audit20261005(unittest.TestCase):
    def response(self, payload):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload
        return response

    def test_court_rss_links_to_subportal_map_to_central_id(self):
        # Przed poprawką: „Link prowadzi poza centralny portal MS.” dla każdego kanału sądu (rss --sad).
        r = ms.parse_rss(fixture('rss_court.xml'), 'url', 5)
        first = r['wyniki'][0]
        self.assertEqual(first['id'], '150500000000503_I_ACa_001461_2023_Uz_2025-07-29_003')
        self.assertEqual(first['url'], ms.BASE + '/details/$N/150500000000503_I_ACa_001461_2023_Uz_2025-07-29_003')
        self.assertTrue(first['link_zrodlowy'].startswith('http://orzeczenia.bialystok.sa.gov.pl/details/'))
        self.assertEqual(first['sad'], 'Sąd Apelacyjny w Białymstoku')
        self.assertTrue(any('podportal' in u for u in r['uwagi']))
        self.assertTrue(any('2 ostatnich pozycji' in u for u in r['uwagi']))

    def test_rss_foreign_hosts_still_rejected(self):
        src = fixture('rss_court.xml')
        for host in ['evil.test', 'orzeczenia.bialystok.sa.gov.pl.evil.test', 'orzeczenia.example.gov.pl']:
            with self.subTest(host=host), self.assertRaises(ms.Unknown):
                ms.parse_rss(src.replace('orzeczenia.bialystok.sa.gov.pl/details', host + '/details'), 'url', 5)

    def test_incomplete_read_is_unknown_not_empty(self):
        # IncompleteRead nie jest OSError — wcześniej uciekał i dawał kod wyjścia 1 („brak wyników”).
        import http.client
        client = ms.Client()
        response = self.response(b'')
        response.read.side_effect = http.client.IncompleteRead(b'<html>', 5000)
        with patch.object(client.opener, 'open', return_value=response), self.assertRaises(ms.Unknown):
            client.get(ms.BASE)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ms, 'run', side_effect=KeyError('x')), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', 'x', '--json']), 2)
        self.assertEqual(stdout.getvalue(), '')

    def test_truncated_html_is_unknown(self):
        truncated = fixture('content.html')[:5000]
        with self.assertRaises(ms.Unknown):
            ms.parse_content(truncated, ID)

    def test_portal_error_pages_recognised(self):
        for code, name, reason in [(400, 'error400.html', 'Błąd danych'), (404, 'error404.html', 'Strona o podanym adresie nie istnieje')]:
            client = ms.Client()
            with self.subTest(code=code), patch.object(client.opener, 'open', side_effect=http_error(code, name)):
                with self.assertRaises(ms.PortalError) as ctx:
                    client.get(ms.BASE)
                self.assertEqual(ctx.exception.reason, reason)

    def test_content_data_error_explained(self):
        # Treść świeżych publikacji: portal (także w przeglądarce) zwraca HTTP 400 „Błąd danych”.
        client = unittest.mock.Mock()
        client.get.side_effect = [fixture('details_reasons.html'), ms.PortalError(400, 'Błąd danych', 'u')]
        with self.assertRaises(ms.Unknown) as ctx:
            ms.run(ms.parser().parse_args(['orzeczenie', REASONS_ID]), client)
        self.assertIn('Portal nie udostępnia treści', str(ctx.exception))
        self.assertIn('Błąd danych', str(ctx.exception))

    def test_unknown_id_404_explained(self):
        client = unittest.mock.Mock()
        client.get.side_effect = ms.PortalError(404, 'Strona o podanym adresie nie istnieje', 'u')
        with self.assertRaises(ms.Unknown) as ctx:
            ms.run(ms.parser().parse_args(['metryka', REASONS_ID]), client)
        self.assertIn('nie zna dokumentu', str(ctx.exception))

    def test_reasons_and_order_document_warns_about_date(self):
        # XIII Ga 696/25 „zarządzenie, uzasadnienie”: Data orzeczenia 2026-09-25, a wyrok zapadł 2026-09-11.
        r = ms.parse_meta(fixture('details_reasons.html'), REASONS_ID)
        self.assertEqual(r['typ'], 'zarządzenie, uzasadnienie')
        self.assertEqual(r['data_orzeczenia'], '2026-09-25')
        self.assertTrue(any('bywa datą uzasadnienia' in u for u in r['uwagi']))
        self.assertTrue(any('Powołane przepisy' in u for u in r['uwagi']))  # dokument jeszcze nieprzetworzony
        # „zarządzenie, wyrok, uzasadnienie” zawiera sentencję — bez uwagi o dacie
        r = ms.parse_meta(DETAILS_ACA10, ACA10_ID)
        self.assertEqual(r['typ'], 'zarządzenie, wyrok, uzasadnienie')
        self.assertFalse(any('bywa datą uzasadnienia' in u for u in r['uwagi']))
        self.assertEqual(r['metryka']['Podstawa prawna'], 'art. 385 ¹ i nast. kc.')

    def test_relevance_read_from_portal_script(self):
        # Przed poprawką „Istotność”: "" mimo 5 gwiazdek (relevanceInit w skrypcie strony).
        r = ms.parse_meta(fixture('details_reasons.html'), REASONS_ID)
        self.assertEqual(r['metryka']['Istotność'], '5')

    def test_keywords_and_source_newlines(self):
        r = ms.parse_meta(fixture('details_keywords.html'), KEYWORDS_ID)
        self.assertEqual(r['metryka']['Hasła tematyczne'], 'Energetyczne prawo, Koncesja i taryfy')
        self.assertNotIn('Istotność', r['metryka'])
        tresc = ms.parse_content(fixture('content_whitespace.html'), XXIU_ID)['tresc']
        self.assertTrue(tresc.startswith('Sygn. akt XXI U 2364/25\nWYROK\n'), tresc[:60])
        self.assertIn('art. 477⁽¹⁴⁾ § 1 k.p.c.', tresc)  # PDF: „477( 14) § 1”

    def test_empty_regulations_list_warns(self):
        client = unittest.mock.Mock()
        client.get.side_effect = [DETAILS_ACA10, fixture('regulations_empty.html')]
        r = ms.run(ms.parser().parse_args(['przepisy', ACA10_ID]), client)
        self.assertEqual(r['powolane_przepisy'], [])
        self.assertTrue(any('NIE znaczy' in u and '385 ¹' in u for u in r['uwagi']))

    def test_search_collision_nonfinal_flags_and_strict_note(self):
        client = unittest.mock.Mock()
        client.get.return_value = fixture('search_collision.html')
        r = ms.run(ms.parser().parse_args(['sygnatura', 'II', 'K', '1/20', '--strict']), client)
        self.assertEqual(r['liczba_wynikow'], 3)
        self.assertEqual([w['oznaczenie_nieprawomocne'] for w in r['wyniki']], [True, True, None])
        self.assertEqual({w['sad'] for w in r['wyniki']}, {'Sąd Rejonowy w Zambrowie', 'Sąd Rejonowy w Giżycku', 'Sąd Okręgowy w Koninie'})
        self.assertTrue(any('występuje w 3 sądach' in u for u in r['uwagi']))
        self.assertTrue(any('2 z 3 pozycji' in u for u in r['uwagi']))
        self.assertTrue(any('--strict nie filtruje' in u for u in r['uwagi']))

    def test_page_beyond_last_is_error_not_empty(self):
        import re
        no_rows = re.sub(r'<section id="results">.*</section>', '<section id="results"></section>', fixture('search_collision.html'), flags=re.S)
        with self.assertRaises(ValueError) as ctx:
            ms.parse_search(no_rows, 'url', number=5)
        self.assertIn('ostatnia strona to 1', str(ctx.exception))
        self.assertNotIsInstance(ctx.exception, ms.Empty)

    def test_empty_query_rejected_before_request(self):
        client = unittest.mock.Mock()
        with self.assertRaises(ValueError):
            ms.run(ms.parser().parse_args(['szukaj', '--sort', 'datapublikacji']), client)
        client.get.assert_not_called()

    def test_real_tspd_challenge_is_unknown(self):
        client = ms.Client()
        with patch.object(client.opener, 'open', return_value=self.response(fixture('tspd.html').encode())), self.assertRaises(ms.Unknown):
            client.get(ms.BASE)

    def test_strict_message_distinguishes_nonfinal(self):
        client = unittest.mock.Mock()
        client.get.return_value = fixture('details.html')
        with self.assertRaises(ms.Unknown) as ctx:
            ms.run(ms.parser().parse_args(['metryka', ID, '--strict']), client)
        self.assertIn('oznacza orzeczenie jako nieprawomocne', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
