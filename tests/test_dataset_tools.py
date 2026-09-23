"""Synthetic-only regressions for unsafe paths and career-progress edge cases."""
import copy
from pathlib import Path
import stat
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo

from scripts.audit_dataset import candidate_reasons, is_number, load_json, reconstruct_skills, select_target
from scripts.prepare_dataset import FILES, prepare


class PrepareDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def make_zip(self, extras=()):
        path = self.root / "kit.zip"
        with ZipFile(path, "w") as archive:
            for name in FILES:
                archive.writestr("nested/kit/" + name, "synthetic " + name)
            for name, contents in extras:
                archive.writestr(name, contents)
        return path

    def test_extract_only_seven_and_idempotent(self):
        archive = self.make_zip([("nested/kit/.DS_Store", "ignore"), ("__MACOSX/._kit", "ignore")])
        destination = self.root / "raw"
        first = prepare(archive, destination)
        self.assertEqual(set(FILES), {p.name for p in destination.iterdir()})
        self.assertEqual(first, prepare(archive, destination))

    def test_plain_folder(self):
        source = self.root / "source"
        source.mkdir()
        for name in FILES:
            (source / name).write_text("synthetic")
        self.assertEqual(7, len(prepare(source, self.root / "raw")))

    def test_zip_traversal_refused(self):
        for name in ("../outside", "/absolute", "C:/windows", "bad\\path"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    prepare(self.make_zip([(name, "bad")]), self.root / "raw")
                self.assertFalse((self.root / "raw").exists())

    def test_archive_symlink_refused(self):
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            prepare(self.make_zip([(link, "outside")]), self.root / "raw")

    def test_ambiguous_kit_refused(self):
        extras = [("other/" + name, "synthetic") for name in FILES]
        with self.assertRaises(ValueError):
            prepare(self.make_zip(extras), self.root / "raw")

    def test_normalized_zip_alias_refused_before_writing(self):
        for name in ("nested/kit/./employees.json", "nested/kit//employees.json"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    prepare(self.make_zip([(name, "replacement")]), self.root / "raw")
                self.assertFalse((self.root / "raw").exists())

    def test_existing_different_file_is_preserved(self):
        destination = self.root / "raw"
        destination.mkdir()
        target = destination / "skills.json"
        target.write_text("preserve")
        with self.assertRaises(ValueError):
            prepare(self.make_zip(), destination)
        self.assertEqual("preserve", target.read_text())
        self.assertEqual(["skills.json"], [p.name for p in destination.iterdir()])

    def test_destination_symlink_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        destination = self.root / "raw"
        destination.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare(self.make_zip(), destination)
        self.assertEqual([], list(outside.iterdir()))

    def test_source_file_symlink_refused(self):
        source = self.root / "source"
        source.mkdir()
        for name in FILES:
            (source / name).write_text("synthetic")
        (source / "skills.json").unlink()
        (source / "skills.json").symlink_to(source / "events.json")
        with self.assertRaises(ValueError):
            prepare(source, self.root / "raw")


class ProgressAuditTests(unittest.TestCase):
    def setUp(self):
        self.employee = {"employee_id": "SYNTHETIC", "skills": {"S": 4},
                         "last_review_date": "2026-01-01", "role": "R", "grade": "Middle",
                         "career_goal": None}
        self.event = {"event_id": "SYNTHETIC_EVENT", "mandatory": False, "target_roles": ["R"],
                      "target_grades": ["Middle"], "format": "self_paced", "upcoming_sessions": [],
                      "prerequisites": {}, "develops_skills": [{"skill_id": "S", "gain": 1, "max_level": 3}]}

    def row(self, day="2026-01-02", status="completed", record="R1"):
        return {"employee_id": "SYNTHETIC", "event_id": "SYNTHETIC_EVENT", "record_id": record,
                "status": status, "date": day}

    def replay(self, rows):
        return reconstruct_skills(self.employee, rows, {"SYNTHETIC_EVENT": self.event}, "2026-10-01")

    def test_cap_does_not_reduce_baseline(self):
        levels, stats = self.replay([self.row()])
        self.assertEqual(4, levels["S"])
        self.assertEqual(1, stats["zero_effect_completions"])

    def test_only_completed_strictly_after_review_and_not_future(self):
        self.employee["skills"] = {"S": 0}
        rows = [self.row("2026-01-01"), self.row("2026-01-02", "in_progress", "R2"),
                self.row("2026-10-02", record="R3"), self.row("2026-01-03", record="R4")]
        levels, stats = self.replay(rows)
        self.assertEqual(1, levels["S"])
        self.assertEqual(1, stats["replayed_completions"])

    def test_missing_skill_starts_at_zero_and_replay_preserves_inputs(self):
        self.employee["skills"] = {}
        before = copy.deepcopy(self.employee)
        self.assertEqual({"S": 1}, self.replay([self.row()])[0])
        self.assertEqual(before, self.employee)

    def test_clamp_gain_and_repeat_calculation_is_deterministic(self):
        self.employee["skills"] = {"S": 2}
        self.event["develops_skills"][0]["gain"] = 3
        rows = [self.row(), self.row("2026-01-03", record="R2")]
        first = self.replay(rows)
        self.assertEqual(3, first[0]["S"])
        self.assertEqual(first, self.replay(list(reversed(rows))))

    def test_completed_excluded_but_recurring_club_allowed(self):
        reasons = candidate_reasons(self.employee, self.event, {"S": 4}, [self.row()], "2026-10-01")
        self.assertIn("already_completed", reasons)
        self.event["event_id"] = "EV_036"
        row = self.row()
        row["event_id"] = "EV_036"
        self.assertNotIn("already_completed", candidate_reasons(self.employee, self.event, {"S": 4}, [row], "2026-10-01"))

    def test_filters_prerequisites_and_future_scheduled_session(self):
        self.event.update({"format": "online", "upcoming_sessions": ["2026-09-01"], "prerequisites": {"NEW": 1}})
        reasons = candidate_reasons(self.employee, self.event, {"S": 4}, [], "2026-10-01")
        self.assertEqual(["prerequisites", "no_future_session"], reasons)

    def test_lead_without_goal_has_no_target(self):
        self.employee["grade"] = "Lead"
        self.assertIsNone(select_target(self.employee, {}))

    def test_explicit_cross_role_goal_and_next_grade(self):
        profiles = {("R", "Senior"): {"role": "R", "grade": "Senior"},
                    ("SECOND", "Junior"): {"role": "SECOND", "grade": "Junior"}}
        self.assertEqual(profiles["R", "Senior"], select_target(self.employee, profiles))
        self.employee["career_goal"] = {"target_role": "SECOND", "target_grade": "Junior"}
        self.assertEqual(profiles["SECOND", "Junior"], select_target(self.employee, profiles))


class InputNumberTests(unittest.TestCase):
    def test_nonfinite_and_boolean_are_not_numbers(self):
        for value in (float("inf"), float("-inf"), float("nan"), True, False):
            self.assertFalse(is_number(value))
        self.assertTrue(is_number(2))
        self.assertTrue(is_number(1.5))

    def test_nonstandard_json_constants_rejected(self):
        for literal in ("Infinity", "-Infinity", "NaN"):
            with self.assertRaises(ValueError):
                load_json('{"duration_hours": ' + literal + '}')

    def test_json_exponent_overflow_is_invalid_number(self):
        value = load_json('{"duration_hours": 1e400}')["duration_hours"]
        self.assertFalse(is_number(value))

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(ValueError):
            load_json('{"employee_id": "A", "employee_id": "B"}')


if __name__ == "__main__":
    unittest.main()
