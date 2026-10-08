"""Stage 1 / Step 1: Platform domain contract invariants.

These tests pin the guarantees the Stage 1 domain layer must hold before any
persistence exists. They cover the containment invariant, the separation of
Platform and Core run identities, the absence of money and credential material,
the open decisions that must stay open, and the one-way architectural boundary
between the Platform domain and Core.

Nothing here tests persistence, because none exists.
"""

from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timedelta, timezone
import pathlib
import unittest

from app.platform import (
    APIKey,
    APIKeyStatus,
    DomainContractError,
    Membership,
    MembershipRole,
    MembershipStatus,
    Organization,
    OrganizationStatus,
    Project,
    ProjectStatus,
    ProviderAccount,
    ProviderAccountStatus,
    RunRecord,
    RunRecordStatus,
    UsageRecord,
    User,
    UserStatus,
    new_id,
    utc_now,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
PLATFORM_DIR = REPO / "app" / "platform"

# Field-name fragments that must never appear on a Stage 1 domain record.
FINANCIAL_FRAGMENTS = (
    "cost",
    "price",
    "charge",
    "wallet",
    "balance",
    "margin",
    "revenue",
    "invoice",
    "payment",
    "credit",
    "billing",
)

# Credential material that must never be a persisted domain field.
#
# `token` is deliberately not a bare fragment: the usage contract legitimately
# records `input_tokens`, `output_tokens`, and `cached_tokens`, which are
# physical measurements rather than credentials. Only credential-shaped uses of
# the word are forbidden, and those are listed explicitly.
SECRET_FRAGMENTS = (
    "secret",
    "password",
    "passwd",
    "hash",
    "ciphertext",
    "private_key",
    "access_token",
    "refresh_token",
    "api_token",
    "bearer_token",
    "session_token",
    "token_hash",
    "api_key_value",
)

# Exact field names that would store a usable credential if they existed.
FORBIDDEN_CREDENTIAL_FIELDS = (
    "token",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "plaintext",
    "secret",
)


def field_names(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def declared_field_names(class_name: str) -> list[str]:
    """Return the field names one domain class actually declares.

    Read from the source with an AST walk so annotations, ``ClassVar`` entries,
    and docstring prose are never confused with stored data. This is what makes a
    "no financial field" assertion mean *no field* rather than *no such word
    anywhere in the file*.
    """
    path = PLATFORM_DIR / "models.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            found: list[str] = []
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name
                ):
                    found.append(statement.target.id)
            return found
    raise AssertionError(f"class {class_name} not found in {path}")


def fragment_hits(names, fragments) -> list[str]:
    """Return the ``field: fragment`` pairs where a fragment appears in a field."""
    hits: list[str] = []
    for name in names:
        for fragment in fragments:
            if fragment in name.lower():
                hits.append(f"{name}:{fragment}")
    return hits


ALL_DOMAIN_CLASSES = (
    "User",
    "Organization",
    "Membership",
    "Project",
    "ProviderAccount",
    "APIKey",
    "RunRecord",
    "UsageRecord",
)


class TenantContainmentTests(unittest.TestCase):
    """A, B, C, D, K: tenant-owned records require an explicit organization_id."""

    TENANT_OWNED = (Membership, Project, ProviderAccount, APIKey, RunRecord, UsageRecord)

    def test_every_tenant_owned_record_declares_organization_id(self) -> None:
        """K: containment is an explicit field, not an implied one."""
        for cls in self.TENANT_OWNED:
            with self.subTest(record=cls.__name__):
                self.assertIn("organization_id", field_names(cls))

    def test_organization_id_is_required_for_every_tenant_owned_record(self) -> None:
        """A: a missing organization_id is refused, not defaulted."""
        cases = {
            "Membership": lambda: Membership(
                id=new_id(),
                organization_id="",
                user_id=new_id(),
                role=MembershipRole.MEMBER,
                created_at=utc_now(),
            ),
            "Project": lambda: Project(
                id=new_id(), organization_id="", name="p", created_at=utc_now()
            ),
            "APIKey": lambda: APIKey(
                id=new_id(),
                organization_id="",
                created_by_user_id=new_id(),
                name="k",
                created_at=utc_now(),
            ),
            "RunRecord": lambda: RunRecord(
                id=new_id(), organization_id="", project_id=new_id(), created_at=utc_now()
            ),
            "UsageRecord": lambda: UsageRecord(
                id=new_id(),
                organization_id="",
                run_record_id=new_id(),
                core_run_id="core-1",
                attempt_number=0,
                created_at=utc_now(),
            ),
        }
        for label, build in cases.items():
            with self.subTest(record=label):
                with self.assertRaises(DomainContractError):
                    build()

    def test_project_cannot_be_created_without_organization_id(self) -> None:
        """B: a project is owned by a tenant, never by a user directly."""
        with self.assertRaises(DomainContractError):
            Project.create(organization_id="", name="orphan")
        with self.assertRaises(TypeError):
            Project(id=new_id(), name="no-tenant", created_at=utc_now())  # type: ignore[call-arg]

    def test_provider_account_requires_organization_id_and_secret_ref(self) -> None:
        """C: both the tenant and the credential reference are mandatory."""
        org = Organization.create(name="Org")
        self.assertIsNotNone(
            ProviderAccount.create(
                provider_name="deepseek",
                secret_ref="secret-reference",
                organization_id=org.id,
            )
        )
        with self.assertRaises(DomainContractError):
            ProviderAccount.create(
                provider_name="deepseek", secret_ref="", organization_id=org.id
            )

    def test_provider_account_allows_a_system_owned_account(self) -> None:
        """A system-owned reference has no tenant, which is expressible."""
        account = ProviderAccount.create(provider_name="mock", secret_ref="ref-1")
        self.assertIsNone(account.organization_id)

    def test_api_key_cannot_be_created_without_organization_id(self) -> None:
        """D: a key always belongs to exactly one organization."""
        with self.assertRaises(DomainContractError):
            APIKey.create(organization_id="", created_by_user_id=new_id(), name="ci")
        with self.assertRaises(DomainContractError):
            APIKey.create(organization_id=new_id(), created_by_user_id="", name="ci")

    def test_user_is_an_identity_entity_without_organization_id(self) -> None:
        """K: a user is global; tenancy comes from membership, not from the user."""
        self.assertNotIn("organization_id", field_names(User))
        self.assertIn("organization_id", field_names(Membership))


class RunIdentitySeparationTests(unittest.TestCase):
    """E: Platform identity and Core correlation identity are distinct."""

    def setUp(self) -> None:
        self.org = Organization.create(name="Org")
        self.project = Project.create(organization_id=self.org.id, name="Proj")

    def test_run_record_carries_both_identities(self) -> None:
        """E: the record exposes a Platform id and a Core correlation id."""
        names = field_names(RunRecord)
        self.assertIn("id", names)
        self.assertIn("core_run_id", names)
        record = RunRecord.create(
            organization_id=self.org.id, project_id=self.project.id
        )
        self.assertTrue(record.id)
        self.assertIsNone(record.core_run_id)

    def test_the_two_identities_cannot_be_the_same_value(self) -> None:
        """E: collapsing the identities is refused rather than stored."""
        with self.assertRaises(DomainContractError):
            RunRecord(
                id="same-id",
                organization_id=self.org.id,
                project_id=self.project.id,
                created_at=utc_now(),
                status=RunRecordStatus.RUNNING,
                core_run_id="same-id",
                started_at=utc_now(),
            )

    def test_a_started_run_requires_a_core_run_id_and_a_start_time(self) -> None:
        """E: a non-queued run must be able to describe what Core did."""
        with self.assertRaises(DomainContractError):
            RunRecord(
                id=new_id(),
                organization_id=self.org.id,
                project_id=self.project.id,
                created_at=utc_now(),
                status=RunRecordStatus.RUNNING,
            )
        record = RunRecord(
            id=new_id(),
            organization_id=self.org.id,
            project_id=self.project.id,
            created_at=utc_now(),
            status=RunRecordStatus.RUNNING,
            core_run_id="core-run-1",
            started_at=utc_now(),
        )
        self.assertEqual(record.core_run_id, "core-run-1")
        self.assertNotEqual(record.core_run_id, record.id)

    def test_a_run_record_is_not_a_core_run_or_a_run_store(self) -> None:
        """The record has no execution, phase, or workspace-activity fields."""
        names = field_names(RunRecord)
        for forbidden in ("events", "phase", "execution_results", "run_scope",
                          "workspace", "commands", "allowed_tool_ids"):
            with self.subTest(field=forbidden):
                self.assertNotIn(forbidden, names)


class RunLifecycleExpressivenessTests(unittest.TestCase):
    """J: O-7 stays open, but the model can express every required outcome."""

    REQUIRED_OUTCOMES = ("queued", "running", "succeeded", "failed", "cancelled",
                         "timed_out", "interrupted")

    def test_every_required_outcome_is_expressible(self) -> None:
        """J: R-6 requires these outcomes to be representable."""
        values = {member.value for member in RunRecordStatus}
        for outcome in self.REQUIRED_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertIn(outcome, values)

    def test_worker_loss_is_representable_without_staying_running(self) -> None:
        """J: a lost worker has a state, so a record is not stuck by design."""
        self.assertIn(RunRecordStatus.INTERRUPTED, set(RunRecordStatus))
        self.assertNotIn(RunRecordStatus.INTERRUPTED, {RunRecordStatus.RUNNING})

    def test_the_status_enum_is_not_presented_as_final(self) -> None:
        """J: the provisional nature of O-7 is documented, not implied away."""
        doc = RunRecordStatus.__doc__ or ""
        self.assertIn("O-7", doc)
        self.assertIn("open", doc.lower())

    def test_terminal_detection_is_a_read_only_predicate(self) -> None:
        """J: the predicate encodes no transition rules and no resumption."""
        org = Organization.create(name="Org")
        project = Project.create(organization_id=org.id, name="P")
        queued = RunRecord.create(organization_id=org.id, project_id=project.id)
        self.assertFalse(queued.is_terminal)
        interrupted = dataclasses.replace(
            queued,
            status=RunRecordStatus.INTERRUPTED,
            core_run_id="core-1",
            started_at=utc_now(),
        )
        self.assertTrue(interrupted.is_terminal)


class UsageRecordIdentityTests(unittest.TestCase):
    """F, G: three distinct identities, and no money."""

    def _record(self, **overrides) -> UsageRecord:
        payload = {
            "organization_id": new_id(),
            "run_record_id": new_id(),
            "core_run_id": "core-run-1",
            "attempt_number": 0,
            "input_tokens": 10,
            "output_tokens": 4,
        }
        payload.update(overrides)
        return UsageRecord.create(**payload)

    def test_usage_record_carries_run_record_id_core_run_id_and_attempt_number(self) -> None:
        """F: all three identities are separate fields."""
        names = field_names(UsageRecord)
        for expected in ("run_record_id", "core_run_id", "attempt_number"):
            with self.subTest(field=expected):
                self.assertIn(expected, names)

    def test_usage_record_has_no_ambiguous_run_id_field(self) -> None:
        """F: a single ambiguous run_id must not exist."""
        self.assertNotIn("run_id", field_names(UsageRecord))

    def test_the_run_identities_may_differ(self) -> None:
        """F: the Platform reference and the Core correlation are independent."""
        record = self._record()
        self.assertNotEqual(record.run_record_id, record.core_run_id)

    def test_usage_record_has_no_financial_field(self) -> None:
        """G: usage is a measurement, never a financial record."""
        for name in field_names(UsageRecord):
            lowered = name.lower()
            for fragment in FINANCIAL_FRAGMENTS:
                with self.subTest(field=name, fragment=fragment):
                    self.assertNotIn(fragment, lowered)

    def test_financial_keywords_absent_from_every_declared_field(self) -> None:
        """G: the absence is structural — no recorded field carries money."""
        for class_name in ALL_DOMAIN_CLASSES:
            hits = fragment_hits(declared_field_names(class_name), FINANCIAL_FRAGMENTS)
            with self.subTest(record=class_name):
                self.assertEqual(hits, [], f"{class_name} has financial fields: {hits}")

    def test_uniqueness_key_is_expressible(self) -> None:
        """O-8: the model supports UNIQUE(run_record_id, attempt_number)."""
        run_record_id = new_id()
        first = self._record(run_record_id=run_record_id, attempt_number=0)
        second = self._record(run_record_id=run_record_id, attempt_number=1)
        self.assertEqual(
            (first.run_record_id, first.attempt_number),
            (run_record_id, 0),
        )
        self.assertNotEqual(
            (first.run_record_id, first.attempt_number),
            (second.run_record_id, second.attempt_number),
        )

    def test_non_finite_duration_is_refused(self) -> None:
        """A measurement must be a real measurement."""
        for bad in (float("nan"), float("inf"), float("-inf"), -0.1):
            with self.subTest(value=bad):
                with self.assertRaises(DomainContractError):
                    self._record(duration_seconds=bad)


class SecretSafetyTests(unittest.TestCase):
    """H: a credential reference is never disclosed, and no token is stored."""

    def test_provider_account_repr_does_not_disclose_the_reference(self) -> None:
        """H: repr must not echo the reference."""
        reference = "vault:tenants/acme/openai#primary"
        account = ProviderAccount.create(
            provider_name="openai", secret_ref=reference, organization_id=new_id()
        )
        rendered = repr(account)
        self.assertNotIn(reference, rendered)
        self.assertIn("<opaque>", rendered)

    def test_provider_account_repr_is_safe_at_every_construction_path(self) -> None:
        """H: the override covers the factory too."""
        account = ProviderAccount.create(provider_name="x", secret_ref="ref-sensitive")
        f = dataclasses.fields(ProviderAccount)
        secret_field = next(field for field in f if field.name == "secret_ref")
        self.assertFalse(secret_field.repr)

    def test_provider_account_stores_no_credential_material_field(self) -> None:
        """H: only an opaque reference exists; no key, token, or ciphertext."""
        names = field_names(ProviderAccount)
        for fragment in ("api_key", "token", "password", "ciphertext",
                         "private_key", "plaintext"):
            with self.subTest(fragment=fragment):
                self.assertFalse(
                    [n for n in names if fragment in n.lower()],
                    f"unexpected credential field matching {fragment!r}",
                )

    def test_api_key_stores_neither_token_nor_hash(self) -> None:
        """I: the safe representation's algorithm is still an open decision."""
        names = declared_field_names("APIKey")
        hits = fragment_hits(names, SECRET_FRAGMENTS)
        self.assertEqual(hits, [], f"APIKey has credential fields: {hits}")
        for name in names:
            with self.subTest(field=name):
                self.assertNotIn(name.lower(), FORBIDDEN_CREDENTIAL_FIELDS)

    def test_no_platform_record_carries_credential_material(self) -> None:
        """H, I: sweep every declared field, allowing only the opaque reference."""
        allowed = {"secret_ref"}
        for class_name in ALL_DOMAIN_CLASSES:
            for name in declared_field_names(class_name):
                lowered = name.lower()
                for fragment in SECRET_FRAGMENTS:
                    if fragment in lowered and name not in allowed:
                        self.fail(
                            f"{class_name}.{name} looks like credential material"
                        )

    def test_run_record_and_usage_record_have_no_secret_field(self) -> None:
        """H: no secret reaches the lifecycle or the measurement record.

        Token counters are measurements, so the check is against fields that would
        store a credential, not against the substring "token".
        """
        for class_name in ("RunRecord", "UsageRecord"):
            names = declared_field_names(class_name)
            hits = fragment_hits(names, SECRET_FRAGMENTS)
            with self.subTest(record=class_name):
                self.assertEqual(hits, [], f"{class_name} has credential fields: {hits}")
            for name in names:
                with self.subTest(record=class_name, field=name):
                    self.assertNotIn(name.lower(), FORBIDDEN_CREDENTIAL_FIELDS)

    def test_token_counters_are_measurements_not_credentials(self) -> None:
        """H: the usage counters are physical measurements and stay allowed."""
        names = declared_field_names("UsageRecord")
        for counter in ("input_tokens", "output_tokens", "cached_tokens"):
            with self.subTest(field=counter):
                self.assertIn(counter, names)


class APIKeyAuthorityTests(unittest.TestCase):
    """I and R-5: no token format is chosen, and revocation is explicit."""

    def setUp(self) -> None:
        self.org = Organization.create(name="Org")

    def _key(self, **overrides) -> APIKey:
        payload = {
            "organization_id": self.org.id,
            "created_by_user_id": new_id(),
            "name": "ci",
        }
        payload.update(overrides)
        return APIKey.create(**payload)

    def test_the_contract_requires_no_token_format(self) -> None:
        """I: a key record can be built with no prefix, token, or hash."""
        key = self._key()
        self.assertEqual(key.key_prefix, "")
        self.assertTrue(key.is_usable)

    def test_no_concrete_format_is_required_by_the_contract(self) -> None:
        """I: O-1 stays open, so no scheme is baked into the contract.

        The claim is about the record, not about prose: every credential-shaped
        field is optional or absent, so a key can be represented without any
        token scheme being chosen.
        """
        names = declared_field_names("APIKey")
        self.assertNotIn("token", " ".join(names).lower())
        self.assertNotIn("hash", " ".join(names).lower())
        # Only identification metadata exists, and it is optional.
        prefix_field = next(
            field for field in dataclasses.fields(APIKey) if field.name == "key_prefix"
        )
        self.assertIsNot(prefix_field.default, dataclasses.MISSING)
        self.assertEqual(prefix_field.default, "")

    def test_revocation_is_explicit_and_consistent(self) -> None:
        """R-5: revocation must be recorded, not implied."""
        key = self._key()
        revoked = dataclasses.replace(
            key, status=APIKeyStatus.REVOKED, revoked_at=utc_now()
        )
        self.assertFalse(revoked.is_usable)
        with self.assertRaises(DomainContractError):
            dataclasses.replace(key, status=APIKeyStatus.REVOKED)
        with self.assertRaises(DomainContractError):
            dataclasses.replace(key, revoked_at=utc_now())

    def test_the_creator_is_recorded_so_either_authority_model_is_supported(self) -> None:
        """R-5: neither model is chosen, but both remain implementable."""
        key = self._key()
        self.assertTrue(key.created_by_user_id)
        self.assertIn("created_by_user_id", field_names(APIKey))

    def test_scopes_are_data_not_authority(self) -> None:
        """A scope list does not grant anything by itself."""
        key = self._key(scopes=("runs:write",))
        self.assertEqual(key.scopes, ("runs:write",))
        self.assertNotIn("authority", " ".join(field_names(APIKey)))


class MembershipAuthorityTests(unittest.TestCase):
    """Membership is the only path to tenant authority, and it can be withdrawn."""

    def test_revoked_or_inactive_membership_is_not_active_authorization(self) -> None:
        membership = Membership.create(
            organization_id=new_id(),
            user_id=new_id(),
            role=MembershipRole.OWNER,
        )
        self.assertTrue(membership.is_active)
        for status in (MembershipStatus.INACTIVE, MembershipStatus.REVOKED):
            with self.subTest(status=status):
                self.assertFalse(dataclasses.replace(membership, status=status).is_active)

    def test_role_is_not_a_permission_object(self) -> None:
        """A role names standing, not a path, command, tool, or permission."""
        for member in MembershipRole:
            with self.subTest(role=member):
                self.assertNotIn("/", member.value)

    def test_membership_requires_user_and_organization(self) -> None:
        with self.assertRaises(DomainContractError):
            Membership.create(
                organization_id=new_id(), user_id="", role=MembershipRole.MEMBER
            )
        with self.assertRaises(DomainContractError):
            Membership.create(
                organization_id="", user_id=new_id(), role=MembershipRole.MEMBER
            )


class IdentityIsNotAuthorityTests(unittest.TestCase):
    """Section 11: identifiers are references, never proof of authority."""

    def test_no_record_exposes_an_authority_or_permission_field(self) -> None:
        for cls in (User, Organization, Membership, Project, ProviderAccount,
                    APIKey, RunRecord, UsageRecord):
            for name in field_names(cls):
                lowered = name.lower()
                for fragment in ("authority", "permission", "authorized",
                                 "allowed", "capability", "scope_authority"):
                    with self.subTest(record=cls.__name__, field=name):
                        self.assertNotIn(fragment, lowered)

    def test_organization_holds_no_money_or_execution_field(self) -> None:
        """The tenant boundary is not a wallet and not an authority."""
        for name in field_names(Organization):
            lowered = name.lower()
            for fragment in FINANCIAL_FRAGMENTS + ("authority", "execute", "command"):
                with self.subTest(field=name, fragment=fragment):
                    self.assertNotIn(fragment, lowered)

    def test_project_workspace_reference_is_opaque_not_a_path_authority(self) -> None:
        """Section 6: the workspace is server-derived, not a client path."""
        project = Project.create(
            organization_id=new_id(), name="P", workspace_ref="ws-ref-1"
        )
        self.assertEqual(project.workspace_ref, "ws-ref-1")
        # It is an opaque reference, not a resolved path: the domain layer
        # contains no path handling at all.
        imports = set()
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module)
        self.assertNotIn("pathlib", imports)
        self.assertNotIn("os", imports)


