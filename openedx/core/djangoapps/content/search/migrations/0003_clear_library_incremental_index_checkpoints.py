from django.db import migrations
from opaque_keys.edx.locator import LibraryLocatorV2


def clear_library_checkpoints(apps, schema_editor):
    """
    Checkpoints written before the course/library index split record libraries indexed into the course index.
    An incremental rebuild would skip those libraries and never add them to the new library index.
    """
    IncrementalIndexCompleted = apps.get_model("search", "IncrementalIndexCompleted")
    library_ids = [
        checkpoint.id
        for checkpoint in IncrementalIndexCompleted.objects.all()
        if isinstance(checkpoint.context_key, LibraryLocatorV2)
    ]
    IncrementalIndexCompleted.objects.filter(id__in=library_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("search", "0002_incrementalindexcompleted"),
    ]

    operations = [
        migrations.RunPython(clear_library_checkpoints, migrations.RunPython.noop),
    ]
