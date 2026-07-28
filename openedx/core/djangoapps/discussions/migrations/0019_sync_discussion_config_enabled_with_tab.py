"""
Backfill DiscussionsConfiguration.enabled and CourseAppStatus.enabled for
existing courses to match CourseOverviewTab.is_hidden.

When a course is imported, the discussion tab's is_hidden value is carried over
from the source course. However, DiscussionsConfiguration.enabled and
CourseAppStatus.enabled default to True and are not updated from the imported
tab state, causing a desync.

This is a one-time backfill for existing courses. Going forward, the
update_course_discussion_config handler keeps these values in sync on every
course publish.
"""

from django.db import migrations


def sync_enabled_from_course_overview_tab(apps, schema_editor):
    CourseOverviewTab = apps.get_model("course_overviews", "CourseOverviewTab")
    DiscussionsConfiguration = apps.get_model("discussions", "DiscussionsConfiguration")
    CourseAppStatus = apps.get_model("course_apps", "CourseAppStatus")

    discussion_tabs = CourseOverviewTab.objects.filter(tab_id="discussion").select_related("course_overview")

    for tab in discussion_tabs.iterator():
        course_key = tab.course_overview_id
        expected_enabled = not tab.is_hidden
        DiscussionsConfiguration.objects.filter(
            context_key=course_key,
        ).exclude(
            enabled=expected_enabled,
        ).update(enabled=expected_enabled)
        CourseAppStatus.objects.filter(
            course_key=course_key,
            app_id="discussion",
        ).exclude(
            enabled=expected_enabled,
        ).update(enabled=expected_enabled)


class Migration(migrations.Migration):

    dependencies = [
        ("discussions", "0018_auto_20230904_1054"),
        ("course_overviews", "0030_backfill_new_catalog_courseruns"),
        ("course_apps", "0002_alter_historicalcourseappstatus_options"),
    ]

    operations = [
        migrations.RunPython(sync_enabled_from_course_overview_tab, migrations.RunPython.noop),
    ]
