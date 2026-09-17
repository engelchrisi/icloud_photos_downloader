import getpass
import http.cookiejar as cookielib
import json
import logging
import typing
from contextlib import contextmanager
from functools import partial
from itertools import chain
from os import mkdir, path
from re import Pattern, match
from tempfile import gettempdir
from typing import Any, Callable, Dict, Generator, List, Mapping, Sequence
from uuid import uuid1

from foundation.core import compose, constant, identity
from foundation.json import (
    Rule,
    apply_rules,
    re_compile_ignorecase,
)
from foundation.string import obfuscate
from pyicloud_ipd.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
)
from pyicloud_ipd.services.photos import PhotosService
from pyicloud_ipd.session import PyiCloudSession

LOGGER = logging.getLogger(__name__)

HEADER_DATA = {
    "X-Apple-ID-Account-Country": "account_country",
    "X-Apple-ID-Session-Id": "session_id",
    "X-Apple-Session-Token": "session_token",
    "X-Apple-TwoSV-Trust-Token": "trust_token",
    "X-Apple-TwoSV-Trust-Eligible": "trust_eligible",
    "X-Apple-I-Rscd": "apple_rscd",
    "X-Apple-I-Ercd": "apple_ercd",
    "scnt": "scnt",
}


def origin_referer_headers(input: str) -> Dict[str, str]:
    return {"Origin": input, "Referer": f"{input}/"}


