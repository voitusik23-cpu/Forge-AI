// Tools & capabilities: the section whose navigation entry previously rendered an empty area.
//
// Sources, and nothing else:
//   * /api/tools        -> the tools the registry declares: id, name, description.
//   * /api/capabilities -> the capabilities the runtime reports: capability_id, domain, name,
//     description, and a metadata object that is rendered verbatim rather than interpreted.
//
// Honesty rules:
//   1. an empty list is shown as "the registry declares none", never as an invented entry;
//   2. a tool is a declaration, not proof that it will run for a given task -- the screen says so;
//   3. no tool is described as available when the backend is offline: the reason is shown instead;
//   4. nothing is inferred from a name: the description comes from the API or is shown as absent.

import { useState } from "react";
import { Badge, Button, Card, CardHead } from "../components/Primitives";
import { IconRefresh, IconSearch } from "../components/Icons";
import { t, type Lang } from "../i18n";
import type { BackendState, Capability, Tool } from "../api/client";

type ToolsProps = {
  lang: Lang;
  backend: BackendState;
  onRefresh: () => void;
};

export const Tools = ({ lang, backend, onRefresh }: ToolsProps) => {
  const live = backend.status === "live";
  const tools: Tool[] = live && Array.isArray(backend.tools) ? backend.tools : [];
  const capabilities: Capability[] = live && Array.isArray(backend.capabilities)
    ? backend.capabilities
    : [];
  const [query, setQuery] = useState("");

  const unknown = t(lang, "runs.unknown");
  const needle = query.trim().toLowerCase();
  const matches = (value: unknown): boolean =>
    needle === "" || String(value ?? "").toLowerCase().includes(needle);

  const visibleTools = tools.filter(
    (item) => matches(item.id) || matches(item.name) || matches(item.description)
  );
  const visibleCapabilities = capabilities.filter(
    (item) =>
      matches(item.capability_id) || matches(item.name) || matches(item.domain) ||
      matches(item.description)
  );

  return (
    <div className="content">
      <div className="row between" style={{ marginBottom: 18 }}>
        <div>
          <h1>{t(lang, "tools.title")}</h1>
          <div className="small muted" style={{ marginTop: 4 }}>
            {t(lang, "tools.subtitle")}
          </div>
        </div>
        <Button onClick={onRefresh} tone="default">
          <IconRefresh size={15} /> {t(lang, "action.refresh")}
        </Button>
      </div>

      {backend.status === "offline" ? (
        <div className="notice warn" style={{ marginBottom: 16 }}>
          {t(lang, "tools.offline")} {backend.reason}
        </div>
      ) : null}

      <div className="grid cols-3" style={{ marginBottom: 16 }}>
        <Card tight className="rise">
          <div className="kpi">
            <span className="label">{t(lang, "tools.kpi.tools")}</span>
            <span className="value mono">{live ? tools.length : unknown}</span>
            <span className="small muted mono">/api/tools</span>
          </div>
        </Card>
        <Card tight className="rise">
          <div className="kpi">
            <span className="label">{t(lang, "tools.kpi.capabilities")}</span>
            <span className="value mono">{live ? capabilities.length : unknown}</span>
            <span className="small muted mono">/api/capabilities</span>
          </div>
        </Card>
        <Card tight className="rise">
          <div className="kpi">
            <span className="label">{t(lang, "tools.kpi.kind")}</span>
            <span className="value mono">{t(lang, "tools.kpi.declared")}</span>
            <span className="small muted mono">{t(lang, "tools.declaration.note")}</span>
          </div>
        </Card>
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 16, alignItems: "center" }}>
        <IconSearch size={15} />
        <input
          className="input"
          placeholder={t(lang, "tools.search")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>

      <Card>
        <CardHead title={t(lang, "tools.table.title")} hint={`/api/tools · ${tools.length}`} />
        <table className="table">
          <thead>
            <tr>
              <th>{t(lang, "tools.col.id")}</th>
              <th>{t(lang, "tools.col.name")}</th>
              <th>{t(lang, "tools.col.description")}</th>
            </tr>
          </thead>
          <tbody>
            {visibleTools.map((item) => (
              <tr key={String(item.id ?? item.name)}>
                <td className="mono">{String(item.id ?? unknown)}</td>
                <td>
                  <Badge tone="neutral">{String(item.name ?? unknown)}</Badge>
                </td>
                <td className="small">
                  {String(item.description ?? "") === ""
                    ? t(lang, "tools.description.none")
                    : String(item.description)}
                </td>
              </tr>
            ))}
            {visibleTools.length === 0 ? (
              <tr>
                <td colSpan={3} className="muted">
                  {tools.length === 0 ? t(lang, "tools.table.empty") : t(lang, "tools.table.noMatch")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </Card>

      <div style={{ marginTop: 16 }}>
        <Card>
          <CardHead
            title={t(lang, "tools.caps.title")}
            hint={`/api/capabilities · ${capabilities.length}`}
          />
          <table className="table">
            <thead>
              <tr>
                <th>{t(lang, "tools.col.capability")}</th>
                <th>{t(lang, "tools.col.domain")}</th>
                <th>{t(lang, "tools.col.name")}</th>
                <th>{t(lang, "tools.col.description")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleCapabilities.map((item) => (
                <tr key={String(item.capability_id ?? item.name)}>
                  <td className="mono">{String(item.capability_id ?? unknown)}</td>
                  <td className="mono">{String(item.domain ?? unknown)}</td>
                  <td>
                    <Badge tone="neutral">{String(item.name ?? unknown)}</Badge>
                  </td>
                  <td className="small">
                    {String(item.description ?? "") === ""
                      ? t(lang, "tools.description.none")
                      : String(item.description)}
                  </td>
                </tr>
              ))}
              {visibleCapabilities.length === 0 ? (
                <tr>
                  <td colSpan={4} className="muted">
                    {capabilities.length === 0
                      ? t(lang, "tools.caps.empty")
                      : t(lang, "tools.table.noMatch")}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </Card>
      </div>

      <div className="notice" style={{ marginTop: 16 }}>
        {t(lang, "tools.note")}
      </div>
    </div>
  );
};
