# Forge AI — Stage 1 / Step 3: Platform Persistence, Repositories, and Unit of Work

> **Status.** Implemented. This document describes what exists and why, and it is
> the persistence design of record. It was written **after** the implementation,
> because the design document this task named
> (`docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md`) **did not exist in the repository**:
> it has never been committed, and no other document carried its content. That gap
> is recorded in `docs/DECISIONS.md` as `D-PLATFORM-20` rather than papered over;
> the implemented contract below is derived from the Step 3 brief, the frozen Stage 1
> Architecture Contract (sections 17 and 18), and the Step 2 schema contract, which
> is the normative source for every database behaviour referenced here.
>
> **Authority.** `docs/STAGE-1-ARCHITECTURE-CONTRACT.md` and
> `docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md` remain normative. Where this document and
> they disagree, they win.

---

## 1. Scope

This step closes **persistence only**: row mapping, repository interfaces, PostgreSQL
repository implementations, the Unit of Work, scope management, and error
normalization.

**Not in this step, deliberately:** HTTP endpoints, authentication, an authorization
engine, Billing, payments, the Platform -> Core transport,
`TrustedExecutionRequest`, credential resolution, API-key token generation or
hashing, Core changes, and any redesign of O-1 or O-7.

## 2. Layering

```
application services
  -> protocols            interfaces; no database knowledge at all
  -> UnitOfWork           transaction boundary, scope, repository set
  -> repositories         parameterized SQL; no transaction management
  -> Session              transaction guard, error normalization
  -> PlatformDatabase     connection pool, role safety check
  -> PostgreSQL + RLS
```

The dependency direction is one-way. `app/platform/models.py` imports nothing from
this package, this package's protocols import nothing from a driver, and the driver
(`asyncpg`) is imported only inside `database.py`, lazily. Nothing under `app/`
outside `app/platform/` imports the persistence package.

**The domain does not become an ORM.** The eight records stay frozen stdlib
dataclasses. `mapper.py` is the single module that knows both a domain record and a
physical column, which is what keeps a schema change from turning into a model
change.

## 3. Two logins, three scopes

The schema defines two database roles. A deployment **must** serve them through two
different logins: if one login were a member of both, a connection handling a tenant
request could step into the system scope and the boundary would exist only in the
policy text. `PlatformDatabase` therefore takes an `application_role`, steps down to
it at pool setup, and **refuses to build a pool** when the effective role is a
superuser, has `BYPASSRLS`, or owns the Platform tables — because all three exempt a
session from the policies it is supposed to obey.

| Scope | Factory | Effective role | Context | What it may do |
| --- | --- | --- | --- | --- |
| tenant | `database.tenant(org, user_id=...)` | `forge_platform_app` | `forge.organization_id`, `forge.user_id` | everything tenant-owned: projects, provider accounts, API keys, runs, usage |
| pre-tenant | `database.pre_tenant(user_id=...)` | `forge_platform_system` + system scope | no tenant | read identity and tenancy: user, memberships, organizations |
| server | `database.server()` | `forge_platform_app` | none | bootstrap writes for a known tenant; reads nothing |
| server (system) | `database.server(role=SYSTEM_ROLE, system_scope=True)` | `forge_platform_system` + system scope | none | system-owned provider credentials; membership administration |

`forge.system_scope` **is not an authorization mechanism.** Any session can set a
custom GUC, so what gates the system scope is the policy's `TO` clause plus the role,
which the deployment controls. The flag only narrows the scope further and keeps
system-owned rows invisible by default. The security tests assert that the tenant
path gains nothing by setting it.

## 4. Transaction discipline

* **Repositories never open, commit, or roll back a transaction.** The words do not
  appear in `repositories.py`. `Session` refuses every statement outside a
  transaction, so a repository used outside a Unit of Work raises
  `TransactionError` instead of quietly auto-committing.
* **The Unit of Work owns the lifecycle.** It commits on clean exit, rolls back on
  exception, and closes the session in `finally`. `commit()` is idempotent, so an
  explicit `uow.rollback()` followed by a normal exit is not an error.
* **The scope is transaction-local.** `set_config(name, value, true)` is used rather
  than a literal `SET LOCAL`, because `SET` accepts no placeholder and building the
  statement would mean interpolating a caller-supplied value into SQL. The role is
  switched with `SET LOCAL ROLE`, so it also reverts when the transaction ends.
* **The connection is clean on release.** `Session.close()` ends the transaction if
  one is open, resets the role, and marks the session closed. A pooled connection
  therefore cannot carry one request's tenant into the next; the tests exercise
  tenant A, then tenant B, repeatedly on one pool.

