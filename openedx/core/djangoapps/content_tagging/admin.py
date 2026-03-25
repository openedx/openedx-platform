""" Tagging app admin """
from django.contrib import admin  # noqa: I001

from .models import TaxonomyOrg

admin.site.register(TaxonomyOrg)
