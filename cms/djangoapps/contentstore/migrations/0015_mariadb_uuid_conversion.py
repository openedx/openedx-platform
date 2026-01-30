# Generated migration for MariaDB UUID field conversion (Django 5.2)
"""
Migration to convert UUIDField from char(32) to uuid type for MariaDB compatibility.

This migration is necessary because Django 5 changed the behavior of UUIDField for MariaDB
databases from using CharField(32) to using a proper UUID type. This change isn't managed
automatically, so we need to generate migrations to safely convert the columns.

This migration only executes for MariaDB databases and is a no-op for other backends.

See: https://www.albertyw.com/note/django-5-mariadb-uuidfield
"""

from django.db import migrations


def apply_mariadb_migration(apps, schema_editor):
    """Apply the migration only for MariaDB databases."""
    connection = schema_editor.connection
    
    # Check if this is a MariaDB database
    if connection.vendor != 'mysql':
        return
    
    # Additional check for MariaDB specifically (vs MySQL)
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        if 'mariadb' not in version.lower():
            return
    
    # Apply the field changes for MariaDB
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE oel_publishing_learningpackage "
            "MODIFY uuid uuid NOT NULL"
        )


def reverse_mariadb_migration(apps, schema_editor):
    """Reverse the migration only for MariaDB databases."""
    connection = schema_editor.connection
    
    # Check if this is a MariaDB database
    if connection.vendor != 'mysql':
        return
    
    # Additional check for MariaDB specifically (vs MySQL)
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()[0]
        if 'mariadb' not in version.lower():
            return
    
    # Reverse the field changes for MariaDB
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE oel_publishing_learningpackage "
            "MODIFY uuid char(32) NOT NULL"
        )


class Migration(migrations.Migration):

    dependencies = [
        ('contentstore', '0014_remove_componentlink_downstream_is_modified_and_more'),
    ]

    operations = [
        migrations.RunPython(
            code=apply_mariadb_migration,
            reverse_code=reverse_mariadb_migration,
        ),
    ]
