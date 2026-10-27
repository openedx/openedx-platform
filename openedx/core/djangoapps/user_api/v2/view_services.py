"""Operations version 2 of the User API performs on the account retirement pipeline."""

import logging

from rest_framework.exceptions import NotFound
from wiki.models import ArticleRevision
from wiki.models.pluginbase import RevisionPluginRevision

from common.djangoapps.student.models import (
    AccountRecovery,
    CourseEnrollmentAllowed,
    ManualEnrollmentAudit,
    PendingEmailChange,
    PendingNameChange,
    PendingSecondaryEmailChange,
    get_retired_email_by_email,
    get_retired_username_by_username,
)
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest
from openedx.core.djangoapps.course_groups.models import UnregisteredLearnerCohortAssignments
from openedx.core.djangoapps.credit.models import CreditRequest, CreditRequirementStatus
from openedx.core.djangoapps.user_api.accounts.signals import (
    USER_RETIRE_LMS_CRITICAL,
    USER_RETIRE_LMS_MISC,
    USER_RETIRE_MAILINGS,
)
from openedx.core.djangoapps.user_api.accounts.utils import (
    handle_retirement_cancellation,
    redact_and_delete_historical_social_auth,
)
from openedx.core.djangoapps.user_api.accounts.views import (
    AccountRetirementPartnerReportView,
    AccountRetirementView,
    LMSAccountRetirementView,
)
from openedx.core.djangoapps.user_api.models import (
    RetirementStateError,
    UserOrgTag,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)

from .errors import AmbiguousAccountName, RetirementStateConflict
from .selectors import partner_reports_requested_as, retirements_requested_as
from .serializers import (
    ERASURE_CORE_SCOPE,
    ERASURE_MISC_SCOPE,
    OUTCOME_ALREADY_ABSENT,
    OUTCOME_DELETED,
)

log = logging.getLogger(__name__)

#: The step a retirement stands at for as long as the learner may still change
#: their mind, which is the only step a retirement can be cancelled from.
PENDING_STATE = "PENDING"

#: The step a retirement stands at once every other step has run, which is the
#: only step its record is deleted from.
COMPLETE_STATE = "COMPLETE"

#: What is written over a retirement record's copies of the account's name and
#: address before the record is deleted, so that a downstream system treating
#: the delete as a soft one holds the replacement rather than the data.
REDACTED_VALUE = "redacted-before-delete"
REDACTED_EMAIL = "redacted-before-delete@safe.com"

#: What a caller is told about a failure. The specific reason is logged with
#: the records it names rather than returned, because it is written for whoever
#: operates the pipeline and not for whoever calls it.
NO_SUCH_RETIREMENT = "No retirement was requested under that name."
NOT_OPEN_TO_ACTION = "This retirement does not stand at a step of the pipeline that admits this operation."
CANNOT_MOVE_TO_STEP = "This retirement cannot be moved to that step from the step it stands at."
ONLY_PENDING_IS_CANCELLED = (
    f"Only a retirement still at {PENDING_STATE} can be cancelled, and this one has moved past it."
)
BATCH_LEFT_ALONE = "Some of the records named are not in a state this operation acts on, so none of them was changed."
NAME_IS_AMBIGUOUS = "More than one record was requested under that name, so nothing was done."
SEVERAL_RECORDS_HOLD_THIS_NAME = (
    "More than one record was requested under this name, and acting on one of them would act on an "
    "account this request did not name."
)


def _only_record_named(username, records):
    """
    Return the one record requested under this name, or refuse the name.

    Every operation of this pipeline that names one account resolves it here,
    so one name addresses the same record whichever operation is asked for.
    The name a record keeps is not unique, so a name several records were
    requested under is refused: one of them belongs to an account the caller
    did not name, and nothing says which.
    """
    if not records:
        log.info("No record is requested under the name %r.", username)
        raise NotFound(NO_SUCH_RETIREMENT)
    if len(records) > 1:
        log.warning(
            "The name %r was refused: %s records are requested under it, %r.",
            username, len(records), sorted(record.id for record in records),
        )
        raise AmbiguousAccountName(NAME_IS_AMBIGUOUS)
    return records[0]


def retirement_requested_as(records, username):
    """Return the one record of this selection requested under exactly this name."""
    return _only_record_named(
        username, retirements_requested_as(records, [username]).get(username, []),
    )


def retirement_open_to_action(records, username):
    """
    Return the retirement requested under this name while it admits pipeline work.

    A retirement admits work only between the step that waits for the learner
    and the step that records a part of the pipeline as finished, which is what
    keeps a step from being run twice over the same account.
    """
    retirement = retirement_requested_as(records, username)
    try:
        # Which steps admit work is decided by the pipeline's own model layer,
        # where the order of the steps is kept, rather than being written out a
        # second time here. It resolves the name as well, and the name resolved
        # above addresses one record, so the two agree on which record that is.
        UserRetirementStatus.get_retirement_for_retirement_action(username)
    except UserRetirementStatus.DoesNotExist as exc:
        log.info("No retirement is requested under the name %r: %s", username, exc)
        raise NotFound(NO_SUCH_RETIREMENT) from exc
    except RetirementStateError as exc:
        log.info("The retirement requested as %r admits no pipeline work: %s", username, exc)
        raise RetirementStateConflict(NOT_OPEN_TO_ACTION) from exc
    return retirement


