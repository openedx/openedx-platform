"""
Views for v1 contentstore API.
"""
from .certificates import CourseCertificatesView  # noqa: F401
from .course_details import CourseDetailsView  # noqa: F401
from .course_index import ContainerChildrenView, CourseIndexView  # noqa: F401
from .course_rerun import CourseRerunView  # noqa: F401
from .course_team import CourseTeamView  # noqa: F401
from .course_waffle_flags import CourseWaffleFlagsView  # noqa: F401
from .grading import CourseGradingView  # noqa: F401
from .group_configurations import CourseGroupConfigurationsView  # noqa: F401
from .help_urls import HelpUrlsView  # noqa: F401
from .home import HomePageCoursesView, HomePageLibrariesView, HomePageView  # noqa: F401
from .proctoring import ProctoredExamSettingsView, ProctoringErrorsView  # noqa: F401
from .settings import CourseSettingsView  # noqa: F401
from .textbooks import CourseTextbooksView  # noqa: F401
from .vertical_block import ContainerHandlerView, vertical_container_children_redirect_view  # noqa: F401
from .videos import CourseVideosView, VideoDownloadView, VideoUsageView  # noqa: F401
