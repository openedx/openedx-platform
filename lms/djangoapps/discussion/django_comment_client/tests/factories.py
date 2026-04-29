# pylint: disable=missing-module-docstring
from factory.django import DjangoModelFactory

from openedx.core.djangoapps.django_comment_common.models import Permission, Role


class RoleFactory(DjangoModelFactory):  # pylint: disable=missing-class-docstring
    class Meta:
        model = Role

    name = 'Student'
    course_id = 'edX/toy/2012_Fall'


class PermissionFactory(DjangoModelFactory):
    class Meta:
        model = Permission

    name = 'create_comment'
