"""
WE'RE USING MIGRATIONS!

If you make changes to this model, be sure to create an appropriate migration
file and check it in at the same time as your model changes. To do that,

1. Go to the edx-platform dir
2. ./manage.py schemamigration instructor_task --auto description_of_your_change
3. Add the migration file created in edx-platform/lms/djangoapps/instructor_task/migrations/


ASSUMPTIONS: modules have unique IDs, even across different module_types

"""
import csv
import hashlib
import json
import logging
import os.path
from uuid import uuid4

from botocore.exceptions import ClientError
from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.files.base import ContentFile
from django.db import models, transaction
from django.utils.translation import gettext as _
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField
from simple_history.models import HistoricalRecords

from openedx.core.storage import get_storage

logger = logging.getLogger(__name__)

# define custom states used by InstructorTask
QUEUING = 'QUEUING'
PROGRESS = 'PROGRESS'
SCHEDULED = 'SCHEDULED'
TASK_INPUT_LENGTH = 10000
DJANGO_STORE_STORAGE_CLASS = 'storages.backends.s3boto3.S3Boto3Storage'


class InstructorTask(models.Model):
    """
    Stores information about background tasks that have been submitted to
    perform work by an instructor (or course staff).
    Examples include grading and rescoring.

    `task_type` identifies the kind of task being performed, e.g. rescoring.
    `course_id` uses the course run's unique id to identify the course.
    `task_key` stores relevant input arguments encoded into key value for testing to see
           if the task is already running (together with task_type and course_id).
    `task_input` stores input arguments as JSON-serialized dict, for reporting purposes.
        Examples include url of problem being rescored, id of student if only one student being rescored.

    `task_id` stores the id used by celery for the background task.
    `task_state` stores the last known state of the celery task
    `task_output` stores the output of the celery task.
        Format is a JSON-serialized dict.  Content varies by task_type and task_state.

    `requester` stores id of user who submitted the task
    `created` stores date that entry was first created
    `updated` stores date that entry was last modified

    .. no_pii:
    """
    class Meta:
        app_label = "instructor_task"

    task_type = models.CharField(max_length=50, db_index=True)  # noqa: DJ012
    course_id = CourseKeyField(max_length=255, db_index=True)
    task_key = models.CharField(max_length=255, db_index=True)
    task_input = models.TextField()
    task_id = models.CharField(max_length=255, db_index=True)  # max_length from celery_taskmeta
    task_state = models.CharField(max_length=50, null=True, db_index=True)  # max_length from celery_taskmeta  # noqa: DJ001  # pylint: disable=line-too-long
    task_output = models.CharField(max_length=1024, null=True)  # noqa: DJ001
    requester = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True, null=True)
    updated = models.DateTimeField(auto_now=True)
    subtasks = models.TextField(blank=True)  # JSON dictionary

    def __repr__(self):
        return 'InstructorTask<{!r}>'.format({
            'task_type': self.task_type,
            'course_id': self.course_id,
            'task_input': self.task_input,
            'task_id': self.task_id,
            'task_state': self.task_state,
            'task_output': self.task_output,
        })

    def __str__(self):
        return str(repr(self))

    @classmethod
    def create(cls, course_id, task_type, task_key, task_input, requester):
        """
        Create an instance of InstructorTask.
        """
        # create the task_id here, and pass it into celery:
        task_id = str(uuid4())
        json_task_input = json.dumps(task_input)

        # check length of task_input, and return an exception if it's too long
        if len(json_task_input) > TASK_INPUT_LENGTH:
            logger.error(
                'Task input longer than: `%s` for `%s` of course: `%s`',
                TASK_INPUT_LENGTH,
                task_type,
                course_id
            )
            error_msg = _('An error has occurred. Task was not created.')
            raise AttributeError(error_msg)

        # create the task, then save it:
        instructor_task = cls(
            course_id=course_id,
            task_type=task_type,
            task_id=task_id,
            task_key=task_key,
            task_input=json_task_input,
            task_state=QUEUING,
            requester=requester
        )
        instructor_task.save_now()

        return instructor_task

    @transaction.atomic
    def save_now(self):
        """
        Writes InstructorTask immediately, ensuring the transaction is committed.
        """
        self.save()

    @staticmethod
    def create_output_for_success(returned_result):
        """
        Converts successful result to output format.

        Raises a ValueError exception if the output is too long.
        """
        # In future, there should be a check here that the resulting JSON
        # will fit in the column.  In the meantime, just return an exception.
        json_output = json.dumps(returned_result)
        if len(json_output) > 1023:
            raise ValueError(f"Length of task output is too long: {json_output}")
        return json_output

    @staticmethod
    def create_output_for_failure(exception, traceback_string):
        """
        Converts failed result information to output format.

        Traceback information is truncated or not included if it would result in an output string
        that would not fit in the database.  If the output is still too long, then the
        exception message is also truncated.

        Truncation is indicated by adding "..." to the end of the value.
        """
        tag = '...'
        task_progress = {'exception': type(exception).__name__, 'message': str(exception)}
        if traceback_string is not None:
            # truncate any traceback that goes into the InstructorTask model:
            task_progress['traceback'] = traceback_string
        json_output = json.dumps(task_progress)
        # if the resulting output is too long, then first shorten the
        # traceback, and then the message, until it fits.
        too_long = len(json_output) - 1023
        if too_long > 0:
            if traceback_string is not None:
                if too_long >= len(traceback_string) - len(tag):
                    # remove the traceback entry entirely (so no key or value)
                    del task_progress['traceback']
                    too_long -= (len(traceback_string) + len('traceback'))
                else:
                    # truncate the traceback:
                    task_progress['traceback'] = traceback_string[(too_long + len(tag)):] + tag
                    too_long = 0
            if too_long > 0:
                # we need to shorten the message:
                task_progress['message'] = task_progress['message'][(too_long + len(tag)):] + tag
            json_output = json.dumps(task_progress)
        return json_output

    @staticmethod
    def create_output_for_revoked():
        """Creates standard message to store in output format for revoked tasks."""
        return json.dumps({'message': 'Task revoked before running'})


