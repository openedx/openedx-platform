"""
Models used for robust grading.

Robust grading allows student scores to be saved per-subsection independent
of any changes that may occur to the course after the score is achieved.
We also persist students' course-level grades, and update them whenever
a student's score or the course grading policy changes. As they are
persisted, course grades are also immune to changes in course content.
"""

import json
import logging
from base64 import b64encode
from collections import defaultdict, namedtuple
from hashlib import sha1

from django.apps import apps
from django.db import IntegrityError, models, transaction
from django.utils.timezone import now
from lazy import lazy
from model_utils.models import TimeStampedModel
from opaque_keys.edx.django.models import CourseKeyField, UsageKeyField
from opaque_keys.edx.keys import CourseKey, UsageKey
from openedx_events.learning.data import CourseData, PersistentCourseGradeData
from openedx_events.learning.signals import PERSISTENT_GRADE_SUMMARY_CHANGED
from simple_history.models import HistoricalRecords

from lms.djangoapps.courseware.fields import UnsignedBigIntAutoField
from lms.djangoapps.grades import events  # pylint: disable=unused-import
from lms.djangoapps.grades.signals.signals import (
    COURSE_GRADE_PASSED_FIRST_TIME,
    COURSE_GRADE_PASSED_UPDATE_IN_LEARNER_PATHWAY,
)
from openedx.core.lib.cache_utils import get_cache

log = logging.getLogger(__name__)

BLOCK_RECORD_LIST_VERSION = 1

# Used to serialize information about a block at the time it was used in
# grade calculation.
BlockRecord = namedtuple('BlockRecord', ['locator', 'weight', 'raw_possible', 'graded'])


class BlockRecordList:
    """
    An immutable ordered list of BlockRecord objects.
    """

    def __init__(self, blocks, course_key, version=None):
        self.blocks = tuple(blocks)
        self.course_key = course_key
        self.version = version or BLOCK_RECORD_LIST_VERSION

    def __eq__(self, other):
        assert isinstance(other, BlockRecordList)
        return self.json_value == other.json_value

    def __hash__(self):
        """
        Returns an integer Type value of the hash of this
        list of block records, as required by python.
        """
        return hash(self.hash_value)

    def __iter__(self):
        return iter(self.blocks)

    def __len__(self):
        return len(self.blocks)

    @lazy
    def hash_value(self):
        """
        Returns a hash value of the list of block records.

        This currently hashes using sha1, and returns a base64 encoded version
        of the binary digest.  In the future, different algorithms could be
        supported by adding a label indicated which algorithm was used, e.g.,
        "sha256$j0NDRmSPa5bfid2pAcUXaxCm2Dlh3TwayItZstwyeqQ=".
        """
        return b64encode(sha1(self.json_value.encode('utf-8')).digest()).decode('utf-8')

    @lazy
    def json_value(self):
        """
        Return a JSON-serialized version of the list of block records, using a
        stable ordering.
        """
        list_of_block_dicts = [block._asdict() for block in self.blocks]
        for block_dict in list_of_block_dicts:
            block_dict['locator'] = str(block_dict['locator'])  # BlockUsageLocator is not json-serializable
        data = {
            'blocks': list_of_block_dicts,
            'course_key': str(self.course_key),
            'version': self.version,
        }
        return json.dumps(
            data,
            separators=(',', ':'),  # Remove spaces from separators for more compact representation
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, blockrecord_json):
        """
        Return a BlockRecordList from previously serialized json.
        """
        data = json.loads(blockrecord_json)
        course_key = CourseKey.from_string(data['course_key'])
        block_dicts = data['blocks']
        record_generator = (
            BlockRecord(
                locator=UsageKey.from_string(block["locator"]).replace(course_key=course_key),
                weight=block["weight"],
                raw_possible=block["raw_possible"],
                graded=block["graded"],
            )
            for block in block_dicts
        )
        return cls(record_generator, course_key, version=data['version'])

    @classmethod
    def from_list(cls, blocks, course_key):
        """
        Return a BlockRecordList from the given list and course_key.
        """
        return cls(blocks, course_key)


