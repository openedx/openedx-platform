"""
Discussion API serializers
"""
import html
import re
from typing import Dict  # noqa: UP035
from urllib.parse import urlencode, urlunparse

from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import TextChoices
from django.urls import reverse
from django.utils.html import strip_tags
from rest_framework import serializers

from common.djangoapps.student.models import get_user_by_username_or_email
from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.discussion.django_comment_client.base.views import (
    track_comment_edited_event,
    track_forum_response_mark_event,
    track_thread_edited_event,
    track_thread_lock_unlock_event,
)
from lms.djangoapps.discussion.django_comment_client.utils import (
    course_discussion_division_enabled,
    get_group_id_for_user,
    get_group_name,
    is_comment_too_deep,
)
from lms.djangoapps.discussion.rest_api.permissions import (
    NON_UPDATABLE_COMMENT_FIELDS,
    NON_UPDATABLE_THREAD_FIELDS,
    can_delete,
    get_editable_fields,
)
from lms.djangoapps.discussion.rest_api.render import render_body
from lms.djangoapps.discussion.rest_api.utils import (
    get_course_staff_users_list,
    get_course_ta_users_list,
    get_moderator_users_list,
)
from openedx.core.djangoapps.discussions.models import DiscussionTopicLink
from openedx.core.djangoapps.discussions.utils import get_group_names_by_id
from openedx.core.djangoapps.django_comment_common.comment_client.comment import Comment
from openedx.core.djangoapps.django_comment_common.comment_client.thread import Thread
from openedx.core.djangoapps.django_comment_common.comment_client.user import User as CommentClientUser
from openedx.core.djangoapps.django_comment_common.comment_client.utils import CommentClientRequestError
from openedx.core.djangoapps.django_comment_common.models import CourseDiscussionSettings
from openedx.core.djangoapps.user_api.accounts.api import get_profile_images
from openedx.core.lib.api.serializers import CourseKeyField

User = get_user_model()

CLOSE_REASON_CODES = getattr(settings, "DISCUSSION_MODERATION_CLOSE_REASON_CODES", {})
EDIT_REASON_CODES = getattr(settings, "DISCUSSION_MODERATION_EDIT_REASON_CODES", {})


class TopicOrdering(TextChoices):
    """
    Enum for the available options for ordering topics.
    """
    COURSE_STRUCTURE = "course_structure", "Course Structure"
    ACTIVITY = "activity", "Activity"
    NAME = "name", "Name"


def get_context(course, request, thread=None):
    """
    Returns a context appropriate for use with ThreadSerializer or
    (if thread is provided) CommentSerializer.
    """
    course_staff_user_ids = get_course_staff_users_list(course.id)
    moderator_user_ids = get_moderator_users_list(course.id)
    ta_user_ids = get_course_ta_users_list(course.id)
    requester = request.user
    cc_requester = CommentClientUser.from_django_user(requester).retrieve(course_id=course.id)
    cc_requester["course_id"] = course.id
    course_discussion_settings = CourseDiscussionSettings.get(course.id)
    is_global_staff = GlobalStaff().has_user(requester)
    has_moderation_privilege = requester.id in moderator_user_ids or requester.id in ta_user_ids or is_global_staff
    return {
        "course": course,
        "request": request,
        "thread": thread,
        "discussion_division_enabled": course_discussion_division_enabled(course_discussion_settings),
        "group_ids_to_names": get_group_names_by_id(course_discussion_settings),
        "moderator_user_ids": moderator_user_ids,
        "course_staff_user_ids": course_staff_user_ids,
        "ta_user_ids": ta_user_ids,
        "cc_requester": cc_requester,
        "has_moderation_privilege": has_moderation_privilege,
        "is_global_staff": is_global_staff,
        "is_staff_or_admin": requester.id in course_staff_user_ids,
    }


def validate_not_blank(value):
    """
    Validate that a value is not an empty string or whitespace.

    Raises: ValidationError
    """
    if not value.strip():
        raise ValidationError("This field may not be blank.")


def validate_edit_reason_code(value):
    """
    Validate that the value is a valid edit reason code.

    Raises: ValidationError
    """
    if value not in EDIT_REASON_CODES:
        raise ValidationError("Invalid edit reason code")


