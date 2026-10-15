# Stage C — owner decisions and the nullable usage contract

Appended after the owner's decisions. Starting HEAD for this part: `c554edb`.

## Decision 1 — historical records stay in their legacy stores

**No automatic migration was performed, and none will be without a separate instruction.** This is
now the documented state:

| Store | Location | Contents | Compatibility |
|---|---|---|---|
| Workspace-scoped run store | `FORGE_RUN_STORE_ROOT` (the launcher sets `<repo>/.forge/run-store`; the default is `%LOCALAPPDATA%\ForgeAI\runs\projects\ws-<hash>`) | runs written by this build | the reader resolves exactly this root |
| Legacy profile store | `%LOCALAPPDATA%\ForgeAI\runs\run-api-*` (2 604 directories observed) | runs written before the root was made explicit, plus test and workflow runs | **untouched**; not reachable through the workspace-scoped reader, so no cross-workspace visibility is created |

**Future safe migration scenario** (to be executed only on request): a *copy*, never a move, of one
project's records at a time, into that project's workspace-scoped root; the copy is verified by
reading each record back through the API before the source is left as it is; any record whose id
already exists in the target is reported and skipped rather than overwritten. Nothing in this stage
does any of that.

## Decision 2 — `tokens` and `cost` may be `null`, and zero must be proven

The contract now says, in `app/api/models.py`:

* `tokens: Optional[int] = None` and `cost: Optional[float] = None`;
* `null` means **unknown**; a number appears only when evidence supports it;
* a **proven zero** stays `0` / `0.0`, and the dispatch basis says which kind of value it is
  (`X-Forge-Usage-Basis`: `MOCK`, `ACTUAL`, `ESTIMATED`, `UNKNOWN`).

Every site that produced a zeroed telemetry was classified by reading the code around it, not by
replacing all of them:

| Site | Was | Now | Why |
|---|---|---|---|
| idempotency verdict (`DENIED`/`REPLAYED`) | `0` / `0.0` | `0` / `0.0` | no execution happened; the existing comment already said the zero is real |
| declared verification | `0` / `0.0` | `0` / `0.0` | **proven**: three tests measure that this path makes no provider call (`test_the_declared_verification_makes_no_provider_call`, `…_entry_point_makes_no_provider_call`, `…_continues_a_real_project_with_no_model`) |
| agent loop | `0` / `0.0` | `null` | it executes; nothing proves no provider was reached, so zero would have been an invention |
| accepted task | `0` / `0.0` | `null` | same |
| replay of a persisted record | `… or 0` | `null` when the field is absent | an absent field is unknown; a **present** zero is preserved |
| live dispatch | always a number | `null` unless the basis is backed by evidence | silence becomes unknown, a mock or a provider report keeps its numbers |

**My first classification was wrong and the suite said so.** I had changed the declared-verification
path to `null` as well; three tests failed, and they were right: that path performs no provider call,
so zero is the measurement. The path was restored with a comment naming those tests, and the
dispatch test asserts zero again.

Consumers updated with the contract:

* `app/desktop/views.py` formatted `{cost:.4f}`, which would raise on `None`; it renders "unknown";
* `ui/src/screens/Runs.tsx` renders an em dash instead of `0.0000` for an unknown value, with the
  RU/EN key `runs.unknown`.

**A concurrency defect was found by the new tests while doing this.** The idempotency ledger writes
its record with `O_EXCL` and parses it on the losing path, so a racing claim could read the file
mid-write and the ledger (deliberately fail-closed) raised "not valid JSON"; my layer then answered
503. A momentary empty record is an expected race, not an unavailable ledger, so the claim now
retries briefly, while a genuinely unusable ledger (a denied directory) still fails closed at once.
Verified by ten consecutive focused runs and three module runs.

## Decision 3 — per-user idempotency scope is deferred

The scope remains `<project_id>:<key>` plus the request fingerprint. There is no user identity in
this API, no parallel user system was built, and none will be: the scope becomes the authenticated
identity when authentication arrives. Until then the limitation is explicit: two clients that share
a project and a key share its protection, and a key cannot be attributed to a person.

## Verification for this part

| Check | Result |
|---|---|
| New tests (`tests/test_unknown_usage.py`) | `Ran 6 tests … OK` |
| Idempotency module | `Ran 10 tests … OK`, three consecutive runs |
| Declared verification | `Ran 7 tests … OK` |
| Project factory vertical slice (the three measured no-call tests) | `Ran 42 tests … OK` |
| Full suite | `Ran 2456 tests … OK (skipped=36)` — one earlier run hit an unrelated flake in `test_step_graph_managed_execution`, which passes 3/3 in isolation and is outside this change set |
