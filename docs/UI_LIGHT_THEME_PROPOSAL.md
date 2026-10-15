# Light theme proposal — for approval, not implemented

Status: **proposal only.** Nothing in this document has been implemented, and no screen was
redesigned. The current dark theme stays the default and remains available; RU/EN support is
unchanged. Implementation starts only after the owner approves this direction.

## Direction

A refined, premium light interface: **not** a stark white page, but warm off-white surfaces with
**very light lime** used sparingly as the brand accent. Soft, rounded forms with controlled radii,
subtle borders, restrained shadows, and a consistent component system, so the product reads as one
coherent surface rather than a collection of cards.

## 1. Tokens

| Token | Light value | Purpose |
|---|---|---|
| `--bg` | `#F7F8F6` | page background — warm off-white, never `#FFFFFF` |
| `--surface-1` | `#FFFFFF` | cards and panels |
| `--surface-2` | `#F1F4EF` | insets, table headers, inactive tabs |
| `--surface-3` | `#E9EDE7` | hover on `--surface-2`, code blocks |
| `--border` | `#E2E6DF` | default 1px separator |
| `--border-strong` | `#D3D9CE` | focused inputs, selected rows |
| `--text` | `#1B2019` | primary text (contrast ≥ 12:1 on `--bg`) |
| `--text-muted` | `#5A6358` | secondary text (contrast ≥ 5:1) |
| `--accent` | `#7FA83C` | light lime — the only brand colour in the light theme |
| `--accent-hover` | `#6E9433` | hover |
| `--accent-soft` | `#EEF5E2` | tinted fills, selected navigation item |
| `--accent-border` | `#C9DDA6` | tinted borders |
| `--ok` / `--warn` / `--danger` | `#3F7A33` / `#9A6B12` / `#A63A2E` | status text, chosen for contrast on light surfaces |
| `--shadow-1` | `0 1px 2px rgb(24 32 20 / 6%)` | resting cards |
| `--shadow-2` | `0 6px 20px rgb(24 32 20 / 8%)` | popovers, modals |
| `--radius-sm / md / lg` | `8px / 12px / 16px` | fields+buttons / cards / panels+modals |
| `--space-1..6` | `4 8 12 16 24 32px` | the only spacing steps used |

Dark theme keeps its current values; both themes resolve through the same token names, and
`data-theme="light|dark|system"` on `<html>` selects them (already implemented).

## 2. Surfaces and elevation

Three levels only: page (`--bg`) → card (`--surface-1` + `--shadow-1`) → inset (`--surface-2`).
Elevation is expressed by **border + one soft shadow**, never by heavy drop shadows. Panels that
group a whole area use `--radius-lg`; individual cards inside them use `--radius-md` and no
shadow, so nesting does not multiply depth.

## 3. Typography

One family for interface text (Segoe UI Variable → system fallback), one monospace for IDs,
hashes and paths. Scale: `28/20/16/14/12px` with weights `600/600/600/400/400`, line heights
`1.25/1.3/1.45/1.5/1.45`. Numeric columns use tabular figures. Sentence case for headings; no
letter-spacing tricks; uppercase reserved for 11px section labels in `--text-muted`.

## 4. Buttons

One component, four variants: **primary** (accent fill, white text), **secondary** (surface-1,
1px `--border`), **ghost** (transparent, hover `--surface-2`), **danger** (outline `--danger`).
Sizes `sm 28px / md 34px / lg 40px`; radius `--radius-sm`; icon buttons are square at the same
height. Focus is always visible: `0 0 0 3px var(--accent-soft)` plus `--accent-border`. Disabled
uses `--surface-2` fill and `--text-muted` text, never reduced opacity alone (opacity hides the
contrast requirement).

## 5. Fields

Inputs, selects and textareas share one shell: `--surface-1`, 1px `--border`, `--radius-sm`,
height `34px` (textarea auto). Labels sit above in 12px `--text-muted`; help text below, same
size; errors replace help text and add `--danger` border. Placeholders are `--text-muted`, never
the only label. Search fields carry a leading icon and `⌘K`-style hint on the right.

## 6. Cards

Header (title 16px/600 + optional muted subtitle), body, optional footer actions. `--radius-md`,
1px `--border`, `--shadow-1`, `--space-4` padding. Cards never nest more than two levels deep.
Statistic cards show: label (11px uppercase muted), value (28px/600, tabular), and a delta line.
Status is never colour alone — a dot/icon plus text ("connected", "DEGRADED"), which also keeps
the interface readable for colour-vision deficiencies.

## 7. Navigation

Left rail `232px` (collapsible to `64px` icons-only): `--surface-1`, 1px right border, items
`36px` high, `--radius-sm`, icon + label, selected = `--accent-soft` fill with `--accent` left
marker `3px`. Top bar `56px`: workspace selector, search, connection state, theme switch, language
switch, account. Breadcrumbs appear only three levels deep or more.

## 8. Theme switching

The existing mechanism is kept: `data-theme` on `<html>`, values `light|dark|system`, stored in
`localStorage.forge.appearance`, applied by the inline script before first paint so light never
flashes. The switch is a three-state segmented control (Светлая / Тёмная / Системная) in Settings
and in the top bar. Both themes carry the full token set, so no component needs theme-specific
code; a screenshot pair per screen (light/dark) becomes part of the visual check.

## 9. Scope and risks

Proposed scope: the token layer plus the 8 existing screens (Overview, Agents, Projects, Runs,
Models, Settings, Help, Auth) — a restyle through tokens and shared components, **not** a
navigation or information-architecture change. Risks: legacy literal colours that bypass tokens
(must be swept), tables with fixed widths, and light-theme contrast on status text and charts —
each verified by automated contrast checks and a screenshot pair, in both RU and EN.
