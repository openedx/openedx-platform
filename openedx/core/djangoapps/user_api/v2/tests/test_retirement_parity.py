"""Behaviour parity between the version 1 and version 2 retirement pipeline addresses."""

import contextlib
import json
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from social_django.models import UserSocialAuth
from wiki.models import ArticleRevision
from wiki.models.pluginbase import RevisionPluginRevision

from common.djangoapps.student.models import (
    AccountRecovery,
    CourseEnrollmentAllowed,
    ManualEnrollmentAudit,
    PendingEmailChange,
    PendingNameChange,
    PendingSecondaryEmailChange,
    SocialLink,
    UserProfile,
)
from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest
from openedx.core.djangoapps.course_groups.models import UnregisteredLearnerCohortAssignments
from openedx.core.djangoapps.credit.models import CreditRequest, CreditRequirementStatus
from openedx.core.djangoapps.user_api.accounts import views as legacy_views
from openedx.core.djangoapps.user_api.accounts.serializers import UserRetirementStatusSerializer
from openedx.core.djangoapps.user_api.accounts.signals import (
    USER_RETIRE_LMS_CRITICAL,
    USER_RETIRE_LMS_MISC,
    USER_RETIRE_MAILINGS,
)
from openedx.core.djangoapps.user_api.accounts.tests.retirement_helpers import (  # pylint: disable=unused-import
    create_retirement_status,
    setup_retirement_states,  # noqa: F401
)
from openedx.core.djangoapps.user_api.accounts.views import (
    USER_PROFILE_PII,
    AccountRetirementView,
    LMSAccountRetirementView,
)
from openedx.core.djangoapps.user_api.models import RetirementState, UserOrgTag, UserRetirementStatus
from openedx.core.djangoapps.user_api.v2 import view_services
from openedx.core.djangoapps.user_api.v2.views import (
    RetirementCleanupView,
    RetirementPartnerReportCleanupView,
    RetirementPartnerReportViewSet,
    RetirementViewSet,
)
from openedx.core.djangolib.testing.utils import skip_unless_lms

PASSWORD = "Password1234"
WORKER_USERNAME = "parity-retirement-worker"

#: The step of the pipeline at which the erasures are run.
WORKING_STATE = "RETIRING_LMS"

#: Each step of the erasure of the account's own data, named by what it clears
#: and by where both versions reach it. ``None`` as the holder means each
#: version calls the step through its own module, so both are recorded.
CORE_STEPS = (
    ("profile", AccountRetirementView, "clear_pii_from_userprofile"),
    ("profile_images", AccountRetirementView, "delete_users_profile_images"),
    ("country_cache", AccountRetirementView, "delete_users_country_cache"),
    ("certificate_names", AccountRetirementView, "clear_pii_from_certificate_records"),
    ("entitlement_notes", AccountRetirementView, "retire_entitlement_support_detail"),
    ("pending_email_change", PendingEmailChange, "delete_by_user_value"),
    ("organization_tags", UserOrgTag, "delete_by_user_value"),
    ("pending_secondary_email", PendingSecondaryEmailChange, "redact_and_delete_pending_secondary_email"),
    ("recovery_email", AccountRecovery, "retire_recovery_email"),
    ("enrollment_invitations", CourseEnrollmentAllowed, "delete_by_user_value"),
    ("cohort_assignments", UnregisteredLearnerCohortAssignments, "delete_by_user_value"),
)

#: Each step of the erasure of what the features around the account hold.
MISC_STEPS = (
    ("wiki_plugin_revisions", RevisionPluginRevision, "retire_user"),
    ("wiki_article_revisions", ArticleRevision, "retire_user"),
    ("pending_name_change", PendingNameChange, "delete_by_user_value"),
    ("manual_enrolments", ManualEnrollmentAudit, "retire_manual_enrollments"),
    ("credit_requests", CreditRequest, "retire_user"),
    ("api_access_requests", ApiAccessRequest, "retire_user"),
    ("credit_requirements", CreditRequirementStatus, "retire_user"),
)

#: The step each version reaches through its own module rather than through a
#: shared object, so it is patched in both.
HISTORICAL_SOCIAL_AUTH_STEP = ("historical_social_auth", "redact_and_delete_historical_social_auth")

#: The declared differences between a version 1 retirement record and a
#: version 2 one. Each entry is a (property, reason) pair, and the comparison
#: must find all of them and nothing else.
RECORD_DIFFERENCES = (
    ("current_state", "the step is the name of the step rather than an object carrying its order and its id"),
    ("last_state", "the step is the name of the step rather than an object carrying its order and its id"),
)

