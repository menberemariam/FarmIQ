import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from i18n import (
    AMHARIC_GEEZ,
    CROP_NAMES_AM,
    TERMS_AM,
    normalize_amharic,
    normalize_lang,
)


class TestNormalizeLang(unittest.TestCase):
    def test_canonical_forms(self):
        for raw in ("am", "amh", "am-ET", "አማርኛ", "Amharic", " AM "):
            self.assertEqual(normalize_lang(raw), "am", raw)

    def test_unknown_falls_back(self):
        self.assertEqual(normalize_lang("xx"), "en")
        self.assertEqual(normalize_lang(""), "en")
        self.assertEqual(normalize_lang(None), "en")
        self.assertEqual(normalize_lang("en"), "en")


class TestNormalizeAmharic(unittest.TestCase):
    def test_nfc_equivalence(self):
        import unicodedata

        # A combining-mark decomposed form must normalize to the NFC string.
        decomposed = unicodedata.normalize("NFD", "አማርኛ")
        self.assertEqual(normalize_amharic(decomposed), "አማርኛ")

    def test_orthographic_mapping(self):
        pairs = [
            ("ሐሙስ", "ሀሙስ"),       # historic ḥ series
            ("ዓለም", "አለም"),        # ʿayn
            ("ሠራተኛ", "ሰራተኛ"),     # ś series
            ("ፀጋ", "ጸጋ"),          # ṣ series
            ("ኣማርኛ", "አማርኛ"),     # dialectal aa
        ]
        for variant, canonical in pairs:
            self.assertEqual(normalize_amharic(variant), canonical)

    def test_whitespace_and_bom(self):
        self.assertEqual(normalize_amharic("  አፈር\u200b  ለምነት "), "አፈር ለምነት")

    def test_geez_detection_regex(self):
        self.assertIsNotNone(AMHARIC_GEEZ.search("አማርኛ"))
        self.assertIsNone(AMHARIC_GEEZ.search("Amharic 123"))


class TestGlossary(unittest.TestCase):
    def test_terms_bilingual(self):
        """Every term must contain both Amharic and English for understanding."""
        for term, value in TERMS_AM.items():
            self.assertTrue(AMHARIC_GEEZ.search(value), f"{term} lacks Ge'ez: {value}")
            self.assertRegex(value, r"\([^)]+\)", f"{term} lacks English gloss: {value}")

    def test_crop_names_bilingual(self):
        for crop, value in CROP_NAMES_AM.items():
            self.assertTrue(AMHARIC_GEEZ.search(value), f"{crop} lacks Ge'ez")
            self.assertIn(f"({crop})", value, f"{crop} gloss must match dataset name")


if __name__ == "__main__":
    unittest.main()
