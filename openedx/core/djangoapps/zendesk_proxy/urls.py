"""
Map urls to the relevant view handlers
"""

from django.urls import path

from openedx.core.djangoapps.zendesk_proxy.v0.views import ZendeskPassthroughView as v0_view
from openedx.core.djangoapps.zendesk_proxy.v1.views import ZendeskPassthroughView as v1_view

urlpatterns = [
    path('v0', v0_view.as_view(), name='zendesk_proxy_v0'),
    path('v1', v1_view.as_view(), name='zendesk_proxy_v1'),
]
