"""
Support for inheritance of fields down an XBlock hierarchy.
"""


import warnings
from django.utils import timezone
from xblock.core import XBlockMixin
from xblock.fields import Boolean, Date, Dict, Float, Integer, List, Scope, String, Timedelta
from xblock.runtime import KeyValueStore, KvsFieldData

from xmodule.error_block import ErrorBlock
from xmodule.partitions.partitions import UserPartition

from ..course_metadata_utils import DEFAULT_START_DATE

# Make '_' a no-op so we can scrape strings
# Using lambda instead of `django.utils.translation.ugettext_noop` because Django cannot be imported in this file
_ = lambda text: text


class UserPartitionList(List):
    """Special List class for listing UserPartitions"""
    def from_json(self, values):  # lint-amnesty, pylint: disable=arguments-differ
        return [UserPartition.from_json(v) for v in values]

    def to_json(self, values):  # lint-amnesty, pylint: disable=arguments-differ
        return [user_partition.to_json()
                for user_partition in values]


class InheritanceMixin(XBlockMixin):
    """Field definitions for inheritable fields."""

    graded = Boolean(
        help="Whether this block contributes to the final course grade",
        scope=Scope.settings,
        default=False,
    )
    start = Date(
        help="Start time when this block is visible",
        default=DEFAULT_START_DATE,
        scope=Scope.settings
    )
    due = Date(
        display_name=_("Due Date"),
        help=_("Enter the default date by which problems are due."),
        scope=Scope.settings,
    )
    # This attribute is for custom pacing in self paced courses for Studio if CUSTOM_RELATIVE_DATES flag is active
    relative_weeks_due = Integer(
        display_name=_("Number of Relative Weeks Due By"),
        help=_("Enter the number of weeks the problems are due by relative to the learner's enrollment date"),
        scope=Scope.settings,
    )
    visible_to_staff_only = Boolean(
        help=_("If true, can be seen only by course staff, regardless of start date."),
        default=False,
        scope=Scope.settings,
    )
    course_edit_method = String(
        display_name=_("Course Editor"),
        help=_("Enter the method by which this course is edited (\"XML\" or \"Studio\")."),
        default="Studio",
        scope=Scope.settings,
        deprecated=True  # Deprecated because user would not change away from Studio within Studio.
    )
    giturl = String(
        display_name=_("GIT URL"),
        help=_("Enter the URL for the course data GIT repository."),
        scope=Scope.settings
    )
    xqa_key = String(
        display_name=_("XQA Key"),
        help=_("This setting is not currently supported."), scope=Scope.settings,
        deprecated=True
    )
    graceperiod = Timedelta(
        help="Amount of time after the due date that submissions will be accepted",
        scope=Scope.settings,
    )
    group_access = Dict(
        help=_("Enter the ids for the content groups this problem belongs to."),
        scope=Scope.settings,
    )

    showanswer = String(
        display_name=_("Show Answer"),
        help=_(
            # Translators: DO NOT translate the words in quotes here, they are
            # specific words for the acceptable values.
            'Specify when the Show Answer button appears for each problem. '
            'Valid values are "always", "answered", "attempted", "closed", '
            '"finished", "past_due", "correct_or_past_due", "after_all_attempts", '
            '"after_all_attempts_or_correct", "attempted_no_past_due", and "never".'
        ),
        scope=Scope.settings,
        default="finished",
    )

    show_correctness = String(
        display_name=_("Show Results"),
        help=_(
            # Translators: DO NOT translate the words in quotes here, they are
            # specific words for the acceptable values.
            'Specify when to show answer correctness and score to learners. '
            'Valid values are "always", "never", and "past_due".'
        ),
        scope=Scope.settings,
        default="always",
    )

    rerandomize = String(
        display_name=_("Randomization"),
        help=_(
            # Translators: DO NOT translate the words in quotes here, they are
            # specific words for the acceptable values.
            'Specify the default for how often variable values in a problem are randomized. '
            'This setting should be set to "never" unless you plan to provide a Python '
            'script to identify and randomize values in most of the problems in your course. '
            'Valid values are "always", "onreset", "never", and "per_student".'
        ),
        scope=Scope.settings,
        default="never",
    )
    days_early_for_beta = Float(
        display_name=_("Days Early for Beta Users"),
        help=_("Enter the number of days before the start date that beta users can access the course."),
        scope=Scope.settings,
        default=None,
    )
    static_asset_path = String(
        display_name=_("Static Asset Path"),
        help=_("Enter the path to use for files on the Files & Uploads page. This value overrides the Studio default, c4x://."),  # lint-amnesty, pylint: disable=line-too-long
        scope=Scope.settings,
        default='',
    )
    use_latex_compiler = Boolean(
        display_name=_("Enable LaTeX Compiler"),
        help=_("Enter true or false. If true, you can use the LaTeX templates for HTML components and advanced Problem components."),  # lint-amnesty, pylint: disable=line-too-long
        default=False,
        scope=Scope.settings
    )
    max_attempts = Integer(
        display_name=_("Maximum Attempts"),
        help=_("Enter the maximum number of times a student can try to answer problems. By default, Maximum Attempts is set to null, meaning that students have an unlimited number of attempts for problems. You can override this course-wide setting for individual problems. However, if the course-wide setting is a specific number, you cannot set the Maximum Attempts for individual problems to unlimited."),  # lint-amnesty, pylint: disable=line-too-long
        values={"min": 0}, scope=Scope.settings
    )
    matlab_api_key = String(
        display_name=_("Matlab API key"),
        help=_("Enter the API key provided by MathWorks for accessing the MATLAB Hosted Service. "
               "This key is granted for exclusive use in this course for the specified duration. "
               "Do not share the API key with other courses. Notify MathWorks immediately "
               "if you believe the key is exposed or compromised. To obtain a key for your course, "
               "or to report an issue, please contact moocsupport@mathworks.com"),
        scope=Scope.settings
    )
    # This is should be scoped to content, but since it's defined in the policy
    # file, it is currently scoped to settings.
    user_partitions = UserPartitionList(
        display_name=_("Group Configurations"),
        help=_("Enter the configurations that govern how students are grouped together."),
        default=[],
        scope=Scope.settings
    )
    video_speed_optimizations = Boolean(
        display_name=_("Enable video caching system"),
        help=_("Enter true or false. If true, video caching will be used for HTML5 videos."),
        default=True,
        scope=Scope.settings
    )
    video_auto_advance = Boolean(
        display_name=_("Enable video auto-advance"),
        help=_(
            "Specify whether to show an auto-advance button in videos. If the student clicks it, when the last video in a unit finishes it will automatically move to the next unit and autoplay the first video."  # lint-amnesty, pylint: disable=line-too-long
        ),
        scope=Scope.settings,
        default=False
    )
    video_bumper = Dict(
        display_name=_("Video Pre-Roll"),
        help=_(
            "Identify a video, 5-10 seconds in length, to play before course videos. Enter the video ID from "
            "the Video Uploads page and one or more transcript files in the following format: {format}. "
            "For example, an entry for a video with two transcripts looks like this: {example}"
        ),
        help_format_args=dict(
            format='{"video_id": "ID", "transcripts": {"language": "/static/filename.srt"}}',
            example=(
                '{'
                '"video_id": "77cef264-d6f5-4cf2-ad9d-0178ab8c77be", '
                '"transcripts": {"en": "/static/DemoX-D01_1.srt", "uk": "/static/DemoX-D01_1_uk.srt"}'
                '}'
            ),
        ),
        scope=Scope.settings
    )

    show_reset_button = Boolean(
        display_name=_("Show Reset Button for Problems"),
        help=_(
            "Enter true or false. If true, problems in the course default to always displaying a 'Reset' button. "
            "You can override this in each problem's settings. All existing problems are affected when "
            "this course-wide setting is changed."
        ),
        scope=Scope.settings,
        default=False
    )
    edxnotes = Boolean(
        display_name=_("Enable Student Notes"),
        help=_("Enter true or false. If true, students can use the Student Notes feature."),
        default=False,
        scope=Scope.settings
    )
    edxnotes_visibility = Boolean(
        display_name="Student Notes Visibility",
        help=_("Indicates whether Student Notes are visible in the course. "
               "Students can also show or hide their notes in the courseware."),
        default=True,
        scope=Scope.user_info
    )

    in_entrance_exam = Boolean(
        display_name=_("Tag this block as part of an Entrance Exam section"),
        help=_("Enter true or false. If true, answer submissions for problem blocks will be "
               "considered in the Entrance Exam scoring/gating algorithm."),
        scope=Scope.settings,
        default=False
    )

    self_paced = Boolean(
        display_name=_('Self Paced'),
        help=_(
            'Set this to "true" to mark this course as self-paced. Self-paced courses do not have '
            'due dates for assignments, and students can progress through the course at any rate before '
            'the course ends.'
        ),
        default=False,
        scope=Scope.settings
    )

    hide_from_toc = Boolean(
        display_name=_("Hide from Table of Contents"),
        help=_("Enter true or false. If true, this block will be hidden from the Table of Contents."),
        default=False,
        scope=Scope.settings
    )

    @property
    def close_date(self):
        """
        Return the date submissions should be closed from.

        If graceperiod is present for the course, all the submissions
        can be submitted till due date and the graceperiod. If no
        graceperiod, then the close date is same as the due date.
        """
        due_date = self.due

        if self.graceperiod is not None and due_date:
            return due_date + self.graceperiod
        return due_date

    def is_past_due(self):
        """
        Returns the boolean identifying if the submission due date has passed.
        """
        return self.close_date is not None and timezone.now() > self.close_date

    def has_deadline_passed(self):
        """
        Returns a boolean indicating if the submission is past its deadline.

        If the course is self-paced or no due date has been
        specified, then the submission can be made. If none of these
        cases exists, check if the submission due date has passed or not.
        """
        if self.self_paced or self.close_date is None:
            return False
        return self.is_past_due()


