"""Minimal iCloud login, extracted from upstream icloudpd v1.32.3.

**This is deliberately not part of the shipped package.** It lives under
`tests/` so it is never installed into the download container, and its `srp`
dependency is test-only. The normal way to get credentials is to sign in at
icloud.com in a browser and import the cookies with
`tools/import_browser_cookies.py` — no login code required at all.

This exists for two reasons:

1. As a fallback if a browser web session turns out to be too short-lived for a
   large backfill. A login here yields a *trust token*, which lasts far longer.
2. As executable documentation of the flow the strip removed, exercised by
   `tests/test_minimal_login.py` against recorded fixtures, so it stays correct
   without anyone having to trust unaudited third-party code.

Provenance: the SRP handshake, header set and 2FA calls are upstream's
(`src/pyicloud_ipd/base.py`, MIT, Copyright (c) 2016 Nathan Broadbent), with
the response-observer obfuscation plumbing, SMS 2FA, keyring and web-UI paths
removed. The protocol sequence is unchanged, since Apple's endpoints and the
recorded cassettes both depend on it.

The password is sent to Apple only as an SRP proof — it never crosses the wire
in the clear, and nothing here writes it to disk.

Run it deliberately, on a trusted machine only:

    python -m tests.minimal_login --username you@example.com \
        --cookie-directory ./session
"""

import argparse
import base64
import getpass
import hashlib
import http.cookiejar as cookielib
import json
import logging
import os
import re
import sys
from typing import Any, Dict
from uuid import uuid1

import srp

from pyicloud_ipd.exceptions import PyiCloudAPIResponseException, PyiCloudFailedLoginException
from pyicloud_ipd.session import PyiCloudSession

LOGGER = logging.getLogger(__name__)

WIDGET_KEY = "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d"


class SrpPassword:
    """srp uses the encoded password at process_challenge(), so
    set_encrypt_info() must be called before that."""

    def __init__(self, password: str) -> None:
        self.pwd = password

    def set_encrypt_info(self, protocol: str, salt: bytes, iterations: int) -> None:
        self.protocol = protocol
        self.salt = salt
        self.iterations = iterations

    def encode(self) -> bytes:
        password_hash = hashlib.sha256(self.pwd.encode())
        password_digest = (
            password_hash.hexdigest().encode()
            if self.protocol == "s2k_fo"
            else password_hash.digest()
        )
        return hashlib.pbkdf2_hmac("sha256", password_digest, self.salt, self.iterations, 32)


