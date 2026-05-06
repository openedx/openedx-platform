import datetime

from django.db import migrations, models


class Migration(migrations.Migration):  # pylint: disable=missing-class-docstring

    dependencies = [
        ('entitlements', '0006_courseentitlementsupportdetail_action'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseentitlementpolicy',
            name='expiration_period',
            field=models.DurationField(default=datetime.timedelta(730), help_text='Duration in days from when an entitlement is created until when it is expired.'),  # pylint: disable=line-too-long
        ),
    ]