Order inside a scope factory: transaction first, then role, then context. The
transaction must exist before anything transaction-local is set, or the setting
would have no scope.

## 5. Repository interfaces

Nine protocols in `protocols.py`, one per repository plus the system-scoped provider
account repository. Two rules are encoded in the **signatures** rather than in
documentation:

1. **A tenant-scoped repository never takes an organization identifier.** The tenant
   comes from the Unit of Work. There is no parameter to pass a foreign tenant, and
   therefore none to forget to validate. Methods that need an organization for a
   structural reason take an *entity* identifier instead, and the scope still comes
   from context.
2. **There is no generic `update(**fields)`.** Each mutable transition is a named
   method, so an immutable field has nowhere to be written from.

## 6. Identity boundary

The domain contract accepts any opaque non-empty identifier. The schema stores
`uuid`. `mapper.as_uuid` is that boundary: it converts an identifier or raises
`InvalidIdentifierError` naming the field, **before** any SQL is built. A malformed
identifier therefore becomes a named persistence error rather than a driver cast
failure, and it can never decay into an empty context that silently returns nothing.

`core_run_id` and `task_id` are `text` in storage and are **not** identifiers of this
kind: they are opaque correlation/text values, so no validation is applied to them —
matching the contract, which does not make them UUIDs.

## 7. Repositories

| Repository | Notes |
| --- | --- |
| `users` | create, get, find_by_email (retrieval only, not authentication), status, display name, `list_all` for the server scope |
| `organizations` | create, get, `list_for_user` (the discovery read). **No update:** no scope holds `UPDATE`, so no method advertises one |
| `memberships` (tenant) | create, get, find/list by user, list for tenant, status, role. The tenant comes from the session and is not a parameter |
| `system_memberships` | create, for the bootstrap case only: the first membership of a new tenant has no tenant context to derive |
| `projects` | create, get, list, find_by_slug, name, slug, workspace_ref, status |
| `provider_accounts` (tenant) | create, get, list, find_by_provider_name, secret_ref, metadata, status |
| `system_provider_accounts` | the same shape, bounded to `organization_id IS NULL` |
| `api_keys` | create, get, list, list by creator, revoke, expiry, record use |
| `run_records` | create_queued, get, find/list by core_run_id, list by task_id, claim, finish, set_status, increment_attempt_count |
| `usage_records` | **append, get, list only** |

## 8. Provider accounts: two ownership modes, two repositories

`ProviderAccount` has exactly two modes, and they are reached through two different
repositories rather than one repository with a nullable filter:

* `provider_accounts` writes the tenant from the session. Every statement is bounded
  by the current tenant, and `organization_id IS NULL` is never used as a lookup.
* `system_provider_accounts` is bounded to `organization_id IS NULL` and is reachable
  only from the server scope with the system role, whose policy requires both a null
  organization and an explicitly declared system scope.

There is no `get_by_organization(None)` and no method that could accept one. A tenant
cannot see, create, or promote into a system-owned account; the system scope cannot
see or adopt a tenant account.

## 9. Usage records: append-only by construction

`UsageRecordRepository` has no update and no delete method, and the protocol has none
either. That is the design: the table's privileges withhold `UPDATE` and `DELETE`
and no policy grants them, so an attempt is refused by the database even if a method
existed. A duplicate `(run_record_id, attempt_number)` is a **unique violation**, not
a silent overwrite, and a correction is a new record.

No field carries money. There is no cost, price, charge, balance, or currency, and
none may be added: this is a measurement, not an invoice.

## 10. Run lifecycle persistence

`RunRecord.id` is the Platform identity; `core_run_id` is the Core correlation
identity. They are different values, the schema refuses to let them collapse, and no
method accepts one where the other belongs. `core_run_id` is **not** unique (K-8
defers retry/resume semantics), so `find_by_core_run_id` returns one row and
`list_by_core_run_id` returns every match.

A queued run carries no `core_run_id`, no `started_at`, and no initiator, because the
schema forbids the first two and queued is the one state in which an absent initiator
is accepted. `claim` sets `core_run_id`, `started_at`, and the initiator together —
the transition that crosses the Core boundary — and its `status = 'queued'`
predicate is part of the `UPDATE`, so two workers racing to claim one run cannot both
succeed. `increment_attempt_count` is `attempt_count = attempt_count + 1` in a single
statement, so concurrent increments cannot lose an update.

No new lifecycle state is introduced and no terminal-state rule is asserted: which
statuses are terminal is **O-7**, still open, and this layer constrains only what the
domain contract and the schema already fix.

## 11. Concurrency

