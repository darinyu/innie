from __future__ import annotations

from io import StringIO
import unittest

from innie.prompting import mask_secret, prompt_masked_secret


class PromptingTest(unittest.TestCase):
    def test_mask_secret_shows_first_five_then_stars(self) -> None:
        self.assertEqual("abcde********", mask_secret("abcdefghijklm"))

    def test_mask_secret_masks_short_values(self) -> None:
        self.assertEqual("****", mask_secret("abcd"))

    def test_prompt_masked_secret_reads_value_and_prints_masked_preview(self) -> None:
        stdin = StringIO("secret-value\n")
        stdout = StringIO()

        value = prompt_masked_secret("Copy Client Secret: ", stdin=stdin, stdout=stdout)

        self.assertEqual("secret-value", value)
        self.assertIn("secre*******", stdout.getvalue())
        self.assertNotIn("secret-value", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
