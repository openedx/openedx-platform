"""Operations version 2 of the User API performs on the account retirement pipeline."""

import logging

from django.apps import apps
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.contrib.sites.models import Site
from django.db import transaction
from edx_ace import ace
from edx_ace.recipient import Recipient
from rest_framework.exceptions import APIException, NotFound
from wiki.models import ArticleRevision
from wiki.models.pluginbase import RevisionPluginRevision

from common.djangoapps.student.models import (
    AccountRecovery,
    CourseEnrollmentAllowed,
    LoginFailures,
    ManualEnrollmentAudit,
    PendingEmailChange,
    PendingNameChange,
    PendingSecondaryEmailChange,
    get_retired_email_by_email,
    get_retired_username_by_username,
)
from openedx.core.djangoapps.ace_common.template_context import get_base_template_context
from openedx.core.djangoapps.api_admin.models import ApiAccessRequest
from openedx.core.djangoapps.course_groups.models import UnregisteredLearnerCohortAssignments
from openedx.core.djangoapps.credit.models import CreditRequest, CreditRequirementStatus
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from openedx.core.djangoapps.user_api.accounts.signals import (
    USER_RETIRE_LMS_CRITICAL,
    USER_RETIRE_LMS_MISC,
    USER_RETIRE_MAILINGS,
)
from openedx.core.djangoapps.user_api.accounts.utils import (
    create_retirement_request_and_deactivate_account,
    get_orgs_for_user,
    handle_retirement_cancellation,
    redact_and_delete_historical_social_auth,
    username_suffix_generator,
)
from openedx.core.djangoapps.user_api.accounts.views import AccountRetirementView, LMSAccountRetirementView
from openedx.core.djangoapps.user_api.message_types import DeletionNotificationMessage
from openedx.core.djangoapps.user_api.models import (
    RetirementStateError,
    UserOrgTag,
    UserRetirementPartnerReportingStatus,
    UserRetirementStatus,
)

from .errors import (
    AccountTemporarilyLocked,
    AmbiguousAccountName,
    PasswordNotConfirmed,
    RetirementStateConflict,
)
from .selectors import partner_reports_requested_as, retirements_requested_as
from .serializers import (
    ERASURE_CORE_SCOPE,
    ERASURE_MISC_SCOPE,
    OUTCOME_ALREADY_ABSENT,
    OUTCOME_DELETED,
    REPLACEMENT_NO_SUCH_ACCOUNT,
    REPLACEMENT_REPLACED,
)

log = logging.getLogger(__name__)

#: The step a retirement stands at for as long as the learner may still change
#: their mind, which is the only step a retirement can be cancelled from.
PENDING_STATE = "PENDING"

#: The step a retirement stands at once every other step has run, which is the
#: only step its record is deleted from.
COMPLETE_STATE = "COMPLETE"

#: Written over a record's copies of name and address before it is deleted.
REDACTED_VALUE = "redacted-before-delete"
REDACTED_EMAIL = "redacted-before-delete@safe.com"

#: What a caller is told about a failure; the specific reason is logged instead.
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
PASSWORD_NOT_CONFIRMED = "The password sent is not the password of the account making the request."
ACCOUNT_TEMPORARILY_LOCKED = (
    "This account is closed to password attempts for a while, after several were made with a "
    "password it does not hold. Try again later."
)
RETIREMENT_ALREADY_REQUESTED = (
    "The retirement of this account has already been requested, and it stands where the pipeline "
    "has left it."
)
RETIREMENT_COULD_NOT_BE_REQUESTED = "The retirement could not be requested. Nothing was changed."
RETIREMENT_WAS_NOT_RECORDED = (
    "The retirement could not be recorded in full, so nothing of it was kept. The request may be "
    "sent again."
)
NAMES_WERE_NOT_REPLACED = "The names could not be replaced. Nothing was changed."


def confirm_the_callers_password(request, password):
    """
    Confirm that the password sent is the one the account making the request holds.

    Attempts are counted the way they are counted when signing in, and an
    account that has had several refused is closed to further ones for a
    while, so trying passwords here is no cheaper than trying them there. A
    confirmed password clears that count, as signing in does.

    The value is read and compared and never written down: no log line and no
    answer carries it.
    """
    user = request.user
    counted = LoginFailures.is_feature_enabled()
    if counted and LoginFailures.is_user_locked_out(user):
        log.info("A request from %r was refused: the account is closed to attempts.", user.username)
        raise AccountTemporarilyLocked(ACCOUNT_TEMPORARILY_LOCKED)
    if authenticate(username=user.username, password=password, request=request) is None:
        if counted:
            LoginFailures.increment_lockout_counter(user)
        log.info("A request from %r was refused: the password sent was not the account's.", user.username)
        raise PasswordNotConfirmed(PASSWORD_NOT_CONFIRMED)
    if counted:
        LoginFailures.clear_lockout_counter(user)


