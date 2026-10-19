# ADR O-2 — Production credential verification

Status: **PROPOSED — awaiting owner approval.** Nothing in this document is implemented. No endpoint was
opened, no test was changed, and no third-party provider was contacted.

## 1. Current state and the proven gap

* `app/platform/principal.py:125` defines `@dataclass(frozen=True) class AuthenticatedPrincipal` with exactly
  four fields — `subject_id`, `subject_kind`, `method`, `verified_at` — and its docstring states the design
  intent: a principal answers **who**; every **what** is resolved per operation by the application layer
  against server data. The bare constructor raises; only `create()` builds an instance.
* `app/platform/application/authorization.py:62` `SubjectIdentity` carries the verified principal together
  with the `User` row it was checked against, so callers act on the same facts the decision used.
* `DiscoveryService._authorized(principal, organization_id)` resolves the caller, selects an organization from
  the caller's **own candidate set**, and opens the tenant transaction; a claim outside that set is answered
  `not_found`, never `not_permitted`, so tenants cannot probe each other.
* **The gap:** no component in `app/` verifies a credential from an inbound request. The project records this
  itself in `tests/test_platform_application_discovery.py`: the authentication adapter there is a **test
  double**, "credential verification is open decision **O-2** and no mechanism ships", and the double
  "lives in `tests/`, is never imported from `app/`". `app/api/main.py` likewise documents that the Core HTTP
  surface **has no authentication** and refuses to bind beyond loopback for that reason.

## 2. Server-side opaque sessions vs bearer tokens

| | Server-side opaque session | Signed bearer token (JWT) |
|---|---|---|
| Where truth lives | server store; token is a random key with no meaning | inside the token; server verifies a signature |
| Revocation | immediate (delete the row) | needs a deny-list or short TTL; until then the token stays valid |
| Logout | delete the row | client-side discard; token remains usable until expiry |
| Secret material on the client | an opaque string that reveals nothing | a self-describing payload (subject, org, scopes) |
| Failure mode if a key leaks | sessions unaffected unless the store leaks | every token signed by that key is forgeable |
| Cost in this repository | one table + store code, no crypto dependency | key management, rotation, clock and audience rules |
| Fit with current code | resolves to `subject_id`, then re-checks storage — exactly what `SubjectIdentity` expects | the payload tempts callers to trust claims instead of re-reading storage |

## 3. Credential lifecycle (recommended: server-side session)

1. **Establish** — the caller presents a credential to a dedicated verification step outside the application
   services; the verifier resolves it to a `subject_id` and reports how it did so.
2. **Mint** — the server creates a session row: opaque random token (stored only as a hash), `subject_id`,
   issue time, expiry, and a revocation column. The raw token is returned once, in the response body.
3. **Present** — the client sends the token (e.g. `Authorization: Bearer <opaque>`); the server hashes it and
   looks the row up. No claim inside the token is trusted, because the token carries none.
4. **Expire** — a bounded lifetime; expiry is enforced at every lookup, never by the client.
5. **Revoke / logout** — delete or mark the row; the effect is immediate for every subsequent request.
6. **Rotate** — re-issuing on sensitive changes is a row operation, not a token re-signing exercise.

## 4. Secret storage and leak protection

* Only **hashes** of session tokens are stored; the raw token exists on the client and in the response body.
* Credentials are never logged, never placed in DTOs, never echoed in error messages, and never written to
  documentation; the existing closed error-code table is reused so a refusal cannot leak identifiers.
* The verification step lives in the HTTP composition only, so application services continue to receive a
  `principal` argument and never see a token, a password, or a header.
* Runtime secrets come from the existing `SecretStore` boundary; `.env` and protected artifacts stay untouched.

## 5. Re-checking the subject against authoritative storage

After the token resolves to a `subject_id`, the application still resolves the subject through storage
(`SubjectIdentity`) before any use case runs — a suspended account, an inactive membership, or a deleted user
must be invisible **because of what the database says**, not because Python remembered. This preserves the
property the existing tests assert: a refusal is the database's answer, not an `if` in application code.

## 6. Server-side resolution of organization and project

* `organization_id` remains a **claim** validated against the caller's candidate set (`select_candidate`),
  with the non-disclosure answer `not_found`.
* `project_id` is a lookup key only: the Core derives the workspace **exclusively** from the trusted result
  (`AuthorizedProject`), never from the request, a header, the idempotency fingerprint, or a path.
* No request field may carry `subject_id`, `organization_id`, `workspace`, or an "authorized project" marker
  that the server treats as fact. `NoSubjectParameterTests` already asserts that no input DTO has a subject
  field; the same rule extends to organization and workspace.

## 7. Cross-project and cross-tenant protection

* Tenant isolation is structural: the repository has **no** `organization_id` parameter; it reads and writes
  inside the tenant of the surrounding Unit of Work opened by `_authorized`.
* A foreign or non-existent tenant identifier produces the same code and the same message (measured today).
* Workspace references stay opaque and server-derived (`Project.workspace_ref`), and the domain contract
  already asserts that this reference is not a filesystem-path authority.

## 8. Required negative tests and acceptance criteria

