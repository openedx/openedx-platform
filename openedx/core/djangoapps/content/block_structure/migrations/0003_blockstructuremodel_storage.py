from django.db import migrations, models

import openedx.core.djangoapps.content.block_structure.models


class Migration(migrations.Migration):

    dependencies = [
        ('block_structure', '0002_blockstructuremodel'),
    ]

    operations = [
        migrations.AlterField(
            model_name='blockstructuremodel',
            name='data',
            field=openedx.core.djangoapps.content.block_structure.models.CustomizableFileField(),
        ),
    ]
