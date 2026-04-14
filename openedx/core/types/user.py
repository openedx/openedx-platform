"""
Typing utilities for the User models.
"""
from __future__ import annotations

import django.contrib.auth.models

# base type for an authenticated user
type AuthUser = django.contrib.auth.models.User
# base type for a generic user making an HTTP request, which may or may not be authenticated:
type User = AuthUser | django.contrib.auth.models.AnonymousUser
