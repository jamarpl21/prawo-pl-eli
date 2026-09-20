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
        items = ms.parse_regulations(fixture('regulations.html'))
        self.assertEqual(len(items), 2)
        self.assertIn('Kodeks cywilny', items[1]['tytul'])
        with self.assertRaises(ms.Unknown):
            ms.parse_regulations(fixture('details.html'))

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


if __name__ == '__main__':
    unittest.main()