class MinimalLogin:
    """Enough of PyiCloudService to drive a login and persist the result.

    PyiCloudSession reads `session_path`, `cookiejar_path`, `session_data`,
    `http_timeout` and `response_observer` off its service, and saves both the
    session file and the cookie jar after every request — so persistence comes
    for free and is identical to what the downloader expects to read.
    """

    def __init__(
        self,
        domain: str,
        apple_id: str,
        cookie_directory: str,
        client_id: str | None = None,
        http_timeout: float = 30.0,
    ) -> None:
        self.domain = domain
        self.apple_id = apple_id
        self.http_timeout = http_timeout
        self.response_observer = None
        self.data: Dict[str, Any] = {}
        self.client_id = client_id or f"auth-{str(uuid1()).lower()}"

        if domain == "com":
            self.AUTH_ENDPOINT = "https://idmsa.apple.com/appleauth/auth"
            self.AUTH_ROOT_ENDPOINT = "https://idmsa.apple.com"
            self.HOME_ENDPOINT = "https://www.icloud.com"
            self.SETUP_ENDPOINT = "https://setup.icloud.com/setup/ws/1"
        elif domain == "cn":
            self.AUTH_ENDPOINT = "https://idmsa.apple.com.cn/appleauth/auth"
            self.AUTH_ROOT_ENDPOINT = "https://idmsa.apple.com.cn"
            self.HOME_ENDPOINT = "https://www.icloud.com.cn"
            self.SETUP_ENDPOINT = "https://setup.icloud.com.cn/setup/ws/1"
        else:
            raise NotImplementedError(f"Domain '{domain}' is not supported")

        self._cookie_directory = os.path.expanduser(os.path.normpath(cookie_directory))
        os.makedirs(self._cookie_directory, mode=0o700, exist_ok=True)

        self.session_data: Dict[str, Any] = {}
        if os.path.exists(self.session_path):
            try:
                with open(self.session_path, encoding="utf-8") as handle:
                    self.session_data = json.load(handle)
            except json.JSONDecodeError:
                pass
        self.session_data.setdefault("client_id", self.client_id)
        self.client_id = self.session_data["client_id"]

        self.session = PyiCloudSession(self)
        self.session.headers.update(
            {
                "Origin": self.HOME_ENDPOINT,
                "Referer": f"{self.HOME_ENDPOINT}/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            }
        )
        self.session.cookies = cookielib.LWPCookieJar(filename=self.cookiejar_path)  # type: ignore[assignment]
        if os.path.exists(self.cookiejar_path):
            try:
                self.session.cookies.load(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
            except (FileNotFoundError, OSError):
                pass

        self.params = {
            "clientBuildNumber": "2522Project44",
            "clientMasteringNumber": "2522B2",
            "clientId": self.client_id,
        }

    # -- paths, matching PyiCloudService so the downloader reads what we write

    @property
    def cookiejar_path(self) -> str:
        return os.path.join(
            self._cookie_directory, "".join(c for c in self.apple_id if re.match(r"\w", c))
        )

    @property
    def session_path(self) -> str:
        return os.path.join(
            self._cookie_directory,
            "".join(c for c in self.apple_id if re.match(r"\w", c)) + ".session",
        )

    # -- state Apple's responses tell us about

    @property
    def is_trusted_session(self) -> bool:
        return bool(self.data.get("hsaTrustedBrowser", False))

    @property
    def requires_2fa(self) -> bool:
        return (
            self.data["dsInfo"].get("hsaVersion", 0) == 2
            and (self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session)
            and self.data["dsInfo"].get("hasICloudQualifyingDevice", False)
        )

    @property
    def requires_2sa(self) -> bool:
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) >= 1 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    def _auth_headers(self, overrides: Dict[str, str] | None = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/javascript",
            "Content-Type": "application/json",
            "X-Apple-OAuth-Client-Id": WIDGET_KEY,
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": self.HOME_ENDPOINT,
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": self.client_id,
            "X-Apple-Widget-Key": WIDGET_KEY,
        }
        if self.session_data.get("scnt"):
            headers["scnt"] = self.session_data["scnt"]
        if self.session_data.get("session_id"):
            headers["X-Apple-ID-Session-Id"] = self.session_data["session_id"]
        if overrides:
            headers.update(overrides)
        return headers

    # -- the login itself

    def login(self, password: str) -> None:
        """SRP sign-in. The password leaves this process only as an SRP proof."""
        srp_password = SrpPassword(password)
        srp.rfc5054_enable()
        srp.no_username_in_x()
        usr = srp.User(self.apple_id, srp_password, hash_alg=srp.SHA256, ng_type=srp.NG_2048)
        uname, A = usr.start_authentication()

        headers = self._auth_headers(
            {"Origin": self.AUTH_ROOT_ENDPOINT, "Referer": f"{self.AUTH_ROOT_ENDPOINT}/"}
        )

        try:
            response = self.session.post(
                f"{self.AUTH_ENDPOINT}/signin/init",
                data=json.dumps(
                    {
                        "a": base64.b64encode(A).decode(),
                        "accountName": uname,
                        "protocols": ["s2k", "s2k_fo"],
                    }
                ),
                headers=headers,
            )
            if response.status_code == 401:
                raise PyiCloudAPIResponseException(response.text, str(response.status_code))
        except PyiCloudAPIResponseException as error:
            raise PyiCloudFailedLoginException(
                "Failed to initiate srp authentication.", error
            ) from error

        body = response.json()
        srp_password.set_encrypt_info(body["protocol"], base64.b64decode(body["salt"]), body["iteration"])
        m1 = usr.process_challenge(base64.b64decode(body["salt"]), base64.b64decode(body["b"]))
        m2 = usr.H_AMK

        data = {
            "accountName": uname,
            "c": body["c"],
            "m1": base64.b64encode(m1).decode(),
            "m2": base64.b64encode(m2).decode(),
            "rememberMe": True,
            "trustTokens": [],
        }
        if self.session_data.get("trust_token"):
            data["trustTokens"] = [self.session_data["trust_token"]]

        try:
            response = self.session.post(
                f"{self.AUTH_ENDPOINT}/signin/complete",
                params={"isRememberMeEnabled": "true"},
                data=json.dumps(data),
                headers=headers,
            )
            if response.status_code == 409:
                pass  # 2FA required
            elif response.status_code == 412:
                # accounts without 2FA answer 412 "precondition not met"
                self.session.post(
                    f"{self.AUTH_ENDPOINT}/repair/complete",
                    data=json.dumps({}),
                    headers=self._auth_headers(),
                )
            elif 400 <= response.status_code < 600:
                raise PyiCloudAPIResponseException(response.text, str(response.status_code))
        except PyiCloudAPIResponseException as error:
            raise PyiCloudFailedLoginException(
                "Invalid email/password combination.", error
            ) from error

        self.authenticate_with_token()

    def authenticate_with_token(self) -> None:
        """Exchange the session token for a full iCloud session."""
        try:
            response = self.session.post(
                f"{self.SETUP_ENDPOINT}/accountLogin",
                data=json.dumps(
                    {
                        "accountCountryCode": self.session_data.get("account_country"),
                        "dsWebAuthToken": self.session_data.get("session_token"),
                        "extended_login": True,
                        "trustToken": self.session_data.get("trust_token", ""),
                    }
                ),
            )
            self.data = response.json()
        except PyiCloudAPIResponseException as error:
            raise PyiCloudFailedLoginException("Invalid authentication token.", error) from error

    def request_2fa_code(self) -> bool:
        """Ask Apple to push a code to the trusted devices."""
        try:
            self.session.put(
                f"{self.AUTH_ENDPOINT}/verify/trusteddevice/securitycode",
                headers=self._auth_headers({"Accept": "application/json"}),
            )
            return True
        except PyiCloudAPIResponseException:
            return False

    def validate_2fa_code(self, code: str) -> bool:
        try:
            self.session.post(
                f"{self.AUTH_ENDPOINT}/verify/trusteddevice/securitycode",
                data=json.dumps({"securityCode": {"code": code}}),
                headers=self._auth_headers({"Accept": "application/json"}),
            )
        except PyiCloudAPIResponseException as error:
            if str(error.code) == "-21669":
                LOGGER.error("Code verification failed.")
                return False
            raise

        self.trust_session()
        return not self.requires_2sa

    def trust_session(self) -> bool:
        """Get a trust token, so this session survives much longer."""
        try:
            self.session.get(f"{self.AUTH_ENDPOINT}/2sv/trust", headers=self._auth_headers())
            self.authenticate_with_token()
            return True
        except PyiCloudAPIResponseException:
            LOGGER.error("Session trust failed.")
            return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Create an iCloud session with a password.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--cookie-directory", required=True)
    parser.add_argument("--domain", choices=["com", "cn"], default="com")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    print("The browser-cookie route needs no password at all; see")
    print("tools/import_browser_cookies.py. Continue only if you want a longer-lived session.")

    service = MinimalLogin(args.domain, args.username, args.cookie_directory)
    service.login(getpass.getpass(f"iCloud password for {args.username}: "))

    if service.requires_2fa:
        service.request_2fa_code()
        while True:
            code = input("Two-factor code (6 digits): ").strip()
            if len(code) == 6 and code.isdigit():
                break
            print("Must be six digits.")
        if not service.validate_2fa_code(code):
            print("Code rejected.", file=sys.stderr)
            return 1

    print(f"Session written to {service.session_path}")
    print(f"Cookies written to {service.cookiejar_path}")
    print("Copy both into the container's cookie directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
