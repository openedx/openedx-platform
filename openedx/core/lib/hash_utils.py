"""
Utilities related to hashing

This duplicates functionality in django-oauth-provider,
specifically long_token and short token functions which was used to create
random tokens
"""
import hashlib

from django.conf import settings
from django.utils.crypto import get_random_string
from django.utils.encoding import force_bytes


def create_hash256(max_length=None):
    """
    Generate a hash that can be used as an application secret
    Warning: this is not sufficiently secure for tasks like encryption
    Currently, this is just meant to create sufficiently random tokens
    """
    hash_object = hashlib.sha256(force_bytes(get_random_string(32)))
    hash_object.update(force_bytes(settings.SECRET_KEY))
    output_hash = hash_object.hexdigest()
    if max_length is not None and len(output_hash) > max_length:
        return output_hash[:max_length]
    return output_hash


def short_token():
    """
    Generates a hash of length 32
    Warning: this is not sufficiently secure for tasks like encription
    Currently, this is just meant to create sufficiently random tokens
    """
    return create_hash256(max_length=32)
