# Generated migration for adding optional email checkbox configuration fields

from django.db import migrations, models
import django.utils.translation


class Migration(migrations.Migration):

    dependencies = [
        ('third_party_auth', '0013_default_site_id_wrapper_function'),
    ]

    operations = [
        migrations.AddField(
            model_name='samlproviderconfig',
            name='marketing_emails_opt_in_optional',
            field=models.BooleanField(
                default=False,
                help_text=django.utils.translation.gettext_lazy(
                    "If enabled, the marketing emails opt-in checkbox will be optional for users "
                    "registering via this provider instead of required. When disabled, marketing email opt-in "
                    "is determined by the global MARKETING_EMAILS_OPT_IN setting."
                ),
            ),
        ),
    ]
