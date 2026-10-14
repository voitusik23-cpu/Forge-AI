import type { ReactNode } from "react";

export const Card = ({
  children,
  className = "",
  tight = false,
}: {
  children: ReactNode;
  className?: string;
  tight?: boolean;
}) => <section className={`card${tight ? " tight" : ""} ${className}`}>{children}</section>;

export const CardHead = ({
  title,
  action,
  hint,
}: {
  title: string;
  action?: ReactNode;
  hint?: string;
}) => (
  <header className="card-head">
    <div>
      <div className="card-title">{title}</div>
      {hint ? <div className="small muted" style={{ marginTop: 2 }}>{hint}</div> : null}
    </div>
    {action}
  </header>
);

export const Badge = ({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "danger" | "info" | "neutral";
}) => <span className={`badge ${tone}`}>{children}</span>;

export const StatusDot = ({ tone = "idle" }: { tone?: "ok" | "warn" | "danger" | "idle" }) => (
  <span className={`dot ${tone}`} />
);

export const Button = ({
  children,
  tone = "default",
  size = "md",
  wide = false,
  disabled = false,
  onClick,
  type = "button",
}: {
  children: ReactNode;
  tone?: "default" | "primary" | "ghost" | "danger";
  size?: "md" | "small";
  wide?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  type?: "button" | "submit";
}) => (
  <button
    type={type}
    disabled={disabled}
    onClick={onClick}
    className={`btn${tone === "default" ? "" : ` ${tone}`}${size === "small" ? " small" : ""}${
      wide ? " wide" : ""
    }`}
  >
    {children}
  </button>
);

export const Field = ({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}) => (
  <label className="field">
    <span className="label">{label}</span>
    {children}
    {error ? <span className="error">{error}</span> : hint ? <span className="hint">{hint}</span> : null}
  </label>
);

export const Meter = ({ value }: { value: number }) => (
  <div className="meter" role="progressbar" aria-valuenow={Math.round(value * 100)}>
    <span style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
  </div>
);

// A sparkline with no chart library: the data is real, the geometry is ours.
export const Sparkline = ({
  points,
  tone = "violet",
}: {
  points: number[];
  tone?: "violet" | "cyan" | "ok" | "danger";
}) => {
  const width = 220;
  const height = 34;
  if (points.length < 2) {
    return <svg className="spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true" />;
  }
  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((value, index) => `${index * step},${height - ((value - min) / span) * (height - 6) - 3}`)
    .join(" ");
  const strokes: Record<string, string> = {
    violet: "#9a80ff",
    cyan: "#67e8f9",
    ok: "#34d399",
    danger: "#fb7185",
  };
  const colour = strokes[tone];
  return (
    <svg className="spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={`spark-${tone}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={colour} stopOpacity="0.42" />
          <stop offset="100%" stopColor={colour} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polyline points={path} fill="none" stroke={colour} strokeWidth="1.8" strokeLinejoin="round" />
      <polygon points={`0,${height} ${path} ${width},${height}`} fill={`url(#spark-${tone})`} />
    </svg>
  );
};
