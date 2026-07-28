from django.db import migrations, models


class Migration(migrations.Migration):  # pylint: disable=missing-class-docstring

    dependencies = [
        ('entitlements', '0007_change_expiration_period_default'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseentitlementpolicy',
            name='mode',
            field=models.CharField(max_length=32, null=True, choices=[(None, '---------'), ('verified', 'verified'), ('professional', 'professional')]),  # pylint: disable=line-too-long
        ),
        migrations.AlterField(
            model_name='courseentitlementpolicy',
            name='site',
            field=models.ForeignKey(to='sites.Site', null=True, on_delete=models.CASCADE),
        ),
    ]
