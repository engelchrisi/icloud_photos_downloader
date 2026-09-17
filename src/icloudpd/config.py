import datetime
from dataclasses import dataclass
from typing import Sequence

from icloudpd.log_level import LogLevel
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.live_photo_mov_filename_policy import LivePhotoMovFilenamePolicy
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.version_size import AssetVersionSize, LivePhotoVersionSize


@dataclass(kw_only=True)
class _DefaultConfig:
    directory: str
    cookie_directory: str
    sizes: Sequence[AssetVersionSize]
    live_photo_size: LivePhotoVersionSize
    recent: int | None
    until_found: int | None
    albums: Sequence[str]
    list_albums: bool
    library: str
    list_libraries: bool
    skip_videos: bool
    skip_live_photos: bool
    xmp_sidecar: bool
    force_size: bool
    folder_structure: str
    set_exif_datetime: bool
    dry_run: bool
    keep_unicode_in_filenames: bool
    live_photo_mov_filename_policy: LivePhotoMovFilenamePolicy
    align_raw: RawTreatmentPolicy
    file_match_policy: FileMatchPolicy
    skip_created_before: datetime.datetime | datetime.timedelta | None
    skip_created_after: datetime.datetime | datetime.timedelta | None
    skip_photos: bool


@dataclass(kw_only=True)
class UserConfig(_DefaultConfig):
    username: str


@dataclass(kw_only=True)
class GlobalConfig:
    help: bool
    version: bool
    use_os_locale: bool
    only_print_filenames: bool
    log_level: LogLevel
    no_progress_bar: bool
    threads_num: int
    domain: str
