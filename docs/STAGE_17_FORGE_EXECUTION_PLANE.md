# Stage 17 — Forge Execution Plane v0.1

## Canonical Technical Documentation

### 1. Architecture Overview

Forge Execution Plane v0.1 establishes a bounded, process-isolated, single-node execution environment for engineering tasks. It operates strictly subordinate to the Forge Control Plane, fulfilling the core authority separation invariant:

$$\\text{Recommendation} \\neq \\text{Authorization} \\neq \\text{Approval} \\neq \\text{Execution} \\neq \\text{Verification}$$

The Execution Plane has zero autonomous authority. It cannot self-authorize, modify permissions, bypass approvals, alter project state, or elevate privilege.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                CONTROL PLANE                                │
│                                                                             │
│   AgentHarness / AIDecision                                                 │
│       │                                                                     │
│       ▼                                                                     │
│   ExecutionCoordinator                                                      │
│       ├── PermissionPolicy (allowed_commands, allowed_tools)                │
│       ├── ApprovalPolicy / ApprovalResolver (HUMAN_REQUIRED, gating)        │
│       └── ExecutionPolicy (profile limits, timeouts, output, path bounds)   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Authorized & Approved ExecutionRequest
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXECUTION PLANE (v0.1)                            │
│                                                                             │
│   ExecutionBackend Protocol (Contract)                                      │
│       └── LocalProcessExecutionBackend / LocalExecutionAdapter              │
│                                                                             │
│   1. Workspace Isolation: Ephemeral scratch directory (COPY semantics)     │
│   2. Process Isolation: Subprocess (shell=False, env whitelist, timeout)   │
│   3. Process Tree Lifecycle: Guaranteed termination & handle closure        │
│   4. Best-Effort Network: Environment poisoning & proxy nullification       │
│   5. Secret Handling: Scoped environment injection & stream redaction       │
│   6. Artifact Harvest: Structured extraction of declared run artifacts      │
│   7. Workspace Teardown: Mandatory filesystem cleanup                       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ ExecutionResult + Harvested Artifacts
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                                CONTROL PLANE                                │
│                                                                             │
│   WorkspaceVerifier / AcceptanceGate -> RunTrace Record                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2. Execution Lifecycle

Workspaces, credentials, and processes exist only during active command execution. No idle workspaces, suspended processes, or lingering credentials exist while awaiting approval.

1. **Decision**: `AIDecision` emitted by agent or harness.
2. **Permission Check**: `PermissionPolicy` validates whether the executable is permitted. If denied, exits immediately with zero execution footprint.
3. **Approval Gating**: `ApprovalPolicy` evaluates approval requirements. If `HUMAN_REQUIRED` or pending, stops immediately (`APPROVAL_WAITING`). No ephemeral workspace is created, no files are copied, no process is spawned, and no credentials are prepared.
4. **Ephemeral Workspace Allocation**: When authorized and approved, `EphemeralWorkspaceManager` creates a dedicated temporary scratch directory and stages input files using strict COPY semantics.
5. **Environment Scoping & Credential Injection**: Safe environment variable whitelist is constructed. Ephemeral run credentials from `ExecutionRequest.environment_variables` are injected and registered with `SecretRedactor`.
6. **Subprocess Invocation**: Subprocess is spawned (`shell=False`) in a new process group within the scratch directory. Timeout is enforced.
7. **Observation & Telemetry**: Exit code, duration, stdout, and stderr are captured.
8. **Process Tree Termination & Handle Closure**: Process tree is terminated (`taskkill /T /F` on Windows, process group signal on POSIX). File and pipe handles are verified closed.
9. **Artifact Harvesting**: Declared `artifact_targets` are validated (rejecting path traversal and symlinks), hashed with SHA-256, measured, typed, and collected into `ExecutionResult.artifacts`.
10. **Workspace Teardown**: Ephemeral scratch directory is deterministically deleted with Windows file-lock retry backoff.
11. **Verification & Acceptance**: Control Plane evaluates `WorkspaceVerifier` and `AcceptanceGate` independently.

### 3. Ephemeral Workspace Model

- **Root Isolation**: Execution operates within a temporary scratch root allocated via `tempfile.TemporaryDirectory(prefix="forge_scratch_")`.
- **COPY Semantics Only**: Staging copies files using standard file I/O (`shutil.copy2`). Symlinks, hardlinks, and copy-on-write optimizations are explicitly rejected as staging mechanisms in v0.1 to guarantee that host workspace inodes remain immutable.
- **Path Traversal Protection**: The working directory and artifact paths must resolve strictly within the scratch root. Any attempt to traverse (`..`) or escape the root is rejected with `POLICY_DENIED`.
- **Primary Workspace Immutability**: All modifications, intermediate builds, and temporary files occur strictly inside the scratch directory. The primary workspace is never mutated during execution.

