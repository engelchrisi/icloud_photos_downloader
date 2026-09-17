import datetime
import inspect
import os
import shutil
import zoneinfo
from argparse import ArgumentError
from typing import Sequence, Tuple
from unittest import TestCase

import pytest

from icloudpd.cli import format_help, parse
from icloudpd.config import GlobalConfig, UserConfig
from icloudpd.log_level import LogLevel
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.live_photo_mov_filename_policy import LivePhotoMovFilenamePolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize
from tests.helpers import (
    path_from_project_root,
    run_icloudpd_test,
    run_main,
)


class CliTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog
        self.root_path = path_from_project_root(__file__)
        self.fixtures_path = os.path.join(self.root_path, "fixtures")

    def test_cli_help(self) -> None:
        result = format_help()
        # Test that help output contains key sections and content rather than exact formatting
        self.assertIn(
            "usage: icloudpd [GLOBAL] [COMMON] [<USER> [COMMON] <USER> [COMMON] ...]", result
        )
        self.assertIn("GLOBAL options. Applied for all user settings.", result)
        self.assertIn(
            "COMMON options. If specified before the first username, then used as defaults for settings for all users.",
            result,
        )
        self.assertIn("USER options. Can be specified for setting user configuration only.", result)

        # Test that all major options are present
        self.assertIn("--help, -h", result)
        self.assertIn("--version", result)
        self.assertIn("--username", result)
        self.assertIn("--directory", result)
        self.assertIn("--size", result)
        self.assertIn("--live-photo-size", result)
        self.assertIn("--dry-run", result)

        # Test that option descriptions are present
        self.assertIn("Show this information", result)
        self.assertIn("Show the version, commit hash, and timestamp", result)
        self.assertIn("Apple ID email address. Starts a new configuration", result)
        # Directory option exists with proper help text (format varies by Python version)
        self.assertTrue(
            "-d, --directory DIRECTORY" in result
            or "-d DIRECTORY, --directory DIRECTORY" in result,
            "Expected directory option format not found in help text",
        )
        self.assertIn("Local directory to use for downloads", result)

    def test_cli_parser(self) -> None:
        self.assertEqual.__self__.maxDiff = None  # type: ignore[attr-defined]
        self.assertEqual(
            parse(["--help"]),
            (
                GlobalConfig(
                    help=True,
                    version=False,
                    use_os_locale=False,
                    only_print_filenames=False,
                    log_level=LogLevel.DEBUG,
                    no_progress_bar=False,
                    threads_num=1,
                    domain="com",
                ),
                [],
            ),
            "--help",
        )
        self.assertEqual(
            parse(["--version", "--use-os-locale"]),
            (
                GlobalConfig(
                    help=False,
                    version=True,
                    use_os_locale=True,
                    only_print_filenames=False,
                    log_level=LogLevel.DEBUG,
                    no_progress_bar=False,
                    threads_num=1,
                    domain="com",
                ),
                [],
            ),
            "--version --use-os-locale",
        )
        self.assertEqual(
            parse(
                ["--directory", "abc", "--username", "u1", "--username", "u2", "--directory", "def"]
            ),
            (
                GlobalConfig(
                    help=False,
                    version=False,
                    use_os_locale=False,
                    only_print_filenames=False,
                    log_level=LogLevel.DEBUG,
                    no_progress_bar=False,
                    threads_num=1,
                    domain="com",
                ),
                [
                    UserConfig(
                        directory="abc",
                        username="u1",
                        cookie_directory="~/.pyicloud",
                        sizes=[AssetVersionSize.ORIGINAL],
                        live_photo_size=LivePhotoVersionSize.ORIGINAL,
                        recent=None,
                        until_found=None,
                        albums=[],
                        list_albums=False,
                        library="PrimarySync",
                        list_libraries=False,
                        skip_videos=False,
                        skip_live_photos=False,
                        xmp_sidecar=False,
                        force_size=False,
                        folder_structure="{:%Y/%m/%d}",
                        set_exif_datetime=False,
                        dry_run=False,
                        keep_unicode_in_filenames=False,
                        live_photo_mov_filename_policy=LivePhotoMovFilenamePolicy.SUFFIX,
                        align_raw=RawTreatmentPolicy.AS_IS,
                        file_match_policy=FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX,
                        skip_created_before=None,
                        skip_created_after=None,
                        skip_photos=False,
                    ),
                    UserConfig(
                        directory="def",
                        cookie_directory="~/.pyicloud",
                        username="u2",
                        sizes=[AssetVersionSize.ORIGINAL],
                        live_photo_size=LivePhotoVersionSize.ORIGINAL,
                        recent=None,
                        until_found=None,
                        albums=[],
                        list_albums=False,
                        library="PrimarySync",
                        list_libraries=False,
                        skip_videos=False,
                        skip_live_photos=False,
                        xmp_sidecar=False,
                        force_size=False,
                        folder_structure="{:%Y/%m/%d}",
                        set_exif_datetime=False,
                        dry_run=False,
                        keep_unicode_in_filenames=False,
                        live_photo_mov_filename_policy=LivePhotoMovFilenamePolicy.SUFFIX,
                        align_raw=RawTreatmentPolicy.AS_IS,
                        file_match_policy=FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX,
                        skip_created_before=None,
                        skip_created_after=None,
                        skip_photos=False,
                    ),
                ],
            ),
            "defaults propagated and overwritten",
        )
        self.assertEqual(
            parse(
                [
                    "-d",
                    "abc",
                    "--username",
                    "u1",
                    "--skip-created-before",
                    "2025-01-02",
                    "--skip-created-after",
                    "2d",
                ]
            ),
            (
                GlobalConfig(
                    help=False,
                    version=False,
                    use_os_locale=False,
                    only_print_filenames=False,
                    log_level=LogLevel.DEBUG,
                    no_progress_bar=False,
                    threads_num=1,
                    domain="com",
                ),
                [
                    UserConfig(
                        directory="abc",
                        username="u1",
                        cookie_directory="~/.pyicloud",
                        sizes=[AssetVersionSize.ORIGINAL],
                        live_photo_size=LivePhotoVersionSize.ORIGINAL,
                        recent=None,
                        until_found=None,
                        albums=[],
                        list_albums=False,
                        library="PrimarySync",
                        list_libraries=False,
                        skip_videos=False,
                        skip_live_photos=False,
                        xmp_sidecar=False,
                        force_size=False,
                        folder_structure="{:%Y/%m/%d}",
                        set_exif_datetime=False,
                        dry_run=False,
                        keep_unicode_in_filenames=False,
                        live_photo_mov_filename_policy=LivePhotoMovFilenamePolicy.SUFFIX,
                        align_raw=RawTreatmentPolicy.AS_IS,
                        file_match_policy=FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX,
                        skip_created_before=datetime.datetime(
                            year=2025, month=1, day=2, tzinfo=zoneinfo.ZoneInfo(key="Etc/UTC")
                        ),
                        skip_created_after=datetime.timedelta(days=2),
                        skip_photos=False,
                    ),
                ],
            ),
            "valid skip-created parsed",
        )
        with pytest.raises(
            ArgumentError,
            match="argument --skip-created-before: Not an ISO timestamp or time interval in days",
        ):
            _ = parse(
                [
                    "-d",
                    "abc",
                    "--username",
                    "u1",
                    "--skip-created-before",
                    "2025-01-33",
                    "--skip-created-after",
                    "2d",
                ]
            )
        with pytest.raises(
            ArgumentError,
            match="argument --skip-created-after: Not an ISO timestamp or time interval in days",
        ):
            _ = parse(
                [
                    "-d",
                    "abc",
                    "--username",
                    "u1",
                    "--skip-created-before",
                    "2025-01-02",
                    "--skip-created-after",
                    "2",
                ]
            )

    def test_cli(self) -> None:
        result = run_main(["--help"])
        self.assertEqual(result.exit_code, 0, "exit code")

    def test_log_levels(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])

        parameters: Sequence[Tuple[str, Sequence[str], Sequence[str]]] = [
            ("debug", ["DEBUG", "INFO"], []),
            ("info", ["INFO"], ["DEBUG"]),
            ("error", [], ["DEBUG", "INFO"]),
        ]
        for log_level, expected, not_expected in parameters:
            self._caplog.clear()
            _, result = run_icloudpd_test(
                self.assertEqual,
                self.root_path,
                base_dir,
                "listing_photos.yml",
                [],
                [],
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--recent",
                    "0",
                    "--log-level",
                    log_level,
                ],
            )
            self.assertEqual(result.exit_code, 0, "exit code")
            for text in expected:
                self.assertIn(text, self._caplog.text)
            for text in not_expected:
                self.assertNotIn(text, self._caplog.text)

    def test_tqdm(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])

        _, result = run_icloudpd_test(
            self.assertEqual,
            self.root_path,
            base_dir,
            "listing_photos.yml",
            [],
            [],
            [
                "--username",
                "jdoe@gmail.com",
                "--recent",
                "0",
            ],
            additional_env={"FORCE_TQDM": "yes"},
        )
        self.assertEqual(result.exit_code, 0, "exit code")

    def test_unicode_directory(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])

        _, result = run_icloudpd_test(
            self.assertEqual,
            self.root_path,
            base_dir,
            "listing_photos.yml",
            [],
            [],
            [
                "--username",
                "jdoe@gmail.com",
                "--recent",
                "0",
                "--log-level",
                "info",
            ],
        )
        self.assertEqual(result.exit_code, 0, "exit code")

    def test_missing_directory(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        # need path removed
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)

        result = run_main(
            [
                "--username",
                "jdoe@gmail.com",
                "--recent",
                "0",
                "--log-level",
                "info",
                "-d",
                base_dir,
            ],
        )
        # fails on the missing session before the directory is ever touched
        self.assertEqual(result.exit_code, 1, "exit code")

        self.assertFalse(os.path.exists(base_dir), f"{base_dir} exists")

    def test_missing_directory_param(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        result = run_main(
            [
                "--username",
                "jdoe@gmail.com",
                "--recent",
                "0",
                "--log-level",
                "info",
            ],
        )
        self.assertEqual(result.exit_code, 2, "exit code")

        self.assertFalse(os.path.exists(base_dir), f"{base_dir} exists")
