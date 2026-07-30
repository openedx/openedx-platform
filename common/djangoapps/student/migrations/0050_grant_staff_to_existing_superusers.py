"""
Backfill ``is_staff`` for existing superusers.

A new ``pre_save`` signal grants ``is_staff`` to any superuser saved from now
on, but that does not touch superusers that already exist in the database. This
one-time data migration grants staff access to those accounts so the behaviour
is consistent for old and new superusers alike.
"""

from django.conf import settings
from django.db import migrations


def grant_staff_to_superusers(apps, schema_editor):
    """Mark every existing superuser as staff."""
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.', 1))
    User.objects.filter(is_superuser=True, is_staff=False).update(is_staff=True)


def noop(apps, schema_editor):
    """
    Reverse is a no-op.

    We cannot know which superusers were intentionally non-staff before this
    migration ran, so removing ``is_staff`` on reverse would be unsafe.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('student', '0049_manualenrollmentaudit_statetransition_typo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(grant_staff_to_superusers, noop),
    ]
