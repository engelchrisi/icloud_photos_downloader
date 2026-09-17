"""Session-only authentication.

This build cannot log in. It only reuses a session that was created elsewhere
(by the unmodified upstream icloudpd on a trusted machine, via `--auth-only`)
and copied into the cookie directory. There is deliberately no code path that
accepts, prompts for, stores or transmits an Apple ID password, and none that
performs 2FA/2SA, so a session that has expired fails loudly instead of falling
back to an interactive login.
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
            "Re-run the upstream icloudpd with --auth-only on a trusted machine and copy the "
            "refreshed session and cookie files into the cookie directory."
        )

    return icloud
