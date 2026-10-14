"""Tests for the desktop program: languages, access, art geometry and the timeline."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.desktop import art
from app.desktop.accounts import (
    DEVELOPER_EMAIL,
    AccountStore,
    LocalCodeDelivery,
    is_valid_email,
    normalise_email,
)
from app.desktop.i18n import (
    FALLBACK_LANGUAGE,
    TRANSLATIONS,
    Translator,
    available_languages,
    extra_keys,
    missing_keys,
)
from app.desktop.intro import TOTAL_SECONDS, hammer_swing, phase_at


class LanguageTests(unittest.TestCase):
    def test_01_both_languages_define_the_same_keys(self) -> None:
        for code, _name in available_languages():
            self.assertEqual(missing_keys(code), (), f"{code} is missing keys")
            self.assertEqual(extra_keys(code), (), f"{code} has keys the reference lacks")

    def test_02_switching_changes_a_visible_label(self) -> None:
        t = Translator("ru")
        self.assertEqual(t.t("nav.projects"), "Проекты")
        t.set_language("en")
        self.assertEqual(t.t("nav.projects"), "Projects")
        self.assertEqual(t.language, "en")

    def test_03_toggle_moves_between_the_two_shipped_languages(self) -> None:
        t = Translator("ru")
        self.assertEqual(t.toggle(), "en")
        self.assertEqual(t.toggle(), "ru")

    def test_04_an_unknown_language_falls_back_instead_of_raising(self) -> None:
        t = Translator("klingon")
        self.assertEqual(t.language, FALLBACK_LANGUAGE)

    def test_05_a_missing_key_is_visible_rather_than_guessed(self) -> None:
        t = Translator("ru")
        self.assertEqual(t.t("no.such.key"), "no.such.key")

    def test_06_the_russian_title_is_the_string_the_shell_test_expects(self) -> None:
        self.assertEqual(TRANSLATIONS["ru"]["app.title"], "FORGE — «Кузница»")

    def test_07_status_tokens_are_not_translated(self) -> None:
        for code in TRANSLATIONS:
            self.assertEqual(TRANSLATIONS[code]["app.status.connected"], "CONNECTED")
            self.assertEqual(TRANSLATIONS[code]["app.status.disconnected"], "DISCONNECTED")

    def test_08_formatting_values_does_not_crash_on_a_broken_template(self) -> None:
        t = Translator("en")
        self.assertEqual(t.t("register.welcome"), "Welcome, {email}.")
        self.assertEqual(t.t("register.welcome", email="a@b.co"), "Welcome, a@b.co.")


class EmailValidationTests(unittest.TestCase):
    def test_09_accepts_ordinary_addresses(self) -> None:
        for address in ("a@b.co", "owner@forge.ai", "first.last+tag@sub.example.com"):
            self.assertTrue(is_valid_email(address), address)

    def test_10_rejects_typos(self) -> None:
        for address in ("", "no-at-sign", "two@@at.co", "space in@mail.co", "trailing@dot.", "@nodomain.co", "a@b"):
            self.assertFalse(is_valid_email(address), address)

    def test_11_normalises_case_and_padding(self) -> None:
        self.assertEqual(normalise_email("  Owner@Forge.AI  "), "owner@forge.ai")


class AccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.path = Path(self._directory.name) / "accounts.json"
        self.now = [1000.0]
        self.store = AccountStore(
            path=self.path, delivery=LocalCodeDelivery(), clock=lambda: self.now[0]
        )

    def test_20_registration_returns_a_six_digit_code_in_local_mode(self) -> None:
        ok, message_key, shown = self.store.register("smith@forge.ai")
        self.assertTrue(ok)
        self.assertIn("register.code.sent", message_key)
        self.assertEqual(len(shown), 6)
        self.assertTrue(shown.isdigit())

    def test_21_the_code_is_never_written_to_disk_in_clear(self) -> None:
        _ok, _key, shown = self.store.register("smith@forge.ai")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(shown, raw)
        self.assertIn("code_hash", raw)
        self.assertNotIn("code\"", raw)

    def test_22_the_wrong_code_is_refused_and_the_right_one_admits(self) -> None:
        _ok, _key, shown = self.store.register("smith@forge.ai")
        wrong = "000000" if shown != "000000" else "111111"
        ok, message_key = self.store.confirm("smith@forge.ai", wrong)
        self.assertFalse(ok)
        self.assertEqual(message_key, "register.error.code_wrong")
        self.assertFalse(self.store.is_confirmed("smith@forge.ai"))

        ok, message_key = self.store.confirm("smith@forge.ai", shown)
        self.assertTrue(ok, message_key)
        self.assertTrue(self.store.is_confirmed("smith@forge.ai"))

    def test_23_a_code_expires(self) -> None:
        _ok, _key, shown = self.store.register("smith@forge.ai")
        self.now[0] += self.store.code_ttl_seconds + 1
        ok, message_key = self.store.confirm("smith@forge.ai", shown)
        self.assertFalse(ok)
        self.assertEqual(message_key, "register.error.code_expired")

    def test_24_five_wrong_attempts_destroy_the_code(self) -> None:
        _ok, _key, shown = self.store.register("smith@forge.ai")
        wrong = "000000" if shown != "000000" else "111111"
        for _ in range(self.store.max_attempts):
            self.store.confirm("smith@forge.ai", wrong)
        # The pending record is destroyed, so even the correct code is useless -- that is
        # the security property being asserted. The message is "expired, request a new
        # one" rather than "wrong code", because the latter would invite a retry that
        # cannot possibly succeed.
        ok, message_key = self.store.confirm("smith@forge.ai", shown)
        self.assertFalse(ok)
        self.assertEqual(message_key, "register.error.code_expired")
        self.assertEqual(self.store.pending, {})

    def test_25_an_invalid_address_creates_nothing(self) -> None:
        ok, message_key, shown = self.store.register("not-an-address")
        self.assertFalse(ok)
        self.assertEqual(message_key, "register.error.email_invalid")
        self.assertEqual(shown, "")
        self.assertEqual(self.store.pending, {})

    def test_26_developer_access_skips_confirmation_and_says_so(self) -> None:
        account = self.store.developer_access()
        self.assertEqual(account.email, DEVELOPER_EMAIL)
        self.assertTrue(account.is_developer)
        self.assertTrue(account.confirmed)
        self.assertTrue(self.store.is_confirmed(DEVELOPER_EMAIL))

    def test_27_accounts_survive_a_reload_and_pending_records_do_not_leak_codes(self) -> None:
        _ok, _key, shown = self.store.register("smith@forge.ai")
        self.store.confirm("smith@forge.ai", shown)
        self.store.developer_access()

        reloaded = AccountStore(path=self.path)
        self.assertTrue(reloaded.is_confirmed("smith@forge.ai"))
        self.assertTrue(reloaded.is_confirmed(DEVELOPER_EMAIL))
        self.assertEqual(reloaded.pending, {})

    def test_28_a_corrupt_file_does_not_stop_the_program(self) -> None:
        self.path.write_text("{ this is not json", encoding="utf-8")
        store = AccountStore(path=self.path)
        self.assertEqual(store.accounts, {})
        ok, _key, _shown = store.register("smith@forge.ai")
        self.assertTrue(ok)

    def test_29_a_delivery_failure_is_reported_and_leaves_no_pending_code(self) -> None:
        class Broken:
            name = "broken"

            def deliver(self, email: str, code: str):  # noqa: ANN201 - test double
                raise RuntimeError("no route to host")

        store = AccountStore(path=self.path, delivery=Broken())
        ok, message_key, shown = store.register("smith@forge.ai")
        self.assertFalse(ok)
        self.assertTrue(message_key.startswith("delivery_failed:"))
        self.assertEqual(shown, "")
        self.assertEqual(store.pending, {})

    def test_30_the_persisted_file_is_plain_json(self) -> None:
        self.store.register("smith@forge.ai")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["pending"]), 1)


class ArtGeometryTests(unittest.TestCase):
    def test_40_the_timeline_is_ordered_and_about_twenty_three_seconds(self) -> None:
        self.assertGreaterEqual(TOTAL_SECONDS, 20.0)
        self.assertLessEqual(TOTAL_SECONDS, 30.0)

    def test_41_every_moment_maps_to_a_phase_and_a_bounded_progress(self) -> None:
        seen = set()
        for step in range(0, 240):
            name, progress = phase_at(step * 0.1)
            seen.add(name)
            self.assertGreaterEqual(progress, 0.0)
            self.assertLessEqual(progress, 1.0)
        self.assertEqual(seen, {"stars", "ship", "inside", "forge"})

    def test_42_the_hammer_swings_between_raised_and_struck(self) -> None:
        values = [hammer_swing(t * 0.05) for t in range(200)]
        self.assertAlmostEqual(max(values), 1.0, places=2)
        self.assertLess(min(values), 0.05)

    def test_43_the_ship_is_a_closed_polygon_of_the_expected_size(self) -> None:
        points = art.ship_polygon(100.0, 50.0, 10.0)
        self.assertEqual(len(points) % 2, 0)
        self.assertGreaterEqual(len(points) // 2, 6)
        self.assertAlmostEqual(min(points[0::2]), 90.0)
        self.assertAlmostEqual(max(points[0::2]), 110.0)

    def test_44_the_anvil_stands_on_its_base(self) -> None:
        points = art.anvil_polygon(50.0, 100.0, 20.0)
        ys = points[1::2]
        self.assertAlmostEqual(max(ys), 100.0)
        self.assertLess(min(ys), 100.0)
        xs = points[0::2]
        self.assertLess(min(xs), 50.0)
        self.assertGreater(max(xs), 50.0)

    def test_45_sparks_are_deterministic_for_a_phase(self) -> None:
        first = art.sparks(8, 10.0, 10.0, 2.0)
        second = art.sparks(8, 10.0, 10.0, 2.0)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 8)

    def test_46_the_starfield_is_deterministic_and_wraps(self) -> None:
        stars = art.star_points(40, 300, 200, seed=5)
        self.assertEqual(stars, art.star_points(40, 300, 200, seed=5))
        moved = art.drift(stars, 5.0, 200.0)
        for (_x, y, _r), (_x0, y0, _r0) in zip(moved, stars):
            self.assertAlmostEqual(y, (y0 + 5.0) % 200.0)

    def test_47_both_smiths_are_placed_by_the_emblem(self) -> None:
        # The emblem is drawn for a square; the two figures and the anvil must sit inside.
        class Recorder:
            def __init__(self) -> None:
                self.items = []

            def create_rectangle(self, *a, **k):  # noqa: ANN002, ANN003 - test double
                self.items.append("rect")

            def create_polygon(self, *a, **k):  # noqa: ANN002, ANN003
                self.items.append("poly")

            def create_oval(self, *a, **k):  # noqa: ANN002, ANN003
                self.items.append("oval")

            def create_line(self, *a, **k):  # noqa: ANN002, ANN003
                self.items.append("line")

        recorder = Recorder()
        art.draw_emblem(recorder, 80.0)
        # background + arch + anvil + two bodies + hammer heads + sparks + head circles
        self.assertGreaterEqual(recorder.items.count("poly"), 5)
        self.assertGreaterEqual(recorder.items.count("oval"), 3)


class DesktopProgramTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        from app.desktop.app import ForgeDesktopApp

        self.store = AccountStore(path=Path(self._directory.name) / "accounts.json")
        self.app = ForgeDesktopApp(headless=True, accounts=self.store)
        self.addCleanup(self.app.shutdown)

    def test_50_the_default_language_is_russian(self) -> None:
        self.assertEqual(self.app.language, "ru")
        self.assertIn("Кузница", self.app.nav_labels())

    def test_51_switching_language_relabels_the_navigation(self) -> None:
        self.assertEqual(self.app.set_language("en"), "en")
        self.assertIn("Projects", self.app.nav_labels())
        self.assertNotIn("Проекты", self.app.nav_labels())
        self.assertEqual(self.app.set_language("ru"), "ru")
        self.assertIn("Проекты", self.app.nav_labels())

    def test_52_the_navigation_covers_every_view_and_the_manual(self) -> None:
        from app.desktop.app import ForgeDesktopApp

        self.assertEqual(
            list(ForgeDesktopApp._nav_keys()),
            [
                "nav.forge",
                "nav.projects",
                "nav.agents",
                "nav.runs",
                "nav.costs",
                "nav.settings",
                "nav.manual",
            ],
        )

    def test_53_the_manual_is_available_in_both_languages_and_differs(self) -> None:
        from app.desktop.app import MANUAL_SECTIONS

        russian = [self.app.manual_text(key) for key in MANUAL_SECTIONS]
        self.app.set_language("en")
        english = [self.app.manual_text(key) for key in MANUAL_SECTIONS]
        for left, right in zip(russian, english):
            self.assertTrue(left.strip() and right.strip())
            self.assertNotEqual(left, right)
        self.assertIn("confirmation code", " ".join(english))
        self.assertIn("код подтверждения", " ".join(russian))

    def test_54_the_developer_entry_is_labelled_in_the_language_table(self) -> None:
        for code in ("ru", "en"):
            self.app.set_language(code)
            self.assertTrue(self.app.translator.t("register.dev"))
            self.assertTrue(self.app.translator.t("register.dev.hint"))

    def test_55_headless_start_does_not_open_a_window_or_block(self) -> None:
        # A headless program must be safe to start in a test process.
        self.app.start()
        self.assertTrue(self.app.is_running)
        self.assertEqual(self.app.current_view_name, "forge")




class IntroFramingTests(unittest.TestCase):
    """The interior must be framed so the figures are inside the window.

    Written after a measured regression: with the smiths drawn at scale 190 in a
    620-pixel-tall window, the hammer reached the ceiling and the anvil fell below the
    frame. These assertions fail on that drawing and pass on the corrected one.
    """

    WIDTH = 900
    HEIGHT = 620

    def setUp(self) -> None:
        try:
            import tkinter as tk

            self.root = tk.Tk()
        except Exception as exc:  # noqa: BLE001 - no display on this machine
            self.skipTest(f"no display available: {type(exc).__name__}")
        self.addCleanup(self._destroy_root)

    def _destroy_root(self) -> None:
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001 - teardown must not fail the test
            pass

    def _forge_canvas(self):
        from app.desktop.i18n import Translator
        from app.desktop.intro import ForgeIntro

        intro = ForgeIntro(
            self.root, Translator("ru"), lambda: None, width=self.WIDTH, height=self.HEIGHT
        )
        self.addCleanup(intro.window.destroy)
        intro._draw("forge", 0.7, 20.0)
        self.root.update_idletasks()
        return intro._canvas

    def _bbox(self, canvas, tag: str):
        box = canvas.bbox(tag)
        self.assertIsNotNone(box, f"nothing was drawn for {tag}")
        return box

    def test_60_every_interior_group_is_drawn(self) -> None:
        canvas = self._forge_canvas()
        for tag in ("forge-fire", "anvil", "smith-left", "smith-right"):
            self._bbox(canvas, tag)
        self.assertGreater(len(canvas.find_all()), 90)

    def test_61_every_interior_group_is_inside_the_canvas(self) -> None:
        canvas = self._forge_canvas()
        for tag in ("forge-fire", "anvil", "smith-left", "smith-right"):
            x0, y0, x1, y1 = self._bbox(canvas, tag)
            self.assertGreaterEqual(x0, -4, f"{tag} starts left of the window")
            self.assertLessEqual(x1, self.WIDTH + 4, f"{tag} runs past the right edge")
            self.assertGreaterEqual(y0, -4, f"{tag} starts above the window")
            self.assertLessEqual(y1, self.HEIGHT + 4, f"{tag} runs below the window")

    def test_62_the_anvil_stands_between_the_two_smiths(self) -> None:
        canvas = self._forge_canvas()
        left = self._bbox(canvas, "smith-left")
        anvil = self._bbox(canvas, "anvil")
        right = self._bbox(canvas, "smith-right")
        left_centre = (left[0] + left[2]) / 2
        right_centre = (right[0] + right[2]) / 2
        anvil_centre = (anvil[0] + anvil[2]) / 2
        self.assertLess(left_centre, anvil_centre)
        self.assertLess(anvil_centre, right_centre)

    def test_63_a_smith_stays_under_35_percent_of_the_window_height(self) -> None:
        canvas = self._forge_canvas()
        for tag in ("smith-left", "smith-right"):
            x0, y0, x1, y1 = self._bbox(canvas, tag)
            share = (y1 - y0) / self.HEIGHT
            self.assertLess(share, 0.35, f"{tag} covers {share:.0%} of the window height")

    def test_64_the_figures_do_not_cover_the_caption(self) -> None:
        canvas = self._forge_canvas()
        caption_top = self.HEIGHT - 60  # the caption line sits at height - 42
        for tag in ("smith-left", "smith-right"):
            _x0, _y0, _x1, y1 = self._bbox(canvas, tag)
            self.assertLessEqual(y1, caption_top + 4, f"{tag} reaches into the caption")

    def test_65_the_other_phases_draw_something_too(self) -> None:
        from app.desktop.i18n import Translator
        from app.desktop.intro import ForgeIntro

        for phase, elapsed in (("stars", 2.0), ("ship", 7.0), ("inside", 12.0)):
            intro = ForgeIntro(
                self.root, Translator("ru"), lambda: None, width=self.WIDTH, height=self.HEIGHT
            )
            try:
                intro._draw(phase, 0.7, elapsed)
                self.root.update_idletasks()
                self.assertGreater(len(intro._canvas.find_all()), 20, phase)
            finally:
                intro.window.destroy()


if __name__ == "__main__":
    unittest.main()