def move_retirement(retirement, new_state, response, force):
    """Record that this retirement now stands at another step of the pipeline."""
    try:
        retirement.update_state({
            "username": retirement.original_username,
            "new_state": new_state,
            "response": response,
            "force": force,
        })
    except RetirementStateError as exc:
        log.info("Retirement %s was not moved to %r: %s", retirement.id, new_state, exc)
        raise RetirementStateConflict(CANNOT_MOVE_TO_STEP) from exc


def cancel_retirement(retirement):
    """
    Undo this retirement request, restoring the account it was made for.

    The record is deleted, which is what also removes the permanent record of
    the request, and the account is returned to the address it was registered
    under with a password nobody holds, so its owner signs in again by asking
    for a new one.
    """
    standing_at = retirement.current_state.state_name
    if standing_at != PENDING_STATE:
        log.info("Retirement %s stands at %s, so it was not cancelled.", retirement.id, standing_at)
        raise RetirementStateConflict(ONLY_PENDING_IS_CANCELLED)
    handle_retirement_cancellation(retirement)


def _erase_core_data(retirement):
    """Erase what the platform itself holds about the account being retired."""
    user = retirement.user
    retired_username = retirement.retired_username or get_retired_username_by_username(user.username)
    retired_email = retirement.retired_email or get_retired_email_by_email(user.email)
    original_email = retirement.original_email

    AccountRetirementView.clear_pii_from_userprofile(user)
    AccountRetirementView.delete_users_profile_images(user)
    AccountRetirementView.delete_users_country_cache(user)
    AccountRetirementView.clear_pii_from_certificate_records(user)
    AccountRetirementView.retire_entitlement_support_detail(user)

    PendingEmailChange.delete_by_user_value(user, field="user")
    UserOrgTag.delete_by_user_value(user, field="user")
    PendingSecondaryEmailChange.redact_and_delete_pending_secondary_email(user.id)
    AccountRecovery.retire_recovery_email(user.id)

    CourseEnrollmentAllowed.delete_by_user_value(original_email, field="email")
    UnregisteredLearnerCohortAssignments.delete_by_user_value(original_email, field="email")

    USER_RETIRE_LMS_CRITICAL.send(
        sender=AccountRetirementView,
        user=user,
        retired_username=retired_username,
        retired_email=retired_email,
    )

    user.first_name = ""
    user.last_name = ""
    user.is_active = False
    user.username = retired_username
    user.email = retired_email
    user.save()


def _erase_misc_data(retirement):
    """Erase what the features built on top of the platform hold about the account being retired."""
    user = retirement.user
    RevisionPluginRevision.retire_user(user)
    ArticleRevision.retire_user(user)
    PendingNameChange.delete_by_user_value(user, field="user")
    ManualEnrollmentAudit.retire_manual_enrollments(user, retirement.retired_email)

    CreditRequest.retire_user(retirement)
    ApiAccessRequest.retire_user(user)
    CreditRequirementStatus.retire_user(retirement)
    redact_and_delete_historical_social_auth(user.id)

    USER_RETIRE_LMS_MISC.send(sender=LMSAccountRetirementView, user=user)
    USER_RETIRE_MAILINGS.send(
        sender=LMSAccountRetirementView,
        email=retirement.original_email,
        new_email=retirement.retired_email,
        user=user,
    )


#: What each scope erases. The steps of a scope run in the order they are
#: written, and each scope announces what it has done to whatever else holds
#: data about the account. The sender named is the one the pipeline has always
#: announced these steps under, because a listener may select on it.
_ERASURES = {
    ERASURE_CORE_SCOPE: _erase_core_data,
    ERASURE_MISC_SCOPE: _erase_misc_data,
}


def erase_account_data(retirement, scope):
    """
    Erase one set of the retiring account's personal data.

    The steps are not wrapped in a transaction: a part of the pipeline that
    fails halfway is recovered by the driver recording the retirement as
    errored and running the step again, and a rollback would undo erasures
    that had already succeeded.
    """
    try:
        _ERASURES[scope](retirement)
    except Exception:
        log.exception("Erasing the %s data of retirement %s failed partway through.", scope, retirement.id)
        raise


def record_partner_report(retirement):
    """Queue this retirement for reporting to the organizations the account studied with."""
    # pylint: disable=protected-access
    organizations = AccountRetirementPartnerReportView._get_orgs_for_user(retirement.user)
    if not organizations:
        return
    UserRetirementPartnerReportingStatus.objects.get_or_create(
        user=retirement.user,
        defaults={
            "original_username": retirement.original_username,
            "original_email": retirement.original_email,
            "original_name": retirement.original_name,
        },
    )


