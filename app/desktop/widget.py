"""Forge Pulse -- a frameless desktop widget for Forge AI («Кузница»).

What it is
----------
A small always-on-top card that shows the *current* state of the local Forge
core: whether it is reachable, which provider accounts are healthy, how many runs
have succeeded, and what the recent activity looks like.  Clicking the card drags
it; a double click opens the full desktop shell; the close button hides it.

Design constraints
------------------
* **No new dependencies.** Standard-library ``tkinter`` only. ``customtkinter`` is
  used by the main shell when present, but a widget that needs a package the
  project does not declare would not be a finished deliverable.
* **No invented data.** Every number comes from ``ForgeApiClient``.  When the core
  is unreachable or the dashboard read model is empty, the widget says so instead
  of drawing zeros that look like measurements.
* **The view model is pure.** :class:`WidgetSnapshot` and its helpers can be built
  and asserted without a display, which is what the tests exercise.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional, Sequence, Tuple

from app.api.client import ForgeApiClient
from app.api.service import ForgeApiService

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #

BACKGROUND = "#0b0f19"
CARD_TOP = "#141b2d"
CARD_BOTTOM = "#0f1626"
HAIRLINE = "#243049"
TEXT = "#e6edf7"
TEXT_MUTED = "#8b98b0"
ACCENT = "#ff9d3d"
ACCENT_DIM = "#3a2a18"
OK = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"
RING_TRACK = "#1e2740"

TONE_COLORS = {
    "ok": OK,
    "warn": WARN,
    "bad": BAD,
    "muted": TEXT_MUTED,
}


# --------------------------------------------------------------------------- #
# View model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProviderPill:
    """One provider account, reduced to what the card can show in a line."""

    name: str
    detail: str
    tone: str = "muted"


@dataclass(frozen=True)
class WidgetSnapshot:
    """Everything the card renders, derived from the core and nothing else."""

    core_connected: bool
    core_label: str
    providers: Tuple[ProviderPill, ...] = ()
    runs_total: int = 0
    success_rate: Optional[float] = None
    total_tokens: int = 0
    total_cost: float = 0.0
    activity: Tuple[int, ...] = ()
    updated_at: str = ""
    degraded_reason: str = ""


def format_tokens(value: Any) -> str:
    """Compact token count: 950, 12.4k, 3.1M."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"
    if number < 1000:
        return f"{int(number)}"
    if number < 1_000_000:
        return f"{number / 1000:.1f}k"
    return f"{number / 1_000_000:.1f}M"