Uniqueness is left to the database, never to a pre-check:
`UNIQUE (organization_id, user_id)` for memberships, the tenant-scoped partial unique
index for project slugs, both partial unique indexes for provider accounts, and
`UNIQUE (run_record_id, attempt_number)` for measurements. A check-then-insert would
still lose a race, so the constraint is the authority and the losing writer receives
a `UniqueViolationError` naming the constraint. The claim path uses a conditional
`UPDATE` for the same reason. The tests cover each of these conflicts.

## 12. Error normalization

`database.normalize_error` is the only place a driver exception is translated. The
hierarchy is deliberately small, and each member exists because a caller must be able
to act differently: `EntityNotFound`, `InvalidIdentifierError`, `UniqueViolationError`,
`ForeignKeyViolationError`, `CheckViolationError`, `PermissionDeniedError`,
`TransactionError`, `ConnectionError`, all deriving from `PersistenceError`.

Two rules:

* **An authorization failure is not reported as absence.** A policy denial is
  `PermissionDeniedError`; conflating it with "not found" would let an authorization
  failure masquerade as a missing record.
* **The useful diagnostics survive.** The SQLSTATE and the violated constraint name
  are preserved, so a caller can react to a specific lost race and an operator can
  read the original message. An already-normalized error passes through unchanged, so
  wrapping is idempotent.

## 13. Test strategy

`tests/test_platform_persistence.py` (107 tests) and `tests/persistence_fixture.py`
run against real PostgreSQL. The fixture provisions **two logins, each a member of
exactly one platform role**, because a single login in both would let the tests step
around the boundary they are meant to prove.

Coverage: Unit of Work (transaction, commit, rollback, explicit rollback, context
before/after commit and rollback, no inheritance across transactions, unusable
repositories after exit, all four scopes, malformed identifiers, unsafe pool
refusal); mapping for all eight entities, nullable semantics, timezone round trip,
representation safety; tenant isolation (read, update, delete, foreign user,
foreign project, foreign run transition, no-context denial); every repository's
documented operations; lifecycle transitions; append-only usage; pre-tenant
discovery; and error normalization.

## 14. Two policy gaps this step closed

Implementing the repositories found two real inconsistencies in the Step 2 schema,
both of the same shape — **a privilege granted with no policy to match**. Neither was
a live tenant escape, and both are recorded rather than quietly fixed:

1. The system role held `INSERT` on `users` with no INSERT policy, so the bootstrap
   path (organization + first `OWNER` membership) could not actually run.
2. The tenant role held `UPDATE` on `users` with no UPDATE policy, so an update
   matched zero rows and the repository reported a missing record — a silent wrong
   answer, the worst of the three possible outcomes.

Both now have policies: `users_system_insert` for the bootstrap, and
`users_tenant_update` bounded to the subject's own row
(`id = platform.current_user_id()`), so one subject cannot rewrite another's record.

## 15. Adversarial review and its findings

An independent adversarial review of this layer was run before it was committed. It
found eight defects that the suite above had missed, two of which were ways to
**defeat the pool's role guard** — the check that exists precisely so a deployment
cannot run with row-level security silently disabled. All eight were reproduced, fixed,
and given a regression test that fails against the previous behaviour; the full record
is in `docs/DECISIONS.md` under `D-PLATFORM-20`.

The two serious ones, summarised because they changed the guard's shape:

* **A superuser login was accepted.** The guard asked `pg_has_role` for membership and
  read the privileged-role attributes *after* substituting the application role. Since
  `pg_has_role` is vacuously true for a superuser, and the login's own attributes were
  never read, the guard passed. It now verifies the login's attributes before any
  switch, using both the catalogue flags and `pg_has_role(role, 'postgres', 'USAGE')`.
* **One login could hold both scopes.** A tenant login that was also granted the system
  role could step into the system scope from a tenant pool. A tenant pool now refuses to
  build on such a login.

The remaining six: the ownership check was pinned to schema `public`; two `organizations`
methods could never succeed and reported a permission refusal as absence (removed); the
membership insert took the tenant from the assembled record (the tenant insert now takes
no organization parameter at all, with a separate `system_memberships` repository for
the bootstrap path); unserializable provider metadata escaped as raw `TypeError`; a bare
string scope was silently split into characters; and `find_by_slug` disagreed with the
writer about the empty slug. The last of those is worth recording: the first attempt at
the fix used plain equality, which cannot match `NULL`, and the regression test caught it.

**What the review confirmed as correct:** every tenant-scoped statement is bounded by the
session tenant; the system-owned provider account repository is unreachable from a tenant
session in every operation; there is no SQL injection (only hardcoded column constants
and validated role names are interpolated, and all values are bound); no tenant context
leaks across pooled connections; error normalization is idempotent; the mapping layer
matches every domain record field for field; and nothing outside `app/platform` imports
the persistence package or the driver.

