"""
Django model to store the "course index" data
"""
from bson.objectid import ObjectId
from django.contrib.auth import get_user_model
from django.db import models
from opaque_keys.edx.locator import CourseLocator, LibraryLocator
from opaque_keys.edx.django.models import LearningContextKeyField
from simple_history.models import HistoricalRecords

from xmodule.modulestore import ModuleStoreEnum
from xmodule.util.misc import get_library_or_course_attribute

User = get_user_model()


class SplitModulestoreCourseIndex(models.Model):
    """
    A "course index" for a course in "split modulestore."

    This model/table mostly stores the current version of each course.
    (Well, twice for each course - "draft" and "published" branch versions are
    tracked separately.)

    This MySQL table / django model is designed to replace the "active_versions"
    MongoDB collection. They contain the same information.

    It also stores the "wiki_slug" to facilitate looking up a course
    by it's wiki slug, which is required due to the nuances of the
    django-wiki integration.

    .. no_pii:
    """
    # For compatibility with MongoDB, each course index must have an ObjectId. We still have an integer primary key too.
    objectid = models.CharField(max_length=24, null=False, blank=False, unique=True)

    # course_id: The ID of this course (or library). Must start with "course-v1:" or "library-v1:"
    # This is case-sensitive; however, many other parts of the system aren't case sensitive, so we add an explicit index
    # on Lower(course_id) to make this case-insensitively unique as well.
    # So: (1) queries of course_id by default are case-sensitive. (2) queries that want to be case-insensitive need to
    # explicitly compare Lower(course_id) with the lowercase key in question. (3) Course IDs that differ only in case
    # are prohibited.
    course_id = LearningContextKeyField(case_sensitive=True, unique=True, null=False)
    # Extract the "org" value from the course_id key so that we can search by org.
    # This gets set automatically by clean()
    org = models.CharField(max_length=255, db_index=True)

    # Version fields: The ObjectId of the current entry in the "structures" collection, for this course.
    # The version is stored separately for each "branch".
    # Note that there are only three branch names allowed. Draft/published are used for courses, while "library" is used
    # for content libraries.

    # ModuleStoreEnum.BranchName.draft = 'draft-branch'
    draft_version = models.CharField(max_length=24, null=False, blank=True)
    # ModuleStoreEnum.BranchName.published = 'published-branch'
    published_version = models.CharField(max_length=24, null=False, blank=True)
    # ModuleStoreEnum.BranchName.library = 'library'
    library_version = models.CharField(max_length=24, null=False, blank=True)

    # Wiki slug for this course
    wiki_slug = models.CharField(max_length=255, db_index=True, blank=True)

    # Base store - whether the "structures" and "definitions" data are in MongoDB or object storage (S3)
    BASE_STORE_MONGO = "mongodb"
    BASE_STORE_DJANGO = "django"
    BASE_STORE_CHOICES = [
        (BASE_STORE_MONGO, "MongoDB"),  # For now, MongoDB is the only implemented option
        (BASE_STORE_DJANGO, "Django - not implemented yet"),
    ]
    base_store = models.CharField(max_length=20, blank=False, choices=BASE_STORE_CHOICES)

    # Edit history:
    # ID of the user that made the latest edit. This is not a ForeignKey because some values (like
    # ModuleStoreEnum.UserID.*) are not real user IDs.
    edited_by_id = models.IntegerField(null=True)
    edited_on = models.DateTimeField()
    # last_update is different from edited_on, and is used only to prevent collisions?
    last_update = models.DateTimeField()

    # Keep track of the history of this table:
    history = HistoricalRecords()

    def __str__(self):
        return f"Course Index ({self.course_id})"

    class Meta:
        ordering = ["course_id"]
        verbose_name_plural = "Split modulestore course indexes"
        constraints = [
            # Explicitly force "course_id" to be case-insensitively unique
            models.UniqueConstraint(
                models.functions.Lower("course_id"),
                name="splitmodulestorecourseindex_courseid_unique_ci",
            ),
        ]

    def as_v1_schema(self):
        """ Return in the same format as was stored in MongoDB """
        versions = {}
        for branch in ("draft", "published", "library"):
            # The current version of this branch, a hex-encoded ObjectID - or an empty string:
            version_str = getattr(self, f"{branch}_version")
            if version_str:
                versions[getattr(ModuleStoreEnum.BranchName, branch)] = ObjectId(version_str)
        return {
            "_id": ObjectId(self.objectid),
            "org": self.course_id.org,
            "course": get_library_or_course_attribute(self.course_id),
            "run": self.course_id.run,  # pylint: disable=no-member
            "edited_by": self.edited_by_id,
            "edited_on": self.edited_on,
            "last_update": self.last_update,
            "versions": versions,
            "schema_version": 1,  # This matches schema version 1, see SplitMongoModuleStore.SCHEMA_VERSION
            "search_targets": {"wiki_slug": self.wiki_slug},
        }

    @staticmethod
    def fields_from_v1_schema(values):
        """ Convert the MongoDB-style dict shape to a dict of fields that match this model """
        if values["run"] == LibraryLocator.RUN and ModuleStoreEnum.BranchName.library in values["versions"]:
            # This is a content library:
            locator = LibraryLocator(org=values["org"], library=values["course"])
        else:
            # This is a course:
            locator = CourseLocator(org=values["org"], course=values["course"], run=values["run"])
        result = {
            "course_id": locator,
            "org": values["org"],
            "edited_by_id": values["edited_by"],
            "edited_on": values["edited_on"],
            "base_store": SplitModulestoreCourseIndex.BASE_STORE_MONGO,
        }
        if "_id" in values:
            result["objectid"] = str(values["_id"])  # Convert ObjectId to its hex representation
        if "last_update" in values:
            result["last_update"] = values["last_update"]
        if "search_targets" in values and "wiki_slug" in values["search_targets"]:
            result["wiki_slug"] = values["search_targets"]["wiki_slug"]
        for branch in ("draft", "published", "library"):
            version = values["versions"].get(getattr(ModuleStoreEnum.BranchName, branch))
            if version:
                result[f"{branch}_version"] = str(version)  # Convert version from ObjectId to hex string
        return result

    @staticmethod
    def field_name_for_branch(branch_name):
        """ Given a full branch name, get the name of the field in this table that stores that branch's version """
        if branch_name == ModuleStoreEnum.BranchName.draft:
            return "draft_version"
        if branch_name == ModuleStoreEnum.BranchName.published:
            return "published_version"
        if branch_name == ModuleStoreEnum.BranchName.library:
            return "library_version"
        raise ValueError(f"Unknown branch name: {branch_name}")

    def clean(self):
        """
        Validation for this model
        """
        super().clean()
        # Check that course_id is a supported type:
        course_id_str = str(self.course_id)
        if not course_id_str.startswith("course-v1:") and not course_id_str.startswith("library-v1:"):
            raise ValueError(
                f"Split modulestore cannot store course[like] object with key {course_id_str}"
                " - only course-v1/library-v1 prefixed keys are supported."
            )
        # Set the "org" field automatically - ensure it always matches the "org" in the course_id
        self.org = self.course_id.org

    def save(self, *args, **kwargs):
        """ Save this model """
        # Override to ensure that full_clean()/clean() is always called, so that the checks in clean() above are run.
        # But don't run validations; they just run extra queries and the database enforces them anyways.
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)
