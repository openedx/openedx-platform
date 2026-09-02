from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('xblock_django', '0004_delete_xblock_disable_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='xblockstudioconfiguration',
            name='advanced_by_default',
            field=models.BooleanField(default=False, help_text="Offer this XBlock in the Advanced component list of every course, without course teams having to add it to the course's Advanced Module List. Has no effect on XBlocks that are already offered as basic components."),
        ),
    ]