def format_cost(value: Any) -> str:
    """Cost in minor units of display, never invented."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "$0.00"
    if number == 0:
        return "$0.00"
    if number < 0.01:
        return "<$0.01"
    return f"${number:,.2f}"


def success_ratio(success: Any, failure: Any) -> Optional[float]:
    """Share of successful runs, or ``None`` when nothing has run yet.

    ``None`` is not zero: "no runs yet" and "every run failed" are different facts
    and the card must not present one as the other.
    """

    try:
        succeeded = int(success)
        failed = int(failure)
    except (TypeError, ValueError):
        return None
    total = succeeded + failed
    if total <= 0:
        return None
    return succeeded / total


def ring_extent(fraction: Optional[float], *, full: float = 359.9) -> float:
    """Arc extent for the gauge. An unknown fraction draws no arc at all."""

    if fraction is None:
        return 0.0
    clamped = min(max(float(fraction), 0.0), 1.0)
    # Never a full circle: tkinter draws 360 degrees as an empty arc.
    return clamped * full


def _tone_for_alert(alert: Any) -> str:
    name = getattr(alert, "value", alert)
    text = str(name or "").lower()
    if text in {"critical", "danger", "empty", "exhausted"}:
        return "bad"
    if text in {"warning", "low"}:
        return "warn"
    if text in {"healthy", "active", "ok"}:
        return "ok"
    return "muted"


def _pill_from_summary(summary: Any) -> ProviderPill:
    provider = str(getattr(summary, "provider", "") or "unknown")
    account = getattr(summary, "account_status", None)
    balance = str(getattr(account, "balance_display", "") or "").strip()
    tokens = getattr(summary, "total_tokens", 0)
    detail = balance or f"{format_tokens(tokens)} tok"
    tone = _tone_for_alert(getattr(account, "alert", None))
    if tone == "muted" and str(getattr(account, "status", "")).lower() in {
        "active",
        "healthy",
        "ok",
    }:
        tone = "ok"
    return ProviderPill(name=provider, detail=detail, tone=tone)


def build_snapshot(client: Any) -> WidgetSnapshot:
    """Read the core and normalize it. Never raises, never invents a number."""

    updated = datetime.now().strftime("%H:%M:%S")
    connected = False
    core_label = "core unreachable"
    try:
        health = client.check_health()
        connected = str(getattr(health, "core_status", "")).lower() == "connected"
        core_label = "core online" if connected else "core offline"
    except Exception:  # noqa: BLE001 - a widget must survive an absent core
        return WidgetSnapshot(
            core_connected=False,
            core_label=core_label,
            updated_at=updated,
            degraded_reason="core недоступен",
        )

    try:
        report = client.get_dashboard()
    except Exception:  # noqa: BLE001 - the card shows the gap instead of guessing
        return WidgetSnapshot(
            core_connected=connected,
            core_label=core_label,
            updated_at=updated,
            degraded_reason="нет данных телеметрии",
        )

    providers = tuple(
        _pill_from_summary(summary) for summary in getattr(report, "providers", ()) or ()
    )
    totals = getattr(report, "all_time_totals", None)
    runs = tuple(getattr(report, "runs", ()) or ())

    total_tokens = int(_number(getattr(totals, "total_tokens", 0)))
    total_cost = float(_number(getattr(totals, "effective_cost", 0.0)))
    runs_total = int(_number(getattr(totals, "total_runs", len(runs))))
    succeeded = int(_number(getattr(totals, "success_count", 0)))
    failed = int(_number(getattr(totals, "failure_count", 0)))

    activity: Sequence[int] = tuple(
        int(_number(getattr(run, "total_tokens", 0))) for run in runs[-24:]
    )

    degraded = ""
    if not providers and runs_total == 0:
        degraded = "пока нет запусков"
    return WidgetSnapshot(
        core_connected=connected,
        core_label=core_label,
        providers=providers,
        runs_total=runs_total,
        success_rate=success_ratio(succeeded, failed),
        total_tokens=total_tokens,
        total_cost=total_cost,
        activity=activity,
        updated_at=updated,
        degraded_reason=degraded,
    )


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------- #
# Widget
# --------------------------------------------------------------------------- #


@dataclass
class WidgetOptions:
    width: int = 336
    height: int = 196
    margin: int = 24
    refresh_seconds: float = 5.0
    topmost: bool = True
    alpha: float = 0.96
    offset: Tuple[int, int] = (-1, -1)
    on_open_console: Optional[Callable[[], None]] = None
    extra: dict = field(default_factory=dict)


class ForgePulseWidget:
    """Frameless, draggable, always-on-top status card."""

    def __init__(
        self,
        client: Optional[Any] = None,
        *,
        options: Optional[WidgetOptions] = None,
    ) -> None:
        self._client = client if client is not None else ForgeApiClient(api_service=ForgeApiService())
        self.options = options or WidgetOptions()
        self._root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._drag_origin: Tuple[int, int] = (0, 0)
        self._refresh_job: Optional[str] = None
        self._snapshot: Optional[WidgetSnapshot] = None
        self._console_opened = False

    # -- lifecycle -------------------------------------------------------- #

    @property
    def root(self) -> Optional[tk.Tk]:
        return self._root

    @property
    def snapshot(self) -> Optional[WidgetSnapshot]:
        return self._snapshot

    def initialize(self) -> tk.Tk:
        root = tk.Tk()
        root.title("Forge Pulse")
        root.overrideredirect(True)
        if self.options.topmost:
            root.attributes("-topmost", True)
        try:
            root.attributes("-alpha", self.options.alpha)
        except tk.TclError:  # pragma: no cover - depends on the window manager
            pass
        root.configure(bg=BACKGROUND)

        width, height = self.options.width, self.options.height
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        x = screen_w - width - self.options.margin
        y = screen_h - height - self.options.margin * 3
        root.geometry(f"{width}x{height}+{x}+{y}")

        canvas = tk.Canvas(
            root,
            width=width,
            height=height,
            bg=BACKGROUND,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Button-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<Double-Button-1>", self._on_double_click)
        canvas.bind("<Button-3>", self._on_context_menu)
        root.bind("<Escape>", lambda _event: self.close())

        self._root = root
        self._canvas = canvas
        return root

    def refresh(self) -> WidgetSnapshot:
        snapshot = build_snapshot(self._client)
        self._snapshot = snapshot
        if self._canvas is not None:
            self._render(snapshot)
        return snapshot

    def run(self) -> None:
        """Initialize, draw once, then refresh on a timer until closed."""

        if self._root is None:
            self.initialize()
        self.refresh()
        self._schedule_refresh()
        if self._root is not None:
            self._root.mainloop()

    def close(self) -> None:
        if self._refresh_job is not None and self._root is not None:
            try:
                self._root.after_cancel(self._refresh_job)
            except Exception:  # noqa: BLE001 - cancelling twice is harmless
                pass
            self._refresh_job = None
        if self._root is not None:
            try:
                self._root.destroy()
            finally:
                self._root = None
                self._canvas = None

    # -- interaction ------------------------------------------------------ #

    def _on_press(self, event: Any) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._window_origin = (
            self._root.winfo_x() if self._root else 0,
            self._root.winfo_y() if self._root else 0,
        )

    def _on_drag(self, event: Any) -> None:
        if self._root is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        self._root.geometry(f"+{self._window_origin[0] + dx}+{self._window_origin[1] + dy}")

    def _on_double_click(self, _event: Any) -> None:
        self.open_console()

    def open_console(self) -> bool:
        """Run the callback the composition gave us, or the packaged shell."""

        if self.options.on_open_console is not None:
            self.options.on_open_console()
            self._console_opened = True
            return True
        try:  # pragma: no cover - spawns a real process
            import subprocess
            import sys

            subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                [sys.executable, "-m", "app.desktop"],
                close_fds=True,
            )
            self._console_opened = True
            return True
        except Exception:  # noqa: BLE001 - the widget stays usable without it
            return False

    def _on_context_menu(self, event: Any) -> None:
        if self._root is None:
            return
        menu = tk.Menu(self._root, tearoff=0, bg=CARD_TOP, fg=TEXT, activebackground=ACCENT_DIM)
        menu.add_command(label="Обновить", command=self.refresh)
        menu.add_command(label="Открыть консоль", command=self.open_console)
        menu.add_separator()
        menu.add_command(label="Закрыть виджет", command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _schedule_refresh(self) -> None:
        if self._root is None:
            return
        self._refresh_job = self._root.after(
            max(int(self.options.refresh_seconds * 1000), 250), self._tick
        )

    def _tick(self) -> None:
        self.refresh()
        self._schedule_refresh()

    # -- rendering -------------------------------------------------------- #

    def _render(self, snapshot: WidgetSnapshot) -> None:
        canvas = self._canvas
        if canvas is None:
            return
        canvas.delete("all")
        width, height = self.options.width, self.options.height

        self._draw_card(canvas, width, height)
        self._draw_header(canvas, snapshot, width)
        self._draw_gauge(canvas, snapshot, x=76, y=118, radius=44)
        self._draw_stats(canvas, snapshot, x=140, y=84)
        self._draw_sparkline(canvas, snapshot, x=140, y=132, width=176, height=34)
        self._draw_pills(canvas, snapshot, width)

    def _draw_card(self, canvas: tk.Canvas, width: int, height: int) -> None:
        # A vertical "gradient" made of a few bands: tkinter has no gradient, and
        # banding at low contrast reads as depth rather than as stripes.
        bands = 26
        for index in range(bands):
            ratio = index / (bands - 1)
            color = _mix(CARD_TOP, CARD_BOTTOM, ratio)
            y0 = 6 + (height - 12) * index / bands
            y1 = 6 + (height - 12) * (index + 1) / bands + 1
            canvas.create_rectangle(6, y0, width - 6, y1, fill=color, outline=color)
        _rounded_rect(canvas, 6, 6, width - 6, height - 6, 18, outline=HAIRLINE, fill="")
        canvas.create_line(24, 7, width - 24, 7, fill="#2c3a5a")
        # Forge ember: a warm accent bar on the left edge.
        canvas.create_rectangle(7, 34, 10, height - 40, fill=ACCENT, outline=ACCENT)

    def _draw_header(self, canvas: tk.Canvas, snapshot: WidgetSnapshot, width: int) -> None:
        canvas.create_text(24, 26, anchor="w", text="FORGE", fill=ACCENT,
                           font=("Segoe UI Semibold", 13))
        canvas.create_text(84, 27, anchor="w", text="«Кузница»", fill=TEXT_MUTED,
                           font=("Segoe UI", 9))
        dot = OK if snapshot.core_connected else BAD
        canvas.create_oval(width - 116, 20, width - 106, 30, fill=dot, outline=dot)
        canvas.create_text(width - 100, 25, anchor="w",
                           text="online" if snapshot.core_connected else "offline",
                           fill=TEXT_MUTED, font=("Segoe UI", 9))
        # The timestamp lives in the footer: the header line is already carrying the
        # wordmark, the subtitle and the connection state.
        canvas.create_text(width - 24, self.options.height - 24, anchor="e",
                           text=snapshot.updated_at, fill=HAIRLINE,
                           font=("Segoe UI", 8))
        # Close affordance.
        canvas.create_text(width - 30, 48, anchor="center", text="✕", fill=TEXT_MUTED,
                           font=("Segoe UI", 10), tags=("close",))
        canvas.tag_bind("close", "<Button-1>", lambda _e: self.close())

    def _draw_gauge(self, canvas: tk.Canvas, snapshot: WidgetSnapshot,
                    *, x: int, y: int, radius: int) -> None:
        box = (x - radius, y - radius, x + radius, y + radius)
        canvas.create_arc(*box, start=90, extent=359.9, style=tk.ARC,
                          outline=RING_TRACK, width=11)
        rate = snapshot.success_rate
        extent = ring_extent(rate)
        if extent > 0:
            canvas.create_arc(*box, start=90, extent=-extent, style=tk.ARC,
                              outline=ACCENT, width=11)
        label = "—" if rate is None else f"{rate * 100:.0f}%"
        canvas.create_text(x, y - 6, text=label, fill=TEXT,
                           font=("Segoe UI Semibold", 17))
        canvas.create_text(x, y + 14, text="успех", fill=TEXT_MUTED,
                           font=("Segoe UI", 8))

    def _draw_stats(self, canvas: tk.Canvas, snapshot: WidgetSnapshot, *, x: int, y: int) -> None:
        canvas.create_text(x, y, anchor="w", text=f"{snapshot.runs_total}",
                           fill=TEXT, font=("Segoe UI Semibold", 15))
        canvas.create_text(x + 40, y + 3, anchor="w", text="запусков",
                           fill=TEXT_MUTED, font=("Segoe UI", 9))
        canvas.create_text(x, y + 24, anchor="w",
                           text=f"{format_tokens(snapshot.total_tokens)} токенов",
                           fill=TEXT_MUTED, font=("Segoe UI", 9))
        canvas.create_text(x + 116, y + 24, anchor="w",
                           text=format_cost(snapshot.total_cost),
                           fill=TEXT_MUTED, font=("Segoe UI", 9))
        if snapshot.degraded_reason:
            canvas.create_text(x, y + 44, anchor="w", text=snapshot.degraded_reason,
                               fill=WARN, font=("Segoe UI", 8))

    def _draw_sparkline(self, canvas: tk.Canvas, snapshot: WidgetSnapshot,
                        *, x: int, y: int, width: int, height: int) -> None:
        canvas.create_line(x, y + height, x + width, y + height, fill=HAIRLINE)
        values = list(snapshot.activity)
        if len(values) < 2:
            canvas.create_text(x, y + height // 2, anchor="w",
                               text="активность: нет данных", fill=HAIRLINE,
                               font=("Segoe UI", 8))
            return
        peak = max(values) or 1
        step = width / (len(values) - 1)
        points = []
        for index, value in enumerate(values):
            px = x + index * step
            py = y + height - (value / peak) * (height - 4)
            points.extend((px, py))
        # Filled area first, then the line on top.
        area = [x, y + height, *points, x + width, y + height]
        canvas.create_polygon(area, fill="#1b2436", outline="")
        canvas.create_line(*points, fill=ACCENT, width=2, smooth=True)

    def _draw_pills(self, canvas: tk.Canvas, snapshot: WidgetSnapshot, width: int) -> None:
        pills = snapshot.providers[:3]
        if not pills:
            canvas.create_text(24, self.options.height - 22, anchor="w",
                               text="провайдеры не настроены", fill=TEXT_MUTED,
                               font=("Segoe UI", 8))
            return
        x = 24
        limit = width - 24
        for pill in pills:
            text = f"{pill.name} · {pill.detail}"
            approx = 8 + len(text) * 5.4
            if x + approx > limit:
                break
            _rounded_rect(canvas, x, self.options.height - 34, x + approx,
                          self.options.height - 14, 9, outline=HAIRLINE, fill="#111827")
            dot = TONE_COLORS.get(pill.tone, TEXT_MUTED)
            canvas.create_oval(x + 7, self.options.height - 28, x + 14,
                               self.options.height - 21, fill=dot, outline=dot)
            canvas.create_text(x + 19, self.options.height - 24, anchor="w", text=text,
                               fill=TEXT_MUTED, font=("Segoe UI", 8))
            x += approx + 8


# --------------------------------------------------------------------------- #
# Small drawing helpers
# --------------------------------------------------------------------------- #


def _mix(first: str, second: str, ratio: float) -> str:
    """Linear blend of two ``#rrggbb`` colors."""

    ratio = min(max(ratio, 0.0), 1.0)
    a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
    b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * ratio) for i in range(3))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _rounded_rect(canvas: tk.Canvas, x0: float, y0: float, x1: float, y1: float,
                  radius: float, *, outline: str, fill: str = "") -> int:
    """A rounded rectangle built from four arcs and two rectangles.

    ``fill=""`` leaves the interior untouched, which is what the card wants: its
    background is drawn as bands underneath.
    """

    points = [
        x0 + radius, y0, x1 - radius, y0, x1, y0, x1, y0 + radius,
        x1, y1 - radius, x1, y1, x1 - radius, y1, x0 + radius, y1,
        x0, y1, x0, y1 - radius, x0, y0 + radius, x0, y0,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24,
                                 outline=outline, fill=fill, width=1)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point: ``python -m app.desktop.widget``."""

    arguments = list(argv if argv is not None else [])
    if "--help" in arguments or "-h" in arguments:
        print(__doc__)
        return 0
    widget = ForgePulseWidget()
    try:
        widget.run()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        widget.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the widget
    raise SystemExit(main())
