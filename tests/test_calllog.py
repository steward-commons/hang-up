#!/usr/bin/env python3
"""Tests for Operation Hang Up call log."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import calllog  # noqa: E402


class CallLogTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tmpdir.name)
        self.store = self.home / "data" / "calls.json"
        self.key = self.home / "keys" / "hangup.key"
        os.environ["HANGUP_HOME"] = str(self.home)
        os.environ["HANGUP_KEY_PATH"] = str(self.key)
        os.environ.pop("HANGUP_STORE", None)
        os.environ.pop("HANGUP_BACKUP", None)
        os.environ.pop("HANGUP_KEY_DIR", None)
        self.addCleanup(self._tmpdir.cleanup)
        self.addCleanup(os.environ.pop, "HANGUP_HOME", None)
        self.addCleanup(os.environ.pop, "HANGUP_KEY_PATH", None)

    def add(self, number, when, name, pitch, kind, **kwargs):
        return calllog.add_call(number, when, name, pitch, kind, **kwargs)

    def test_add_and_list_persists(self):
        entry = self.add(
            "555-010-0100",
            "2026-11-18 14:30",
            "ACME WARRANTY",
            "extended car warranty",
            "recording",
        )
        self.assertEqual(entry["number"], "5550100100")
        self.assertEqual(entry["number_display"], "(555) 010-0100")
        self.assertEqual(entry["kind"], "recording")
        self.assertTrue(self.store.is_file())
        rows = calllog.list_calls()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["caller_id"], "ACME WARRANTY")
        self.assertIn("2026-11-18T14:30:00", rows[0]["when"])

    def test_numbers_normalize_together(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording")
        self.add("5550100100", "2026-11-02 09:00", "ACME", "warranty again", "recording")
        self.add("1 (555) 010-0100", "2026-11-03 09:00", "ACME", "still warranty", "live")
        rows = calllog.list_calls("15550100100")
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["number"] == "5550100100" for r in rows))

    def test_list_filters_by_number(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0199", "2026-11-01 10:00", "SOLAR", "free panels", "live")
        rows = calllog.list_calls("5550100199")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pitch"], "free panels")

    def test_rejects_bad_kind_and_missing_fields(self):
        with self.assertRaises(calllog.CallLogError):
            self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "robot")
        with self.assertRaises(calllog.CallLogError):
            self.add("555-010-0100", "2026-11-01 09:00", "", "warranty", "live")
        with self.assertRaises(calllog.CallLogError):
            self.add("555-010-0100", "2026-11-01 09:00", "ACME", "", "live")
        with self.assertRaises(calllog.CallLogError):
            self.add("abc", "2026-11-01 09:00", "ACME", "warranty", "live")
        with self.assertRaises(calllog.CallLogError):
            self.add("555-010-0100", "next Tuesday", "ACME", "warranty", "live")

    def test_repeats_summarizes_offenders(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0100", "2026-11-04 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0199", "2026-11-02 09:00", "SOLAR", "panels", "live")
        rows = calllog.repeats(minimum=2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["number"], "5550100100")
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["top_name"], "ACME")
        self.assertEqual(calllog.repeats(minimum=3), [])

    def test_case_requires_three_calls(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0100", "2026-11-02 09:00", "ACME", "warranty", "recording")
        with self.assertRaises(calllog.CallLogError) as ctx:
            calllog.export_case("555-010-0100")
        self.assertIn("3 or more", str(ctx.exception))
        self.add("555-010-0100", "2026-11-03 09:00", "ACME INC", "warranty last chance", "live")
        paths = calllog.export_case("5550100100")
        self.assertEqual(len(paths), 1)
        body = paths[0].read_text(encoding="utf-8")
        self.assertIn("# Case file — (555) 010-0100", body)
        self.assertIn("**Calls logged:** 3", body)
        self.assertIn("warranty last chance", body)
        self.assertIn("donotcall.gov/file-a-complaint", body)
        self.assertIn("reportfraud.ftc.gov", body)
        self.assertIn("Do not call this number back", body)
        self.assertIn("Not legal advice", body)
        self.assertTrue(paths[0].is_relative_to(self.home / "data" / "cases"))

    def test_case_all_exports_only_qualifying_numbers(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0100", "2026-11-02 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0100", "2026-11-03 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0199", "2026-11-01 10:00", "SOLAR", "panels", "live")
        paths = calllog.export_case(everyone=True)
        names = {p.name for p in paths}
        self.assertIn("case-5550100100.md", names)
        # The three ACME calls also qualify as a company case.
        self.assertTrue(any(n.startswith("case-company-") for n in names))
        self.assertFalse(any("5550100199" in n for n in names))

    def test_cli_add_list_repeats_and_case(self):
        rc = calllog.main(
            [
                "add",
                "--number",
                "555-010-0100",
                "--when",
                "2026-11-10 08:00",
                "--name",
                "ACME",
                "--pitch",
                "warranty",
                "--kind",
                "recording",
            ]
        )
        self.assertEqual(rc, 0)
        calllog.main(
            [
                "add",
                "--number",
                "5550100100",
                "--when",
                "2026-11-11 08:00",
                "--name",
                "ACME",
                "--pitch",
                "warranty",
                "--kind",
                "recording",
            ]
        )
        calllog.main(
            [
                "add",
                "--number",
                "1-555-010-0100",
                "--when",
                "2026-11-12 08:00",
                "--name",
                "ACME",
                "--pitch",
                "warranty",
                "--kind",
                "live",
            ]
        )
        self.assertEqual(calllog.main(["list"]), 0)
        self.assertEqual(calllog.main(["repeats"]), 0)
        self.assertEqual(calllog.main(["case", "555-010-0100"]), 0)
        case = self.home / "data" / "cases" / "case-5550100100.md"
        self.assertTrue(case.is_file())

    def test_cli_case_without_enough_calls_fails(self):
        calllog.main(
            [
                "add",
                "--number",
                "555-010-0100",
                "--when",
                "2026-11-10 08:00",
                "--name",
                "ACME",
                "--pitch",
                "warranty",
                "--kind",
                "recording",
            ]
        )
        self.assertEqual(calllog.main(["case", "555-010-0100"]), 2)
        self.assertEqual(calllog.main(["case", "--all"]), 2)

    def test_quick_parses_free_text_and_keeps_raw(self):
        first = calllog.parse_quick("spam call 555-010-0100 car warranty recording")
        self.assertEqual(first["number"], "5550100100")
        self.assertEqual(first["kind"], "recording")
        self.assertEqual(first["pitch"], "car warranty")
        self.assertEqual(first["raw_text"], "spam call 555-010-0100 car warranty recording")
        second = calllog.parse_quick("2026-11-18 2:30pm 555-010-0100 ACME WARRANTY live")
        self.assertEqual(second["number"], "5550100100")
        self.assertEqual(second["kind"], "live")
        self.assertEqual(second["caller_id"], "ACME WARRANTY")
        self.assertEqual(second["company"], "ACME WARRANTY")
        self.assertEqual(second["when"], "2026-11-18 14:30")
        entry = calllog.quick_add("spam call 555-010-0100 car warranty recording")
        self.assertEqual(entry["raw_text"], "spam call 555-010-0100 car warranty recording")
        self.assertEqual(entry["number"], "5550100100")
        self.assertEqual(calllog.main(["quick", "2026-11-18", "2:30pm", "555-010-0199", "SOLAR", "panels", "live"]), 0)
        rows = calllog.list_calls("5550100199")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "SOLAR")
        self.assertEqual(rows[0]["pitch"], "panels")
        self.assertIn("2026-11-18T14:30:00", rows[0]["when"])

    def test_company_groups_repeats_and_case_across_numbers(self):
        self.add(
            "555-010-0100",
            "2026-11-01 09:00",
            "ACME WARRANTY",
            "warranty",
            "recording",
            company="ACME WARRANTY",
        )
        self.add(
            "555-010-0101",
            "2026-11-08 09:00",
            "ACME",
            "warranty again",
            "recording",
            company="Acme Warranty",
        )
        self.add(
            "555-010-0199",
            "2026-11-02 09:00",
            "SOLAR",
            "panels",
            "live",
            company="SOLAR CO",
        )
        companies = calllog.repeats_by_company(minimum=2)
        self.assertEqual(len(companies), 1)
        self.assertEqual(calllog.company_key(companies[0]["company"]), "acme warranty")
        self.assertEqual(companies[0]["count"], 2)
        self.assertEqual(companies[0]["number_count"], 2)
        with self.assertRaises(calllog.CallLogError):
            calllog.export_case(company="ACME WARRANTY")
        self.add(
            "555-010-0102",
            "2026-11-20 09:00",
            "ACME WARRANTY",
            "last chance",
            "live",
            company="ACME WARRANTY",
        )
        paths = calllog.export_case(company="acme warranty")
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].name.startswith("case-company-acme-warranty"))
        body = paths[0].read_text(encoding="utf-8")
        self.assertIn("**Company:**", body)
        self.assertIn("5550100100", body.replace("-", "").replace(" ", "").replace("(", "").replace(")", ""))
        self.assertIn("last chance", body)
        self.assertEqual(calllog.main(["case", "--company", "ACME WARRANTY"]), 0)

    def test_registry_and_dnc_windows(self):
        calllog.set_registry("2026-01-01")
        self.assertEqual(calllog.registry_date().isoformat(), "2026-01-01")
        self.assertEqual(calllog.honor_date(calllog.registry_date()).isoformat(), "2026-02-01")
        early = self.add("555-010-0100", "2026-01-15 09:00", "ACME", "too soon", "recording", company="ACME")
        late1 = self.add("555-010-0100", "2026-03-01 09:00", "ACME", "after honor", "recording", company="ACME")
        late2 = self.add("555-010-0199", "2026-06-01 09:00", "ACME", "same year", "live", company="ACME")
        # A fourth call outside a 12-month window of the first qualifying pair is still qualifying,
        # but the test is already met by late1+late2.
        registered = calllog.registry_date()
        early_stamp = calllog._as_when(early["when"])
        late_stamp = calllog._as_when(late1["when"])
        self.assertFalse(calllog.is_after_honor_period(early_stamp, registered))
        self.assertTrue(calllog.is_after_honor_period(late_stamp, registered))
        test = calllog.twelve_month_test(calllog.list_calls(), registered)
        self.assertTrue(test["met"])
        self.assertEqual(test["qualifying"], 2)
        # Only one qualifying call fails the 2+ test.
        one = [row for row in calllog.list_calls() if row["id"] == late1["id"]]
        self.assertFalse(calllog.twelve_month_test(one, registered)["met"])
        # Two qualifying calls 400 days apart fail the 12-month window.
        far = [
            late1,
            self.add("555-010-0100", "2027-06-01 09:00", "ACME", "next year", "live", company="ACME"),
        ]
        far_only = [late1, far[-1]]
        self.assertFalse(calllog.twelve_month_test(far_only, registered)["met"])
        paths = calllog.export_case(company="ACME")
        body = paths[0].read_text(encoding="utf-8")
        self.assertIn("2026-01-01", body)
        self.assertIn("2026-02-01", body)
        self.assertIn("before the honor mark", body)
        self.assertIn("31+ days after registration", body)
        self.assertIn("MET", body)
        self.assertIn("47 U.S.C. § 227(c)(5)", body)
        self.assertIn("64.1200(c)(2)", body)
        self.assertIn("Not legal advice", body)
        self.assertEqual(calllog.main(["registry"]), 0)
        self.assertEqual(calllog.main(["registry", "--date", "2025-12-15"]), 0)
        self.assertEqual(calllog.registry_date().isoformat(), "2025-12-15")

    def test_evidence_is_copied_and_hashed_in_case_file(self):
        shot = self.home / "voicemail-shot.png"
        shot.write_bytes(b"\x89PNG-fake-evidence")
        entry = self.add(
            "555-010-0100",
            "2026-11-01 09:00",
            "ACME",
            "warranty",
            "recording",
            evidence=[str(shot)],
        )
        digest = entry["evidence"][0]["sha256"]
        stored = self.home / "data" / "evidence" / f"{digest}.png"
        self.assertTrue(stored.is_file())
        self.assertEqual(stored.read_bytes(), shot.read_bytes())
        self.assertEqual(len(digest), 64)
        self.add("555-010-0100", "2026-11-02 09:00", "ACME", "warranty", "recording")
        self.add("555-010-0100", "2026-11-03 09:00", "ACME", "warranty", "live")
        body = calllog.export_case("555-010-0100")[0].read_text(encoding="utf-8")
        self.assertIn(digest, body)
        self.assertIn("voicemail-shot.png", body)
        self.assertIn("SHA-256", body)
        self.assertEqual(
            calllog.main(
                [
                    "add",
                    "--number",
                    "555-010-0100",
                    "--when",
                    "2026-11-04 09:00",
                    "--name",
                    "ACME",
                    "--pitch",
                    "warranty",
                    "--kind",
                    "live",
                    "--evidence",
                    str(shot),
                ]
            ),
            0,
        )

    def test_backup_round_trip_and_auto_backup(self):
        self.add("555-010-0100", "2026-11-01 09:00", "ACME", "warranty", "recording", company="ACME")
        shot = self.home / "clip.wav"
        shot.write_bytes(b"RIFF-fake-wav")
        calllog.quick_add("spam call 555-010-0100 car warranty recording", evidence=[str(shot)])
        calllog.set_registry("2026-01-15")
        enc = self.home / "backup" / "calllog.enc"
        self.assertTrue(enc.is_file())
        self.assertTrue(self.key.is_file())
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.key.parent.stat().st_mode), 0o700)
        # Key material must never appear in the ciphertext file.
        self.assertNotIn(self.key.read_bytes().strip(), enc.read_bytes())
        original = calllog.load_store()
        evidence_files = list((self.home / "data" / "evidence").iterdir())
        self.assertTrue(evidence_files)
        # Wipe and restore.
        for child in (self.home / "data").iterdir():
            if child.is_dir():
                for nested in child.iterdir():
                    nested.unlink()
            elif child.suffix == ".json":
                child.unlink()
        self.assertFalse(self.store.is_file())
        restored = calllog.restore_backup()
        self.assertEqual(restored, self.home / "data")
        again = calllog.load_store()
        self.assertEqual(again["registry"]["registered_on"], "2026-01-15")
        self.assertEqual(len(again["calls"]), len(original["calls"]))
        self.assertEqual(again["calls"][0]["number"], original["calls"][0]["number"])
        self.assertTrue(evidence_files[0].is_file())
        # CLI backup / restore into another folder.
        other = self.home / "restored"
        self.assertEqual(calllog.main(["backup"]), 0)
        self.assertEqual(calllog.main(["restore", "--to", str(other)]), 0)
        self.assertTrue((other / "calls.json").is_file())
        # A wrong key cannot decrypt.
        bad = self.home / "keys" / "wrong.key"
        bad.write_bytes(calllog.Fernet.generate_key() + b"\n")
        os.environ["HANGUP_KEY_PATH"] = str(bad)
        with self.assertRaises(calllog.CallLogError):
            calllog.restore_backup(dest=self.home / "fail")
        os.environ["HANGUP_KEY_PATH"] = str(self.key)


if __name__ == "__main__":
    unittest.main()
