# pylint: disable=missing-module-docstring
from django.apps import AppConfig
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from edx_proctoring.runtime import set_runtime_service


class LearningSequencesConfig(AppConfig):  # pylint: disable=missing-class-docstring
    name = 'openedx.core.djangoapps.content.learning_sequences'
    verbose_name = _('Learning Sequences and Outlines')

    def ready(self):
        # Register celery workers
        # from .tasks import ls_listen_for_course_publish  # pylint: disable=unused-variable

        if settings.FEATURES.get('ENABLE_SPECIAL_EXAMS'):
            from .services import LearningSequencesRuntimeService
            set_runtime_service('learning_sequences', LearningSequencesRuntimeService())
