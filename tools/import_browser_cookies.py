#!/usr/bin/env python
"""Build an icloudpd cookie jar from cookies copied out of a browser.

Run this on a trusted machine after signing in at icloud.com. Your Apple ID
password never reaches this script or any downloader code — it goes only to
Apple, through the browser.

Where to get the values: devtools -> Application -> Cookies ->
https://www.icloud.com. The cookies are HttpOnly, so page JavaScript cannot
read them, but devtools shows them.

    python tools/import_browser_cookies.py \
        --username you@example.com \
        --cookie-directory ./session \
        --from-json cookies.json

cookies.json maps cookie names to values, e.g.

    {
      "X-APPLE-WEBAUTH-LOGIN": "v=1:t=...",
      "X-APPLE-WEBAUTH-VALIDATE": "v=1:t=...",
      "X-APPLE-WEBAUTH-HSA-TRUST": "...",
      "X-APPLE-UNIQUE-CLIENT-ID": "..."
    }

Copy whatever the browser actually shows for that domain; the exact set varies
(Apple has renamed these), and every cookie in the jar is sent regardless.

Prefer the JSON file over --cookie on the command line: values are bearer
credentials and a command line ends up in your shell history. Delete the JSON
file afterwards, and never commit it — this repository is public.

This script makes no network connections and imports nothing outside the
standard library.
"""

import argparse
import json
import os
import re
import sys
from http.cookiejar import Cookie, LWPCookieJar

DOMAIN = ".icloud.com"

# The cookies a signed-in icloud.com session holds. Anything else is accepted
# with a note rather than rejected: the whole jar is sent to Apple regardless of
# what is listed here, and Apple does change this set. Observed 2026-09-18:
# X-APPLE-WEBAUTH-HSA-TRUST in place of the X-APPLE-WEBAUTH-HSA-LOGIN that
# upstream's fixtures show, so both are listed as expected.
KNOWN_COOKIES = (
    "X-APPLE-WEBAUTH-TOKEN",
    "X-APPLE-WEBAUTH-LOGIN",
    "X-APPLE-WEBAUTH-VALIDATE",
    "X-APPLE-WEBAUTH-HSA-TRUST",
    "X-APPLE-WEBAUTH-HSA-LOGIN",
    "X-APPLE-WEBAUTH-USER",
    "X-APPLE-UNIQUE-CLIENT-ID",
    "X-APPLE-DS-WEB-SESSION-TOKEN",
)

# Required. X-APPLE-WEBAUTH-TOKEN is the one Apple names explicitly: without it
# /validate answers 421 with "Missing X-APPLE-WEBAUTH-TOKEN cookie", which is
# easy to misread as a wrong password or an expired session.
REQUIRED_COOKIES = (
    "X-APPLE-WEBAUTH-TOKEN",
    "X-APPLE-WEBAUTH-VALIDATE",
)


def cookiejar_path(cookie_directory: str, username: str) -> str:
    """Mirror PyiCloudService.cookiejar_path so icloudpd finds the jar."""
    sanitised = "".join(c for c in username if re.match(r"\w", c))
    return os.path.join(cookie_directory, sanitised)


def make_cookie(name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=DOMAIN,
        domain_specified=True,
        domain_initial_dot=True,
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--username", required=True, help="Apple ID email address")
    parser.add_argument(
        "--cookie-directory", required=True, help="Directory to write the cookie jar into"
    )
    parser.add_argument(
        "--from-json",
        help="JSON file mapping cookie names to values (preferred: keeps secrets out of shell history)",
    )
    parser.add_argument(
        "--from-cookie-header",
        help="File containing a raw 'Cookie:' request header copied from devtools -> Network. "
        "Easiest and least error-prone: it is exactly the set the browser sends.",
    )
    parser.add_argument(
        "--cookie",
        nargs=2,
        action="append",
        metavar=("NAME", "VALUE"),
        default=[],
        help="A single cookie, repeatable. Ends up in shell history — prefer --from-json.",
    )
    return parser.parse_args(argv)


def parse_cookie_header(text: str) -> dict[str, str]:
    """Parse a raw 'Cookie:' request header into name -> value.

    Values are split on the first '=' only, since several Apple cookie values
    are base64 and contain '=' padding.

    >>> parse_cookie_header("Cookie: a=1; b=v=2:t=x==")
    {'a': '1', 'b': 'v=2:t=x=='}
    """
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1]
    cookies: dict[str, str] = {}
    for pair in text.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            print(f"note: skipping malformed cookie fragment {pair!r}", file=sys.stderr)
            continue
        name, value = pair.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def collect_cookies(args: argparse.Namespace) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if args.from_cookie_header:
        with open(args.from_cookie_header, encoding="utf-8-sig") as handle:
            cookies.update(parse_cookie_header(handle.read()))
    if args.from_json:
        with open(args.from_json, encoding="utf-8") as handle:
            loaded = json.load(handle)
        if not isinstance(loaded, dict):
            raise SystemExit(f"{args.from_json} must contain a JSON object of name -> value")
        cookies.update({str(k): str(v) for k, v in loaded.items()})
    cookies.update({name: value for name, value in args.cookie})
    return cookies


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    cookies = collect_cookies(args)

    if not cookies:
        raise SystemExit("No cookies given. Use --from-cookie-header, --from-json or --cookie.")

    missing_required = [name for name in REQUIRED_COOKIES if name not in cookies]
    if missing_required:
        raise SystemExit(
            f"Refusing to write a jar without: {', '.join(missing_required)}. "
            "Authentication cannot succeed without these, and a jar that exists but "
            "does not work is harder to diagnose than a missing one."
        )
    unexpected = [name for name in cookies if name not in KNOWN_COOKIES]
    if unexpected:
        print(f"note: passing through additional cookies: {', '.join(unexpected)}", file=sys.stderr)

    os.makedirs(args.cookie_directory, mode=0o700, exist_ok=True)
    path = cookiejar_path(args.cookie_directory, args.username)

    jar = LWPCookieJar(filename=path)
    for name, value in cookies.items():
        jar.set_cookie(make_cookie(name, value))
    jar.save(ignore_discard=True, ignore_expires=True)

    # LWPCookieJar.save writes in text mode, so on Windows it emits CRLF. The
    # jar is typically imported here and then used on Linux, where the LWP
    # parser strips only the "\n" -- the surviving "\r" lands inside the last
    # attribute of each cookie. Normalise so the file is platform-independent.
    with open(path, "rb") as handle:
        raw = handle.read()
    if b"\r\n" in raw:
        with open(path, "wb") as handle:
            handle.write(raw.replace(b"\r\n", b"\n"))

    os.chmod(path, 0o600)

    print(f"Wrote {len(cookies)} cookies to {path}")
    print("These cookies grant access to your iCloud photo library. Treat them as secrets:")
    print("  - do not commit them (this repository is public)")
    print("  - delete any JSON file you copied them from")
    print(f"Verify with: icloudpd --username {args.username} "
          f"--cookie-directory {args.cookie_directory} --list-albums")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
