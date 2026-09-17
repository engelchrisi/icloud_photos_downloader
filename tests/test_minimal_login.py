"""Tests for the test-only minimal login helper.

These run entirely against recorded cassettes — no network, no real account.
They exist so the fallback login path stays known-good without anyone having to
run unaudited third-party code, and so it can be trusted if a browser session
turns out to be too short-lived for a large backfill.
"""

import inspect
import json
import os
from unittest import TestCase

import pytest
from vcr import VCR

import pyicloud_ipd
from tests.helpers import path_from_project_root, recreate_path
from tests.minimal_login import MinimalLogin

vcr = VCR(decode_compressed_response=True, record_mode="none")


class MinimalLoginTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self) -> None:
        self.root_path = path_from_project_root(__file__)
        self.fixtures_path = os.path.join(self.root_path, "fixtures")
        self.vcr_path = os.path.join(self.root_path, "vcr_cassettes")

    def _cookie_dir(self) -> str:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[1][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        recreate_path(base_dir)
        os.makedirs(cookie_dir)
        return cookie_dir

    def test_bad_password_raises(self) -> None:
        cookie_dir = self._cookie_dir()
        service = MinimalLogin("com", "bad_username", cookie_dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "failed_auth.yml")):
            with self.assertRaises(pyicloud_ipd.exceptions.PyiCloudFailedLoginException) as ctx:
                service.login("bad_password")

        self.assertIn("Invalid email/password combination.", str(ctx.exception))

    def test_login_requiring_2fa_persists_session(self) -> None:
        cookie_dir = self._cookie_dir()
        service = MinimalLogin("com", "jdoe@gmail.com", cookie_dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "auth_requires_2fa.yml")):
            service.login("password1")

        self.assertTrue(service.requires_2fa, "should report 2FA needed")

        # the point of logging in at all: a session file the downloader can read
        self.assertTrue(os.path.exists(service.session_path))
        with open(service.session_path, encoding="utf-8") as handle:
            session = json.load(handle)
        self.assertIn("session_token", session)
        self.assertNotIn("password", json.dumps(session).lower())

    def test_valid_2fa_code_trusts_session(self) -> None:
        cookie_dir = self._cookie_dir()
        service = MinimalLogin("com", "jdoe@gmail.com", cookie_dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "2fa_flow_valid_code.yml")):
            service.login("password1")
            self.assertTrue(service.requires_2fa)
            self.assertTrue(service.request_2fa_code(), "push notification")
            self.assertTrue(service.validate_2fa_code("654321"))

        self.assertTrue(os.path.exists(service.cookiejar_path))

    def test_invalid_2fa_code_returns_false(self) -> None:
        cookie_dir = self._cookie_dir()
        service = MinimalLogin("com", "jdoe@gmail.com", cookie_dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "2fa_flow_invalid_code.yml")):
            service.login("password1")
            service.request_2fa_code()
            self.assertFalse(service.validate_2fa_code("000000"))

    def test_helper_is_not_part_of_the_installed_package(self) -> None:
        """It must be impossible to reach this code from the container build."""
        import importlib.util

        for name in (
            "pyicloud_ipd.minimal_login",
            "icloudpd.minimal_login",
            "pyicloud_ipd.sms",
        ):
            self.assertIsNone(importlib.util.find_spec(name), name)

        # srp must not be a runtime dependency of the package
        pyproject = os.path.join(self.root_path, os.pardir, "pyproject.toml")
        with open(pyproject, encoding="utf-8") as handle:
            content = handle.read()
        runtime_block = content.split("dependencies = [", 1)[1].split("]", 1)[0]
        self.assertNotIn("srp", runtime_block)
