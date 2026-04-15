"""
Noop migration to test rollback
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('oauth_dispatch', '0011_noop_migration_to_test_rollback'),
    ]

    operations = [
        migrations.RunSQL(migrations.RunSQL.noop, reverse_sql=migrations.RunSQL.noop)
    ]
