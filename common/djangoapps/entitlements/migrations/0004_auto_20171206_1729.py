import uuid

from django.db import migrations, models


class Migration(migrations.Migration):  # pylint: disable=missing-class-docstring

    dependencies = [
        ('entitlements', '0003_auto_20171205_1431'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseentitlement',
            name='uuid',
            field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
