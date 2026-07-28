"""Constants used by the Open edX Authorization (AuthZ) framework."""

from enum import Enum

from common.djangoapps.student.auth import has_studio_read_access, has_studio_write_access


class LegacyAuthoringPermission(Enum):
    READ = "read"
    WRITE = "write"


LEGACY_PERMISSION_HANDLER_MAP = {
    LegacyAuthoringPermission.READ: has_studio_read_access,
    LegacyAuthoringPermission.WRITE: has_studio_write_access,
}