class PyiCloudService:
    """
    Attaches to the iCloud Photos service using an already-established session.

    This build has no login capability: it cannot accept a password, perform
    SRP authentication, or complete 2FA/2SA. The session and cookie files must
    be created elsewhere and placed in the cookie directory.

    Usage:
        from pyicloud_ipd import PyiCloudService
        pyicloud = PyiCloudService('com', 'username@apple.com')
        pyicloud.photos
    """

    def __init__(
        self,
        domain: str,
        apple_id: str,
        response_observer: Callable[[Mapping[str, Any]], None] | None = None,
        cookie_directory: str | None = None,
        verify: bool = True,
        client_id: str | None = None,
        with_family: bool = True,
        http_timeout: float = 30.0,
    ):
        self.apple_id = apple_id
        self.data: Dict[str, Any] = {}
        self.params: Dict[str, Any] = {}
        self.client_id: str = client_id or (f"auth-{str(uuid1()).lower()}")
        self.with_family = with_family
        self.http_timeout = http_timeout
        self.response_observer = response_observer
        self.observer_rules: Sequence[Rule] = []

        if domain == "com":
            self.HOME_ENDPOINT = "https://www.icloud.com"
            self.SETUP_ENDPOINT = "https://setup.icloud.com/setup/ws/1"
        elif domain == "cn":
            self.HOME_ENDPOINT = "https://www.icloud.com.cn"
            self.SETUP_ENDPOINT = "https://setup.icloud.com.cn/setup/ws/1"
        else:
            raise NotImplementedError(f"Domain '{domain}' is not supported yet")

        self.domain = domain

        if cookie_directory:
            self._cookie_directory = path.expanduser(path.normpath(cookie_directory))
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)
        else:
            topdir = path.join(gettempdir(), "pyicloud")
            self._cookie_directory = path.join(topdir, getpass.getuser())
            if not path.exists(topdir):
                mkdir(topdir, 0o777)
            if not path.exists(self._cookie_directory):
                mkdir(self._cookie_directory, 0o700)

        LOGGER.debug("Using session file %s", self.session_path)

        self.session_data = {}
        try:
            with open(self.session_path, encoding="utf-8") as session_f:
                self.session_data = json.load(session_f)
        except (FileNotFoundError, json.JSONDecodeError):
            LOGGER.info("Session file does not exist")
        session_client_id: str | None = self.session_data.get("client_id")
        if session_client_id:
            self.client_id = session_client_id
        else:
            self.session_data.update({"client_id": self.client_id})

        def apply_rules_and_observe(response: Mapping[str, Any]) -> None:
            if self.response_observer:
                self.response_observer(apply_rules("", self.observer_rules, response))

        self.session: PyiCloudSession = PyiCloudSession(
            self, apply_rules_and_observe if self.response_observer else None
        )
        self.session.verify = verify
        self.session.headers.update(
            {
                "Origin": self.HOME_ENDPOINT,
                "Referer": f"{self.HOME_ENDPOINT}/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            }
        )

        cookiejar_path = self.cookiejar_path
        self.session.cookies = cookielib.LWPCookieJar(filename=cookiejar_path)  # type: ignore[assignment]
        if path.exists(cookiejar_path):
            try:
                self.session.cookies.load(ignore_discard=True, ignore_expires=True)  # type: ignore[attr-defined]
                LOGGER.debug("Read cookies from %s", cookiejar_path)
            except (FileNotFoundError, OSError):
                # Most likely a pickled cookiejar from earlier versions.
                # The cookiejar will get replaced with a valid one after
                # successful authentication.
                LOGGER.warning("Failed to read cookiejar %s", cookiejar_path)

        # Unsure if this is still needed
        self.params = {
            "clientBuildNumber": "2522Project44",
            "clientMasteringNumber": "2522B2",
            "clientId": self.client_id,
        }

        # set observer rules
        def obfuscate_rule(r: Pattern[str]) -> Rule:
            return (r, obfuscate)

        obfuscate_rules_from_pattern: Callable[[Sequence[str]], List[Rule]] = compose(
            list, partial(map, compose(obfuscate_rule, re_compile_ignorecase))
        )
        self.cookie_obfuscate_rules = obfuscate_rules_from_pattern(
            [r"X_APPLE_.*", r"DES.*", r"acn01", r"aasp"]
        )

        self.header_obfuscate_rules = obfuscate_rules_from_pattern([r"X-APPLE-.*", r"scnt"])

        def pass_rule(r: Pattern[str]) -> Rule:
            return (r, identity)

        pass_rules_from_pattern: Callable[[Sequence[str]], List[Rule]] = compose(
            list, partial(map, compose(pass_rule, re_compile_ignorecase))
        )
        self.header_pass_rules = pass_rules_from_pattern(
            [r"^(request|response)\.headers\.(Origin|Referer|Content-Type|Location)$"]
        )

        def drop_rule(r: Pattern[str]) -> Rule:
            return (r, constant(None))

        drop_rules_from_pattern: Callable[[Sequence[str]], List[Rule]] = compose(
            list, partial(map, compose(drop_rule, re_compile_ignorecase))
        )
        self.header_drop_rules: List[Rule] = drop_rules_from_pattern(
            [r"^(request|response)\.headers\..+"]
        )

        self.validate_response_body_obfuscate_rules = obfuscate_rules_from_pattern(
            [
                r"^response\.content\.dsInfo\.appleId$",
                r"^response\.content\.dsInfo\.appleIdAlias$",
                r"^response\.content\.dsInfo\.iCloudAppleIdAlias$",
                r"^response\.content\.dsInfo\.appleIdEntries\.value$",
                r"^response\.content\.dsInfo\.fullName",
                r"^response\.content\.dsInfo\.firstName",
                r"^response\.content\.dsInfo\.lastName",
                r"^response\.content\.dsInfo\.dsid",
                r"^response\.content\.dsInfo\.notificationId",
                r"^response\.content\.dsInfo\.aDsID",
                r"^response\.content\.dsInfo\.primaryEmail",
            ]
        )
        self.validate_response_body_drop_rules = drop_rules_from_pattern(
            [
                r"^response\.content\.webservices\.",
                r"^response\.content\.configBag\.urls\.",
                r"^response\.content\.apps.*",
                r"^response\.content\.dsInfo\.mailFlags",
            ]
        )
        self.auth_token_body_obfuscate_rules = list(
            chain(
                obfuscate_rules_from_pattern(
                    [
                        r"^request\.content\.dsWebAuthToken",
                        r"^request\.content\.trustToken",
                    ]
                ),
                self.validate_response_body_obfuscate_rules,
            )
        )
        self.auth_token_body_drop_rules = self.validate_response_body_drop_rules

        self.authenticate()

        self._photos: PhotosService | None = None

    @contextmanager
    def use_rules(self, rules: Sequence[Rule]) -> Generator[Sequence[Rule], Any, None]:
        temp_rules = self.observer_rules
        try:
            self.observer_rules = rules
            yield temp_rules
        finally:
            self.observer_rules = temp_rules

    def authenticate(self) -> None:
        """
        Attaches to iCloud using the stored session only.

        Tries the saved session token first, then the trust token, both of
        which live in the session file. If neither works there is no fallback:
        this build cannot log in, so it raises instead of asking for a
        password.
        """

        if not self.session_data.get("session_token"):
            raise PyiCloudFailedLoginException(
                f"No session token found in {self.session_path}. Create a session with the "
                "upstream icloudpd --auth-only on a trusted machine and copy the session and "
                "cookie files into the cookie directory."
            )

        try:
            self.data = self._validate_token()
        except PyiCloudAPIResponseException:
            LOGGER.debug("Session token rejected, trying the trust token")
            try:
                self._authenticate_with_token()
            except (PyiCloudAPIResponseException, PyiCloudFailedLoginException) as error:
                raise PyiCloudFailedLoginException(
                    "The stored session has expired and this build cannot log in. Re-run the "
                    "upstream icloudpd with --auth-only on a trusted machine and copy the "
                    "refreshed session and cookie files into the cookie directory."
                ) from error

        # Is this needed?
        self.params.update({"dsid": self.data["dsInfo"]["dsid"]})

        self._webservices = self.data["webservices"]

        LOGGER.info("Authentication completed successfully")
        LOGGER.debug(self.params)

    def _authenticate_with_token(self) -> None:
        """Authenticate using session token."""
        data = {
            "accountCountryCode": self.session_data.get("account_country"),
            "dsWebAuthToken": self.session_data.get("session_token"),
            "extended_login": True,
            "trustToken": self.session_data.get("trust_token", ""),
        }

        try:
            # set observer with obfuscator
            if self.response_observer:
                rules = list(
                    chain(
                        self.cookie_obfuscate_rules,
                        self.header_obfuscate_rules,
                        self.header_pass_rules,
                        self.header_drop_rules,
                        self.auth_token_body_obfuscate_rules,
                        self.auth_token_body_drop_rules,
                    )
                )
            else:
                rules = []

            with self.use_rules(rules):
                req = self.session.post(
                    f"{self.SETUP_ENDPOINT}/accountLogin", data=json.dumps(data)
                )
            self.data = req.json()
        except PyiCloudAPIResponseException as error:
            msg = "Invalid authentication token."
            raise PyiCloudFailedLoginException(msg, error) from error

        # {'domainToUse': 'iCloud.com'}
        domain_to_use = self.data.get("domainToUse")
        if domain_to_use is not None:
            msg = f"Apple insists on using {domain_to_use} for your request. Please use --domain parameter"
            raise PyiCloudConnectionException(msg)

    def _validate_token(self) -> Dict[str, Any]:
        """Checks if the current access token is still valid."""
        LOGGER.debug("Checking session token validity")
        headers = origin_referer_headers(self.HOME_ENDPOINT)
        try:
            # set observer with obfuscator
            if self.response_observer:
                rules = list(
                    chain(
                        self.cookie_obfuscate_rules,
                        self.header_obfuscate_rules,
                        self.header_pass_rules,
                        self.header_drop_rules,
                        self.validate_response_body_obfuscate_rules,
                        self.validate_response_body_drop_rules,
                    )
                )
            else:
                rules = []

            with self.use_rules(rules):
                response = self.session.post(
                    f"{self.SETUP_ENDPOINT}/validate", data="null", headers=headers
                )
            LOGGER.debug("Session token is still valid")
            result: Dict[str, Any] = response.json()
            return result
        except PyiCloudAPIResponseException as err:
            LOGGER.debug("Invalid authentication token")
            raise err

    def _get_auth_headers(self, overrides: Dict[str, str] | None = None) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/javascript",
            "Content-Type": "application/json",
            "X-Apple-OAuth-Client-Id": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
            "X-Apple-OAuth-Client-Type": "firstPartyAuth",
            "X-Apple-OAuth-Redirect-URI": "https://www.icloud.com.cn"
            if self.domain == "cn"
            else "https://www.icloud.com",
            "X-Apple-OAuth-Require-Grant-Code": "true",
            "X-Apple-OAuth-Response-Mode": "web_message",
            "X-Apple-OAuth-Response-Type": "code",
            "X-Apple-OAuth-State": self.client_id,
            "X-Apple-Widget-Key": "d39ba9916b7251055b22c7f910e2ea796ee65e98b2ddecea8f5dde8d9d1a815d",
        }
        scnt = self.session_data.get("scnt")
        if scnt:
            headers["scnt"] = scnt

        session_id = self.session_data.get("session_id")
        if session_id:
            headers["X-Apple-ID-Session-Id"] = session_id

        if overrides:
            headers.update(overrides)
        return headers

    @property
    def cookiejar_path(self) -> str:
        """Get path for cookiejar file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.apple_id if match(r"\w", c)]),
        )

    @property
    def session_path(self) -> str:
        """Get path for session data file."""
        return path.join(
            self._cookie_directory,
            "".join([c for c in self.apple_id if match(r"\w", c)]) + ".session",
        )

    @property
    def requires_2sa(self) -> bool:
        """Returns True if two-step authentication is required."""
        return self.data.get("dsInfo", {}).get("hsaVersion", 0) >= 1 and (
            self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session
        )

    @property
    def requires_2fa(self) -> bool:
        """Returns True if two-factor authentication is required."""
        return (
            self.data["dsInfo"].get("hsaVersion", 0) == 2
            and (self.data.get("hsaChallengeRequired", False) or not self.is_trusted_session)
            and self.data["dsInfo"].get("hasICloudQualifyingDevice", False)
        )

    @property
    def is_trusted_session(self) -> bool:
        """Returns True if the session is trusted."""
        return typing.cast(bool, self.data.get("hsaTrustedBrowser", False))

    def _get_webservice_url(self, ws_key: str) -> str:
        """Get webservice URL, raise an exception if not exists."""
        if self._webservices.get(ws_key) is None:
            raise PyiCloudServiceNotActivatedException("Webservice not available", ws_key)
        return typing.cast(str, self._webservices[ws_key]["url"])

    @property
    def photos(self) -> PhotosService:
        """Gets the 'Photo' service."""
        if not self._photos:
            service_root = self._get_webservice_url("ckdatabasews")
            self._photos = PhotosService(service_root, self.session, self.params)
        return self._photos

    def __unicode__(self) -> str:
        return f"iCloud API: {self.apple_id}"

    def __str__(self) -> str:
        as_unicode = self.__unicode__()
        return as_unicode

    def __repr__(self) -> str:
        return f"<{str(self)}>"
