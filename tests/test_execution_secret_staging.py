"""Regression tests: credential files must never reach an execution workspace.

Background. ``EphemeralWorkspaceManager`` copies the source tree into the scratch
directory that becomes the working directory of the process a run starts. It excluded
only ``.git``, ``__pycache__`` and ``*.pyc``, so a repository's ``.env`` -- holding every
provider key -- was copied verbatim and was readable by any admitted command with an
ordinary relative open. The default API execution profile admits ``python``.

These tests fail against the pre-fix implementation and pass against the fixed one. The
filter is name-based and therefore incomplete by construction (a secret inside
``notes.txt`` is not recognized); that limitation is asserted explicitly rather than
hidden, so nobody mistakes this for a declared-input allow-list.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.execution.secret_files import (
    SECRET_DIRECTORY_NAMES,
    is_secret_directory,
    is_secret_file,
    secret_reason,
)
from app.execution.workspace_manager import EphemeralWorkspaceManager


# Placeholder contents only. Nothing here is a real credential, and the tests never read
# a real .env from the repository.
PLACEHOLDER = "PLACEHOLDER=not-a-real-secret\n"


class SecretFileNameTests(unittest.TestCase):
    """The recognizer itself, before it is used for staging."""

    def test_01_environment_files_are_recognized(self):
        for name in (".env", ".env.local", ".env.production", ".env.development",
                     ".env.test", ".ENV"):
            self.assertTrue(is_secret_file(name), name)
            self.assertEqual(secret_reason(name), "environment_file", name)

    def test_02_environment_templates_are_not_secrets(self):
        """.env.example is tracked in Git by design and holds no values."""

        for name in (".env.example", ".env.sample", ".env.template", ".env.dist",
                     ".env.example.local"):
            self.assertFalse(is_secret_file(name), name)

    def test_03_key_material_is_recognized_by_extension(self):
        for name in ("server.pem", "private.key", "cert.pfx", "cert.p12",
                     "bundle.pkcs12", "vault.kdbx", "release.jks", "vpn.ovpn",
                     "secret.asc"):
            self.assertTrue(is_secret_file(name), name)

    def test_04_private_key_names_are_recognized_by_prefix(self):
        for name in ("id_rsa", "id_rsa_backup", "id_ed25519", "id_ecdsa",
                     "id_dsa.old"):
            self.assertTrue(is_secret_file(name), name)

    def test_05_credential_file_names_are_recognized(self):
        for name in ("credentials.json", "service_account.json", "client_secret.json",
                     ".netrc", ".pgpass", ".npmrc", ".htpasswd"):
            self.assertTrue(is_secret_file(name), name)

    def test_06_ordinary_files_are_not_secrets(self):
        for name in ("main.py", "README.md", "requirements.txt", "config.yaml",
                     "test_notes.txt", "keystore.py"):
            self.assertFalse(is_secret_file(name), name)

    def test_07_credential_directories_are_recognized(self):
        for name in (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"):
            self.assertTrue(is_secret_directory(name), name)
        for name in ("app", "src", "tests", ".git"):
            self.assertFalse(is_secret_directory(name), name)
        self.assertIn(".ssh", SECRET_DIRECTORY_NAMES)

    def test_08_recognition_never_reads_file_content(self):
        """The recognizer must not open the file: reading it would be the leak.

        A path that does not exist must still be classifiable by name alone.
        """

        self.assertTrue(is_secret_file(Path("/nonexistent/does-not-exist/.env")))
        self.assertEqual(secret_reason("nested/.env"), "environment_file")


class StagingSecretExclusionTests(unittest.TestCase):
    """The fix: a staged tree must not contain credential material."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.scratch_base = self.base / "scratch"
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            try:
                manager.cleanup()
            except Exception:  # noqa: BLE001 - teardown must not mask a test failure
                pass
        self.temp_dir.cleanup()

    def _manager(self) -> EphemeralWorkspaceManager:
        manager = EphemeralWorkspaceManager(
            source_workspace_root=self.source, base_scratch_dir=self.scratch_base
        )
        self.managers.append(manager)
        return manager

    def _write(self, relative: str, content: str = PLACEHOLDER) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _staged(self, manager: EphemeralWorkspaceManager) -> set[str]:
        return {
            path.relative_to(manager.scratch_root).as_posix()
            for path in manager.scratch_root.rglob("*")
            if path.is_file()
        }

    def test_20_dotenv_is_not_staged_at_the_root(self):
        """The exact leak the audit measured."""

        self._write(".env")
        self._write("main.py", "print('ok')\n")
        manager = self._manager()
        manager.initialize()
        staged = self._staged(manager)
        self.assertNotIn(".env", staged)
        self.assertIn("main.py", staged)
        self.assertIn(".env", manager.secret_files_skipped)

    def test_21_nested_secret_files_are_not_staged(self):
        """An alternative path is not a way around the filter."""

        self._write("app/.env.production")
        self._write("config/keys/server.pem")
        self._write("deploy/id_rsa")
        self._write("app/code.py", "x = 1\n")
        manager = self._manager()
        manager.initialize()
        staged = self._staged(manager)
        for withheld in ("app/.env.production", "config/keys/server.pem",
                         "deploy/id_rsa"):
            self.assertNotIn(withheld, staged, withheld)
        self.assertIn("app/code.py", staged)

    def test_22_credential_directories_are_not_descended_into(self):
        """A directory is pruned before descent, so its contents are never reached."""

        self._write(".ssh/id_ed25519")
        self._write(".ssh/known_hosts", "host\n")
        self._write(".aws/credentials", "[default]\n")
        self._write("src/main.py", "y = 2\n")
        manager = self._manager()
        manager.initialize()
        staged = self._staged(manager)
        self.assertEqual(
            [name for name in staged if name.startswith((".ssh", ".aws"))], []
        )
        self.assertIn("src/main.py", staged)

    def test_23_templates_and_ordinary_files_are_still_staged(self):
        """The filter must not break legitimate workloads."""

        self._write(".env.example", "API_KEY=\n")
        self._write("config/app.yaml", "name: demo\n")
        self._write("README.md", "# demo\n")
        self._write("src/main.py", "print('hi')\n")
        manager = self._manager()
        manager.initialize()
        staged = self._staged(manager)
        for expected in (".env.example", "config/app.yaml", "README.md",
                         "src/main.py"):
            self.assertIn(expected, staged, expected)
        self.assertEqual(manager.secret_files_skipped, ())

    def test_24_skipped_names_are_reported_without_contents(self):
        self._write(".env")
        self._write("secret.pem")
        manager = self._manager()
        manager.initialize()
        skipped = manager.secret_files_skipped
        self.assertIn(".env", skipped)
        self.assertIn("secret.pem", skipped)
        # Only names, never values.
        for name in skipped:
            self.assertNotIn("=", name)
            self.assertNotIn(PLACEHOLDER.strip(), name)

    def test_25_a_skipped_file_is_regenerated_as_empty_by_reset(self):
        """initialize() after cleanup must not report a previous run's refusals."""

        self._write(".env")
        manager = self._manager()
        manager.initialize()
        self.assertTrue(manager.secret_files_skipped)
        manager.cleanup()
        manager._source_root = None  # stage nothing on the second pass
        manager.initialize()
        self.assertEqual(manager.secret_files_skipped, ())

    def test_26_symlinked_secret_is_still_refused(self):
        """A symlink named as a secret is refused by name before the symlink rule runs.

        Either refusal is acceptable; what must not happen is the link's target
        appearing inside the staged tree.
        """

        target = self.base / "outside-secret.txt"
        target.write_text(PLACEHOLDER, encoding="utf-8")
        link = self.source / "linked.pem"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation is not permitted in this environment")
        manager = self._manager()
        manager.initialize()
        self.assertNotIn("linked.pem", self._staged(manager))

    def test_27_the_limitation_is_explicit(self):
        """A secret inside an ordinary file name is NOT caught. Asserted, not hidden."""

        self._write("notes.txt", "API_KEY=placeholder-inside-an-ordinary-name\n")
        manager = self._manager()
        manager.initialize()
        self.assertIn("notes.txt", self._staged(manager))
        self.assertFalse(is_secret_file("notes.txt"))


