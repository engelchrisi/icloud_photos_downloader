"""The cookie jar alone must be enough to authenticate.

This is the property the whole deployment rests on: the user signs in at
icloud.com in a browser, imports the X-APPLE-WEBAUTH-* cookies, and the
downloader works without any session file and without ever seeing a password.
If these tests fail, the container needs a session minted by login code again.
"""

import importlib.util
import inspect
import os
import shutil
import sys
from http.cookiejar import LWPCookieJar
from unittest import TestCase

import pytest

from tests.helpers import path_from_project_root, recreate_path, run_cassette, run_main

sys.path.insert(0, os.path.join(path_from_project_root(__file__), os.pardir, "tools"))
from import_browser_cookies import cookiejar_path, make_cookie  # noqa: E402


class CookieOnlySessionTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self) -> None:
        self.root_path = path_from_project_root(__file__)
        self.fixtures_path = os.path.join(self.root_path, "fixtures")
        self.vcr_path = os.path.join(self.root_path, "vcr_cassettes")

    def _cookies_from_fixture(self) -> dict[str, str]:
        """Read the master fixture jar, so values round-trip byte-exactly."""
        jar = LWPCookieJar(filename=os.path.join(self.root_path, "cookie", "jdoegmailcom"))
        jar.load(ignore_discard=True, ignore_expires=True)
        return {cookie.name: cookie.value or "" for cookie in jar}

    def _write_jar(self, cookie_dir: str, username: str) -> str:
        """Build a jar the way tools/import_browser_cookies.py does."""
        path = cookiejar_path(cookie_dir, username)
        jar = LWPCookieJar(filename=path)
        for name, value in self._cookies_from_fixture().items():
            jar.set_cookie(make_cookie(name, value))
        jar.save(ignore_discard=True, ignore_expires=True)
        return path

    def test_authenticates_from_cookies_with_no_session_file(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        recreate_path(base_dir)
        os.makedirs(cookie_dir)

        jar_path = self._write_jar(cookie_dir, "jdoe@gmail.com")
        self.assertTrue(os.path.exists(jar_path))
        # the decisive part: no .session file exists
        self.assertFalse(os.path.exists(jar_path + ".session"))

        result = run_cassette(
            os.path.join(self.vcr_path, "listing_albums.yml"),
            [
                "--username",
                "jdoe@gmail.com",
                "--cookie-directory",
                cookie_dir,
                "--list-albums",
                "--no-progress-bar",
            ],
        )

        self.assertEqual(result.exit_code, 0, "exit code")
        self.assertIn("WhatsApp", result.output.splitlines())

        shutil.rmtree(base_dir)

    def test_fails_clearly_when_no_credentials_at_all(self) -> None:
        """With no jar and no session, fail offline — never call Apple first."""
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        recreate_path(base_dir)
        os.makedirs(cookie_dir)

        # no cassette: any HTTP request here would be a real one, so reaching
        # the network at all fails the test
        result = run_main(
            [
                "--username",
                "jdoe@gmail.com",
                "--cookie-directory",
                cookie_dir,
                "--list-albums",
                "--no-progress-bar",
            ],
        )

        self.assertEqual(result.exit_code, 1, "exit code")
        self.assertIn("import_browser_cookies", result.output)

        shutil.rmtree(base_dir)

    def test_runtime_package_has_no_login_code(self) -> None:
        """The installed package must not be able to log in."""
        self.assertIsNone(importlib.util.find_spec("pyicloud_ipd.sms"))

        src_path = os.path.join(self.root_path, os.pardir, "src")
        offenders = []
        for dirpath, _dirnames, filenames in os.walk(src_path):
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                full = os.path.join(dirpath, filename)
                with open(full, encoding="utf-8") as handle:
                    content = handle.read()
                for marker in ("signin/init", "signin/complete", "securitycode", "2sv/trust"):
                    if marker in content:
                        offenders.append(f"{full}: {marker}")

        self.assertEqual(offenders, [], "login endpoints found in shipped code")
