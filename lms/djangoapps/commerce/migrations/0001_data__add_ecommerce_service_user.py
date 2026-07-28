from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import migrations, models, transaction

USERNAME = settings.ECOMMERCE_SERVICE_WORKER_USERNAME
EMAIL = USERNAME + '@fake.email'

def forwards(apps, schema_editor):
    """Add the service user."""
    User = get_user_model()
    with transaction.atomic():
        user, created = User.objects.get_or_create(username=USERNAME, email=EMAIL)
        if created:
            user.set_unusable_password()
            user.save()

def backwards(apps, schema_editor):
    """Remove the service user."""
    with transaction.atomic():
        User.objects.get(username=USERNAME, email=EMAIL).delete()

class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('user_api', '0002_retirementstate_userretirementstatus'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
