"""
Utilities for working with opaque-keys.

Consider moving these into opaque-keys if they generalize well.
"""
import hashlib
from typing import NamedTuple, Self

from opaque_keys.edx.keys import CourseKey, UsageKey


class BlockKey(NamedTuple):
    """
    A pair of strings (type, id) that uniquely identify an XBlock Usage within a LearningContext.

    Put another way: LearningContextKey * BlockKey = UsageKey.

    Example:
       "course-v1:myOrg+myCourse+myRun"                          <- LearningContextKey string
       ("html", "myBlock")                                       <- BlockKey
       "course-v1:myOrg+myCourse+myRun+type@html+block@myBlock"  <- UsageKey string
    """
    type: str
    id: str

    @classmethod
    def from_usage_key(cls, usage_key):
        return cls(usage_key.block_type, usage_key.block_id)

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"

    @classmethod
    def from_string(cls, s: str) -> Self:
        """
        Convert a BlockKey string into a BlockKey object.
        """
        parts = s.split(':')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid string format for BlockKey: {s}")
        return cls(parts[0], parts[1])

    def to_usage_key(self, course_key: CourseKey) -> UsageKey:
        """
        Converts this BlockKey into a UsageKey.
        """
        return course_key.make_usage_key(self.type, self.id)


def derive_key(source: UsageKey, dest_parent: BlockKey) -> BlockKey:
    """
    Return a new reproducible BlockKey for a given source usage and destination parent block.

    When recursively copying a block structure, we need to generate new block IDs for the
    blocks. We don't want to use the exact same IDs as we might copy blocks multiple times.
    However, we do want to be able to reproduce the same IDs when copying the same block
    so that if we ever need to re-copy the block from its source (that is, to update it with
    upstream changes) we don't affect any data tied to the ID, such as grades.

    This is used by the copy_from_template function of the modulestore, and can be used by
    pluggable django apps that need to copy blocks from one course to another in an
    idempotent way. In the future, this should be created into a proper API function
    in the spirit of OEP-49.
    """
    source_context = source.context_key
    if hasattr(source_context, 'for_version'):
        source_context = source_context.for_version(None)
    # Compute a new block ID. This new block ID must be consistent when this
    # method is called with the same (source, dest_parent) pair.
    # Note: years after this was written, mypy pointed out that the way we are
    # encoding & formatting the source context means it looks like b'....', ie
    # it literally contains the character 'b' and single quotes within the unique_data
    # string. So that's a little silly, but it's fine, and we can't change it now.
    unique_data = "{}:{}:{}".format(
        str(source_context).encode("utf-8"),  # type: ignore[str-bytes-safe]
        source.block_id,
        dest_parent.id,
    )
    new_block_id = hashlib.sha1(unique_data.encode('utf-8')).hexdigest()[:20]
    return BlockKey(source.block_type, new_block_id)
