import { useEffect, useState } from "react";
import { Button, Card, CardHead } from "../components/Primitives";
import { VersionLine } from "../components/VersionLine";
import { HELP_TOPICS, HELP_TOPIC_BY_ID } from "../help-content";
import { t, type Lang } from "../i18n";
import type { Status } from "../api/client";

/** Where the owner's manual lives, repository-relative. Never an absolute host path. */
const MANUAL_PATH = "/docs/USER_MANUAL.md";

export const Help = ({ lang, topic, status }: { lang: Lang; topic: string; status: Status | null }) => {
  const [active, setActive] = useState(topic || HELP_TOPICS[0].id);
  // Whether the shell server actually serves the manual. A link that answers 404 is worse than
  // no link, so the card asks once and states the answer instead of promising a page.
  const [manualServed, setManualServed] = useState<boolean | null>(null);

  useEffect(() => {
    setActive(topic || HELP_TOPICS[0].id);
  }, [topic]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const response = await fetch(MANUAL_PATH, { headers: { accept: "text/markdown, text/plain" } });
        if (alive) setManualServed(response.ok);
      } catch {
        if (alive) setManualServed(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const current = HELP_TOPIC_BY_ID(active) ?? HELP_TOPICS[0];

  return (
    <div className="content">
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ margin: 0, fontSize: "var(--t-title)" }}>{t(lang, "help.title")}</h1>
        <div className="small muted" style={{ marginTop: 4 }}>
          {t(lang, "help.subtitle")}
        </div>
        {/* Which build this guide belongs to: version · short commit, with the backend and UI
            build times beside them. The values are the ones /api/status.details carries. */}
        <div style={{ marginTop: 6 }}>
          <VersionLine lang={lang} status={status} detailed />
        </div>
      </div>

      <div className="grid overview">
        <div className="stack">
          <Card className="rise">
            <CardHead title={current.title[lang]} hint={t(lang, "help.topic")} />
            <div className="stack">
              {current.body[lang].map((paragraph, index) => (
                <p key={index} className="small" style={{ margin: 0, lineHeight: 1.65 }}>
                  {paragraph}
                </p>
              ))}
            </div>
            <div className="row" style={{ gap: 8, marginTop: 16 }}>
              <Button
                size="small"
                disabled={HELP_TOPICS[0].id === current.id}
                onClick={() => setActive(HELP_TOPICS[Math.max(0, HELP_TOPICS.indexOf(current) - 1)].id)}
              >
                {t(lang, "help.prev")}
              </Button>
              <Button
                size="small"
                disabled={HELP_TOPICS[HELP_TOPICS.length - 1].id === current.id}
                onClick={() =>
                  setActive(
                    HELP_TOPICS[Math.min(HELP_TOPICS.length - 1, HELP_TOPICS.indexOf(current) + 1)].id
                  )
                }
              >
                {t(lang, "help.next")}
              </Button>
            </div>
          </Card>
        </div>

        <Card className="rise d1">
          <CardHead title={t(lang, "help.contents")} hint={`${HELP_TOPICS.length}`} />
          <div className="stack" style={{ gap: 2 }}>
            {HELP_TOPICS.map((item) => (
              <button
                key={item.id}
                onClick={() => setActive(item.id)}
                className={`nav-item${item.id === current.id ? " active" : ""}`}
              >
                <span className="label">{item.title[lang]}</span>
              </button>
            ))}
          </div>
        </Card>

        {/* The owner's manual: one link, and an honest statement when the shell cannot serve it. */}
        <Card className="rise d2" tight>
          <CardHead title={t(lang, "help.manual.title")} hint={t(lang, "help.manual.hint")} />
          <div className="stack small" style={{ gap: 8 }}>
            <a href={MANUAL_PATH} target="_blank" rel="noopener noreferrer" className="mono small">
              {t(lang, "help.manual.open")}
            </a>
            <div className="muted">{t(lang, "help.manual.note")}</div>
            {manualServed === false ? (
              <div className="notice warn">{t(lang, "help.manual.missing")}</div>
            ) : null}
          </div>
        </Card>
      </div>
    </div>
  );
};
