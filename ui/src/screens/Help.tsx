import { useEffect, useState } from "react";
import { Button, Card, CardHead } from "../components/Primitives";
import { VersionLine } from "../components/VersionLine";
import { HELP_TOPICS, HELP_TOPIC_BY_ID } from "../help-content";
import { t, type Lang } from "../i18n";
import type { Status } from "../api/client";

export const Help = ({ lang, topic, status }: { lang: Lang; topic: string; status: Status | null }) => {
  const [active, setActive] = useState(topic || HELP_TOPICS[0].id);

  useEffect(() => {
    setActive(topic || HELP_TOPICS[0].id);
  }, [topic]);

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
      </div>
    </div>
  );
};