class DatetimeAndIdentifierTests(unittest.TestCase):
    """Section 14: one timestamp rule, and no bespoke identifier type."""

    def test_timestamps_must_be_timezone_aware(self) -> None:
        naive = datetime(2026, 10, 8, 12, 0, 0)
        with self.assertRaises(DomainContractError):
            User(id=new_id(), email="a@b.co", created_at=naive)
        aware = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(
            User(id=new_id(), email="a@b.co", created_at=aware).created_at, aware
        )

    def test_timestamps_are_never_floats(self) -> None:
        with self.assertRaises(DomainContractError):
            Organization(id=new_id(), name="n", created_at=1700000000.0)  # type: ignore[arg-type]

    def test_utc_now_is_aware_and_utc(self) -> None:
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.utcoffset(), timedelta(0))

    def test_identifiers_are_opaque_strings_from_the_standard_library(self) -> None:
        identifier = new_id()
        self.assertIsInstance(identifier, str)
        # A standard-library UUID string, not a bespoke identifier type.
        self.assertRegex(
            identifier,
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )
        from uuid import UUID

        self.assertEqual(str(UUID(identifier)), identifier)

    def test_a_malformed_identifier_is_refused(self) -> None:
        for bad in ("has space", "semi;colon", "x" * 200):
            with self.subTest(value=bad):
                with self.assertRaises(DomainContractError):
                    Project.create(organization_id=bad, name="p")


