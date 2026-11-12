# Generated migration for adding marketing emails opt-in optional configuration field

from django.db import migrations, models
import django.utils.translation


class Migration(migrations.Migration):

    dependencies = [
        ('third_party_auth', '0014_samlproviderconfig_optional_email_checkboxes'),
    ]

    operations = [
        migrations.AddField(
            model_name='samlproviderconfig',
            name='marketing_emails_opt_in_optional',
            field=models.BooleanField(
                default=True,
                help_text=django.utils.translation.gettext_lazy(
                    "If enabled, the marketing emails opt-in checkbox will be optional (not required) "
                    "and will default to unchecked (False) during registration for users authenticating "
                    "via this provider. This gives users explicit control over marketing email preferences "
                    "without forcing them to opt-in."
                ),
            ),
        ),
    ]
