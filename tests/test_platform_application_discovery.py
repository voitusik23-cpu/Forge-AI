"""AS-1 and AS-2 contract tests against live PostgreSQL (Stage 1 / Step 4).

The first Application Services tests. They run against **real** PostgreSQL 17, because
what they assert is exactly the behaviour a fake database cannot have:

* row-level security refusing a read the application layer did not authorize;
* subject-bound discovery: a session may address only the subject it declared;
* a suspended account and an inactive membership actually being invisible to the
  discovery policy, so the refusal is the database's answer and not a Python ``if``;
* the non-disclosure rule, measured as "a cross-tenant identifier and a non-existent one
  produce the same code **and** the same message".

The **authentication** adapter is a test double, deliberately: credential verification is
open decision O-2 and no mechanism ships. The double lives in ``tests/``, is never
imported from ``app/``, and maps a fixed token to a fixed subject
(design sections 3.4 and 11.1).

The throwaway database, its migrations, and the two non-privileged logins come from the
persistence fixture, so this suite provisions nothing of its own and either suite can run
alone.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import pathlib
import sys
import unittest
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.platform.application import (  # noqa: E402
    AmbiguousOrganization,
    ApplicationError,
    AuthorizedContext,
    DiscoveryService,
    ErrorCode,
    NotPermitted,
    NotFound,
    SubjectNotAdmissible,
    Unauthenticated,
)
from app.platform.application.organizations import (  # noqa: E402
    OrganizationSummary,
    ProjectSummary,
)
from app.platform.enums import (  # noqa: E402
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
    ProjectStatus,
    UserStatus,
)
from app.platform.models import (  # noqa: E402
    Membership,
    Organization,
    Project,
    User,
    utc_now,
)
from app.platform.persistence import PlatformDatabase  # noqa: E402
from app.platform.persistence.errors import PermissionDeniedError  # noqa: E402
from app.platform.principal import (  # noqa: E402
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthenticationMethod,
    Credential,
    SubjectKind,
)
import app.platform.principal as principal_module  # noqa: E402


#: The platform role constants, imported rather than spelled out, so a rename in the
#: persistence layer cannot silently leave a fixture granting the wrong role.
SYSTEM_ROLE = "forge_platform_system"


#: One event loop for the whole module. `asyncio.run` closes its loop on return, while
#: an asyncpg pool stays bound to the loop it was created on, so a pool opened in
#: ``setUp`` and used by later calls would fail with "Event loop is closed".
_LOOP = asyncio.new_event_loop()


def run_async(coro):
    """Run a coroutine on the module loop, without closing it."""

    asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coro)


def _close_module_loop() -> None:
    if not _LOOP.is_closed():
        _LOOP.run_until_complete(_LOOP.shutdown_asyncgens())
        _LOOP.close()


import atexit  # noqa: E402

atexit.register(_close_module_loop)


# --------------------------------------------------------------------------- #
# test-only authentication adapter
# --------------------------------------------------------------------------- #
class TestAuthenticationAdapter:
    """A port implementation **for tests**, not a mechanism.

    It maps a fixed token to a fixed subject and refuses everything else, fail-closed on
    every path the design requires (section 3.5). It is deliberately trivial: the point
    of this slice is the boundary *after* authentication, and inventing a credential
    check here would both fake O-2 and prove nothing.
    """

    def __init__(self, tokens: dict[str, str] | None = None) -> None:
        self._tokens = dict(tokens or {})

    async def verify(self, credential: Credential) -> AuthenticatedPrincipal:
        if not isinstance(credential, Credential):
            raise AuthenticationError("missing credential")
        subject = self._tokens.get(credential.material)
        if subject is None:
            # Unknown and malformed are the same answer on purpose.
            raise AuthenticationError("credential could not be resolved")
        return principal_for(subject)


def principal_for(subject_id: str) -> AuthenticatedPrincipal:
    """A principal for a known subject, produced the only legitimate way."""

    return AuthenticatedPrincipal.create(
        subject_id,
        subject_kind=SubjectKind.USER,
        method=AuthenticationMethod.SESSION,
        principal_token=principal_module._PRINCIPAL_SENTINEL,
    )


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
class RecordingDatabase:
    """A real ``PlatformDatabase`` that counts scope opens.

    An M2-8 assertion is a statement about **execution order**, which cannot be checked
    by inspecting a result. This wrapper counts tenant-scope opens (and pre-tenant opens
    separately, so a test can show that authentication is the *first* thing that runs)
    while delegating every call to the real object.
    """

    def __init__(self, dsn: str, application_role: str = "forge_platform_app") -> None:
        self._inner = PlatformDatabase(
            dsn, application_role=application_role, min_size=1, max_size=4
        )
        self.tenant_opens = 0
        self.pre_tenant_opens = 0
        self.order: list[str] = []

    async def connect(self) -> "RecordingDatabase":
        await self._inner.connect()
        return self

    async def close(self) -> None:
        await self._inner.close()

    def pre_tenant(self, **kwargs):
        self.pre_tenant_opens += 1
        self.order.append("pre_tenant")
        return self._inner.pre_tenant(**kwargs)

    def server(self, **kwargs):
        return self._inner.server(**kwargs)

    def tenant(self, organization_id, **kwargs):
        self.tenant_opens += 1
        self.order.append(f"tenant:{organization_id}")
        return self._inner.tenant(organization_id, **kwargs)


class Seed:
    """One subject with a deliberate spread of tenancy states."""

    def __init__(self) -> None:
        self.primary_org = str(uuid.uuid4())
        self.secondary_org = str(uuid.uuid4())
        self.suspended_org = str(uuid.uuid4())
        self.inactive_org = str(uuid.uuid4())
        self.foreign_org = str(uuid.uuid4())
        self.primary_project = str(uuid.uuid4())
        self.archived_project = str(uuid.uuid4())
        self.foreign_project = str(uuid.uuid4())
        self.owner = str(uuid.uuid4())
        self.inactive_member = str(uuid.uuid4())
        self.suspended_user = str(uuid.uuid4())
        self.unaffiliated_user = str(uuid.uuid4())
        self.foreign_owner = str(uuid.uuid4())


async def seed_subject(system_database, tenant_database) -> Seed:
    """Seed the spread through the repositories, one handle per scope.

    Two handles rather than one, because each Platform login is a member of **exactly
    one** role -- that separation is a security invariant, so a fixture that used one
    login for both scopes would be weakening the boundary the tests are about.

    Seeding through the repositories rather than raw SQL means the policies a request
    would meet are the ones that let the fixture in: a mistake in the fixture cannot
    quietly create state a request could never reach.
    """

    seed = Seed()

    async def users(unit):
        for user_id, status in (
            (seed.owner, UserStatus.ACTIVE),
            (seed.inactive_member, UserStatus.ACTIVE),
            (seed.suspended_user, UserStatus.SUSPENDED),
            (seed.unaffiliated_user, UserStatus.ACTIVE),
            (seed.foreign_owner, UserStatus.ACTIVE),
        ):
            await unit.users.create(
                User(
                    id=user_id,
                    email=f"{user_id[:8]}-{uuid.uuid4().hex[:6]}@example.test",
                    created_at=utc_now(),
                    status=status,
                )
            )

    async def organizations(unit):
        for org_id, name, status in (
            (seed.primary_org, "Primary", OrganizationStatus.ACTIVE),
            (seed.secondary_org, "Secondary", OrganizationStatus.ACTIVE),
            (seed.suspended_org, "Suspended", OrganizationStatus.SUSPENDED),
            (seed.inactive_org, "Inactive membership", OrganizationStatus.ACTIVE),
            (seed.foreign_org, "Foreign", OrganizationStatus.ACTIVE),
        ):
            await unit.organizations.create(
                Organization(
                    id=org_id,
                    name=name,
                    created_at=utc_now(),
                    status=status,
                    slug=f"org-{org_id[:8]}",
                )
            )

    async def memberships(unit):
        for org_id, user_id, status, role in (
            (seed.primary_org, seed.owner, MembershipStatus.ACTIVE,
             MembershipRole.OWNER),
            (seed.secondary_org, seed.owner, MembershipStatus.ACTIVE,
             MembershipRole.MEMBER),
            (seed.suspended_org, seed.owner, MembershipStatus.ACTIVE,
             MembershipRole.OWNER),
            (seed.inactive_org, seed.owner, MembershipStatus.INACTIVE,
             MembershipRole.ADMIN),
            (seed.primary_org, seed.inactive_member, MembershipStatus.INACTIVE,
             MembershipRole.MEMBER),
            (seed.foreign_org, seed.foreign_owner, MembershipStatus.ACTIVE,
             MembershipRole.OWNER),
        ):
            await unit.system_memberships.create(
                org_id,
                Membership(
                    id=str(uuid.uuid4()),
                    organization_id=org_id,
                    user_id=user_id,
                    role=role,
                    status=status,
                    created_at=utc_now(),
                ),
            )

    async with system_database.server(role=SYSTEM_ROLE, system_scope=True) as unit:
        await users(unit)
        await organizations(unit)
        await memberships(unit)

    # Projects are tenant-owned, so they go through a tenant scope. The seed re-decides
    # nothing: it writes and reads in the same transaction, which is what the services
    # are required to do.
    async with tenant_database.tenant(
        seed.primary_org, user_id=seed.owner
    ) as unit:
        await unit.projects.create(
            seed.primary_project, "Primary project", slug="primary-project"
        )
        archived = await unit.projects.create(
            seed.archived_project, "Archived project", slug="archived-project"
        )
        await unit.projects.set_status(archived.id, ProjectStatus.ARCHIVED)

    async with tenant_database.tenant(
        seed.foreign_org, user_id=seed.foreign_owner
    ) as unit:
        await unit.projects.create(
            seed.foreign_project, "Foreign project", slug="foreign-project"
        )

    return seed


class ApplicationDiscoveryTestCase(unittest.TestCase):
    """One migrated database per class, one seeded spread per test.

    The database, its migrations and the two non-privileged logins come from
    ``persistence_fixture``. The fixture is entered once per class as a plain object
    (``__aenter__``/``__aexit__`` are driven explicitly and **awaited**), which keeps the
    provisioning in one place and lets either suite run alone.
    """

    _handle = None
    _seed_template = None

    @classmethod
    def setUpClass(cls):
        from persistence_fixture import (
            SKIP_REASON_NO_DRIVER as P_SKIP_DRIVER,
            SKIP_REASON_NO_SERVER as P_SKIP_SERVER,
            PersistenceDatabase,
            asyncpg_module,
            live_postgres,
        )

        cls._PersistenceDatabase = PersistenceDatabase

        if asyncpg_module() is None:
            raise unittest.SkipTest(P_SKIP_DRIVER)

        async def probe():
            connection = await live_postgres()
            if connection is None:
                return False
            await connection.close()
            return True

        if not run_async(probe()):
            raise unittest.SkipTest(P_SKIP_SERVER)

        cls._handle = PersistenceDatabase()
        run_async(cls._handle.__aenter__())

    @classmethod
    def tearDownClass(cls):
        if cls._handle is not None:
            run_async(cls._handle.__aexit__(None, None, None))
            cls._handle = None

    def setUp(self):
        self._database = None
        self._tenant_database = None
        self._seed = None

        async def setup():
            # One handle per scope: the bootstrap rows are server-only, and the two
            # logins are each a member of exactly one platform role.
            seeder = PlatformDatabase(
                self._handle.system_dsn,
                application_role=SYSTEM_ROLE,
                min_size=1,
                max_size=4,
            )
            tenant_writer = PlatformDatabase(
                self._handle.dsn, min_size=1, max_size=4
            )
            await seeder.connect()
            await tenant_writer.connect()
            try:
                self._seed = await seed_subject(seeder, tenant_writer)
            finally:
                await seeder.close()
                await tenant_writer.close()
            # Discovery runs on the system login (the pre-tenant scope); the
            # authorized tenant read runs on the application login. One handle each,
            # because each login holds exactly one platform role.
            self._database = await RecordingDatabase(
                self._handle.system_dsn, application_role=SYSTEM_ROLE
            ).connect()
            self._tenant_database = await RecordingDatabase(
                self._handle.dsn
            ).connect()

        run_async(setup())

    def tearDown(self):
        async def teardown():
            if self._tenant_database is not None:
                await self._tenant_database.close()
            if self._database is not None:
                await self._database.close()

        run_async(teardown())

    def service(self) -> DiscoveryService:
        return DiscoveryService(self._database, self._tenant_database)

    def owner(self) -> AuthenticatedPrincipal:
        return principal_for(self._seed.owner)


# --------------------------------------------------------------------------- #
# AS-1 — list the caller's organizations
# --------------------------------------------------------------------------- #
class AS1ListOrganizationsTests(ApplicationDiscoveryTestCase):
    def test_001_returns_the_callers_active_organizations(self):
        try:
            organizations = run_async(
                self.service().list_organizations(self.owner())
            )
            ids = {o.organization_id for o in organizations}
            self.assertEqual(
                ids, {self._seed.primary_org, self._seed.secondary_org},
                "AS-1 must return the organizations the subject actually belongs to",
            )
        finally:
            pass

    def test_002_the_suspended_organization_is_excluded(self):
        """A tenant-owned read is refused for a suspended tenant.

        The subject holds an ACTIVE membership there, so this is not a membership
        decision: the tenant itself is not selectable.
        """

        try:
            ids = {
                o.organization_id
                for o in run_async(self.service().list_organizations(self.owner()))
            }
            self.assertNotIn(self._seed.suspended_org, ids)
        finally:
            pass

    def test_003_the_inactive_membership_is_excluded(self):
        try:
            ids = {
                o.organization_id
                for o in run_async(self.service().list_organizations(self.owner()))
            }
            self.assertNotIn(self._seed.inactive_org, ids)
        finally:
            pass

    def test_004_another_tenants_organization_is_never_returned(self):
        try:
            ids = {
                o.organization_id
                for o in run_async(self.service().list_organizations(self.owner()))
            }
            self.assertNotIn(self._seed.foreign_org, ids)
            # And the foreign owner sees only their own.
            foreign_ids = {
                o.organization_id
                for o in run_async(
                    self.service().list_organizations(principal_for(self._seed.foreign_owner))
                )
            }
            self.assertEqual(foreign_ids, {self._seed.foreign_org})
        finally:
            pass

    def test_005_role_is_the_callers_own(self):
        try:
            roles = {
                o.organization_id: o.role
                for o in run_async(self.service().list_organizations(self.owner()))
            }
            self.assertEqual(roles[self._seed.primary_org], MembershipRole.OWNER.value)
            self.assertEqual(roles[self._seed.secondary_org], MembershipRole.MEMBER.value)
        finally:
            pass

    def test_006_a_subject_with_no_membership_gets_an_empty_list_not_an_error(self):
        try:
            organizations = run_async(
                self.service().list_organizations(
                    principal_for(self._seed.unaffiliated_user)
                )
            )
            self.assertEqual(tuple(organizations), ())
        finally:
            pass

    def test_007_a_suspended_subject_is_refused(self):
        try:
            with self.assertRaises(SubjectNotAdmissible) as caught:
                run_async(
                    self.service().list_organizations(
                        principal_for(self._seed.suspended_user)
                    )
                )
            self.assertEqual(caught.exception.code, ErrorCode.SUBJECT_NOT_ADMISSIBLE)
        finally:
            pass

    def test_008_an_inactive_membership_does_not_admit_its_organization(self):
        """The same subject IS refused when only an inactive membership exists.

        The owner has an INACTIVE membership in ``inactive_org``; naming that
        organization must fail, and the list must not contain it.
        """

        try:
            with self.assertRaises(NotFound) as caught:
                run_async(
                    self.service().list_projects(
                        self.owner(), self._seed.inactive_org
                    )
                )
            self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)
        finally:
            pass


# --------------------------------------------------------------------------- #
# AS-1 — identity refusals
# --------------------------------------------------------------------------- #
class AS1IdentityTests(ApplicationDiscoveryTestCase):
    def test_010_no_principal_is_refused(self):
        try:
            with self.assertRaises(Unauthenticated) as caught:
                run_async(self.service().list_organizations(None))
            self.assertEqual(caught.exception.code, ErrorCode.UNAUTHENTICATED)
        finally:
            pass

    def test_011_a_client_supplied_identifier_is_not_a_principal(self):
        """The value a client would send is refused as an identity.

        A raw string is the shape a hostile caller would try, and the boundary must
        refuse it rather than coercing it into a subject.
        """

        try:
            for bad in (self._seed.owner, "", 7, object()):
                with self.assertRaises(Unauthenticated, msg=repr(bad)):
                    run_async(self.service().list_organizations(bad))
        finally:
            pass

    def test_012_the_authentication_adapter_refuses_an_unknown_token(self):
        try:
            adapter = TestAuthenticationAdapter({"good-token": self._seed.owner})
            principal = run_async(adapter.verify(Credential("good-token")))
            self.assertEqual(principal.subject_id, self._seed.owner)
            for bad in ("", "wrong", "admin", self._seed.owner):
                with self.assertRaises(AuthenticationError, msg=repr(bad)):
                    run_async(adapter.verify(Credential(bad)))
        finally:
            pass


# --------------------------------------------------------------------------- #
# AS-2 — projects in the selected organization
# --------------------------------------------------------------------------- #
class AS2ListProjectsTests(ApplicationDiscoveryTestCase):
    def test_020_lists_the_projects_of_the_selected_organization(self):
        try:
            projects = run_async(
                self.service().list_projects(self.owner(), self._seed.primary_org)
            )
            self.assertEqual(
                {p.project_id for p in projects},
                {self._seed.primary_project, self._seed.archived_project},
            )
        finally:
            pass

    def test_021_an_organization_outside_the_candidate_set_is_not_found(self):
        """A cross-tenant identifier is ``not_found``, never ``not_permitted``.

        Design section 6.3: answering ``not_permitted`` would confirm that the
        identifier exists and allow a caller to enumerate other tenants.
        """

        try:
            with self.assertRaises(NotFound) as caught:
                run_async(
                    self.service().list_projects(self.owner(), self._seed.foreign_org)
                )
            self.assertEqual(caught.exception.code, ErrorCode.NOT_FOUND)
            self.assertNotIsInstance(caught.exception, NotPermitted)
        finally:
            pass

    def test_022_a_non_existent_identifier_is_indistinguishable_from_a_foreign_one(self):
        """The non-disclosure rule, measured rather than asserted in prose."""

        try:
            with self.assertRaises(NotFound) as foreign:
                run_async(
                    self.service().list_projects(self.owner(), self._seed.foreign_org)
                )
            with self.assertRaises(NotFound) as absent:
                run_async(
                    self.service().list_projects(self.owner(), str(uuid.uuid4()))
                )
            self.assertEqual(foreign.exception.code, absent.exception.code)
            self.assertEqual(str(foreign.exception), str(absent.exception))
            self.assertNotIn(self._seed.foreign_org, str(foreign.exception))
        finally:
            pass

    def test_023_a_suspended_organization_is_not_usable(self):
        try:
            with self.assertRaises(NotFound):
                run_async(
                    self.service().list_projects(self.owner(), self._seed.suspended_org)
                )
        finally:
            pass

    def test_024_the_server_never_substitutes_the_right_organization(self):
        """A wrong claim is refused, never silently corrected.

        Design section 4.2: substituting teaches the client that a wrong identifier
        works, and hides the defect from an operator.
        """

        try:
            with self.assertRaises(NotFound):
                run_async(
                    self.service().list_projects(self.owner(), self._seed.foreign_org)
                )
            # The correct organization still works, so the refusal was about the claim.
            projects = run_async(
                self.service().list_projects(self.owner(), self._seed.primary_org)
            )
            self.assertTrue(projects)
        finally:
            pass

    def test_025_an_ambiguous_selection_is_refused_when_no_organization_is_named(self):
        try:
            with self.assertRaises(AmbiguousOrganization) as caught:
                run_async(self.service().list_projects(self.owner(), None))
            self.assertEqual(caught.exception.code, ErrorCode.AMBIGUOUS_ORGANIZATION)
        finally:
            pass

    def test_026_an_archived_project_is_still_listed(self):
        """Listing is not filtering: the lifecycle state is reported, not hidden.

        A caller that asks what is in its own organization gets the answer, including
        the archived project, with ``status`` saying so.
        """

        try:
            projects = run_async(
                self.service().list_projects(self.owner(), self._seed.primary_org)
            )
            by_id = {p.project_id: p for p in projects}
            self.assertEqual(
                by_id[self._seed.archived_project].status, ProjectStatus.ARCHIVED.value
            )
            self.assertEqual(
                by_id[self._seed.primary_project].status, ProjectStatus.ACTIVE.value
            )
        finally:
            pass

    def test_027_another_tenants_projects_are_never_returned(self):
        try:
            projects = run_async(
                self.service().list_projects(self.owner(), self._seed.primary_org)
            )
            self.assertNotIn(
                self._seed.foreign_project, {p.project_id for p in projects}
            )
        finally:
            pass

    def test_028_a_suspended_subject_is_refused_before_the_tenant_read(self):
        try:
            with self.assertRaises(SubjectNotAdmissible):
                run_async(
                    self.service().list_projects(
                        principal_for(self._seed.suspended_user),
                        self._seed.primary_org,
                    )
                )
        finally:
            pass


# --------------------------------------------------------------------------- #
# M2-2 — no DTO accepts a subject
# --------------------------------------------------------------------------- #
class NoSubjectParameterTests(unittest.TestCase):
    """M2-2, checked structurally over the whole package.

    A ``user_id`` input field anywhere in the application layer would be a way for a
    client to name a subject, which is exactly what subject-bound discovery forbids.
    """

    def test_030_no_input_dto_has_a_subject_field(self):
        import app.platform.application as package

        module_names = [
            "app.platform.application.organizations",
            "app.platform.application.authorization",
            "app.platform.application.context",
        ]
        forbidden = {"user_id", "subject_id", "principal_id", "created_by_user_id"}
        offenders: list[tuple[str, str]] = []
        for name in module_names:
            module = __import__(name, fromlist=["_"])
            for attribute in vars(module).values():
                if dataclasses.is_dataclass(attribute) and isinstance(attribute, type):
                    if attribute.__module__ != name:
                        continue
                    for field in dataclasses.fields(attribute):
                        if field.name in forbidden:
                            offenders.append((name, field.name))
        self.assertEqual(offenders, [], "an input DTO must not accept a subject")

    def test_031_the_use_case_signatures_take_a_principal_not_an_identifier(self):
        """The second half of M2-2: no method accepts an identifier where a verified
        principal belongs, and the first parameter is always the principal."""

        for method_name in ("list_organizations", "list_projects", "authorized_context"):
            signature = inspect.signature(getattr(DiscoveryService, method_name))
            parameters = list(signature.parameters)
            self.assertEqual(
                parameters[:2], ["self", "principal"],
                f"{method_name} must take the principal first",
            )
            for name, parameter in signature.parameters.items():
                if name in ("self", "principal", "organization_id"):
                    continue
                self.assertIn(
                    parameter.kind,
                    (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL),
                    f"{method_name} has an unexpected input parameter {name!r}",
                )

    def test_032_the_organization_argument_is_named_as_a_claim(self):
        """There is exactly one client-supplied identifier in this slice, and the name
        of the parameter says what it is: an organization the caller selected."""

        signature = inspect.signature(DiscoveryService.list_projects)
        self.assertIn("organization_id", signature.parameters)


# --------------------------------------------------------------------------- #
# AuthorizedContext
# --------------------------------------------------------------------------- #
class AuthorizedContextTests(ApplicationDiscoveryTestCase):
    def test_040_the_service_binds_exactly_the_selected_tenant(self):
        try:
            async def body():
                service = self.service()
                async with service.authorized_context(
                    self.owner(), self._seed.primary_org
                ) as context:
                    return context

            context = run_async(body())
            self.assertEqual(context.organization_id, self._seed.primary_org)
            self.assertEqual(context.subject_id, self._seed.owner)
            self.assertEqual(context.membership.organization_id, self._seed.primary_org)
        finally:
            pass

    def test_041_a_context_cannot_be_constructed_by_a_caller(self):
        with self.assertRaises(TypeError) as caught:
            AuthorizedContext(None, None, None)
        self.assertIn("cannot be constructed directly", str(caught.exception))

    def test_042_the_sentinel_guard_refuses_a_foreign_token(self):
        with self.assertRaises(PermissionError):
            AuthorizedContext.create(None, None, None, authorization_token=object())

    def test_043_a_context_refuses_a_membership_that_does_not_match(self):
        """Two consistency checks, so a context cannot authorize the wrong pair."""

        try:
            async def fetch():
                # The system handle reads the organization; the application handle
                # reads the membership. One role per login, so one handle per scope.
                system = PlatformDatabase(
                    self._handle.system_dsn,
                    application_role=SYSTEM_ROLE,
                    min_size=1,
                    max_size=2,
                )
                application = PlatformDatabase(
                    self._handle.dsn, min_size=1, max_size=2
                )
                await system.connect()
                await application.connect()
                try:
                    async with system.server(
                        role=SYSTEM_ROLE, system_scope=True
                    ) as unit:
                        organization = await unit.organizations.get(
                            self._seed.primary_org
                        )
                    async with application.tenant(
                        self._seed.primary_org, user_id=self._seed.owner
                    ) as unit:
                        membership = await unit.memberships.find_for_user(
                            self._seed.owner
                        )
                finally:
                    await system.close()
                    await application.close()
                return organization, membership

            organization, membership = run_async(fetch())
            from app.platform.application.context import authorization_sentinel

            # Correct pair: accepted.
            AuthorizedContext.create(
                self.owner(), organization, membership,
                authorization_token=authorization_sentinel(),
            )
            # Wrong subject: refused.
            with self.assertRaises(ValueError):
                AuthorizedContext.create(
                    principal_for(self._seed.foreign_owner), organization, membership,
                    authorization_token=authorization_sentinel(),
                )
        finally:
            pass


# --------------------------------------------------------------------------- #
# M2-8 — authentication precedes the transaction
# --------------------------------------------------------------------------- #
class OrderingTests(ApplicationDiscoveryTestCase):
    def test_050_a_refused_identity_opens_no_scope_at_all(self):
        """No principal: not even a pre-tenant scope is opened.

        The refusal must happen before any database work, so a request that cannot be
        authenticated does not touch the database.
        """

        try:
            with self.assertRaises(Unauthenticated):
                run_async(self.service().list_organizations(None))
            self.assertEqual(self._database.pre_tenant_opens, 0)
            self.assertEqual(self._database.tenant_opens, 0)
            self.assertEqual(self._database.order, [])
        finally:
            pass

    def test_051_a_client_supplied_identifier_opens_no_scope(self):
        try:
            with self.assertRaises(Unauthenticated):
                run_async(self.service().list_organizations(self._seed.owner))
            self.assertEqual(self._database.order, [])
        finally:
            pass

    def test_052_no_tenant_scope_is_opened_when_the_subject_is_inadmissible(self):
        """M2-8 for AS-1 and AS-2.

        A suspended subject is refused during the pre-tenant resolution, so the
        tenant-scoped Unit of Work is never opened. This is the assertion that would
        catch a future refactor which opens the operating transaction before
        authenticating and authorizing.
        """

        try:
            for call in (
                lambda: self.service().list_organizations(
                    principal_for(self._seed.suspended_user)
                ),
                lambda: self.service().list_projects(
                    principal_for(self._seed.suspended_user), self._seed.primary_org
                ),
            ):
                with self.assertRaises(SubjectNotAdmissible):
                    run_async(call())
            self.assertEqual(
                self._database.tenant_opens, 0,
                "an inadmissible subject must not open a tenant scope",
            )
        finally:
            pass

    def test_053_a_cross_tenant_claim_opens_a_tenant_scope_only_for_the_bound_tenant(self):
        """A refused claim is refused **before** any tenant scope, so the wrong tenant is
        never entered at all."""

        try:
            with self.assertRaises(NotFound):
                run_async(
                    self.service().list_projects(self.owner(), self._seed.foreign_org)
                )
            self.assertEqual(self._database.tenant_opens, 0)
            self.assertNotIn(
                f"tenant:{self._seed.foreign_org}", self._database.order,
                "the foreign tenant must never be entered",
            )
        finally:
            pass

    def test_054_the_authorized_path_enters_only_the_bound_tenant(self):
        try:
            run_async(
                self.service().list_projects(self.owner(), self._seed.primary_org)
            )
            self.assertEqual(
                self._tenant_database.tenant_opens, 1,
                "exactly one tenant scope is opened for one authorized read",
            )
            self.assertIn(
                f"tenant:{self._seed.primary_org}", self._tenant_database.order
            )
            self.assertNotIn(
                f"tenant:{self._seed.secondary_org}", self._tenant_database.order
            )
            self.assertEqual(
                self._database.tenant_opens, 0,
                "the discovery handle must never enter a tenant scope",
            )
        finally:
            pass

    def test_055_the_authentication_step_runs_before_any_database_work(self):
        """The port is consulted first, and a success is what allows a scope.

        Asserted by ordering rather than by result: the adapter is called, and only then
        does a pre-tenant scope appear.
        """

        try:
            events: list[str] = []
            adapter = TestAuthenticationAdapter({"token": self._seed.owner})

            async def body():
                credential = Credential("token")
                principal = await adapter.verify(credential)
                events.append("authenticated")
                service = self.service()
                return await service.list_organizations(principal)

            result = run_async(body())
            events.append("done")
            self.assertEqual(events, ["authenticated", "done"])
            self.assertTrue(result)
            self.assertEqual(self._database.order[0], "pre_tenant")
        finally:
            pass


# --------------------------------------------------------------------------- #
# error translation and layering
# --------------------------------------------------------------------------- #
class ErrorAndLayeringTests(ApplicationDiscoveryTestCase):
    def test_060_every_refusal_carries_a_code_from_the_closed_list(self):
        try:
            cases = (
                (lambda: self.service().list_organizations(None), Unauthenticated),
                (
                    lambda: self.service().list_organizations(
                        principal_for(self._seed.suspended_user)
                    ),
                    SubjectNotAdmissible,
                ),
                (
                    lambda: self.service().list_projects(
                        self.owner(), self._seed.foreign_org
                    ),
                    NotFound,
                ),
                (
                    lambda: self.service().list_projects(self.owner(), None),
                    AmbiguousOrganization,
                ),
            )
            for call, expected in cases:
                with self.assertRaises(expected) as caught:
                    run_async(call())
                self.assertIn(caught.exception.code, ErrorCode.ALL)
                self.assertIsInstance(caught.exception, ApplicationError)
        finally:
            pass

    def test_061_a_client_visible_message_carries_no_identifier(self):
        try:
            with self.assertRaises(NotFound) as caught:
                run_async(
                    self.service().list_projects(self.owner(), self._seed.foreign_org)
                )
            rendered = str(caught.exception)
            self.assertNotIn(self._seed.foreign_org, rendered)
            self.assertNotIn("uuid", rendered.lower())
        finally:
            pass

    def test_062_the_slice_cannot_reach_system_owned_credentials(self):
        """Two halves, both about this slice rather than about persistence.

        First, structurally: no module in the slice names a system repository, so the
        system scope and system-owned credentials are unreachable from here (design
        section 8.2 item 3). Second, empirically: a system-owned provider account is
        invisible to the tenant scope the slice uses, so even a future code path that
        guessed would find nothing.
        """

        # Half 1: the slice names no system repository.
        for module_name in ("organizations.py", "authorization.py", "context.py",
                            "errors.py"):
            source = (
                REPO_ROOT / "app" / "platform" / "application" / module_name
            ).read_text(encoding="utf-8")
            for forbidden in (
                "system_users",
                "system_memberships",
                "system_provider_accounts",
                "SYSTEM_ROLE",
                "system_scope",
            ):
                self.assertNotIn(
                    forbidden, source,
                    f"{module_name} must not reach the system scope",
                )

        # Half 2: a system-owned account is invisible to the tenant scope, measured.
        async def body():
            seeder = PlatformDatabase(
                self._handle.system_dsn,
                application_role=SYSTEM_ROLE,
                min_size=1,
                max_size=2,
            )
            await seeder.connect()
            try:
                async with seeder.server(
                    role=SYSTEM_ROLE, system_scope=True
                ) as unit:
                    account = await unit.system_provider_accounts.create(
                        str(uuid.uuid4()), "openai", "ref:system"
                    )
            finally:
                await seeder.close()

            async with self._tenant_database.tenant(
                self._seed.primary_org, user_id=self._seed.owner
            ) as unit:
                self.assertEqual(
                    await unit.session.fetchval(
                        "SELECT count(*) FROM provider_accounts "
                        "WHERE organization_id IS NULL"
                    ),
                    0,
                    "a system-owned account must not be visible to a tenant scope",
                )
                self.assertEqual(
                    list(await unit.provider_accounts.list_for_current_tenant()), []
                )
                self.assertIsNone(
                    await unit.system_provider_accounts.get(account.id),
                    "the system repository must find nothing from a tenant session",
                )

        run_async(body())

    def test_063_summaries_do_not_expose_other_people(self):
        """A discovery response is about the caller's own tenancy.

        The summaries carry the caller's own role and nothing about any other member:
        no count, no identifier, no email.
        """

        try:
            organizations = run_async(
                self.service().list_organizations(self.owner())
            )
            for summary in organizations:
                for name in dataclasses.asdict(summary):
                    self.assertNotIn(
                        name, ("member_count", "members", "users", "owner_email",
                               "created_by_user_id")
                    )
            fields = {f.name for f in dataclasses.fields(OrganizationSummary)}
            self.assertEqual(fields, {"organization_id", "name", "slug", "role"})
            project_fields = {f.name for f in dataclasses.fields(ProjectSummary)}
            self.assertEqual(
                project_fields, {"project_id", "name", "slug", "status"}
            )
        finally:
            pass


if __name__ == "__main__":
    unittest.main()
