"""The opening animation: deep space, a ship, and the forge inside it.

Twenty-three seconds of vector animation, skippable at any moment, drawn with
:mod:`app.desktop.art` so the program needs no image files and no network:

======  ==========================================================
0-4 s   a drifting starfield, caption "deep space, far from the forge"
4-10 s  a courier ship flies in, growing as it approaches
10-14 s the camera passes through the hull: concentric rings expand and fade
14-23 s inside: two smiths at an anvil, a hammer falling, sparks, a distant forge
======  ==========================================================

The timeline is a pure function (:func:`phase_at`), so the sequence can be asserted in
tests without opening a window, and ``skip``/finish callbacks make the window safe to
close at any moment.
"""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable, Optional, Tuple

from app.desktop import art
from app.desktop.i18n import Translator

PHASES: Tuple[Tuple[str, float, float], ...] = (
    ("stars", 0.0, 4.0),
    ("ship", 4.0, 10.0),
    ("inside", 10.0, 14.0),
    ("forge", 14.0, 23.0),
)

#: Total length in seconds: inside the 20-30 s the owner asked for.
TOTAL_SECONDS = PHASES[-1][2]
FRAME_MS = 33
HAMMER_STRIKES_PER_SECOND = 0.9


def phase_at(elapsed: float) -> Tuple[str, float]:
    """``(phase name, progress 0..1)`` for a moment in the animation."""

    moment = max(0.0, float(elapsed))
    for index, (name, start, end) in enumerate(PHASES):
        if moment < end or index == len(PHASES) - 1:
            span = max(0.001, end - start)
            return name, max(0.0, min(1.0, (moment - start) / span))
    return PHASES[-1][0], 1.0


def caption_key(phase: str) -> str:
    return {
        "stars": "intro.caption.stars",
        "ship": "intro.caption.ship",
        "inside": "intro.caption.inside",
        "forge": "intro.caption.forge",
    }.get(phase, "intro.caption.stars")


def hammer_swing(elapsed: float) -> float:
    """1.0 with the hammer raised, 0.0 at the strike. Pure, so it can be tested."""

    cycle = (max(0.0, elapsed) * HAMMER_STRIKES_PER_SECOND) % 1.0
    return 1.0 - cycle