class VisibleBlocks(models.Model):
    """
    A django model used to track the state of a set of visible blocks under a
    given subsection at the time they are used for grade calculation.

    This state is represented using an array of BlockRecord, stored
    in the blocks_json field. A hash of this json array is used for lookup
    purposes.

    .. no_pii:
    """
    blocks_json = models.TextField()
    hashed = models.CharField(max_length=100, unique=True)
    course_id = CourseKeyField(blank=False, max_length=255, db_index=True)

    _CACHE_NAMESPACE = "grades.models.VisibleBlocks"

    class Meta:
        app_label = "grades"

    def __str__(self):
        """
        String representation of this model.
        """
        return f"VisibleBlocks object - hash:{self.hashed}, raw json:'{self.blocks_json}'"

    @property
    def blocks(self):
        """
        Returns the blocks_json data stored on this model as a list of
        BlockRecords in the order they were provided.
        """
        return BlockRecordList.from_json(self.blocks_json)

    @classmethod
    def bulk_read(cls, user_id, course_key):
        """
        Reads and returns all visible block records for the given user and course from
        the cache.  The cache is initialized with the visible blocks for this user and
        course if no entry currently exists.

        Arguments:
            course_key: The course identifier for the desired records
        """
        prefetched = get_cache(cls._CACHE_NAMESPACE).get(cls._cache_key(user_id, course_key), None)
        if prefetched is None:
            prefetched = cls._initialize_cache(user_id, course_key)
        return prefetched

    @classmethod
    def cached_get_or_create(cls, user_id, blocks):
        """
        Given a ``user_id`` and a ``BlockRecordList`` object, attempts to
        fetch the related VisibleBlocks model from the request cache.  This
        will create and save a new ``VisibleBlocks`` record if no record
        exists corresponding to the hash_value of ``blocks``.
        """
        prefetched = get_cache(cls._CACHE_NAMESPACE).get(cls._cache_key(user_id, blocks.course_key))
        if prefetched is not None:
            model = prefetched.get(blocks.hash_value)
            if not model:
                # We still have to do a get_or_create, because
                # another user may have had this block hash created,
                # even if the user we checked the cache for hasn't yet.
                model, _ = cls.objects.get_or_create(
                    hashed=blocks.hash_value, blocks_json=blocks.json_value, course_id=blocks.course_key,
                )
                cls._update_cache(user_id, blocks.course_key, [model])
        else:
            model, _ = cls.objects.get_or_create(
                hashed=blocks.hash_value,
                defaults={'blocks_json': blocks.json_value, 'course_id': blocks.course_key},
            )
        return model

    @classmethod
    def bulk_create(cls, user_id, course_key, block_record_lists):
        """
        Bulk creates VisibleBlocks for the given iterator of
        BlockRecordList objects and updates the VisibleBlocks cache
        for the block records' course with the new VisibleBlocks.
        Returns the newly created visible blocks.
        """
        visual_blocks = [
            VisibleBlocks(
                blocks_json=brl.json_value,
                hashed=brl.hash_value,
                course_id=course_key,
            )
            for brl in block_record_lists
        ]

        created_visual_blocks = []
        existing_visual_blocks = []

        try:
            # Try to bulk create the blocks assuming all blocks are new
            with transaction.atomic():
                created_visual_blocks = cls.objects.bulk_create(visual_blocks)
        except IntegrityError:
            log.warning('Falling back to create VisualBlocks one by one for user {} in course {}'.format(  # noqa: UP032
                user_id,
                course_key
            ))

            # Try to create blocks one by one and mark newly created blocks
            for visual_block in visual_blocks:
                existing_blocks = cls.objects.filter(hashed=visual_block.hashed)

                if existing_blocks.exists():
                    # As only one record has a matching hash it is safe to use first
                    existing_visual_blocks.append(existing_blocks.first())
                else:
                    # Create the visual block and add mark as newly created
                    visual_block.save()
                    created_visual_blocks.append(visual_block)

        # Update the cache with the conjunction of created and existing blocks
        cls._update_cache(user_id, course_key, existing_visual_blocks + created_visual_blocks)

        # Return the new visual blocks
        return created_visual_blocks

    @classmethod
    def bulk_get_or_create(cls, user_id, course_key, block_record_lists):
        """
        Bulk creates VisibleBlocks for the given iterator of
        BlockRecordList objects for the given user and course_key, but
        only for those that aren't already created.
        """
        cached_records = cls.bulk_read(user_id, course_key)
        non_existent_brls = {brl for brl in block_record_lists if brl.hash_value not in cached_records}
        cls.bulk_create(user_id, course_key, non_existent_brls)

    @classmethod
    def _initialize_cache(cls, user_id, course_key):
        """
        Prefetches visible blocks for the given user and course and stores in the cache.
        Returns a dictionary mapping hashes of these block records to the
        block record objects.
        """
        grades_with_blocks = PersistentSubsectionGrade.objects.select_related('visible_blocks').filter(
            user_id=user_id,
            course_id=course_key,
        )
        prefetched = {grade.visible_blocks.hashed: grade.visible_blocks for grade in grades_with_blocks}
        get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(user_id, course_key)] = prefetched
        return prefetched

    @classmethod
    def _update_cache(cls, user_id, course_key, visible_blocks):
        """
        Adds a specific set of visible blocks to the request cache.
        This assumes that prefetch has already been called.
        """
        get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(user_id, course_key)].update(
            {visible_block.hashed: visible_block for visible_block in visible_blocks}
        )

    @classmethod
    def _cache_key(cls, user_id, course_key):
        return f"visible_blocks_cache.{course_key}.{user_id}"


