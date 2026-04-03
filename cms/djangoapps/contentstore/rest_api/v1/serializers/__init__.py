"""
Serializers for v1 contentstore API.
"""
from .certificates import CourseCertificatesSerializer
from .course_details import CourseDetailsSerializer
from .course_index import CourseIndexSerializer
from .course_rerun import CourseRerunSerializer
from .course_team import CourseTeamSerializer
from .course_waffle_flags import CourseWaffleFlagsSerializer
from .grading import CourseGradingModelSerializer, CourseGradingSerializer
from .group_configurations import CourseGroupConfigurationsSerializer
from .home import CourseHomeTabSerializer, LibraryTabSerializer, StudioHomeSerializer
from .proctoring import (
    LimitedProctoredExamSettingsSerializer,
    ProctoredExamConfigurationSerializer,
    ProctoredExamSettingsSerializer,
    ProctoringErrorsSerializer,
)
from .settings import CourseSettingsSerializer
from .textbooks import CourseTextbooksSerializer
from .vertical_block import ContainerChildrenSerializer, ContainerHandlerSerializer
from .videos import (
    CourseVideosSerializer,
    VideoDownloadSerializer,
    VideoImageSerializer,
    VideoUploadSerializer,
    VideoUsageSerializer,
)