class ArchitectureBoundaryTests(unittest.TestCase):
    """L: Core does not import the Platform domain, and the domain stays pure."""

    FORBIDDEN_DOMAIN_IMPORTS = frozenset(
        {"sqlalchemy", "psycopg", "psycopg2", "sqlite3", "alembic", "stripe",
         "asyncpg", "pymongo", "redis"}
    )

    def _imports(self, path: pathlib.Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_domain_layer_imports_only_the_standard_library(self) -> None:
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            imports = self._imports(path)
            for module in imports:
                root = module.split(".")[0]
                with self.subTest(file=path.name, module=module):
                    self.assertNotIn(root, self.FORBIDDEN_DOMAIN_IMPORTS)
                    self.assertIn(
                        root,
                        {"app", "__future__", "dataclasses", "datetime", "enum",
                         "math", "re", "typing", "uuid"},
                        f"unexpected import in the domain layer: {module}",
                    )

    def test_no_core_module_imports_the_platform_domain(self) -> None:
        """L: the dependency is one-way; Core stays autonomous."""
        offenders: list[str] = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if PLATFORM_DIR in path.parents:
                continue
            for module in self._imports(path):
                if module == "app.platform" or module.startswith("app.platform."):
                    offenders.append(f"{path.relative_to(REPO)} -> {module}")
        self.assertEqual(offenders, [], f"Core imports the Platform domain: {offenders}")

    # One name is deliberately excluded from the check below. Core already had a
    # provider-credential reference model before the Platform domain existed
    # (app/agents/providers/models.py::ProviderAccount), and Step 1 does not
    # rewrite Core. The two types have the same name and different roles: Core's
    # is a provider-side credential pointer used by the provider layer, while the
    # Platform record is a tenant-owned credential reference for the control
    # plane. They must never be imported into each other's layer, which the import
    # checks above enforce.
    PRE_EXISTING_CORE_TYPE = "ProviderAccount"

    def test_no_core_module_outside_the_domain_defines_a_platform_record(self) -> None:
        """L: no Core module introduces a second copy of a Platform record."""
        entity_names = {"User", "Organization", "Membership", "Project",
                        "ProviderAccount", "APIKey", "RunRecord", "UsageRecord"}
        entity_names.discard(self.PRE_EXISTING_CORE_TYPE)
        offenders: list[str] = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if PLATFORM_DIR in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in entity_names:
                    offenders.append(f"{path.relative_to(REPO)} defines {node.name}")
        self.assertEqual(offenders, [], f"Core defines a Platform record: {offenders}")

    def test_the_pre_existing_core_provider_account_is_a_different_type(self) -> None:
        """The one shared name is a different type in a different layer."""
        from app.agents.providers.models import ProviderAccount as CoreProviderAccount
        from app.platform.models import ProviderAccount as PlatformProviderAccount

        self.assertIsNot(CoreProviderAccount, PlatformProviderAccount)
        core_fields = {f.name for f in dataclasses.fields(CoreProviderAccount)}
        platform_fields = set(declared_field_names("ProviderAccount"))
        # Core's is identified by an account id and a provider id; the Platform
        # record is identified by tenant ownership.
        self.assertIn("account_id", core_fields)
        self.assertIn("organization_id", platform_fields)
        self.assertNotIn("organization_id", core_fields)

    def test_the_domain_layer_creates_no_billing_entity(self) -> None:
        """Billing is a later stage and must not appear here."""
        defined = set()
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    defined.add(node.name)
        for forbidden in ("Wallet", "CreditTransaction", "CostRecord", "PricingPlan",
                          "PriceRule", "Invoice", "Payment", "Commission",
                          "ResellerAccount", "Partner"):
            with self.subTest(entity=forbidden):
                self.assertNotIn(forbidden, defined)

    def test_the_domain_layer_implements_no_persistence_or_transport(self) -> None:
        """Section 17: repositories, resolvers, and transports are later steps."""
        defined = set()
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    defined.add(node.name)
        for forbidden in ("Repository", "Session", "Engine", "Migration",
                          "SecretResolver", "EphemeralSecretResolver",
                          "TrustedExecutionRequest", "Transport"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, defined)


class OpenDecisionPreservationTests(unittest.TestCase):
    """Section 18: the domain layer takes no decision the contract leaves open."""

    def test_no_auth_or_session_mechanism_is_chosen(self) -> None:
        """O-2 stays open."""
        for name in field_names(User):
            with self.subTest(field=name):
                self.assertNotIn(name.lower(), {"password_hash", "session_id",
                                                "access_token", "refresh_token"})

    def test_the_secret_reference_is_opaque_and_unparsed(self) -> None:
        """O-4 stays open: any non-empty reference is accepted unexamined."""
        for reference in ("env:OPENAI_API_KEY", "vault:abc/def#v2", "kms:v1:Zm9v",
                          "opaque-reference", "42"):
            with self.subTest(reference=reference):
                account = ProviderAccount.create(
                    provider_name="openai", secret_ref=reference
                )
                self.assertEqual(account.secret_ref, reference)
        # Nothing parses a scheme out of the reference, so no grammar is fixed.
        names = declared_field_names("ProviderAccount")
        self.assertNotIn("scheme", " ".join(names).lower())
        self.assertNotIn("backend", " ".join(names).lower())

    def test_no_workspace_topology_is_embedded(self) -> None:
        """O-5 stays open and Core stays cross-platform."""
        imported: set[str] = set()
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
        for module in ("pathlib", "os", "posixpath", "ntpath", "shutil", "tempfile"):
            with self.subTest(module=module):
                self.assertNotIn(module, imported)
        # The only workspace field is a single opaque reference.
        self.assertEqual(
            [n for n in declared_field_names("Project") if "workspace" in n],
            ["workspace_ref"],
        )

    def test_no_transport_is_embedded(self) -> None:
        """O-6 stays open: no transport is chosen by the domain layer."""
        imported: set[str] = set()
        for path in sorted(PLATFORM_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
        for transport in ("grpc", "requests", "httpx", "socket", "subprocess",
                          "fastapi", "flask", "http", "urllib"):
            with self.subTest(transport=transport):
                self.assertNotIn(transport, imported)

    def test_no_billing_concept_is_embedded(self) -> None:
        """O-10..O-13 stay deferred: no billing field and no billing class."""
        billing_fields = ("wallet", "balance", "credit", "invoice", "commission",
                          "reseller", "margin", "revenue", "subscription",
                          "price", "charge", "payment", "cost")
        for class_name in ALL_DOMAIN_CLASSES:
            hits = fragment_hits(declared_field_names(class_name), billing_fields)
            with self.subTest(record=class_name):
                self.assertEqual(hits, [], f"{class_name} has billing fields: {hits}")
        # And no billing vocabulary exists in the status enums either.
        from app.platform import enums as platform_enums

        enum_members: list[str] = []
        for name in dir(platform_enums):
            value = getattr(platform_enums, name)
            if isinstance(value, type) and issubclass(value, platform_enums._DomainEnum):
                enum_members.extend(member.value for member in value)
        joined = " ".join(enum_members).lower()
        for concept in ("wallet", "invoice", "commission", "reseller", "margin",
                        "revenue", "subscription", "charge", "price"):
            with self.subTest(concept=concept):
                self.assertNotIn(concept, joined)

    def test_records_are_immutable_values(self) -> None:
        """A domain contract is a value; a mutable record invites silent drift."""
        for cls in (User, Organization, Membership, Project, ProviderAccount,
                    APIKey, RunRecord, UsageRecord):
            with self.subTest(record=cls.__name__):
                self.assertTrue(cls.__dataclass_params__.frozen)

    def test_status_vocabularies_are_closed_enums(self) -> None:
        """Statuses are vocabularies, not free-text fields."""
        for member in (UserStatus, OrganizationStatus, MembershipRole,
                       MembershipStatus, ProjectStatus, ProviderAccountStatus,
                       APIKeyStatus, RunRecordStatus):
            with self.subTest(enum=member.__name__):
                self.assertTrue(issubclass(member, str))


if __name__ == "__main__":
    unittest.main()
