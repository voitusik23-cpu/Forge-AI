// Talks to the Forge Core API using the paths that were verified to exist.
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

export type ProviderAccount = {
  status?: string;
  balance_display?: string;
  balance_numeric?: number | null;
};

export type Provider = {
  provider?: string;
  account_status?: ProviderAccount;
};

export type Dashboard = { providers?: Provider[] };

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

    try {
      dashboard = await getJson<Dashboard>("/api/dashboard");
    } catch (error) {
      notes.push(`dashboard: ${error instanceof Error ? error.message : String(error)}`);
    }
    try {
      runs = await getJson<Runs>("/api/product/runs");
    } catch (error) {
      notes.push(`runs: ${error instanceof Error ? error.message : String(error)}`);
    }
    try {
      onboarding = await getJson<Onboarding>("/api/product/onboarding");
    } catch (error) {
      notes.push(`onboarding: ${error instanceof Error ? error.message : String(error)}`);
    }
    const sources: Source[] = [
      { path: "/api/status", ok: true, detail: `core ${String(core.core ?? "?")}` },
      {
        path: "/api/dashboard",
        ok: !notes.some((note) => note.startsWith("dashboard:")),
        detail: `${Array.isArray(dashboard.providers) ? dashboard.providers.length : 0} providers`,
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
    return { status: "live", core, dashboard, runs, onboarding, notes, sources };
  } catch (error) {
    return { status: "offline", reason: error instanceof Error ? error.message : String(error) };
  }
};
