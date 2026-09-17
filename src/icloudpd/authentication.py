"""Session-only authentication.

This build cannot log in. It only reuses credentials created elsewhere: sign in
at icloud.com in a browser and import the resulting X-APPLE-WEBAUTH-* cookies
with tools/import_browser_cookies.py. There is deliberately no code path that
accepts, prompts for, stores or transmits an Apple ID password, and none that
performs 2FA/2SA, so expired cookies fail loudly instead of falling back to an
interactive login.
"""

import logging
from typing import Any, Callable, Mapping

from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.exceptions import PyiCloudFailedLoginException


def authenticator(
    logger: logging.Logger,
    domain: str,
    username: str,
    response_observer: Callable[[Mapping[str, Any]], None] | None = None,
    cookie_directory: str | None = None,
    client_id: str | None = None,
) -> PyiCloudService:
    """Attach to iCloud using an existing session; never logs in."""
    logger.debug("Attaching to existing iCloud session...")

    icloud = PyiCloudService(
        domain,
        username,
        response_observer,
        cookie_directory=cookie_directory,
        client_id=client_id,
    )

    if icloud.requires_2fa or icloud.requires_2sa:
        raise PyiCloudFailedLoginException(
            "The stored session needs two-factor authentication, which this build cannot do. "
            "Sign in at icloud.com in a browser, complete 2FA there, and re-import the "
            "X-APPLE-WEBAUTH-* cookies with tools/import_browser_cookies.py."
        )

    return icloud
