"""
WE'RE USING MIGRATIONS!

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py schemamigration courseware --auto description_of_your_change
3. Add the migration file created in edx-platform/lms/djangoapps/coursewarehistoryextended/migrations/


ASSUMPTIONS: modules have unique IDs, even across different module_types

"""


from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from lms.djangoapps.courseware.fields import UnsignedBigIntAutoField
from lms.djangoapps.courseware.models import BaseStudentModuleHistory, StudentModule


class StudentModuleHistoryExtended(BaseStudentModuleHistory):
    """Keeps a complete history of state changes for a given XModule for a given
    Student. Right now, we restrict this to problems so that the table doesn't
    explode in size.

    This new extended CSMH has a larger primary key that won't run out of space
    so quickly."""

    class Meta:
        app_label = 'coursewarehistoryextended'
        get_latest_by = "created"
        indexes = [
            models.Index(
                fields=['student_module'],
                name="student_module_idx"
            ),
        ]

    id = UnsignedBigIntAutoField(primary_key=True)  # pylint: disable=invalid-name

    student_module = models.ForeignKey(StudentModule, db_index=False, db_constraint=False, on_delete=models.DO_NOTHING)

    @receiver(post_save, sender=StudentModule)
    def save_history(sender, instance, **kwargs):  # pylint: disable=no-self-argument, unused-argument
        """
        Checks the instance's module_type, and creates & saves a
        StudentModuleHistoryExtended entry if the module_type is one that
        we save.
        """
        BaseStudentModuleHistory.save_history_entry(
            instance,
            StudentModuleHistoryExtended,
            "lms.djangoapps.coursewarehistoryextended.models.student_module_history_extended_map"
        )

    @receiver(post_delete, sender=StudentModule)
    def delete_history(sender, instance, **kwargs):  # pylint: disable=no-self-argument, unused-argument
        """
        Django can't cascade delete across databases, so we tell it at the model level to
        on_delete=DO_NOTHING and then listen for post_delete so we can clean up the CSMHE rows.
        """
        StudentModuleHistoryExtended.objects.filter(student_module=instance).all().delete()

    def __str__(self):
        return str(repr(self))
