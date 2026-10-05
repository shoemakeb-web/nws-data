import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('updater', Path(__file__).parents[1] / 'scripts/update_cases.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
SAMPLE = 'Animal ID,Confirmed Date,County,State,Status\nA,10/2/2026,Brewster,Texas,Active\nB,9/26/2026,Crockett,Texas,Inactive\n'
NOW = datetime(2026, 10, 5, 23, 35, tzinfo=timezone.utc)


def Response(text=SAMPLE, fail=False):
    if fail:
        raise OSError('HTTP 503')
    return text


class UpdaterTests(unittest.TestCase):
    def test_uploaded_baseline_is_valid(self):
        fixture = Path(__file__).with_name('baseline.csv').read_text()
        _, count, latest = updater.validate_csv(fixture, fixture)
        self.assertEqual(count, 53)
        self.assertEqual(latest, '2026-10-02')

    def test_success_writes_matching_status_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            out, status = Path(directory)/'cases.csv', Path(directory)/'status.json'
            result = updater.update(out, status, NOW, get=lambda *a, **kw: Response(), sleep=lambda _: None)
            self.assertEqual(result['csv_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(result['row_count'], 2)
            self.assertEqual(result['latest_case_date'], '2026-10-02')
            self.assertFalse(result['source_publication_time_verified'])
            self.assertEqual(json.loads(status.read_text()), result)

    def test_transient_failure_recovers_within_three_attempts(self):
        calls = []
        def get(*args, **kwargs):
            calls.append(kwargs)
            return Response(fail=len(calls)<3)
        with tempfile.TemporaryDirectory() as directory:
            updater.update(Path(directory)/'cases.csv', Path(directory)/'status.json', NOW, get=get, sleep=lambda _: None)
        self.assertEqual(len(calls), 3)

    def test_failure_preserves_csv_and_previous_success_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            out, status = Path(directory)/'cases.csv', Path(directory)/'status.json'
            out.write_text(SAMPLE)
            status.write_text(json.dumps({'ok':True, 'last_successful_fetch_utc':'2026-10-04T23:35:00Z'}))
            with self.assertRaises(RuntimeError):
                updater.update(out, status, NOW, get=lambda *a, **kw: Response('<html>error</html>'), sleep=lambda _: None)
            self.assertEqual(out.read_text(), SAMPLE)
            meta = json.loads(status.read_text())
            self.assertFalse(meta['ok'])
            self.assertEqual(meta['last_successful_fetch_utc'], '2026-10-04T23:35:00Z')

    def test_bad_dates_missing_headers_duplicate_ids_and_statuses_rejected(self):
        for text in ['wrong,headers\na,b', SAMPLE.replace('10/2/2026','2/30/2026'), SAMPLE.replace('\nB,','\nA,'), SAMPLE.replace('Inactive','Unknown'), 'Animal ID,Confirmed Date,County,State,Status\n']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                updater.validate_csv(text)

    def test_existing_cases_cannot_silently_disappear(self):
        with self.assertRaisesRegex(ValueError, 'omitted existing'):
            updater.validate_csv(SAMPLE.split('B,')[0], SAMPLE)

    def test_status_changes_and_added_cases_are_allowed(self):
        _, count, _ = updater.validate_csv(SAMPLE.replace('Active','Inactive')+'C,10/3/2026,Brewster,Texas,Active\n', SAMPLE)
        self.assertEqual(count,3)

    def test_recovery_skip_uses_previous_evening_eastern_day(self):
        now=datetime(2026,10,6,0,35,tzinfo=timezone.utc)
        self.assertFalse(updater.recovery_needed({'ok':True,'last_successful_fetch_utc':'2026-10-05T23:35:00Z'},now))
        self.assertTrue(updater.recovery_needed({'ok':True,'last_successful_fetch_utc':'2026-10-05T12:15:00Z'},now))
        self.assertTrue(updater.recovery_needed({'ok':False,'last_successful_fetch_utc':'2026-10-05T23:35:00Z'},now))

    def test_recovery_cutoff_follows_eastern_winter_time(self):
        now=datetime(2026,11,3,0,35,tzinfo=timezone.utc)
        self.assertFalse(updater.recovery_needed({'ok':True,'last_successful_fetch_utc':'2026-11-02T23:35:00Z'},now))
        self.assertTrue(updater.recovery_needed({'ok':True,'last_successful_fetch_utc':'2026-11-02T21:35:00Z'},now))


if __name__ == '__main__':
    unittest.main()
