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
        # HTTP 400 „Błąd danych” na treści (awaria obserwowana 2026-10-05; 2026-11-13 już nie występowała):
        # komunikat ma mówić, co robić, i nie twierdzić, że awaria trwa.
        client = unittest.mock.Mock()
        client.get.side_effect = [fixture('details_reasons.html'), ms.PortalError(400, 'Błąd danych', 'u')]
        with self.assertRaises(ms.Unknown) as ctx:
            ms.run(ms.parser().parse_args(['orzeczenie', REASONS_ID]), client)
        message = str(ctx.exception)
        self.assertIn('Portal nie wydał treści', message)
        self.assertIn('Błąd danych', message)
        self.assertIn('Ponów później', message)
        self.assertIn('metryka', message)
        self.assertIn('NIE odtwarzaj treści', message)
        for stale in ('24.09', '2026-10-05', 'awaria', 'SAOS'):
            self.assertNotIn(stale, message)
        client.get.side_effect = [fixture('details_reasons.html'), ms.PortalError(400, 'Błąd danych', 'u')]
        with self.assertRaises(ms.Unknown) as ctx:
            ms.run(ms.parser().parse_args(['przepisy', REASONS_ID]), client)
        self.assertIn('Portal nie wydał listy powołanych przepisów', str(ctx.exception))
        self.assertIn('Podstawa prawna', str(ctx.exception))
        self.assertNotIn('wniosek o odpis', str(ctx.exception))

    def test_content_data_error_exit_code_2_no_stdout(self):
        def get(self, url, pdf=False):
            if '/content/' in url:
                raise ms.PortalError(400, 'Błąd danych', url)
            return fixture('details_reasons.html')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ms.Client, 'get', get), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['orzeczenie', REASONS_ID, '--json']), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('BŁĄD (UNKNOWN)', stderr.getvalue())
        self.assertIn('Błąd danych', stderr.getvalue())

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
        # Fixtura z 2026-10-05 nie ma zakładek „Powołane przepisy”/„Orzeczenia podobne”; 2026-11-13 brak
        # zakładek nie przewidywał już braku treści (7/7 z treścią) — silnik nie dodaje o tym uwagi.
        self.assertFalse(any('zakładek' in u for u in r['uwagi']))
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


def search_page(total, rows=10):
    """search.html z `rows` pozycjami i `total` trafieniami (pełna strona listy portalu)."""
    import re
    source = fixture('search.html')
    row = re.search(r'<tr class="t-first t-last">.*</tr>', source, re.S).group(0)
    return source.replace(row, row * rows).replace('big_number">1<', 'big_number">%d<' % total)