def validate_close_reason_code(value):
    """
    Validate that the value is a valid close reason code.

    Raises: ValidationError
    """
    if value not in CLOSE_REASON_CODES:
        raise ValidationError("Invalid close reason code")


def _validate_privileged_access(context: Dict) -> bool:  # noqa: UP006
    """
    Return the field specified by ``field_name`` if requesting user is privileged.

    Checks that the course exists in the context, and that the user has privileged
    access.

    Args:
        context (Dict): The serializer context.

    Returns:
        bool: Course exists and the user has privileged access.
    """
    course = context.get('course', None)
    is_requester_privileged = context.get('has_moderation_privilege')
    return course and is_requester_privileged


def filter_spam_urls_from_html(html_string):
    """
    Filters out spam posts from html
    Returns:
        clean_post, is_spam
    """
    html_string = html.unescape(html_string)
    soup = BeautifulSoup(html_string, "html.parser")
    patterns = []
    is_spam = False
    for domain in settings.DISCUSSION_SPAM_URLS:
        escaped = domain.replace(".", r"\.")
        domain_pattern = rf"(\w+\.)*{escaped}(?:/\S*)*"
        patterns.append(re.compile(rf"(https?://)?{domain_pattern}", re.IGNORECASE))

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get('href')
        if href:
            if any(p.search(href) for p in patterns):
                a_tag.replace_with(a_tag.get_text(strip=True))
                is_spam = True

    for text_node in soup.find_all(string=True):
        new_text = text_node
        for p in patterns:
            new_text = p.sub('', new_text)
        if new_text != text_node:
            text_node.replace_with(new_text.strip())
            is_spam = True

    return str(soup), is_spam


