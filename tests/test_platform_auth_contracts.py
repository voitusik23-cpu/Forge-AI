"""Authentication trust-boundary contract tests (Stage 1 / Step 4).

These assert the **shape of the boundary**, not a credential check, because no
credential check ships in Step 4: the authentication mechanism is open decision O-2.
A fake verifier is used deliberately and is legitimate here -- the property under test
is that the verifier port is the only way an identity can exist, not that any
particular credential scheme works.

No database is required, so this suite runs everywhere. Real PostgreSQL coverage of the
Application Services layer is the next implementation task's deliverable
(design section 12).

Contract: docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md sections 3, 8, 11.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import unittest

from app.platform.principal import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthenticationMethod,
    AuthenticationVerifier,
    Credential,
    SubjectKind,
)
import app.platform.principal as principal_module

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PRINCIPAL_PATH = REPO_ROOT / "app" / "platform" / "principal.py"


def _sentinel():
    """The module-private creation token.

    Reached through the module object rather than a re-export, because that is exactly
    the friction the design wants: a verifier implementation imports the private name
    explicitly, and ordinary code has no reason to.
    """

    return principal_module._PRINCIPAL_SENTINEL


def _docstrings(tree: ast.AST) -> set[str]:
    """Every docstring in a module, so a text scan can skip documentation."""

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.add(doc)
    return found


def _principal(subject_id: str = "user-1", **kwargs) -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal.create(
        subject_id,
        subject_kind=kwargs.get("subject_kind", SubjectKind.USER),
        method=kwargs.get("method", AuthenticationMethod.SESSION),
        verified_at=kwargs.get("verified_at"),
        principal_token=_sentinel(),
    )


class FakeVerifier:
    """A verifier that answers from a fixed table.

    It exists to exercise the *port*, not to be a mechanism. It is fail-closed on every
    path the design requires (section 3.5), so the tests below can assert the refusals
    without inventing cryptography.
    """

    def __init__(self, tokens: dict[str, str] | None = None, *, available: bool = True):
        self._tokens = dict(tokens or {})
        self._available = available

    async def verify(self, credential: Credential) -> AuthenticatedPrincipal:
        if not self._available:
            raise AuthenticationError("verifier unavailable")
        if not isinstance(credential, Credential):
            raise AuthenticationError("missing credential")
        subject = self._tokens.get(credential.material)
        if subject is None:
            # Unknown and malformed are the same answer on purpose.
            raise AuthenticationError("credential could not be resolved")
        return AuthenticatedPrincipal.create(
            subject,
            subject_kind=SubjectKind.USER,
            method=AuthenticationMethod.SESSION,
            principal_token=_sentinel(),
        )


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class PrincipalShapeTests(unittest.TestCase):
    """A principal says WHO, and nothing else."""

    def test_001_a_principal_is_immutable(self):
        principal = _principal()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            principal.subject_id = "other"  # type: ignore[misc]

    def test_002_a_principal_carries_no_authority(self):
        """The four fields, and no room for a fifth that would be authority.

        This is the load-bearing assertion of the whole boundary: if a principal could
        carry an organization, a role, or a scope, then "verified identity" and
        "authorized operation" would be the same object and the authorization step
        could be skipped by constructing one.
        """

        names = {f.name for f in dataclasses.fields(AuthenticatedPrincipal)}
        self.assertEqual(
            names, {"subject_id", "subject_kind", "method", "verified_at"}
        )
        for forbidden in (
            "organization_id",
            "project_id",
            "role",
            "roles",
            "scopes",
            "permissions",
            "workspace",
            "workspace_root",
            "email",
            "display_name",
            "credential",
            "token",
            "is_admin",
        ):
            self.assertNotIn(forbidden, names)

    def test_003_a_principal_has_no_attribute_that_reads_as_authorization(self):
        principal = _principal()
        for forbidden in (
            "organization_id",
            "project_id",
            "role",
            "scopes",
            "workspace_root",
            "is_admin",
            "can",
            "may",
        ):
            self.assertFalse(
                hasattr(principal, forbidden),
                f"a principal must not expose {forbidden!r}",
            )


class PrincipalCreationTests(unittest.TestCase):
    """Only the verifier port may produce an identity."""

    def test_010_the_direct_constructor_refuses(self):
        with self.assertRaises(TypeError) as caught:
            AuthenticatedPrincipal(  # type: ignore[call-arg]
                "user-1", SubjectKind.USER, AuthenticationMethod.SESSION, None
            )
        self.assertIn("cannot be constructed directly", str(caught.exception))

    def test_011_creation_without_the_sentinel_refuses(self):
        with self.assertRaises(PermissionError) as caught:
            AuthenticatedPrincipal.create(
                "user-1",
                subject_kind=SubjectKind.USER,
                method=AuthenticationMethod.SESSION,
            )
        self.assertIn("authentication sentinel", str(caught.exception))

    def test_012_a_wrong_token_refuses(self):
        with self.assertRaises(PermissionError):
            AuthenticatedPrincipal.create(
                "user-1",
                subject_kind=SubjectKind.USER,
                method=AuthenticationMethod.SESSION,
                principal_token=object(),
            )

    def test_013_dataclasses_replace_cannot_forge_a_copy(self):
        """Another route to an unverified identity, closed.

        ``dataclasses.replace`` is the obvious way to "adjust" a value object, and it
        would bypass the sentinel if the class accepted the generated ``__init__``.
        """

        principal = _principal()
        with self.assertRaises(TypeError):
            dataclasses.replace(principal, subject_id="attacker")  # type: ignore[type-var]

    def test_014_a_malformed_subject_id_is_refused(self):
        for bad in ("", "   ", "has space", "a" * 200, None, 7, "bad/slash"):
            with self.assertRaises(AuthenticationError, msg=repr(bad)):
                AuthenticatedPrincipal.create(
                    bad,  # type: ignore[arg-type]
                    subject_kind=SubjectKind.USER,
                    method=AuthenticationMethod.SESSION,
                    principal_token=_sentinel(),
                )

    def test_015_an_unknown_kind_or_method_is_refused(self):
        for kwargs in (
            {"subject_kind": "user", "method": AuthenticationMethod.SESSION},
            {"subject_kind": SubjectKind.USER, "method": "session"},
        ):
            with self.assertRaises(AuthenticationError, msg=repr(kwargs)):
                AuthenticatedPrincipal.create(
                    "user-1", principal_token=_sentinel(), **kwargs  # type: ignore[arg-type]
                )

    def test_016_a_principal_is_a_value(self):
        first = _principal("u1")
        second = _principal("u1", verified_at=first.verified_at)
        third = _principal("u2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len({first, second, third}), 2)

    def test_017_every_subject_kind_and_method_is_representable(self):
        for kind in SubjectKind:
            for method in AuthenticationMethod:
                principal = AuthenticatedPrincipal.create(
                    f"subject-{kind.value}",
                    subject_kind=kind,
                    method=method,
                    principal_token=_sentinel(),
                )
                self.assertIs(principal.subject_kind, kind)
                self.assertIs(principal.method, method)


class CredentialTests(unittest.TestCase):
    """The credential envelope is opaque and never rendered."""

    def test_020_an_empty_credential_is_refused(self):
        for bad in ("", "   ", None, 7):
            with self.assertRaises(AuthenticationError, msg=repr(bad)):
                Credential(bad)  # type: ignore[arg-type]

    def test_021_the_material_never_appears_in_repr(self):
        credential = Credential("super-secret-material", "bearer")
        rendered = repr(credential)
        self.assertNotIn("super-secret-material", rendered)
        self.assertIn("redacted", rendered)

    def test_022_the_envelope_does_not_interpret_the_material(self):
        """No format validation, because that would invent the mechanism.

        The envelope accepts anything non-empty; interpreting it belongs to the
        verifier (O-2). A shape check here would silently become a contract.
        """

        for material in ("a", "not-a-jwt", "Bearer x", "x" * 5000):
            self.assertEqual(Credential(material).material, material)


class VerifierPortTests(unittest.TestCase):
    """The port is usable, and every refusal yields no principal."""

    def test_030_a_resolvable_credential_yields_a_principal(self):
        verifier = FakeVerifier({"good-token": "user-1"})
        self.assertIsInstance(verifier, AuthenticationVerifier)
        principal = _run(verifier.verify(Credential("good-token")))
        self.assertEqual(principal.subject_id, "user-1")
        self.assertIs(principal.subject_kind, SubjectKind.USER)

    def test_031_an_unknown_credential_is_refused(self):
        verifier = FakeVerifier({"good-token": "user-1"})
        with self.assertRaises(AuthenticationError):
            _run(verifier.verify(Credential("wrong-token")))

    def test_032_an_unavailable_verifier_refuses_rather_than_degrading(self):
        """An outage must not become a permissive path."""

        verifier = FakeVerifier({"good-token": "user-1"}, available=False)
        with self.assertRaises(AuthenticationError):
            _run(verifier.verify(Credential("good-token")))

    def test_033_a_refusal_carries_no_subject(self):
        verifier = FakeVerifier()
        with self.assertRaises(AuthenticationError) as caught:
            _run(verifier.verify(Credential("anything")))
        self.assertFalse(hasattr(caught.exception, "principal"))
        self.assertFalse(hasattr(caught.exception, "subject_id"))
        self.assertNotIn("anything", str(caught.exception))

    def test_034_a_missing_credential_is_a_refusal_not_an_anonymous_principal(self):
        """There is no anonymous principal, and this asserts the shape of that.

        A ``None`` never reaches the port by type, and an empty envelope is refused at
        construction, so "no credential" cannot be represented as a successful
        verification.
        """

        with self.assertRaises(AuthenticationError):
            Credential(None)  # type: ignore[arg-type]
        with self.assertRaises(AuthenticationError):
            Credential("")

    def test_035_the_port_has_exactly_one_method(self):
        methods = [
            name
            for name in dir(AuthenticationVerifier)
            if not name.startswith("_")
        ]
        self.assertEqual(methods, ["verify"])


class LayeringTests(unittest.TestCase):
    """The module stays inside its layer, checked structurally rather than by review."""

    @classmethod
    def setUpClass(cls):
        cls.source = PRINCIPAL_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source, filename=str(PRINCIPAL_PATH))

    def test_040_imports_no_driver_no_core_and_no_http_framework(self):
        forbidden_roots = {
            "asyncpg",
            "psycopg",
            "psycopg2",
            "sqlalchemy",
            "alembic",
            "fastapi",
            "flask",
            "starlette",
            "requests",
            "httpx",
            "jwt",
            "jose",
            "boto3",
        }
        imported: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & forbidden_roots, set(),
            "the authentication boundary must not depend on a driver, an HTTP "
            "framework, or a token library",
        )

    def test_041_imports_nothing_from_core(self):
        """Core stays Platform-independent, so Platform's identity contract must not
        reach into Core's execution plane to describe a subject."""

        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("app.execution")
                    or node.module.startswith("app.runtime")
                    or node.module.startswith("app.api"),
                    f"the principal contract must not import {node.module}",
                )

    def test_042_only_the_platform_package_imports_the_principal_module(self):
        """Nothing under ``app/`` outside ``app/platform/`` may depend on it.

        This is the same direction rule the domain contracts carry, extended to the
        identity contract: Core must not learn who a Platform subject is.
        """

        offenders: list[str] = []
        for path in (REPO_ROOT / "app").rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative.startswith("app/platform/"):
                continue
            text = path.read_text(encoding="utf-8")
            if "platform.principal" in text or "platform import principal" in text:
                offenders.append(relative)
        self.assertEqual(offenders, [])

    def test_043_the_module_declares_no_credential_mechanism(self):
        """No hashing, no token parsing, no comparison of secrets.

        A module that hashed or compared anything would be implementing O-2 by
        accident, and the design says it must not.
        """

        for forbidden in (
            "hashlib",
            "hmac",
            "secrets",
            "bcrypt",
            "argon2",
            "jwt.decode",
            "base64",
        ):
            self.assertNotIn(
                forbidden, self.source,
                f"the principal contract must not contain {forbidden!r}",
            )


