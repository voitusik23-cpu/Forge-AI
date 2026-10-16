"""Child output decoding: Cyrillic, UTF-8, invalid bytes, and preserved evidence.

The measured defect: a Windows child writing Cyrillic through the console codepage produces cp1251
bytes, which strict UTF-8 cannot read, so real Russian text became replacement characters in the run
history -- silently, in the place a Russian-speaking owner reads first. (The engine itself never
crashed: the traceback that looked like a product defect came from the trial script's own text-mode
subprocess, which is corrected separately.)

These tests pin the ordered strategy that replaced the blanket decode, and the evidence the adapter
now records about it.
"""

from __future__ import annotations

import pathlib
import unittest

ADAPTER = "app/execution/adapter.py"


class DecodingTests(unittest.TestCase):
    def test_01_utf8_cyrillic_survives_exactly(self) -> None:
        from app.execution.adapter import decode_process_output

        text, encoding = decode_process_output("купить хлеб".encode("utf-8"))
        self.assertEqual(text, "купить хлеб")
        self.assertEqual(encoding, "utf-8")

    def test_02_console_encoded_cyrillic_is_recovered_not_lost(self) -> None:
        from app.execution.adapter import decode_process_output

        # Exactly what a Windows child produces when its stdout uses the console codepage.
        raw = "купить хлеб".encode("cp1251")
        text, encoding = decode_process_output(raw)
        self.assertEqual(text, "купить хлеб")
        self.assertFalse(encoding.endswith("+replace"), encoding)

    def test_03_ascii_is_unchanged(self) -> None:
        from app.execution.adapter import decode_process_output

        text, encoding = decode_process_output(b"plain ascii output\n")
        self.assertEqual(text, "plain ascii output\n")
        self.assertEqual(encoding, "utf-8")

    def test_04_invalid_bytes_never_raise_and_are_flagged(self) -> None:
        from app.execution.adapter import decode_process_output

        text, encoding = decode_process_output(b"\xff\xfe\x00 broken")
        self.assertIsInstance(text, str)
        # There is no valid multi-byte UTF-8 here, so a single-byte encoding may accept it -- but it
        # must never be reported as UTF-8.
        self.assertNotEqual(encoding, "utf-8", encoding)
        self.assertIn("broken", text)

    def test_05_empty_output_is_empty_not_an_error(self) -> None:
        from app.execution.adapter import decode_process_output

        self.assertEqual(decode_process_output(b""), ("", "utf-8"))

    def test_06_mixed_utf8_and_broken_bytes_do_not_raise(self) -> None:
        from app.execution.adapter import decode_process_output

        raw = "начало ".encode("utf-8") + b"\xff\xfe" + " конец".encode("utf-8")
        text, _ = decode_process_output(raw)
        self.assertIsInstance(text, str)
        self.assertIn("начало", text)

    def test_07_no_blanket_decode_remains(self) -> None:
        source = pathlib.Path(ADAPTER).read_text(encoding="utf-8")
        self.assertNotIn('stdout_bytes.decode("utf-8", errors="replace")', source)
        self.assertNotIn('stderr_bytes.decode("utf-8", errors="replace")', source)
        self.assertIn("decode_process_output(stdout_bytes)", source)
        self.assertIn("decode_process_output(stderr_bytes)", source)

    def test_08_the_decoding_is_recorded_as_evidence(self) -> None:
        source = pathlib.Path(ADAPTER).read_text(encoding="utf-8")
        self.assertIn('"output_decoding"', source)
        self.assertIn('"undecodable_output"', source)

    def test_09_the_strategy_is_ordered_and_platform_aware(self) -> None:
        source = pathlib.Path(ADAPTER).read_text(encoding="utf-8")
        self.assertIn("getpreferredencoding", source)
        self.assertIn("cp1251", source)
        # UTF-8 is attempted first, before any fallback.
        helper = source.split("def decode_process_output", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(helper.index('decode("utf-8")'), helper.index("_platform_fallback_encodings()"))


if __name__ == "__main__":
    unittest.main()
