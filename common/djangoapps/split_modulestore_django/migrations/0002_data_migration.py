import logging

from django.db import migrations, models
from django.db.utils import IntegrityError

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

from ..models import SplitModulestoreCourseIndex as SplitModulestoreCourseIndex_Real

log = logging.getLogger(__name__)


def forwards_func(apps, schema_editor):
    """
    Copy all course index data from MongoDB to MySQL, unless it's already present in MySQL.

    This migration is used as part of an upgrade path from storing course indexes in MongoDB to storing them in MySQL.
    On edX.org, we began writing to MySQL+MongoDB before we deployed this migration, so some courses are already in
    MySQL. But any courses that haven't been modified recently would only be in MongoDB and need to be copied over to
    MySQL before we can switch reading course indexes to MySQL.
    """
    db_alias = schema_editor.connection.alias
    SplitModulestoreCourseIndex = apps.get_model("split_modulestore_django", "SplitModulestoreCourseIndex")
    split_modulestore = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.split)

    for course_index in split_modulestore.db_connection.find_matching_course_indexes(force_mongo=True):
        data = SplitModulestoreCourseIndex_Real.fields_from_v1_schema(course_index)
        course_id = data["course_id"]

        try:
            mysql_entry = SplitModulestoreCourseIndex.objects.get(course_id=course_id)
            # This course index ("active version") already exists in MySQL.
            # Let's just make sure it's the latest version. If the MongoDB somehow contains a newer version, something
            # has gone wrong and we should investigate to ensure we're not losing any data.
            if mysql_entry.edited_on < data["edited_on"]:
                log.error(
                    "Possible data issue found during data migration of course indexes from MongoDB to MySQL: \n"
                    f"Course {course_id} already exists in MySQL but the MongoDB version is newer. "
                    "That's unexpected because since the course index table was added to MySQL, there has never been a "
                    "time when we would write course_indexes updates only to MongoDB without also writing to MySQL. "
                    "\nMongo data: "
                    f"edited_on: {data['edited_on']}, "
                    f"last_update: {data['last_update']}, "
                    f"published_version: {data.get('published_version', 'none')}"
                    "\nMySQL data: "
                    f"edited_on: {mysql_entry.edited_on}, "
                    f"last_update: {mysql_entry.last_update}, "
                    f"published_version: {mysql_entry.published_version}"
                    "\nThe MySQL version will be overwritten and the MongoDB version used."
                )
                for key in (
                    "edited_on", "edited_by_id", "last_update",
                    "draft_version", "published_version", "library_version"
                ):
                    if key in data:  # library_version is probably not in data, that's OK
                        setattr(mysql_entry, key, data[key])
                mysql_entry.save(using=db_alias)
        except SplitModulestoreCourseIndex.DoesNotExist:
            # This course exists in MongoDB but hasn't yet been migrated to MySQL. Do that now.
            SplitModulestoreCourseIndex(**data).save(using=db_alias)

def reverse_func(apps, schema_editor):
    """
    Reversing the data migration is a no-op, because edX.org used a migration path path that started with writing to
    both MySQL+MongoDB while still reading from MongoDB, then later executed this data migration, then later cut over to
    reading from MySQL only. If we reversed this by deleting all entries, it would undo any writes that took place
    before this data migration, which are unrelated.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('split_modulestore_django', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]