#: Each view of this version with the view whose operations it takes over, for
#: the media types a body may be sent in. The disposals are part of the
#: earlier version's two pipeline views and are compared against those.
MEDIA_TYPE_PAIRS = (
    (RetirementViewSet, legacy_views.AccountRetirementStatusView),
    (RetirementCleanupView, legacy_views.AccountRetirementStatusView),
    (RetirementPartnerReportViewSet, legacy_views.AccountRetirementPartnerReportView),
    (RetirementPartnerReportCleanupView, legacy_views.AccountRetirementPartnerReportView),
)


@pytest.mark.usefixtures("setup_retirement_states")
@override_settings(RETIREMENT_SERVICE_WORKER_USERNAME=WORKER_USERNAME)
class RetirementParityTestCase(APITestCase):
    """The caller both versions of the pipeline's addresses admit."""

    def setUp(self):
        super().setUp()
        self.worker = UserFactory.create(username=WORKER_USERNAME, password=PASSWORD)
        assert self.client.login(username=WORKER_USERNAME, password=PASSWORD)

    def retirement_for(self, user):
        """Return a retirement of this account, standing where the erasures run."""
        return create_retirement_status(user, state=RetirementState.objects.get(state_name=WORKING_STATE))


@skip_unless_lms
class ErasureStepParityTests(RetirementParityTestCase):
    """Both versions clear the same things in the same order."""

    def steps_taken(self, steps, call, own_module_step=None):
        """Return the steps a call takes, in the order it takes them."""
        taken = []

        def record(label):
            def step(*args, **kwargs):  # pylint: disable=unused-argument
                taken.append(label)
            return step

        with contextlib.ExitStack() as patches:
            for label, holder, attribute in steps:
                patches.enter_context(
                    mock.patch.object(holder, attribute, side_effect=record(label)),
                )
            if own_module_step is not None:
                label, attribute = own_module_step
                for module in (legacy_views, view_services):
                    patches.enter_context(
                        mock.patch.object(module, attribute, side_effect=record(label)),
                    )
            response = call()
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
        return taken

    def legacy_erasure(self, address_name, user):
        """Run a version 1 erasure of this account."""
        return lambda: self.client.post(
            reverse(address_name), {"username": user.username}, format="json",
        )

    def conforming_erasure(self, scope, user):
        """Run a version 2 erasure of this account."""
        return lambda: self.client.post(
            reverse("user_v2:retirement_erasure", kwargs={"username": user.username}),
            {"scope": scope},
            format="json",
        )

    def test_the_core_scope_takes_the_same_steps_in_the_same_order(self):
        legacy_user = UserFactory.create()
        self.retirement_for(legacy_user)
        conforming_user = UserFactory.create()
        self.retirement_for(conforming_user)

        legacy = self.steps_taken(CORE_STEPS, self.legacy_erasure("accounts_retire", legacy_user))
        conforming = self.steps_taken(CORE_STEPS, self.conforming_erasure("lms_core", conforming_user))

        assert legacy == [label for label, _holder, _attribute in CORE_STEPS]
        assert conforming == legacy

    def test_the_miscellaneous_scope_takes_the_same_steps_in_the_same_order(self):
        legacy_user = UserFactory.create()
        self.retirement_for(legacy_user)
        conforming_user = UserFactory.create()
        self.retirement_for(conforming_user)

        legacy = self.steps_taken(
            MISC_STEPS,
            self.legacy_erasure("accounts_retire_misc", legacy_user),
            own_module_step=HISTORICAL_SOCIAL_AUTH_STEP,
        )
        conforming = self.steps_taken(
            MISC_STEPS,
            self.conforming_erasure("lms_misc", conforming_user),
            own_module_step=HISTORICAL_SOCIAL_AUTH_STEP,
        )

        assert legacy == [
            *[label for label, _holder, _attribute in MISC_STEPS],
            HISTORICAL_SOCIAL_AUTH_STEP[0],
        ]
        assert conforming == legacy

    def test_neither_scope_takes_a_step_belonging_to_the_other(self):
        core_user = UserFactory.create()
        self.retirement_for(core_user)
        misc_user = UserFactory.create()
        self.retirement_for(misc_user)

        assert not self.steps_taken(MISC_STEPS, self.conforming_erasure("lms_core", core_user))
        assert not self.steps_taken(CORE_STEPS, self.conforming_erasure("lms_misc", misc_user))


