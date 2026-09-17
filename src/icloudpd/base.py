#!/usr/bin/env python
"""Main script that uses Click to parse command-line arguments"""

import datetime
import itertools
import json
import logging
import os
import sys
import typing
from functools import partial, singledispatch
from multiprocessing import freeze_support
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Sequence,
    Tuple,
)

from requests.exceptions import (
    ChunkedEncodingError,
    ContentDecodingError,
    StreamConsumedError,
    UnrewindableBodyError,
)
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from tzlocal import get_localzone

from foundation.core import compose, identity, map_, partial_1_1
from icloudpd import download, exif_datetime
from icloudpd.authentication import authenticator
from icloudpd.config import GlobalConfig, UserConfig
from icloudpd.counter import Counter
from icloudpd.filename_policies import create_filename_builder
from icloudpd.log_level import LogLevel
from icloudpd.paths import local_download_path, remove_unicode_chars
from icloudpd.string_helpers import parse_timestamp_or_timedelta, truncate_middle
from icloudpd.xmp_sidecar import generate_xmp_file
from pyicloud_ipd.asset_version import add_suffix_to_filename, calculate_version_filename
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionErrorException,
    PyiCloudFailedLoginException,
    PyiCloudFailedMFAException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailableException,
)
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.item_type import AssetItemType  # fmt: skip
from pyicloud_ipd.live_photo_mov_filename_policy import LivePhotoMovFilenamePolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.services.photos import (
    PhotoAlbum,
    PhotoAsset,
    PhotoLibrary,
)
from pyicloud_ipd.utils import (
    disambiguate_filenames,
    size_to_suffix,
)
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize

freeze_support()  # fmt: skip # fixing tqdm on macos


def build_filename_cleaner(keep_unicode: bool) -> Callable[[str], str]:
    """Build filename cleaner based on unicode preference.

    Args:
        keep_unicode: If True, preserve Unicode characters. If False, remove non-ASCII characters.

    Returns:
        Function that processes filenames according to unicode preference.
        Note: Basic filesystem character cleaning (clean_filename) is always applied in calculate_filename.
    """
    if keep_unicode:
        # Only basic cleaning is needed (already applied in calculate_filename)
        return identity
    else:
        # Apply unicode removal in addition to basic cleaning
        return remove_unicode_chars


def lp_filename_concatinator(filename: str) -> str:
    """Generate concatenator-style live photo filename, adding HEVC suffix for HEIC files"""
    import os

    from foundation.core import compose
    from foundation.string_utils import endswith, lower

    name, ext = os.path.splitext(filename)
    if not ext:
        return filename

    is_heic = compose(endswith(".heic"), lower)(ext)
    return name + ("_HEVC.MOV" if is_heic else ".MOV")


def lp_filename_original(filename: str) -> str:
    """Generate original-style live photo filename by replacing extension with .MOV"""
    from foundation.string_utils import replace_extension

    replace_with_mov = replace_extension(".MOV")
    return replace_with_mov(filename)


def skip_created_generator(
    name: str, formatted: str | None
) -> datetime.datetime | datetime.timedelta | None:
    """Converts ISO dates to datetime and interval in days to timeinterval using supplied name as part of raised exception in case of the error"""
    if formatted is None:
        return None
    result = parse_timestamp_or_timedelta(formatted)
    if result is None:
        raise ValueError(f"{name} parameter did not parse ISO timestamp or interval successfully")
    if isinstance(result, datetime.datetime):
        return ensure_tzinfo(get_localzone(), result)
    return result


def ensure_tzinfo(tz: datetime.tzinfo, input: datetime.datetime) -> datetime.datetime:
    if input.tzinfo is None:
        return input.astimezone(tz)
    return input


# Must import the constants object so that we can mock values in tests.