def request_retirement_of_own_account(user):
    """
    Record that this account's owner has asked for it to be closed, and tell them so.

    The account is the one given and no other: nothing here resolves a name,
    so the record written is the record of the account the request was made
    from.

    The address the account holds is read before the request is recorded,
    because recording it replaces that address with the one a retired account
    is stored under, and the message about the request has to reach the
    address its owner gave.
    """
    address_before_the_request = user.email
    try:
        create_retirement_request_and_deactivate_account(user)
    except RetirementStateError as exc:
        if UserRetirementStatus.objects.filter(user=user).exists():
            log.info("A retirement is already held for the account %r: %s", user.username, exc)
            raise RetirementStateConflict(RETIREMENT_ALREADY_REQUESTED) from exc
        log.error("No retirement could be requested for %r: %s", user.username, exc)
        raise APIException(RETIREMENT_COULD_NOT_BE_REQUESTED) from exc
    _tell_the_owner_their_account_is_closing(user, address_before_the_request)


def _tell_the_owner_their_account_is_closing(user, address):
    """
    Send the one message a request to close an account sends, to the address given.

    A message that cannot be sent leaves the request unrecorded rather than
    recorded in silence: the owner of an account being closed is told that it
    is, and the whole request is answered as this server's failure so that it
    can be sent again.
    """
    try:
        context = get_base_template_context(Site.objects.get_current())
        context.update({"full_name": user.profile.name})
        language = user.preferences.model.get_value(
            user, LANGUAGE_KEY, default=settings.LANGUAGE_CODE,
        )
        ace.send(DeletionNotificationMessage().personalize(
            recipient=Recipient(lms_user_id=0, email_address=address),
            language=language,
            user_context=context,
        ))
    except Exception as exc:
        log.exception("The message about the retirement requested for %r could not be sent.", user.username)
        raise APIException(RETIREMENT_WAS_NOT_RECORDED) from exc


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


def _admits_pipeline_work(retirement):
    """
    Say whether the step this retirement stands at admits pipeline work.

    A step the pipeline itself requires -- the step waiting for the learner
    and the steps a retirement ends at -- admits none, and neither does a step
    recording that a part of the pipeline has finished. This is the rule
    ``UserRetirementStatus.get_retirement_for_retirement_action`` applies to a
    name, applied here to the record already resolved, so that the record
    judged is the record the caller is handed to work on.
    """
    state = retirement.current_state
    return not (state.required or state.state_name.endswith("_COMPLETE"))


def retirement_open_to_action(records, username):
    """
    Return the retirement requested under this name while it admits pipeline work.

    A retirement admits work only between the step that waits for the learner
    and the step that records a part of the pipeline as finished, which is what
    keeps a step from being run twice over the same account.
    """
    retirement = retirement_requested_as(records, username)
    if not _admits_pipeline_work(retirement):
        log.info(
            "The retirement requested as %r stands at %s, which admits no pipeline work.",
            username, retirement.current_state.state_name,
        )
        raise RetirementStateConflict(NOT_OPEN_TO_ACTION)
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
    # The registered name, not the current one: a re-run would otherwise hash a hash.
    retired_username = retirement.retired_username or get_retired_username_by_username(
        retirement.original_username,
    )
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


