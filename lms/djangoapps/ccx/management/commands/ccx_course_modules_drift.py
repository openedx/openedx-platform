"""
Report CCXs whose CCX REST API v0 ``structure_json`` disagrees with what learners can see.

The v0 API used to store ``course_modules`` in ``CustomCourseForEdX.structure_json``, which nothing
uses to decide what learners see; the CCX schedule (``visible_to_staff_only`` overrides) does. Run
this before enabling the ``ccx.v0_course_modules_use_schedule`` flag for a course, to see which CCXs
will report different ``course_modules``, and periodically while the flag is being rolled out.

Example:
    ./manage.py lms ccx_course_modules_drift --master-course-id course-v1:Org+Course+Run
"""

import logging

from django.core.management.base import BaseCommand
from edx_django_utils.cache import RequestCache
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.ccx.models import CustomCourseForEdX
from lms.djangoapps.ccx.utils import get_ccx_visible_chapters
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Report CCXs whose ``structure_json`` disagrees with the chapters learners can see.
    """
    help = 'Report CCXs whose CCX REST API v0 structure_json disagrees with the chapters learners can see.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--master-course-id',
            action='append',
            dest='master_course_ids',
            default=[],
            help='Only check CCXs of this master course. Can be repeated.',
        )

    def handle(self, *args, **options):
        # CCXs created on the coach dashboard have no list to compare
        ccxs = CustomCourseForEdX.objects.exclude(structure_json__isnull=True).exclude(structure_json='null')
        if options['master_course_ids']:
            ccxs = ccxs.filter(course_id__in=[CourseKey.from_string(key) for key in options['master_course_ids']])

        master_courses = {}
        checked = drifted = errors = 0
        for ccx in ccxs.order_by('course_id', 'id').iterator():
            checked += 1
            try:
                if ccx.course_id not in master_courses:
                    master_courses[ccx.course_id] = modulestore().get_course(ccx.course_id)
                visible = set(get_ccx_visible_chapters(master_courses[ccx.course_id], ccx))
            except Exception:  # pylint: disable=broad-except
                errors += 1
                log.exception('Could not check CCX %s', ccx.locator)
                continue
            finally:
                # the overrides of every CCX would otherwise stay in the request cache
                RequestCache('ccx-overrides').clear()

            listed = set(ccx.structure or [])
            if listed != visible:
                drifted += 1
                log.warning(
                    'CCX %s course_modules drift: listed but hidden from learners: %s; '
                    'visible to learners but not listed: %s',
                    ccx.locator, sorted(listed - visible), sorted(visible - listed),
                )

        summary = f'ccx_course_modules_drift checked={checked} drifted={drifted} errors={errors}'
        log.info(summary)
        self.stdout.write(summary)