def create_logger(config: GlobalConfig) -> logging.Logger:
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logger = logging.getLogger("icloudpd")
    if config.only_print_filenames:
        logger.disabled = True
    else:
        # Need to make sure disabled is reset to the correct value,
        # because the logger instance is shared between tests.
        logger.disabled = False
        if config.log_level == LogLevel.DEBUG:
            logger.setLevel(logging.DEBUG)
        elif config.log_level == LogLevel.INFO:
            logger.setLevel(logging.INFO)
        elif config.log_level == LogLevel.ERROR:
            logger.setLevel(logging.ERROR)
        else:
            # Developer's error - not an exhaustive match
            raise ValueError(f"Unsupported logging level {config.log_level}")
    return logger


def run_with_configs(global_config: GlobalConfig, user_configs: Sequence[UserConfig]) -> int:
    """Run the application with the new configuration system"""

    logger = create_logger(global_config)

    for user_config in user_configs:
        with logging_redirect_tqdm():
            lp_filename_generator = (
                lp_filename_original
                if user_config.live_photo_mov_filename_policy == LivePhotoMovFilenamePolicy.ORIGINAL
                else lp_filename_concatinator
            )

            filename_cleaner = build_filename_cleaner(user_config.keep_unicode_in_filenames)

            filename_builder = create_filename_builder(
                user_config.file_match_policy, filename_cleaner
            )

            passer = partial(
                where_builder,
                logger,
                user_config.skip_videos,
                user_config.skip_created_before,
                user_config.skip_created_after,
                user_config.skip_photos,
                filename_builder,
            )

            downloader = (
                partial(
                    download_builder,
                    logger,
                    user_config.folder_structure,
                    user_config.directory,
                    user_config.sizes,
                    user_config.force_size,
                    global_config.only_print_filenames,
                    user_config.set_exif_datetime,
                    user_config.skip_live_photos,
                    user_config.live_photo_size,
                    user_config.dry_run,
                    user_config.file_match_policy,
                    user_config.xmp_sidecar,
                    lp_filename_generator,
                    filename_builder,
                    user_config.align_raw,
                )
                if user_config.directory is not None
                else (lambda _s, _c, _p: False)
            )

            logger.info(f"Processing user: {user_config.username}")
            result = core_single_run(
                logger,
                global_config,
                user_config,
                passer,
                downloader,
            )

            if result != 0:
                return result

    return 0


@singledispatch
def offset_to_datetime(offset: Any) -> datetime.datetime:
    raise NotImplementedError()


@offset_to_datetime.register(datetime.datetime)
def _(offset: datetime.datetime) -> datetime.datetime:
    return offset


@offset_to_datetime.register(datetime.timedelta)
def _(offset: datetime.timedelta) -> datetime.datetime:
    return datetime.datetime.now(get_localzone()) - offset


def where_builder(
    logger: logging.Logger,
    skip_videos: bool,
    skip_created_before: datetime.datetime | datetime.timedelta | None,
    skip_created_after: datetime.datetime | datetime.timedelta | None,
    skip_photos: bool,
    filename_builder: Callable[[PhotoAsset], str],
    photo: PhotoAsset,
) -> bool:
    if skip_videos and photo.item_type == AssetItemType.MOVIE:
        logger.debug(asset_type_skip_message(AssetItemType.IMAGE, filename_builder, photo))
        return False
    if skip_photos and photo.item_type == AssetItemType.IMAGE:
        logger.debug(asset_type_skip_message(AssetItemType.MOVIE, filename_builder, photo))
        return False

    if skip_created_before is not None:
        temp_created_before = offset_to_datetime(skip_created_before)
        if photo.created < temp_created_before:
            logger.debug(skip_created_before_message(temp_created_before, photo, filename_builder))
            return False

    if skip_created_after is not None:
        temp_created_after = offset_to_datetime(skip_created_after)
        if photo.created > temp_created_after:
            logger.debug(skip_created_after_message(temp_created_after, photo, filename_builder))
            return False

    return True


def skip_created_before_message(
    target_created_date: datetime.datetime,
    photo: PhotoAsset,
    filename_builder: Callable[[PhotoAsset], str],
) -> str:
    filename = filename_builder(photo)
    return f"Skipping {filename}, as it was created {photo.created}, before {target_created_date}."


def skip_created_after_message(
    target_created_date: datetime.datetime,
    photo: PhotoAsset,
    filename_builder: Callable[[PhotoAsset], str],
) -> str:
    filename = filename_builder(photo)
    return f"Skipping {filename}, as it was created {photo.created}, after {target_created_date}."