#: What each scope erases, in order, and the signal it sends; the sender is the
#: v1 view, because a listener may select on it.
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
    organizations = get_orgs_for_user(retirement.user)
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
    organizations = get_orgs_for_user(report.user)
    return {
        "user_id": report.user.pk,
        "original_username": report.original_username,
        "original_email": report.original_email,
        "original_name": report.original_name,
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
        # Overwrite the record's PII first, for systems that treat a delete as soft.
        UserRetirementStatus.objects.filter(id__in=doomed).update(
            original_username=REDACTED_VALUE,
            original_email=REDACTED_EMAIL,
            original_name=REDACTED_VALUE,
        )
        # Re-check the step on delete: a record moved off it after the write was never
        # answered about.
        UserRetirementStatus.objects.filter(
            id__in=doomed, current_state__state_name=COMPLETE_STATE,
        ).delete()
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


#: Where the name an account is stored under is also kept, which is what has to
#: be rewritten for that name to be replaced everywhere it is read. The pairs
#: are written out here because the operation this one replaces holds them
#: inside its own handler, where nothing can read them from; a test resolves
#: every one of them, so a renamed column or an application a deployment does
#: not install is a failure of that test rather than a name replaced in some
#: places and not others.
#: Where an account's own name is kept, which is the one of the places below
#: that decides whether the name asked for was handed to an account. A row
#: anywhere else carries a copy of a name beside an account's work, and a copy
#: left behind by an account that is no longer stored under that name is not an
#: account to hand a new name to.
WHERE_AN_ACCOUNT_HOLDS_ITS_NAME = ("auth.user", "username")

NAMES_ARE_ALSO_KEPT_IN = (
    WHERE_AN_ACCOUNT_HOLDS_ITS_NAME,
    ("consent.DataSharingConsent", "username"),
    ("consent.HistoricalDataSharingConsent", "username"),
    ("credit.CreditEligibility", "username"),
    ("credit.CreditRequest", "username"),
    ("credit.CreditRequirementStatus", "username"),
    ("user_api.UserRetirementPartnerReportingStatus", "original_username"),
    ("user_api.UserRetirementStatus", "original_username"),
)


def _where_names_are_kept():
    """Return the model holding an account's name at each place one is kept, by that place."""
    try:
        return {
            (model, column): apps.get_model(model)
            for model, column in NAMES_ARE_ALSO_KEPT_IN
        }
    except LookupError as exc:
        log.exception("A model holding an account's name could not be read.")
        raise APIException(NAMES_WERE_NOT_REPLACED) from exc


def _name_nobody_is_stored_under(asked_for):
    """
    Return the name asked for, or that name with a suffix where an account holds it.

    The suffix is generated rather than counted up, so a generated name can be
    taken as well and the result is tried again until nobody holds it. A
    separator is put between the name asked for and what was generated, so
    that the two cannot be read as one word.
    """
    suffix_length = getattr(settings, "SOCIAL_AUTH_UUID_LENGTH", 4)
    candidate = asked_for
    while User.objects.filter(username=candidate).exists():
        candidate = f"{asked_for}-{username_suffix_generator(suffix_length)}"
    return candidate


def _rewrite_name(current_username, replacement, places):
    """
    Replace this name wherever it is kept, and return how many rows carried it at each place.

    One account's name is replaced in every place at once or in none of them,
    so that no account is left holding one name in one place and another
    elsewhere. The count is kept per place, because what an account is stored
    under is answered from the place that is the account and not from the
    number of rows a name was found in.
    """
    rewritten = {}
    with transaction.atomic():
        for place, model in places.items():
            _, column = place
            rewritten[place] = model.objects.filter(**{column: current_username}).update(
                **{column: replacement},
            )
    return rewritten


def replace_usernames(mappings):
    """
    Replace the name each account named is stored under, and report each mapping.

    A mapping is reported as replaced where an account is stored under the
    name answered for it afterwards, and as naming no account otherwise. That
    is read from the place an account's own name is kept rather than from how
    many rows carried the name given: a name kept beside the work of an
    account that is no longer stored under it is rewritten the way the
    operation this one replaces rewrites it, but it is not an account, and
    reporting it as replaced would have the caller carry a name no account
    holds to the other services it replaces names in.

    No name is handed out for a mapping that reached no account. The name
    asked for is worked out before the rewrite, as it is in the operation this
    one replaces, so that the work either does is the same; where no account
    was stored under the name given, the name worked out is simply not
    reported and no account holds it.

    """
    places = _where_names_are_kept()
    results = []
    for mapping in mappings:
        (current_username, asked_for), = mapping.items()
        replacement = _name_nobody_is_stored_under(asked_for)
        rewritten = _rewrite_name(current_username, replacement, places)
        handed_to_an_account = bool(rewritten[WHERE_AN_ACCOUNT_HOLDS_ITS_NAME])
        if handed_to_an_account:
            log.info(
                "The name of one account was replaced in %s places.",
                sum(rewritten.values()),
            )
        results.append({
            "current_username": current_username,
            "new_username": replacement if handed_to_an_account else None,
            "outcome": (
                REPLACEMENT_REPLACED if handed_to_an_account else REPLACEMENT_NO_SUCH_ACCOUNT
            ),
        })
    return results