* No credential → refusal **before** any read or write; the sequence test that no tenant scope opens.
* Malformed, wrong-scheme, expired, revoked credential → refusal, and for revoked: immediate.
* Token of subject A used to address subject B's organization → `not_found`, never `not_permitted`.
* Suspended subject / inactive membership → refused by the storage answer.
* Client-supplied `subject_id`, `organization_id`, `project_id`, `workspace` → ignored or refused.
* Session token value must never appear in any log line, error body, or trace (assert on captured output).
* Acceptance: every case above is asserted by a test that fails before the mechanism and passes after it; the
  suite that exists today (`test_platform_application_discovery.py`, 42 tests) keeps passing unchanged.

## 9. Migrations, operational risks, implementation order

* One new table (session token hash, subject id, issued/expires/revoked timestamps) plus an index; no change to
  existing tables, so no backfill and no data migration for current rows.
* Risks: clock skew on expiry; store availability (a session store outage must **fail closed**, refusing
  rather than admitting); token leakage in client storage (bounded by short lifetime and revocation);
  operational need for a revocation path and an audit trail.
* Order: (1) tokens-only-on-the-server store and its tests, offline; (2) the verification step in the HTTP
  composition with its negative tests, still loopback-only; (3) session lifecycle endpoints (login, logout,
  revoke) — **only after owner approval**; (4) then, and only then, the Phase 7 requests that need a session.

## 10. Open owner decisions

1. Approve **server-side opaque sessions** (recommended) or signed bearer tokens.
2. Approve the credential form for the first mechanism: local username + password with hashes, an
   operator-provisioned pre-shared key, or an external OIDC provider (least reversible).
3. Approve opening the session lifecycle endpoints on the loopback-only Core surface.
4. Decide whether the Core surface ever gains authentication at all, or whether sessions terminate at a
   fronting component and only an assertion crosses into Core.

**Recommendation.** Server-side opaque sessions with hashed tokens, resolved to a subject and then re-checked
against storage, with `organization_id` and `project_id` continuing to be claims resolved server-side. It
needs no cryptography to get right, makes revocation and logout immediate, keeps claims out of the client, and
matches the shape the application services already expect: a verified `principal`, and nothing else.

## 11. Open questions (deliberately unresolved — for the owner's decision)

None of these is implemented, and this ADR stays **PROPOSED — awaiting owner approval** until they are settled.

1. **Creating and first activating a local account.** Who may create the first account, and how is it
   activated without an existing session? Options: operator-provisioned out of band, a one-time activation
   secret issued on the server and delivered to the owner, or an external identity provider. Needs a decision
   on whether self-registration exists at all — for a single-owner desktop product, "no self-registration"
   is the safer default.
2. **Password hashing algorithm and its parameters.** Candidate: Argon2id (memory-hard, tunable) or, if a
   dependency must be avoided, PBKDF2-HMAC-SHA256 with a per-account random salt and a calibrated iteration
   count. Decisions needed: the exact algorithm, the cost parameters for this machine, whether parameters are
   stored per hash (they should be, to allow upgrading), and the rehash-on-login policy. No hash parameters
   are chosen here, and no dependency is added by this document.
3. **Password reset and account recovery.** Options: an operator-run reset that mints a one-time token with a
   short expiry; a recovery secret generated at activation; or no recovery path, with the operator re-creating
   the account. Must also decide what happens to **existing sessions** on reset — recommended: revoke every
   session for that subject.
4. **How the desktop client stores its session.** Options: in memory only (session lost on close, most
   restrictive), or in a file under the user's profile protected by an ACL restricted to that user, with a
   bounded lifetime. The client must never store a password, and the token must never be written into the
   repository, into logs, or into any file under the project tree.
5. **Protecting the local API from other local processes.** The surface is loopback-only today, which any
   local process can reach. Options: require the session token for every non-public route; bind additionally
   to a Unix-domain/named pipe with an ACL; or have the launcher hand the token to the window through a
   channel a different user cannot read. Needs a decision on how much protection is warranted for a
   single-user desktop machine, and this must not be presented as protection against a local administrator.
6. **Expiry, revocation and rotation.** Decisions: absolute lifetime, idle timeout, whether rotation happens
   on privilege-relevant changes, whether the operator can revoke all sessions at once, and how revocation
   interacts with in-flight runs (recommended: revoke affects new requests; running work is stopped through
   the existing cancellation path, never silently continued).
7. **Cookie versus header, and CSRF, if a browser path is used.** If sessions are ever presented by a browser
   with cookies, the session cookie must be `HttpOnly`, `SameSite=Strict`, `Secure` where TLS exists, and
   state-changing routes need CSRF protection. If the desktop window keeps using an `Authorization` header,
   CSRF does not apply and this question stays closed. Needs a decision on which path the product keeps.
8. **Behaviour when the session store is unavailable.** Must **fail closed**: an unreachable or unreadable
   store refuses requests rather than admitting them, and the refusal must be distinguishable in the logs
   from "no valid session" without disclosing which identifiers exist. Needs a decision on the operator-facing
   message and on whether the desktop shell shows a retry affordance.

**Recommended option for discussion:** server-side opaque sessions with hashed tokens, plus **local** password
verification, keeping the principal's four fields unchanged and letting organisation and project stay claims
resolved server-side.