def compute_inherited_metadata(block):
    """Given a block, traverse all of its descendants and do metadata
    inheritance.  Should be called on a CourseBlock after importing a
    course.

    NOTE: This means that there is no such thing as lazy loading at the
    moment--this accesses all the children."""
    if block.has_children:
        if isinstance(block.xblock_kvs, InheritanceKeyValueStore):
            parent_metadata = block.xblock_kvs.inherited_settings.copy()
        else:
            parent_metadata = {}
        # add any of block's explicitly set fields to the inheriting list
        for field in InheritanceMixin.fields.values():  # lint-amnesty, pylint: disable=no-member
            if field.is_set_on(block):
                # inherited_settings values are json repr
                parent_metadata[field.name] = field.read_json(block)

        for child in block.get_children():
            inherit_metadata(child, parent_metadata)
            compute_inherited_metadata(child)


def inherit_metadata(block, inherited_data):
    """
    Updates this block with metadata inherited from a containing block.
    Only metadata specified in self.inheritable_metadata will
    be inherited

    `inherited_data`: A dictionary mapping field names to the values that
        they should inherit
    """
    if isinstance(block, ErrorBlock):
        return

    block_type = block.scope_ids.block_type
    if isinstance(block.xblock_kvs, InheritanceKeyValueStore):
        # This XBlock's field_data is backed by InheritanceKeyValueStore, which supports pre-computed inherited fields
        block.xblock_kvs.inherited_settings = inherited_data
    else:
        # We cannot apply pre-computed field data to this XBlock during import, but inheritance should still work
        # normally when it's used in Studio/LMS, which use a different runtime.
        # Though if someone ever needs a hacky temporary fix here, it's possible here to force it with:
        #     init_dict = {key: getattr(block, key) for key in block.fields.keys()}
        #     block._field_data = InheritanceKeyValueStore(init_dict)
        warnings.warn(
            f'Cannot inherit metadata to {block_type} block with KVS {block.xblock_kvs}',
            stacklevel=2,
        )