class ForgeIntro:
    """The animated opening. Calls ``on_finish`` exactly once, however it ends."""

    def __init__(
        self,
        parent: tk.Misc,
        translator: Translator,
        on_finish: Callable[[], None],
        *,
        width: int = 900,
        height: int = 620,
        duration: Optional[float] = None,
        on_tick: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        self._parent = parent
        self._t = translator
        self._on_finish = on_finish
        self._width = width
        self._height = height
        self._duration = float(duration if duration is not None else TOTAL_SECONDS)
        self._on_tick = on_tick
        self._elapsed = 0.0
        # Real time, not frames: a frame that takes 300 ms must not stretch the
        # opening. Measured before this change, a 16-second wait showed the 4-second
        # starfield phase -- the animation was running many times slower than its
        # declared duration.
        self._started_at = time.monotonic()
        self._finished = False
        self._after_id: Optional[str] = None

        self._window = tk.Toplevel(parent)
        self._window.title(self._t.t("app.title"))
        self._window.configure(bg=art.SPACE_DEEP)
        self._window.geometry(f"{width}x{height}")
        self._window.resizable(False, False)
        self._window.protocol("WM_DELETE_WINDOW", self.skip)

        self._canvas = tk.Canvas(
            self._window, width=width, height=height, bg=art.SPACE_DEEP, highlightthickness=0
        )
        self._canvas.pack(fill="both", expand=True)
        self._caption = self._canvas.create_text(
            width / 2,
            height - 42,
            text=self._t.t("intro.skipping"),
            fill=art.STAR,
            font=("Segoe UI", 11),
        )
        self._stars = art.star_points(90, width, height)

        self._window.bind("<Escape>", lambda _event: self.skip())
        self._canvas.bind("<Button-1>", lambda _event: self.skip())
        self._window.focus_force()

    # ------------------------------------------------------------------- window control
    @property
    def window(self) -> tk.Toplevel:
        return self._window

    @property
    def finished(self) -> bool:
        return self._finished

    @property
    def elapsed(self) -> float:
        return self._elapsed

    def start(self) -> None:
        self._draw("stars", 0.0, 0.0)
        self._schedule()

    def _schedule(self) -> None:
        self._after_id = self._window.after(FRAME_MS, self._tick)

    def _tick(self) -> None:
        if self._finished:
            return
        self._elapsed = time.monotonic() - self._started_at
        phase, progress = phase_at(self._elapsed)
        try:
            self._draw(phase, progress, self._elapsed)
        except tk.TclError:
            # The window went away between frames; treat it as the end of the opening.
            self._finish()
            return
        if self._on_tick is not None:
            self._on_tick(phase, progress)
        if self._elapsed >= self._duration:
            self._finish()
            return
        self._schedule()

    def skip(self) -> None:
        self._finish()

    def _finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._after_id is not None:
            try:
                self._window.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self._window.destroy()
        except tk.TclError:
            pass
        self._on_finish()

    # ------------------------------------------------------------------------ painting
    def _draw(self, phase: str, progress: float, elapsed: float) -> None:
        canvas = self._canvas
        canvas.delete("frame")
        width, height = self._width, self._height

        # Background in bands: cheap, and it reads as depth.
        bands = 14
        for index in range(bands):
            ratio = index / (bands - 1)
            colour = art.SPACE_DEEP if ratio < 0.6 else art.SPACE_MID
            top = height * index / bands
            canvas.create_rectangle(
                0, top, width, top + height / bands + 1, fill=colour, outline="", tags="frame"
            )

        self._stars = art.drift(self._stars, 0.9 + 2.4 * progress, height)
        art.draw_starfield(canvas, self._stars)
        self._tag_new_items()

        if phase == "stars":
            self._nebula(progress)
        elif phase == "ship":
            self._ship(progress)
        elif phase == "inside":
            self._through_the_hull(progress)
        else:
            self._forge(progress, elapsed)
        self._tag_new_items()

        canvas.itemconfigure(
            self._caption,
            text=self._t.t(caption_key(phase)) + "   —   " + self._t.t("intro.skipping"),
        )
        canvas.tag_raise(self._caption)

    def _tag_group(self, before: set, tag: str) -> None:
        """Tag everything drawn since ``before``, so groups can be asserted in tests."""

        for item in self._canvas.find_all():
            if item not in before:
                self._canvas.addtag_withtag(tag, item)

    def _tag_new_items(self) -> None:
        """Everything drawn for this frame belongs to the frame.

        The helpers in :mod:`app.desktop.art` draw without tags (they are also used by
        the widget), so the frame tags them here in one pass, after ``delete("frame")``
        has already cleared the previous frame.
        """

        for item in self._canvas.find_all():
            if "frame" not in self._canvas.gettags(item):
                self._canvas.addtag_withtag("frame", item)

    def _nebula(self, progress: float) -> None:
        cx, cy = self._width * 0.72, self._height * 0.30
        for index in range(6):
            spread = (1.0 - progress * 0.15) * (40 + index * 26)
            self._canvas.create_oval(
                cx - spread * 1.6,
                cy - spread,
                cx + spread * 1.6,
                cy + spread,
                outline=art.NEBULA,
                width=2,
            )

    def _ship(self, progress: float) -> None:
        start_x, end_x = -260.0, self._width * 0.58
        cx = start_x + (end_x - start_x) * min(1.0, progress * 1.15)
        cy = self._height * 0.46 - 30 * progress
        scale = 90 + 210 * progress
        art.draw_ship(
            self._canvas, cx, cy, scale, flicker=0.5 + 0.5 * ((progress * 5.0) % 1.0)
        )

    def _through_the_hull(self, progress: float) -> None:
        cx, cy = self._width / 2, self._height / 2
        for index in range(5):
            spread = 40 + (progress * 1.25 + index * 0.16) * self._width * 0.55
            shade = art.HULL if index % 2 == 0 else art.HULL_DARK
            self._canvas.create_oval(
                cx - spread,
                cy - spread * 0.62,
                cx + spread,
                cy + spread * 0.62,
                outline=shade,
                width=3,
            )
        radius = 30 + 120 * progress
        self._canvas.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=art.COCKPIT,
            width=max(2, int(6 * (1.0 - progress) + 1)),
        )

    def _forge(self, progress: float, elapsed: float) -> None:
        canvas = self._canvas
        width, height = self._width, self._height
        horizon = height * 0.86
        phase = elapsed * 3.0

        # The camera is inside now, so the starfield is covered by the room itself.
        canvas.create_rectangle(0, 0, width, height, fill="#15110d", outline="")
        canvas.create_rectangle(0, horizon, width, height, fill="#1a1410", outline="")
        canvas.create_rectangle(0, 0, width, height * 0.12, fill="#0f0c09", outline="")
        fire_mark = set(canvas.find_all())
        art.draw_forge(canvas, width * 0.84, horizon - 16, 66.0, phase)
        self._tag_group(fire_mark, "forge-fire")

        anvil_x = width * 0.47
        anvil_mark = set(canvas.find_all())
        art.fill_polygon(
            canvas, art.anvil_polygon(anvil_x, horizon, 92.0), art.IRON, art.HULL_DARK
        )
        self._tag_group(anvil_mark, "anvil")

        # Two smiths flanking the anvil. Scale 120 (not 190): at 190 the hammer reached
        # the ceiling of a 620-pixel window and the anvil fell outside the frame.
        swing = hammer_swing(elapsed)
        left_mark = set(canvas.find_all())
        art.draw_smith(canvas, width * 0.34, horizon, 120.0, swing, facing=1)
        self._tag_group(left_mark, "smith-left")

        right_mark = set(canvas.find_all())
        art.draw_smith(canvas, width * 0.60, horizon, 120.0, swing=0.05, facing=-1)
        self._tag_group(right_mark, "smith-right")

        heat = art.IRON_HOT if art.hammer_contact(swing) else art.IRON
        canvas.create_line(
            anvil_x - 56,
            horizon - 34,
            anvil_x + 56,
            horizon - 39,
            fill=heat,
            width=6,
        )
        if art.hammer_contact(swing):
            for x, y, radius in art.sparks(16, anvil_x, horizon - 39, phase):
                canvas.create_oval(
                    x - radius, y - radius, x + radius, y + radius, fill=art.FLAME_INNER, outline=""
                )
