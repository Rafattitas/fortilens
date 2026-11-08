import re
import struct
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "management/static"


def png_size(path):
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path.name} is not a PNG")
    return struct.unpack(">II", data[16:24])


class FaviconTests(unittest.TestCase):
    def test_console_links_icons_that_exist(self):
        index = (STATIC / "index.html").read_text(encoding="utf-8")
        head = index[: index.index("</head>")]
        self.assertIn('<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">', head)
        self.assertIn('<link rel="icon" href="/static/favicon-32.png" sizes="32x32" type="image/png">', head)
        self.assertIn('<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">', head)
        self.assertEqual(png_size(STATIC / "favicon-32.png"), (32, 32))
        self.assertEqual(png_size(STATIC / "apple-touch-icon.png"), (180, 180))

    def test_report_page_icon_matches_console_icon(self):
        page = (ROOT / "portable/reporting.html").read_text(encoding="utf-8")
        match = re.search(r'<link rel="icon" type="image/svg\+xml" href="data:image/svg\+xml,([^"]+)">', page)
        self.assertIsNotNone(match, "The standalone report page carries its own icon")
        inline = urllib.parse.unquote(match.group(1)).replace("'", '"')
        console = (STATIC / "favicon.svg").read_text(encoding="utf-8").strip()
        self.assertEqual(inline, console)

    def test_standalone_report_pages_allow_only_self_and_data_images(self):
        for name in ("portable/portal.py", "portable/usage_service.py"):
            with self.subTest(source=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn("img-src 'self' data:;", text)


if __name__ == "__main__":
    unittest.main()