def own_metadata(block):
    """
    Return a JSON-friendly dictionary that contains only non-inherited field
    keys, mapped to their serialized values
    """
    return block.get_explicitly_set_fields_by_scope(Scope.settings)


class InheritingFieldData(KvsFieldData):
    """
    A `FieldData` implementation that can inherit value from parents to children.

    This wraps a KeyValueStore, and will work fine with any KVS implementation.
    Sometimes this wraps a subclass of InheritanceKeyValueStore, but that's not
    a requirement.

    This class is the way that inheritance "normally" works in modulestore.
    During XML import/export, however, a different mechanism is used:
    InheritanceKeyValueStore.
    """

    def __init__(self, inheritable_names, **kwargs):
        """
        `inheritable_names` is a list of names that can be inherited from
        parents.

        """
        super().__init__(**kwargs)
        self.inheritable_names = set(inheritable_names)

    def has_default_value(self, name):
        """
        Return whether or not the field `name` has a default value
        """
        has_default_value = getattr(self._kvs, 'has_default_value', False)
        if callable(has_default_value):
            return has_default_value(name)

        return has_default_value

    def default(self, block, name):
        """
        The default for an inheritable name is found on a parent.
        """
        if name in self.inheritable_names:
            # Walk up the content tree to find the first ancestor
            # that this field is set on. Use the field from the current
            # block so that if it has a different default than the root
            # node of the tree, the block's default will be used.
            field = block.fields[name]
            ancestor = block.get_parent()
            # In case, if block's parent is of type 'library_content',
            # bypass inheritance and use kvs' default instead of reusing
            # from parent as '_copy_from_templates' puts fields into
            # defaults.
            if ancestor and \
               ancestor.location.block_type == 'library_content' and \
               self.has_default_value(name):
                return super().default(block, name)

            while ancestor is not None:
                if field.is_set_on(ancestor):
                    return field.read_json(ancestor)
                else:
                    ancestor = ancestor.get_parent()
        return super().default(block, name)


