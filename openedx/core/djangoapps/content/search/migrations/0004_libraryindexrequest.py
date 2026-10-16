from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("search", "0003_clear_library_incremental_index_checkpoints")]

    operations = [
        migrations.CreateModel(
            name="LibraryIndexRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("library_key", models.CharField(max_length=255, unique=True)),
                ("requested_revision", models.PositiveBigIntegerField(default=0)),
                ("completed_revision", models.PositiveBigIntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
