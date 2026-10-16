"""Artifact safety regressions using only disposable, synthetic files."""

import contextlib
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github/scripts/stage_translation_artifact.py'
sys.path.insert(0, str(SCRIPT.parent))
import stage_translation_artifact as artifact


class TranslationArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        self.staging = self.root / 'staging'
        self.staging.mkdir()
        self.outside = self.root / 'unrelated'
        self.outside.mkdir()
        (self.outside / 'sample.txt').write_text('synthetic unrelated content')
        self.output = self.root / 'github-output'
        self.output.touch()

    def stage(self):
        return artifact.stage_translation_data(self.data, self.staging)

    def assert_refused(self):
        with self.assertRaises((ValueError, OSError)):
            self.stage()
        self.assertEqual(list(self.staging.iterdir()), [])

    def cli(self, **options):
        env = dict(os.environ, GITHUB_OUTPUT=str(self.output))
        return subprocess.run([
            sys.executable, str(SCRIPT), '--data-dir', str(self.data),
            '--staging-parent', str(options.get('staging_parent', self.staging)),
        ], env=env, capture_output=True, text=True, timeout=10)

    def test_copies_only_top_level_regular_toml_files_byte_for_byte(self):
        contents = {'mod.toml': b'[entry]\nraw = "Text"\n',
                    '_glossary.toml': 'label = "木头"\n'.encode()}
        for name, content in contents.items():
            (self.data / name).write_bytes(content)
        (self.data / 'notes.txt').write_text('not a TOML file')
        nested = self.data / 'nested'
        nested.mkdir()
        (nested / 'not-top-level.toml').write_text('nested = true')
        (nested / 'ignored-link.toml').symlink_to(self.outside)

        staged = self.stage()

        self.assertEqual({p.name for p in staged.iterdir()}, set(contents))
        for name, content in contents.items():
            self.assertEqual((staged / name).read_bytes(), content)
            self.assertTrue(stat.S_ISREG((staged / name).lstat().st_mode))
            self.assertFalse(os.path.samefile(staged / name, self.data / name))

    def test_each_run_creates_an_independent_fresh_snapshot(self):
        source = self.data / 'mod.toml'
        source.write_text('value = 1')
        first = self.stage()
        source.write_text('value = 2')
        second = self.stage()
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.staging)
        self.assertEqual((first / 'mod.toml').read_text(), 'value = 1')
        self.assertEqual((second / 'mod.toml').read_text(), 'value = 2')

    def test_rejects_file_symlinks_inside_outside_and_dangling(self):
        regular = self.data / 'regular.toml'
        regular.write_text('value = 1')
        for target in (regular, self.outside / 'sample.txt', self.root / 'missing'):
            with self.subTest(target=target.name):
                link = self.data / 'linked.toml'
                link.symlink_to(target)
                self.assert_refused()
                link.unlink()

    def test_rejects_directory_symlinks_with_or_without_toml_suffix(self):
        (self.data / 'ordinary.toml').write_text('value = 1')
        inner = self.data / 'nested'
        inner.mkdir()
        for name in ('linked.toml', 'linked-directory'):
            for target in (inner, self.outside):
                with self.subTest(name=name, target=target.name):
                    link = self.data / name
                    link.symlink_to(target, target_is_directory=True)
                    self.assert_refused()
                    link.unlink()

    def test_rejects_linked_data_root_including_dangling_links(self):
        self.data.rmdir()
        for target in (self.outside, self.root / 'missing'):
            with self.subTest(target=target.name):
                self.data.symlink_to(target, target_is_directory=True)
                self.assert_refused()
                self.data.unlink()

    def test_rejects_missing_or_non_directory_data_root(self):
        self.data.rmdir()
        self.assert_refused()
        self.data.write_text('not a directory')
        self.assert_refused()

    def test_rejects_directory_named_toml_without_traversing_it(self):
        directory = self.data / 'directory.toml'
        directory.mkdir()
        (directory / 'sample.txt').write_text('must not be copied')
        self.assert_refused()

    def test_rejects_fifo_instead_of_opening_or_hanging(self):
        os.mkfifo(self.data / 'pipe.toml')
        self.assert_refused()

    def test_empty_or_non_toml_data_fails_closed(self):
        self.assert_refused()
        (self.data / 'notes.txt').write_text('nothing to upload')
        self.assert_refused()

    def test_staging_parent_must_be_outside_data(self):
        (self.data / 'mod.toml').write_text('value = 1')
        with self.assertRaisesRegex(ValueError, 'outside'):
            artifact.stage_translation_data(self.data, self.data)
        nested = self.data / 'nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'outside'):
            artifact.stage_translation_data(self.data, nested)
        self.assertEqual(list(nested.iterdir()), [])

    def test_copy_failure_removes_partial_snapshot_and_publishes_no_output(self):
        (self.data / 'mod.toml').write_text('value = 1')

        def fail_copy(source, destination):
            destination.write(b'partial')
            raise OSError('synthetic copy failure')

        with patch.object(artifact.shutil, 'copyfileobj', side_effect=fail_copy), \
                patch.object(sys, 'argv', [str(SCRIPT), '--data-dir', str(self.data),
                                         '--staging-parent', str(self.staging)]), \
                patch.dict(os.environ, GITHUB_OUTPUT=str(self.output)), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(artifact.main(), 1)
        self.assertEqual(list(self.staging.iterdir()), [])
        self.assertEqual(self.output.read_text(), '')

    def test_cli_success_publishes_only_the_completed_snapshot(self):
        (self.data / 'mod.toml').write_text('value = 1')
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = Path(result.stdout.strip())
        self.assertEqual(self.output.read_text(), f'path={staged}\n')
        self.assertEqual(staged.parent, self.staging)
        self.assertEqual((staged / 'mod.toml').read_text(), 'value = 1')

    def test_cli_symlink_failure_exits_nonzero_without_upload_output(self):
        (self.data / 'linked.toml').symlink_to(self.outside, target_is_directory=True)
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.output.read_text(), '')
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_staging_creation_failure_exits_without_upload_output(self):
        (self.data / 'mod.toml').write_text('value = 1')
        result = self.cli(staging_parent=self.root / 'missing-staging-parent')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_text(), '')
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_output_publication_failure_removes_snapshot(self):
        (self.data / 'mod.toml').write_text('value = 1')
        self.output.unlink()
        self.output.mkdir()
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_file_swapped_for_symlink_before_open_is_not_followed(self):
        source = self.data / 'mod.toml'
        source.write_text('value = 1')
        real_open = os.open

        def swap_before_open(path, flags, *args, **kwargs):
            if path == source.name:
                source.unlink()
                source.symlink_to(self.outside / 'sample.txt')
            return real_open(path, flags, *args, **kwargs)

        with patch.object(artifact.os, 'open', side_effect=swap_before_open):
            self.assert_refused()

    def test_resolved_path_escape_after_data_root_swap_is_rejected(self):
        (self.data / 'mod.toml').write_text('value = 1')
        (self.outside / 'mod.toml').write_text('unrelated = true')
        real_listdir = os.listdir

        def swap_root(fd):
            self.data.rename(self.root / 'original-data')
            self.data.symlink_to(self.outside, target_is_directory=True)
            return real_listdir(fd)

        with patch.object(artifact.os, 'listdir', side_effect=swap_root):
            with self.assertRaisesRegex(ValueError, 'escaped'):
                self.stage()
        self.assertEqual(list(self.staging.iterdir()), [])


class TranslationArtifactWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / '.github/workflows/translate.yml').read_text()

    def step(self, name):
        pattern = rf'^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)'
        match = re.search(pattern, self.workflow, re.M | re.S)
        self.assertIsNotNone(match, f'Missing workflow step: {name}')
        return match.group(1)

    def test_upload_requires_successful_staging_even_when_prior_steps_failed(self):
        stage = self.step('Stage translation data for upload')
        self.assertIn('id: stage_translation_data\n', stage)
        self.assertIn('if: always()\n', stage)
        self.assertNotIn('continue-on-error:', stage)
        self.assertIn('ARTIFACT_STAGING_PARENT: ${{ runner.temp }}', stage)
        self.assertIn('python .github/scripts/stage_translation_artifact.py', stage)
        self.assertIn('--data-dir data --staging-parent "$ARTIFACT_STAGING_PARENT"', stage)

        upload = self.step('Upload translation data as artifact')
        condition = re.search(r'^        if: (.+)$', upload, re.M).group(1)
        self.assertEqual(condition, "always() && steps.stage_translation_data.outcome == 'success' "
                         "&& steps.stage_translation_data.outputs.path != ''")
        self.assertIn('path: ${{ steps.stage_translation_data.outputs.path }}/\n', upload)
        self.assertIn('if-no-files-found: error\n', upload)

    def test_logs_remain_independent_and_never_upload_raw_data(self):
        logs = self.step('Upload translation logs as artifact')
        self.assertIn('if: always()\n', logs)
        self.assertNotIn('stage_translation_data', logs)
        self.assertNotIn('data/', logs)
        self.assertIn('.github/log/translation.log', logs)
        self.assertNotIn('data/*.toml', self.workflow[self.workflow.index(
            '      - name: Stage translation data for upload'):])

    def test_regressions_run_in_production_and_in_unprivileged_pr_ci(self):
        self.assertIn("-p 'test_translation_artifact.py'", self.step(
            'Check production translation contracts'))
        ci = (ROOT / '.github/workflows/translation-artifact-tests.yml').read_text()
        self.assertIn('  pull_request:\n', ci)
        self.assertIn('  contents: read\n', ci)
        self.assertIn('persist-credentials: false\n', ci)
        self.assertIn("-p 'test_translation_artifact.py'", ci)
        self.assertNotIn('secrets.', ci)
        self.assertNotIn('workflow_dispatch:', ci)
        self.assertNotIn('schedule:', ci)


if __name__ == '__main__':
    unittest.main()