class BoundaryStatementTests(unittest.TestCase):
    """The two values most likely to be mistaken for authentication are named."""

    def test_050_a_postgres_setting_is_not_a_principal(self):
        """``forge.user_id`` cannot produce an identity.

        There is no code path from a settings string to a principal: the only producer
        is the verifier port, and this module never reads a setting.

        Checked against the compiled code rather than the file text, because the module
        docstring *names* ``forge.user_id`` in order to say it is not proof. A textual
        check would forbid the explanation along with the defect.
        """

        tree = ast.parse(PRINCIPAL_PATH.read_text(encoding='utf-8'),
                         filename=str(PRINCIPAL_PATH))
        documentation = _docstrings(tree)
        literals: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                # Skip docstrings: they are documentation, not behaviour.
                if node.value in documentation:
                    continue
                literals.add(node.value)
        for forbidden in (
            "current_setting",
            "set_config",
            "forge.user_id",
            "forge.organization_id",
            "forge.system_scope",
        ):
            self.assertFalse(
                any(forbidden in literal for literal in literals),
                f"the principal contract must not read {forbidden!r}",
            )
        self.assertTrue(literals)

    def test_051_a_client_supplied_identifier_is_not_a_principal(self):
        """A raw string cannot be promoted to a principal without the sentinel.

        This is the property that makes "the client sent us a user_id" harmless: the
        value would have to pass through a verifier, and the verifier is the component
        that decides whether the credential belongs to it.
        """

        with self.assertRaises(PermissionError):
            AuthenticatedPrincipal.create(
                "user-1",
                subject_kind=SubjectKind.USER,
                method=AuthenticationMethod.SESSION,
                principal_token="user-1",  # a client value is not a token
            )

    def source_of_module(self) -> str:
        return PRINCIPAL_PATH.read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