@skip_unless_lms
class ErasureStateParityTests(RetirementParityTestCase):
    """Both versions leave the stored data in the same state."""

    def furnished_account(self, name):
        """Return an account carrying one of everything the erasures clear."""
        user = UserFactory.create(username=name, email=f"{name}@example.com", first_name="A", last_name="B")
        user.profile.name = "Learner Name"
        user.profile.city = "Somewhere"
        user.profile.bio = "A biography."
        user.profile.year_of_birth = 1990
        user.profile.save()
        SocialLink.objects.create(
            user_profile=user.profile, platform="facebook", social_link="https://facebook.com/x",
        )
        PendingEmailChange.objects.create(
            user=user, new_email=f"new-{name}@example.com", activation_key=f"email-{name}",
        )
        UserOrgTag.objects.create(user=user, key="foo", value="bar", org="edX")
        PendingSecondaryEmailChange.objects.create(
            user=user, new_secondary_email=f"second-{name}@example.com", activation_key=f"second-{name}",
        )
        AccountRecovery.objects.create(
            user=user, secondary_email=f"recovery-{name}@example.com", is_active=True,
        )
        PendingNameChange.objects.create(user=user, new_name="Another Name", rationale="because")
        UserSocialAuth.objects.create(user=user, provider="google-oauth2", uid=f"{name}@example.com")
        cache.set(UserProfile.country_cache_key_name(user.id), "Timor-leste")
        retirement = self.retirement_for(user)
        CourseEnrollmentAllowed.objects.create(
            email=retirement.original_email, course_id="course-v1:edX+DemoX+Demo",
        )
        return user, retirement

    def stored_state(self, user, retirement):
        """Return what is stored about this account, described without naming the account."""
        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)
        return {
            "username_is_the_hashed_one": user.username == retirement.retired_username,
            "email_is_the_hashed_one": user.email == retirement.retired_email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_active": user.is_active,
            "profile": {field: getattr(profile, field) for field in USER_PROFILE_PII},
            "social_links": profile.social_links.count(),
            "pending_email_changes": PendingEmailChange.objects.filter(user=user).count(),
            "organization_tags": UserOrgTag.objects.filter(user=user).count(),
            "pending_secondary_email_changes": PendingSecondaryEmailChange.objects.filter(user=user).count(),
            "recovery_emails": AccountRecovery.objects.filter(user=user).count(),
            "enrollment_invitations": CourseEnrollmentAllowed.objects.filter(
                email=retirement.original_email,
            ).count(),
            "cohort_assignments": UnregisteredLearnerCohortAssignments.objects.filter(
                email=retirement.original_email,
            ).count(),
            "country_cache": cache.get(UserProfile.country_cache_key_name(user.id)),
            "pending_name_changes": PendingNameChange.objects.filter(user=user).count(),
            "social_auths": UserSocialAuth.objects.filter(user=user).count(),
            "historical_social_auths": UserSocialAuth.history.model.objects.filter(user_id=user.id).count(),
        }

    def test_the_core_scope_leaves_the_same_state_behind(self):
        legacy_user, legacy_retirement = self.furnished_account("parity-core-legacy")
        conforming_user, conforming_retirement = self.furnished_account("parity-core-conforming")

        assert self.client.post(
            reverse("accounts_retire"), {"username": legacy_user.username}, format="json",
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.post(
            reverse("user_v2:retirement_erasure", kwargs={"username": conforming_user.username}),
            {"scope": "lms_core"},
            format="json",
        ).status_code == status.HTTP_204_NO_CONTENT

        legacy_state = self.stored_state(legacy_user, legacy_retirement)
        assert legacy_state == self.stored_state(conforming_user, conforming_retirement)
        # The comparison is only worth anything if the erasure really ran.
        assert legacy_state["username_is_the_hashed_one"] is True
        assert legacy_state["is_active"] is False
        assert legacy_state["profile"]["name"] == ""
        assert legacy_state["social_links"] == 0
        assert legacy_state["pending_email_changes"] == 0
        assert legacy_state["organization_tags"] == 0
        assert legacy_state["recovery_emails"] == 0
        assert legacy_state["enrollment_invitations"] == 0
        assert legacy_state["country_cache"] is None

    def test_the_miscellaneous_scope_leaves_the_same_state_behind(self):
        legacy_user, legacy_retirement = self.furnished_account("parity-misc-legacy")
        conforming_user, conforming_retirement = self.furnished_account("parity-misc-conforming")

        assert self.client.post(
            reverse("accounts_retire_misc"), {"username": legacy_user.username}, format="json",
        ).status_code == status.HTTP_204_NO_CONTENT
        assert self.client.post(
            reverse("user_v2:retirement_erasure", kwargs={"username": conforming_user.username}),
            {"scope": "lms_misc"},
            format="json",
        ).status_code == status.HTTP_204_NO_CONTENT

        legacy_state = self.stored_state(legacy_user, legacy_retirement)
        assert legacy_state == self.stored_state(conforming_user, conforming_retirement)
        assert legacy_state["pending_name_changes"] == 0
        assert legacy_state["historical_social_auths"] == 0
        # This scope clears the history of a sign-in method, not the method itself.
        assert legacy_state["social_auths"] == 1
        # The account itself is left alone by this scope.
        assert legacy_state["username_is_the_hashed_one"] is False
        assert legacy_state["is_active"] is True


@skip_unless_lms
class ErasureSignalParityTests(RetirementParityTestCase):
    """Both versions announce the same things to the same listeners."""

    def announcements(self, signal, call, user, retirement):
        """Return what a call announced through this signal, described without naming the account."""
        heard = []

        def listen(sender, **kwargs):
            kwargs.pop("signal", None)
            described = {"sender": sender}
            for key, value in sorted(kwargs.items()):
                if getattr(value, "pk", None) == user.pk:
                    described[key] = "<the account>"
                elif value == retirement.retired_username:
                    described[key] = "<the hashed name>"
                elif value == retirement.retired_email:
                    described[key] = "<the hashed address>"
                elif value == retirement.original_email:
                    described[key] = "<the registered address>"
                else:
                    described[key] = value
            heard.append(described)

        signal.connect(listen)
        try:
            response = call()
        finally:
            signal.disconnect(listen)
        assert response.status_code == status.HTTP_204_NO_CONTENT, response.content
        return heard

    def test_the_core_scope_announces_the_same_thing(self):
        legacy_user = UserFactory.create()
        legacy_retirement = self.retirement_for(legacy_user)
        conforming_user = UserFactory.create()
        conforming_retirement = self.retirement_for(conforming_user)

        legacy = self.announcements(
            USER_RETIRE_LMS_CRITICAL,
            lambda: self.client.post(
                reverse("accounts_retire"), {"username": legacy_user.username}, format="json",
            ),
            legacy_user,
            legacy_retirement,
        )
        conforming = self.announcements(
            USER_RETIRE_LMS_CRITICAL,
            lambda: self.client.post(
                reverse("user_v2:retirement_erasure", kwargs={"username": conforming_user.username}),
                {"scope": "lms_core"},
                format="json",
            ),
            conforming_user,
            conforming_retirement,
        )

        assert legacy == [{
            "sender": AccountRetirementView,
            "retired_email": "<the hashed address>",
            "retired_username": "<the hashed name>",
            "user": "<the account>",
        }]
        assert conforming == legacy

    def test_the_miscellaneous_scope_announces_the_same_two_things(self):
        legacy_user = UserFactory.create()
        legacy_retirement = self.retirement_for(legacy_user)
        conforming_user = UserFactory.create()
        conforming_retirement = self.retirement_for(conforming_user)

        for signal, expected in (
            (USER_RETIRE_LMS_MISC, [{"sender": LMSAccountRetirementView, "user": "<the account>"}]),
            (
                USER_RETIRE_MAILINGS,
                [{
                    "sender": LMSAccountRetirementView,
                    "email": "<the registered address>",
                    "new_email": "<the hashed address>",
                    "user": "<the account>",
                }],
            ),
        ):
            with self.subTest(signal=signal):
                legacy = self.announcements(
                    signal,
                    lambda: self.client.post(
                        reverse("accounts_retire_misc"),
                        {"username": legacy_user.username},
                        format="json",
                    ),
                    legacy_user,
                    legacy_retirement,
                )
                conforming = self.announcements(
                    signal,
                    lambda: self.client.post(
                        reverse(
                            "user_v2:retirement_erasure",
                            kwargs={"username": conforming_user.username},
                        ),
                        {"scope": "lms_misc"},
                        format="json",
                    ),
                    conforming_user,
                    conforming_retirement,
                )
                assert legacy == expected
                assert conforming == legacy


@skip_unless_lms
class StateMachineParityTests(RetirementParityTestCase):
    """The moves the pipeline allows and refuses are the same on both versions."""

    #: Each case is the step a retirement stands at, the step asked for,
    #: whether the move is forced, and whether each version carries it out.
    MOVES = (
        ("LOCKING_ACCOUNT", "PENDING", False, False),
        ("LOCKING_ACCOUNT", "PENDING", True, True),
        ("COMPLETE", "PENDING", False, False),
        ("COMPLETE", "PENDING", True, True),
        ("LOCKING_ACCOUNT", "LOCKING_ACCOUNT", False, False),
        ("LOCKING_ACCOUNT", "LOCKING_ACCOUNT", True, True),
    )

    def ask_for_the_move(self, address, body, legacy):
        """Ask one of the two versions for a move, and return the status it answered with."""
        if legacy:
            return self.client.patch(reverse("accounts_retirement_update"), body, format="json")
        return self.client.patch(address, body, format="json")

    def test_both_versions_carry_out_and_refuse_the_same_moves(self):
        for standing_at, asked_for, force, moves in self.MOVES:
            with self.subTest(standing_at=standing_at, asked_for=asked_for, force=force):
                legacy_retirement = self.retirement_for(UserFactory.create())
                conforming_retirement = self.retirement_for(UserFactory.create())
                for retirement in (legacy_retirement, conforming_retirement):
                    retirement.current_state = RetirementState.objects.get(state_name=standing_at)
                    retirement.save()

                body = {"new_state": asked_for, "response": "foo"}
                if force:
                    body["force"] = True

                legacy = self.client.patch(
                    reverse("accounts_retirement_update"),
                    {**body, "username": legacy_retirement.original_username},
                    format="json",
                )
                conforming = self.client.patch(
                    reverse(
                        "user_v2:retirement_detail",
                        kwargs={"username": conforming_retirement.original_username},
                    ),
                    body,
                    format="json",
                )

                if moves:
                    assert legacy.status_code == status.HTTP_204_NO_CONTENT
                    assert conforming.status_code == status.HTTP_204_NO_CONTENT
                else:
                    assert legacy.status_code == status.HTTP_400_BAD_REQUEST
                    assert conforming.status_code == status.HTTP_409_CONFLICT

                legacy_retirement.refresh_from_db()
                conforming_retirement.refresh_from_db()
                assert (
                    conforming_retirement.current_state.state_name
                    == legacy_retirement.current_state.state_name
                )
                assert (
                    conforming_retirement.last_state.state_name
                    == legacy_retirement.last_state.state_name
                )


@skip_unless_lms
class RetirementRecordParityTests(RetirementParityTestCase):
    """A version 2 retirement record carries the values a version 1 one carries."""

    def setUp(self):
        super().setUp()
        self.retirement = self.retirement_for(
            UserFactory.create(username="parity-subject", email="parity-subject@example.com"),
        )

    def test_the_record_carries_the_same_values_bar_the_declared_differences(self):
        legacy = UserRetirementStatusSerializer(instance=self.retirement).data
        conforming = json.loads(
            self.client.get(
                reverse("user_v2:retirement_detail", kwargs={"username": "parity-subject"}),
            ).content,
        )

        assert set(conforming) == set(legacy)
        changed = sorted(
            name for name in legacy
            if json.loads(json.dumps(legacy[name], default=str)) != conforming[name]
        )
        assert changed == sorted(name for name, _reason in RECORD_DIFFERENCES)

    def test_the_declared_differences_are_the_step_names(self):
        conforming = self.client.get(
            reverse("user_v2:retirement_detail", kwargs={"username": "parity-subject"}),
        ).data
        legacy = UserRetirementStatusSerializer(instance=self.retirement).data
        assert conforming["current_state"] == legacy["current_state"]["state_name"] == WORKING_STATE
        assert conforming["last_state"] == legacy["last_state"]["state_name"] == WORKING_STATE

    def test_neither_version_carries_the_log_of_the_moves_a_retirement_has_made(self):
        conforming = self.client.get(
            reverse("user_v2:retirement_detail", kwargs={"username": "parity-subject"}),
        ).data
        assert "responses" not in conforming
        assert "responses" not in UserRetirementStatusSerializer(instance=self.retirement).data
        assert UserRetirementStatus.objects.get(id=self.retirement.id).responses != ""


class MediaTypeParityTests(SimpleTestCase):
    """Each operation reads a body in the media types the operation it replaces reads."""

    def test_each_view_reads_what_the_view_it_takes_over_from_reads(self):
        for conforming, legacy in MEDIA_TYPE_PAIRS:
            with self.subTest(view=conforming.__name__):
                assert tuple(conforming.parser_classes) == tuple(legacy.parser_classes)

    def test_the_cancellation_is_the_one_operation_whose_view_narrowed_nothing(self):
        """It reads no body on either version, so there is nothing for a media type to decide."""
        assert "parser_classes" not in vars(legacy_views.CancelAccountRetirementStatusView)