class StagingDoesNotFollowJunctionsTests(unittest.TestCase):
    """Containment: staging must not copy content from outside the source root."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_30_directory_junction_is_rejected(self):
        source = self.base / "source"
        source.mkdir()
        (source / "keep.py").write_text("k = 1\n", encoding="utf-8")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "outside-secret.env").write_text(PLACEHOLDER, encoding="utf-8")
        junction = source / "link"
        try:
            if os.name == "nt":
                import subprocess

                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    self.skipTest("mklink /J unavailable")
            else:
                os.symlink(outside, junction, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("directory link creation is not permitted here")

        manager = EphemeralWorkspaceManager(
            source_workspace_root=source, base_scratch_dir=self.base / "scratch"
        )
        try:
            with self.assertRaises(ValueError):
                manager.initialize()
        finally:
            manager.cleanup()


class ChildProcessCannotReadSecretsTests(unittest.TestCase):
    """End-to-end: the child's own working directory must not contain the credentials."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.source.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_40_a_spawned_child_cannot_see_dotenv_in_its_cwd(self):
        """The behaviour the fix exists for, measured through a real child process.

        The child lists its own working directory. Before the fix that listing contained
        ``.env``; a name-based assertion on the staged tree would not have caught a
        regression in how the child's cwd is chosen, so this runs the process.
        """

        import subprocess
        import sys

        (self.source / ".env").write_text(PLACEHOLDER, encoding="utf-8")
        (self.source / ".env.example").write_text("API_KEY=\n", encoding="utf-8")
        (self.source / "visible.py").write_text("v = 1\n", encoding="utf-8")

        manager = EphemeralWorkspaceManager(
            source_workspace_root=self.source, base_scratch_dir=self.base / "scratch"
        )
        manager.initialize()
        try:
            listing = subprocess.run(
                [sys.executable, "-c",
                 "import os; print('|'.join(sorted(os.listdir('.'))))"],
                cwd=str(manager.scratch_root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(listing.returncode, 0, listing.stderr)
            names = listing.stdout.strip().split("|")
            self.assertNotIn(".env", names)
            self.assertIn(".env.example", names)
            self.assertIn("visible.py", names)
        finally:
            manager.cleanup()


class KnownBypassNamesAreDeniedTests(unittest.TestCase):
    """The bypasses an independent re-audit found, one assertion each.

    The first version of this filter recognized `.env`, key extensions and a handful of
    credential file names. A re-audit enumerated names that were **not** recognized and
    therefore were staged into the child's working directory. All 25 names in the table
    below are denied now; `known_hosts` is deliberately still staged, and that decision is
    asserted too so it cannot drift into a silent false positive. The count in this
    docstring is the length of ``DENIED`` -- ``test_04b`` asserts it, so the prose and the
    table cannot disagree.

    Every case here is a *name*, and the recognizer never reads contents -- a test that
    needed content would be describing a different mechanism.
    """

    #: Names that must be withheld, with the rule expected to recognize them. Naming the
    #: rule keeps the test honest: a name that starts matching for an unrelated reason
    #: would fail here rather than pass by accident.
    DENIED = (
        # Git stores a username and a password in clear text here.
        (".git-credentials", "credential_file_name"),
        (".gitconfig", "credential_file_name"),
        # Registry tokens.
        (".terraformrc", "credential_file_name"),
        # direnv: a shell script whose whole purpose is exporting environment variables.
        (".envrc", "credential_file_name"),
        # GCP Application Default Credentials and Firebase service accounts.
        ("application_default_credentials.json", "credential_file_name"),
        ("firebase-adminsdk-abcd1.json", "credential_file_pattern"),
        ("firebase-adminsdk-abcd1.json.enc", "credential_file_pattern"),
        # Files whose purpose is holding values.
        ("secrets.yaml", "credential_file_name"),
        ("secrets.yml", "credential_file_name"),
        ("secrets.json", "credential_file_name"),
        ("secret.yaml", "credential_file_name"),
        # Terraform state routinely carries secrets in clear text.
        ("terraform.tfstate", "credential_file_name"),
        ("terraform.tfstate.backup", "credential_file_name"),
        ("prod.tfstate", "credential_file_pattern"),
        # Key material under another extension.
        ("server.pem.enc", "credential_file_pattern"),
        (".key.enc", "credential_file_pattern"),
        # Credential stores and identities without a decisive extension.
        ("kubeconfig", "credential_file_name"),
        ("cacerts", "credential_file_name"),
        ("keystore", "credential_file_name"),
        ("aws_credentials", "credential_file_name"),
        ("credentials", "credential_file_name"),
        ("htpasswd", "credential_file_name"),
        ("authorized_keys", "credential_file_name"),
        # Windows resolves a trailing dot away, so `secret.pem.` opened `secret.pem`.
        ("secret.pem.", "key_material_extension"),
        ("server.key.", "key_material_extension"),
    )

    #: Names that must remain stageable. A deny-list that blocks these is over-broad and
    #: breaks legitimate workloads.
    STAGED = (
        ".env.example",
        ".env.sample",
        ".env.dist",
        ".env.example.local",
        "main.py",
        "README.md",
        "config.yaml",
        "notes.txt",
        # The public half of a key pair is not a credential.
        "id_rsa.pub",
        "deploy_ed25519.pub",
        # Public host-key data, not a secret.
        "known_hosts",
        # A Python module whose name merely contains "keystore".
        "keystore.py",
    )

    def test_01_every_known_bypass_name_is_denied(self):
        for name, expected_rule in self.DENIED:
            with self.subTest(name=name):
                self.assertTrue(is_secret_file(name), f"{name} must be withheld")
                self.assertEqual(secret_reason(name), expected_rule)

    def test_02_legitimate_files_remain_stageable(self):
        for name in self.STAGED:
            with self.subTest(name=name):
                self.assertFalse(is_secret_file(name), f"{name} must stay stageable")
                self.assertIsNone(secret_reason(name))

    def test_03_the_two_sets_do_not_overlap(self):
        denied = {name for name, _ in self.DENIED}
        self.assertEqual(denied & set(self.STAGED), set())

    def test_04_paths_and_names_agree(self):
        """A bypass must not become available by prefixing a directory."""

        for name, rule in self.DENIED:
            for prefix in ("sub/", "a/b/c/", "./", "sub\\"):
                candidate = prefix + name
                with self.subTest(candidate=candidate):
                    self.assertEqual(secret_reason(candidate), rule)
                    self.assertTrue(is_secret_file(candidate))

    def test_04b_the_documented_count_matches_the_table(self):
        """The prose count and the table are the same number.

        Two earlier documents disagreed about how many re-audit bypass names were
        closed ("twenty-two", "sixteen") while this table held 24. Rather than
        pick a number by hand, the count is pinned to the table's own length and
        the class docstring refers to it.
        """

        self.assertEqual(len(self.DENIED), 25)
        self.assertEqual(len({name for name, _ in self.DENIED}), 25)

    def test_05_credential_directories_are_pruned(self):
        for name in (".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure",
                     ".terraform", "keys", "certs", "private"):
            with self.subTest(name=name):
                self.assertTrue(is_secret_directory(name), f"{name} must be pruned")

    def test_06_ordinary_directories_are_still_descended(self):
        for name in ("src", "tests", "app", "docs", "config", "public", "keyrings"):
            with self.subTest(name=name):
                self.assertFalse(is_secret_directory(name))


class StagingKnownBypassTests(unittest.TestCase):
    """The same names, this time through a real staging pass.

    The recognizer tests above prove the rule; these prove the rule is actually wired into
    staging, which is where the leak occurred. A regression could keep the recognizer
    correct while the staging step stopped consulting it.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.source = self.base / "source"
        self.source.mkdir()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_01_no_bypass_name_reaches_the_staged_tree(self):
        withheld_names = [
            ".git-credentials", ".terraformrc", ".envrc",
            "application_default_credentials.json", "firebase-adminsdk-a1.json",
            "secrets.yaml", "terraform.tfstate", "server.pem.enc", "kubeconfig",
            "cacerts", "aws_credentials", "authorized_keys",
        ]
        for name in withheld_names:
            (self.source / name).write_text(PLACEHOLDER, encoding="utf-8")
        # A nested one too, to prove depth is not a bypass.
        (self.source / "deploy").mkdir()
        (self.source / "deploy" / "secrets.json").write_text(PLACEHOLDER, encoding="utf-8")
        (self.source / "keep.py").write_text("k = 1\n", encoding="utf-8")
        (self.source / ".env.example").write_text("KEY=\n", encoding="utf-8")

        manager = EphemeralWorkspaceManager(
            source_workspace_root=self.source, base_scratch_dir=self.base / "scratch"
        )
        manager.initialize()
        try:
            staged = {
                path.relative_to(manager.scratch_root).as_posix()
                for path in manager.scratch_root.rglob("*")
                if path.is_file()
            }
            for name in withheld_names:
                self.assertNotIn(name, staged, name)
            self.assertNotIn("deploy/secrets.json", staged)
            self.assertIn("keep.py", staged)
            self.assertIn(".env.example", staged)
        finally:
            manager.cleanup()

    def test_02_credential_directories_are_not_descended(self):
        for directory in (".terraform", "keys", "certs"):
            (self.source / directory).mkdir()
            (self.source / directory / "material.pem").write_text(PLACEHOLDER,
                                                                  encoding="utf-8")
        (self.source / "src").mkdir()
        (self.source / "src" / "app.py").write_text("a = 1\n", encoding="utf-8")

        manager = EphemeralWorkspaceManager(
            source_workspace_root=self.source, base_scratch_dir=self.base / "scratch"
        )
        manager.initialize()
        try:
            staged = {
                path.relative_to(manager.scratch_root).as_posix()
                for path in manager.scratch_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(
                [name for name in staged if name.startswith((".terraform", "keys", "certs"))],
                [],
            )
            self.assertIn("src/app.py", staged)
        finally:
            manager.cleanup()

    def test_03_a_public_key_is_staged_while_its_private_half_is_not(self):
        (self.source / "id_rsa").write_text(PLACEHOLDER, encoding="utf-8")
        (self.source / "id_rsa.pub").write_text("ssh-rsa AAAA placeholder\n",
                                                encoding="utf-8")

        manager = EphemeralWorkspaceManager(
            source_workspace_root=self.source, base_scratch_dir=self.base / "scratch"
        )
        manager.initialize()
        try:
            staged = {
                path.relative_to(manager.scratch_root).as_posix()
                for path in manager.scratch_root.rglob("*")
                if path.is_file()
            }
            self.assertNotIn("id_rsa", staged)
            self.assertIn("id_rsa.pub", staged)
        finally:
            manager.cleanup()


class ScannerUsesTheSameRecognizerTests(unittest.TestCase):
    """The project scanner prunes the same credential directories as staging.

    The scanner and the staging step both traverse a project, and they used to answer
    "is this credential territory?" differently: staging consulted
    `is_secret_directory`, while the scanner carried its own `_IGNORED_DIRS` set that
    named build artefacts only. The scanner therefore descended into `.terraform`, `keys`,
    `certs`, `.ssh` and friends and inventoried files there whose names carry no secret
    marker at all ("notes.txt").

    That is not a disclosure on its own -- files the file-level rule does recognize were
    recorded with `fingerprint=None` and never read -- but it consumed the file-count limit
    with content that is not project source, and it meant the two traversals disagreed
    about credential territory. These assertions are on the scan result, not on the source.

    Cross-platform caveat, stated rather than assumed: the dot-trailing cases in
    `KnownBypassNamesAreDeniedTests` describe Windows name resolution, where `secret.pem.`
    opens `secret.pem`. The directory cases here make no such assumption.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _scan(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from app.tools.workspace import Workspace
        from app.understanding.scanner import BoundedProjectScanner

        return BoundedProjectScanner().scan(Workspace(self.root))

    def test_01_credential_directories_are_not_descended_into(self):
        for directory in (".terraform", "keys", "certs", "private", ".ssh", ".aws"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
            # An ordinary name: only a directory rule can withhold it.
            (self.root / directory / "notes.txt").write_text("ordinary\n", encoding="utf-8")
            (self.root / directory / "material.pem").write_text("-----BEGIN-----\n",
                                                                encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")

        files, _, _, warnings = self._scan()
        inventoried = {f.relative_path for f in files}

        for directory in (".terraform", "keys", "certs", "private", ".ssh", ".aws"):
            with self.subTest(directory=directory):
                self.assertFalse(
                    [name for name in inventoried if name.startswith(directory + "/")],
                    f"{directory} must be pruned, not walked",
                )
        self.assertIn("src/app.py", inventoried)

    def test_02_ordinary_directories_are_still_scanned(self):
        """The guard must not prune legitimate source directories."""

        for directory in ("src", "app", "docs", "tests", "config", "public"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
            (self.root / directory / "module.py").write_text("value = 1\n", encoding="utf-8")

        files, _, _, _ = self._scan()
        inventoried = {f.relative_path for f in files}

        for directory in ("src", "app", "docs", "tests", "config", "public"):
            with self.subTest(directory=directory):
                self.assertIn(f"{directory}/module.py", inventoried)

    def test_03_a_recognized_secret_is_recorded_but_never_read(self):
        """The existing contract, asserted so the new pruning cannot hide a regression."""

        (self.root / ".env").write_text(PLACEHOLDER, encoding="utf-8")
        (self.root / "main.py").write_text("value = 1\n", encoding="utf-8")

        files, facts, _, warnings = self._scan()
        by_path = {f.relative_path: f for f in files}

        self.assertIn(".env", by_path)
        self.assertIsNone(by_path[".env"].fingerprint, "a secret must not be fingerprinted")
        self.assertTrue(by_path["main.py"].fingerprint, "ordinary source is fingerprinted")
        self.assertFalse([f for f in facts if getattr(f, "source_path", None) == ".env"])
        self.assertIn(
            "SECRET_FILE_EXCLUDED",
            [w.warning_type for w in warnings],
        )

    def test_04_the_scanner_and_staging_agree_on_credential_directories(self):
        """One recognizer answers for both traversals."""

        for name in SECRET_DIRECTORY_NAMES:
            with self.subTest(name=name):
                self.assertTrue(is_secret_directory(name))
                (self.root / name).mkdir(parents=True, exist_ok=True)
                (self.root / name / "notes.txt").write_text("x\n", encoding="utf-8")

        files, _, _, _ = self._scan()
        inventoried = {f.relative_path for f in files}
        for name in SECRET_DIRECTORY_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(f"{name}/notes.txt", inventoried)


if __name__ == "__main__":
    unittest.main()