class PersistentSubsectionGrade(TimeStampedModel):
    """
    A django model tracking persistent grades at the subsection level.

    .. no_pii:
    """

    class Meta:
        app_label = "grades"
        unique_together = [
            # * Specific grades can be pulled using all three columns,
            # * Progress page can pull all grades for a given (course_id, user_id)
            # * Course staff can see all grades for a course using (course_id,)
            ('course_id', 'user_id', 'usage_key'),
        ]
        # Allows querying in the following ways:
        # (modified): find all the grades updated within a certain timespan
        # (modified, course_id): find all the grades updated within a timespan for a certain course
        # (modified, course_id, usage_key): find all the grades updated within a timespan for a subsection
        #   in a course
        # (first_attempted, course_id, user_id): find all attempted subsections in a course for a user
        # (first_attempted, course_id): find all attempted subsections in a course for all users
        indexes = [
            models.Index(
                fields=['modified', 'course_id', 'usage_key'],
                name="course_id_usage_key_idx"
            ),
            models.Index(
                fields=['first_attempted', 'course_id', 'user_id'],
                name="first_course_id_user_id_idx"
            ),
        ]

    # primary key will need to be large for this table
    id = UnsignedBigIntAutoField(primary_key=True)  # pylint: disable=invalid-name

    user_id = models.IntegerField(blank=False)
    course_id = CourseKeyField(blank=False, max_length=255)

    # note: the usage_key may not have the run filled in for
    # old mongo courses.  Use the full_usage_key property
    # instead when you want to use/compare the usage_key.
    usage_key = UsageKeyField(blank=False, max_length=255)

    # Information relating to the state of content when grade was calculated
    subtree_edited_timestamp = models.DateTimeField('Last content edit timestamp', blank=True, null=True)
    course_version = models.CharField('Guid of latest course version', blank=True, max_length=255)

    # earned/possible refers to the number of points achieved and available to achieve.
    # graded refers to the subset of all problems that are marked as being graded.
    earned_all = models.FloatField(blank=False)
    possible_all = models.FloatField(blank=False)
    earned_graded = models.FloatField(blank=False)
    possible_graded = models.FloatField(blank=False)

    # timestamp for the learner's first attempt at content in
    # this subsection. If null, indicates no attempt
    # has yet been made.
    first_attempted = models.DateTimeField(null=True, blank=True)

    # track which blocks were visible at the time of grade calculation
    visible_blocks = models.ForeignKey(VisibleBlocks, db_column='visible_blocks_hash', to_field='hashed',
                                       on_delete=models.CASCADE)

    _CACHE_NAMESPACE = 'grades.models.PersistentSubsectionGrade'

    @property
    def full_usage_key(self):
        """
        Returns the "correct" usage key value with the run filled in.
        """
        if self.usage_key.run is None:  # pylint: disable=no-member
            # pylint: disable=unexpected-keyword-arg,no-value-for-parameter
            return self.usage_key.replace(course_key=self.course_id)
        else:
            return self.usage_key

    def __str__(self):
        """
        Returns a string representation of this model.
        """
        return (  # noqa: UP032
            "{} user: {}, course version: {}, subsection: {} ({}). {}/{} graded, {}/{} all, first_attempted: {}"
        ).format(
            type(self).__name__,
            self.user_id,
            self.course_version,
            self.usage_key,
            self.visible_blocks_id,
            self.earned_graded,
            self.possible_graded,
            self.earned_all,
            self.possible_all,
            self.first_attempted,
        )

    @classmethod
    def prefetch(cls, course_key, users):
        """
        Prefetches grades for the given users in the given course.
        """
        cache_key = cls._cache_key(course_key)
        get_cache(cls._CACHE_NAMESPACE)[cache_key] = defaultdict(list)
        cached_grades = get_cache(cls._CACHE_NAMESPACE)[cache_key]
        queryset = cls.objects.select_related('visible_blocks', 'override').filter(
            user_id__in=[user.id for user in users],
            course_id=course_key,
        )
        for record in queryset:
            cached_grades[record.user_id].append(record)

    @classmethod
    def clear_prefetched_data(cls, course_key):
        """
        Clears prefetched grades for this course from the RequestCache.
        """
        get_cache(cls._CACHE_NAMESPACE).pop(cls._cache_key(course_key), None)

    @classmethod
    def read_grade(cls, user_id, usage_key):
        """
        Reads a grade from database

        Arguments:
            user_id: The user associated with the desired grade
            usage_key: The location of the subsection associated with the desired grade

        Raises PersistentSubsectionGrade.DoesNotExist if applicable
        """
        return cls.objects.select_related('visible_blocks', 'override').get(
            user_id=user_id,
            course_id=usage_key.course_key,  # course_id is included to take advantage of db indexes
            usage_key=usage_key,
        )

    @classmethod
    def bulk_read_grades(cls, user_id, course_key):
        """
        Reads all grades for the given user and course.

        Arguments:
            user_id: The user associated with the desired grades
            course_key: The course identifier for the desired grades
        """
        try:
            prefetched_grades = get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(course_key)]
            try:
                return prefetched_grades[user_id]
            except KeyError:
                # The user's grade is not in the cached dict of subsection grades,
                # so return an empty list.
                return []
        except KeyError:
            # subsection grades were not prefetched for the course, so get them from the DB
            return cls.objects.select_related('visible_blocks', 'override').filter(
                user_id=user_id,
                course_id=course_key,
            )

    @classmethod
    def update_or_create_grade(cls, **params):
        """
        Wrapper for objects.update_or_create.
        """
        cls._prepare_params(params)
        VisibleBlocks.cached_get_or_create(params['user_id'], params['visible_blocks'])
        cls._prepare_params_visible_blocks_id(params)

        # TODO: do we NEED to pop these?
        first_attempted = params.pop('first_attempted')
        user_id = params.pop('user_id')
        usage_key = params.pop('usage_key')

        grade, _ = cls.objects.update_or_create(
            user_id=user_id,
            course_id=usage_key.course_key,
            usage_key=usage_key,
            defaults=params,
        )

        grade.override = PersistentSubsectionGradeOverride.get_override(user_id, usage_key)
        if first_attempted is not None and grade.first_attempted is None:
            grade.first_attempted = first_attempted
            grade.save()

        cls._emit_grade_calculated_event(grade)
        return grade

    @classmethod
    def bulk_create_grades(cls, grade_params_iter, user_id, course_key):
        """
        Bulk creation of grades.
        """
        if not grade_params_iter:
            return

        PersistentSubsectionGradeOverride.prefetch(user_id, course_key)

        list(map(cls._prepare_params, grade_params_iter))
        VisibleBlocks.bulk_get_or_create(
            user_id, course_key, [params['visible_blocks'] for params in grade_params_iter]
        )
        list(map(cls._prepare_params_visible_blocks_id, grade_params_iter))

        grades = [PersistentSubsectionGrade(**params) for params in grade_params_iter]
        grades = cls.objects.bulk_create(grades)
        for grade in grades:
            cls._emit_grade_calculated_event(grade)
        return grades

    @classmethod
    def _prepare_params(cls, params):
        """
        Prepares the fields for the grade record.
        """
        if not params.get('course_id', None):
            params['course_id'] = params['usage_key'].course_key
        params['course_version'] = params.get('course_version', None) or ""
        params['visible_blocks'] = BlockRecordList.from_list(params['visible_blocks'], params['course_id'])

    @classmethod
    def _prepare_params_visible_blocks_id(cls, params):
        """
        Prepares the visible_blocks_id field for the grade record,
        using the hash of the visible_blocks field.  Specifying
        the hashed field eliminates extra queries to get the
        VisibleBlocks record.  Use this variation of preparing
        the params when you are sure of the existence of the
        VisibleBlock.
        """
        params['visible_blocks_id'] = params['visible_blocks'].hash_value
        del params['visible_blocks']

    @staticmethod
    def _emit_grade_calculated_event(grade):
        events.subsection_grade_calculated(grade)

    @classmethod
    def _cache_key(cls, course_id):
        return f"subsection_grades_cache.{course_id}"

    @classmethod
    def delete_subsection_grades_for_learner(cls, user_id, course_key):
        """
        Clears Subsection grades and overrides for a learner in a course
        Arguments:
            user_id: The user associated with the desired grade
            course_id: The id of the course associated with the desired grade
        """
        try:
            deleted_count, deleted_obj = cls.objects.filter(
                user_id=user_id,
                course_id=course_key,
            ).delete()
            get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(course_key)].pop(user_id)
            if deleted_obj['grades.PersistentSubsectionGradeOverride'] is not None:
                PersistentSubsectionGradeOverride.clear_prefetched_overrides_for_learner(user_id, course_key)
        except KeyError:
            pass

        return deleted_count


