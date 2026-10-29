"""Serializers for version 2 of the User API."""


from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.urls import reverse
from drf_spectacular.utils import extend_schema_field
from edx_rest_framework_extensions.errors import ErrorResponseSerializer
from rest_framework import serializers

from lms.djangoapps.verify_student.models import ManualVerification, SoftwareSecurePhotoVerification
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_authn.views.registration_form import contains_html, contains_url

from .paginators import UserApiPagination

#: Value of the ``view`` query parameter that embeds the owning user in each preference.
FULL_VIEW = "full"

#: Value of the ``view`` query parameter that reduces a user to its identifying fields.
MINIMAL_VIEW = "minimal"

#: Properties the ``minimal`` preset keeps of a directory entry.
USER_MINIMAL_FIELDS = ("id", "username", "email")

#: Properties the ``minimal`` preset keeps of a stored preference.
PREFERENCE_MINIMAL_FIELDS = ("username", "key", "value")


class ErrorResponse(ErrorResponseSerializer):  # pylint: disable=abstract-method
    """
    The body of every unsuccessful response.

    ``type`` is a URI naming the kind of failure, ``title`` names it in one
    line, ``status`` repeats the HTTP status code and ``detail`` explains what
    happened; ``errors`` lists the per-field messages of a rejected parameter.
    """


class RetiredAccountErrorResponse(ErrorResponse):  # pylint: disable=abstract-method
    """
    The body of a refusal that also reports what is left of a retired account.

    The two members below are carried only when the account was selected by an
    email address it held before being retired; every other refusal at this
    status carries the standard members alone.
    """

    can_cancel_retirement = serializers.BooleanField(
        required=False,
        help_text=(
            "Whether the retirement can still be reversed, which it can only while it is inside "
            "the cooling-off period."
        ),
    )
    retirement_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text=(
            "Identifies the retirement to a caller able to reverse it, or null when there is "
            "nothing left to reverse."
        ),
    )


class _KeptFieldsMixin:
    """
    Describes an object reduced to the properties named in ``kept_fields``.

    The response presets reduce an already serialized object, so a preset's
    shape has a describing serializer of its own rather than a serializing one.
    """

    #: Names of the properties the preset returns.
    kept_fields = ()

    def get_fields(self):
        """Return the declared fields narrowed to the ones the preset returns."""
        fields = super().get_fields()
        return {name: field for name, field in fields.items() if name in self.kept_fields}


class _OptionalFieldsMixin:
    """
    Describes an object whose every property is optional.

    A caller that selects properties with ``fields`` decides which ones come
    back, so none of them is guaranteed to be present.
    """

    def get_fields(self):
        """Return the declared fields, none of them mandatory."""
        fields = super().get_fields()
        for field in fields.values():
            field.required = False
            field.read_only = False
        return fields


def full_view_requested(request):
    """Return True when the caller asked for the embedded-user response preset."""
    if request is None:
        return False
    params = getattr(request, "query_params", None)
    if params is None:
        params = getattr(request, "GET", {})
    return params.get("view") == FULL_VIEW


class UserDirectorySerializer(serializers.ModelSerializer):
    """A user of the platform directory, addressed by username."""

    id = serializers.IntegerField(
        read_only=True,
        help_text="Database identifier of the user. Addresses in this API use the username instead.",
    )
    url = serializers.HyperlinkedIdentityField(
        view_name="user_v2:user_detail",
        lookup_field="username",
        help_text="Absolute URL of this user's directory entry.",
    )
    email = serializers.EmailField(read_only=True, help_text="Registered email address of the user.")
    username = serializers.CharField(read_only=True, help_text="Unique name the user is addressed by.")
    name = serializers.SerializerMethodField(
        help_text="Full name recorded on the user's profile, or null when the user has no profile.",
    )
    preferences = serializers.SerializerMethodField(
        help_text="The user's stored preferences, as a mapping of preference key to value.",
    )

    class Meta:
        model = User
        fields = ("id", "url", "email", "name", "username", "preferences")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_name(self, user):
        """Return the profile name, or None for a user who has no profile row."""
        profile = getattr(user, "profile", None)
        return profile.name if profile is not None else None

    @extend_schema_field(serializers.DictField(child=serializers.CharField()))
    def get_preferences(self, user):
        """Return the user's preferences as a key-to-value mapping."""
        return UserPreference.get_all_preferences(user)


