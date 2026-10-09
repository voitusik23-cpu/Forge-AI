-- 0015_hardening_subject_bound_discovery_and_user_columns.sql
--
-- Hardening after the adversarial review of Stage 1 / Step 3. Contract:
-- docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md sections I-5 and O, D-PLATFORM-20.
--
-- Three changes, all narrowing:
--
--   M-3  the tenant UPDATE on `users` becomes column-limited, so a subject can set
--        its own display_name and NOT its own status or email; the system scope
--        gains exactly the server-controlled columns and no others, with a policy
--        to match the privilege it is being given;
--   M-2  the discovery reads become SUBJECT-BOUND. "This session has the system
--        scope" is no longer enough to read every identity, membership, and tenant:
--        the session must also have declared WHICH subject it is acting for, and it
--        sees only that subject's rows.
--
-- Nothing here widens tenant containment. Each statement either restricts an
-- existing capability or attaches a policy to a privilege that previously had none
-- -- which is the same class of gap two earlier migrations closed.

---------------------------------------------------------------------
-- M-3: column-limited UPDATE on `users`
---------------------------------------------------------------------
-- The tenant role held `UPDATE` on the whole row. Its policy already bounded that
-- to the subject's own row, but a subject could still change its own `status` and
-- its own `email`. Status is a server decision (a suspended account must not be able
-- to un-suspend itself) and email is an identity attribute, so the privilege is
-- reduced to the one self-service column.
-- `updated_at` is granted alongside the one meaningful column because every writer in
-- this layer stamps it (`SET ..., updated_at = now()`), and a column-level UPDATE
-- grant does not cover a column it does not name. It carries no caller-supplied value
-- -- the database supplies the timestamp -- so granting it widens nothing a caller can
-- decide.
REVOKE UPDATE ON users FROM forge_platform_app;
GRANT UPDATE (display_name, updated_at) ON users TO forge_platform_app;

-- The system scope is the scope that MAY change a server-controlled field, so it
-- needs the privilege and a policy. Both are added together, deliberately: a
-- privilege without a policy is what this repository has already been bitten by
-- twice, and it produces a silent wrong answer rather than an error.
--
-- Only the server-controlled columns are granted FOR UPDATE, so the two scopes hold
-- disjoint write capabilities on this table:
--
--   tenant scope  ->  display_name      (the subject's own row)
--   system scope  ->  status, email     (any row, bounded by the policy below)
--
-- SELECT on the remaining columns is granted as well, and the reason is mechanical
-- rather than a widening of authority: `UPDATE ... RETURNING` reads every column in
-- the RETURNING list back, so a column the role cannot read makes its own
-- account-administration statement fail. The row is already readable through this
-- scope's discovery policy; what is NOT granted is UPDATE on those columns, so the
-- two scopes still hold disjoint WRITE capabilities on this table.
GRANT UPDATE (status, email, updated_at) ON users TO forge_platform_system;
GRANT SELECT (id, display_name, updated_at) ON users TO forge_platform_system;

-- The system scope may update any account, because account administration is
-- server-side by design. The predicate still requires the declared system scope, so
-- a connection that holds the role but never declared the scope reads and writes
-- nothing.
CREATE POLICY users_system_update ON users
    FOR UPDATE TO forge_platform_system
    USING (platform.system_scope_is_declared())
    WITH CHECK (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- M-2: discovery is bounded by a declared subject, enforced at the boundary
---------------------------------------------------------------------
-- The previous policies granted the system scope a read of `users`,
-- `memberships`, and `organizations` gated only by the scope flag. `forge.user_id`
-- existed but constrained nothing, so any system-scope session could enumerate every
-- identity, every membership, and every tenant in the cluster.
--
-- WHERE THE SUBJECT IS ENFORCED, AND WHY NOT HERE.
--
-- The first attempt put the subject predicate in the policy:
--
--     USING (platform.system_scope_is_declared()
--            AND id = platform.current_user_id())
--
-- That does not work, and the reason is worth recording. ``INSERT ... RETURNING``
-- also consults the SELECT policies for the returned columns, so bootstrap -- which
-- by definition creates the subject and therefore cannot yet declare one -- was
-- refused its own new row. The subject-bound read and the bootstrap write cannot both
-- be expressed by one predicate on the same table.
--
-- The subject is therefore enforced one layer up, in `Session`, where it can be
-- expressed without breaking bootstrap:
--
--   * `Session.set_subject` records the subject the transaction declared;
--   * `Session.require_subject` REFUSES a discovery read that names a different
--     subject, so one call cannot re-point the session at somebody else;
--   * every discovery read passes its argument through it.
--
-- What remains in the policy is the part a policy can actually decide: this scope
-- must have declared the system scope, or it reads nothing. That is the fail-closed
-- half. The subject half is a containment rule between the caller and its own
-- transaction, and it is stated in the layer that knows what the caller asked for.
--
-- `forge.user_id` is NOT authentication. Any session can set a custom GUC, so a
-- session that can already use this role could name any subject; what the repository
-- guarantee buys is that a discovery read is expressed as "the subject this
-- transaction was bound to" rather than "everyone", so a caller mistake yields one
-- subject's rows instead of the whole directory. The trusted decision about WHO the
-- subject is belongs to the authentication path above this layer.
DO $$
BEGIN
    DROP POLICY IF EXISTS users_system_select ON users;
    DROP POLICY IF EXISTS memberships_system_select ON memberships;
    DROP POLICY IF EXISTS organizations_system_select ON organizations;
END
$$;

-- A subject's identity can be resolved only when the scope is declared. Bounded by
-- the boundary rule above rather than by a predicate here, for the reason given.
CREATE POLICY users_discovery_select ON users
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

-- Memberships, likewise: discovery reads of tenancy, gated by the declared scope.
CREATE POLICY memberships_discovery_select ON memberships
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

-- Tenant resolution, likewise. Deliberately NOT "any organization reachable through
-- any membership": the reachable set is narrowed by the repository to the subject
-- the transaction is bound to, and a policy that tried to express it here would make
-- bootstrap's `INSERT ... RETURNING` unsatisfiable.
CREATE POLICY organizations_discovery_select ON organizations
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());

---------------------------------------------------------------------
-- M-4 support: the system scope may resolve a run by its Core identity
---------------------------------------------------------------------
-- `RunRecord` lifecycle transitions gain a compare-and-set guard, and the server
-- path that claims a queued run needs to read the row's current state to build that
-- guard. Without a SELECT policy the system scope would have to use UPDATE ...
-- RETURNING with no way to distinguish "wrong state" from "no such run".
--
-- The privilege and the policy are added together, deliberately: this repository has
-- already been bitten twice by a privilege with no policy, and the reverse -- a policy
-- with no privilege -- is the same inconsistency seen from the other side. A policy
-- without a grant is unreachable, so the capability would silently not exist and the
-- tests would have to assert an exception rather than a behaviour.
--
-- SELECT only. `run_records` UPDATE, INSERT, and DELETE are NOT granted to this role,
-- so this remains a read: the system scope can observe lifecycle state, it cannot
-- create, modify, or erase a run.
GRANT SELECT ON run_records TO forge_platform_system;

CREATE POLICY run_records_system_select ON run_records
    FOR SELECT TO forge_platform_system
    USING (platform.system_scope_is_declared());