class PersistentCourseGrade(TimeStampedModel):
    """
    A django model tracking persistent course grades.

    .. no_pii:
    """

    class Meta:
        app_label = "grades"
        # Indices:
        # (course_id, user_id) for individual grades
        # (course_id) for instructors to see all course grades, implicitly created via the unique_together constraint
        # (user_id) for course dashboard; explicitly declared as an index below
        # (passed_timestamp, course_id) for tracking when users first earned a passing grade.
        # (modified): find all the grades updated within a certain timespan
        # (modified, course_id): find all the grades updated within a certain timespan for a course
        unique_together = [
            ('course_id', 'user_id'),
        ]
        indexes = [
            models.Index(fields=['passed_timestamp', 'course_id'], name="passed_timestamp_course_id_idx"),
            models.Index(fields=['modified', 'course_id'], name="modified_course_id_idx")
        ]

    # primary key will need to be large for this table
    id = UnsignedBigIntAutoField(primary_key=True)  # pylint: disable=invalid-name
    user_id = models.IntegerField(blank=False, db_index=True)
    course_id = CourseKeyField(blank=False, max_length=255)

    # Information relating to the state of content when grade was calculated
    course_edited_timestamp = models.DateTimeField('Last content edit timestamp', blank=True, null=True)
    course_version = models.CharField('Course content version identifier', blank=True, max_length=255)
    grading_policy_hash = models.CharField('Hash of grading policy', blank=False, max_length=255)

    # Information about the course grade itself
    percent_grade = models.FloatField(blank=False)
    letter_grade = models.CharField('Letter grade for course', blank=False, max_length=255)

    # Information related to course completion
    passed_timestamp = models.DateTimeField('Date learner earned a passing grade', blank=True, null=True)

    _CACHE_NAMESPACE = "grades.models.PersistentCourseGrade"

    def __str__(self):
        """
        Returns a string representation of this model.
        """
        return ', '.join([
            f"{type(self).__name__} user: {self.user_id}",
            f"course version: {self.course_version}",
            f"grading policy: {self.grading_policy_hash}",
            f"percent grade: {self.percent_grade}%",
            f"letter grade: {self.letter_grade}",
            f"passed timestamp: {self.passed_timestamp}",
        ])

    @classmethod
    def prefetch(cls, course_id, users):
        """
        Prefetches grades for the given users for the given course.
        """
        get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(course_id)] = {
            grade.user_id: grade
            for grade in
            cls.objects.filter(user_id__in=[user.id for user in users], course_id=course_id)
        }

    @classmethod
    def clear_prefetched_data(cls, course_key):
        """
        Clears prefetched grades for this course from the RequestCache.
        """
        get_cache(cls._CACHE_NAMESPACE).pop(cls._cache_key(course_key), None)

    @classmethod
    def read(cls, user_id, course_id):
        """
        Reads a grade from database

        Arguments:
            user_id: The user associated with the desired grade
            course_id: The id of the course associated with the desired grade

        Raises PersistentCourseGrade.DoesNotExist if applicable
        """
        try:
            prefetched_grades = get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(course_id)]
            try:
                return prefetched_grades[user_id]
            except KeyError:
                # user's grade is not in the prefetched dict, so
                # assume they have no grade
                raise cls.DoesNotExist  # pylint: disable=raise-missing-from  # noqa: B904
        except KeyError:
            # grades were not prefetched for the course, so fetch it
            return cls.objects.get(user_id=user_id, course_id=course_id)

    @classmethod
    def update_or_create(cls, user_id, course_id, **kwargs):
        """
        Creates a course grade in the database.
        Returns a PersistedCourseGrade object.
        """
        passed = kwargs.pop('passed')

        if kwargs.get('course_version', None) is None:
            kwargs['course_version'] = ""

        grade, _ = cls.objects.update_or_create(
            user_id=user_id,
            course_id=course_id,
            defaults=kwargs
        )
        if passed and not grade.passed_timestamp:
            COURSE_GRADE_PASSED_FIRST_TIME.send(
                sender=None,
                course_id=course_id,
                user_id=user_id
            )
            grade.passed_timestamp = now()
            grade.save()
            COURSE_GRADE_PASSED_UPDATE_IN_LEARNER_PATHWAY.send(
                sender=None,
                user_id=user_id,
                course_id=course_id,
            )

        cls._emit_grade_calculated_event(grade)
        cls._update_cache(course_id, user_id, grade)
        cls._emit_openedx_persistent_grade_summary_changed_event(course_id, user_id, grade)
        return grade

    @classmethod
    def _update_cache(cls, course_id, user_id, grade):
        course_cache = get_cache(cls._CACHE_NAMESPACE).get(cls._cache_key(course_id))
        if course_cache is not None:
            course_cache[user_id] = grade

    @classmethod
    def _cache_key(cls, course_id):
        return f"grades_cache.{course_id}"

    @staticmethod
    def _emit_grade_calculated_event(grade):
        events.course_grade_calculated(grade)

    @classmethod
    def delete_course_grade_for_learner(cls, course_id, user_id):
        """
        Clears course grade for a learner in a course
        Arguments:
            course_id: The id of the course associated with the desired grade
            user_id: The user associated with the desired grade
        """
        try:
            cls.objects.get(user_id=user_id, course_id=course_id).delete()
            get_cache(cls._CACHE_NAMESPACE)[cls._cache_key(course_id)].pop(user_id)
        except (PersistentCourseGrade.DoesNotExist, KeyError):
            pass

    @staticmethod
    def _emit_openedx_persistent_grade_summary_changed_event(course_id, user_id, grade):
        """
        When called emits an event when a persistent grade is created or updated.
        """
        # .. event_implemented_name: PERSISTENT_GRADE_SUMMARY_CHANGED
        # .. event_type: org.openedx.learning.course.persistent_grade_summary.changed.v1
        PERSISTENT_GRADE_SUMMARY_CHANGED.send_event(
            grade=PersistentCourseGradeData(
                user_id=user_id,
                course=CourseData(
                    course_key=course_id,
                ),
                course_edited_timestamp=grade.course_edited_timestamp,
                course_version=grade.course_version,
                grading_policy_hash=grade.grading_policy_hash,
                percent_grade=grade.percent_grade,
                letter_grade=grade.letter_grade,
                passed_timestamp=grade.passed_timestamp,
            )
        )