def _partner_report_of(report):
    """Return what a partner needs in order to forget the account this queue entry names."""
    # pylint: disable=protected-access
    organizations = AccountRetirementPartnerReportView._get_orgs_for_user(report.user)
    return {
        "user_id": report.user.pk,
        "original_username": report.original_username,
        AccountRetirementPartnerReportView.ORIGINAL_EMAIL_KEY: report.original_email,
        AccountRetirementPartnerReportView.ORIGINAL_NAME_KEY: report.original_name,
        "orgs": sorted(organizations),
        "created": report.created,
    }


def claim_partner_reports(waiting, limit=None):
    """
    Take the partner reports waiting to be processed, and return them.

    The entries returned are exactly the entries taken: they are read first
    and then marked by the identifiers that were read, so an entry added while
    the answer is being built is left for the next caller rather than marked as
    taken by one that never saw it. Nothing releases an entry once it is
    taken.
    """
    if limit is not None:
        waiting = waiting[:limit]
    taken = list(waiting)
    reports = [_partner_report_of(report) for report in taken]
    UserRetirementPartnerReportingStatus.objects.filter(
        id__in=[report.id for report in taken],
    ).update(is_being_processed=True)
    return reports


def _batch_outcomes(usernames, deleted):
    """Return what was done about each name given, in the order the names were given."""
    return [
        {
            "username": username,
            "outcome": OUTCOME_DELETED if username in deleted else OUTCOME_ALREADY_ABSENT,
        }
        for username in usernames
    ]


def _refuse_batch(refused):
    """Refuse a whole batch, naming the records at fault and why."""
    log.info("A batch was left alone because of %r.", sorted(refused))
    raise RetirementStateConflict(BATCH_LEFT_ALONE, errors=refused)


def _one_record_for_each_name(found):
    """
    Return the one record each name carries, refusing the batch over any name carrying several.

    A batch is acted on whole or not at all, and a name several records were
    requested under names an account the caller did not, so the batch is
    refused and nothing is deleted.
    """
    ambiguous = {
        username: [SEVERAL_RECORDS_HOLD_THIS_NAME]
        for username, records in found.items()
        if len(records) > 1
    }
    if ambiguous:
        log.warning("A batch was left alone because these names are ambiguous: %r.", sorted(ambiguous))
        raise AmbiguousAccountName(NAME_IS_AMBIGUOUS, errors=ambiguous)
    return {username: records[0] for username, records in found.items()}


def delete_retirements(records, usernames):
    """
    Delete the retirement records of the accounts named, once the pipeline has finished with them.

    The records are resolved from the selection given, so a record outside it
    is reported as absent and left alone. A name no record was requested under
    is reported as such rather than refused, so a caller repeating a request
    that has already been carried out is answered rather than left to tell
    "already done" from "never existed".
    A record the pipeline has not finished with refuses the whole batch and
    nothing is deleted, because the caller asked for a set of records and a
    half-applied delete cannot be undone.
    """
    found = _one_record_for_each_name(retirements_requested_as(records, usernames))
    refused = {
        username: [
            f"This retirement stands at {record.current_state.state_name}; only a retirement at "
            f"{COMPLETE_STATE} is deleted here.",
        ]
        for username, record in found.items()
        if record.current_state.state_name != COMPLETE_STATE
    }
    if refused:
        _refuse_batch(refused)
    doomed = [record.id for record in found.values()]
    if doomed:
        # The record's own copies of the name, the address and the full name are
        # written over before it goes, so that a system syncing these rows and
        # treating a delete as a soft one is left holding the replacement.
        UserRetirementStatus.objects.filter(id__in=doomed).update(
            original_username=REDACTED_VALUE,
            original_email=REDACTED_EMAIL,
            original_name=REDACTED_VALUE,
        )
        UserRetirementStatus.objects.filter(id__in=doomed).delete()
    return _batch_outcomes(usernames, found)


def delete_partner_reports(entries, usernames):
    """
    Delete the partner report entries of the accounts named, once they have been reported on.

    The entries are resolved from the selection given, so an entry outside it
    is reported as absent and left alone. An entry nobody has taken for
    processing has not been reported on yet, so it refuses the whole batch and
    nothing is deleted; a name with no entry at all is reported as such, which
    is what a repeat of a request already carried out reports for every name.
    """
    found = _one_record_for_each_name(partner_reports_requested_as(entries, usernames))
    refused = {
        username: ["This partner report has not been taken for processing, so it has not been reported on yet."]
        for username, report in found.items()
        if not report.is_being_processed
    }
    if refused:
        _refuse_batch(refused)
    doomed = [report.id for report in found.values()]
    if doomed:
        UserRetirementPartnerReportingStatus.objects.filter(id__in=doomed).delete()
    return _batch_outcomes(usernames, found)