class _ContentSerializer(serializers.Serializer):
    # pylint: disable=abstract-method
    """
    A base class for thread and comment serializers.
    """
    id = serializers.CharField(read_only=True)  # pylint: disable=invalid-name
    author = serializers.SerializerMethodField()
    author_label = serializers.SerializerMethodField()
    created_at = serializers.CharField(read_only=True)
    updated_at = serializers.CharField(read_only=True)
    raw_body = serializers.CharField(source="body", validators=[validate_not_blank])
    rendered_body = serializers.SerializerMethodField()
    abuse_flagged = serializers.SerializerMethodField()
    voted = serializers.SerializerMethodField()
    vote_count = serializers.SerializerMethodField()
    editable_fields = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    anonymous = serializers.BooleanField(default=False)
    anonymous_to_peers = serializers.BooleanField(default=False)
    last_edit = serializers.SerializerMethodField(required=False)
    edit_reason_code = serializers.CharField(required=False, validators=[validate_edit_reason_code])
    edit_by_label = serializers.SerializerMethodField(required=False)

    non_updatable_fields = set()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rendered_body = None

        for field in self.non_updatable_fields:
            setattr(self, f"validate_{field}", self._validate_non_updatable)

    def _validate_non_updatable(self, value):
        """Ensure that a field is not edited in an update operation."""
        if self.instance:
            raise ValidationError("This field is not allowed in an update.")
        return value

    def _is_user_privileged(self, user_id):
        """
        Returns a boolean indicating whether the given user_id identifies a
        privileged user.
        """
        return user_id in self.context["moderator_user_ids"] or user_id in self.context["ta_user_ids"]

    def _is_anonymous(self, obj):
        """
        Returns a boolean indicating whether the content should be anonymous to
        the requester.
        """
        user_id = self.context["request"].user.id
        is_user_staff = user_id in self.context["moderator_user_ids"] or user_id in self.context["ta_user_ids"]

        return (
            obj["anonymous"] or
            obj["anonymous_to_peers"] and not is_user_staff
        )

    def get_author(self, obj):
        """
        Returns the author's username, or None if the content is anonymous.
        """
        return None if self._is_anonymous(obj) else obj["username"]

    def _get_user_label(self, user_id):
        """
        Returns the role label (i.e. "Staff", "Moderator" or "Community TA") for the user
        with the given id.
        """
        is_staff = user_id in self.context["course_staff_user_ids"]
        is_moderator = user_id in self.context["moderator_user_ids"]
        is_ta = user_id in self.context["ta_user_ids"]

        return (
            "Staff" if is_staff else
            "Moderator" if is_moderator else
            "Community TA" if is_ta else
            None
        )

    def _get_user_label_from_username(self, username):
        """
        Returns role label of user from username
        Possible Role Labels: Staff, Moderator, Community TA or None
        """
        try:
            user = User.objects.get(username=username)
            return self._get_user_label(user.id)
        except ObjectDoesNotExist:
            return None

    def get_author_label(self, obj):
        """
        Returns the role label for the content author.
        """
        if self._is_anonymous(obj) or obj["user_id"] is None:
            return None
        else:
            user_id = int(obj["user_id"])
            return self._get_user_label(user_id)

    def get_rendered_body(self, obj):
        """
        Returns the rendered body content.
        """
        if self._rendered_body is None:
            self._rendered_body = render_body(obj["body"])
            self._rendered_body, is_spam = filter_spam_urls_from_html(self._rendered_body)
            if is_spam and settings.CONTENT_FOR_SPAM_POSTS:
                self._rendered_body = settings.CONTENT_FOR_SPAM_POSTS
        return self._rendered_body

    def get_abuse_flagged(self, obj):
        """
        Returns a boolean indicating whether the requester has flagged the
        content as abusive.
        """
        total_abuse_flaggers = len(obj.get("abuse_flaggers", []))
        return (
            self.context["has_moderation_privilege"] and total_abuse_flaggers > 0 or
            self.context["cc_requester"]["id"] in obj.get("abuse_flaggers", [])
        )

    def get_voted(self, obj):
        """
        Returns a boolean indicating whether the requester has voted for the
        content.
        """
        return obj["id"] in self.context["cc_requester"]["upvoted_ids"]

    def get_vote_count(self, obj):
        """
        Returns the number of votes for the content.
        """
        return obj.get("votes", {}).get("up_count", 0)

    def get_editable_fields(self, obj):
        """
        Return the list of the fields the requester can edit
        """
        return sorted(get_editable_fields(obj, self.context))

    def get_can_delete(self, obj):
        """
        Returns if the current user can delete this thread/comment.
        """
        return can_delete(obj, self.context)

    def get_last_edit(self, obj):
        """
        Returns information about the last edit for this content for
        privileged users.
        """
        is_user_author = str(obj['user_id']) == str(self.context['request'].user.id)
        if not (_validate_privileged_access(self.context) or is_user_author):
            return None
        edit_history = obj.get("edit_history")
        if not edit_history:
            return None
        last_edit = edit_history[-1]
        reason_code = last_edit.get("reason_code")
        if reason_code:
            last_edit["reason"] = EDIT_REASON_CODES.get(reason_code)
        return last_edit

    def get_edit_by_label(self, obj):
        """
        Returns the role label for the last edit user.
        """
        is_user_author = str(obj['user_id']) == str(self.context['request'].user.id)
        is_user_privileged = _validate_privileged_access(self.context)
        edit_history = obj.get("edit_history")
        if (is_user_author or is_user_privileged) and edit_history:
            last_edit = edit_history[-1]
            return self._get_user_label_from_username(last_edit.get('editor_username'))


