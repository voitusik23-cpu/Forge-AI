import { Badge, Button, Card, CardHead, StatusDot } from "../components/Primitives";
import { IconRefresh } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState } from "../api/client";

const text = (value: unknown, fallback = "—"): string =>
  value === undefined || value === null || value === "" ? fallback : String(value);

export const Projects = ({
  lang,
  backend,
  onRefresh,
  onHelp,
}: {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
  onHelp: () => void;
}) => {
  const live = backend.status === "live";
  const projects = live && Array.isArray(backend.onboarding.projects) ? backend.onboarding.projects : [];
  const active = live ? String(backend.core.active_project ?? "") : "";

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "nav.projects")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "projects.subtitle")}
          </div>
        </div>
        <div className="row">
          <Button size="small" tone="ghost" onClick={onHelp}>
            {t(lang, "help.how")}
          </Button>
          <Button onClick={onRefresh}>
            <IconRefresh size={15} /> {t(lang, "action.refresh")}
          </Button>
        </div>
      </div>

      {!live ? <div className="notice warn" style={{ marginBottom: 16 }}>{t(lang, "state.offline")}</div> : null}

      <Card className="rise">
        <CardHead
          title={t(lang, "nav.projects")}
          hint={`/api/projects · ${projects.length}`}
          action={<Badge tone={live ? "info" : "neutral"}>{live ? "LIVE" : "OFFLINE"}</Badge>}
        />
        {projects.length === 0 ? (
          <div className="small muted">{t(lang, "projects.empty")}</div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t(lang, "projects.col.id")}</th>
                <th>{t(lang, "projects.col.name")}</th>
                <th>{t(lang, "projects.col.path")}</th>
                <th>{t(lang, "projects.col.status")}</th>
                <th>{t(lang, "projects.col.active")}</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => {
                const id = String(project.project_id ?? "");
                return (
                  <tr key={id}>
                    <td className="mono small">{text(project.project_id)}</td>
                    <td style={{ fontWeight: 600 }}>{text(project.name)}</td>
                    <td className="mono small" style={{ maxWidth: 380, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {text(project.path)}
                    </td>
                    <td>
                      <Badge tone={project.status === "active" ? "ok" : "neutral"}>
                        {String(project.status ?? "UNKNOWN").toUpperCase()}
                      </Badge>
                    </td>
                    <td>
                      {id === active ? (
                        <span className="row" style={{ gap: 8 }}>
                          <StatusDot tone="ok" />
                          <span className="small">{t(lang, "projects.active")}</span>
                        </span>
                      ) : (
                        <span className="small muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="rise d1" tight>
        <div className="stack small">
          <div className="row between">
            <span className="muted">{t(lang, "projects.create.gap")}</span>
            <Badge tone="warn">{t(lang, "projects.create.noroute")}</Badge>
          </div>
          {/* No create control is drawn at all: a disabled button would imply that a route exists
              and is merely switched off. The card states the fact and names what does exist. */}
          <div className="muted">{t(lang, "projects.create.reason")}</div>
          <div>
            <div className="label">{t(lang, "projects.create.routes")}</div>
            <div className="stack mono small" style={{ gap: 4, marginTop: 6 }}>
              {[
                "GET /api/status",
                "GET /api/projects",
                "GET /api/agents",
                "GET /api/models",
                "GET /api/runs/{run_id}",
                "POST /api/tasks/run",
              ].map((route) => (
                <span key={route} className="muted">
                  {route}
                </span>
              ))}
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
};