class Paging20261113(unittest.TestCase):
    """Pomiar 2026-11-13: przy remisach klucza strony powtarzają i gubią pozycje (także przy --sort data)."""

    def search(self, *argv, total=72, rows=10):
        client = unittest.mock.Mock()
        client.get.return_value = search_page(total, rows)
        return ms.run(ms.parser().parse_args(['szukaj', '--sad', '15051510'] + list(argv)), client)

    def paging_notes(self, result):
        return [u for u in result['uwagi'] if 'więcej niż jedną stronę' in u]

    def test_unstable_sorts_warn_and_point_to_all_pages_mode(self):
        for sort in ('score', 'istotnosc', 'datapublikacji'):
            with self.subTest(sort=sort):
                notes = self.paging_notes(self.search('--sort', sort))
                self.assertEqual(len(notes), 1)
                self.assertIn('--sort ' + sort + ' jest niestabilna', notes[0])
                self.assertIn('Pełną listę (72 trafienia) pobierz opcją --wszystkie', notes[0])
                self.assertIn('Nie składaj jej ręcznie', notes[0])
        notes = self.paging_notes(self.search())  # domyślne score
        self.assertIn('--sort score jest niestabilna', notes[0])
        self.assertNotIn('None', notes[0])

    def test_over_limit_note_says_narrow_the_query(self):
        notes = self.paging_notes(self.search(total=3192))
        self.assertIn('Pełnej listy (3192 trafienia) nie pobierzesz', notes[0])
        self.assertIn('limit 200', notes[0])
        self.assertIn('--wydzial', notes[0])
        self.assertEqual(ms.hits(205), '205 trafień')
        self.assertEqual(ms.hits(212), '212 trafień')
        self.assertEqual(ms.hits(1), '1 trafienie')

    def test_data_sort_still_warns_about_date_ties(self):
        for direction in ('descending', 'ascending'):
            with self.subTest(direction=direction):
                notes = self.paging_notes(self.search('--sort', 'data', '--kierunek', direction, '--strona', '2'))
                self.assertEqual(len(notes), 1)
                self.assertIn('także przy --sort data', notes[0])
                self.assertIn('--wszystkie', notes[0])

    def test_single_page_result_has_no_paging_note(self):
        for total in (1, 10):
            with self.subTest(total=total):
                self.assertFalse(self.paging_notes(self.search('--sort', 'score', total=total, rows=total)))
        self.assertTrue(self.paging_notes(self.search('--sort', 'score', total=11)))

    def test_default_sort_unchanged_on_later_pages(self):
        # Domyślne sortowanie nie zależy od numeru strony (strona 1 po score + strona 2 po dacie = gorsza suma).
        for page in ('1', '2'):
            args = ms.parser().parse_args(['szukaj', 'x', '--strona', page])
            self.assertEqual(ms.search_url(args).split('/')[-3:], ['score', 'descending', page])

    def test_signature_search_gets_note_too(self):
        client = unittest.mock.Mock()
        client.get.return_value = search_page(15)
        r = ms.run(ms.parser().parse_args(['sygnatura', 'I', 'ACa', '1410/23']), client)
        self.assertTrue(self.paging_notes(r))


def row_html(ident, date, nonfinal=False, typ='wyrok z uzasadnieniem'):
    flag = '<p>Orzeczenie nieprawomocne</p>' if nonfinal else ''
    return ('<tr><td class="title"><div class="single_result"><div class="title"><h4><a href="/details/$N/%s">I C %s</a></h4>'
            '<p>%s</p><p>Sąd Rejonowy w Giżycku</p>%s<p>Data orzeczenia: %s</p><p>Data publikacji: 2025-01-01</p></div>'
            '</div></td></tr>') % (ident, ident[-6:], typ, flag, date)


def list_page(total, rows):
    """Strona listy portalu: `rows` = [(id, data, nieprawomocne), …], `total` w .big_number."""
    return ('<!DOCTYPE html><html><head><title>Portal Orzeczeń Sądów Powszechnych</title></head><body>'
            '<span class="big_number">%d</span><section id="results"><table><tbody>%s</tbody></table></section>'
            '</body></html>') % (total, ''.join(row_html(*r) for r in rows))