class ThreadSerializer(_ContentSerializer):
    """
    A serializer for thread data.

    N.B. This should not be used with a comment_client Thread object that has
    not had retrieve() called, because of the interaction between DRF's attempts
    at introspection and Thread's __getattr__.
    """
    course_id = serializers.CharField()
    topic_id = serializers.CharField(source="commentable_id", validators=[validate_not_blank])
    group_id = serializers.IntegerField(required=False, allow_null=True)
    group_name = serializers.SerializerMethodField()
    type = serializers.ChoiceField(
        source="thread_type",
        choices=[(val, val) for val in ["discussion", "question"]]
    )
    preview_body = serializers.SerializerMethodField()
    abuse_flagged_count = serializers.SerializerMethodField(required=False)
    title = serializers.CharField(validators=[validate_not_blank])
    pinned = serializers.SerializerMethodField()
    closed = serializers.BooleanField(required=False)
    following = serializers.SerializerMethodField()
    comment_count = serializers.SerializerMethodField(read_only=True)
    unread_comment_count = serializers.SerializerMethodField(read_only=True)
    comment_list_url = serializers.SerializerMethodField()
    endorsed_comment_list_url = serializers.SerializerMethodField()
    non_endorsed_comment_list_url = serializers.SerializerMethodField()
    read = serializers.BooleanField(required=False)
    has_endorsed = serializers.BooleanField(source="endorsed", read_only=True)
    response_count = serializers.IntegerField(source="resp_total", read_only=True, required=False)
    close_reason_code = serializers.CharField(required=False, validators=[validate_close_reason_code])
    close_reason = serializers.SerializerMethodField()
    closed_by = serializers.SerializerMethodField()
    closed_by_label = serializers.SerializerMethodField(required=False)

    non_updatable_fields = NON_UPDATABLE_THREAD_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Compensate for the fact that some threads in the comments service do
        # not have the pinned field set
        if self.instance and self.instance.get("pinned") is None:
            self.instance["pinned"] = False

    def get_abuse_flagged_count(self, obj):
        """
        Returns the number of users that flagged content as abusive only if user has staff permissions
        """
        if _validate_privileged_access(self.context):
            return obj.get("abuse_flagged_count")

    def get_pinned(self, obj):
        """
        Compensate for the fact that some threads in the comments service do
        not have the pinned field set.
        """
        return bool(obj["pinned"])

    def get_group_name(self, obj):
        """
        Returns the name of the group identified by the thread's group_id.
        """
        return self.context["group_ids_to_names"].get(obj["group_id"])

    def get_following(self, obj):
        """
        Returns a boolean indicating whether the requester is following the
        thread.
        """
        return obj["id"] in self.context["cc_requester"]["subscribed_thread_ids"]

    def get_comment_list_url(self, obj, endorsed=None):
        """
        Returns the URL to retrieve the thread's comments, optionally including
        the endorsed query parameter.
        """
        if (
            (obj["thread_type"] == "question" and endorsed is None) or
            (obj["thread_type"] == "discussion" and endorsed is not None)
        ):
            return None
        path = reverse("comment-list")
        query_dict = {"thread_id": obj["id"]}
        if endorsed is not None:
            query_dict["endorsed"] = endorsed
        return self.context["request"].build_absolute_uri(
            urlunparse(("", "", path, "", urlencode(query_dict), ""))
        )

    def get_endorsed_comment_list_url(self, obj):
        """
        Returns the URL to retrieve the thread's endorsed comments.
        """
        return self.get_comment_list_url(obj, endorsed=True)

    def get_non_endorsed_comment_list_url(self, obj):
        """
        Returns the URL to retrieve the thread's non-endorsed comments.
        """
        return self.get_comment_list_url(obj, endorsed=False)

    def get_comment_count(self, obj):
        """
        Increments comment count to include post and returns total count of
        contributions (i.e. post + responses + comments) for the thread
        """
        return obj["comments_count"] + 1

    def get_unread_comment_count(self, obj):
        """
        Returns the number of unread comments. If the thread has never been read,
        this additionally includes 1 for the post itself, in addition to its responses and
        comments.
        """
        if not obj["read"] and obj["comments_count"] == obj["unread_comments_count"]:
            return obj["unread_comments_count"] + 1
        return obj["unread_comments_count"]

    def get_preview_body(self, obj):
        """
        Returns a cleaned version of the thread's body to display in a preview capacity.
        """
        return strip_tags(self.get_rendered_body(obj)).replace('\n', ' ').replace('&nbsp;', ' ')

    def get_close_reason(self, obj):
        """
        Returns the reason for which the thread was closed.
        """
        is_user_author = str(obj['user_id']) == str(self.context['request'].user.id)
        if not (_validate_privileged_access(self.context) or is_user_author):
            return None
        reason_code = obj.get("close_reason_code")
        return CLOSE_REASON_CODES.get(reason_code)

    def get_closed_by(self, obj):
        """
        Returns the username of the moderator who closed this thread,
        only to other privileged users and author.
        """
        is_user_author = str(obj['user_id']) == str(self.context['request'].user.id)
        if _validate_privileged_access(self.context) or is_user_author:
            return obj.get("closed_by")

    def get_closed_by_label(self, obj):
        """
        Returns the role label for the user who closed the post.
        """
        is_user_author = str(obj['user_id']) == str(self.context['request'].user.id)
        if is_user_author or _validate_privileged_access(self.context):
            return self._get_user_label_from_username(obj.get("closed_by"))

    def create(self, validated_data):
        thread = Thread(user_id=self.context["cc_requester"]["id"], **validated_data)
        thread.save()
        return thread

    def update(self, instance, validated_data):
        for key, val in validated_data.items():
            instance[key] = val
            requesting_user_id = self.context["cc_requester"]["id"]
            if key == "closed" and val:
                instance["closing_user_id"] = requesting_user_id
                track_thread_lock_unlock_event(self.context['request'], self.context['course'],
                                               instance, validated_data.get('close_reason_code'))

            if key == "closed" and not val:
                instance["closing_user_id"] = requesting_user_id
                track_thread_lock_unlock_event(self.context['request'], self.context['course'],
                                               instance, validated_data.get('close_reason_code'), locked=False)

            if key == "body" and val:
                instance["editing_user_id"] = requesting_user_id
                track_thread_edited_event(self.context['request'], self.context['course'],
                                          instance, validated_data.get('edit_reason_code'))
        instance.save()
        return instance


