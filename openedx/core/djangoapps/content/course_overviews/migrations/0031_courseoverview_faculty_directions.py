from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            "course_overviews",
            "0030_courseoverview_complexity_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="courseoverview",
            name="faculty",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="courseoverview",
            name="directions",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="historicalcourseoverview",
            name="faculty",
            field=models.TextField(null=True),
        ),
        migrations.AddField(
            model_name="historicalcourseoverview",
            name="directions",
            field=models.TextField(null=True),
        ),
    ]