### 4. Artifact Model

Durable work products are strictly distinguished from transient process observations:

- **Observation / Telemetry**: `stdout`, `stderr`, exit code, execution duration. Captured in `ExecutionResult` and audit events; discarded with workspace teardown.
- **Artifact**: A durable file created on disk matching a declared path in `ExecutionRequest.artifact_targets`. Harvested and verified before scratch directory deletion.

```python
@dataclass(frozen=True)
class Artifact:
    artifact_id: str             # Deterministic digest identifier
    run_id: str                  # Originating Run ID
    relative_path: str           # Path relative to scratch root
    media_type: str              # MIME type (e.g. text/plain, application/json)
    size_bytes: int              # Size in bytes
    sha256: str                  # Cryptographic SHA-256 digest
    provenance_source: str       # Command or tool ID that generated it
    created_at: str              # ISO-8601 UTC timestamp
    metadata: Mapping[str, object] # Safe metadata dictionary
```

Enforcement guarantees:
- Path must be relative and cannot contain `..` or escape the root.
- Symlinks are rejected.
- Size limit is enforced (default 10 MB per artifact).
- Traceable to `run_id` and SHA-256 digest.

### 5. Credential Handling

- **No Disk Persistence**: Credentials and tokens are never written to disk or recorded in persistent project state.
- **Run-Scoped Injection**: Injected solely via child process environment variables in `ExecutionRequest.environment_variables`.
- **Automatic Output Redaction**: Injected secret values and common token patterns are registered with `SecretRedactor` and replaced with `[REDACTED]` in all output buffers (`stdout`, `stderr`, metadata).
- **Process Lifetime Binding**: Environment variables exist only in child process memory and are destroyed upon process termination.

### 6. Network Posture and Limitations

- **Default Posture**: `network_access=False` (offline execution).
- **Best-Effort Local Enforcement**: In v0.1, network restriction is enforced via environment proxy poisoning (`http_proxy=http://127.0.0.1:0`, `https_proxy=http://127.0.0.1:0`, `all_proxy=http://127.0.0.1:0`, `NO_PROXY=""`) and pre-flight binary inspection.
- **Security Boundary Clarification**: This is **best-effort local enforcement**, NOT physical network namespace isolation. Arbitrary binaries creating raw network sockets are not blocked at the OS kernel level in v0.1. True OS/container network namespace isolation is reserved for containerized execution backends.

### 7. Process Lifecycle & Windows Compatibility

Process management is a mandatory lifecycle contract:

```
[Timeout or Cancel Triggered]
             │
             ▼
1. Terminate Process Tree
   - Windows: taskkill /PID <pid> /T /F
   - POSIX: SIGTERM -> wait(0.2s) -> SIGKILL to process group
             │
             ▼
2. Verify Termination
   - Verify proc.poll() is not None; kill fallback
             │
             ▼
3. Close Pipe Handles
   - Explicitly close proc.stdout and proc.stderr
             │
             ▼
4. Harvest Artifacts (if exit condition allows)
             │
             ▼
5. Cleanup Ephemeral Workspace
   - Bounded exponential retry (up to 5 attempts) to absorb Windows file locks
```

### 8. Pluggable Execution Backends

The Control Plane interacts with execution runners via the minimal `ExecutionBackend` protocol:

```python
class ExecutionBackend(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult: ...
```

- **v0.1 Implementation**: `LocalProcessExecutionBackend` (alias `LocalExecutionAdapter`). Executes bounded local processes inside ephemeral scratch directories.
- **Future Compatible Backends**:
  - Container Execution Backend (Docker / Podman with `--network none`).
  - Remote Execution Backend (Isolated worker nodes).
  - Cloud Execution Backend (Ephemeral container pods).

### 9. External Benchmark Evaluation

