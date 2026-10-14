// Talks to the Forge Core API using the paths that Stage A verified to exist.
// Nothing is invented: if /api/status cannot be read, the interface says the backend is
// unreachable and every number is replaced by a dash.

export type Status = {
  core?: string;
  api?: string;
  provider_status?: string;
  providers_ready?: number;
  providers_total?: number;
  active_project?: string;
  timestamp?: string;
};

export type AccountStatus = {
  status?: string;
  balance_display?: string;
  balance_numeric?: number | null;
  quota_limit?: number | null;
  quota_usage?: number | null;
  alert?: string;
  last_checked?: string;
  details?: Record<string, unknown>;
};

export type Provider = {
  provider?: string;
  account_status?: AccountStatus;
  forge_spent?: number;
  total_requests?: number;
  total_runs?: number;
  total_attempts?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
  total_tokens?: number;
  estimated_cost?: number;
  provider_reported_cost?: number;
  effective_cost?: number;
  retry_count?: number;
  fallback_count?: number;
  success_count?: number;
  failure_count?: number;
};

export type Totals = {
  total_spent?: number;
  total_runs?: number;
  total_requests?: number;
  total_tokens?: number;
  avg_run_cost?: number;
  successful_runs?: number;
  failed_runs?: number;
};

export type Agent = {
  name?: string;
  provider?: string;
  model?: string;
  status?: string;
  capabilities?: string[];
};

export type Tool = { id?: string; name?: string; description?: string };

export type Capability = {
  capability_id?: string;
  domain?: string;
  name?: string;
  description?: string;
  metadata?: Record<string, unknown>;
};

export type Dashboard = {
  providers?: Provider[];
  models?: unknown[];
  agents?: unknown[];
  projects?: unknown[];
  runs?: unknown[];
  today_totals?: Totals;
  all_time_totals?: Totals;
  generated_at?: string;
};

export type RunRow = {
  run_id?: string;
  id?: string;
  goal?: string;
  title?: string;
  status?: string;
  tokens?: number;
  created_at?: string;
};

export type Runs = {
  project_id?: string;
  runs?: RunRow[];
  total_recorded?: number;
  truncated?: boolean;
};

export type Project = { project_id?: string; name?: string; path?: string; status?: string };

export type Onboarding = { project_id?: string; projects?: Project[] };

export type Source = { path: string; ok: boolean; detail: string };

export type BackendState =
  | { status: "loading" }
  | {
      status: "live";
      core: Status;
      dashboard: Dashboard;
      runs: Runs;
      onboarding: Onboarding;
      agents: Agent[];
      tools: Tool[];
      catalogue: Record<string, unknown> | null;
      capabilities: Capability[];
      notes: string[];
      sources: Source[];
    }
  | { status: "offline"; reason: string };

const getJson = async <T>(path: string): Promise<T> => {
  const response = await fetch(path, { headers: { accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} → HTTP ${response.status}`);
  }
  return (await response.json()) as T;
};

export const loadBackend = async (): Promise<BackendState> => {
  try {
    // /api/status is the authority on whether the core answers at all.
    const core = await getJson<Status>("/api/status");
    const notes: string[] = [];
    let dashboard: Dashboard = {};
    let runs: Runs = {};
    let onboarding: Onboarding = {};
    let agents: Agent[] = [];
    let tools: Tool[] = [];
    let capabilities: Capability[] = [];

    const attempt = async <T>(path: string, key: string, fallback: T): Promise<T> => {
      try {
        return await getJson<T>(path);
      } catch (error) {
        notes.push(`${key}: ${error instanceof Error ? error.message : String(error)}`);
        return fallback;
      }
    };

    dashboard = await attempt<Dashboard>("/api/dashboard", "dashboard", {});
    runs = await attempt<Runs>("/api/product/runs", "runs", {});
    onboarding = await attempt<Onboarding>("/api/product/onboarding", "onboarding", {});
    agents = await attempt<Agent[]>("/api/agents", "agents", []);
    tools = await attempt<Tool[]>("/api/tools", "tools", []);
    capabilities = await attempt<Capability[]>("/api/capabilities", "capabilities", []);
    const catalogue = await attempt<Record<string, unknown> | null>("/api/models", "models", null);

    const sources: Source[] = [
      { path: "/api/status", ok: true, detail: `core ${String(core.core ?? "?")}` },
      {
        path: "/api/dashboard",
        ok: !notes.some((note) => note.startsWith("dashboard:")),
        detail: `${Array.isArray(dashboard.providers) ? dashboard.providers.length : 0} providers`,
      },
      {
        path: "/api/agents",
        ok: !notes.some((note) => note.startsWith("agents:")),
        detail: `${agents.length} agents`,
      },
      {
        path: "/api/models",
        ok: !notes.some((note) => note.startsWith("models:")),
        detail: `${Array.isArray((catalogue as { models?: unknown[] } | null)?.models) ? (catalogue as { models: unknown[] }).models.length : 0} models`,
      },
      {
        path: "/api/tools",
        ok: !notes.some((note) => note.startsWith("tools:")),
        detail: `${tools.length} tools`,
      },
      {
        path: "/api/capabilities",
        ok: !notes.some((note) => note.startsWith("capabilities:")),
        detail: `${capabilities.length} capabilities`,
      },
      {
        path: "/api/product/runs",
        ok: !notes.some((note) => note.startsWith("runs:")),
        detail: `${typeof runs.total_recorded === "number" ? runs.total_recorded : 0} runs`,
      },
      {
        path: "/api/product/onboarding",
        ok: !notes.some((note) => note.startsWith("onboarding:")),
        detail: `${Array.isArray(onboarding.projects) ? onboarding.projects.length : 0} projects`,
      },
    ];
    return {
      status: "live",
      core,
      dashboard,
      runs,
      onboarding,
      agents,
      tools,
      capabilities,
      catalogue,
      notes,
      sources,
    };
  } catch (error) {
    return { status: "offline", reason: error instanceof Error ? error.message : String(error) };
  }
};
