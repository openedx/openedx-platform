from django.db import migrations, models, connection

def table_description():
    """Handle MySQL/Postgres vs SQLite compatibility for table introspection"""
    if connection.vendor == 'sqlite':
        fields = connection.introspection.get_table_description(connection.cursor(), 'course_overviews_courseoverview')
        return [f.name for f in fields]
    else:
        cursor = connection.cursor()
        if connection.vendor == 'mysql':
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'course_overviews_courseoverview' AND table_schema = DATABASE()
            """)
        elif connection.vendor == 'postgresql':
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'course_overviews_courseoverview' AND table_catalog = current_database()
            """)
        rows = cursor.fetchall()
        return [r[0] for r in rows]

class Migration(migrations.Migration):

    dependencies = [
        ('course_overviews', '0008_remove_courseoverview_facebook_url'),
    ]

    operations = []
    fields = table_description()

    # Ensure 'facebook_url' is added if it does not exist in the table
    if fields and not any(f == 'facebook_url' for f in fields):
        operations.append(
            migrations.AddField(
                model_name='courseoverview',
                name='facebook_url',
                field=models.TextField(null=True),
            )
        )
