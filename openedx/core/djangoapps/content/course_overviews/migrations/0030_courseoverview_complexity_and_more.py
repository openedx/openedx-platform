from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "course_overviews",
            "0029_alter_historicalcourseoverview_options",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="courseoverview",
            name="complexity",
            field=models.CharField(
                max_length=10,
                default="medium",
                choices=[
                    ("easy", "Easy"),
                    ("medium", "Medium"),
                    ("hard", "Hard"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="historicalcourseoverview",
            name="complexity",
            field=models.CharField(
                max_length=10,
                default="medium",
                choices=[
                    ("easy", "Easy"),
                    ("medium", "Medium"),
                    ("hard", "Hard"),
                ],
            ),
        ),
    ]
