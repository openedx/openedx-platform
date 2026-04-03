import logging

import six
from ccx_keys.locator import CCXLocator
from django.db import migrations

from openedx.core.djangoapps.django_comment_common.models import (
    FORUM_ROLE_ADMINISTRATOR,
    FORUM_ROLE_COMMUNITY_TA,
    FORUM_ROLE_MODERATOR,
    FORUM_ROLE_STUDENT
)
from openedx.core.djangoapps.django_comment_common.utils import (
    ADMINISTRATOR_ROLE_PERMISSIONS,
    MODERATOR_ROLE_PERMISSIONS,
    STUDENT_ROLE_PERMISSIONS
)

log = logging.getLogger("edx.ccx")


def seed_forum_roles_for_existing_ccx(apps, schema_editor):
    """
    Seed forum roles and make CCX coach forum admin for respective CCX(s).

    Arguments:
        apps (Applications): Apps in edX platform.
        schema_editor (SchemaEditor): For editing database schema i.e create, delete field (column)

    """
    CustomCourseForEdX = apps.get_model("ccx", "CustomCourseForEdX")
    Role = apps.get_model("django_comment_common", "Role")
    Permission = apps.get_model("django_comment_common", "Permission")
    db_alias = schema_editor.connection.alias
    # This will need to be changed if ccx gets moved out of the default db for some reason.
    if db_alias != 'default':
        log.info("Skipping CCX migration on non-default database.")
        return

    for ccx in CustomCourseForEdX.objects.all():
        if not ccx.course_id or ccx.course_id.deprecated:
            # prevent migration for deprecated course ids or invalid ids.
            log.warning(
                "Skipping CCX %s due to invalid or deprecated course_id: %s.",
                ccx.id,
                ccx.course_id
            )
            continue

        ccx_locator = CCXLocator.from_course_locator(ccx.course_id, str(ccx.id))

        # Create forum roles.
        admin_role, _ = Role.objects.get_or_create(name=FORUM_ROLE_ADMINISTRATOR, course_id=ccx_locator)
        moderator_role, _ = Role.objects.get_or_create(name=FORUM_ROLE_MODERATOR, course_id=ccx_locator)
        community_ta_role, _ = Role.objects.get_or_create(name=FORUM_ROLE_COMMUNITY_TA, course_id=ccx_locator)
        student_role, _ = Role.objects.get_or_create(name=FORUM_ROLE_STUDENT, course_id=ccx_locator)

        # Add permissions for each role.
        for name in ADMINISTRATOR_ROLE_PERMISSIONS:
            admin_role.permissions.add(Permission.objects.get_or_create(name=name)[0])
        for name in MODERATOR_ROLE_PERMISSIONS:
            moderator_role.permissions.add(Permission.objects.get_or_create(name=name)[0])
        for name in STUDENT_ROLE_PERMISSIONS:
            student_role.permissions.add(Permission.objects.get_or_create(name=name)[0])
        for permission in student_role.permissions.all():
            moderator_role.permissions.add(permission)
        for permission in moderator_role.permissions.all():
            community_ta_role.permissions.add(permission)
        for permission in moderator_role.permissions.all():
            admin_role.permissions.add(permission)

        # Make CCX coach forum admin.
        ccx.coach.roles.add(admin_role)

        log.info("Seeded forum permissions for CCX %s", ccx_locator)


class Migration(migrations.Migration):

    dependencies = [
        ('ccx', '0003_add_master_course_staff_in_ccx'),
        ('django_comment_common', '0002_forumsconfig'),
    ]

    operations = [
        migrations.RunPython(code=seed_forum_roles_for_existing_ccx, reverse_code=migrations.RunPython.noop)
    ]
