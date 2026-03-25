"""
Manage cross-domain configuration.
"""


from config_models.admin import ConfigurationModelAdmin  # noqa: I001
from django.contrib import admin

from .models import XDomainProxyConfiguration

admin.site.register(XDomainProxyConfiguration, ConfigurationModelAdmin)
