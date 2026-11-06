"""
Serializers for the v2 cohorts REST API.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers

from openedx.core.djangoapps.course_groups import cohorts
from openedx.core.djangoapps.course_groups.models import CourseCohort

User = get_user_model()

ASSIGNMENT_TYPE_CHOICES = ("manual", "random")


class CohortSerializer(serializers.Serializer):
    """
    Representation of a single cohort within a course.

    ``CourseUserGroup`` carries the name and membership, while the assignment
    type and content-group association live on related models, so the fields
    are declared explicitly rather than derived from one model.
    """

    id = serializers.IntegerField(read_only=True, help_text="Identifier of the cohort.")
    name = serializers.CharField(max_length=255, help_text="Display name of the cohort.")
    assignment_type = serializers.ChoiceField(
        choices=ASSIGNMENT_TYPE_CHOICES,
        help_text="How learners are placed into this cohort.",
    )
    user_count = serializers.IntegerField(
        read_only=True,
        help_text="Number of actively enrolled learners in this cohort.",
    )
    user_partition_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="Identifier of the content group configuration this cohort is associated with.",
    )
    group_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text=(
            "Identifier of the content group within the configuration. Send null to unlink. "
            "Requires user_partition_id when not null."
        ),
    )

    def validate(self, attrs):
        """Require the configuration alongside a content group, before anything is written."""
        if attrs.get("group_id") is not None and attrs.get("user_partition_id") is None:
            raise serializers.ValidationError(
                {"user_partition_id": ["Required when group_id is supplied."]}
            )
        return attrs


class CohortUpdateSerializer(CohortSerializer):
    """
    Update payload for a cohort, where every field is optional.

    ``group_id`` is nullable and meaningful when explicitly set to null, which
    unlinks the content group association.
    """

    name = serializers.CharField(max_length=255, required=False, help_text="New display name of the cohort.")
    assignment_type = serializers.ChoiceField(
        choices=ASSIGNMENT_TYPE_CHOICES,
        required=False,
        help_text="How learners are placed into this cohort.",
    )

    def validate(self, attrs):
        """Refuse an empty update, then apply the content-group rule."""
        if not attrs:
            raise serializers.ValidationError(
                "Supply at least one of name, assignment_type or group_id."
            )
        return super().validate(attrs)


class CohortMemberSerializer(serializers.ModelSerializer):
    """
    Representation of a learner who belongs to a cohort.
    """

    name = serializers.SerializerMethodField(help_text="The learner's full name.")

    class Meta:
        model = User
        fields = ("username", "email", "name")

    def get_name(self, user) -> str:
        """Return the learner's full name."""
        return f"{user.first_name} {user.last_name}".strip()


class CohortMembershipRequestSerializer(serializers.Serializer):
    """
    Request payload for adding learners to a cohort.
    """

    users = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        allow_empty=False,
        help_text="Usernames or email addresses to place in this cohort.",
    )


class CohortMembershipResultSerializer(serializers.Serializer):
    """
    Outcome of an add-learners request, partitioned by what happened to each entry.
    """

    added = serializers.ListField(
        child=serializers.DictField(),
        help_text="Learners placed in this cohort who were in no cohort before: username and email.",
    )
    changed = serializers.ListField(
        child=serializers.DictField(),
        help_text="Learners moved here from another cohort: username, email and previous_cohort.",
    )
    present = serializers.ListField(
        child=serializers.CharField(),
        help_text="Entries already in this cohort.",
    )
    unknown = serializers.ListField(
        child=serializers.CharField(),
        help_text="Entries matching no user.",
    )
    preassigned = serializers.ListField(
        child=serializers.CharField(),
        help_text="Email addresses with no account yet, assigned to this cohort when they register.",
    )
    invalid = serializers.ListField(
        child=serializers.CharField(),
        help_text="Entries that are not valid email addresses.",
    )


class CohortSettingsSerializer(serializers.Serializer):
    """
    Course-level cohort configuration.
    """

    id = serializers.IntegerField(
        read_only=True,
        allow_null=True,
        help_text="Identifier of the stored settings, or null when the course has none stored yet.",
    )
    is_cohorted = serializers.BooleanField(
        help_text="Whether cohorts are enabled for this course.",
    )


def assignment_type_of(cohort):
    """
    Return the cohort's assignment type.

    Cohorts created before ``CourseCohort`` existed have no row there until a
    settings migration backfills one, and the platform then records them as
    manual; they are manual here too.
    """
    if hasattr(cohort, "cohort"):
        return cohorts.get_assignment_type(cohort)
    return CourseCohort.MANUAL


def represent_cohort(cohort, course_key):
    """
    Build the serializable representation of a cohort.
    """
    group_id, partition_id = cohorts.get_group_info_for_cohort(cohort)
    return {
        "id": cohort.id,
        "name": cohort.name,
        "assignment_type": assignment_type_of(cohort),
        "user_count": cohort.users.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=1,
        ).count(),
        "user_partition_id": partition_id,
        "group_id": group_id,
    }