| Feature / Mechanism | Source | Status | Forge Architectural Disposition |
| :--- | :--- | :--- | :--- |
| **Local Agent (bounded role)** | Kimi Work | **ADAPT** | Forge harness roles remain bounded and non-autonomous. |
| **Local Workspace Isolation** | Kimi Work | **ADAPT** | Ephemeral scratch directory isolation alongside primary persistent workspace root. |
| **Browser / WebBridge** | Kimi Work | **KEEP / LATER** | Interactive web browsing reserved for future browser-testing skills; out of scope for v0.1 backend execution. |
| **Computer Use / GUI Automation** | Kimi Work | **KEEP / LATER** | Privileged OS desktop automation is deferred to future dedicated environments with strict human-in-the-loop controls. |
| **Macro Goal Engine** | Kimi Work | **KEEP / LATER** | Multi-step macro planning belongs in future orchestrator stages; not low-level execution plane. |
| **Agent Swarm Coordination** | Kimi Work | **KEEP / LATER** | Multi-agent coordination deferred until single-agent execution plane is validated. |
| **Cron / Task Scheduler** | Kimi Work | **KEEP / LATER** | Standing background scheduler belongs in future control plane lifecycle. |
| **Ephemeral Worker Workspace** | Runnable | **ADOPT** | Allocate dedicated temporary scratch directories per run with guaranteed cleanup on termination. |
| **Scoped Tool/MCP Identifiers** | Runnable | **ADAPT** | Treat tool IDs and scopes as declarative advisory contracts; authorization remains strictly with `PermissionPolicy`. |
| **Scoped Ephemeral Credentials** | Runnable | **ADAPT** | Inject secrets strictly as child environment variables; scrub all output buffers; zero disk persistence. |
| **Explicit Run Artifacts** | Runnable | **ADAPT** | Distinguish between ephemeral telemetry logs and harvested durable output files bound to `run_id`. |
| **Guaranteed Workspace Cleanup** | Runnable | **ADOPT** | Always wipe scratch directories on exit via deterministic lifecycle context. |
| **Live Canvas / Frontend Preview** | Lovable / Canva | **KEEP / LATER** | Visual frontend preview and hot-reload canvas belong to future UI presentation layers. |

---

# Среда выполнения Forge v0.1 — русская версия

### 1. Архитектурный обзор

Среда выполнения (Execution Plane) Forge v0.1 предоставляет изолированную среду выполнения инженерных задач на одном узле на уровне процессов. Она строго подчинена плоскости управления (Control Plane) Forge и соблюдает фундаментальный инвариант разделения полномочий:

$$\\text{Рекомендация} \\neq \\text{Авторизация} \\neq \\text{Утверждение} \\neq \\text{Выполнение} \\neq \\text{Верификация}$$

Среда выполнения обладает нулевыми автономными полномочиями. Она не может самостоятельно авторизовать действия, изменять права доступа, обходить согласования, менять состояние проекта или повышать привилегии.

### 2. Жизненный цикл выполнения

Рабочие области, учетные данные и процессы существуют исключительно во время активного выполнения команды. В период ожидания согласования человека не создается никаких временных каталогов, не запускается фоновых процессов и не подготавливается учетных данных:

1. **Решение (Decision)**: Агент или харнесс генерирует `AIDecision`.
2. **Проверка прав (Permission Check)**: `PermissionPolicy` проверяет разрешение на запуск исполняемого файла. При отказе выполнение завершается немедленно без создания рабочих сред.
3. **Согласование (Approval Gating)**: `ApprovalPolicy` проверяет необходимость согласования. Если требуется одобрение (`APPROVAL_WAITING`), процесс останавливается. Никаких временных рабочих областей не создается, файлы не копируются, учетные данные не передаются.
4. **Создание временной рабочей области**: При наличии авторизации и согласования `EphemeralWorkspaceManager` выделяет изолированный временный каталог и копирует входные файлы, используя строгую семантику COPY.
5. **Ограничение окружения и передача учетных данных**: Формируется белый список безопасных системных переменных окружения. Учетные данные из `ExecutionRequest.environment_variables` передаются процессу и регистрируются в `SecretRedactor`.
6. **Запуск процесса**: Запускается подпроцесс (`shell=False`) в новой группе процессов внутри изолированного каталога с контролем таймаута.
7. **Сбор телеметрии**: Фиксируются код завершения, длительность, потоки `stdout` и `stderr`.
8. **Завершение дерева процессов**: Дерево процессов гарантированно завершается (`taskkill /T /F` в Windows, сигналы группе процессов в POSIX), дескрипторы закрываются.
9. **Сбор артефактов (Artifact Harvest)**: Заявленные целевые артефакты (`artifact_targets`) проверяются на безопасность путей, хэшируются по алгоритму SHA-256, типизируются и собираются в `ExecutionResult.artifacts`.
10. **Очистка рабочей области**: Временный каталог удаляется с поддержкой повторных попыток при блокировках файлов в Windows.
11. **Верификация и приемка**: Контрольная плоскость независимо оценивает результаты через `WorkspaceVerifier` и `AcceptanceGate`.

### 3. Модель изолированной рабочей области

