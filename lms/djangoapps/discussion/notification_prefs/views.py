"""
Views to support notification preferences.
"""
import json
import os
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error
from hashlib import sha256

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.padding import PKCS7
from django.conf import settings
from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET, require_POST

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.discussion.notification_prefs import NOTIFICATION_PREF_KEY
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import delete_user_preference

AES_BLOCK_SIZE_BYTES = int(AES.block_size / 8)


class UsernameDecryptionException(Exception):
    pass


class UsernameCipher:
    """
    A transformation of a username to/from an opaque token

    The purpose of the token is to make one-click unsubscribe links that don't
    require the user to log in. To prevent users from unsubscribing other users,
    we must ensure the token cannot be computed by anyone who has this
    source code. The token must also be embeddable in a URL.

    Thus, we take the following steps to encode (and do the inverse to decode):
    1. Pad the UTF-8 encoding of the username with PKCS#7 padding to match the
       AES block length
    2. Generate a random AES block length initialization vector
    3. Use AES-256 (with a hash of settings.SECRET_KEY as the encryption key)
       in CBC mode to encrypt the username
    4. Prepend the IV to the encrypted value to allow for initialization of the
       decryption cipher
    5. base64url encode the result
    """
    @staticmethod
    def _get_aes_cipher(initialization_vector):
        hash_ = sha256()
        hash_.update(settings.SECRET_KEY.encode())
        return Cipher(AES(hash_.digest()), CBC(initialization_vector), backend=default_backend())

    @staticmethod
    def encrypt(username):  # pylint: disable=missing-function-docstring
        initialization_vector = os.urandom(AES_BLOCK_SIZE_BYTES)

        if not isinstance(initialization_vector, (bytes, bytearray)):
            initialization_vector = initialization_vector.encode('utf-8')

        aes_cipher = UsernameCipher._get_aes_cipher(initialization_vector)
        encryptor = aes_cipher.encryptor()
        padder = PKCS7(AES.block_size).padder()
        padded = padder.update(username.encode("utf-8")) + padder.finalize()
        return urlsafe_b64encode(initialization_vector + encryptor.update(padded) + encryptor.finalize()).decode()

    @staticmethod
    def decrypt(token):  # pylint: disable=missing-function-docstring
        try:
            base64_decoded = urlsafe_b64decode(token)
        except (TypeError, Error):
            raise UsernameDecryptionException("base64url")  # pylint: disable=raise-missing-from  # noqa: B904

        if len(base64_decoded) < AES_BLOCK_SIZE_BYTES:
            raise UsernameDecryptionException("initialization_vector")

        initialization_vector = base64_decoded[:AES_BLOCK_SIZE_BYTES]
        aes_encrypted = base64_decoded[AES_BLOCK_SIZE_BYTES:]
        aes_cipher = UsernameCipher._get_aes_cipher(initialization_vector)
        decryptor = aes_cipher.decryptor()
        unpadder = PKCS7(AES.block_size).unpadder()

        try:
            decrypted = decryptor.update(aes_encrypted) + decryptor.finalize()
        except ValueError:
            raise UsernameDecryptionException("aes")  # pylint: disable=raise-missing-from  # noqa: B904

        try:
            unpadded = unpadder.update(decrypted) + unpadder.finalize()
            if len(unpadded) == 0:
                raise UsernameDecryptionException("padding")
            return unpadded
        except ValueError:
            raise UsernameDecryptionException("padding")  # pylint: disable=raise-missing-from  # noqa: B904


def enable_notifications(user):
    """
    Enable notifications for a user.
    Currently only used for daily forum digests.
    """
    # Calling UserPreference directly because this method is called from a couple of places,
    # and it is not clear that user is always the user initiating the request.
    UserPreference.objects.get_or_create(
        user=user,
        key=NOTIFICATION_PREF_KEY,
        defaults={
            "value": UsernameCipher.encrypt(user.username)
        }
    )


@require_POST
def ajax_enable(request):
    """
    A view that enables notifications for the authenticated user

    This view should be invoked by an AJAX POST call. It returns status 204
    (no content) or an error. If notifications were already enabled for this
    user, this has no effect. Otherwise, a preference is created with the
    unsubscribe token (an encryption of the username) as the value.username
    """
    if not request.user.is_authenticated:
        raise PermissionDenied

    enable_notifications(request.user)

    return HttpResponse(status=204)


@require_POST
def ajax_disable(request):
    """
    A view that disables notifications for the authenticated user

    This view should be invoked by an AJAX POST call. It returns status 204
    (no content) or an error.
    """
    if not request.user.is_authenticated:
        raise PermissionDenied

    delete_user_preference(request.user, NOTIFICATION_PREF_KEY)

    return HttpResponse(status=204)


@require_GET
def ajax_status(request):
    """
    A view that retrieves notifications status for the authenticated user.

    This view should be invoked by an AJAX GET call. It returns status 200,
    with a JSON-formatted payload, or an error.
    """
    if not request.user.is_authenticated:
        raise PermissionDenied

    qs = UserPreference.objects.filter(
        user=request.user,
        key=NOTIFICATION_PREF_KEY
    )

    return HttpResponse(json.dumps({"status": len(qs)}), content_type="application/json")  # pylint: disable=http-response-with-content-type-json, http-response-with-json-dumps


@require_GET
def set_subscription(request, token, subscribe):
    """
    A view that disables or re-enables notifications for a user who may not be authenticated

    This view is meant to be the target of an unsubscribe link. The request
    must be a GET, and the `token` parameter must decrypt to a valid username.
    The subscribe flag feature controls whether the view subscribes or unsubscribes the user, with subscribe=True
    used to "undo" accidentally clicking on the unsubscribe link

    A 405 will be returned if the request method is not GET. A 404 will be
    returned if the token parameter does not decrypt to a valid username. On
    success, the response will contain a page indicating success.
    """
    try:
        username = UsernameCipher().decrypt(token.encode()).decode()
        user = User.objects.get(username=username)
    except UnicodeDecodeError:
        raise Http404("base64url")  # pylint: disable=raise-missing-from  # noqa: B904
    except UsernameDecryptionException as exn:
        raise Http404(str(exn))  # pylint: disable=raise-missing-from  # noqa: B904
    except User.DoesNotExist:
        raise Http404("username")  # pylint: disable=raise-missing-from  # noqa: B904

    # Calling UserPreference directly because the fact that the user is passed in the token implies
    # that it may not match request.user.
    if subscribe:
        UserPreference.objects.get_or_create(user=user,
                                             key=NOTIFICATION_PREF_KEY,
                                             defaults={
                                                 "value": UsernameCipher.encrypt(user.username)
                                             })
        return render_to_response("resubscribe.html", {'token': token})
    else:
        UserPreference.objects.filter(user=user, key=NOTIFICATION_PREF_KEY).delete()
        return render_to_response("unsubscribe.html", {'token': token})
