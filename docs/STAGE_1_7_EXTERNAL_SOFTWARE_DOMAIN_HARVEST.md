# Forge AI — Stage 1.7 External Software and Domain Architecture Harvest

**Status:** CONCEPTUAL RESEARCH. Findings below are classified observations or candidates, not approved Forge requirements. **Research checked:** 2026-10-03. No external repository or binary was cloned, installed, or executed.

## Executive summary

The strongest reusable result is a **domain-independent Core with explicit extension boundaries**. Business platforms repeatedly separate operational documents from ledgers, distinguish physical stock movements from reconciliation, and keep domain modules connected through documented transaction/workflow boundaries. Hospitality, fiscal/POS, browser automation and agent tooling each have their own lifecycle and failure semantics; they should be modeled as optional Domain Patterns, Skills, Capabilities or Integrations selected only after project Discovery.

Forge already has provider routing, a deterministic template Planner, bounded primary/reviewer/revision execution, and target documentation for Discovery, capabilities, memory, governance, and approval. It does not implement general project tools, Domain Pattern Library, integrations, durable domain workflows, or a domain-extension registry. Prior documentation says Mastery Creator is optional and KeyCore-Hub is unresolved. The contents/behavior of Axis, KonnexMastery, FlashCast, Primorie-related systems and Mastery Creator were not available as verifiable project evidence in the current repository; no internal details are inferred.

**Recommended principle:** Observe → compare independent sources → generalize only repeated mechanisms → classify by layer → evaluate applicability → preserve as candidate knowledge/pattern → obtain owner decision before adoption. Popularity, one project, or an external product's feature list does not make a Forge requirement.

## EXTERNAL SOFTWARE FINDINGS

### Selected open-source projects reviewed

These were reviewed through public repositories/documentation only. Maturity is an evidence-based qualitative observation, not a security or quality certification. Licenses are reported as stated by project sources; this is not legal advice and does not replace checking the exact files/modules/version intended for reuse.

