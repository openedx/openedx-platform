"""
Admin site bindings for ccxcon
"""


from django.contrib import admin  # noqa: I001

from .models import CCXCon

admin.site.register(CCXCon)