class UserDirectoryMinimalSerializer(_KeptFieldsMixin, UserDirectorySerializer):
    """A directory entry reduced to the properties that identify the user."""

    kept_fields = USER_MINIMAL_FIELDS


class UserDirectoryProjectionSerializer(_OptionalFieldsMixin, UserDirectorySerializer):
    """A directory entry reduced to the properties the caller selected."""


class UserPreferenceRecordSerializer(serializers.ModelSerializer):
    """
    A stored preference, identified by the user that owns it and its key.

    ``user`` carries the owner's whole directory entry and is included only
    when the caller asks for the ``full`` response preset; by default the
    owner appears as the ``username`` string alone.
    """

    url = serializers.SerializerMethodField(help_text="Absolute URL of this preference.")
    username = serializers.CharField(
        source="user.username",
        read_only=True,
        help_text="Username of the user this preference belongs to.",
    )
    key = serializers.CharField(read_only=True, help_text="Name of the preference.")
    value = serializers.CharField(read_only=True, help_text="Stored value of the preference.")
    user = serializers.SerializerMethodField(
        help_text="Directory entry of the owning user; null unless the full response preset is asked for.",
    )

    class Meta:
        model = UserPreference
        fields = ("username", "key", "value", "url", "user")
        read_only_fields = fields

    @extend_schema_field(serializers.URLField())
    def get_url(self, preference):
        """Return the member address of this preference."""
        path = reverse(
            "user_v2:user_preference_detail",
            kwargs={"username": preference.user.username, "key": preference.key},
        )
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request is not None else path

    @extend_schema_field(UserDirectorySerializer(allow_null=True))
    def get_user(self, preference):
        """Embed the owner only when the caller asked for the full preset."""
        if not full_view_requested(self.context.get("request")):
            return None
        return UserDirectorySerializer(preference.user, context=self.context).data


class UserPreferenceRecordMinimalSerializer(_KeptFieldsMixin, UserPreferenceRecordSerializer):
    """A stored preference reduced to its owner, its name and its value."""

    kept_fields = PREFERENCE_MINIMAL_FIELDS


class UserPreferenceRecordProjectionSerializer(_OptionalFieldsMixin, UserPreferenceRecordSerializer):
    """A stored preference reduced to the properties the caller selected."""


class TimeZoneSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A time zone and the way it is displayed to a learner."""

    time_zone = serializers.CharField(help_text="Name of the time zone, for example ``US/Pacific``.")
    description = serializers.CharField(
        help_text="Display form of the time zone, for example ``US/Pacific (PST, UTC-0800)``.",
    )


class TimeZoneQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters accepted by the time-zone list."""

    country_code = serializers.RegexField(
        r"^[A-Za-z]{2}$",
        required=False,
        # An empty value narrows nothing, the way an empty value of any other
        # parameter does, and is accepted rather than held to the pattern.
        allow_blank=True,
        error_messages={
            "invalid": "Enter a two-letter ISO 3166-1 alpha-2 country code, for example FR.",
        },
        help_text=(
            "Two-letter ISO 3166-1 alpha-2 country code, case-insensitive. "
            "Every time zone is returned when it is omitted or left empty, and when "
            "the code names a country with no time zones of its own."
        ),
    )


#: Value of the ``view`` query parameter that returns only the fields the
#: account's owner shares with everybody.
SHARED_VIEW = "shared"

#: Properties the ``minimal`` preset keeps of an account.
ACCOUNT_MINIMAL_FIELDS = ("username", "email", "name", "profile_image")

#: Properties the ``ordering`` parameter of the account list accepts.
ACCOUNT_SORT_FIELDS = ("username",)

#: The order the account list uses when it is asked for none. Usernames are
#: unique, so sorting by one settles every row: the same selection asked for
#: twice is cut into the same pages.
ACCOUNT_DEFAULT_SORT = ACCOUNT_SORT_FIELDS[0]


class AccountProfileImageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Where a user's profile image can be fetched, in each size the site renders."""

    has_image = serializers.BooleanField(
        help_text="Whether the user has uploaded an image. The URLs below are a default image when it is false.",
    )
    image_url_full = serializers.URLField(required=False, help_text="Address of the image at its largest size.")
    image_url_large = serializers.URLField(required=False, help_text="Address of the image at its large size.")
    image_url_medium = serializers.URLField(required=False, help_text="Address of the image at its medium size.")
    image_url_small = serializers.URLField(required=False, help_text="Address of the image at its smallest size.")


class AccountLanguageSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A language the user has told the site they speak."""

    code = serializers.CharField(help_text="Language code, for example ``en`` or ``pt-br``.")


class AccountSocialLinkSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A link to one of the user's profiles on another site."""

    platform = serializers.CharField(help_text="Name of the site, for example ``facebook`` or ``linkedin``.")
    social_link = serializers.CharField(
        allow_blank=True, help_text="Address of the user's profile on that site.",
    )


class AccountExtendedFieldSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """One of the extra profile fields this site collects."""

    field_name = serializers.CharField(help_text="Name the site configured the field under.")
    field_value = serializers.JSONField(
        help_text=(
            "Value the user gave, in the shape this site stores it in: a string for a field "
            "collected as text, and whatever the field holds otherwise. It is an empty string "
            "when the field is unanswered and null where the site stores no value at all."
        ),
    )


class AccountSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    A user's account and profile.

    Which properties a response carries depends on who asks: a user reading
    their own account, and a staff caller reading anyone's, receive every
    property below; anyone else receives the ones the account's owner shares,
    which is why all but ``username``, ``profile_image`` and ``account_privacy``
    are marked optional here.
    """

    username = serializers.CharField(help_text="Unique name the user is addressed by.")
    profile_image = AccountProfileImageSerializer(
        allow_null=True,
        help_text="Where the user's profile image can be fetched, null for an account without a profile.",
    )
    account_privacy = serializers.CharField(
        help_text=(
            "How much of this account other learners may see: `all_users`, `private`, or `custom` "
            "when the owner chose field by field."
        ),
    )
    email = serializers.EmailField(required=False, help_text="Address the site writes to the user at.")
    id = serializers.IntegerField(
        required=False,
        help_text="Numeric identifier of the user. Addresses in this API use the username instead.",
    )
    name = serializers.CharField(
        required=False, allow_null=True, help_text="Full name the user gave, as it appears on certificates.",
    )
    bio = serializers.CharField(required=False, allow_null=True, help_text="Short self-description.")
    country = serializers.CharField(
        required=False, allow_null=True, help_text="Two-letter ISO 3166-1 alpha-2 code of the user's country.",
    )
    state = serializers.CharField(
        required=False, allow_null=True, help_text="Code of the state or territory, recorded for the United States.",
    )
    date_joined = serializers.DateTimeField(required=False, help_text="When the account was created.")
    last_login = serializers.DateTimeField(
        required=False, allow_null=True, help_text="When the user last signed in, null if they never have.",
    )
    is_active = serializers.BooleanField(
        required=False, help_text="Whether the user has confirmed their email address.",
    )
    gender = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Gender the user selected: `m`, `f`, `o`, or null when unanswered.",
    )
    year_of_birth = serializers.IntegerField(
        required=False, allow_null=True, help_text="Year the user was born, null when unanswered.",
    )
    level_of_education = serializers.CharField(
        required=False,
        allow_null=True,
        help_text=(
            "Highest level of education the user selected: `p` for a doctorate, `m` for a "
            "master's or professional degree, `b` for a bachelor's degree, `a` for an associate "
            "degree, `hs` for secondary or high school, `jhs` for junior secondary or middle "
            "school, `el` for elementary or primary school, `none` for no formal education, or "
            "`other`. Null when unanswered, and an account registered long ago may carry a code "
            "the site no longer offers."
        ),
    )
    goals = serializers.CharField(
        required=False, allow_null=True, help_text="What the user said they want from the site.",
    )
    mailing_address = serializers.CharField(required=False, allow_null=True, help_text="Postal address the user gave.")
    phone_number = serializers.CharField(
        required=False, allow_null=True, help_text="Digits, optionally led by a `+`, or null when unanswered.",
    )
    language_proficiencies = AccountLanguageSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text=(
            "Languages the user speaks, ordered by language code, null for an account without a profile."
        ),
    )
    social_links = AccountSocialLinkSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text=(
            "The user's profiles on other sites, ordered by platform, null for an account without a profile."
        ),
    )
    extended_profile = AccountExtendedFieldSerializer(
        many=True,
        required=False,
        allow_null=True,
        help_text=(
            "The extra profile fields this site collects, in configured order, null for an "
            "account without a profile."
        ),
    )
    requires_parental_consent = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text=(
            "Whether the user is too young to share a profile without a guardian's consent, null "
            "for an account without a profile."
        ),
    )
    pending_name_change = serializers.CharField(
        required=False, allow_null=True, help_text="Name the user has asked to change to, null when none is pending.",
    )
    verified_name = serializers.CharField(
        required=False, allow_null=True, help_text="Name verified against the user's photo identification.",
    )
    secondary_email = serializers.EmailField(
        required=False, allow_null=True, help_text="Recovery address, present only where the site collects one.",
    )
    secondary_email_enabled = serializers.BooleanField(
        required=False,
        allow_null=True,
        help_text="Whether this site collects a recovery address, null where it collects none.",
    )
    course_certificates = serializers.DictField(
        required=False,
        allow_null=True,
        help_text="Always null here; certificates are served by the certificates API.",
    )
    time_zone = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Always null here; the time-zone preference is served by the preferences API.",
    )


class AccountMinimalSerializer(_KeptFieldsMixin, AccountSerializer):
    """An account reduced to the properties that identify its owner."""

    kept_fields = ACCOUNT_MINIMAL_FIELDS


class AccountProjectionSerializer(_OptionalFieldsMixin, AccountSerializer):
    """An account reduced to the properties the caller selected."""


class AccountQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters accepted by the account list."""

    username = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=(
            "Comma-separated usernames. A name no account holds is left out of the page rather than refused. "
            "Omit this and the other selectors to receive your own account."
        ),
    )
    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text="Address of the one account to return. Only staff may select an account this way.",
    )
    lms_user_id = serializers.IntegerField(
        required=False,
        help_text="Numeric identifier of the one account to return. Only staff may select an account this way.",
    )
    view = serializers.ChoiceField(
        choices=[
            (MINIMAL_VIEW, "The properties that identify the account's owner"),
            (SHARED_VIEW, "The properties the owner shares with everybody"),
        ],
        required=False,
        allow_blank=True,
        help_text=(
            "Response preset. `minimal` reduces each account to the properties that identify its owner; "
            "`shared` returns only the properties the owner shares with everybody, even to a caller who "
            "may see more. Omit it, or send it empty, to receive everything the caller may see."
        ),
    )
    ordering = serializers.ChoiceField(
        choices=[
            *((field, f"{field}, ascending") for field in ACCOUNT_SORT_FIELDS),
            *((f"-{field}", f"{field}, descending") for field in ACCOUNT_SORT_FIELDS),
        ],
        required=False,
        allow_blank=True,
        default=ACCOUNT_DEFAULT_SORT,
        error_messages={
            "invalid_choice": (
                f"Sort by any of: {', '.join(ACCOUNT_SORT_FIELDS)}, each optionally prefixed with '-'."
            ),
        },
        help_text=(
            "Property to sort the page by, prefixed with `-` to sort descending. Omit it, or "
            f"send it empty, to sort by ascending `{ACCOUNT_DEFAULT_SORT}`. Usernames are unique, "
            "so a selection asked for twice is cut into the same pages."
        ),
    )


