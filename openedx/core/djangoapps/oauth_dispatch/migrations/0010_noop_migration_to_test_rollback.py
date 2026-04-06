"""
Noop migration to test rollback
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('oauth_dispatch', '0009_delete_enable_scopes_waffle_switch'),
    ]

    operations = [
        migrations.RunSQL(migrations.RunSQL.noop, reverse_sql=migrations.RunSQL.noop)
    ]
