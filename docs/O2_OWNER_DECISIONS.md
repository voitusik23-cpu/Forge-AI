# O-2 — Owner decision package

Companion to `docs/ADR_O2_CREDENTIAL_VERIFICATION.md` (**status unchanged: PROPOSED — awaiting owner
approval**) and `docs/PHASE7_VERTICAL_SLICE_PLAN.md`. Nothing here is implemented: no endpoint was opened, no
account was created, no dependency was added, and no test was changed.

Reading the table: **Recommendation** is what the code in this repository supports best; **Needs your
approval** marks decisions that are yours to make, not mine.

## D1. No self-registration; safe first account

* **Recommendation.** No self-registration at all. The first account is created by an operator-run command
  that already has the Platform credentials, and it is activated with a one-time secret with a short expiry.
* **Alternatives.** (a) Self-registration with an activation secret issued to the owner; (b) external
  identity provider, which removes local account creation entirely.
* **Security impact.** Self-registration on a loopback service would let any local process claim an identity
  and then request project creation — the exact escalation this design exists to prevent. Operator-creates-
  first-account keeps the initial trust root outside the running API.
* **Desktop impact.** The first run of the desktop app must handle "no account yet" honestly: a message
  telling the owner to run the activation step, never a form that pretends to register.
* **Tests.** No route exists that creates the first account (assert the route table); activation secret is
  single-use and expires; a second activation attempt after use is refused; the refusal discloses nothing.
* **Needs your approval:** yes — whether self-registration is permanently excluded.

## D2. Password hashing: Argon2id or PBKDF2-HMAC-SHA256

* **Recommendation.** **Argon2id** if a dependency is acceptable; otherwise **PBKDF2-HMAC-SHA256**, which is
  already available in the standard library (`hashlib.pbkdf2_hmac`) and needs no new package.
* **Alternatives.** Argon2id (memory-hard, best resistance to GPU cracking, requires a new dependency);
  PBKDF2-HMAC-SHA256 (stdlib only, weaker against GPUs at equal cost, still acceptable with a high iteration
  count); scrypt (stdlib `hashlib.scrypt`, memory-hard, a middle option).
* **Security impact.** The choice sets offline cracking cost if the database ever leaks. Memory-hard
  algorithms (Argon2id, scrypt) cost an attacker more per guess than PBKDF2 at the same login latency.
* **Desktop impact.** High iteration/memory parameters add latency to the login step on the owner's machine;
  parameters must be measured there, not copied from a blog.
* **Tests.** A stored hash verifies with the right password and fails with a wrong one; the stored format
  records algorithm, parameters and salt; a hash created with old parameters is transparently re-hashed on the
  next successful login; no plaintext password appears in any log or error.
* **Needs your approval:** yes — **adding a dependency or not** is your call. This document adds none.

## D3. Hashing parameters and hash-upgrade policy

* **Recommendation.** Store parameters **with** each hash (e.g. `algorithm$params$salt$hash`), calibrate once
  on the owner's machine to a target login latency, and re-hash on successful login whenever the stored
  parameters are below the current target.
* **Alternatives.** Fixed global parameters (simpler, but upgrading later requires a migration); per-user
  parameters (more flexibility, more code).
* **Security impact.** Parameters that are too low weaken the hash; raising them later without re-hashing on
  login leaves old accounts weak.
* **Desktop impact.** Calibration must not make login feel broken; if it exceeds the target latency, lower the
  parameters and say so rather than shipping a slow first run.
* **Tests.** Re-hash-on-login is asserted (old-format hash replaced with a new one after a correct password);
  wrong password never triggers a re-hash; the parameter fields round-trip through storage.
* **Needs your approval:** yes — the latency budget for login on your machine.

## D4. Password reset and session revocation

* **Recommendation.** Operator-run reset only, minting a single-use token with a short expiry; a successful
  reset **revokes every existing session** of that subject.
* **Alternatives.** A recovery secret issued at activation; no recovery path (re-create the account).
* **Security impact.** Reset is the classic account-takeover path: without session revocation, an attacker who
  already holds a session keeps it after the legitimate owner resets the password.
* **Desktop impact.** After a reset the desktop app must return to the login state instead of showing a broken
  window; it must not silently retry with a stale token.
* **Tests.** Reset token is single-use and expiring; after reset, every previously issued session is refused;
  the reset path leaks no identifiers; a refused session is distinguishable from "no session" only in logs.
* **Needs your approval:** yes — whether a recovery path exists at all.

## D5. Where the desktop client keeps its session

* **Recommendation.** In memory only, with the session lifetime bounded by an idle timeout; if persistence
  across restarts is wanted, a file under the user's profile with an ACL restricted to that user.
* **Alternatives.** In-memory only (safest, session lost on close); ACL-protected file (convenient, at rest on
  disk); OS credential store (not chosen here — adds a platform dependency).
* **Security impact.** Any on-disk token is readable by anything running as that user, so on-disk storage buys
  convenience, not secrecy.
* **Desktop impact.** In-memory means the owner logs in after each start; the on-disk option must be paired
  with the idle timeout and a visible "sign out".
* **Tests.** The token is never written inside the repository tree, never into logs, and never into a launch
  journal; the chosen store is refused when its ACL is wider than the current user; sign-out clears it.
* **Needs your approval:** yes — convenience versus safety on your own machine.

## D6. Protecting the loopback API from other local processes

