# Generated migration for adding optional checkbox skip configuration field

import django.utils.translation
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('third_party_auth', '0013_default_site_id_wrapper_function'),
    ]

    operations = [
        migrations.AddField(
            model_name='samlproviderconfig',
            name='skip_registration_optional_checkboxes',
            field=models.BooleanField(
                default=False,
                help_text=django.utils.translation.gettext_lazy(
                    "If enabled, optional checkboxes (marketing emails opt-in, etc.) will not be rendered "
                    "on the registration form for users registering via this provider. When these checkboxes "
                    "are skipped, their values are inferred as False (opted out)."
                ),
            ),
        ),
    ]
