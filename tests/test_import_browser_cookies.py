"""Tests for tools/import_browser_cookies.py.

Two of these cover bugs found while importing a real browser session on
2026-09-18, both of which produced a jar that looked perfectly correct and
failed anyway:

* the jar was written on Windows and read on Linux, where the LWP parser strips
  only "\\n" and leaves the "\\r" inside the last attribute of every cookie;
* the documented cookie set, inherited from upstream's fixtures, was missing
  X-APPLE-WEBAUTH-TOKEN, which Apple answers 421 without.
"""

import json
import os
import sys
from http.cookiejar import LWPCookieJar
from unittest import TestCase

import pytest

from tests.helpers import path_from_project_root, recreate_path

sys.path.insert(0, os.path.join(path_from_project_root(__file__), os.pardir, "tools"))
from import_browser_cookies import (  # noqa: E402
    REQUIRED_COOKIES,
    cookiejar_path,
    main,
    parse_cookie_header,
)

# Shaped like the real ones -- "v=1:t=..." values, base64 with "=" padding --
# without being anyone's credentials.
SAMPLE = {
    "X-APPLE-WEBAUTH-TOKEN": "v=2:t=AQAAAABo1example==:s=a",
    "X-APPLE-WEBAUTH-VALIDATE": "v=1:t=AQAAAABo1example==",
    "X-APPLE-WEBAUTH-HSA-TRUST": "98af37deadbeef==SRVX",
    "X-APPLE-UNIQUE-CLIENT-ID": "AQ==",
}


class ParseCookieHeaderTestCase(TestCase):
    def test_splits_on_first_equals_only(self) -> None:
        """Apple's values contain '=' -- base64 padding and "v=1:t=" prefixes."""
        parsed = parse_cookie_header("; ".join(f"{k}={v}" for k, v in SAMPLE.items()))
        self.assertEqual(parsed, SAMPLE)

    def test_accepts_the_header_name_and_whitespace(self) -> None:
        """Copying from devtools may bring 'Cookie:' and stray whitespace along."""
        self.assertEqual(
            parse_cookie_header("  Cookie: a=1;  b=2 ;\n"),
            {"a": "1", "b": "2"},
        )

    def test_a_value_containing_a_colon_survives_the_header_name_strip(self) -> None:
        """Stripping 'Cookie:' must not eat the ':' inside 'v=1:t=...'."""
        parsed = parse_cookie_header("Cookie: X-APPLE-WEBAUTH-TOKEN=v=2:t=abc==")
        self.assertEqual(parsed, {"X-APPLE-WEBAUTH-TOKEN": "v=2:t=abc=="})

    def test_ignores_empty_and_malformed_fragments(self) -> None:
        self.assertEqual(parse_cookie_header("a=1;; junk ;b=2;"), {"a": "1", "b": "2"})


class ImportBrowserCookiesTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, tmp_path: object) -> None:
        self.tmp = str(tmp_path)

    def _write_json(self, cookies: dict[str, str]) -> str:
        path = os.path.join(self.tmp, "cookies.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cookies, handle)
        return path

    def _run(self, cookies: dict[str, str]) -> str:
        cookie_dir = os.path.join(self.tmp, "session")
        recreate_path(cookie_dir)
        main(
            [
                "--username",
                "jdoe@gmail.com",
                "--cookie-directory",
                cookie_dir,
                "--from-json",
                self._write_json(cookies),
            ]
        )
        return cookiejar_path(cookie_dir, "jdoe@gmail.com")

    def test_jar_has_no_carriage_returns_even_on_windows(self) -> None:
        """LWPCookieJar.save writes text mode, so on Windows it emits CRLF.

        The jar is imported here and used on Linux, whose LWP parser strips only
        the "\\n" -- so a surviving "\\r" ends up inside the last attribute of
        every cookie and authentication fails with no obvious cause.
        """
        with open(self._run(SAMPLE), "rb") as handle:
            raw = handle.read()
        self.assertNotIn(b"\r", raw)

    def test_values_round_trip_byte_exactly(self) -> None:
        """Whatever the browser had must come back out unchanged."""
        jar = LWPCookieJar(filename=self._run(SAMPLE))
        jar.load(ignore_discard=True, ignore_expires=True)
        self.assertEqual({c.name: c.value for c in jar}, SAMPLE)

    def test_cookies_are_scoped_to_icloud_and_secure(self) -> None:
        jar = LWPCookieJar(filename=self._run(SAMPLE))
        jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            self.assertEqual(cookie.domain, ".icloud.com")
            self.assertEqual(cookie.path, "/")
            self.assertTrue(cookie.secure)

    def test_refuses_to_write_a_jar_that_cannot_work(self) -> None:
        """Apple answers 421 'Missing X-APPLE-WEBAUTH-TOKEN cookie' without it.

        Failing here is much easier to diagnose than a jar that exists, looks
        right, and is rejected by Apple.
        """
        incomplete = {k: v for k, v in SAMPLE.items() if k != "X-APPLE-WEBAUTH-TOKEN"}
        with self.assertRaises(SystemExit) as caught:
            self._run(incomplete)
        self.assertIn("X-APPLE-WEBAUTH-TOKEN", str(caught.exception))

    def test_required_cookies_are_a_subset_of_the_documented_set(self) -> None:
        from import_browser_cookies import KNOWN_COOKIES

        self.assertTrue(set(REQUIRED_COOKIES).issubset(KNOWN_COOKIES))

    def test_jar_is_not_world_readable(self) -> None:
        """The jar is a bearer credential for the whole photo library."""
        path = self._run(SAMPLE)
        if sys.platform == "win32":
            pytest.skip("POSIX permission bits are not meaningful on Windows")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