def download_builder(
    logger: logging.Logger,
    folder_structure: str,
    directory: str,
    primary_sizes: Sequence[AssetVersionSize],
    force_size: bool,
    only_print_filenames: bool,
    set_exif_datetime: bool,
    skip_live_photos: bool,
    live_photo_size: LivePhotoVersionSize,
    dry_run: bool,
    file_match_policy: FileMatchPolicy,
    xmp_sidecar: bool,
    lp_filename_generator: Callable[[str], str],
    filename_builder: Callable[[PhotoAsset], str],
    raw_policy: RawTreatmentPolicy,
    icloud: PyiCloudService,
    counter: Counter,
    photo: PhotoAsset,
) -> bool:
    """function for actually downloading the photos"""

    try:
        created_date = photo.created.astimezone(get_localzone())
    except (ValueError, OSError):
        logger.error("Could not convert photo created date to local timezone (%s)", photo.created)
        created_date = photo.created

    from foundation.core import compose
    from foundation.string_utils import eq, lower

    is_none_folder = compose(eq("none"), lower)

    if is_none_folder(folder_structure):
        date_path = ""
    else:
        try:
            date_path = folder_structure.format(created_date)
        except ValueError:  # pragma: no cover
            # This error only seems to happen in Python 2
            logger.error("Photo created date was not valid (%s)", photo.created)
            # e.g. ValueError: year=5 is before 1900
            # (https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/122)
            # Just use the Unix epoch
            created_date = datetime.datetime.fromtimestamp(0)
            date_path = folder_structure.format(created_date)

    try:
        versions, filename_overrides = disambiguate_filenames(
            photo.versions_with_raw_policy(raw_policy), primary_sizes, photo, lp_filename_generator
        )
    except KeyError as ex:
        print(f"KeyError: {ex} attribute was not found in the photo fields.")
        with open(file="icloudpd-photo-error.json", mode="w", encoding="utf8") as outfile:
            json.dump(
                {
                    "master_record": photo._master_record,
                    "asset_record": photo._asset_record,
                },
                outfile,
            )
        print("icloudpd has saved the photo record to: ./icloudpd-photo-error.json")
        print("Please create a Gist with the contents of this file: https://gist.github.com")
        print(
            "Then create an issue on GitHub: "
            "https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues"
        )
        print("Include a link to the Gist in your issue, so that we can see what went wrong.\n")
        return False

    download_dir = os.path.normpath(os.path.join(directory, date_path))
    success = False

    for download_size in primary_sizes:
        if download_size not in versions and download_size != AssetVersionSize.ORIGINAL:
            if force_size:
                error_filename = filename_builder(photo)
                logger.error(
                    "%s size does not exist for %s. Skipping...",
                    download_size.value,
                    error_filename,
                )
                continue
            if AssetVersionSize.ORIGINAL in primary_sizes:
                continue  # that should avoid double download for original
            download_size = AssetVersionSize.ORIGINAL

        version = versions[download_size]
        photo_filename = filename_builder(photo)
        filename = calculate_version_filename(
            photo_filename,
            version,
            download_size,
            lp_filename_generator,
            photo.item_type,
            filename_overrides.get(download_size),
        )

        download_path = local_download_path(filename, download_dir)

        original_download_path = None
        file_exists = os.path.isfile(download_path)
        if not file_exists and download_size == AssetVersionSize.ORIGINAL:
            # Deprecation - We used to download files like IMG_1234-original.jpg,
            # so we need to check for these.
            # Now we match the behavior of iCloud for Windows: IMG_1234.jpg
            original_download_path = add_suffix_to_filename("-original", download_path)
            file_exists = os.path.isfile(original_download_path)

        if file_exists:
            if file_match_policy == FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX:
                # for later: this crashes if download-size medium is specified
                file_size = os.stat(original_download_path or download_path).st_size
                photo_size = version.size
                if file_size != photo_size:
                    download_path = (f"-{photo_size}.").join(download_path.rsplit(".", 1))
                    logger.debug("%s deduplicated", truncate_middle(download_path, 96))
                    file_exists = os.path.isfile(download_path)
            if file_exists:
                counter.increment()
                logger.debug("%s already exists", truncate_middle(download_path, 96))

        if not file_exists:
            counter.reset()
            if only_print_filenames:
                print(download_path)
            else:
                truncated_path = truncate_middle(download_path, 96)
                logger.debug("Downloading %s...", truncated_path)

                download_result = download.download_media(
                    logger,
                    dry_run,
                    icloud,
                    photo,
                    download_path,
                    version,
                    download_size,
                    filename_builder,
                )
                success = download_result

                if download_result:
                    from foundation.core import compose
                    from foundation.string_utils import endswith, lower

                    is_jpeg = compose(endswith((".jpg", ".jpeg")), lower)

                    if (
                        not dry_run
                        and set_exif_datetime
                        and is_jpeg(filename)
                        and not exif_datetime.get_photo_exif(logger, download_path)
                    ):
                        # %Y:%m:%d looks wrong, but it's the correct format
                        date_str = created_date.strftime("%Y-%m-%d %H:%M:%S%z")
                        logger.debug("Setting EXIF timestamp for %s: %s", download_path, date_str)
                        exif_datetime.set_photo_exif(
                            logger,
                            download_path,
                            created_date.strftime("%Y:%m:%d %H:%M:%S"),
                        )
                    if not dry_run:
                        download.set_utime(download_path, created_date)
                    logger.info("Downloaded %s", truncated_path)

        if xmp_sidecar:
            generate_xmp_file(logger, download_path, photo._asset_record, dry_run)

    # Also download the live photo if present
    if not skip_live_photos:
        lp_size = live_photo_size
        photo_versions_with_policy = photo.versions_with_raw_policy(raw_policy)
        if lp_size in photo_versions_with_policy:
            version = photo_versions_with_policy[lp_size]
            lp_photo_filename = filename_builder(photo)
            lp_filename = calculate_version_filename(
                lp_photo_filename,
                version,
                lp_size,
                lp_filename_generator,
                photo.item_type,
            )
            if live_photo_size != LivePhotoVersionSize.ORIGINAL:
                # Add size to filename if not original
                lp_filename = add_suffix_to_filename(
                    size_to_suffix(live_photo_size),
                    lp_filename,
                )
            else:
                pass
            lp_download_path = os.path.join(download_dir, lp_filename)

            lp_file_exists = os.path.isfile(lp_download_path)

            if only_print_filenames:
                if not lp_file_exists:
                    print(lp_download_path)
                # Handle deduplication case for only_print_filenames
                if (
                    lp_file_exists
                    and file_match_policy == FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX
                ):
                    lp_file_size = os.stat(lp_download_path).st_size
                    lp_photo_size = version.size
                    if lp_file_size != lp_photo_size:
                        lp_download_path = (f"-{lp_photo_size}.").join(
                            lp_download_path.rsplit(".", 1)
                        )
                        logger.debug("%s deduplicated", truncate_middle(lp_download_path, 96))
                        # Print the deduplicated filename but don't download
                        print(lp_download_path)
            else:
                if lp_file_exists:
                    if file_match_policy == FileMatchPolicy.NAME_SIZE_DEDUP_WITH_SUFFIX:
                        lp_file_size = os.stat(lp_download_path).st_size
                        lp_photo_size = version.size
                        if lp_file_size != lp_photo_size:
                            lp_download_path = (f"-{lp_photo_size}.").join(
                                lp_download_path.rsplit(".", 1)
                            )
                            logger.debug("%s deduplicated", truncate_middle(lp_download_path, 96))
                            lp_file_exists = os.path.isfile(lp_download_path)
                    if lp_file_exists:
                        logger.debug("%s already exists", truncate_middle(lp_download_path, 96))
                if not lp_file_exists:
                    truncated_path = truncate_middle(lp_download_path, 96)
                    logger.debug("Downloading %s...", truncated_path)
                    download_result = download.download_media(
                        logger,
                        dry_run,
                        icloud,
                        photo,
                        lp_download_path,
                        version,
                        lp_size,
                        filename_builder,
                    )
                    success = download_result and success
                    if download_result:
                        logger.info("Downloaded %s", truncated_path)
    return success