class PersistentSubsectionGradeOverride(models.Model):
    """
    A django model tracking persistent grades overrides at the subsection level.

    .. no_pii:
    """
    class Meta:
        app_label = "grades"

    grade = models.OneToOneField(PersistentSubsectionGrade, related_name='override', on_delete=models.CASCADE)  # noqa: DJ012  # pylint: disable=line-too-long

    # Created/modified timestamps prevent race-conditions when using with async rescoring tasks
    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True, db_index=True)

    # earned/possible refers to the number of points achieved and available to achieve.
    # graded refers to the subset of all problems that are marked as being graded.
    earned_all_override = models.FloatField(null=True, blank=True)
    possible_all_override = models.FloatField(null=True, blank=True)
    earned_graded_override = models.FloatField(null=True, blank=True)
    possible_graded_override = models.FloatField(null=True, blank=True)
    # store the source of the system that caused the override
    system = models.CharField(max_length=100, blank=True, null=True)  # noqa: DJ001
    # store the reason for the override
    override_reason = models.CharField(max_length=300, blank=True, null=True)  # noqa: DJ001

    _CACHE_NAMESPACE = "grades.models.PersistentSubsectionGradeOverride"

    # This is necessary because CMS does not install the grades app, but it
    # imports this models code. Simple History will attempt to connect to the installed
    # model in the grades app, which will fail.
    if 'grades' in apps.app_configs:
        history = HistoricalRecords()
        _history_user = None

    def __str__(self):
        return ', '.join([
            f"{type(self).__name__}",
            f"earned_all_override: {self.earned_all_override}",
            f"possible_all_override: {self.possible_all_override}",
            f"earned_graded_override: {self.earned_graded_override}",
            f"possible_graded_override: {self.possible_graded_override}",
        ])

    def get_history(self):
        return self.history.all()  # pylint: disable=no-member

    @classmethod
    def prefetch(cls, user_id, course_key):
        get_cache(cls._CACHE_NAMESPACE)[(user_id, str(course_key))] = {
            override.grade.usage_key: override
            for override in
            cls.objects.filter(grade__user_id=user_id, grade__course_id=course_key)
        }

    @classmethod
    def get_override(cls, user_id, usage_key):  # pylint: disable=missing-function-docstring
        prefetch_values = get_cache(cls._CACHE_NAMESPACE).get((user_id, str(usage_key.course_key)), None)
        if prefetch_values is not None:
            return prefetch_values.get(usage_key)
        try:
            return cls.objects.get(
                grade__user_id=user_id,
                grade__course_id=usage_key.course_key,
                grade__usage_key=usage_key,
            )
        except PersistentSubsectionGradeOverride.DoesNotExist:
            pass

    @classmethod
    def update_or_create_override(
        cls, requesting_user, subsection_grade_model, feature=None, action=None, **override_data  # pylint: disable=unused-argument
    ):
        """
        Creates or updates an override object for the given PersistentSubsectionGrade.
        Args:
            requesting_user: The user that is creating the override.
            subsection_grade_model: The PersistentSubsectionGrade object associated with this override.
            override_data: The parameters of score values used to create the override record.
        """
        grade_defaults = cls._prepare_override_params(subsection_grade_model, override_data)
        grade_defaults['override_reason'] = override_data['comment'] if 'comment' in override_data else None
        grade_defaults['system'] = override_data['system'] if 'system' in override_data else None

        try:
            override = PersistentSubsectionGradeOverride.objects.get(grade=subsection_grade_model)
            for key, value in grade_defaults.items():
                setattr(override, key, value)
        except PersistentSubsectionGradeOverride.DoesNotExist:
            override = PersistentSubsectionGradeOverride(grade=subsection_grade_model, **grade_defaults)
        if requesting_user:
            # setting this on a non-field attribute which simple
            # history reads from to determine which user to attach to
            # the history row
            override._history_user = requesting_user  # pylint: disable=protected-access
        override.save()

        return override

    @staticmethod
    def _prepare_override_params(subsection_grade_model, override_data):
        """
        Helper method to strip any grade override field names that won't work
        as defaults when calling PersistentSubsectionGradeOverride.update_or_create(),
        and to use default values from the associated PersistentSubsectionGrade
        for any override fields that are not specified.
        """
        allowed_fields_and_defaults = {
            'earned_all_override': 'earned_all',
            'possible_all_override': 'possible_all',
            'earned_graded_override': 'earned_graded',
            'possible_graded_override': 'possible_graded',
        }
        cleaned_data = {}
        for override_field_name, field_name in allowed_fields_and_defaults.items():
            cleaned_data[override_field_name] = override_data.get(
                override_field_name,
                getattr(subsection_grade_model, field_name)
            )
        return cleaned_data

    @classmethod
    def clear_prefetched_overrides_for_learner(cls, user_id, course_key):
        get_cache(cls._CACHE_NAMESPACE).pop((user_id, str(course_key)), None)
