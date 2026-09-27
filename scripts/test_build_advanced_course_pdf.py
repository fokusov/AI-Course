"""Regression checks for PDF navigation and the explicit document boundary."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_advanced_course_pdf as advanced
import build_course_pdf as shared


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.source = "advanced-course/lessons/01-substitute-shipment/lesson.md"
        self.rules = "advanced-course/lessons/01-substitute-shipment/rules.md"
        self.headings = {self.source: {"пример"}, self.rules: {"правила-проведения"}}
        self.allowed = set(self.headings)
        self.external = {"course/index.md": "https://github.com/fokusov/AI-Course/blob/main/course/index.md"}

    def resolve(self, target, source=None):
        return advanced.resolve_link(source or self.source, target, self.allowed, self.headings, self.external)

    def test_file_and_unicode_fragment(self):
        self.assertEqual(self.resolve("rules.md"), "#" + shared.key_for(self.rules))
        self.assertEqual(self.resolve("rules.md#%D0%BF%D1%80%D0%B0%D0%B2%D0%B8%D0%BB%D0%B0-%D0%BF%D1%80%D0%BE%D0%B2%D0%B5%D0%B4%D0%B5%D0%BD%D0%B8%D1%8F"),
                         "#" + shared.key_for(self.rules, "правила-проведения"))
        self.assertEqual(self.resolve("#пример"), "#" + shared.key_for(self.source, "пример"))

    def test_public_base_link_is_explicit(self):
        self.assertEqual(self.resolve("../course/index.md", "advanced-course/README.md"), self.external["course/index.md"])
        with self.assertRaises(ValueError):
            self.resolve("../course/not-allowlisted.md", "advanced-course/README.md")

    def test_external_url_is_unchanged(self):
        url = "https://example.org/manual?a=1&b=2#section"
        self.assertEqual(self.resolve(url), url)

    def test_bad_links_fail_closed(self):
        for target in ("missing.md", "rules.md#missing", "rules.md?raw=1", "../../../docs/author/secret.md",
                       "../../../../outside.md", "file:///C:/secret.md", "javascript:alert(1)", "/absolute.md"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.resolve(target)

    def test_manifest_rejects_private_and_duplicate_files(self):
        manifest = advanced.load_manifest()
        with tempfile.TemporaryDirectory() as directory:
            location = Path(directory) / "manifest.json"
            for files in (["../docs/author/secret.md"], ["lessons/01/instructor.md"], ["README.md", "README.md"]):
                manifest["files"] = files
                location.write_text(json.dumps(manifest), encoding="utf-8")
                with self.subTest(files=files), patch.object(advanced, "MANIFEST", location), self.assertRaises(ValueError):
                    advanced.load_manifest()


class RenderTests(unittest.TestCase):
    def test_details_are_expanded_without_dropping_the_title(self):
        block = {"t": "RawBlock", "c": ["html", "<details>\n<summary>Подсказка</summary>\n"]}
        expanded = advanced.expand_details(block)
        self.assertEqual(expanded["c"][0]["c"][0]["c"], "Подсказка")
        self.assertIsNone(advanced.expand_details({"t": "RawBlock", "c": ["html", "</details>\n"]}))
        with self.assertRaises(ValueError):
            advanced.expand_details({"t": "RawBlock", "c": ["html", "<script>hidden()</script>"]})

    def test_unsupported_diagram_is_not_silently_simplified(self):
        with self.assertRaises(ValueError):
            advanced.MermaidDiagram("flowchart TD\nA[First] --> B[Next]\nA --> C[Branch]", 490)


if __name__ == "__main__":
    unittest.main()
