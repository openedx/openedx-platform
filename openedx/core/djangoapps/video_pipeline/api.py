"""
API utils in order to communicate to edx-video-pipeline.
"""

import json  # noqa: I001
import logging

from django.core.exceptions import ObjectDoesNotExist
from oauth2_provider.models import Application
from requests.exceptions import HTTPError

from openedx.core.djangoapps.video_pipeline.models import VEMPipelineIntegration
from openedx.core.djangoapps.video_pipeline.utils import create_video_pipeline_api_client

log = logging.getLogger(__name__)


def send_transcript_credentials(pipeline_integration, credentials_payload):
    """
    Sends transcript credentials to video pipeline
    """
    error_response, is_updated = {}, False
    try:
        oauth_client = Application.objects.get(name=pipeline_integration.client_name)
    except ObjectDoesNotExist:
        return error_response, is_updated

    client = create_video_pipeline_api_client(
        oauth_client.client_id,
        oauth_client.client_secret
    )
    error_message = "Unable to update transcript credentials -- org=%s, provider=%s, response=%s"
    try:
        response = client.post(pipeline_integration.api_url, json=credentials_payload)
        response.raise_for_status()
        if response.ok:
            is_updated = True
        else:
            is_updated = False
            error_response = json.loads(response.text)
            log.error(
                error_message,
                credentials_payload.get('org'),
                credentials_payload.get('provider'),
                response.text
            )
    except HTTPError as ex:
        is_updated = False
        log.exception(
            error_message,
            credentials_payload.get('org'),
            credentials_payload.get('provider'),
            ex.response.content
        )
        error_response = json.loads(ex.response.content)

    return error_response, is_updated


def update_3rd_party_transcription_service_credentials(**credentials_payload):
    """
    Updates the 3rd party transcription service's credentials. Credentials are updated
    for all the enabled pipelines.

    Arguments:
        credentials_payload(dict): A payload containing org, provider and its credentials.

    Returns:
        A Boolean specifying whether the credentials were updated or not
        and an error response received from pipeline.

    Note: If one of the pipeline fails to update the credentials, False is returned, meaning
    that credentials were not updated and both calls should be tried again.
    """
    error_response, is_updated = {}, False

    vem_pipeline_integration = VEMPipelineIntegration.current()

    if vem_pipeline_integration.enabled:
        log.info('Sending transcript credentials to VEM for org: {} and provider: {}'.format(
            credentials_payload.get('org'), credentials_payload.get('provider')
        ))
        error_response, is_updated = send_transcript_credentials(vem_pipeline_integration, credentials_payload)

    return error_response, is_updated