class _TextField(serializers.CharField):
    """
    Text that is only taken as text.

    The base field spells out a number or a decimal as the digits that write
    it, so a property sent as ``5`` arrives as ``"5"`` and is stored. Here a
    value of any other type is refused, naming the property that carried it.
    """

    def to_internal_value(self, data):
        """Return the text sent, or refuse a value that is not text."""
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class AccountUpdateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    The properties of an account a user may change.

    The request body is a merge patch, so every property is optional and only
    the ones present are changed; sending null clears a property that allows it.
    An empty text is taken for each property that accepts one, because what it
    means there -- clearing the property, or a value the property cannot hold
    -- is settled where the change is carried out.
    """

    email = serializers.EmailField(
        required=False,
        help_text="New address. The change takes effect only once the user confirms it from a message sent there.",
    )
    secondary_email = serializers.EmailField(
        required=False, help_text="New recovery address, confirmed the same way as the primary one.",
    )
    name = _TextField(
        required=False,
        allow_blank=True,
        help_text="Full name. A change may need identity verification before it takes effect.",
    )
    bio = _TextField(
        required=False, allow_blank=True, help_text="Short self-description. Send an empty text to clear it.",
    )
    country = _TextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Two-letter ISO 3166-1 alpha-2 code of the user's country.",
    )
    state = _TextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="Code of the state or territory, recorded for the United States.",
    )
    city = _TextField(required=False, allow_null=True, allow_blank=True, help_text="City the user lives in.")
    gender = _TextField(
        required=False, allow_null=True, allow_blank=True, help_text="Gender the user selects: `m`, `f` or `o`.",
    )
    year_of_birth = serializers.IntegerField(required=False, allow_null=True, help_text="Year the user was born.")
    level_of_education = _TextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text=(
            "Highest level of education: `p` for a doctorate, `m` for a master's or professional "
            "degree, `b` for a bachelor's degree, `a` for an associate degree, `hs` for secondary "
            "or high school, `jhs` for junior secondary or middle school, `el` for elementary or "
            "primary school, `none` for no formal education, or `other`."
        ),
    )
    goals = _TextField(
        required=False, allow_null=True, allow_blank=True, help_text="What the user wants from the site.",
    )
    mailing_address = _TextField(
        required=False, allow_null=True, allow_blank=True, help_text="Postal address.",
    )
    phone_number = _TextField(
        required=False,
        allow_blank=True,
        help_text="Digits, optionally led by a `+`. Send an empty text to clear it.",
    )
    language_proficiencies = AccountLanguageSerializer(
        many=True, required=False, help_text="The whole list of languages the user speaks, replacing the stored one.",
    )
    social_links = AccountSocialLinkSerializer(
        many=True,
        required=False,
        help_text=(
            "Links to add or replace, merged into the stored list by platform. "
            "Send an empty address to drop a platform."
        ),
    )
    extended_profile = AccountExtendedFieldSerializer(
        many=True, required=False, help_text="Values for the extra profile fields this site collects.",
    )
    account_privacy = _TextField(
        required=False,
        allow_null=True,
        allow_blank=True,
        help_text="How much of the account other learners may see: `all_users`, `private` or `custom`.",
    )


class _TextListField(serializers.ListField):
    """
    A list of text whose refusal is one message naming the entries at fault.

    The base field reports an entry it will not take under the position that
    entry holds, which reaches a caller as a mapping of positions nested
    inside the member naming the list, where nothing can display it. The
    refusal here names those positions in a sentence instead.
    """

    def to_internal_value(self, data):
        """Return the entries sent, refusing the whole list when one of them is not text."""
        try:
            return super().to_internal_value(data)
        except serializers.ValidationError as refusal:
            if not isinstance(refusal.detail, dict):
                raise
            positions = ", ".join(str(position) for position in sorted(refusal.detail))
            raise serializers.ValidationError(
                f"Every entry must be text, and the entries at these positions are not: {positions}.",
            ) from refusal


#: Most addresses one search may name. A search answers with at most one
#: account per address, so holding the request to the largest page a caller
#: may ask for keeps the answer within a page a caller may read.
EMAIL_SEARCH_MAX_ADDRESSES = UserApiPagination.max_page_size


class EmailSearchSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """The addresses an account search is asked to resolve."""

    emails = _TextListField(
        child=serializers.CharField(allow_blank=True),
        allow_empty=True,
        max_length=EMAIL_SEARCH_MAX_ADDRESSES,
        help_text=(
            f"Email addresses to look up, at most {EMAIL_SEARCH_MAX_ADDRESSES} of them. An "
            "address no account holds is left out of the results."
        ),
    )


class EmailSearchResultSerializer(serializers.ModelSerializer):
    """The account that holds one of the addresses searched for."""

    email = serializers.EmailField(read_only=True, help_text="Registered email address of the user.")
    id = serializers.IntegerField(read_only=True, help_text="Numeric identifier of the user.")
    username = serializers.CharField(read_only=True, help_text="Unique name the user is addressed by.")

    class Meta:
        model = User
        fields = ("email", "id", "username")
        read_only_fields = fields


#: Longest name the column holding a pending change can store.
NAME_MAX_LENGTH = 255


class NameChangeSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """A request to put a different full name on a user's profile."""

    name = serializers.CharField(
        max_length=NAME_MAX_LENGTH,
        help_text="Full name to put on the profile once the request is confirmed.",
    )

    def validate_name(self, name):
        """Refuse a name carrying markup or a web address."""
        if contains_html(name):
            raise serializers.ValidationError("Name cannot contain the following characters: < >")
        if contains_url(name):
            raise serializers.ValidationError("Name cannot contain a URL")
        return name


