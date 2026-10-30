from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('certificates', '0038_pdf_certificate_purge_data_management'),
    ]

    operations = [
        migrations.AlterField(
            model_name='certificatetemplate',
            name='template',
            field=models.TextField(help_text='Mako template HTML. Mako runs any Python code in the template on the LMS server, so only give add or change access to certificate templates to users you would trust with shell access to the LMS.'),
        ),
    ]