class InstructorTaskSchedule(TimeStampedModel):
    """
    A database model to store information about _when_ to execute a scheduled background task.

    The primary use case is to allow instructors to schedule their email messages (authored with the bulk course email
    tool) to be sent at a later date and time.

    .. no_pii:
    """
    class Meta:
        app_label = "instructor_task"

    task = models.OneToOneField(InstructorTask, on_delete=models.DO_NOTHING)
    task_args = models.TextField(null=False, blank=False)
    task_due = models.DateTimeField(null=False)

    if 'instructor_task' in apps.app_configs:
        history = HistoricalRecords()


class ReportStore:
    """
    Simple abstraction layer that can fetch and store CSV files for reports
    download. Should probably refactor later to create a ReportFile object that
    can simply be appended to for the sake of memory efficiency, rather than
    passing in the whole dataset. Doing that for now just because it's simpler.
    """
    @classmethod
    def from_config(cls, config_name):
        """
        Return one of the ReportStore subclasses depending on django
        configuration. Look at subclasses for expected configuration.
        """
        # Convert old configuration parameters to those expected by
        # DjangoStorageReportStore for backward compatibility
        config = getattr(settings, config_name, {})
        storage_type = config.get('STORAGE_TYPE', '').lower()
        if storage_type == 's3':
            return DjangoStorageReportStore(
                storage_class=DJANGO_STORE_STORAGE_CLASS,
                storage_kwargs={
                    'bucket_name': config['BUCKET'],
                    'location': config['ROOT_PATH'],
                    'custom_domain': config.get("CUSTOM_DOMAIN", None),
                    'querystring_expire': 300,
                    'gzip': True,
                },
            )
        elif storage_type == 'localfs':
            return DjangoStorageReportStore(
                storage_class='django.core.files.storage.FileSystemStorage',
                storage_kwargs={
                    'location': config['ROOT_PATH'],
                },
            )
        return DjangoStorageReportStore.from_config(config_name)

    def _get_utf8_encoded_rows(self, rows):
        """
        Given a list of `rows` containing unicode strings, return a
        new list of rows with those strings encoded as utf-8 for CSV
        compatibility.
        """
        for row in rows:
            yield [str(item) for item in row]