#: Property the verification list is sorted by, and the order it uses when
#: asked for none: the most recently changed attempt first.
VERIFICATION_SORT_FIELD = "updated_at"
VERIFICATION_DEFAULT_SORT = f"-{VERIFICATION_SORT_FIELD}"

#: Shape of the day a summary reports an accepted proof as running out on:
#: ``MM/DD/YYYY``, or empty while the day is not reported at all.
EXPIRATION_DAY_PATTERN = r"^(\d{2}/\d{2}/\d{4})?$"


class VerificationStatusSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Where a user stands on having proved their identity."""

    is_verified = serializers.BooleanField(
        help_text="Whether an accepted proof of identity is on record and has not expired.",
    )
    status = serializers.CharField(
        help_text=(
            "`none` until a proof has been sent in, `approved` once one has been accepted, "
            "`pending` while one is being looked at, `must_reverify` once one has been turned "
            "down, and `expired` once an accepted proof has run out."
        ),
    )
    expiration_datetime = serializers.RegexField(
        EXPIRATION_DAY_PATTERN,
        help_text=(
            "Calendar day the accepted proof runs out, written as `MM/DD/YYYY`: the day alone, "
            "where the attempt that carries the proof records the moment. It is empty until that "
            "day is near enough for the user to be asked to prove their identity again."
        ),
    )


class VerificationSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """
    One attempt a user has made at proving their identity.

    An attempt is stored by the service that carried out the check, in a table
    of its own per kind of check, so the kind, what was said about the attempt
    and the identifier it was filed under are read from the record rather than
    held on it.
    """

    type = serializers.SerializerMethodField(
        help_text=(
            "How the identity was checked: `Software Secure` for a photo check, `Manual` for one "
            "recorded by hand, and `SSO` for one recorded when the user signed in elsewhere, "
            "which is also what a check carried out by an installed verification service is "
            "reported as."
        ),
    )
    status = serializers.CharField(
        help_text=(
            "Where this attempt stands: `created` before it has been started, `ready` and "
            "`submitted` while a photo check is on its way, `must_retry` once sending one on has "
            "failed, `pending` while a check carried out elsewhere is being looked at, `approved` "
            "once the attempt has been accepted and `denied` once it has been turned down."
        ),
    )
    expiration_datetime = serializers.DateTimeField(
        allow_null=True,
        help_text=(
            "Moment this attempt stops counting as proof, to the second. It is null for an "
            "attempt that never expires."
        ),
    )
    message = serializers.SerializerMethodField(
        help_text="Why the attempt was turned down, or the reason it was accepted by hand. Empty otherwise.",
    )
    updated_at = serializers.DateTimeField(help_text="When this attempt last changed.")
    receipt_id = serializers.SerializerMethodField(
        help_text="Identifier the photo verification service filed this attempt under, null for other kinds.",
    )

    def get_type(self, obj) -> str:
        """Return how the identity was checked."""
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return "Software Secure"
        if isinstance(obj, ManualVerification):
            return "Manual"
        return "SSO"

    def get_message(self, obj) -> str:
        """Return what was said about this attempt: why it was turned down, or why it was accepted by hand."""
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return obj.error_msg
        if isinstance(obj, ManualVerification):
            return obj.reason
        return ""

    def get_receipt_id(self, obj) -> str | None:
        """Return the identifier the photo verification service filed this attempt under."""
        if isinstance(obj, SoftwareSecurePhotoVerification):
            return obj.receipt_id
        return None


class VerificationQuerySerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Query parameters accepted by the verification list."""

    ordering = serializers.ChoiceField(
        choices=[
            (VERIFICATION_SORT_FIELD, "Least recently changed attempt first"),
            (VERIFICATION_DEFAULT_SORT, "Most recently changed attempt first"),
        ],
        required=False,
        allow_blank=True,
        default=VERIFICATION_DEFAULT_SORT,
        error_messages={
            "invalid_choice": (
                f"Sort by any of: {VERIFICATION_SORT_FIELD}, each optionally prefixed with '-'."
            ),
        },
        help_text=(
            "Property to sort the page by, prefixed with `-` to sort descending. Omit it, or send "
            f"it empty, to sort by `{VERIFICATION_DEFAULT_SORT}`. Attempts changed at the same "
            "moment are ordered by the receipt identifier, so a page is always in the same order."
        ),
    )
