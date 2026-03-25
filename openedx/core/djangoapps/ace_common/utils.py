"""
Utility functions for edx-ace.
"""
import logging  # noqa: I001

log = logging.getLogger(__name__)


def setup_firebase_app(firebase_credentials, app_name='fcm-app'):
    """
    Returns a Firebase app instance if the Firebase credentials are provided.
    """
    import firebase_admin  # pylint: disable=import-outside-toplevel

    if firebase_credentials:
        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            certificate = firebase_admin.credentials.Certificate(firebase_credentials)
            app = firebase_admin.initialize_app(certificate, name=app_name)
        return app
