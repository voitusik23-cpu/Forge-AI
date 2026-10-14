// Custom stroke icons, drawn to match the design system: 24px grid, 1.6 stroke, round caps.
type IconProps = { size?: number; className?: string };

const base = (size: number) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
});

export const IconOverview = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <rect x="3" y="3" width="7" height="7" rx="2" />
    <rect x="14" y="3" width="7" height="7" rx="2" />
    <rect x="3" y="14" width="7" height="7" rx="2" />
    <rect x="14" y="14" width="7" height="7" rx="2" />
  </svg>
);

export const IconProjects = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5h3l2 2.5h7.5A2.5 2.5 0 0 1 20.5 10v7A2.5 2.5 0 0 1 18 19.5H5.5A2.5 2.5 0 0 1 3 17z" />
  </svg>
);

export const IconAgents = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="8" r="3.2" />
    <path d="M5 20c0-3.4 3.1-6 7-6s7 2.6 7 6" />
    <path d="M17.5 4.5l1.6 1.6M20.5 8h2" />
  </svg>
);

export const IconModels = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M12 3l7.5 4.3v8.4L12 20l-7.5-4.3V7.3z" />
    <path d="M12 12l7.5-4.3M12 12v8M12 12L4.5 7.7" />
  </svg>
);

export const IconRuns = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M4 12h4l2.5-6 3 12L16 12h4" />
  </svg>
);

export const IconTools = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M14.5 6.5a4 4 0 1 0 4.7 5.3L21 13.6 13.6 21 3 10.4V3h7.4l1 1.2a4 4 0 0 1 3.1 2.3z" />
  </svg>
);

export const IconUsage = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M4 19h16" />
    <path d="M7 19V9M12 19V5M17 19v-6" />
  </svg>
);

export const IconSettings = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.8v2.4M12 18.8v2.4M4.2 7l2 1.2M17.8 15.8l2 1.2M4.2 17l2-1.2M17.8 8.2l2-1.2" />
  </svg>
);

export const IconHelp = ({ size = 20, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.6 9.2a2.5 2.5 0 1 1 3.4 2.3c-.7.3-1 .9-1 1.6v.4" />
    <circle cx="12" cy="17" r=".6" fill="currentColor" />
  </svg>
);

export const IconSearch = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="M16 16l4 4" />
  </svg>
);

export const IconPlay = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M7 4.5l12 7.5-12 7.5z" />
  </svg>
);

export const IconRefresh = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M20 12a8 8 0 1 1-2.4-5.7" />
    <path d="M20 4v4h-4" />
  </svg>
);

export const IconChevron = ({ size = 16, className }: IconProps) => (
  <svg {...base(size)} className={className}>
    <path d="M6 9l6 6 6-6" />
  </svg>
);

export const ForgeMark = ({ size = 38 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 48 48" className="mark">
    <defs>
      <linearGradient id="forgeMark" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stopColor="#9a80ff" />
        <stop offset="55%" stopColor="#5b8cff" />
        <stop offset="100%" stopColor="#67e8f9" />
      </linearGradient>
    </defs>
    <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#forgeMark)" opacity="0.16" />
    <rect x="2.8" y="2.8" width="42.4" height="42.4" rx="12.4" stroke="url(#forgeMark)" strokeWidth="1.4" fill="none" />
    <path d="M15 33h18l-3.2-4.6H18.2z" fill="url(#forgeMark)" opacity="0.85" />
    <path d="M21.4 28.4V14.6l9.2 5.3z" fill="url(#forgeMark)" />
    <path d="M13.5 36.6h21" stroke="url(#forgeMark)" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);