def inheriting_field_data(kvs):
    """Create an InheritanceFieldData that inherits the names in InheritanceMixin."""
    return InheritingFieldData(
        inheritable_names=InheritanceMixin.fields.keys(),  # lint-amnesty, pylint: disable=no-member
        kvs=kvs,
    )


class InheritanceKeyValueStore(KeyValueStore):
    """
    Common superclass for kvs's which know about inheritance of settings. Offers simple
    dict-based storage of fields and lookup of inherited values.

    Note: inherited_settings is a dict of key to json values (internal xblock field repr)

    Using this KVS is an alternative to using InheritingFieldData(). That one works with any KVS, like
    DictKeyValueStore, and doesn't require any special behavior. On the other hand, this InheritanceKeyValueStore only
    does inheritance properly if you first use compute_inherited_metadata() to walk the tree of XBlocks and pre-compute
    the inherited metadata for the whole tree, storing it in the inherited_settings field of each instance of this KVS.

    🟥 Warning: Unlike the base class, this KVS makes the assumption that you're using a completely separate KVS
       instance for every XBlock, so that we only have to look at the "field_name" part of the key. You cannot use this
       as a drop-in replacement for DictKeyValueStore for this reason.
    """
    def __init__(self, initial_values=None, inherited_settings=None):
        super().__init__()
        self.inherited_settings = inherited_settings or {}
        self._fields = initial_values or {}

    def get(self, key):
        return self._fields[key.field_name]

    def set(self, key, value):
        # xml backed courses are read-only, but they do have some computed fields
        self._fields[key.field_name] = value

    def delete(self, key):
        del self._fields[key.field_name]

    def has(self, key):
        return key.field_name in self._fields

    def default(self, key):
        """
        Check to see if the default should be from inheritance. If not
        inheriting, this will raise KeyError which will cause the caller to use
        the field's global default.
        """
        return self.inherited_settings[key.field_name]
