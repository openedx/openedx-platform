"""
Management command to regenerate unverified certificates when a course
transitions to honor code.
"""
import logging  # noqa: I001
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.generation_handler import generate_certificate_task
from lms.djangoapps.certificates.models import GeneratedCertificate

log = logging.getLogger(__name__)
User = get_user_model()


class Command(BaseCommand):
    """
    Management command to regenerate unverified certificates when a course
    transitions to honor code.
    """
    def add_arguments(self, parser):
        parser.add_argument(
            '-c', '--course-keys',
            nargs='+',
            dest='course_keys',
            default='',
            help='Course run key or space separated list of course run keys. If no '
                 'key is provided, all unverified certs will be regenerated.'
        )
        parser.add_argument(
            '--excluded-keys',
            nargs='+',
            dest='excluded_keys',
            default='',
            help='Course run key or space separated list of course run keys to exclude from regenerating'
        )
        parser.add_argument(
            '--batch_size',
            action='store',
            dest='batch_size',
            type=int,
            default=200,
            help='Number of certs per batch'
        )
        parser.add_argument(
            '--sleep_seconds',
            action='store',
            dest='sleep_seconds',
            type=int,
            default=20,
            help='Seconds to sleep between batches'
        )

    def handle(self, *args, **options):
        courses_str = options['course_keys']
        excluded_str = options['excluded_keys']
        if courses_str and excluded_str:
            raise CommandError('You may not specify both course keys and excluded course keys.')
        if not courses_str:
            log.info(
                'No course keys provided. Will regenerate all '
                'unverified certs for courses not specified by an excluded key'
            )

        batch_size = options['batch_size']
        sleep_seconds = options['sleep_seconds']

        course_keys = _convert_to_course_key(courses_str)
        excluded_keys = _convert_to_course_key(excluded_str)

        count = 0
        if len(course_keys) > 0:
            for key in course_keys:
                # rolling count to maintain batch_size across courses
                count = _handle_course(key, batch_size, sleep_seconds, count)
        else:
            # regenerate unverified certs for all courses with the exception of excluded keys
            count = _handle_all_courses(batch_size, sleep_seconds, excluded_keys)
        return f'{count}'


def _convert_to_course_key(course_id_strings):
    """
    Takes in a list of course id strings and returns list of course key object
    """
    key_object_list = []
    for course_id in course_id_strings:
        # Parse the serialized course key into a CourseKey
        try:
            course_key = CourseKey.from_string(course_id)
        except InvalidKeyError as e:
            raise CommandError(f'{course_id} is not a valid course-key') from e
        key_object_list.append(course_key)
    return key_object_list


def _handle_course(course_key, batch_size, sleep_seconds, count):
    """
    Regenerates unverified status certificates for the designated course with delay seconds between certs.
    Returns how many certs were originally unverified and regenerated.
    """

    certs = GeneratedCertificate.objects.filter(
        course_id=course_key,
        status=CertificateStatuses.unverified,
    )

    log.info(f'Regenerating {len(certs)} unverified certificates for {course_key}')

    return _regenerate_certs(certs, batch_size, sleep_seconds, count)


def _handle_all_courses(batch_size, sleep_seconds, excluded_keys):
    """
    Regenerates unverified status certificates for all courses except for those designated by the excluded keys with
    delay seconds between certs. Returns how many certs were originally unverified and regenerated.
    """
    certs = GeneratedCertificate.objects\
        .filter(status=CertificateStatuses.unverified)\
        .exclude(course_id__in=excluded_keys)
    log.info(f'Regenerating {len(certs)} unverified certificates in all courses except for excluded keys')

    return _regenerate_certs(certs, batch_size, sleep_seconds, 0)


def _regenerate_certs(certs, batch_size, sleep_seconds, count):
    """
    Triggers generate certificate task for a given set of certificates
    """
    for cert in certs:
        user = User.objects.get(id=cert.user_id)
        generate_certificate_task(user, cert.course_id, generation_mode='batch', delay_seconds=0)
        count += 1
        if count % batch_size == 0:
            log.info(f'Regenerated {count} unverified certificates. Sleeping for {sleep_seconds} seconds.')
            time.sleep(sleep_seconds)
    return count