class CommentSerializer(_ContentSerializer):
    """
    A serializer for comment data.

    N.B. This should not be used with a comment_client Comment object that has
    not had retrieve() called, because of the interaction between DRF's attempts
    at introspection and Comment's __getattr__.
    """
    thread_id = serializers.CharField()
    parent_id = serializers.CharField(required=False, allow_null=True)
    endorsed = serializers.BooleanField(required=False)
    endorsed_by = serializers.SerializerMethodField()
    endorsed_by_label = serializers.SerializerMethodField()
    endorsed_at = serializers.SerializerMethodField()
    child_count = serializers.IntegerField(read_only=True)
    children = serializers.SerializerMethodField(required=False)
    abuse_flagged_any_user = serializers.SerializerMethodField(required=False)
    profile_image = serializers.SerializerMethodField(read_only=True)

    non_updatable_fields = NON_UPDATABLE_COMMENT_FIELDS

    def __init__(self, *args, **kwargs):
        remove_fields = kwargs.pop('remove_fields', None)
        super().__init__(*args, **kwargs)

        if remove_fields:
            # for multiple fields in a list
            for field_name in remove_fields:
                self.fields.pop(field_name)

    def get_endorsed_by(self, obj):
        """
        Returns the username of the endorsing user, if the information is
        available and would not identify the author of an anonymous thread.
        This information is unavailable outside the thread context.
        """
        if not self.context.get("thread"):
            return None
        endorsement = obj.get("endorsement")
        if endorsement:
            endorser_id = int(endorsement["user_id"])
            # Avoid revealing the identity of an anonymous non-staff question
            # author who has endorsed a comment in the thread
            if not (
                self._is_anonymous(self.context["thread"]) and
                not self._is_user_privileged(endorser_id)
            ):
                return User.objects.get(id=endorser_id).username
        return None

    def get_endorsed_by_label(self, obj):
        """
        Returns the role label (i.e. "Staff" or "Community TA") for the
        endorsing user.
        This information is unavailable outside the thread context.
        """
        if not self.context.get("thread"):
            return None
        endorsement = obj.get("endorsement")
        if endorsement:
            return self._get_user_label(int(endorsement["user_id"]))
        else:
            return None

    def get_endorsed_at(self, obj):
        """
        Returns the timestamp for the endorsement, if available.
        This information is unavailable outside the thread context.
        """
        if not self.context.get("thread"):
            return None
        endorsement = obj.get("endorsement")
        return endorsement["time"] if endorsement else None

    def get_children(self, obj):
        return [
            CommentSerializer(child, context=self.context).data
            for child in obj.get("children", [])
        ]

    def to_representation(self, data):
        # pylint: disable=arguments-differ
        data = super().to_representation(data)

        # Django Rest Framework v3 no longer includes None values
        # in the representation.  To maintain the previous behavior,
        # we do this manually instead.
        if 'parent_id' not in data:
            data["parent_id"] = None

        return data

    def get_abuse_flagged_any_user(self, obj):
        """
        Returns a boolean indicating whether any user has flagged the
        content as abusive.
        """
        if _validate_privileged_access(self.context):
            return len(obj.get("abuse_flaggers", [])) > 0

    def get_profile_image(self, obj):
        request = self.context["request"]
        return get_profile_images(request.user.profile, request.user, request)

    def validate(self, attrs):
        """
        Ensure that parent_id identifies a comment that is actually in the
        thread identified by thread_id and does not violate the configured
        maximum depth.
        """
        parent = None
        parent_id = attrs.get("parent_id")
        if parent_id:
            try:
                parent = Comment(id=parent_id).retrieve()
            except CommentClientRequestError:
                pass
            if not (parent and parent["thread_id"] == attrs["thread_id"]):
                raise ValidationError(
                    "parent_id does not identify a comment in the thread identified by thread_id."
                )
        if is_comment_too_deep(parent):
            raise ValidationError("Comment level is too deep.")
        return attrs

    def create(self, validated_data):
        comment = Comment(
            course_id=self.context["thread"]["course_id"],
            user_id=self.context["cc_requester"]["id"],
            **validated_data
        )
        comment.save()
        return comment

    def update(self, instance, validated_data):
        for key, val in validated_data.items():
            instance[key] = val
            # TODO: The comments service doesn't populate the endorsement
            # field on comment creation, so we only provide
            # endorsement_user_id on update
            requesting_user_id = self.context["cc_requester"]["id"]
            if key == "endorsed":
                track_forum_response_mark_event(self.context['request'], self.context['course'], instance, val)
                instance["endorsement_user_id"] = requesting_user_id
            if key == "body" and val:
                instance["editing_user_id"] = requesting_user_id
                track_comment_edited_event(self.context['request'], self.context['course'],
                                           instance, validated_data.get('edit_reason_code'))

        instance.save()
        return instance


