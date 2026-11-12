import csv
import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from io import StringIO

SPEC = importlib.util.spec_from_file_location('updater', Path(__file__).parents[1] / 'scripts/update_cases.py')
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)
SAMPLE = 'Animal ID,Confirmed Date,County,State,Status\nA,10/2/2026,Brewster,Texas,Active\nB,9/26/2026,Crockett,Texas,Inactive\n'
NOW = datetime(2026, 10, 5, 23, 35, tzinfo=timezone.utc)
OLD_ID = 'Texas, BREWSTER, SU:28426:27, SS:12802:27'
NEW_ID = 'Texas, JEFF DAVIS, SU:28426:27, SS:12802:27'
CORRECTED_CASE = {
    'Animal ID': OLD_ID, 'Animal Type': 'Domestic', 'Case Type': 'Domestic',
    'Confirmed Date': '11/2/2026', 'County': 'Brewster', 'Species': 'Cattle',
    'State': 'Texas', 'Status': 'Active',
}


def correction_csv(*records):
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CORRECTED_CASE)
    writer.writeheader()
    writer.writerows(records)
    return output.getvalue()


def corrected_record(**changes):
    record = dict(CORRECTED_CASE, **{'Animal ID': NEW_ID, 'County': 'Jeff Davis'})
    record.update(changes)
    return record


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

    def test_verified_county_correction_keeps_source_identity_and_count(self):
        prior = correction_csv(CORRECTED_CASE)
        current = correction_csv(corrected_record())
        payload, count, latest = updater.validate_csv(current, prior)
        self.assertEqual(count, 1)
        self.assertEqual(latest, '2026-11-02')
        records = list(csv.DictReader(StringIO(payload.decode())))
        self.assertEqual(records, [corrected_record()])
        self.assertEqual(updater.validate_csv(current, current)[0], payload)

    def test_verified_correction_allows_date_status_updates_and_new_cases(self):
        updated = corrected_record(**{'Confirmed Date': '11/3/2026', 'Status': 'Inactive'})
        addition = dict(CORRECTED_CASE, **{'Animal ID': 'another case'})
        _, count, latest = updater.validate_csv(
            correction_csv(updated, addition), correction_csv(CORRECTED_CASE))
        self.assertEqual((count, latest), (2, '2026-11-03'))

    def test_correction_does_not_excuse_an_unrelated_omission(self):
        other = dict(CORRECTED_CASE, **{'Animal ID': 'unrelated case'})
        with self.assertRaisesRegex(ValueError, 'omitted existing'):
            updater.validate_csv(correction_csv(corrected_record()),
                                 correction_csv(CORRECTED_CASE, other))

    def test_unlisted_identity_changes_and_reversed_correction_are_rejected(self):
        for current, prior in [
            (correction_csv(dict(corrected_record(), **{'Animal ID': 'Texas, JEFF DAVIS, SU:99999:27, SS:12802:27'})), correction_csv(CORRECTED_CASE)),
            (correction_csv(dict(corrected_record(), **{'Animal ID': 'Texas, JEFF DAVIS, SU:28426:27, SS:99999:27'})), correction_csv(CORRECTED_CASE)),
            (correction_csv(dict(corrected_record(), **{'Animal ID': 'Texas, PECOS, SU:28426:27, SS:12802:27'})), correction_csv(CORRECTED_CASE)),
            (correction_csv(CORRECTED_CASE), correction_csv(corrected_record())),
        ]:
            with self.subTest(current=current), self.assertRaisesRegex(ValueError, 'omitted existing'):
                updater.validate_csv(current, prior)

    def test_correction_requires_expected_context_in_both_snapshots(self):
        for field in ('County', 'State', 'Animal Type', 'Case Type', 'Species'):
            for value in ('mismatch', ''):
                for change_prior in (False, True):
                    old, new = dict(CORRECTED_CASE), corrected_record()
                    (old if change_prior else new)[field] = value
                    with self.subTest(field=field, value=value, prior=change_prior), self.assertRaises(ValueError):
                        updater.validate_csv(correction_csv(new), correction_csv(old))

    def test_correction_rejects_missing_context_columns(self):
        for field in ('Animal Type', 'Case Type', 'Species'):
            for change_prior in (False, True):
                old, new = correction_csv(CORRECTED_CASE), correction_csv(corrected_record())
                text = old if change_prior else new
                reader = csv.DictReader(StringIO(text))
                fields = [name for name in reader.fieldnames if name != field]
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()
                writer.writerows(reader)
                if change_prior:
                    old = output.getvalue()
                else:
                    new = output.getvalue()
                with self.subTest(field=field, prior=change_prior), self.assertRaisesRegex(ValueError, 'context'):
                    updater.validate_csv(new, old)

    def test_correction_collisions_are_rejected_even_without_missing_ids(self):
        both = correction_csv(CORRECTED_CASE, corrected_record())
        for current, prior in [
            (both, correction_csv(CORRECTED_CASE)),
            (both, None),
            (both, both),
            (correction_csv(corrected_record()), both),
            (correction_csv(CORRECTED_CASE), both),
        ]:
            with self.subTest(current=current, prior=prior), self.assertRaisesRegex(ValueError, 'Ambiguous'):
                updater.validate_csv(current, prior)

    def test_duplicate_prior_ids_cannot_be_reconciled(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            updater.validate_csv(correction_csv(corrected_record()),
                                 correction_csv(CORRECTED_CASE, CORRECTED_CASE))

    def test_unexplained_omission_preserves_last_good_files(self):
        with tempfile.TemporaryDirectory() as directory:
            out, status = Path(directory)/'cases.csv', Path(directory)/'status.json'
            other = dict(CORRECTED_CASE, **{'Animal ID': 'unrelated case'})
            prior = correction_csv(CORRECTED_CASE, other)
            out.write_text(prior)
            old_meta = {'ok': True, 'last_successful_fetch_utc': '2026-11-03T03:59:32Z',
                        'row_count': 2, 'csv_sha256': hashlib.sha256(out.read_bytes()).hexdigest()}
            status.write_text(json.dumps(old_meta))
            with self.assertRaises(RuntimeError):
                updater.update(out, status, NOW, get=lambda *a, **kw: correction_csv(corrected_record()), sleep=lambda _: None)
            self.assertEqual(out.read_bytes(), prior.encode())
            meta = json.loads(status.read_text())
            self.assertFalse(meta['ok'])
            for key in ('last_successful_fetch_utc', 'row_count', 'csv_sha256'):
                self.assertEqual(meta[key], old_meta[key])

    def test_correction_recovers_failed_updater_with_matching_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            out, status = Path(directory)/'cases.csv', Path(directory)/'status.json'
            out.write_text(correction_csv(CORRECTED_CASE))
            status.write_text(json.dumps({'ok': False, 'error': 'Source omitted existing case IDs'}))
            now = datetime(2026, 11, 12, tzinfo=timezone.utc)
            result = updater.update(out, status, now, get=lambda *a, **kw: correction_csv(corrected_record()), sleep=lambda _: None)
            self.assertTrue(result['ok'])
            self.assertIsNone(result['error'])
            self.assertEqual(result['row_count'], 1)
            self.assertEqual(result['csv_sha256'], hashlib.sha256(out.read_bytes()).hexdigest())
            self.assertEqual(json.loads(status.read_text()), result)

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