## 15.1 Second review: the hardening pass

A **second** adversarial review reported six further findings — one HIGH and five
MEDIUM — and all six are fixed with regression tests. The full record is
`D-PLATFORM-21`; the parts that changed this design are summarised here because a
reader of the sections above would otherwise be misled.

**Commit correctness (H-1).** PostgreSQL accepts `COMMIT` on a transaction that an
earlier failed statement aborted, **rolls it back**, and returns the status string
`ROLLBACK` without raising. A service that caught a repository error and continued
therefore lost every write silently. `Session` now records the aborted state, checks
the `COMMIT` status string, rolls back, and raises `TransactionError` naming the
original cause. Section 4's description of `commit` as idempotent still holds for a
transaction that already ended; it is no longer true that a failed transaction commits
quietly, which was the defect.

**Discovery (M-2).** The discovery policies no longer grant the system scope a read of
every identity, membership, and tenant. Discovery is bound to **one subject per
transaction**, enforced by `Session.require_subject`, and `users.list_all` is removed.
Section 3's scope table and section 7's repository inventory both changed: the
`memberships` and `organizations` rows now say the subject is required, and the `users`
row no longer offers a directory read.

The binding lives in the session rather than in the policy, and **that choice was made
after measuring the alternative**: a subject predicate in the policy makes
`INSERT ... RETURNING` unsatisfiable for bootstrap, because `RETURNING` also consults
the SELECT policies for the columns it returns. `forge.user_id` remains not
authentication; section 3's statement about `forge.system_scope` applies equally to the
subject setting.

**Identity writes (M-3).** The two Platform scopes now hold **disjoint** write
capabilities on `users`: the tenant scope may set `display_name` on the subject's own
row, and the system scope may set `status` and `email` on any row with a policy to
match. Section 3's "the system scope cannot change an account" is superseded: it can,
and only for server-controlled fields.

**Lifecycle (M-4) and tenant binding (M-5).** `finish` and `set_status` are
compare-and-set inside the `UPDATE` and raise `ConcurrentModificationError` on a lost
race; a finished run cannot be finished again or revived, and **O-7 remains open**.
A tenant-scoped write whose record names a different tenant is refused rather than
redirected. The system scope gained read-only `SELECT` on `run_records` so the guard can
see the state it guards.

## 16. Open decisions

Unchanged by this step. O-1 (key format, and the authority model it gates), O-2
(authentication), O-3 (secret backend), O-4 (`secret_ref` grammar), O-5 (workspace
topology), O-6 (Platform -> Core transport), and O-7 (`RunRecord` status enum) remain
**open**. O-8 stays resolved at contract level. O-9 stays resolved and implemented.
O-10 … O-13 stay deferred. No new decision was invented to make an implementation
convenient.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  POSTGRESQL SCHEMA AND RLS IMPLEMENTED
Step 3     =  PERSISTENCE, REPOSITORIES, UNIT OF WORK IMPLEMENTED

Implementation status:  SCHEMA IMPLEMENTED, DATABASE CONTRACT TESTS PASSING
```

## OPEN DECISIONS

**Open:** O-1 API key format and authority model; O-2 session/auth mechanism; O-3
secret backend/KMS; O-4 `secret_ref` grammar; O-5 workspace topology; O-6
Platform -> Core transport; O-7 `RunRecord` status enum.

**Resolved for contract:** O-8 — `UNIQUE (run_record_id, attempt_number)`.

**Resolved as a design decision:** O-9 — the RLS design, implemented.

**Deferred:** O-10 provider pricing source; O-11 payment processor; O-12 tax/invoice;
O-13 reseller/partner economics; all Billing and deferred entities.

---

# Forge AI — Stage 1 / Шаг 3: персистентность Platform, репозитории и Unit of Work — русская версия

> **Статус.** Реализовано. Этот документ описывает то, что существует, и почему —
> он является зафиксированным дизайном персистентности. Он написан **после**
> реализации, потому что названный в задании документ
> (`docs/STAGE-1-STEP-3-PERSISTENCE-DESIGN.md`) **в репозитории отсутствовал**: он
> никогда не коммитился, и никакой другой документ его содержания не нёс. Этот
> пробел зафиксирован в `docs/DECISIONS.md` как `D-PLATFORM-20`, а не замаскирован;
> реализованный контракт ниже выведен из задания Шага 3, замороженного Architecture
> Contract Stage 1 (разделы 17 и 18) и контракта схемы Шага 2, который является
> нормативным источником для любого упомянутого здесь поведения базы данных.
>
> **Authority.** `docs/STAGE-1-ARCHITECTURE-CONTRACT.md` и
> `docs/STAGE-1-STEP-2-SCHEMA-DESIGN.md` остаются нормативными. При расхождении
> побеждают они.

## 1. Область

Этот шаг закрывает **только персистентность**: маппинг строк, интерфейсы
репозиториев, реализации репозиториев для PostgreSQL, Unit of Work, управление
скоупом и нормализацию ошибок.

**Намеренно не в этом шаге:** HTTP-эндпоинты, аутентификация, движок авторизации,
Billing, платежи, транспорт Platform -> Core, `TrustedExecutionRequest`,
разрешение кредилов, генерация или хэширование токенов API-ключей, изменения Core и
любой редизайн O-1 или O-7.

## 2. Слоистость

```
application services
  -> protocols            интерфейсы; вообще без знаний о базе данных
  -> UnitOfWork           граница транзакции, скоуп, набор репозиториев
  -> repositories         параметризованный SQL; без управления транзакциями
  -> Session              защита транзакции, нормализация ошибок
  -> PlatformDatabase     пул соединений, проверка безопасности роли
  -> PostgreSQL + RLS