class DiscussionTopicSerializer(serializers.Serializer):
    """
    Serializer for DiscussionTopic
    """
    id = serializers.CharField(read_only=True)  # pylint: disable=invalid-name
    name = serializers.CharField(read_only=True)
    thread_list_url = serializers.CharField(read_only=True)
    children = serializers.SerializerMethodField()
    thread_counts = serializers.DictField(read_only=True)

    def get_children(self, obj):
        """
        Returns a list of children of DiscussionTopicSerializer type
        """
        if not obj.children:
            return []
        return [DiscussionTopicSerializer(child).data for child in obj.children]

    def create(self, validated_data):
        """
        Overriden create abstract method
        """

    def update(self, instance, validated_data):
        """
        Overriden update abstract method
        """


class DiscussionTopicSerializerV2(serializers.Serializer):
    """
    Serializer for new style topics.
    """
    id = serializers.CharField(  # pylint: disable=invalid-name
        read_only=True,
        source="external_id",
        help_text="Provider-specific unique id for the topic"
    )
    usage_key = serializers.CharField(
        read_only=True,
        help_text="Usage context for the topic",
    )
    name = serializers.CharField(
        read_only=True,
        source="title",
        help_text="Topic name",
    )
    thread_counts = serializers.SerializerMethodField(
        read_only=True,
        help_text="Mapping of thread counts by type of thread",
    )
    enabled_in_context = serializers.BooleanField(
        read_only=True,
        help_text="Whether this topic is enabled in its context",
    )

    def get_thread_counts(self, obj: DiscussionTopicLink) -> Dict[str, int]:  # noqa: UP006
        """
        Get thread counts from provided context
        """
        return self.context['thread_counts'].get(obj.external_id, {
            "discussion": 0,
            "question": 0,
        })


class DiscussionRolesSerializer(serializers.Serializer):
    """
    Serializer for course discussion roles.
    """

    ACTION_CHOICES = (
        ('allow', 'allow'),
        ('revoke', 'revoke')
    )
    action = serializers.ChoiceField(ACTION_CHOICES)
    user_id = serializers.CharField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = None

    def validate_user_id(self, user_id):
        """
        Validate user id
        Args:
            user_id (str): username or email

        Returns:
            str: user id if valid
        """
        try:
            self.user = get_user_by_username_or_email(user_id)
            return user_id
        except User.DoesNotExist as err:
            raise ValidationError(f"'{user_id}' is not a valid student identifier") from err

    def validate(self, attrs):
        """Validate the data at an object level."""

        # Store the user object to avoid fetching it again.
        if hasattr(self, 'user'):
            attrs['user'] = self.user
        return attrs

    def create(self, validated_data):
        """
        Overriden create abstract method
        """

    def update(self, instance, validated_data):
        """
        Overriden update abstract method
        """


