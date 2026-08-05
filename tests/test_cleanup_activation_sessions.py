import contextlib
import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import cleanup_activation_sessions as cleanup


class TestCleanupActivationSessions(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        self.session_dir = self.root / "-"
        self.session_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_session(
        self,
        path: Path,
        *,
        prompt=".",
        entrypoint="sdk-cli",
        cwd="/",
        malformed=False,
        include_cwd=True,
    ):
        records = [
            {"type": "queue-operation", "content": prompt},
            {
                "type": "user",
                "message": {"role": "user", "content": prompt},
                "entrypoint": entrypoint,
            },
        ]
        if include_cwd:
            records[1]["cwd"] = cwd
        lines = [json.dumps(record) for record in records]
        if malformed:
            lines.append("{broken")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _session_path(self):
        return self.session_dir / f"{uuid.uuid4()}.jsonl"

    def test_default_dry_run_lists_candidate_and_preserves_file(self):
        candidate = self._session_path()
        self._write_session(candidate)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cleanup.main(["--projects-root", str(self.root)])

        self.assertEqual(result, 0)
        self.assertTrue(candidate.exists())
        self.assertIn(str(candidate), output.getvalue())
        self.assertIn("candidate count: 1", output.getvalue())
        self.assertIn("dry-run: no files deleted", output.getvalue())

    def test_apply_deletes_only_matching_candidates(self):
        candidate = self._session_path()
        wrong_prompt = self._session_path()
        wrong_entrypoint = self._session_path()
        wrong_cwd = self._session_path()
        malformed = self._session_path()
        non_uuid = self.session_dir / "not-a-uuid.jsonl"
        other_path = self.root / "other" / f"{uuid.uuid4()}.jsonl"
        other_path.parent.mkdir()

        self._write_session(candidate)
        self._write_session(wrong_prompt, prompt="other")
        self._write_session(wrong_entrypoint, entrypoint="terminal")
        self._write_session(wrong_cwd, cwd="/Users/example")
        self._write_session(malformed, malformed=True)
        self._write_session(non_uuid)
        self._write_session(other_path)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cleanup.main(["--projects-root", str(self.root), "--apply"])

        self.assertEqual(result, 0)
        self.assertFalse(candidate.exists())
        for preserved in (wrong_prompt, wrong_entrypoint, wrong_cwd, malformed, non_uuid, other_path):
            self.assertTrue(preserved.exists())
        self.assertIn("deleted: 1", output.getvalue())
        self.assertIn("skipped: 0", output.getvalue())
        self.assertIn("failed: 0", output.getvalue())

    def test_cwd_is_optional_when_missing(self):
        candidate = self._session_path()
        self._write_session(candidate, include_cwd=False)

        found = cleanup.find_candidates(self.root)

        self.assertEqual([item.path for item in found], [candidate.resolve()])

    def test_symlink_escape_is_not_a_candidate(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        session_id = str(uuid.uuid4())
        outside_file = outside / f"{session_id}.jsonl"
        self._write_session(outside_file)
        link = self.session_dir / outside_file.name
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable")

        self.assertEqual(cleanup.find_candidates(self.root), [])
        result = cleanup.main(["--projects-root", str(self.root), "--apply"])
        self.assertEqual(result, 0)
        self.assertTrue(outside_file.exists())

    def test_project_bucket_symlink_escape_is_not_scanned(self):
        outside = Path(self.tmp.name) / "outside-bucket"
        outside.mkdir()
        candidate = outside / f"{uuid.uuid4()}.jsonl"
        self._write_session(candidate)
        self.session_dir.rmdir()
        try:
            self.session_dir.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable")

        self.assertEqual(cleanup.find_candidates(self.root), [])
        self.assertTrue(candidate.exists())

    def test_invalid_projects_root_returns_nonzero(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = cleanup.main(["--projects-root", str(self.root / "missing")])

        self.assertNotEqual(result, 0)
        self.assertIn("invalid projects root", output.getvalue())


if __name__ == "__main__":
    unittest.main()