* **Recommendation.** Require the session token on **every** non-public route, and keep the surface
  loopback-only. Treat this as protection against other *user-level* processes, not against a local
  administrator — that distinction must be stated in the UI/help text rather than implied.
* **Alternatives.** (a) Named-pipe/Unix-socket transport with an ACL instead of TCP; (b) the launcher passes
  the token to the window through a channel another user cannot read; (c) accept the exposure and rely on the
  machine being single-user.
* **Security impact.** Any local process can reach a loopback port, so without a token requirement every
  project and run route is reachable by any program running as any user who can connect.
* **Desktop impact.** The window must obtain the token from the launcher rather than asking the owner to paste
  it; the launch journal must never contain it.
* **Tests.** Every non-public route refuses without a token and accepts with one; a request carrying only a
  client-supplied identity field is refused; the token is absent from the launcher journal and API logs.
* **Needs your approval:** yes — how much hardening is proportionate here.

## D7. Expiry, revocation and rotation

* **Recommendation.** Absolute lifetime plus idle timeout; immediate revocation by deleting the session row;
  rotation on privilege-relevant changes (password reset, explicit sign-out everywhere); revocation affects
  new requests, and work already running is stopped through the existing cancellation path rather than left
  running silently.
* **Alternatives.** Long-lived tokens with a deny-list (weaker revocation until the list is consulted); very
  short lifetimes with frequent re-authentication (safer, more friction).
* **Security impact.** Server-side rows make revocation immediate; long lifetimes without revocation make a
  stolen token durable.
* **Desktop impact.** An idle timeout that is too short will interrupt the owner mid-task; pick it with the
  desktop workflow in mind and show a clear "session expired" state.
* **Tests.** Expired session refused; revoked session refused immediately; rotation invalidates the previous
  token; a revoked session cannot start a new run, and a running one reaches a terminal cancelled state.
* **Needs your approval:** yes — the concrete lifetime and idle timeout.

## D8. Browser cookie/CSRF path — needed or not?

* **Recommendation.** **Not needed now.** The desktop window talks to the loopback API with an
  `Authorization` header, so there is no cookie and no CSRF surface. If a browser-hosted UI is ever added, the
  session cookie must be `HttpOnly`, `SameSite=Strict`, `Secure` where TLS exists, and state-changing routes
  must carry CSRF protection.
* **Alternatives.** Adopt cookies now (several browser protections come free, but CSRF work appears
  immediately and the desktop path becomes indirect).
* **Security impact.** Cookies without `SameSite`/CSRF protection are the standard cross-site request-forgery
  exposure; headers avoid the class entirely.
* **Desktop impact.** None for the header path; a cookie path would change how the Edge window is configured.
* **Tests.** For the header path, assert no `Set-Cookie` is emitted by session routes. If the cookie path is
  ever adopted, add CSRF-positive and CSRF-negative tests before it ships.
* **Needs your approval:** yes — whether the product keeps only the desktop window.

## D9. Fail-closed when the session store is unavailable

* **Recommendation.** An unreachable or unreadable session store refuses the request (503-class) rather than
  admitting it; the operator-facing log distinguishes "store unavailable" from "no valid session", while the
  client-visible message reveals no identifiers.
* **Alternatives.** Degrade to "no authentication" (rejected: it silently opens the surface); degrade to cached
  sessions in memory (acceptable only with an explicit, short TTL and a loud log entry).
* **Security impact.** Failing open turns a storage outage into an unauthenticated API — the single most
  dangerous failure mode in this design.
* **Desktop impact.** The window must show a retryable error instead of a blank or misleading screen.
* **Tests.** With the store made unavailable, every session-protected route refuses; the refusal carries no
  identifier; the log distinguishes the two causes; recovery restores service without restarting the app.
* **Needs your approval:** yes — the operator-facing wording, once the mechanism exists.

## First PR boundary (proposal only — nothing implemented)

**In scope:** the session store (hashed tokens only; one table plus an index; lifetimes stored per row), the
token verification step as a dependency of the HTTP composition, and the negative tests. No public lifecycle
route (no login/logout/revoke endpoints) and no UI wiring.

**Out of scope:** login endpoints, the desktop sign-in flow, any UI change, project→Core workspace linkage,
and any change to `authorize_project`, Phase 2 or `RunIdempotencyGuard`.

**Checks required before the next stage:** the full existing Platform suite stays green (currently
`Ran 42 tests … OK`, exit 0, `test_070`–`test_074` = `ok`); `python -m compileall -q app tests` = 0;
`git diff --check` = 0; the new negative tests fail before the change and pass after it; and a review confirms
no client-supplied `subject_id`, `organization_id`, `project_id` or workspace is ever treated as fact.

## Consistency with existing contracts

* `AuthenticatedPrincipal` keeps its **four** fields; this package adds none and trusts no client-supplied
  principal.
* Platform persistence and tenancy are untouched: the tenant still comes from the Unit of Work opened by
  `_authorized`, and the project repository still takes no `organization_id`.
* Core's `authorize_project` remains the single project boundary; no competing mechanism is introduced.
* **The known risk is not resolved by this package** — `app/api/service.py:475 self._active_project_id =
  "default"` still means one active workspace per Core instance, so **cross-project isolation is not proven**
  and project→workspace integration is **not implemented**. It is a Phase 7 slice, not a documentation change.
* ADR status remains **PROPOSED — awaiting owner approval**.