class DiscussionRolesMemberSerializer(serializers.Serializer):
    """
    Serializer for course discussion roles member data.
    """
    username = serializers.CharField()
    email = serializers.EmailField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    group_name = serializers.SerializerMethodField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.course_discussion_settings = self.context['course_discussion_settings']

    def get_group_name(self, instance):
        """Return the group name of the user."""
        group_id = get_group_id_for_user(instance, self.course_discussion_settings)
        group_name = get_group_name(group_id, self.course_discussion_settings)
        return group_name

    def create(self, validated_data):
        """
        Overriden create abstract method
        """

    def update(self, instance, validated_data):
        """
        Overriden update abstract method
        """


class DiscussionRolesListSerializer(serializers.Serializer):
    """
    Serializer for course discussion roles member list.
    """
    course_id = serializers.CharField()
    results = serializers.SerializerMethodField()
    division_scheme = serializers.SerializerMethodField()

    def get_results(self, obj):
        """Return the nested serializer data representing a list of member users."""
        context = {
            'course_id': obj['course_id'],
            'course_discussion_settings': self.context['course_discussion_settings']
        }
        serializer = DiscussionRolesMemberSerializer(obj['users'], context=context, many=True)
        return serializer.data

    def get_division_scheme(self, obj):  # pylint: disable=unused-argument
        """Return the division scheme for the course."""
        return self.context['course_discussion_settings'].division_scheme

    def create(self, validated_data):
        """
        Overridden create abstract method
        """

    def update(self, instance, validated_data):
        """
        Overridden update abstract method
        """


class UserStatsSerializer(serializers.Serializer):
    """
    Serializer for course user stats.
    """
    threads = serializers.IntegerField()
    replies = serializers.IntegerField()
    responses = serializers.IntegerField()
    active_flags = serializers.IntegerField()
    inactive_flags = serializers.IntegerField()
    username = serializers.CharField()

    def to_representation(self, instance):
        """Remove flag counts if user is not privileged."""
        data = super().to_representation(instance)
        if not self.context.get("is_privileged", False):
            data["active_flags"] = None
            data["inactive_flags"] = None
        return data


class BlackoutDateSerializer(serializers.Serializer):
    """
    Serializer for blackout dates.
    """
    start = serializers.DateTimeField(help_text="The ISO 8601 timestamp for the start of the blackout period")
    end = serializers.DateTimeField(help_text="The ISO 8601 timestamp for the end of the blackout period")


class ReasonCodeSeralizer(serializers.Serializer):
    """
    Serializer for reason codes.
    """
    code = serializers.CharField(help_text="A code for the an edit or close reason")
    label = serializers.CharField(help_text="A user-friendly name text for the close or edit reason")


class CourseMetadataSerailizer(serializers.Serializer):
    """
    Serializer for course metadata.
    """
    id = CourseKeyField(help_text="The identifier of the course")
    blackouts = serializers.ListField(
        child=BlackoutDateSerializer(),
        help_text="A list of objects representing blackout periods "
                  "(during which discussions are read-only except for privileged users)."
    )
    thread_list_url = serializers.URLField(
        help_text="The URL of the list of all threads in the course.",
    )
    following_thread_list_url = serializers.URLField(
        help_text="thread_list_url with parameter following=True",
    )
    topics_url = serializers.URLField(help_text="The URL of the topic listing for the course.")
    allow_anonymous = serializers.BooleanField(
        help_text="A boolean indicating whether anonymous posts are allowed or not.",
    )
    allow_anonymous_to_peers = serializers.BooleanField(
        help_text="A boolean indicating whether posts anonymous to peers are allowed or not.",
    )
    user_roles = serializers.ListField(
        child=serializers.CharField(),
        help_text="A list of all the roles the requesting user has for this course.",
    )
    user_is_privileged = serializers.BooleanField(
        help_text="A boolean indicating if the current user has a privileged role",
    )
    provider = serializers.CharField(
        help_text="The discussion provider used by this course",
    )
    enable_in_context = serializers.BooleanField(
        help_text="A boolean indicating whether in-context discussion is enabled for the course",
    )
    group_at_subsection = serializers.BooleanField(
        help_text="A boolean indicating whether discussions should be grouped at subsection",
    )
    post_close_reasons = serializers.ListField(
        child=ReasonCodeSeralizer(),
        help_text="A list of reasons that can be specified by moderators for closing a post",
    )
    edit_reasons = serializers.ListField(
        child=ReasonCodeSeralizer(),
        help_text="A list of reasons that can be specified by moderators for editing a post, response, or comment",
    )
