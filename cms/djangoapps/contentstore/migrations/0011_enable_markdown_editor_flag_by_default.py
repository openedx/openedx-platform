from django.db import migrations

from cms.djangoapps.contentstore.toggles import ENABLE_REACT_MARKDOWN_EDITOR


def create_flag(apps, schema_editor):
    Flag = apps.get_model('waffle', 'Flag')
    Flag.objects.get_or_create(
        name=ENABLE_REACT_MARKDOWN_EDITOR.name, defaults={'everyone': True}
    )


class Migration(migrations.Migration):
    dependencies = [
        ('contentstore', '0010_container_link_models'),
        ('waffle', '0001_initial'),
    ]

    operations = [
        # Do not remove the flags for rollback.  We don't want to lose originals if
        # they already existed, and it won't hurt if they are created.
        migrations.RunPython(create_flag, reverse_code=migrations.RunPython.noop),
    ]