```

Направление зависимостей одностороннее. `app/platform/models.py` не импортирует
ничего из этого пакета, протоколы этого пакета не импортируют драйвер, а драйвер
(`asyncpg`) импортируется только внутри `database.py`, лениво. Ничто вне
`app/platform/` не импортирует пакет персистентности.

**Домен не становится ORM.** Восемь записей остаются замороженными stdlib
dataclass'ами. `mapper.py` — единственный модуль, который знает и доменную запись, и
физическую колонку, и это то, что не даёт изменению схемы превратиться в изменение
модели.

## 3. Два логина, три скоупа

Схема определяет две роли базы данных. Деплой **обязан** обслуживать их двумя
разными логинами: если бы один логин был членом обеих, соединение, обслуживающее
запрос арендатора, могло бы войти в system-скоуп, и граница существовала бы только
в тексте политик. Поэтому `PlatformDatabase` принимает `application_role`,
переключается на неё при настройке пула и **отказывается создавать пул**, если
эффективная роль — суперпользователь, имеет `BYPASSRLS` или владеет таблицами
Platform: все три освобождают сессию от политик, которым она обязана подчиняться.

| Скоуп | Фабрика | Эффективная роль | Контекст | Что может |
| --- | --- | --- | --- | --- |
| tenant | `database.tenant(org, user_id=...)` | `forge_platform_app` | `forge.organization_id`, `forge.user_id` | всё tenant-owned: проекты, провайдерские аккаунты, API-ключи, запуски, потребление |
| pre-tenant | `database.pre_tenant(user_id=...)` | `forge_platform_system` + system scope | нет тенанта | чтение идентичности и аренды: пользователь, memberships, организации |
| server | `database.server()` | `forge_platform_app` | нет | bootstrap-записи для известного тенанта; не читает ничего |
| server (system) | `database.server(role=SYSTEM_ROLE, system_scope=True)` | `forge_platform_system` + system scope | нет | system-owned провайдерские кредилы; администрирование memberships |

`forge.system_scope` **не является механизмом авторизации.** Любая сессия может
установить пользовательскую GUC, поэтому доступ к system-скоупу даёт клауза `TO` в
политике вместе с ролью, которой управляет деплой. Флаг лишь дополнительно сужает
скоуп и держит system-owned строки невидимыми по умолчанию. Безопасные тесты
проверяют, что тенантный путь не получает ничего от его установки.

## 4. Дисциплина транзакций

* **Репозитории никогда не открывают, не коммитят и не откатывают транзакцию.** Этих
  слов нет в `repositories.py`. `Session` отказывает любому оператору вне
  транзакции, поэтому репозиторий, использованный вне Unit of Work, поднимает
  `TransactionError` вместо тихого автокоммита.
* **Жизненным циклом владеет Unit of Work.** Он коммитит при чистом выходе,
  откатывает при исключении и закрывает сессию в `finally`. `commit()` идемпотентен,
  поэтому явный `uow.rollback()` с последующим нормальным выходом — не ошибка.
* **Скоуп локален для транзакции.** Используется `set_config(name, value, true)`, а
  не литеральный `SET LOCAL`: `SET` не принимает плейсхолдер, и сборка запроса
  означала бы интерполяцию клиентского значения в SQL. Роль переключается через
  `SET LOCAL ROLE`, поэтому она тоже откатывается с концом транзакции.
* **Соединение чисто при возврате.** `Session.close()` завершает транзакцию, если она
  открыта, сбрасывает роль и помечает сессию закрытой. Поэтому соединение из пула не
  может перенести арендатора одного запроса в следующий; тесты многократно прогоняют
  арендатора A, затем B, на одном пуле.

Порядок внутри фабрики скоупа: сначала транзакция, затем роль, затем контекст.
Транзакция должна существовать до установки чего-либо транзакционно-локального,
иначе настройка не получила бы скоупа.

## 5. Интерфейсы репозиториев

Девять протоколов в `protocols.py`: по одному на репозиторий плюс репозиторий
system-owned провайдерских аккаунтов. Два правила закодированы в **сигнатурах**, а
не в документации:

1. **Tenant-scoped репозиторий никогда не принимает идентификатор организации.**
   Тенант приходит из Unit of Work. Нет параметра, чтобы передать чужого арендатора,
   и значит нет параметра, который можно забыть проверить. Методы, которым
   организация нужна структурно, принимают идентификатор *сущности*, и скоуп всё
   равно приходит из контекста.
2. **Нет generic `update(**fields)`.** Каждый изменяемый переход — именованный метод,
   поэтому неизменяемому полю неоткуда быть записанным.

## 6. Граница идентификаторов

Доменный контракт принимает любую непрозрачную непустую строку. Схема хранит `uuid`.
`mapper.as_uuid` — эта граница: он преобразует идентификатор или поднимает
`InvalidIdentifierError` с именем поля, **до** сборки любого SQL. Поэтому
некорректный идентификатор становится именованной ошибкой персистентности, а не
ошибкой приведения от драйвера, и он никогда не может выродиться в пустой контекст,
молча возвращающий ничего.

`core_run_id` и `task_id` — `text` в хранилище и **не** являются идентификаторами
этого рода: это непрозрачные корреляционные/текстовые значения, поэтому к ним не
применяется валидация — в соответствии с контрактом, который не делает их UUID.

## 7. Репозитории

| Репозиторий | Примечания |
| --- | --- |
| `users` | create, get, find_by_email (только извлечение, не аутентификация), статус, display name, `list_all` для server-скоупа |
| `organizations` | create, get, `list_for_user` (чтение discovery). **Без update:** ни один скоуп не имеет `UPDATE`, поэтому ни один метод его не объявляет |
| `memberships` (tenant) | create, get, поиск/список по пользователю, список для тенанта, статус, роль. Арендатор приходит из сессии и не является параметром |
| `system_memberships` | create, только для bootstrap: у первого membership нового арендатора нет контекста, из которого его можно вывести |
| `projects` | create, get, список, find_by_slug, имя, slug, workspace_ref, статус |
| `provider_accounts` (tenant) | create, get, список, find_by_provider_name, secret_ref, metadata, статус |
| `system_provider_accounts` | та же форма, ограниченная `organization_id IS NULL` |
| `api_keys` | create, get, список, список по создателю, revoke, expiry, record use |
| `run_records` | create_queued, get, поиск/список по core_run_id, список по task_id, claim, finish, set_status, increment_attempt_count |
| `usage_records` | **только append, get, список** |

## 8. Провайдерские аккаунты: два режима владения, два репозитория

У `ProviderAccount` ровно два режима, и достигаются они двумя разными репозиториями,
а не одним с nullable-фильтром:

* `provider_accounts` пишет тенанта из сессии. Каждый оператор ограничен текущим
  тенантом, и `organization_id IS NULL` никогда не используется как поиск.
* `system_provider_accounts` ограничен `organization_id IS NULL` и достижим только из
  server-скоупа с system-ролью, чья политика требует и null-организации, и явно
  объявленного system-скоупа.

Нет `get_by_organization(None)` и нет метода, который мог бы его принять. Тенант не
может видеть, создавать или повышать запись до system-owned; system-скоуп не может
видеть или присвоить запись арендатора.

## 9. Записи потребления: append-only по конструкции

У `UsageRecordRepository` нет метода update и нет метода delete, и в протоколе их
тоже нет. Так и задумано: привилегии таблицы не дают `UPDATE` и `DELETE`, и ни одна
политика их не выдаёт, поэтому попытка отвергается базой, даже если бы метод
существовал. Дубликат `(run_record_id, attempt_number)` — это **нарушение
уникальности**, а не молчаливая перезапись, и исправление — это новая запись.

Ни одно поле не несёт денег. Нет ни cost, ни price, ни charge, ни balance, ни
currency, и ни одно не может быть добавлено: это измерение, а не счёт.

## 10. Персистентность жизненного цикла запуска

`RunRecord.id` — идентичность Platform; `core_run_id` — корреляционная идентичность
Core. Это разные значения, схема не даёт им схлопнуться, и ни один метод не принимает
одно там, где нужно другое. `core_run_id` **не** уникален (K-8 откладывает семантику
retry/resume), поэтому `find_by_core_run_id` возвращает одну строку, а
`list_by_core_run_id` — все совпадения.

Queued-запуск не несёт ни `core_run_id`, ни `started_at`, ни инициатора, потому что
схема запрещает первые два, а queued — единственное состояние, в котором отсутствие
инициатора допустимо. `claim` устанавливает `core_run_id`, `started_at` и инициатора
вместе — это переход через границу Core, — и его предикат `status = 'queued'`
является частью `UPDATE`, поэтому два воркера, гоняющиеся за одним запуском, не
могут оба победить. `increment_attempt_count` — это `attempt_count = attempt_count +
1` одним оператором, поэтому конкурентные инкременты не могут потерять обновление.

Новое состояние жизненного цикла не вводится, и правило терминальности не
утверждается: какие статусы терминальны — это **O-7**, всё ещё открытый, и этот слой
ограничивает только то, что уже фиксируют доменный контракт и схема.

## 11. Конкурентность

Уникальность оставлена базе данных, никогда предпроверке:
`UNIQUE (organization_id, user_id)` для memberships, tenant-scoped частичный
уникальный индекс для slug проекта, оба частичных уникальных индекса для
провайдерских аккаунтов и `UNIQUE (run_record_id, attempt_number)` для измерений.
Проверка-затем-вставка всё равно проиграла бы гонку, поэтому авторитет —
ограничение, а проигравший писатель получает `UniqueViolationError` с именем
ограничения. Путь claim использует условный `UPDATE` по той же причине. Тесты
покрывают каждый из этих конфликтов.

## 12. Нормализация ошибок

`database.normalize_error` — единственное место, где переводится исключение
драйвера. Иерархия намеренно мала, и каждый её член существует потому, что
вызывающий должен иметь возможность действовать по-разному: `EntityNotFound`,
`InvalidIdentifierError`, `UniqueViolationError`, `ForeignKeyViolationError`,
`CheckViolationError`, `PermissionDeniedError`, `TransactionError`,
`ConnectionError` — все наследуют `PersistenceError`.

Два правила:

* **Отказ авторизации не выдаётся за отсутствие.** Отказ политики — это
  `PermissionDeniedError`; смешение с «не найдено» позволило бы отказу авторизации
  маскироваться под отсутствующую запись.
* **Полезная диагностика сохраняется.** SQLSTATE и имя нарушенного ограничения
  сохраняются, поэтому вызывающий может отреагировать на конкретную проигранную
  гонку, а оператор может прочитать исходное сообщение. Уже нормализованная ошибка
  проходит без изменений, поэтому обёртывание идемпотентно.

## 13. Стратегия тестов

`tests/test_platform_persistence.py` (107 тестов) и `tests/persistence_fixture.py`
работают против реального PostgreSQL. Фикстура создаёт **два логина, каждый член
ровно одной роли Platform**, потому что один логин в обеих позволил бы тестам обойти
границу, которую они должны доказать.

Покрытие: Unit of Work (транзакция, commit, rollback, явный rollback, контекст
до/после commit и rollback, отсутствие наследования между транзакциями,
непригодность репозиториев после выхода, все четыре скоупа, некорректные
идентификаторы, отказ от небезопасного пула); маппинг всех восьми сущностей,
nullable-семантика, круговой рейс таймстемпов, безопасность repr; изоляция
арендаторов (чтение, обновление, удаление, чужой пользователь, чужой проект, чужой
переход запуска, отказ без контекста); все документированные операции каждого
репозитория; переходы жизненного цикла; append-only потребление; pre-tenant
discovery; нормализация ошибок.

## 14. Два пробела в политиках, закрытые этим шагом

Реализация репозиториев нашла два реальных несоответствия в схеме Шага 2, оба одной
формы — **привилегия выдана, а политики к ней нет**. Ни одно не было живым tenant
escape, и оба зафиксированы, а не тихо исправлены:

1. System-роль имела `INSERT` на `users` без INSERT-политики, поэтому bootstrap-путь
   (организация + первый `OWNER` membership) фактически не мог выполниться.
2. Tenant-роль имела `UPDATE` на `users` без UPDATE-политики, поэтому обновление
   совпадало с нулём строк, и репозиторий сообщал об отсутствующей записи — тихий
   неверный ответ, худший из трёх возможных исходов.

Оба теперь имеют политики: `users_system_insert` для bootstrap и
`users_tenant_update`, ограниченная собственной строкой субъекта
(`id = platform.current_user_id()`), поэтому один субъект не может переписать запись
другого.

## 15. Адверсариальное ревью и его находки — русская версия

Перед коммитом было проведено независимое адверсариальное ревью этого слоя.
Оно нашло восемь дефектов, которые набор тестов выше пропустил, и два из них были
способами **обойти защиту роли пула** — ту самую проверку, которая существует именно
для того, чтобы развёртывание не работало с молча отключённой row-level security. Все восемь были
воспроизведены, исправлены и получили регрессионный тест; полная запись — в
`docs/DECISIONS.md`, `D-PLATFORM-20`.

Два серьёзных, кратко:

* **Логин-суперпользователь принимался.** Защита спрашивала `pg_has_role` и читала
  атрибуты уже *после* подстановки роли. Теперь атрибуты логина проверяются до любого
  переключения.
* **Один логин мог держать оба скоупа.** Тенантный пул теперь отказывается
  строиться на таком логине.

**Что ревью подтвердило как корректное:** каждый tenant-scoped оператор ограничен
арендатором сессии; репозиторий system-owned провайдерских аккаунтов недостижим из
tenant-сессии в любой операции; SQL-инъекций нет; контекст арендатора не утекает между
соединениями пула; нормализация ошибок идемпотентна; слой маппинга соответствует
каждой доменной записи поле за полем; и ничто вне `app/platform` не импортирует пакет
персистентности или драйвер.

## 15.1 Второе ревью: проход усиления — русская версия

**Второе** адверсариальное ревью сообщило о шести новых находках — одна HIGH и пять
MEDIUM — и все шесть исправлены с регрессионными тестами. Полная запись —
`D-PLATFORM-21`.

**Корректность commit (H-1).** PostgreSQL принимает `COMMIT` на aborted-транзакции,
**откатывает** её и возвращает строку `ROLLBACK` без ошибки. Теперь сессия помнит
aborted-состояние, проверяет строку статуса, откатывает и поднимает `TransactionError`.

**Discovery (M-2).** Политики больше не дают system-скоупу чтение всех
идентичностей, membership'ов и арендаторов. Discovery привязан к **одному субъекту на
транзакцию** через `Session.require_subject`, и `users.list_all` удалён. Привязка живёт в сессии, а
не в политике, и **этот выбор сделан после измерения альтернативы**:
предикат субъекта в политике делает `INSERT ... RETURNING` невыполнимым.

**Запись идентичностей (M-3).** Скоупы имеют **непересекающиеся** права
записи на `users`: tenant — `display_name`, system — `status` и `email`.

**Жизненный цикл (M-4) и привязка арендатора (M-5).** `finish` и `set_status` —
compare-and-set внутри `UPDATE`, поднимают `ConcurrentModificationError`; **O-7 остаётся открытым**.
Tenant-scoped запись с чужим `organization_id` отклоняется, а не перенаправляется.

## 16. Открытые решения

Этим шагом не изменены. O-1 (формат ключа и модель authority, которую он
обусловливает), O-2 (аутентификация), O-3 (бэкенд секретов), O-4 (грамматика
`secret_ref`), O-5 (топология workspace), O-6 (транспорт Platform -> Core) и O-7
(enum статусов `RunRecord`) остаются **открытыми**. O-8 остаётся решённым на уровне
контракта. O-9 остаётся решённым и реализованным. O-10 … O-13 остаются отложенными.
Ни одно новое решение не было выдумано ради удобства реализации.

---

## STATUS

```
Stage 0.1  =  FROZEN / GO

Stage 1    =  ARCHITECTURE CONTRACT READY
Step 1     =  DOMAIN CONTRACTS READY
Step 2     =  POSTGRESQL SCHEMA AND RLS IMPLEMENTED
Step 3     =  PERSISTENCE, REPOSITORIES, UNIT OF WORK IMPLEMENTED

Implementation status:  SCHEMA IMPLEMENTED, DATABASE CONTRACT TESTS PASSING
```

## OPEN DECISIONS

**Открыты:** O-1 формат API key и модель authority; O-2 механизм
сессий/аутентификации; O-3 бэкенд секретов/KMS; O-4 грамматика `secret_ref`; O-5
топология workspace; O-6 транспорт Platform -> Core; O-7 enum статусов `RunRecord`.

**Решено для контракта:** O-8 — `UNIQUE (run_record_id, attempt_number)`.

**Решено как дизайн-решение:** O-9 — дизайн RLS, реализован.

**Отложено:** O-10 источник провайдерских цен; O-11 платёжный процессор; O-12
налоги/инвойсы; O-13 экономика reseller/partner; все Billing- и отложенные сущности.