class DjangoStorageReportStore(ReportStore):
    """
    ReportStore implementation that delegates to django's storage api.
    """
    def __init__(self, storage_class=None, storage_kwargs=None):
        if storage_kwargs is None:
            storage_kwargs = {}

        self.storage = get_storage(storage_class, **storage_kwargs)

    @classmethod
    def from_config(cls, config_name):
        """
        By default, the default file storage specified by the `STORAGES['default']`
        setting will be used. To configure the storage used, add a dict in
        settings with the following fields::

            STORAGE_CLASS : The import path of the storage class to use. If
                            not set, the STORAGES['default']['BACKEND'] setting will be used.
            STORAGE_KWARGS : An optional dict of kwargs to pass to the storage
                             constructor. This can be used to specify a
                             different S3 bucket or root path, for example.

        Reference the setting name when calling `.from_config`.
        """
        return cls(
            getattr(settings, config_name).get('STORAGE_CLASS'),
            getattr(settings, config_name).get('STORAGE_KWARGS'),
        )

    def store(self, course_id, filename, buff, parent_dir=''):
        """
        Store the contents of `buff` in a directory determined by hashing
        `course_id`, and name the file `filename`. `buff` can be any file-like
        object, ready to be read from the beginning.
        """
        path = self.path_to(course_id, filename, parent_dir)
        # See https://github.com/boto/boto/issues/2868
        # Boto doesn't play nice with unicode in python3
        buff_contents = buff.read()

        if not isinstance(buff_contents, bytes):
            buff_contents = buff_contents.encode('utf-8')

        buff = ContentFile(buff_contents)

        self.storage.save(path, buff)

    def store_rows(self, course_id, filename, rows, parent_dir=''):
        """
        Given a course_id, filename, and rows (each row is an iterable of
        strings), write the rows to the storage backend in csv format.
        """
        output_buffer = ContentFile('')
        csvwriter = csv.writer(output_buffer)
        csvwriter.writerows(self._get_utf8_encoded_rows(rows))
        output_buffer.seek(0)
        self.store(course_id, filename, output_buffer, parent_dir)

    def links_for(self, course_id):
        """
        For a given `course_id`, return a list of `(filename, url)` tuples.
        Calls the `url` method of the underlying storage backend. Returned
        urls can be plugged straight into an href
        """
        course_dir = self.path_to(course_id)
        try:
            _, filenames = self.storage.listdir(course_dir)
        except OSError:
            # Django's FileSystemStorage fails with an OSError if the course
            # dir does not exist; other storage types return an empty list.
            return []
        except ClientError as ex:
            logger.error(
                'Fetching files failed for course: %s, status: %s, reason: %s',
                course_id,
                ex.response.get('Error'), ex.response.get('Error', {}).get('Message')
            )
            return []

        files = [(filename, os.path.join(course_dir, filename)) for filename in filenames]
        files.sort(key=lambda f: self.storage.get_modified_time(f[1]), reverse=True)
        return [
            (filename, self.storage.url(full_path))
            for filename, full_path in files
        ]

    def path_to(self, course_id, filename='', parent_dir=''):
        """
        Return the full path to a given file for a given course.
        """
        hashed_course_id = hashlib.sha1(str(course_id).encode('utf-8')).hexdigest()
        directory = parent_dir if bool(parent_dir) else hashed_course_id
        return os.path.join(directory, filename)
