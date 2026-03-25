"""
Typing utilities for the User models.
"""
from __future__ import annotations  # noqa: I001

import typing as t

import django.contrib.auth.models

AuthUser: t.TypeAlias = django.contrib.auth.models.User
User: t.TypeAlias = django.contrib.auth.models.User | django.contrib.auth.models.AnonymousUser