def dump_responses(dumper: Callable[[Any], None], responses: List[Mapping[str, Any]]) -> None:
    # dump captured responses
    for entry in responses:
        # compose(logger.debug, compose(json.dumps, response_to_har))(response)
        dumper(json.dumps(entry, indent=2))


def asset_type_skip_message(
    desired_item_type: AssetItemType,
    filename_builder: Callable[[PhotoAsset], str],
    photo: PhotoAsset,
) -> str:
    photo_video_phrase = "photos" if desired_item_type == AssetItemType.IMAGE else "videos"
    filename = filename_builder(photo)
    return f"Skipping {filename}, only downloading {photo_video_phrase}. (Item type was: {photo.item_type})"


def core_single_run(
    logger: logging.Logger,
    global_config: GlobalConfig,
    user_config: UserConfig,
    passer: Callable[[PhotoAsset], bool],
    downloader: Callable[[PyiCloudService, Counter, PhotoAsset], bool],
) -> int:
    """Download all iCloud photos to a local directory for a single execution (no watch loop)"""

    skip_bar = not os.environ.get("FORCE_TQDM") and (
        global_config.only_print_filenames
        or global_config.no_progress_bar
        or not sys.stdout.isatty()
    )
    while True:  # retry loop (not watch - only for immediate retries)
        captured_responses: List[Mapping[str, Any]] = []

        def append_response(captured: List[Mapping[str, Any]], response: Mapping[str, Any]) -> None:
            captured.append(response)

        try:
            icloud = authenticator(
                logger,
                global_config.domain,
                user_config.username,
                partial(append_response, captured_responses),
                user_config.cookie_directory,
                os.environ.get("CLIENT_ID"),
            )

            # dump captured responses for debugging
            # dump_responses(logger.debug, captured_responses)

            # turn off response capture
            icloud.response_observer = None

            if user_config.list_libraries:
                library_names = (
                    icloud.photos.private_libraries.keys() | icloud.photos.shared_libraries.keys()
                )
                print(*library_names, sep="\n")
                return 0

            else:
                # Access to the selected library. Defaults to the primary photos object.
                if user_config.library:
                    if user_config.library in icloud.photos.private_libraries:
                        library_object: PhotoLibrary = icloud.photos.private_libraries[
                            user_config.library
                        ]
                    elif user_config.library in icloud.photos.shared_libraries:
                        library_object = icloud.photos.shared_libraries[user_config.library]
                    else:
                        logger.error("Unknown library: %s", user_config.library)
                        return 1
                else:
                    library_object = icloud.photos

                if user_config.list_albums:
                    print("Albums:")
                    album_titles = [str(a) for a in library_object.albums.values()]
                    print(*album_titles, sep="\n")
                    return 0
                else:
                    if not user_config.directory:
                        # should be checked upstream
                        raise NotImplementedError()
                    else:
                        pass

                    directory = os.path.normpath(user_config.directory)

                    if user_config.skip_photos or user_config.skip_videos:
                        photo_video_phrase = "photos" if user_config.skip_videos else "videos"
                    else:
                        photo_video_phrase = "photos and videos"
                    if len(user_config.albums) == 0:
                        album_phrase = ""
                    elif len(user_config.albums) == 1:
                        album_phrase = f" from album {','.join(user_config.albums)}"
                    else:
                        album_phrase = f" from albums {','.join(user_config.albums)}"

                    logger.debug(f"Looking up all {photo_video_phrase}{album_phrase}...")

                    albums: Iterable[PhotoAlbum] = (
                        list(map_(library_object.albums.__getitem__, user_config.albums))
                        if len(user_config.albums) > 0
                        else [library_object.all]
                    )
                    album_lengths: Callable[[Iterable[PhotoAlbum]], Iterable[int]] = partial_1_1(
                        map_, len
                    )

                    def sum_(inp: Iterable[int]) -> int:
                        return sum(inp)

                    photos_count: int | None = compose(sum_, album_lengths)(albums)
                    for photo_album in albums:
                        photos_enumerator: Iterable[PhotoAsset] = photo_album

                        # Optional: Only download the x most recent photos.
                        if user_config.recent is not None:
                            photos_count = user_config.recent
                            photos_top: Iterable[PhotoAsset] = itertools.islice(
                                photos_enumerator, user_config.recent
                            )
                        else:
                            photos_top = photos_enumerator

                        if user_config.until_found is not None:
                            photos_count = None
                            # ensure photos iterator doesn't have a known length
                            # photos_enumerator = (p for p in photos_enumerator)

                        # Skip the one-line progress bar if we're only printing the filenames,
                        # or if the progress bar is explicitly disabled,
                        # or if this is not a terminal (e.g. cron or piping output to file)
                        if skip_bar:
                            photos_bar: Iterable[PhotoAsset] = photos_top
                            # logger.set_tqdm(None)
                        else:
                            photos_bar = tqdm(
                                iterable=photos_top,
                                total=photos_count,
                                leave=False,
                                dynamic_ncols=True,
                                ascii=True,
                            )
                            # logger.set_tqdm(photos_enumerator)

                        if photos_count is not None:
                            plural_suffix = "" if photos_count == 1 else "s"
                            photos_count_str = (
                                "the first" if photos_count == 1 else str(photos_count)
                            )

                            if user_config.skip_photos or user_config.skip_videos:
                                photo_video_phrase = (
                                    "photo" if user_config.skip_videos else "video"
                                ) + plural_suffix
                            else:
                                photo_video_phrase = (
                                    "photo or video" if photos_count == 1 else "photos and videos"
                                )
                        else:
                            photos_count_str = "???"
                            if user_config.skip_photos or user_config.skip_videos:
                                photo_video_phrase = (
                                    "photos" if user_config.skip_videos else "videos"
                                )
                            else:
                                photo_video_phrase = "photos and videos"
                        logger.info(
                            ("Downloading %s %s %s to %s ..."),
                            photos_count_str,
                            ",".join([_s.value for _s in user_config.sizes]),
                            photo_video_phrase,
                            directory,
                        )

                        consecutive_files_found = Counter(0)

                        def should_break(counter: Counter) -> bool:
                            """Exit if until_found condition is reached"""
                            return (
                                user_config.until_found is not None
                                and counter.value() >= user_config.until_found
                            )

                        photos_counter = 0

                        download_photo = partial(downloader, icloud)

                        for item in photos_bar:
                            try:
                                if should_break(consecutive_files_found):
                                    logger.info(
                                        "Found %s consecutive previously downloaded photos. Exiting",
                                        user_config.until_found,
                                    )
                                    break
                                passer_result = passer(item)
                                passer_result and download_photo(consecutive_files_found, item)

                                photos_counter += 1

                            except StopIteration:
                                break

                        if global_config.only_print_filenames:
                            return 0
                        else:
                            pass

                        if user_config.skip_photos or user_config.skip_videos:
                            photo_video_phrase = "photos" if user_config.skip_videos else "videos"
                        else:
                            photo_video_phrase = "photos and videos"
                        logger.info(f"All {photo_video_phrase} have been downloaded")
        except (PyiCloudFailedLoginException, PyiCloudFailedMFAException) as error:
            logger.error(str(error))
            dump_responses(logger.debug, captured_responses)
            return 1
        except (
            PyiCloudServiceNotActivatedException,
            PyiCloudServiceUnavailableException,
            PyiCloudAPIResponseException,
            PyiCloudConnectionErrorException,
        ) as error:
            logger.error(str(error))
            dump_responses(logger.debug, captured_responses)
            return 1
        except (
            ChunkedEncodingError,
            ContentDecodingError,
            StreamConsumedError,
            UnrewindableBodyError,
        ) as error:
            logger.debug(error)
            logger.debug("Retrying...")
            # these errors we can safely retry
            continue
        except Exception:
            dump_responses(logger.debug, captured_responses)
            raise

        # In single run mode, we don't handle watch intervals - that's done at higher level
        break

    return 0
