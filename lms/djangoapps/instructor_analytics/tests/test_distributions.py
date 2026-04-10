""" Tests for analytics.distributions """


import pytest
from django.test import TestCase
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.student.models import CourseEnrollment
from common.djangoapps.student.tests.factories import UserFactory
from lms.djangoapps.instructor_analytics.distributions import AVAILABLE_PROFILE_FEATURES, profile_distribution


class TestAnalyticsDistributions(TestCase):
    '''Test analytics distribution gathering.'''

    def setUp(self):
        super().setUp()
        self.course_id = CourseLocator('robot', 'course', 'id')

        self.users = [UserFactory(
            profile__gender=['m', 'f', 'o'][i % 3],
            profile__level_of_education=['a', 'hs', 'el'][i % 3],
            profile__year_of_birth=i + 1930
        ) for i in range(30)]

        self.ces = [CourseEnrollment.enroll(user, self.course_id)
                    for user in self.users]

    def test_profile_distribution_bad_feature(self):
        feature = 'robot-not-a-real-feature'
        assert feature not in AVAILABLE_PROFILE_FEATURES
        with pytest.raises(ValueError):  # noqa: PT011
            profile_distribution(self.course_id, feature)

    def test_profile_distribution_easy_choice(self):
        feature = 'gender'
        assert feature in AVAILABLE_PROFILE_FEATURES
        distribution = profile_distribution(self.course_id, feature)
        assert distribution.type == 'EASY_CHOICE'
        assert distribution.data['no_data'] == 0
        assert distribution.data['m'] == (len(self.users) / 3)
        assert distribution.choices_display_names['m'] == 'Male'

    def test_profile_distribution_open_choice(self):
        feature = 'year_of_birth'
        assert feature in AVAILABLE_PROFILE_FEATURES
        distribution = profile_distribution(self.course_id, feature)
        print(distribution)
        assert distribution.type == 'OPEN_CHOICE'
        assert hasattr(distribution, 'choices_display_names')
        assert distribution.choices_display_names is None
        assert 'no_data' not in distribution.data
        assert distribution.data[1930] == 1

    def test_gender_count(self):
        course_enrollments = CourseEnrollment.objects.filter(
            course_id=self.course_id, user__profile__gender='m'
        )
        distribution = profile_distribution(self.course_id, "gender")
        assert distribution.data['m'] == len(course_enrollments)
        course_enrollments[0].deactivate()
        distribution = profile_distribution(self.course_id, "gender")
        assert distribution.data['m'] == (len(course_enrollments) - 1)

    def test_level_of_education_count(self):
        course_enrollments = CourseEnrollment.objects.filter(
            course_id=self.course_id, user__profile__level_of_education='hs'
        )
        distribution = profile_distribution(self.course_id, "level_of_education")
        assert distribution.data['hs'] == len(course_enrollments)
        course_enrollments[0].deactivate()
        distribution = profile_distribution(self.course_id, "level_of_education")
        assert distribution.data['hs'] == (len(course_enrollments) - 1)


class TestAnalyticsDistributionsNoData(TestCase):
    '''Test analytics distribution gathering.'''

    def setUp(self):
        super().setUp()
        self.course_id = CourseLocator('robot', 'course', 'id')

        self.users = [UserFactory(
            profile__year_of_birth=i + 1930,
        ) for i in range(5)]

        self.nodata_users = [UserFactory(
            profile__year_of_birth=None,
            profile__gender=[None, ''][i % 2]
        ) for i in range(4)]

        self.users += self.nodata_users

        self.ces = tuple(CourseEnrollment.enroll(user, self.course_id)
                         for user in self.users)

    def test_profile_distribution_easy_choice_nodata(self):
        feature = 'gender'
        assert feature in AVAILABLE_PROFILE_FEATURES
        distribution = profile_distribution(self.course_id, feature)
        print(distribution)
        assert distribution.type == 'EASY_CHOICE'
        assert hasattr(distribution, 'choices_display_names')
        assert distribution.choices_display_names is not None
        assert 'no_data' in distribution.data
        assert distribution.data['no_data'] == len(self.nodata_users)

    def test_profile_distribution_open_choice_nodata(self):
        feature = 'year_of_birth'
        assert feature in AVAILABLE_PROFILE_FEATURES
        distribution = profile_distribution(self.course_id, feature)
        print(distribution)
        assert distribution.type == 'OPEN_CHOICE'
        assert hasattr(distribution, 'choices_display_names')
        assert distribution.choices_display_names is None
        assert 'no_data' in distribution.data
        assert distribution.data['no_data'] == len(self.nodata_users)