| Project / repository | License | Apparent maturity | Relevant modules/mechanisms | Learn / do not copy |
| --- | --- | --- | --- | --- |
| [ERPNext](https://github.com/frappe/erpnext) | Repository identifies GPL-3.0; verify the exact release and included apps before reuse. | Established, broad ERP with extensive public documentation and a substantial active repository. | Accounts, selling/buying, stock ledger, purchase receipt, stock movement/reconciliation, POS, CRM, manufacturing, workflows and reports. | Learn modular business documents, linked ledgers, permissions and correction trails. Do not reproduce its framework/data model wholesale or assume ERP scope is wanted. |
| [QloApps](https://github.com/Qloapps/QloApps) | Upstream says Core is OSL-3.0; modules may carry their own licenses, and other modules use AFL-3.0. Check each module. | Large, long-running hotel/booking project with active source and a visible issue/release surface; its exact maintenance/support guarantees are unknown. | PMS, booking engine, public hotel website, front-desk/central reservation concerns, module architecture. | Learn separation of hotel operations and booking channels. Do not assume all modules have one license, that its channel coverage fits a project, or that copied design/code is acceptable. |
| [HotelDruid](https://www.hoteldruid.com/en/) and [GitHub source listing](https://github.com/digital-druid/hoteldruid) | Official site says AGPL; it also describes proprietary hosted add-on modules. Inspect current upstream distribution and terms. | Long-lived, narrower PMS (official site lists v3.0.8 released 2025-12-04); the linked GitHub listing carries a `NO_SOURCE_CODE_UPDATE` marker, so it is not treated as a reliable current source mirror. | Room/rate calendars, room assignment, user privileges, receipts/invoices, POS and occupancy/revenue statistics. | Learn that a small PMS may combine reservations and limited operational tools while channel/booking features are separate. Do not infer source maturity from age or mirror activity. |
| [Playwright](https://github.com/microsoft/playwright) | Playwright package metadata states Apache-2.0. | Mature, actively maintained browser automation/test framework with broad docs and multi-browser support. | Browser, isolated contexts, cookies/storage state, pages, automation and test isolation. | Learn context isolation and explicit state lifecycle. Do not treat browser contexts as a complete multi-account governance/security product or assume fingerprint manipulation is appropriate. |

License/status caveats: project README metadata is a starting point, not a grant to copy code or assets into Forge. QloApps has per-module licensing; hosted add-ons can be proprietary. ERPNext/Frappe and Odoo ecosystem modules can have different licenses. For any future reuse, inspect the specific version, dependency licenses, generated assets and distribution model. No source code was copied.

### Patterns observed and their scope

1. **Operational document → derived ledger entries → report/reconciliation:** business actions are recorded through domain documents; ledger entries provide accounting/stock history; reports trace back to source documents. This recurs in ERPNext accounting and inventory documentation. **Candidate universal mechanism:** provenance and traceable state transitions. **Not universal:** double-entry accounting is a domain/accounting capability, not a required model for every Forge-generated project.
2. **Corrections preserve history:** ERPNext documents cancellation/reversal and amendment rather than silently editing finalized ledger rows. This is a useful auditability pattern for financial/inventory systems. **Candidate cross-domain principle:** corrections should preserve provenance where the domain requires an audit trail; exact immutability policy is domain-specific.
3. **Availability is not one quantity:** hospitality sells room capacity over dates and rate/occupancy conditions; warehouses track item quantities by location/valuation; restaurant stock consumption follows recipes/modifiers and operational events. These share the need for a ledger/reservation concept but differ in constraints. Do not merge into a generic “inventory” abstraction without requirements.
4. **Online/offline fiscal operation has a protocol lifecycle:** official Ukrainian PRRO materials describe shift-open/check/report/close operations, test endpoints, offline receipt sequencing and later synchronization. A fiscal adapter must expose server-confirmed state and unsent/uncertain local state; it cannot be treated as a generic payment API.
5. **Payment and fiscalization are separate authorities:** the customer payment outcome, POS order, acquiring provider callback, and fiscal receipt are distinct records/events. Reconciliation correlates them; one must not be inferred solely from another.
6. **Browser profiles are state containers with explicit isolation:** Playwright BrowserContexts isolate cookies/storage and can be created independently within one browser. Persistent contexts, exported storage and real account credentials create a separate data/permission lifecycle. A test-isolation primitive is not a production account-farm architecture.
7. **Domain products are compositions, not universal templates:** QloApps/HotelDruid expose different PMS/booking scopes; ERP suites combine modules; optional channel manager, fiscalization, POS or kitchen functions remain applicability questions.
8. **Agent systems need layered integration:** Stage 1.5/1.6 already distinguish Agent, Skill, Capability, Tool, Knowledge, Memory, permission and runtime. External tool protocols (including MCP) can implement an integration boundary but do not supply Forge authorization or domain semantics.

## HOSPITALITY FINDINGS

QloApps presents a hotel PMS with booking engine and hotel website; HotelDruid documents rooms, periods, rates, room assignment rules, group bookings, user privileges and occupancy/revenue statistics. These are vendor/project descriptions, not proof that one canonical hospitality model fits all businesses.

**Candidate Domain Pattern:** `Property → Unit/RoomType → Availability by date → Rate/Restriction → Reservation → Guest/Party → Stay/Check-in/out → Folio/Charges → Payment/Settlement → Housekeeping/Room status → Audit/Reports`. Channel and booking-engine interactions are integration patterns. Restaurant, warehouse, POS, housekeeping, night audit, payments, fiscalization and channel management are individually conditional. A resort may need some, all, or none of them.

**Harvest:** maintain separate reservation intent, confirmed inventory allocation, actual stay, charge/folio and payment state. Model date/time zones, room assignment changes, overbooking policy, cancellations/no-shows, group reservations, deposits and reconciliation only when Discovery establishes them. Do not hard-code a hotel’s operations into Forge Core.

## RESTAURANT / CAFE FINDINGS

Odoo's restaurant/POS documentation describes tables/floors, orders, sending items to kitchen/bar printers, and fiscal-position/tax configuration. ERP platform POS systems expose sales and stock/accounting integrations. These vendor patterns suggest a workflow boundary:

```text
Menu / item + modifiers
 -> Order / table / service mode
 -> kitchen ticket(s) and preparation state
 -> served items / corrections / voids
 -> tender(s), split/partial payment and reconciliation
 -> receipt/fiscal adapter where required
 -> sales, stock consumption and reporting
```

**Domain-specific candidates:** KDS or printer routing, courses, modifiers, recipe/BOM consumption, table transfers, split checks, tips, shift/cash drawer and offline operation. They are not universal, and payment-terminal/acquiring operation is not itself fiscalization. A restaurant may use no table service or kitchen screen.

## WAREHOUSE / INVENTORY FINDINGS

ERPNext documentation separates Purchase Receipt, Stock Entry, Stock Reconciliation and ledger/reporting. A Purchase Receipt records goods accepted from a supplier; Stock Entry records issue, receipt, transfer, production or movement; Stock Reconciliation compares counted stock against book quantities; serial/batch and warehouse location constrain individual movements. Perpetual valuation can link stock value to accounting.

**Candidate pattern:** `Item/SKU + UOM + Location + Lot/Serial + Movement event + Quantity/value + Source document + Actor/time`. Receiving, put-away, transfer, reservation, consumption, returns, cycle count, adjustment, reorder and valuation are distinct procedures. Separate physical count from book balance and preserve who/why/source for adjustments. FIFO/average valuation and negative-stock rules have accounting consequences and are optional domain policy, not Core defaults.

## ACCOUNTING / FINANCE FINDINGS

ERPNext describes source documents (sales/purchase invoices, payments, stock movements) generating balanced General Ledger entries; receivable/payable/payment ledgers link parties and settlements. Reconciliation aligns external bank/payment records with the ledger. Cancellation/return/reversal retains the original transaction trail in an immutable-ledger approach.

**Candidate domain boundary:** operational events are not themselves the ledger; posted financial entries should be traceable to source documents, accounting period, currency, company, tax and approval. Payment initiation, payment confirmation, invoice settlement and refund are separate facts. **Domain-specific:** double-entry ledger, chart of accounts, tax rules, fiscal close, accrual and accounting-period controls. Forge should not impose this on non-financial generated projects.

## UKRAINE FISCAL / POS FINDINGS

The legal/technical distinction below is deliberate. This is architecture research, not legal advice or a conclusion about a merchant's obligations.

| Evidence type | Finding relevant to architecture | Source / caveat |
| --- | --- | --- |
| **Legal/regulatory text** | The Ukrainian Law №265/95-ВР governs use of settlement transaction registrars in trade, public catering and services; current applicability depends on the current law, subordinate rules, business/transaction facts and effective dates. | [Verkhovna Rada law card](https://zakon.rada.gov.ua/laws/card/265/95-%D0%B2%D1%80) shows current status and revision history. Recheck the text in force at implementation time. |
| **Official technical documentation — ДПС** | The Electronic Cabinet documents fiscal-server PRRO API operations for receipts and Z-reports. The test API explicitly states that test receipts/Z-reports are non-fiscal. Shift opening may create a service/zero receipt; offline and online queues differ. | [ДПС Electronic Cabinet API](https://cabinet.tax.gov.ua/help/api.html). API details and versions can change. |
| **Official PRRO guidance** | Offline mode uses a reserved fiscal-number range and local ordered records, followed by synchronization; it has time limits and required transitions/reports under the applicable rules. | [ДПС PRRO FAQs](https://tax.gov.ua/baneryi/programni-rro/aktualni-zapitannya-vidpovidi/) and [offline guidance](https://dn.tax.gov.ua/media-ark/local-news/470781.html). Some FAQ pages contain dated answers; verify current rule/order before relying on a limit. |
| **Vendor documentation** | Checkbox documents a REST API and separates its merchant dashboard, transaction processing, signing agents and frontend agents. | [Checkbox API guide](https://wiki.checkbox.ua/uk/api/api_eng). Product contract, version and legal suitability must be checked separately. |
| **Official regulatory/e-document concept** | Electronic documents, required attributes, signatures and interchange have their own legal and technical layer. | [Law №851-IV](https://zakon.rada.gov.ua/laws/show/%D0%B5-%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%BE%D0%BE%D0%B1%D1%96%D0%B3); confirm current amendments and trusted-service requirements for a concrete flow. |

**Candidate integration boundaries:**

- **POS/order domain** owns basket, item/tax classifications as configured, tenders and returns as business events.
- **Payment/acquiring integration** owns authorization/capture/refund outcomes and callbacks; reconcile duplicate/out-of-order notifications idempotently.
- **Fiscalization integration** owns cashier/PRRO registration context, shift state, document payload/signature requirements, fiscal number/receipt, offline allowance, queued submission and server acknowledgement.
- **Accounting/e-document integration** owns posting/export, exchange status, signatures/receipts and reconciliation.

These adapters should be replaceable and retain request/response IDs and verified outcomes while redacting credentials and personal data. Do not implement a “universal fiscal receipt” based only on a foreign POS product. Do not infer Ukrainian legal compliance from a provider API or vendor marketing. Exact obligations, limits, required receipt fields, returns/cash operations and accepted signature processes remain **UNKNOWN / project-specific legal review required**.

## MULTI-ACCOUNT / BROWSER FINDINGS

Playwright BrowserContext provides isolated browser sessions with independent cookies and storage and can support multi-user scenarios within a browser process. This is strong evidence for **state isolation**, not for all multi-account automation requirements.

**Candidate reusable model:** `Account Identity → Profile/State Container → Credential Reference → Proxy/Network Policy (conditional) → Wallet/External Identity (conditional) → Task Assignment → Worker/Lease → Run State → Rate/Concurrency Limits → Outcome/Statistics → Reconciliation/Review`.

- Account lifecycle and browser-profile lifecycle should be separate state machines. Account identity is not just a browser directory.
- Persistent storage/import-export is sensitive; encrypt and scope it, record provenance, and support revocation/deletion. Never log cookies/session tokens.
- Workers need leases, isolation, bounded concurrency, cancellation and failure containment; a failed account job must not corrupt other profiles.
- Scheduling/retries need idempotency or state inspection before replay; rate limits and external service rules matter.
- Fingerprint manipulation, proxies, wallets, Discord/Twitter or multi-account behavior are **CONDITIONAL / DECISION REQUIRED**, not universal requirements. They can carry privacy, account-abuse, fraud, security and terms-of-service risks; no circumvention behavior is recommended here.

No assumptions are made about Mastery Creator's implementation, obfuscated code, secret/configuration contents or unobserved behaviors. Only externally documented patterns and prior project-level boundary (“optional; details unknown”) are used.

## ERP / BUSINESS PLATFORM FINDINGS

ERPNext and Odoo illustrate a modular suite spanning finance, inventory, purchasing/sales, POS, CRM and reporting. Reusable patterns are:

- domain-owned source documents connected to ledgers/reports by stable references;
- role/permission and workflow controls close to business actions;
- explicit module/localization boundaries rather than one global tax rule;
- extension points for country and business-specific policy;
- auditable correction/amendment and reconciliation procedures.

Risks: ERP breadth creates coupling and complex setup; module/license compatibility is not uniform; migration/backdating can affect derived ledger values; configuration is not proof of legal compliance. Forge should learn the boundaries and workflows, not generate an ERP by default.

## AGENTIC SOFTWARE FINDINGS

Stage 1.5/1.6 already cover much of the general agent architecture: Agent/Skill/Capability/Knowledge/Tool/Project Memory are distinct; use is permission-scoped; skills are discovered progressively; durable Runs, sandbox, event trace and evaluation remain proposals. External software/agent integrations add these candidate concerns:

- version and provenance of an external Skill/tool server/connector;
- inspect scripts, dependencies, license, permissions and network requests before trust;
- a connector adapter must preserve Forge’s authorization and audit boundary;
- fetched project/domain content is untrusted evidence, not policy;
- evaluation should include tool failures, duplicate events, permission revocation and recovery, not only text quality.

MCP is a possible interoperability protocol, not the Capability model or permission system. Forge should be able to use native adapters or other protocols without changing its domain model.

## MASTER CREATOR / EXISTING PROJECT COMPARISON

| Project evidence | Observed | Inferred | Universalized candidate | Status |
| --- | --- | --- | --- | --- |
| Axis | No source/design evidence for this task was found in the inspected Forge docs. | None. | None. | **UNKNOWN** |
| KonnexMastery | No source/design evidence for this task was found in the inspected Forge docs. | None. | None. | **UNKNOWN** |
| FlashCast | No source/design evidence for this task was found in the inspected Forge docs. | None. | None. | **UNKNOWN** |
| Primorie-related software | No inspectable project material was identified in the Forge docs reviewed. | None. | None. | **UNKNOWN** |
| Mastery Creator | Earlier Forge docs describe it as optional and its architecture/integration contract as unknown; no source or runtime was inspected here. | It may be a candidate external/conditional capability if a concrete project needs it. | No account, fingerprint, proxy, wallet, browser, or social-platform feature can be treated as universal from current evidence. | **OPTIONAL / UNKNOWN** |
| KeyCore-Hub | Earlier Forge docs preserve its architecture and relationship to Forge as unknown. | It may become a project-specific integration after owner requirements and access boundaries are known. | None. | **UNKNOWN / DECISION REQUIRED** |

No credentials, private project files, sessions, wallets, cookies, or source internals were examined or reproduced. Lack of local evidence is not evidence that a project lacks a mechanism.

## REPEATED UNIVERSAL PATTERNS

Patterns supported across independent system categories, generalized carefully:

1. **Explicit state transitions and provenance** for operationally important records and long-running jobs. (ERP ledgers, PRRO shifts/receipts, browser/agent Runs.)
2. **Separate intent, execution, acknowledgement and reconciliation.** A reservation is not a stay; order is not payment; payment is not a fiscal receipt; tool request is not verified success.
3. **Idempotent event processing and duplicate/out-of-order handling** at external boundaries; inspect authoritative state before replay when effects may have occurred.
4. **Role/permission separation and least privilege**, with distinct operators (cashier/accountant/housekeeper/admin; agent/worker) and scoped actions.
5. **Replaceable integrations/adapters** around external authorities and changing protocols, with correlation IDs, versioning and error states.
6. **Correction without silent history erasure** where law/domain/audit requires traceability; reversibility must not be promised falsely.
7. **Isolation of mutable state** (workspace, profile, inventory location, fiscal shift, project/run) and bounded concurrency.
8. **Reconciliation and evidence-based completion**, not simply trusting that a command/model reported success.

“Repeated” here means observed across multiple independent examples/categories, not a claim that every system uses the same implementation or that every generated project must include these mechanisms.

## ARCHITECTURAL COMPARISON MATRIX

`Repeated?` indicates independent examples reviewed in this harvest, not universal adoption. “Forge Today” describes current documented scope, not a commitment.

| Pattern | Source(s) | Repeated? | Forge Today | Gap | Classification | Proposed Status |
| --- | --- | ---: | --- | --- | --- | --- |
| Domain discovery candidates with explicit applicability | Stage 1.3–1.5 target docs; ERP/hospitality product boundaries | Yes, as product practice; not one shared taxonomy | Discovery is target-only | No reviewed domain index/candidate flow | Knowledge / Domain Pattern | **PROPOSED** |
| Business source document linked to accounting/stock ledger | ERPNext accounting + stock docs | Yes within ERP modules | No business ledger | Project-specific financial/stock model | Domain Pattern / Generated Project | **OPTIONAL** |
| Append-only correction/reversal for audited postings | ERPNext immutable ledger; Ukrainian fiscal correction lifecycle | Repeated in regulated/audited flows | General Git safety only | No domain-event model | Domain Pattern / Integration | **OPTIONAL**; apply where required |
| Date-scoped room availability and reservation-to-stay lifecycle | QloApps; HotelDruid | Yes across hotel systems | No hotel capability | No hospitality model | Domain Pattern | **OPTIONAL** |
| Channel manager / booking engine boundary | QloApps; HotelDruid add-on descriptions | Repeated, but deployment/scope varies | None | No channel connector contract | Integration | **OPTIONAL / FUTURE** |
| Restaurant order → kitchen ticket workflow | Odoo Restaurant POS | Evidence from one selected documented suite | None | No restaurant workflow | Domain Pattern / Capability | **OPTIONAL** |
| Recipe/modifier stock consumption | ERPNext manufacturing/stock; restaurant domain inference | Partial; not independently validated across restaurant OSS here | None | No recipe-consumption model | Domain Pattern | **UNKNOWN / candidate** |
| Warehouse stock movement and reconciliation | ERPNext Stock Entry/Reconciliation | Repeated in ERP stock concepts | None | No inventory capability | Domain Pattern / Capability | **OPTIONAL** |
| Item/lot/serial and location scoped stock | ERPNext stock docs | Recurs in inventory systems, one primary repo reviewed | None | No stock identity model | Domain Pattern | **OPTIONAL** |
| Accounting source event → balanced ledger posting | ERPNext accounting docs | Repeated in accounting systems; one primary repo reviewed | No accounting domain | No accounting integration/model | Domain Pattern / Integration | **OPTIONAL** |
| Reconciliation between external settlement and books | ERPNext; payment/fiscal system boundaries | Yes conceptually across finance integrations | None | No reconciliation workflow | Integration / Domain Pattern | **OPTIONAL** |
| PRRO shift, receipt, Z-report and offline queue | ДПС official API/rules; Checkbox vendor API | Multiple independent official/vendor sources; protocols differ | None | No Ukrainian fiscal adapter | Integration / Domain Pattern | **DECISION REQUIRED** per project |
| Payment/acquiring distinct from fiscal receipt | ДПС/Checkbox docs; POS flow | Yes across official/vendor boundary material | Provider layer only for AI models | No business integration boundary | Integration | **PROPOSED** |
| Browser context isolates cookies/storage | Playwright docs | Direct repeated contexts within browser product; broader multi-account claim not established | No browser capability | No profile/credential model | Capability / Runtime | **OPTIONAL** |
| Persistent account profile and credential lifecycle | Browser ecosystem/security implication; source-specific | Weak / not universal in selected primary sources | None | No approved account-state design | Runtime / Security | **UNKNOWN / DECISION REQUIRED** |
| ERP modules separated by domain/localization | ERPNext; Odoo docs | Yes | Forge Core/provider separation only | No domain extension architecture | Core / Domain Pattern | **PROPOSED** |
| Agent/Skill/Tool and permission separation | Stage 1.5–1.6; agent product docs | Yes across sources already benchmarked | Conceptual in docs; partial multi-agent implementation | No general runtime/tools | Core / Runtime / Skill | **PROPOSED** |
| Source/license/dependency review before reuse | Upstream license files, mixed QloApps licenses | Yes as supply-chain requirement | Git safety rules, no import pipeline | No external software intake controls | Core / Knowledge / Security | **MUST HAVE before import** |
| Idempotency and duplicate/out-of-order external events | Payment/PRRO/worker architectures | Repeated design need; exact source contracts vary | Provider fallback is bounded; no business event handling | No external-event contract | Runtime / Integration | **MUST HAVE before side-effecting integrations** |
| Domain-specific acceptance/evaluation cases | Stage 1.5 eval proposal; ERP/POS protocols | Pattern repeated in engineering practice; not yet implemented | Unit/smoke tests for Forge itself | No generated-project/domain eval catalog | Knowledge / Evaluation | **SHOULD HAVE** |
| Domain template must not impose hidden requirements | Stage 1.3–1.6 governance; observed product variability | Repeated requirement across Forge stages | Deterministic generic planning templates | No domain Pattern Library policy | Core / Discovery | **MUST HAVE** |

This matrix has limited evidence for hospitality/restaurant patterns from open repositories: only selected projects were examined; repeated status is deliberately conservative. No mechanism is elevated to universal based on one source.

## DOMAIN-SPECIFIC PATTERNS

| Domain | Candidate pattern | Classification | Applicability questions |
| --- | --- | --- | --- |
| Hospitality | room/date availability, reservation-to-stay conversion, guest folio, housekeeping state, rate restrictions, night close | Domain Pattern | Are there rooms/units, stays, channel bookings, housekeeping or night audit? |
| Restaurant | table/order/course/modifier, kitchen ticket/KDS, recipe consumption, split tenders, shift close | Domain Pattern / Capability | Table service? Kitchen routing? Recipe stock? Offline POS? |
| Warehouse | SKU/UOM, locations, receiving, movement ledger, serial/batch, count/reconciliation, valuation/reorder | Domain Pattern | Track quantity only, or lot/serial/cost/expiry/reservation? |
| Accounting | source documents, double-entry posting, receivables/payables, reconciliation, period controls, reversals | Domain Pattern / Integration | Is accounting in scope or handled by another system/accountant? |
| Ukraine fiscal/POS | fiscal document adapter, PRRO shift/session, offline queue/number range, Z-report, return, server acknowledgement | Integration / Domain Pattern | Which entity, operation, provider, POS and current rules apply? Legal verification required. |
| Browser/multi-account | identity, isolated profile/session, worker lease, credential reference, schedule/limits and outcomes | Capability / Domain Pattern | Are multiple accounts permitted and required? What service policies and data risks apply? |
| ERP/business | modular domains, workflow permissions, source docs and ledgers, localizations, extension points | Knowledge / Domain Pattern | Which domains and system of record are actually needed? |
| Agentic system | role, skill, tool, run, checkpoint, approval, evaluation, integration adapter | Core / Runtime / Capability | Is automation needed, and at what autonomy and permission level? |

## NEW FORGE GAPS

- A **Domain Pattern Library** with applicability and non-applicability, confidence/provenance, source links, counterexamples and review status.
- A Discovery step that asks a small set of domain questions, produces candidate concerns, and asks before selecting domain capabilities.
- Stable, provider-neutral external Integration/Adapter contract distinct from Capability and Tool.
- Domain-level lifecycle/idempotency/reconciliation contracts before external side effects.
- A domain-specific evaluation catalog (e.g., reservation boundary cases, stock movement invariants, duplicate payment webhooks, offline fiscal queue recovery).
- License/dependency/provenance review for imported project code, Skills, tools and generated dependencies.
- A boundary that distinguishes a pattern library/knowledge record from an installable module, Skill, generated template or required project feature.

These are target gaps, not implemented features.

## PROPOSED ADDITIONS

1. **Domain Pattern Library (PROPOSED):** indexed candidate patterns for Hospitality, Restaurant/Cafe, Warehouse, Accounting, Fiscal/POS, E-commerce, CRM, Automation, Browser/Multi-account, Data, Integration/API and Agentic systems. Each entry contains problem, sources, repeated evidence, assumptions, applicability, non-applicability, failure modes, layer classification, evaluation examples, license/provenance where applicable, version and review status.
2. **Domain discovery flow (PROPOSED):** goal → candidate domain(s) → targeted questions → evidence/constraints → capability candidates → applicability/compatibility/security/legal review as relevant → owner decision when material → project-specific model. Domain recognition must not auto-select a feature.
3. **Integration boundary (PROPOSED):** provider-neutral adapter contract for external system identity/version, supported operations, authentication references, idempotency/correlation, lifecycle states, errors, retries, reconciliation, data classification, audit and capability/permission declarations.
4. **Candidate harvesting pipeline (PROPOSED):**

```text
Discover -> collect references -> provenance -> license check
 -> quarantine/static inspection/dependency and secret scan
 -> permission/network analysis -> architecture extraction
 -> generalize -> evaluation/counterexamples
 -> Candidate Knowledge / Skill / Capability / Domain Pattern
 -> owner review -> approved registry
```

   Do not execute unknown software as part of documentation research. A future implementation would need an isolated, disposable sandbox and approved scope before dynamic analysis.
5. **Domain evaluation cases (PROPOSED):** test boundaries and failures, including duplicates, late events, offline-to-online transitions, cancellation/returns, concurrency and reconciliation—not just happy-path screens.
6. Keep Ukraine fiscal/accounting as **replaceable local integrations** with authoritative rule/version references and an explicit date of verification; require qualified legal/accounting review for concrete use.

## SECURITY FINDINGS

- Treat source repositories, packages, Skills, MCP servers, APIs, browser content and generated code as untrusted until provenance, license, permissions and behavior are reviewed.
- Never run suspicious code/binaries on a normal development machine. Use quarantine, static inspection, dependency/SBOM visibility, secret scanning, permission/network analysis and isolated behavioral evaluation when later authorized.
- Browser cookies, storage-state files, session tokens, wallet keys, fiscal signing keys, API credentials and proxy credentials are secrets or sensitive identity material. Store references in a scoped secret service; do not put raw values in prompts, logs, Memory, artifacts or event records.
- Domain adapters need least-privilege operations and explicit user consent, especially for payments, refunds, fiscal receipt issuance, account actions, email, Git, database writes and deployment.
- Online payment confirmation and fiscal receipt issuance are non-interchangeable events. Preserve server/provider acknowledgement and reconcile discrepancies; retries must not duplicate charge/receipt effects.
- Record provenance and redacted outcomes. Avoid retaining personal/business data beyond a defined purpose and retention policy.
- License compatibility, merchant legal duties, financial controls, platform terms and data-protection obligations need specialist review for concrete integrations.

## DOMAIN PATTERN LIBRARY

**PROPOSED:** a curated knowledge capability, not a runtime module marketplace and not a hidden requirements catalog. Potential top-level map for evaluation:

```text
Business Systems
├── Hospitality
├── Restaurant / Cafe
├── Retail / POS
├── Warehouse
├── Accounting / Finance
├── CRM
└── E-commerce

Automation Systems
├── Browser Automation
├── Multi-account (conditional)
├── Workers / Scheduling
└── Integrations

Engineering Systems
├── API / Data / Migration
├── Testing / DevOps
└── Security

AI Systems
├── Agents / Skills / Tools
├── MCP (optional protocol)
├── Memory / Evaluation / Learning
└── Durable Runtime
```

Each pattern is a candidate that helps Discovery ask better questions. It does not add features to a Project Brief or plan until applicability is evidenced and, where material, approved. Repeated patterns can be marked cross-domain; one-source patterns remain domain/project examples with lower confidence.

## ARCHITECTURE IMPACT

| Area | Impact from this research | Classification |
| --- | --- | --- |
| Core boundaries | Preserve generic orchestration and lifecycle; add no hospitality/ERP/fiscal concepts to Core by default. | **CONFIRMED / PROPOSED** |
| Agent / Skill System | Keep Stage 1.6 contracts; domain patterns may inform Skill candidates, not grant permissions. | **CONFIRMED** |
| Capability Registry | A project/domain may request capabilities; availability does not imply applicability or permission. | **PROPOSED refinement** |
| Tool layer | Typed, idempotent where possible, versioned adapters with side-effect classification and audit. | **PROPOSED** |
| Permission model | Scope by identity/project/environment/operation; high-impact action gates. | **PROPOSED**, exact policy **DECISION REQUIRED** |
| Runtime | Durable, checkpointed execution and reconciliation for long external workflows. | **FUTURE**, depends on Stage 1.5 decisions |
| Project Memory | Preserve project-specific observations and owner decisions; never promote automatically. | **CONFIRMED** |
| Forge Knowledge | Store reviewed, generalized patterns with provenance, applicability, exclusions and version. | **CONFIRMED / PROPOSED schema-independent refinement** |
| Domain Pattern Library | Discovery aid/candidate knowledge, not mandatory domain modules. | **PROPOSED** |
| Integration model | Replaceable external-system adapters with provider-specific protocol hidden behind contract. | **PROPOSED** |
| Generated Project | Select actual domain model/stack from requirements; do not embed Forge dependency by default. | **CONFIRMED** |
| KeyCore-Hub | No relationship/integration inferred. | **UNKNOWN / DECISION REQUIRED** |
| Mastery Creator | Optional; no implementation assumptions or integration decisions. | **OPTIONAL / UNKNOWN** |
| UI / Control Center | Show candidate domain concerns, source, uncertainty, decisions and enabled integrations progressively. | **FUTURE** |
| Security | External software review, domain side-effect gates, secret separation and reconciliation. | **PROPOSED** |
| Evaluation | Domain-specific golden scenarios for lifecycle, failure, security and integration outcomes. | **PROPOSED** |

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

These are recommendations, not owner-approved roadmap commitments.

### MUST HAVE before implementing domain-aware project generation

- Domain patterns must be suggestions with applicability and non-applicability, not hidden requirements.
- Keep domain policy and integrations outside domain-independent Core.
- Distinguish observed evidence, inference and generalized candidate; show source/provenance.
- Separate payment, fiscalization, accounting and business transaction outcomes.
- Review license, trust, permissions, credentials and side effects before using external software/integrations.
- Use project-specific requirements and verification; do not claim legal, fiscal or financial compliance from a template or API alone.

### SHOULD HAVE

- Curated Domain Pattern Library and targeted Discovery prompts.
- Replaceable integration adapters with correlation, idempotency, reconciliation and versioning contracts.
- Domain-specific evaluation cases, supply-chain review and source freshness checks.
- Review lifecycle for Candidate Knowledge/Skill/Capability promotion.

### OPTIONAL

- Hospitality/POS/warehouse/accounting integration bundles, MCP connectors, account/profile automation, plugin packages, visual domain maps. Enable only when project evidence and owner scope justify them.

### FUTURE

- Automated repository harvesting, isolated dynamic analysis, broad domain packs, multi-tenant connector registry, domain model recommendation and cross-project learning.

### REJECT / NOT APPLICABLE

- One universal ERP/PMS/domain model for all generated projects.
- Automatic inclusion of restaurant, housekeeping, warehouse, PRRO, proxy, browser profile, wallet, Discord/Twitter or channel manager features based only on broad words like “resort” or “automation.”
- Copying proprietary code, branding, UI, hidden prompts or undocumented internals.
- Running unknown software on the normal machine or importing credentials/sessions into Forge Knowledge.
- Treating one owner project or a popular repository as universal proof.
- Inferring payment completion from receipt issuance, or fiscalization from a foreign POS architecture.
- Making Forge mandatory in generated projects absent an explicit requirement.

## DECISION REQUIRED

1. Which domains should Forge prioritize for its first reviewed Domain Pattern Library, if any?
2. Who may approve Candidate Domain Patterns, Knowledge, Skills and external software; can that authority be delegated?
3. Which project types may invoke payment, PRRO/fiscalization, accounting, browser/multi-account, wallet or other high-risk integrations?
4. What exact permission and approval policy applies to charges/refunds, receipt issuance/returns, fiscal shift operations, account actions and production systems?
5. Should any domain integration be built into Forge, remain a separate optional extension, or be generated into a project repository?
6. What is the approved relationship, if any, between Forge, KeyCore-Hub, Mastery Creator and the owner’s other products? What materials may be inspected?
7. What source/license/security review threshold is required for external Skills, plugins, adapters and generated dependencies?
8. Which authoritative legal/accounting reviewer must validate Ukraine-specific implementation and what sources/date cadence are required?
9. What retention and localization rules apply to guest/customer/account/fiscal/audit data?

## CONFLICTS

- **Product breadth versus small Core:** harvesting many domains can tempt Forge into becoming a monolithic ERP. Keep domain knowledge/patterns modular and project-selected.
- **Capability versus applicability/permission:** technical availability or Skill request does not justify enabling an integration.
- **Payment versus fiscalization:** separate authority, state, events, and reconciliation; do not conflate them.
- **Offline PRRO versus generic retry:** fiscal-number allocation and deferred submission are a domain protocol, not ordinary HTTP retry/fallback.
- **Open-source label versus license scope:** QloApps reports different license boundaries by core/module; inspect every dependency and target version.
- **Browser isolation versus production identity:** Playwright test contexts demonstrate isolated state but do not settle secure multi-account operations, provider terms or legal basis.
- **Current implementation versus target patterns:** current Planner is deterministic/static, and current multi-agent execution is bounded; the harvest pipeline and domain-aware discovery are not implemented.
- **Project evidence:** existing owner projects are named as research targets but no inspectable source evidence was present in reviewed Forge documentation; no project-specific mechanism is asserted.

## DOCUMENTATION IMPACT

| Document | Impact |
| --- | --- |
| `docs/STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md` | This source-backed conceptual harvest, with English and Russian sections. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Recommended after owner review:** add only accepted Domain Pattern Library, integration/security and Discovery boundaries. Do not add domain-specific product requirements. |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | No change; this task applies its governance/security ideas to external business software. |
| `docs/STAGE_1_6_AGENT_SKILL_SYSTEM.md` | No change; domain Skills fit its existing proposal and do not need a duplicate definition. |
| `docs/ARCHITECTURE.md` | No change; no code or runtime integration changed. |
| `docs/ROADMAP.md` | No change until owner selects and sequences domains. |
| `docs/DECISIONS.md` | No change; research candidates are not approved decisions. |
| `docs/FORGE_VISION.md` | No change; the existing product direction remains domain-independent. |

## NEXT STAGE

**Recommended:** Stage 1.8 — Domain Pattern Library Governance and Discovery Acceptance Criteria. Select a small initial domain set with the owner, define evidence/confidence/applicability/non-applicability fields and source freshness, and specify how Discovery asks targeted questions without assuming modules. For any Ukraine fiscal/POS scope, first obtain a concrete project scenario and qualified current legal/accounting review. Do not implement domain packs or integrations in this conceptual stage.

## Sources, repositories and limits

Research was performed through public pages, without cloning or executing code. Current facts can change; check release/version/date before future use.

- **Hospitality / open source:** [QloApps repository and license notes](https://github.com/Qloapps/QloApps); [HotelDruid feature/license/release overview](https://www.hoteldruid.com/en/); [HotelDruid README/license notes](https://www.hoteldruid.com/wiki/doku.php?id=english_readme).
- **ERP / inventory / accounting:** [ERPNext repository](https://github.com/frappe/erpnext); [Stock Entry](https://docs.frappe.io/erpnext/stock-entry); [Stock Reconciliation](https://docs.frappe.io/erpnext/stock-reconciliation); [Immutable Ledger](https://docs.frappe.io/erpnext/immutable-ledger-in-erpnext); [Accounting Introduction](https://docs.frappe.io/erpnext/accounting-introduction).
- **Restaurant / POS:** [Odoo restaurant POS documentation](https://www.odoo.com/documentation/18.0/applications/sales/point_of_sale/restaurant.html); [Odoo fiscal positions](https://www.odoo.com/documentation/18.0/applications/sales/point_of_sale/pricing/fiscal_position.html).
- **Browser isolation / open source:** [Playwright repository](https://github.com/microsoft/playwright); [browser context isolation](https://playwright.dev/docs/browser-contexts); [BrowserContext API](https://playwright.dev/docs/api/class-browsercontext).
- **Ukraine legal and official technical material:** [Law №265/95-ВР](https://zakon.rada.gov.ua/laws/card/265/95-%D0%B2%D1%80); [ДПС PRRO API documentation](https://cabinet.tax.gov.ua/help/api.html); [ДПС PRRO FAQ](https://tax.gov.ua/baneryi/programni-rro/aktualni-zapitannya-vidpovidi/); [ДПС API update notice, 2025](https://tax.gov.ua/en/mass-media/news/874825.html); [Law №851-IV on electronic documents](https://zakon.rada.gov.ua/laws/show/%D0%B5-%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%BE%D0%BE%D0%B1%D1%96%D0%B3).
- **Vendor integration example:** [Checkbox API guide](https://wiki.checkbox.ua/uk/api/api_eng).
- **Agentic patterns:** Stage 1.5 bibliography and Stage 1.6 contracts in this repository; MCP security and OpenAI/Anthropic/GitHub/Cursor public documentation cited there.

---

# Forge AI — Stage 1.7: анализ внешнего ПО и предметных областей

**Статус:** КОНЦЕПТУАЛЬНОЕ ИССЛЕДОВАНИЕ. Выводы ниже — наблюдения или кандидаты, а не утверждённые требования Forge. **Проверка источников:** 2026-10-03. Внешние репозитории и бинарные файлы не клонировались, не устанавливались и не запускались.

## Краткий вывод

Наиболее важный повторяющийся подход — **domain-independent Core с явными границами расширения**. Бизнес-платформы разделяют операционные документы и ledger, отличают физические движения запасов от сверки, соединяют предметные модули через понятные границы транзакций и workflow. У hospitality, fiscal/POS, browser automation и agent tooling разные lifecycle и семантика сбоев; их следует рассматривать как необязательные Domain Patterns, Skills, Capabilities или Integrations, выбранные только после Discovery конкретного проекта.

У Forge уже есть routing провайдеров, детерминированный шаблонный Planner, ограниченное выполнение primary/reviewer/revision и целевая документация для Discovery, capabilities, памяти, governance и approvals. Общих project tools, Domain Pattern Library, integrations, долговечных предметных workflows и реестра domain extensions нет. Предыдущая документация указывает, что Mastery Creator необязателен, а KeyCore-Hub остаётся нерешённым вопросом. Сведения о содержимом/поведении Axis, KonnexMastery, FlashCast, системах Primorie и Mastery Creator не представлены как проверяемое свидетельство в текущем репозитории; внутренние детали не предполагаются.

**Рекомендуемый принцип:** Наблюдать → сравнивать независимые источники → обобщать только повторяемые механизмы → классифицировать по слоям → оценивать применимость → сохранять как candidate knowledge/pattern → принимать решение позднее. Популярность, один проект или перечень функций внешнего продукта не превращают их в требование Forge.

## EXTERNAL SOFTWARE FINDINGS

### Изученные open-source проекты

Изучение выполнялось только по публичным репозиториям/документации. Оценка зрелости — качественное наблюдение, а не сертификат безопасности или качества. Лицензии приведены так, как их указывают источники проектов; это не юридическое заключение и не заменяет проверку конкретных файлов/модулей/версии для возможного использования.

| Проект / repository | Лицензия | Наблюдаемая зрелость | Релевантные модули/механизмы | Что изучать / не копировать |
| --- | --- | --- | --- | --- |
| [ERPNext](https://github.com/frappe/erpnext) | Репозиторий указывает GPL-3.0; перед использованием проверить конкретный релиз и включённые приложения. | Сложившаяся широкая ERP с обширной публичной документацией и крупным активным репозиторием. | Accounts, selling/buying, stock ledger, purchase receipt, stock movement/reconciliation, POS, CRM, manufacturing, workflows, reports. | Изучать модульность бизнес-документов, связанные ledgers, permissions и историю исправлений. Не копировать framework/data model целиком и не предполагать, что нужен весь ERP scope. |
| [QloApps](https://github.com/Qloapps/QloApps) | По заявлению upstream, Core — OSL-3.0; у модулей могут быть свои лицензии, часть прочих модулей — AFL-3.0. Проверять каждый модуль. | Крупный длительно развивающийся hotel/booking-проект с активным исходным кодом и видимыми issue/release; гарантии поддержки неизвестны. | PMS, booking engine, сайт отеля, вопросы front desk/central reservation, архитектура модулей. | Изучать разделение гостиничных операций и каналов бронирования. Не предполагать единую лицензию модулей, нужный охват каналов или возможность копирования дизайна/кода. |
| [HotelDruid](https://www.hoteldruid.com/en/) и [GitHub source listing](https://github.com/digital-druid/hoteldruid) | Официальный сайт указывает AGPL; также описаны proprietary hosted add-on modules. Перед использованием проверить актуальную поставку и условия. | Длительно существующая более узкая PMS (на сайте указана v3.0.8, выпущенная 2025-12-04); в ссылке на GitHub есть маркер `NO_SOURCE_CODE_UPDATE`, поэтому репозиторий не считается надёжным актуальным зеркалом исходного кода. | Календарь комнат/тарифов, распределение комнат, user privileges, receipts/invoices, POS, occupancy/revenue statistics. | Учитывать, что небольшая PMS может совмещать брони и ограниченные операционные функции, а channel/booking features могут быть отдельными. Не выводить зрелость исходников из возраста проекта или активности зеркала. |
| [Playwright](https://github.com/microsoft/playwright) | Метаданные пакета Playwright указывают Apache-2.0. | Зрелый активно поддерживаемый browser automation/test framework с широкой документацией и несколькими browser engines. | Browser, isolated contexts, cookies/storage state, pages, automation, test isolation. | Изучать изоляцию контекстов и явный lifecycle состояния. Не считать тестовый механизм полной архитектурой безопасной multi-account системы и не предполагать, что манипуляции fingerprint уместны. |

Оговорки по лицензиям/статусу: README проекта — исходная справка, а не разрешение копировать код или ресурсы в Forge. У QloApps различия лицензий по модулям; hosted add-ons могут быть proprietary. У ERPNext/Frappe и модулей экосистемы Odoo могут различаться лицензии. При будущем переиспользовании проверить конкретную версию, лицензии зависимостей, generated assets и модель распространения. Исходники не копировались.

### Наблюдаемые patterns и их границы

1. **Операционный документ → производные записи ledger → отчёт/сверка:** бизнес-действия фиксируются предметными документами; ledger entries дают историю accounting/stock; отчёты ведут к исходным документам. Это повторяется в документации ERPNext. **Кандидат на универсальный механизм:** provenance и прослеживаемые переходы состояния. **Не универсально:** double-entry accounting относится к accounting capability, а не ко всем генерируемым Forge проектам.
2. **Исправление с сохранением истории:** ERPNext описывает cancellation/reversal и amendment вместо незаметного редактирования финализированных ledger rows. Это полезно для аудита в финансовых/складских системах. **Кандидат на общий принцип:** сохранять provenance исправлений там, где этого требует предметная область/аудит; конкретная политика immutability зависит от области.
3. **Доступность не равна одному количеству:** hospitality продаёт вместимость по датам и тарифам/условиям занятости; склад учитывает товар по локациям/оценке; ресторан списывает запасы по рецептам/modifiers и операционным событиям. Потребность в ledger/reservation схожа, ограничения различны. Нельзя объединять их в абстракцию «inventory» без требований.
4. **Онлайн/офлайн фискальная работа имеет protocol lifecycle:** официальные материалы Украины описывают смену, чеки/отчёты, нумерацию офлайн-чеков и последующую синхронизацию. Fiscal adapter должен показывать подтверждённое сервером состояние и локальное неопубликованное/неопределённое состояние; это не generic payment API.
5. **Payment и fiscalization — отдельные authority:** outcome оплаты, POS order, callback эквайринга и fiscal receipt — разные записи/события. Их нужно связывать сверкой, а не выводить одно из другого.
6. **Browser profiles хранят состояние с явной изоляцией:** Playwright BrowserContexts разделяют cookies/storage и создаются независимо в одном browser. Persistent contexts, импорт/экспорт состояния и реальные credentials имеют отдельный lifecycle данных/permissions. Test-isolation primitive не является архитектурой для массовых аккаунтов.
7. **Предметные продукты — композиции, не универсальные шаблоны:** QloApps и HotelDruid различаются scope PMS/booking; ERP suites объединяют модули; channel manager, fiscalization, POS и kitchen функции остаются вопросами применимости.
8. **Agent systems требуют слоистой интеграции:** Stage 1.5/1.6 уже разделяют Agent, Skill, Capability, Tool, Knowledge, Memory, permissions и runtime. Внешние tool protocols (включая MCP) могут реализовать интеграционную границу, но не authorization или семантику предметной области Forge.

## HOSPITALITY FINDINGS

QloApps описывает гостиничную PMS с booking engine и сайтом; HotelDruid документирует комнаты, периоды, тарифы, правила распределения комнат, групповые бронирования, user privileges и occupancy/revenue statistics. Это заявления проектов, а не доказательство единственной канонической модели hospitality.

**Кандидатный Domain Pattern:** `Property → Unit/RoomType → Availability by date → Rate/Restriction → Reservation → Guest/Party → Stay/Check-in/out → Folio/Charges → Payment/Settlement → Housekeeping/Room status → Audit/Reports`. Channel и booking-engine взаимодействия — integration patterns. Ресторан, склад, POS, housekeeping, night audit, payments, fiscalization и channel management условны. Resort может нуждаться в части, всём или ни в чём из этого перечня.

**Извлечённые идеи:** хранить раздельно намерение брони, подтверждённое резервирование capacity, фактический stay, charges/folio и состояние оплаты. Часовые пояса, изменения назначения комнаты, overbooking policy, cancellation/no-show, group reservations, deposits и reconciliation моделировать только если Discovery их установил. Не помещать гостиничные операции в Forge Core.

## RESTAURANT / CAFE FINDINGS

Документация Odoo restaurant/POS описывает столы/floors, orders, отправку позиций на kitchen/bar printers и конфигурацию fiscal-position/tax. POS ERP-платформ связывает продажи со stock/accounting. Эти vendor patterns подсказывают workflow:

```text
Menu / item + modifiers
 -> Order / table / service mode
 -> kitchen ticket(s) and preparation state
 -> served items / corrections / voids
 -> tender(s), split/partial payment and reconciliation
 -> receipt/fiscal adapter where required
 -> sales, stock consumption and reporting
```

**Кандидаты, специфичные области:** KDS/printer routing, курсы блюд, modifiers, recipe/BOM consumption, переносы стола, split checks, tips, смена/cash drawer и офлайн-работа. Это не универсально; работа терминала/эквайринга не равна фискализации. У ресторана может не быть столового обслуживания или кухонного экрана.

## WAREHOUSE / INVENTORY FINDINGS

Документация ERPNext разделяет Purchase Receipt, Stock Entry, Stock Reconciliation и ledger/reporting. Purchase Receipt фиксирует принятые у поставщика товары; Stock Entry — выдачу, приём, перемещение, производство или движение; Stock Reconciliation сравнивает физический подсчёт с book quantity; serial/batch и warehouse location ограничивают движение. Perpetual valuation может связывать стоимость stock с accounting.

**Кандидатный pattern:** `Item/SKU + UOM + Location + Lot/Serial + Movement event + Quantity/value + Source document + Actor/time`. Приёмка, put-away, перемещение, reservation, consumption, возврат, cycle count, adjustment, reorder и valuation — отдельные процедуры. Разделять физический подсчёт и учётный баланс; сохранять автора/причину/источник корректировки. FIFO/average valuation и negative-stock rules влияют на accounting и являются необязательной политикой области, не Core defaults.

## ACCOUNTING / FINANCE FINDINGS

ERPNext описывает, как source documents (sales/purchase invoices, payments, stock movements) создают сбалансированные записи General Ledger; receivable/payable/payment ledgers связывают стороны и расчёты. Reconciliation сопоставляет внешние банковские/платёжные записи с ledger. Cancellation/return/reversal сохраняет исходную транзакцию в подходе immutable ledger.

**Кандидатная граница области:** операционное событие само по себе не ledger; проведённые финансовые записи должны иметь ссылки на source documents, accounting period, currency, company, tax и approval. Инициирование платежа, подтверждение, settlement invoice и refund — отдельные факты. **Специфично области:** double-entry ledger, chart of accounts, tax rules, fiscal close, accrual и accounting-period controls. Forge не должен навязывать это нефинансовым генерируемым проектам.

## UKRAINE FISCAL / POS FINDINGS

Следующее намеренно разделяет право и технику. Это архитектурное исследование, а не юридическое заключение и не вывод об обязанностях конкретного продавца.

| Тип свидетельства | Архитектурно значимое наблюдение | Источник / оговорка |
| --- | --- | --- |
| **Норма/правовой текст** | Закон Украины №265/95-ВР регулирует применение регистраторов расчётных операций в торговле, общественном питании и услугах; актуальная применимость зависит от действующего текста, подзаконных правил, фактов бизнеса/операции и сроков вступления в силу. | [Карточка закона Верховной Рады](https://zakon.rada.gov.ua/laws/card/265/95-%D0%B2%D1%80) показывает статус и историю редакций. Перед реализацией проверить действующий текст. |
| **Официальная техническая документация — ДПС** | Electronic Cabinet описывает API фискального сервера ПРРО для чеков и Z-отчётов. Для тестового API прямо указано, что тестовые чеки/Z-отчёты нефискальные. Открытие смены может создавать служебный/нулевой чек; офлайн и онлайн очереди различаются. | [API Электронного кабинета ДПС](https://cabinet.tax.gov.ua/help/api.html). API и версии могут меняться. |
| **Официальные инструкции ПРРО** | Офлайн-режим использует зарезервированный диапазон фискальных номеров и локальные упорядоченные записи с последующей синхронизацией; действуют ограничения времени и требования к переходам/отчётам. | [FAQ ДПС по ПРРО](https://tax.gov.ua/baneryi/programni-rro/aktualni-zapitannya-vidpovidi/) и [инструкция по офлайн-режиму](https://dn.tax.gov.ua/media-ark/local-news/470781.html). Часть FAQ датирована; перед использованием проверить действующие правила/приказ. |
| **Документация поставщика** | Checkbox описывает REST API и разделяет merchant dashboard, transaction processing, signing agents и frontend agents. | [Checkbox API guide](https://wiki.checkbox.ua/uk/api/api_eng). Отдельно проверять контракт/версию и правовую применимость продукта. |
| **Официальное регулирование/понятие электронного документа** | У электронных документов, обязательных реквизитов, подписей и обмена есть отдельный правовой и технический слой. | [Закон №851-IV](https://zakon.rada.gov.ua/laws/show/%D0%B5-%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%BE%D0%BE%D0%B1%D1%96%D0%B3); для конкретного процесса уточнить текущие изменения и требования к доверенным услугам. |

**Кандидатные границы интеграций:**

- **POS/order domain** отвечает за basket, настроенные классификации товаров/налогов, tenders и returns как бизнес-события.
- **Payment/acquiring integration** отвечает за authorization/capture/refund и callbacks; дубликаты и события не по порядку обрабатываются идемпотентно.
- **Fiscalization integration** отвечает за кассира/контекст регистрации ПРРО, состояние смены, структуру документа/подписания, фискальный номер/чек, офлайн-допуск, очередь и подтверждение сервера.
- **Accounting/e-document integration** отвечает за posting/export, статус обмена, подписи/квитанции и reconciliation.

Адаптеры должны заменяться и сохранять ID запросов/ответов и подтверждённые результаты, скрывая credentials и персональные данные. Не реализовывать «универсальный фискальный чек» только по архитектуре зарубежного POS. API или маркетинг vendor не подтверждают соответствие законодательству Украины. Точные обязанности, сроки, реквизиты чеков, операции возврата/кассы и процедуры подписи остаются **UNKNOWN / нужна проверка юристом для конкретного проекта**.

## MULTI-ACCOUNT / BROWSER FINDINGS

Playwright BrowserContext предоставляет изолированные браузерные сессии с отдельными cookies/storage и позволяет запускать несколько пользователей в одном browser. Это сильное свидетельство в пользу **изоляции состояния**, но не всех требований multi-account automation.

**Кандидатная повторно используемая модель:** `Account Identity → Profile/State Container → Credential Reference → Proxy/Network Policy (conditional) → Wallet/External Identity (conditional) → Task Assignment → Worker/Lease → Run State → Rate/Concurrency Limits → Outcome/Statistics → Reconciliation/Review`.

- Lifecycle аккаунта и browser profile — разные автоматы состояний. Account identity — не просто каталог browser.
- Persistent storage/import-export чувствительны; шифровать и ограничивать доступ, фиксировать provenance, поддержать отзыв/удаление. Никогда не логировать cookies/session tokens.
- Worker нуждается в lease, изоляции, ограниченной concurrency, cancellation и локализации сбоев; сбой задачи аккаунта не должен портить другие профили.
- Scheduling/retries требуют idempotency либо проверки состояния до повтора; важны rate limits и правила внешнего сервиса.
- Fingerprint manipulation, proxies, wallets, Discord/Twitter и multi-account behavior — **CONDITIONAL / DECISION REQUIRED**, а не универсальные требования. Возможны риски privacy, нарушения правил аккаунта, fraud, security и условий использования; обход ограничений здесь не рекомендуется.

Нет предположений о реализации Mastery Creator, obfuscated code, содержимом секретов/configuration или не наблюдавшемся поведении. Использованы только внешние документированные patterns и прежняя граница проекта («необязателен; детали неизвестны»).

## ERP / BUSINESS PLATFORM FINDINGS

ERPNext и Odoo показывают модульные платформы для finance, inventory, закупок/продаж, POS, CRM и reporting. Повторно используемые patterns:

- доменные source documents, соединённые с ledgers/reports стабильными ссылками;
- role/permission и workflow controls рядом с бизнес-действиями;
- явные границы module/localization вместо единого глобального налогового правила;
- точки расширения для страновой и предметной политики;
- аудируемые процедуры correction/amendment и reconciliation.

Риски: широта ERP увеличивает связанность и сложность настройки; лицензии модулей/совместимость неодинаковы; migration/backdating может менять производные ledger values; конфигурация не доказывает соответствие закону. Forge должна изучать границы и workflows, а не генерировать ERP по умолчанию.

## AGENTIC SOFTWARE FINDINGS

Stage 1.5/1.6 уже охватывают значительную часть общей агентной архитектуры: Agent/Skill/Capability/Knowledge/Tool/Project Memory разделены; использование зависит от permissions; Skills обнаруживаются постепенно; durable Runs, sandbox, event trace и evaluation пока предложения. Интеграции внешнего ПО добавляют кандидатные требования:

- version и provenance внешнего Skill/tool server/connector;
- проверка scripts, dependencies, лицензии, permissions и network requests до доверия;
- connector adapter должен сохранять authorization и audit boundary Forge;
- полученный контент проекта/домена — недоверенное evidence, не policy;
- evaluation проверяет ошибки tools, дубликаты событий, отзыв разрешения и recovery, а не только качество текста.

MCP — возможный протокол interoperability, а не Capability model или система permissions. Forge должна поддерживать нативные adapters или иные протоколы без смены доменной модели.

## MASTER CREATOR / EXISTING PROJECT COMPARISON

| Свидетельство о проекте | Наблюдалось | Выведено | Кандидат на обобщение | Статус |
| --- | --- | --- | --- | --- |
| Axis | В просмотренных документах Forge нет свидетельств об исходниках/архитектуре для этой задачи. | Ничего. | Ничего. | **UNKNOWN** |
| KonnexMastery | В просмотренных документах Forge нет свидетельств об исходниках/архитектуре для этой задачи. | Ничего. | Ничего. | **UNKNOWN** |
| FlashCast | В просмотренных документах Forge нет свидетельств об исходниках/архитектуре для этой задачи. | Ничего. | Ничего. | **UNKNOWN** |
| ПО Primorie | В просмотренной документации Forge не найден материал проекта для проверки. | Ничего. | Ничего. | **UNKNOWN** |
| Mastery Creator | Предыдущие документы Forge называют его optional, а архитектуру/integration contract — неизвестными; исходники и runtime здесь не изучались. | Может быть кандидатом на внешнюю/условную capability при требованиях конкретного проекта. | Никакие функции accounts, fingerprint, proxy, wallet, browser или соцсетей не считаются универсальными на основе текущих данных. | **OPTIONAL / UNKNOWN** |
| KeyCore-Hub | Предыдущие документы Forge оставляют архитектуру и связь с Forge неизвестной. | Может стать интеграцией конкретного проекта после выяснения требований и границ доступа владельцем. | Ничего. | **UNKNOWN / DECISION REQUIRED** |

Credentials, private project files, sessions, wallets, cookies и внутренние исходники не проверялись и не воспроизводились. Отсутствие локальных данных не доказывает отсутствие механизма в проекте.

## REPEATED UNIVERSAL PATTERNS

Patterns, осторожно обобщённые по независимым категориям:

1. **Явные переходы состояния и provenance** для важных операционных записей и долгих процессов. (ERP ledgers, PRRO shifts/receipts, browser/agent Runs.)
2. **Раздельные намерение, выполнение, подтверждение и сверка.** Reservation — не stay; order — не payment; payment — не fiscal receipt; запрос tool — не подтверждённый успех.
3. **Идемпотентная обработка событий и дубликатов/нарушенного порядка** на внешних границах; если эффект мог случиться, проверить авторитетное состояние перед повтором.
4. **Ролевое разделение и least privilege**, отдельные операторы (кассир/бухгалтер/housekeeper/admin; agent/worker) и ограниченные действия.
5. **Заменяемые integrations/adapters** вокруг внешних систем и меняющихся протоколов, с correlation IDs, версиями и состояниями ошибок.
6. **Исправления без молчаливого стирания истории**, где этого требуют предметная область/аудит; нельзя ложно обещать обратимость.
7. **Изоляция изменяемого состояния** (workspace, profile, inventory location, fiscal shift, project/run) и ограниченная concurrency.
8. **Reconciliation и завершение по evidence**, а не по факту, что команда/модель заявила об успехе.

«Повторяется» означает наблюдение в нескольких независимых примерах/категориях, а не единую реализацию во всех системах и не обязательную функцию каждого проекта Forge.

## ARCHITECTURAL COMPARISON MATRIX

`Repeated?` показывает независимые примеры, изученные в этом harvest, а не универсальное внедрение. «Forge сегодня» описывает текущую документацию, а не обязательство.

| Pattern | Source(s) | Repeated? | Forge сегодня | Пробел | Классификация | Предлагаемый статус |
| --- | --- | ---: | --- | --- | --- | --- |
| Кандидаты домена в Discovery с явной применимостью | Целевые документы Stage 1.3–1.5; границы ERP/hospitality продуктов | Да, как практика продуктов; общей таксономии нет | Discovery только целевой | Нет проверенного domain index/candidate flow | Knowledge / Domain Pattern | **PROPOSED** |
| Бизнес-документ связан с accounting/stock ledger | Документация ERPNext accounting + stock | Да в рамках ERP-модулей | Нет бизнес ledger | Предметная финансовая/складская модель зависит от проекта | Domain Pattern / Generated Project | **OPTIONAL** |
| Append-only correction/reversal для учётных postings | Immutable ledger ERPNext; украинский fiscal lifecycle | Повторяется в регулируемых/audited процессах | Только общие Git safety | Нет доменной event model | Domain Pattern / Integration | **OPTIONAL**, применять по необходимости |
| Room availability по датам и lifecycle reservation-to-stay | QloApps; HotelDruid | Да среди hotel systems | Нет hotel capability | Нет hospitality model | Domain Pattern | **OPTIONAL** |
| Граница channel manager / booking engine | QloApps; описания add-ons HotelDruid | Повторяется, scope/deployment отличается | Нет | Нет контракта channel connector | Integration | **OPTIONAL / FUTURE** |
| Restaurant order → kitchen ticket workflow | Odoo Restaurant POS | Свидетельство из одного выбранного набора docs | Нет | Нет restaurant workflow | Domain Pattern / Capability | **OPTIONAL** |
| Списание ингредиентов по recipe/modifier | ERPNext manufacturing/stock; вывод для ресторанной области | Частично; не проверено в нескольких независимых restaurant OSS здесь | Нет | Нет recipe-consumption model | Domain Pattern | **UNKNOWN / кандидат** |
| Движение stock и сверка склада | ERPNext Stock Entry/Reconciliation | Повторяется в ERP stock concepts | Нет | Нет inventory capability | Domain Pattern / Capability | **OPTIONAL** |
| Stock по item/lot/serial и location | Документация ERPNext stock | Встречается в inventory systems; изучен один основной repo | Нет | Нет модели идентичности stock | Domain Pattern | **OPTIONAL** |
| Событие accounting → сбалансированная ledger запись | Документация ERPNext accounting | Повторяется в accounting systems; изучен один основной repo | Нет accounting domain | Нет модели/integration accounting | Domain Pattern / Integration | **OPTIONAL** |
| Сверка внешнего расчёта с books | ERPNext; границы payment/fiscal систем | Концептуально повторяется в finance integrations | Нет | Нет reconciliation workflow | Integration / Domain Pattern | **OPTIONAL** |
| PRRO смена, чек, Z-report и offline queue | Официальные API/правила ДПС; API поставщика Checkbox | Несколько независимых официальных/vendor источников; протоколы отличаются | Нет | Нет украинского fiscal adapter | Integration / Domain Pattern | **DECISION REQUIRED** по проекту |
| Payment/acquiring отдельно от fiscal receipt | Документы ДПС/Checkbox; POS workflow | Да в материалах official/vendor boundary | Provider layer только для AI моделей | Нет бизнес integration boundary | Integration | **PROPOSED** |
| Browser context изолирует cookies/storage | Документация Playwright | Повторяемые contexts внутри продукта; общий multi-account вывод не подтверждён | Нет browser capability | Нет модели profile/credential | Capability / Runtime | **OPTIONAL** |
| Persistent account profile и lifecycle credentials | Вывод безопасности/browser ecosystem; зависит от источника | Слабые/неуниверсальные данные в выбранных первичных источниках | Нет | Нет утверждённого дизайна состояния аккаунта | Runtime / Security | **UNKNOWN / DECISION REQUIRED** |
| ERP модули разделены по домену/localization | ERPNext; документы Odoo | Да | Только разделение Forge Core/provider | Нет domain extension architecture | Core / Domain Pattern | **PROPOSED** |
| Разделение Agent/Skill/Tool и permissions | Stage 1.5–1.6; ранее изученные agent продукты | Да среди уже сравнивавшихся источников | Концептуально в docs; частично multi-agent реализация | Нет общего runtime/tools | Core / Runtime / Skill | **PROPOSED** |
| Проверка source/license/dependencies перед reuse | Upstream license files; разные лицензии модулей QloApps | Да как supply-chain требование | Git safety rules, intake pipeline отсутствует | Нет контроля импорта внешнего ПО | Core / Knowledge / Security | **MUST HAVE до импорта** |
| Idempotency и защита от duplicate/out-of-order внешних событий | Архитектуры payment/PRRO/worker | Повторяющаяся инженерная необходимость; source contracts различаются | Fallback провайдера ограничен; бизнес-события не обрабатываются | Нет external-event contract | Runtime / Integration | **MUST HAVE до side-effect integrations** |
| Domain-specific acceptance/evaluation cases | Eval proposal Stage 1.5; ERP/POS protocols | Повторяется в инженерной практике; пока не реализовано | Unit/smoke tests самого Forge | Нет domain eval catalog generated projects | Knowledge / Evaluation | **SHOULD HAVE** |
| Domain template не должен навязывать скрытые требования | Governance Stage 1.3–1.6; вариативность продуктов | Повторяемое требование в этапах Forge | Детерминированные generic план-шаблоны | Нет политики Domain Pattern Library | Core / Discovery | **MUST HAVE** |

Матрица основана на ограниченном наборе open-source примеров для hospitality/restaurant. Статус repeated намеренно консервативен. Ни один механизм не объявлен универсальным по одному источнику.

## DOMAIN-SPECIFIC PATTERNS

| Область | Кандидатный pattern | Классификация | Вопросы применимости |
| --- | --- | --- | --- |
| Hospitality | room/date availability, reservation-to-stay, guest folio, housekeeping state, rate restrictions, night close | Domain Pattern | Есть ли rooms/units, stays, брони каналов, housekeeping или night audit? |
| Restaurant | table/order/course/modifier, kitchen ticket/KDS, recipe consumption, split tenders, shift close | Domain Pattern / Capability | Столовое обслуживание? Kitchen routing? Учёт ингредиентов? Offline POS? |
| Warehouse | SKU/UOM, locations, receiving, movement ledger, serial/batch, count/reconciliation, valuation/reorder | Domain Pattern | Учитывать только количество или также партии/серийность/стоимость/срок/reservation? |
| Accounting | source documents, double-entry posting, receivables/payables, reconciliation, period controls, reversals | Domain Pattern / Integration | Accounting внутри scope или остаётся в другой системе/у бухгалтера? |
| Ukraine fiscal/POS | fiscal document adapter, PRRO shift/session, offline queue/number range, Z-report, return, server acknowledgement | Integration / Domain Pattern | Какие субъект, операция, provider, POS и текущие правила применимы? Нужна правовая проверка. |
| Browser/multi-account | identity, isolated profile/session, worker lease, credential reference, schedule/limits, outcomes | Capability / Domain Pattern | Нужны и разрешены ли несколько аккаунтов? Какие правила сервиса и риски данных? |
| ERP/business | modular domains, workflow permissions, source docs/ledgers, localizations, extension points | Knowledge / Domain Pattern | Какие домены и system of record реально нужны? |
| Agentic system | role, skill, tool, run, checkpoint, approval, evaluation, integration adapter | Core / Runtime / Capability | Нужна ли автоматизация и какой уровень автономии/permissions? |

## NEW FORGE GAPS

- **Domain Pattern Library** с applicability/non-applicability, confidence/provenance, ссылками, контрпримерами и review status.
- Этап Discovery, который задаёт ограниченный набор вопросов по домену, выдаёт кандидатные concerns и спрашивает перед выбором доменных capabilities.
- Стабильный provider-neutral контракт внешней Integration/Adapter, отличный от Capability и Tool.
- Контракты lifecycle/idempotency/reconciliation для доменов до внешних side effects.
- Domain-specific evaluation catalog (например, границы reservation, инварианты движения stock, дубликаты payment webhook, восстановление offline fiscal queue).
- Проверка license/dependency/provenance для импортированного кода, Skills, tools и generated dependencies.
- Граница между pattern library/knowledge record, installable module, Skill, generated template и обязательной функцией проекта.

Это целевые пробелы, не реализованные функции.

## PROPOSED ADDITIONS

1. **Domain Pattern Library (PROPOSED):** индекс кандидатов для Hospitality, Restaurant/Cafe, Warehouse, Accounting, Fiscal/POS, E-commerce, CRM, Automation, Browser/Multi-account, Data, Integration/API и Agentic systems. Запись содержит проблему, источники, повторяемые свидетельства, допущения, applicability/non-applicability, failure modes, классификацию слоя, примеры evaluation, license/provenance при необходимости, версию и review status.
2. **Domain discovery flow (PROPOSED):** цель → кандидатные домены → целевые вопросы → evidence/constraints → capability candidates → проверка применимости/совместимости/security/legal по необходимости → решение владельца для существенных вопросов → модель конкретного проекта. Распознавание домена не должно автоматически выбирать функции.
3. **Integration boundary (PROPOSED):** provider-neutral контракт адаптера: identity/version внешней системы, операции, ссылки на authentication, idempotency/correlation, lifecycle states, ошибки, retries, reconciliation, классификация данных, audit и декларации capability/permission.
4. **Candidate harvesting pipeline (PROPOSED):**

```text
Discover -> collect references -> provenance -> license check
 -> quarantine/static inspection/dependency and secret scan
 -> permission/network analysis -> architecture extraction
 -> generalize -> evaluation/counterexamples
 -> Candidate Knowledge / Skill / Capability / Domain Pattern
 -> owner review -> approved registry
```

   Не запускать неизвестное ПО в ходе документального исследования. Будущая динамическая проверка требует изолированного disposable sandbox и одобренного scope.
5. **Domain evaluation cases (PROPOSED):** проверять границы и сбои, включая duplicates, late events, offline-to-online transitions, отмены/возвраты, concurrency и reconciliation, а не только happy-path экранов.
6. Оставить украинские fiscal/accounting **заменяемыми локальными интеграциями** с версиями авторитетных правил и датой проверки; для конкретного применения требуется квалифицированный legal/accounting review.

## SECURITY FINDINGS

- Считать внешние репозитории, пакеты, Skills, MCP servers, APIs, browser content и generated code недоверенными до проверки provenance, лицензии, permissions и поведения.
- Не запускать подозрительный код/бинарные файлы на обычной машине разработки. Позже при разрешении использовать quarantine, static inspection, видимость dependencies/SBOM, secret scanning, анализ permissions/network и изолированную поведенческую оценку.
- Browser cookies, storage-state files, session tokens, wallet keys, fiscal signing keys, API credentials и proxy credentials являются секретами или чувствительными identity-данными. Хранить ссылки в scoped secret service; не помещать реальные значения в prompts, logs, Memory, artifacts или event records.
- Domain adapters требуют least-privilege и явного согласия, особенно для payments, refunds, выдачи фискальных чеков, действий с аккаунтом, email, Git, database writes и deployment.
- Подтверждение онлайн-платежа и выпуск фискального чека — несопоставимые события. Сохранять подтверждение сервера/provider и сверять расхождения; retries не должны создавать двойное списание/фискальный эффект.
- Сохранять provenance и редактированный/redacted результат. Не хранить персональные/деловые данные сверх определённой цели и срока.
- Совместимость лицензий, обязанности продавца по закону, финансовый контроль, правила платформ и защита данных требуют специалиста для конкретной интеграции.

## DOMAIN PATTERN LIBRARY

**PROPOSED:** курируемая knowledge capability, а не runtime marketplace модулей и не скрытый каталог требований. Кандидатная карта верхнего уровня:

```text
Business Systems
├── Hospitality
├── Restaurant / Cafe
├── Retail / POS
├── Warehouse
├── Accounting / Finance
├── CRM
└── E-commerce

Automation Systems
├── Browser Automation
├── Multi-account (conditional)
├── Workers / Scheduling
└── Integrations

Engineering Systems
├── API / Data / Migration
├── Testing / DevOps
└── Security

AI Systems
├── Agents / Skills / Tools
├── MCP (optional protocol)
├── Memory / Evaluation / Learning
└── Durable Runtime
```

Каждый pattern — кандидат, помогающий Discovery задать уточняющие вопросы. Он не попадает в Project Brief/plan, пока применимость не подтверждена и существенные вопросы не утверждены. Повторяемые patterns можно помечать как междоменные; примеры с одним источником остаются доменными/проектными примерами с меньшим confidence.

## ARCHITECTURE IMPACT

| Область | Влияние исследования | Классификация |
| --- | --- | --- |
| Core boundaries | Сохранить generic orchestration/lifecycle; не добавлять hospitality/ERP/fiscal concepts в Core по умолчанию. | **CONFIRMED / PROPOSED** |
| Agent / Skill System | Сохранить контракты Stage 1.6; domain patterns могут стать кандидатами Skills, но не выдавать permissions. | **CONFIRMED** |
| Capability Registry | Проект/домен может запросить capability; наличие не означает применимость или разрешение. | **PROPOSED уточнение** |
| Tool layer | Типизированные, версионируемые adapters с классификацией side effects, audit и idempotency где возможно. | **PROPOSED** |
| Permission model | Scope по identity/project/environment/operation; gates для значимых действий. | **PROPOSED**, точная политика — **DECISION REQUIRED** |
| Runtime | Durable execution/checkpoints/reconciliation для долгих внешних workflows. | **FUTURE**, зависит от решений Stage 1.5 |
| Project Memory | Сохранять проектные observations и решения владельца; не продвигать автоматически. | **CONFIRMED** |
| Forge Knowledge | Хранить reviewed, обобщённые patterns с provenance, applicability, exclusions, version. | **CONFIRMED / PROPOSED уточнение без схемы хранения** |
| Domain Pattern Library | Помощь Discovery/candidate knowledge, а не обязательные доменные модули. | **PROPOSED** |
| Integration model | Заменяемые adapters внешних систем со скрытым за контрактом provider protocol. | **PROPOSED** |
| Generated Project | Выбирать предметную модель/stack по требованиям, без зависимости от Forge по умолчанию. | **CONFIRMED** |
| KeyCore-Hub | Связь/integration не выведены. | **UNKNOWN / DECISION REQUIRED** |
| Mastery Creator | Optional; никаких предположений о реализации или integration. | **OPTIONAL / UNKNOWN** |
| UI / Control Center | Постепенно показывать кандидатные доменные вопросы, источник, неопределённость, решения и включённые integrations. | **FUTURE** |
| Security | Review внешнего ПО, gates доменных side effects, секреты и reconciliation. | **PROPOSED** |
| Evaluation | Domain-specific golden scenarios для lifecycle, failures, security и результатов integrations. | **PROPOSED** |

## MUST / SHOULD / OPTIONAL / FUTURE / REJECT

Это рекомендации, не утверждённые владельцем обязательства roadmap.

### MUST HAVE до реализации domain-aware генерации проектов

- Domain patterns должны предлагаться с условиями applicability/non-applicability, а не быть скрытыми требованиями.
- Предметная политика и integrations остаются вне domain-independent Core.
- Разделять observed evidence, inference и generalized candidate; показывать источник/provenance.
- Разделять исходы payment, fiscalization, accounting и бизнес-транзакций.
- Проверять license, trust, permissions, credentials и side effects перед использованием внешнего ПО/integrations.
- Использовать требования конкретного проекта и его verification; не заявлять legal/fiscal/financial compliance по одному шаблону или API.

### SHOULD HAVE

- Курируемая Domain Pattern Library и целевые вопросы Discovery.
- Заменяемые integration adapters с контрактами correlation, idempotency, reconciliation и versioning.
- Domain-specific evaluation cases, supply-chain review и контроль свежести источников.
- Цикл review для продвижения Candidate Knowledge/Skill/Capability.

### OPTIONAL

- Integration bundles для Hospitality/POS/warehouse/accounting, MCP connectors, account/profile automation, plugin packages, visual domain maps. Включать при обосновании требованиями проекта и владельцем.

### FUTURE

- Автоматический harvest репозиториев, динамический анализ в изоляции, широкие domain packs, multi-tenant connector registry, рекомендации доменных моделей и межпроектное обучение.

### REJECTED / NOT APPLICABLE

- Единая универсальная ERP/PMS/domain model для всех генерируемых проектов.
- Автоматическое включение ресторанных, housekeeping, warehouse, PRRO, proxy, browser profile, wallet, Discord/Twitter или channel manager функций по общему слову вроде «resort» или «automation».
- Копирование proprietary кода, branding, UI, скрытых prompts или undocumented internals.
- Запуск неизвестного ПО на обычной машине либо импорт credentials/sessions в Forge Knowledge.
- Считать один проект владельца или популярный репозиторий универсальным доказательством.
- Выводить успешную оплату из выдачи чека или фискализацию из зарубежной POS архитектуры.
- Делать Forge обязательной зависимостью generated projects без явного требования.

## DECISION REQUIRED

1. Какие области Forge следует приоритизировать для первой reviewed Domain Pattern Library, если следует?
2. Кто утверждает Candidate Domain Patterns, Knowledge, Skills и внешнее ПО; можно ли делегировать эти полномочия?
3. Какие классы проектов могут использовать payment, PRRO/fiscalization, accounting, browser/multi-account, wallet или иные high-risk integrations?
4. Какая точная политика permissions/approvals относится к списаниям/возвратам, выпуску чеков, операциям фискальной смены, действиям аккаунтов и production системам?
5. Какие доменные integrations должны быть частью Forge, отдельными optional extensions или генерироваться в репозитории проекта?
6. Какова утверждённая связь Forge с KeyCore-Hub, Mastery Creator и другими продуктами владельца, если она есть? Какие материалы разрешено исследовать?
7. Какой порог проверки source/license/security нужен для внешних Skills, plugins, adapters и generated dependencies?
8. Какой официальный legal/accounting reviewer подтверждает реализацию для Украины и какие источники/периодичность проверки нужны?
9. Какие правила хранения и локализации применяются к guest/customer/account/fiscal/audit data?

## CONFLICTS

- **Широта продукта против малого Core:** сбор большого числа доменов может превратить Forge в монолитную ERP. Оставить доменные знания/patterns модульными и выбираемыми проектом.
- **Capability против applicability/permission:** техническая доступность или запрос Skill не оправдывают включение интеграции.
- **Payment против fiscalization:** отдельные authority, состояния, события и reconciliation; их нельзя смешивать.
- **Offline PRRO против обычного retry:** распределение fiscal numbers и отложенная подача — предметный протокол, не обычный HTTP retry/fallback.
- **Метка open-source против области лицензии:** QloApps сообщает о разных лицензиях Core/модулей; проверять каждую зависимость и версию.
- **Browser isolation против production identity:** Playwright contexts демонстрируют изоляцию, но не решают безопасную multi-account эксплуатацию, правила провайдера или законность.
- **Текущая реализация против целевых patterns:** текущий Planner детерминированный/статический, а multi-agent ограничен; harvest pipeline и domain-aware discovery не реализованы.
- **Свидетельства по проектам:** проекты владельца названы целями исследования, но проверяемых исходных материалов в изученной Forge документации не было; конкретные проектные механизмы не заявляются.

## DOCUMENTATION IMPACT

| Документ | Влияние |
| --- | --- |
| `docs/STAGE_1_7_EXTERNAL_SOFTWARE_DOMAIN_HARVEST.md` | Этот концептуальный harvest со ссылками, на английском и русском. |
| `docs/TECHNICAL_SPECIFICATION.md` | **Рекомендуется после review владельцем:** добавить только принятые границы Domain Pattern Library, integrations/security и Discovery. Не добавлять доменные продуктовые требования. |
| `docs/STAGE_1_5_EXTERNAL_BENCHMARK.md` | Без изменений; здесь идеи governance/security применены к внешнему бизнес-ПО. |
| `docs/STAGE_1_6_AGENT_SKILL_SYSTEM.md` | Без изменений; доменные Skills укладываются в существующее предложение, повторное определение не требуется. |
| `docs/ARCHITECTURE.md` | Без изменений; код и runtime integrations не менялись. |
| `docs/ROADMAP.md` | Без изменений до выбора/приоритизации доменов владельцем. |
| `docs/DECISIONS.md` | Без изменений; исследовательские кандидаты не являются утверждёнными решениями. |
| `docs/FORGE_VISION.md` | Без изменений; направление продукта сохраняется независимым от домена. |

## NEXT STAGE

**Рекомендуется:** Stage 1.8 — Domain Pattern Library Governance and Discovery Acceptance Criteria. Выбрать с владельцем небольшой начальный набор доменов, определить поля evidence/confidence/applicability/non-applicability и свежесть источников, описать целевые вопросы Discovery без предположения модулей. Для украинского fiscal/POS сначала зафиксировать конкретный сценарий проекта и получить квалифицированную актуальную legal/accounting проверку. Не реализовывать domain packs/integrations в этом концептуальном этапе.

## Источники, репозитории и ограничения

Исследование проводилось по публичным страницам без клонирования и исполнения кода. Сведения могут меняться; перед будущим использованием проверять релиз/версию/дату.

- **Hospitality / open source:** [репозиторий и лицензии QloApps](https://github.com/Qloapps/QloApps); [функции/лицензия/релиз HotelDruid](https://www.hoteldruid.com/en/); [README/license HotelDruid](https://www.hoteldruid.com/wiki/doku.php?id=english_readme).
- **ERP / inventory / accounting:** [репозиторий ERPNext](https://github.com/frappe/erpnext); [Stock Entry](https://docs.frappe.io/erpnext/stock-entry); [Stock Reconciliation](https://docs.frappe.io/erpnext/stock-reconciliation); [Immutable Ledger](https://docs.frappe.io/erpnext/immutable-ledger-in-erpnext); [Accounting Introduction](https://docs.frappe.io/erpnext/accounting-introduction).
- **Restaurant / POS:** [Odoo restaurant POS](https://www.odoo.com/documentation/18.0/applications/sales/point_of_sale/restaurant.html); [Odoo fiscal positions](https://www.odoo.com/documentation/18.0/applications/sales/point_of_sale/pricing/fiscal_position.html).
- **Browser isolation / open source:** [репозиторий Playwright](https://github.com/microsoft/playwright); [изоляция browser contexts](https://playwright.dev/docs/browser-contexts); [BrowserContext API](https://playwright.dev/docs/api/class-browsercontext).
- **Украинские правовые и официальные технические материалы:** [Закон №265/95-ВР](https://zakon.rada.gov.ua/laws/card/265/95-%D0%B2%D1%80); [API ПРРО ДПС](https://cabinet.tax.gov.ua/help/api.html); [FAQ ДПС по ПРРО](https://tax.gov.ua/baneryi/programni-rro/aktualni-zapitannya-vidpovidi/); [уведомление ДПС об обновлении API, 2025](https://tax.gov.ua/en/mass-media/news/874825.html); [Закон №851-IV об электронных документах](https://zakon.rada.gov.ua/laws/show/%D0%B5-%D0%B4%D0%BE%D0%BA%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D0%BE%D0%BE%D0%B1%D1%96%D0%B3).
- **Пример vendor integration:** [Checkbox API guide](https://wiki.checkbox.ua/uk/api/api_eng).
- **Agentic patterns:** библиография Stage 1.5 и контракты Stage 1.6 в этом репозитории; MCP security и публичные документы OpenAI/Anthropic/GitHub/Cursor указаны там.
