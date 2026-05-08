"""
Profile Distributions

Aggregate sums for values of fields in students profiles.

For example:
The distribution in a course for gender might look like:
'gender': {
    'type': 'EASY_CHOICE',
    'data': {
        'no_data': 1234,
        'm': 5678,
        'o': 2134,
        'f': 5678
    },
    'display_names': {
        'no_data': 'No Data',
        'm': 'Male',
        'o': 'Other',
        'f': 'Female'
}
"""


from django.db.models import Count

from common.djangoapps.student.models import CourseEnrollment, UserProfile

# choices with a restricted domain, e.g. level_of_education
_EASY_CHOICE_FEATURES = ('gender', 'level_of_education')
# choices with a larger domain e.g. year_of_birth
_OPEN_CHOICE_FEATURES = ('year_of_birth',)

AVAILABLE_PROFILE_FEATURES = _EASY_CHOICE_FEATURES + _OPEN_CHOICE_FEATURES
DISPLAY_NAMES = {
    'gender': 'Gender',
    'level_of_education': 'Level of Education',
    'year_of_birth': 'Year Of Birth',
}


class ProfileDistribution:
    """
    Container for profile distribution data

    `feature` is the name of the distribution feature
    `feature_display_name` is the display name of feature
    `data` is a dictionary of the distribution
    `type` is either 'EASY_CHOICE' or 'OPEN_CHOICE'
    `choices_display_names` is a dict if the distribution is an 'EASY_CHOICE'
    """

    class ValidationError(ValueError):
        """ Error thrown if validation fails. """
        pass  # pylint: disable=unnecessary-pass

    def __init__(self, feature):
        self.feature = feature
        self.feature_display_name = DISPLAY_NAMES.get(feature, feature)

        # to be set later
        self.type = None
        self.data = None
        self.choices_display_names = None

    def validate(self):
        """
        Validate this profile distribution.

        Throws ProfileDistribution.ValidationError
        """
        def validation_assert(predicate):
            """ Throw a ValidationError if false. """
            if not predicate:
                raise ProfileDistribution.ValidationError()

        validation_assert(isinstance(self.feature, str))
        validation_assert(self.feature in DISPLAY_NAMES)
        validation_assert(isinstance(self.feature_display_name, str))
        validation_assert(self.type in ['EASY_CHOICE', 'OPEN_CHOICE'])
        validation_assert(isinstance(self.data, dict))
        if self.type == 'EASY_CHOICE':
            validation_assert(isinstance(self.choices_display_names, dict))


def profile_distribution(course_id, feature):
    """
    Retrieve distribution of students over a given feature.
    feature is one of AVAILABLE_PROFILE_FEATURES.

    Returns a ProfileDistribution instance.

    NOTE: no_data will appear as a key instead of None/null to adhere to the json spec.
    data types are EASY_CHOICE or OPEN_CHOICE
    """

    if feature not in AVAILABLE_PROFILE_FEATURES:
        raise ValueError(
            "unsupported feature requested for distribution u'{}'".format(  # noqa: UP032
                feature)
        )

    prd = ProfileDistribution(feature)

    if feature in _EASY_CHOICE_FEATURES:
        prd.type = 'EASY_CHOICE'

        if feature == 'gender':
            raw_choices = UserProfile.GENDER_CHOICES
        elif feature == 'level_of_education':
            raw_choices = UserProfile.LEVEL_OF_EDUCATION_CHOICES

        # short name and display name (full) of the choices.
        choices = list(raw_choices) + [('no_data', 'No Data')]

        def get_filter(feature, value):
            """ Get the orm filter parameters for a feature. """
            return {
                'gender': {'user__profile__gender': value},
                'level_of_education': {'user__profile__level_of_education': value},
            }[feature]

        def get_count(feature, value):
            """ Get the count of enrolled students matching the feature value. """
            return CourseEnrollment.objects.filter(
                course_id=course_id,
                is_active=True,
                **get_filter(feature, value)
            ).count()

        distribution = {}
        for (short, full) in choices:  # pylint: disable=unused-variable  # noqa: B007
            # handle no data case
            if short == 'no_data':
                distribution['no_data'] = 0
                distribution['no_data'] += get_count(feature, None)
                distribution['no_data'] += get_count(feature, '')
            else:
                distribution[short] = get_count(feature, short)

        prd.data = distribution
        prd.choices_display_names = dict(choices)
    elif feature in _OPEN_CHOICE_FEATURES:
        prd.type = 'OPEN_CHOICE'
        profiles = UserProfile.objects.filter(
            user__courseenrollment__course_id=course_id,
            user__courseenrollment__is_active=True
        )
        query_distribution = profiles.values(
            feature).annotate(Count(feature)).order_by()
        # query_distribution is of the form [{'featureval': 'value1', 'featureval__count': 4},
        #    {'featureval': 'value2', 'featureval__count': 2}, ...]

        distribution = {vald[feature]: vald[feature + '__count'] for vald in query_distribution}
        # distribution is of the form {'value1': 4, 'value2': 2, ...}

        # change none to no_data for valid json key
        if None in distribution:
            # django does not properly count NULL values when using annotate Count
            # so
            #     distribution['no_data'] = distribution.pop(None)
            # would always be 0.

            # Correctly count null values
            distribution['no_data'] = profiles.filter(
                **{feature: None}
            ).count()

        prd.data = distribution

    prd.validate()
    return prd
