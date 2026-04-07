from django.db import migrations

from lms.djangoapps.grades.config.waffle import (
    ENFORCE_FREEZE_GRADE_AFTER_COURSE_END,
    REJECTED_EXAM_OVERRIDES_GRADE,
    WRITABLE_GRADEBOOK
)


def create_flag(apps, schema_editor):
    Flag = apps.get_model('waffle', 'Flag')
    # Replacement for flag_undefined_default=True on flag definition
    Flag.objects.get_or_create(
        name=REJECTED_EXAM_OVERRIDES_GRADE.name, defaults={'everyone': True}
    )
    Flag.objects.get_or_create(
        name=ENFORCE_FREEZE_GRADE_AFTER_COURSE_END.name, defaults={'everyone': True}
    )
    Flag.objects.get_or_create(
        name=WRITABLE_GRADEBOOK.name, defaults={'everyone': True}
    )


class Migration(migrations.Migration):
    dependencies = [
        ('grades', '0017_delete_manual_psgoverride_table'),
        ('waffle', '0001_initial'),
    ]

    operations = [
        # Do not remove the flags for rollback.  We don't want to lose originals if
        # they already existed, and it won't hurt if they are created.
        migrations.RunPython(create_flag, reverse_code=migrations.RunPython.noop),
    ]
