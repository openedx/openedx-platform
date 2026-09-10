# rm user foreign key db constraint to avoid deadlocks when deleting users.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('student', '0049_manualenrollmentaudit_statetransition_typo'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseenrollment',
            name='user',
            field=models.ForeignKey(db_constraint=False, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='courseenrollmentallowed',
            name='user',
            field=models.ForeignKey(blank=True, db_constraint=False, help_text="First user which enrolled in the specified course through the specified e-mail. Once set, it won't change.", null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL),
        ),
    ]
