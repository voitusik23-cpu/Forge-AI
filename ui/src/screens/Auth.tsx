import { useState } from "react";
import { ForgeMark } from "../components/Icons";
import { Button, Card, Field } from "../components/Primitives";
import { t, type Lang } from "../i18n";

type Step = "email" | "code" | "done";

export const Auth = ({
  lang,
  onLanguage,
  onEnter,
}: {
  lang: Lang;
  onLanguage: (lang: Lang) => void;
  onEnter: () => void;
}) => {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [notice, setNotice] = useState<{ tone: "info" | "warn" | "danger" | "ok"; text: string } | null>(null);

  const sendCode = () => {
    if (!email.includes("@")) {
      setNotice({ tone: "danger", text: lang === "ru" ? "Укажите адрес почты" : "Enter an email address" });
      return;
    }
    setStep("code");
    // The Python account store is the authority; this screen only shows what it returns.
    setNotice({ tone: "warn", text: t(lang, "auth.local") });
  };

  const grant = () => {
    setStep("done");
    onEnter();
  };

  return (
    <div className="auth">
      <div className="auth-brand">
        <span className="halo" />
        <span className="halo two" />
        <div className="row" style={{ gap: 14, position: "relative" }}>
          <ForgeMark size={44} />
          <div>
            <div style={{ fontWeight: 700, letterSpacing: "0.18em" }}>FORGE AI</div>
            <div className="small muted">INTELLIGENT COMMAND CENTER</div>
          </div>
        </div>

        <div style={{ position: "relative" }}>
          <div className="slogan">
            {lang === "ru" ? (
              <>
                Кузница идей. <em>Сталь результата.</em>
              </>
            ) : (
              <>
                Where ideas are <em>forged into results.</em>
              </>
            )}
          </div>
          <ul className="stack small muted" style={{ marginTop: 26, paddingLeft: 18 }}>
            <li>{t(lang, "auth.point1")}</li>
            <li>{t(lang, "auth.point2")}</li>
            <li>{t(lang, "auth.point3")}</li>
          </ul>
        </div>

        <div className="row between small muted" style={{ position: "relative" }}>
          <span>2026 Forge AI</span>
          <span>loopback · 127.0.0.1</span>
        </div>
      </div>

      <div className="auth-form">
        <div className="auth-card rise">
          <div className="row between" style={{ marginBottom: 22 }}>
            <div>
              <div style={{ fontSize: "var(--t-title)", fontWeight: 700 }}>{t(lang, "auth.title")}</div>
              <div className="small muted" style={{ marginTop: 4 }}>
                {t(lang, "auth.subtitle")}
              </div>
            </div>
            <div className="segment">
              <button className={lang === "ru" ? "on" : ""} onClick={() => onLanguage("ru")}>
                RU
              </button>
              <button className={lang === "en" ? "on" : ""} onClick={() => onLanguage("en")}>
                EN
              </button>
            </div>
          </div>

          <Card>
            <div className="stack" style={{ gap: 16 }}>
              <Field label={t(lang, "auth.email")} hint="owner@forge.ai">
                <input
                  className="input"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  placeholder="you@example.com"
                  disabled={step !== "email"}
                />
              </Field>

              {step === "email" ? (
                <Button tone="primary" wide onClick={sendCode}>
                  {t(lang, "auth.send")}
                </Button>
              ) : (
                <>
                  <Field label={t(lang, "auth.code")} hint="6">
                    <input
                      className="input code"
                      value={code}
                      maxLength={6}
                      onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                      placeholder="------"
                    />
                  </Field>
                  <Button tone="primary" wide disabled={code.length !== 6} onClick={grant}>
                    {t(lang, "auth.confirm")}
                  </Button>
                </>
              )}

              {notice ? <div className={"notice " + notice.tone}>{notice.text}</div> : null}

              <div className="row" style={{ gap: 10, marginTop: 4 }}>
                <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
                <span className="label">{t(lang, "auth.dev")}</span>
                <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
              </div>

              <Button wide onClick={grant}>
                {t(lang, "auth.dev")}
              </Button>
              <div className="hint small muted">{t(lang, "auth.dev.hint")}</div>

              {step === "done" ? (
                <div className="notice ok">
                  {lang === "ru"
                    ? "Доступ разрешён. Экран Overview — в этой же визуальной системе."
                    : "Access granted. The Overview screen follows the same visual system."}
                </div>
              ) : null}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
};
