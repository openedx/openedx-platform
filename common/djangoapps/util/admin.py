"""Admin interface for the util app. """


from django.contrib import admin  # noqa: I001

from common.djangoapps.util.models import RateLimitConfiguration

admin.site.register(RateLimitConfiguration)