- **Изоляция корня**: Команда выполняется во временном каталоге с префиксом `forge_scratch_`.
- **Семантика COPY**: Исходные файлы копируются напрямую (`shutil.copy2`). Символические ссылки, жесткие ссылки и оптимизации copy-on-write в v0.1 явно запрещены для гарантии неизменности основной рабочей области проекта.
- **Защита от выхода за границы путей**: Рабочий каталог команды и пути артефактов должны строго находиться внутри изолированного корня. Любые попытки перехода (`..`) блокируются с ошибкой `POLICY_DENIED`.
- **Неизменность основной рабочей области**: Все изменения, промежуточные файлы и сборки происходят исключительно во временном каталоге.

### 4. Модель артефактов

Долговечные результаты работы строго отделены от кратковременных наблюдений выполнения:

- **Наблюдение / Телеметрия**: `stdout`, `stderr`, код возврата, длительность. Сохраняются в `ExecutionResult` и аудит-трейсе; удаляются вместе с рабочей областью.
- **Артефакт**: Долговечный файл, созданный на диске и соответствующий объявленному пути в `ExecutionRequest.artifact_targets`. Извлекается до очистки временного каталога.

Каждый артефакт неизменяем, привязан к `run_id`, содержит размер, MIME-тип и контрольную сумму SHA-256.

### 5. Работа с учетными данными

- **Запрет сохранения на диск**: Учетные данные никогда не записываются в файлы и не сохраняются в состоянии проекта.
- **Передача в рамках запуска**: Секреты передаются только как переменные окружения дочернего процесса.
- **Маскирование в выводе**: Все переданные секретные значения регистрируются в `SecretRedactor` и заменяются на `[REDACTED]` во всех выходных потоках.

### 6. Сетевой режим и ограничения

- **Режим по умолчанию**: `network_access=False` (автономное выполнение).
- **Ограничение на уровне best-effort**: В v0.1 ограничение сети реализуется через блокировку прокси (`http_proxy=http://127.0.0.1:0`) и фильтрацию переменных окружения.
- **Граница безопасности**: Это ограничение уровня **best-effort**, а не физическая сетевая изоляция на уровне пространств имен ядра ОС. Настоящая сетевая изоляция контейнеров зарезервирована для будущих бэкендов.

### 7. Жизненный цикл процессов и поддержка Windows

Гарантированное завершение дерева процессов является обязательным контрактом. В Windows используется `taskkill /PID <pid> /T /F` для предотвращения зависших дочерних процессов компиляторов и тестов, после чего закрываются дескрипторы и выполняется очистка каталога с механизмом повторных попыток при блокировках.

### 8. Подключаемые бэкенды выполнения

Плоскость управления взаимодействует со средой выполнения через протокол `ExecutionBackend`:

- **Реализация v0.1**: `LocalProcessExecutionBackend` (псевдоним `LocalExecutionAdapter`). Выполняет локальные процессы в изолированных временных каталогах.
- **Будущие бэкенды**: Контейнерное выполнение (Docker/Podman), удаленные воркеры, облачные изолированные контейнеры.

### 9. Решения по внешним бенчмаркам

- **Kimi Local Agent** = ADAPT (адаптировано в виде ограниченной роли харнесса)
- **Kimi Local Workspace Isolation** = ADAPT (адаптировано в виде временного scratch-каталога)
- **Kimi Browser / WebBridge** = KEEP / LATER (сохранено для будущих задач тестирования интерфейсов)
- **Kimi Computer Use** = KEEP / LATER (сохранено для будущих сред с полным человеческим контролем)
- **Kimi Macro Goal Engine** = KEEP / LATER (сохранено для макро-оркестратора)
- **Kimi Swarm Coordination** = KEEP / LATER (сохранено для многоагентного взаимодействия)
- **Kimi Scheduler** = KEEP / LATER (сохранено для планировщика задач)
- **Runnable Ephemeral Worker Workspace** = ADOPT (принято: временная рабочая область на каждый запуск)
- **Runnable Scoped Tool/MCP Identifiers** = ADAPT (адаптировано: декларативные идентификаторы инструментов)
- **Runnable Scoped Ephemeral Credentials** = ADAPT (адаптировано: передача через env дочернего процесса и маскирование)
- **Runnable Explicit Run Artifacts** = ADAPT (адаптировано: сбор артефактов с привязкой к run_id и SHA-256)
- **Runnable Guaranteed Cleanup** = ADOPT (принято: гарантированное удаление временных рабочих областей)
- **Lovable / Canva Live Canvas** = KEEP / LATER (сохранено для будущих визуальных уровней интерфейса)