def item(n, date=None):
    """Pozycja n: ID zgodne z formatem portalu, kilka pozycji z tą samą datą (remisy klucza)."""
    return ('15051510000%04d_II_K_%06d_2024_Uz_2024-01-01_001' % (n, n), date or '2024-%02d-%02d' % (1 + n // 28 % 12, 1 + n % 28 // 3), False)


class FakePortal:
    """Portal z mokowaną listą: `layout(round, direction, page)` → (liczba_wynikow, [numery pozycji])."""

    def __init__(self, layout, items=None):
        self.layout, self.calls, self.round = layout, [], 0
        self.items = items or {}

    def get(self, url):
        sort, direction, page = url.split('/')[-3:]
        page = int(page)
        if page == 1:
            self.round += 1
        self.calls.append((sort, direction, page))
        total, numbers = self.layout(self.round, direction, page)
        if total == 0:
            return fixture('empty.html')
        return list_page(total, [self.items.get(n) or item(n) for n in numbers])


def stable(total):
    def layout(rnd, direction, page):
        return total, list(range((page - 1) * 10, min(total, page * 10)))
    return layout


def losing(total, lost, good_round=None):
    """Kolejność z remisem na granicy stron: strona 2 powtarza pozycję 9 zamiast `lost`."""
    def layout(rnd, direction, page):
        numbers = list(range((page - 1) * 10, min(total, page * 10)))
        if rnd != good_round and lost in numbers:
            numbers[numbers.index(lost)] = 9
        return total, numbers
    return layout


class AllPages20261113(unittest.TestCase):
    """--wszystkie: pełna lista z kontrolą kompletu (limit 200 trafień i 60 zapytań na wywołanie)."""

    def run_all(self, portal, *argv):
        return ms.run(ms.parser().parse_args(['szukaj', '--sad', '15051510', '--wszystkie'] + list(argv)), portal)

    def ids(self, result):
        return [w['id'] for w in result['wyniki']]

    def test_complete_in_first_round(self):
        portal = FakePortal(stable(25))
        r = self.run_all(portal)
        self.assertEqual((r['liczba_wynikow'], len(r['wyniki']), r['rundy'], r['zapytania']), (25, 25, 1, 3))
        self.assertIs(r['kompletna'], True)
        self.assertNotIn('strona', r)
        self.assertEqual({c[0] for c in portal.calls}, {'data'})  # portal pytany po dacie orzeczenia
        self.assertEqual([c[2] for c in portal.calls], [1, 2, 3])
        self.assertFalse(any('więcej niż jedną stronę' in u for u in r['uwagi']))  # bez uwagi o stronicowaniu
        self.assertTrue(any('Pełna lista (--wszystkie): 25 z 25 pozycji' in u and 'rundy: 1, zapytania: 3' in u
                            for u in r['uwagi']))

    def test_complete_in_second_round_stops_mid_round(self):
        portal = FakePortal(losing(25, 15, good_round=2))
        r = self.run_all(portal)
        self.assertEqual((len(set(self.ids(r))), r['rundy'], r['zapytania']), (25, 2, 5))
        # Runda 2 kończy się na stronie 2, gdy brakująca pozycja się znalazła; strony 3 nie pobrano.
        self.assertEqual([(c[1], c[2]) for c in portal.calls],
                         [('descending', 1), ('descending', 2), ('descending', 3), ('descending', 1), ('descending', 2)])

    def test_complete_in_third_round(self):
        portal = FakePortal(losing(25, 15, good_round=3))
        r = self.run_all(portal)
        self.assertEqual((len(r['wyniki']), r['rundy'], r['zapytania']), (25, 3, 8))
        # Każda runda pyta tak samo (przemienny kierunek nie pomógł w pomiarze 2026-11-13).
        self.assertEqual([c[1] for c in portal.calls if c[2] == 1], ['descending'] * 3)
        portal = FakePortal(losing(25, 15, good_round=2))
        self.run_all(portal, '--kierunek', 'ascending')
        self.assertEqual({c[:2] for c in portal.calls}, {('data', 'ascending')})

    def test_stall_stops_after_three_rounds_without_new_item(self):
        # Do 2.3.0: stałe 3 rundy (9 zapytań). Teraz rundy ogranicza budżet 60 zapytań, a zatrzymanie
        # następuje po 3 rundach z rzędu bez nowej pozycji: runda 1 daje 24 z 25, rundy 2–4 nic → 12 zapytań.
        portal = FakePortal(losing(25, 15))
        with self.assertRaises(ms.Unknown) as ctx:
            self.run_all(portal)
        self.assertEqual(len(portal.calls), 12)
        self.assertIn('po 4 rundach (12 zapytań) zebrano 24 z 25 unikalnych pozycji', str(ctx.exception))
        self.assertIn('(3 rundy z rzędu bez nowej pozycji)', str(ctx.exception))
        self.assertIn('Niepełnej listy nie wypisano', str(ctx.exception))
        stdout, stderr = io.StringIO(), io.StringIO()
        portal = FakePortal(losing(25, 15))
        with patch.object(ms.Client, 'get', lambda self, url: portal.get(url)), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', '--sad', '15051510', '--wszystkie', '--json']), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('BŁĄD (UNKNOWN): --wszystkie: po 4 rundach (12 zapytań) zebrano 24 z 25', stderr.getvalue())

    def test_limit_200_stops_after_one_request(self):
        portal = FakePortal(lambda rnd, d, page: (201, list(range(10))))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ms.Client, 'get', lambda self, url: portal.get(url)), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', '--sad', '15051510', '--wszystkie', '--json']), 2)
        self.assertEqual(len(portal.calls), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('Zapytanie ma 201 trafień, a --wszystkie pobiera najwyżej 200', stderr.getvalue())
        self.assertIn('--wydzial', stderr.getvalue())
        r = self.run_all(FakePortal(stable(200)))  # 200 to jeszcze w limicie: 20 stron
        self.assertEqual((len(r['wyniki']), r['zapytania']), (200, 20))

    def test_single_page_is_one_request(self):
        for total in (1, 7, 10):
            with self.subTest(total=total):
                portal = FakePortal(stable(total))
                r = self.run_all(portal)
                self.assertEqual((len(r['wyniki']), r['rundy'], r['zapytania'], len(portal.calls)), (total, 1, 1, 1))

    def test_zero_results_and_portal_error_keep_codes(self):
        with self.assertRaises(ms.Empty):
            self.run_all(FakePortal(lambda rnd, d, page: (0, [])))
        client = unittest.mock.Mock()
        client.get.side_effect = ms.PortalError(400, 'Błąd danych', 'u')
        with self.assertRaises(ms.Unknown):
            self.run_all(client)
        self.assertEqual(client.get.call_count, 1)

    def test_total_change_restarts_and_drops_collected_items(self):
        def layout(rnd, direction, page):
            total = 25 if (rnd, page) == (1, 1) else 26
            return total, list(range((page - 1) * 10, min(total, page * 10)))
        portal = FakePortal(layout)
        r = self.run_all(portal)
        self.assertEqual((r['liczba_wynikow'], len(r['wyniki']), r['rundy'], r['zapytania']), (26, 26, 2, 5))
        self.assertTrue(any('zmieniła się w trakcie' in u and '25 → 26' in u for u in r['uwagi']))

    def test_shrinking_list_is_never_empty_code(self):
        # Druga strona nagle „Nie znaleziono” — to zmiana listy (UNKNOWN), nie brak wyników (kod 1).
        def layout(rnd, direction, page):
            return (0, []) if page == 2 else (25, list(range(10)))
        portal = FakePortal(layout)
        with self.assertRaises(ms.Unknown) as ctx:
            self.run_all(portal)
        self.assertNotIsInstance(ctx.exception, ms.Empty)
        self.assertIn('zmieniała się w trakcie', str(ctx.exception))
        self.assertEqual(len(portal.calls), 6)

    def test_more_unique_ids_than_total_restarts(self):
        # Runda 1: 14 z 15 (brak 14); runda 2: dwie nowe pozycje przy tej samej liczbie trafień — lista
        # zmieniła skład (16 różnych ID > 15), więc zebrane pozycje są odrzucane, a runda 3 zaczyna od nowa.
        # Zamiana jednej pozycji na inną przy tej samej liczbie trafień jest niewykrywalna (api.md).
        def layout(rnd, direction, page):
            if rnd == 2 and page == 1:
                return 15, [100, 101] + list(range(2, 10))
            return losing(15, 14, good_round=3)(rnd, direction, page)
        r = self.run_all(FakePortal(layout))
        self.assertEqual((len(r['wyniki']), r['rundy']), (15, 3))
        self.assertEqual(set(self.ids(r)), {item(n)[0] for n in range(15)})
        self.assertTrue(any('liczba_wynikow 15, a zebrano 16 różnych ID' in u for u in r['uwagi']))

    def test_request_count_never_exceeds_60(self):
        portal = FakePortal(losing(200, 15))  # 20 stron, komplet nigdy
        with self.assertRaises(ms.Unknown) as ctx:
            self.run_all(portal)
        self.assertEqual(len(portal.calls), 60)  # 20 stron: nadal dokładnie 3 rundy
        self.assertIn('po 3 rundach (60 zapytań) zebrano 199 z 200', str(ctx.exception))
        self.assertIn('wyczerpany limit 60 zapytań', str(ctx.exception))
        self.assertEqual(ms.MAX_REQUESTS, 60)
        counter = iter(range(10 ** 6))  # liczba trafień zmienia się przy KAŻDYM zapytaniu
        portal = FakePortal(lambda rnd, d, page: (11 + next(counter) % 150, list(range((page - 1) * 10, page * 10))))
        with self.assertRaises(ms.Unknown):
            self.run_all(portal)
        self.assertLessEqual(len(portal.calls), 60)

    def test_conflicting_options_rejected_before_request(self):
        for argv in (['--strona', '2'], ['--sort', 'score'], ['--sort', 'istotnosc'], ['--sort', 'datapublikacji']):
            with self.subTest(argv=argv):
                portal = FakePortal(stable(25))
                with self.assertRaises(ValueError) as ctx:
                    self.run_all(portal, *argv)
                self.assertEqual(portal.calls, [])
                self.assertIn('--wszystkie', str(ctx.exception))
        self.assertEqual(len(self.run_all(FakePortal(stable(25)), '--sort', 'data')['wyniki']), 25)
        with self.assertRaises(ValueError):  # puste zapytanie odrzucone także w tym trybie
            ms.run(ms.parser().parse_args(['szukaj', '--wszystkie']), FakePortal(stable(5)))

    def test_order_is_deterministic_and_local(self):
        def shuffled(seed):
            import random
            rng = random.Random(seed)
            def layout(rnd, direction, page):
                order = list(range(25))
                rng.shuffle(order)  # kolejność portalu różna przy każdym zapytaniu
                return 25, order[(page - 1) * 10:page * 10]
            return layout
        runs = []
        for seed in range(6):
            try:
                runs.append(self.ids(self.run_all(FakePortal(shuffled(seed)))))
            except ms.Unknown:
                pass
        self.assertGreaterEqual(len(runs), 2)
        self.assertTrue(all(r == runs[0] for r in runs))
        r = self.run_all(FakePortal(stable(25)))
        keys = [(w['data_orzeczenia'], w['id']) for w in r['wyniki']]
        self.assertEqual(keys, sorted(keys, reverse=True))
        r = self.run_all(FakePortal(stable(25)), '--kierunek', 'ascending')
        keys = [(w['data_orzeczenia'], w['id']) for w in r['wyniki']]
        self.assertEqual(keys, sorted(keys))
        self.assertTrue(any('data orzeczenia rosnąco, potem ID' in u for u in r['uwagi']))

    def test_item_notes_cover_whole_list(self):
        items = {3: item(3)[:2] + (True,), 17: item(17)[:2] + (True,)}
        r = self.run_all(FakePortal(stable(25), items))
        self.assertTrue(any('2 z 25 pozycji na liście' in u for u in r['uwagi']))

    def test_signature_collision_across_pages(self):
        portal = FakePortal(stable(12))

        def get(url):
            text = portal.get(url)
            return text.replace('Sąd Rejonowy w Giżycku', 'Sąd Okręgowy w Koninie') if url.endswith('/2') else text
        client = unittest.mock.Mock()
        client.get.side_effect = get
        r = ms.run(ms.parser().parse_args(['sygnatura', 'II', 'K', '1/20', '--wszystkie']), client)
        self.assertEqual(len(r['wyniki']), 12)
        self.assertTrue(any('występuje w 2 sądach' in u for u in r['uwagi']))

    def test_default_mode_unchanged(self):
        portal = FakePortal(stable(25))
        r = ms.run(ms.parser().parse_args(['szukaj', '--sad', '15051510']), portal)
        self.assertEqual((r['strona'], len(r['wyniki']), len(portal.calls)), (1, 10, 1))
        self.assertEqual(portal.calls[0][:2], ('score', 'descending'))
        self.assertNotIn('kompletna', r)



def transport_error():
    return ms.TransportError("Błąd dostępu do portalu (niekompletna lub przerwana odpowiedź): TimeoutError('timed out')")


class Flaky:
    """Klient: przed oddaniem strony FakePortal zgłasza błąd sieci dla wybranych wywołań (numerowanych od 1).

    Błąd zgłaszany jest PRZED FakePortal.get, więc nieudana próba nie przesuwa licznika rund portalu.
    """

    def __init__(self, portal, failing=(), error=transport_error):
        self.portal, self.failing, self.error, self.attempts = portal, set(failing), error, []

    def get(self, url):
        self.attempts.append(url)
        if len(self.attempts) in self.failing:
            raise self.error()
        return self.portal.get(url)


class RoundsAndRetry20261113(unittest.TestCase):
    """Rundy ograniczone budżetem 60 zapytań, stop po 3 rundach bez postępu, jedno ponowienie po błędzie sieci."""

    def setUp(self):
        pause = patch.object(ms, 'retry_pause')
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def run_all(self, client, *argv):
        return ms.run(ms.parser().parse_args(['szukaj', '--sad', '15051510', '--wszystkie'] + list(argv)), client)

    def test_complete_only_in_fifth_round_of_small_query(self):
        # 25 trafień (3 strony). Unikalne po rundach: 22, 23, 23, 24, 25 — runda bez postępu (3.) nie
        # zatrzymuje, bo następna znów coś wnosi; komplet w 5. rundzie, w środku (strona 2).
        lost = {1: {15, 16, 17}, 2: {16, 17}, 3: {16, 17}, 4: {17}, 5: set()}

        def layout(rnd, direction, page):
            numbers = list(range((page - 1) * 10, min(25, page * 10)))
            return 25, [9 if n in lost[rnd] else n for n in numbers]
        portal = FakePortal(layout)
        r = self.run_all(portal)
        self.assertEqual((len(r['wyniki']), r['rundy'], r['zapytania'], r['ponowienia']), (25, 5, 14, 0))
        self.assertEqual(len(portal.calls), 14)
        self.assertTrue(any('rundy: 5, zapytania: 14.' in u for u in r['uwagi']))
        self.pause.assert_not_called()

    def test_budget_never_exceeded_with_retries(self):
        # 20 stron, komplet nigdy, co 7. próba to błąd sieci (ponowienie się udaje): ponowienia zjadają
        # budżet, więc trzecia runda jest niepełna — łącznie dokładnie 60 prób, nie więcej.
        flaky = Flaky(FakePortal(losing(200, 15)), failing=range(7, 61, 7))
        with self.assertRaises(ms.Unknown) as ctx:
            self.run_all(flaky)
        self.assertEqual(len(flaky.attempts), 60)
        self.assertEqual(len(flaky.portal.calls), 60 - self.pause.call_count)
        self.assertIn('wyczerpany limit 60 zapytań', str(ctx.exception))
        self.assertIn('po 3 rundach (60 zapytań, w tym ', str(ctx.exception))
        # Błąd sieci na 60. próbie: bez ponowienia (budżet), kod 2 z wyjaśnieniem.
        flaky = Flaky(FakePortal(losing(200, 15)), failing={60})
        with self.assertRaises(ms.Unknown) as ctx:
            self.run_all(flaky)
        self.assertEqual(len(flaky.attempts), 60)
        self.assertIn('nie ponowiono — wyczerpany limit 60 zapytań', str(ctx.exception))

    def test_retry_after_transport_error_succeeds(self):
        flaky = Flaky(FakePortal(stable(25)), failing={2})
        r = self.run_all(flaky)
        self.assertEqual((len(r['wyniki']), r['rundy'], r['zapytania'], r['ponowienia']), (25, 1, 4, 1))
        self.pause.assert_called_once_with(ms.RETRY_DELAY)
        self.assertGreaterEqual(ms.RETRY_DELAY, 3)
        self.assertEqual(flaky.attempts[1], flaky.attempts[2])  # ponowiona ta sama strona
        self.assertTrue(any('zapytania: 4 (w tym ponowienia po błędzie sieci: 1)' in u for u in r['uwagi']))
        # Błąd sieci na stronie 1: po ponowieniu zero trafień to nadal kod 1 (brak wyników), nie „zmiana listy”.
        with self.assertRaises(ms.Empty):
            self.run_all(Flaky(FakePortal(lambda rnd, d, page: (0, [])), failing={1}))

    def test_two_transport_errors_on_same_page_exit_2_without_list(self):
        flaky = Flaky(FakePortal(stable(25)), failing={3, 4})
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ms.Client, 'get', lambda self, url: flaky.get(url)), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', '--sad', '15051510', '--wszystkie', '--json']), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(len(flaky.attempts), 4)
        self.pause.assert_called_once_with(ms.RETRY_DELAY)
        self.assertIn('BŁĄD (UNKNOWN): --wszystkie: strona 3 nie pobrana także po ponowieniu (przerwa 5 s)', stderr.getvalue())
        self.assertIn('Stan: runda 1 (4 zapytania, w tym 1 ponowienie po błędzie sieci) zebrano 20 z 25', stderr.getvalue())

    def test_no_retry_after_tspd_portal_error_or_unrecognised_page(self):
        errors = [lambda: ms.Unknown('Portal odrzucił dostęp (F5/TSPD). Ponów później lub otwórz portal w przeglądarce.'),
                  lambda: ms.PortalError(400, 'Błąd danych', 'u'), lambda: ms.PortalError(404, 'Strona o podanym adresie nie istnieje', 'u')]
        for error in errors:
            with self.subTest(error=error()):
                flaky = Flaky(FakePortal(stable(25)), failing={2}, error=error)
                with self.assertRaises(ms.Unknown):
                    self.run_all(flaky)
                self.assertEqual(len(flaky.attempts), 2)
        client = unittest.mock.Mock()  # nierozpoznana strona na stronie 2
        client.get.side_effect = [list_page(25, [item(n) for n in range(10)]), '<html><body>inna strona</body></html>']
        with self.assertRaises(ms.Unknown):
            self.run_all(client)
        self.assertEqual(client.get.call_count, 2)
        self.pause.assert_not_called()

    def test_client_classifies_transport_errors(self):
        import http.client
        import socket
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = http.client.IncompleteRead(b'<html>', 5000)
        transient = [urllib.error.URLError('offline'), socket.timeout('timed out'), TimeoutError('timed out'),
                     ConnectionResetError(54, 'Connection reset by peer')]
        for case in transient + [response]:
            client = ms.Client()
            kwargs = {'return_value': case} if case is response else {'side_effect': case}
            with self.subTest(case=case), patch.object(client.opener, 'open', **kwargs):
                with self.assertRaises(ms.TransportError) as ctx:
                    client.get(ms.BASE)
                self.assertIsInstance(ctx.exception, ms.Unknown)  # nadal kod 2
        tspd = unittest.mock.MagicMock()
        tspd.__enter__.return_value = tspd
        tspd.read.return_value = fixture('tspd.html').encode()
        client = ms.Client()
        with patch.object(client.opener, 'open', return_value=tspd), self.assertRaises(ms.Unknown) as ctx:
            client.get(ms.BASE)
        self.assertNotIsInstance(ctx.exception, ms.TransportError)
        for code, name in [(400, 'error400.html'), (404, 'error404.html')]:
            client = ms.Client()
            with patch.object(client.opener, 'open', side_effect=http_error(code, name)), self.assertRaises(ms.PortalError) as ctx:
                client.get(ms.BASE)
            self.assertNotIsInstance(ctx.exception, ms.TransportError)

    def test_end_to_end_timeout_then_success_through_real_client(self):
        import socket
        pages = [list_page(12, [item(n) for n in range(10)]), list_page(12, [item(n) for n in range(10, 12)])]

        def response(text):
            r = unittest.mock.MagicMock()
            r.__enter__.return_value = r
            r.read.return_value = text.encode()
            return r
        stdout, stderr = io.StringIO(), io.StringIO()
        side = [response(pages[0]), socket.timeout('timed out'), response(pages[1])]
        with patch.object(urllib.request.OpenerDirector, 'open', side_effect=side) as opened, \
                patch.object(ms.time, 'sleep'), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', '--sad', '15051510', '--wszystkie', '--json']), 0)
        self.assertEqual(opened.call_count, 3)
        out = json.loads(stdout.getvalue())
        self.assertEqual((out['liczba_wynikow'], len(out['wyniki']), out['zapytania'], out['ponowienia']), (12, 12, 3, 1))
        self.pause.assert_called_once_with(ms.RETRY_DELAY)

    def test_default_mode_never_retries(self):
        flaky = Flaky(FakePortal(stable(25)), failing={1})
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(ms.Client, 'get', lambda self, url: flaky.get(url)), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(ms.main(['szukaj', '--sad', '15051510', '--json']), 2)
        self.assertEqual((len(flaky.attempts), stdout.getvalue()), (1, ''))
        self.assertIn('BŁĄD (UNKNOWN): Błąd dostępu do portalu', stderr.getvalue())
        self.pause.assert_not_called()

if __name__ == '__main__':
    unittest.main()
