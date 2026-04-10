"""
NOTE: Anytime a `key` is passed into a function here, we assume it's a raw byte
string. It should *not* be a string representation of a hex value. In other
words, passing the `str` value of
`"32fe72aaf2abb44de9e161131b5435c8d37cbdb6f5df242ae860b283115f2dae"` is bad.
You want to pass in the result of calling .decode('hex') on that, so this instead:
"'2\xfer\xaa\xf2\xab\xb4M\xe9\xe1a\x13\x1bT5\xc8\xd3|\xbd\xb6\xf5\xdf$*\xe8`\xb2\x83\x11_-\xae'"

An RSA public key can be in any of the following formats:
* X.509 subjectPublicKeyInfo DER SEQUENCE (binary or PEM encoding)
* PKCS#1 RSAPublicKey DER SEQUENCE (binary or PEM encoding)
* OpenSSH (textual public key only)

An RSA private key can be in any of the following formats:
* PKCS#1 RSAPrivateKey DER SEQUENCE (binary or PEM encoding)
* PKCS#8 PrivateKeyInfo DER SEQUENCE (binary or PEM encoding)
"""


import base64
import binascii
import hmac
import logging
import os
from hashlib import md5, sha256

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.padding import MGF1, OAEP
from cryptography.hazmat.primitives.ciphers import Cipher
from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers.modes import CBC
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.padding import PKCS7

log = logging.getLogger(__name__)

AES_BLOCK_SIZE_BYTES = int(AES.block_size / 8)


def _redact_secret(value, prefix_len=4):
    """
    Return a short, redacted representation of a secret string suitable
    for DEBUG-level logging.

    Preserves enough characters for an operator to tell two distinct
    tenants apart while guaranteeing the full secret cannot be
    reconstructed from log files or Sentry events (CWE-532).
    """
    if not value:
        return "(empty)"
    if len(value) <= prefix_len:
        # Pathologically short secret: redact entirely.
        return "***"
    return value[:prefix_len] + "…"


def encrypt_and_encode(data, key):
    """ Encrypts and encodes `data` using `key' """
    return base64.urlsafe_b64encode(aes_encrypt(data, key))


def decode_and_decrypt(encoded_data, key):
    """ Decrypts and decodes `data` using `key' """
    return aes_decrypt(base64.urlsafe_b64decode(encoded_data), key)


def aes_encrypt(data, key):
    """
    Return a version of the `data` that has been encrypted to
    """
    cipher = aes_cipher_from_key(key)
    padded_data = pad(data)
    encryptor = cipher.encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()


def aes_decrypt(encrypted_data, key):
    """
    Decrypt `encrypted_data` using `key`
    """
    cipher = aes_cipher_from_key(key)
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(encrypted_data) + decryptor.finalize()
    return unpad(padded_data)


def aes_cipher_from_key(key):
    """
    Given an AES key, return a Cipher object that has `encryptor()` and
    `decryptor()` methods. It will create the cipher to use CBC mode, and create
    the initialization vector as Software Secure expects it.
    """
    return Cipher(AES(key), CBC(generate_aes_iv(key)), backend=default_backend())


def generate_aes_iv(key):
    """
    Return the initialization vector Software Secure expects for a given AES
    key (they hash it a couple of times and take a substring).
    """
    return md5(key + md5(key).hexdigest().encode('utf-8')).hexdigest()[:AES_BLOCK_SIZE_BYTES].encode('utf-8')


def random_aes_key():
    return os.urandom(32)


def pad(data):
    """ Pad the given `data` such that it fits into the proper AES block size """

    if not isinstance(data, (bytes, bytearray)):
        data = data.encode()

    padder = PKCS7(AES.block_size).padder()
    return padder.update(data) + padder.finalize()


def unpad(padded_data):
    """  remove all padding from `padded_data` """
    unpadder = PKCS7(AES.block_size).unpadder()
    return unpadder.update(padded_data) + unpadder.finalize()


def rsa_encrypt(data, rsa_pub_key_bytes):
    """
    `rsa_pub_key_bytes` is a byte sequence with the public key
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    if isinstance(rsa_pub_key_bytes, str):
        rsa_pub_key_bytes = rsa_pub_key_bytes.encode('utf-8')
    if rsa_pub_key_bytes.startswith(b'-----'):
        key = serialization.load_pem_public_key(rsa_pub_key_bytes, backend=default_backend())
    elif rsa_pub_key_bytes.startswith(b'ssh-rsa '):
        key = serialization.load_ssh_public_key(rsa_pub_key_bytes, backend=default_backend())
    else:
        key = serialization.load_der_public_key(rsa_pub_key_bytes, backend=default_backend())
    return key.encrypt(data, OAEP(MGF1(SHA1()), SHA1(), label=None))


def rsa_decrypt(data, rsa_priv_key_bytes):
    """
    When given some `data` and an RSA private key, decrypt the data
    """
    if isinstance(data, str):
        data = data.encode('utf-8')
    if isinstance(rsa_priv_key_bytes, str):
        rsa_priv_key_bytes = rsa_priv_key_bytes.encode('utf-8')
    if rsa_priv_key_bytes.startswith(b'-----'):
        key = serialization.load_pem_private_key(rsa_priv_key_bytes, password=None, backend=default_backend())
    else:
        key = serialization.load_der_private_key(rsa_priv_key_bytes, password=None, backend=default_backend())
    return key.decrypt(data, OAEP(MGF1(SHA1()), SHA1(), label=None))


def has_valid_signature(method, headers_dict, body_dict, access_key, secret_key):
    """
    Given a message (either request or response), say whether it has a valid
    signature or not.
    """
    _, expected_signature, _ = generate_signed_message(
        method, headers_dict, body_dict, access_key, secret_key
    )

    authorization = headers_dict["Authorization"]
    auth_token, post_signature = authorization.split(":")
    _, post_access_key = auth_token.split()

    if post_access_key != access_key:
        log.error("Posted access key does not match ours")
        # Log only a short prefix + length so operators debugging a
        # signature mismatch can confirm they are comparing the right
        # tenants, without writing the full API access key into log
        # files or Sentry events (CWE-532). The server's access key is
        # ``settings.VERIFY_STUDENT["SOFTWARE_SECURE"]["API_ACCESS_KEY"]``
        # and must never be reconstructable from logs.
        log.debug(
            "Access key mismatch - theirs: %s (len=%d); ours: %s (len=%d)",
            _redact_secret(post_access_key),
            len(post_access_key or ""),
            _redact_secret(access_key),
            len(access_key or ""),
        )
        return False

    if post_signature != expected_signature:
        log.error("Posted signature does not match expected")
        # Same treatment as above: signatures are HMAC values derived
        # from the shared secret, so only a short prefix is logged.
        log.debug(
            "Signature mismatch - theirs: %s (len=%d); ours: %s (len=%d)",
            _redact_secret(post_signature),
            len(post_signature or ""),
            _redact_secret(expected_signature),
            len(expected_signature or ""),
        )
        return False

    return True


def generate_signed_message(method, headers_dict, body_dict, access_key, secret_key):
    """
    Returns a (message, signature) pair.
    """
    message = signing_format_message(method, headers_dict, body_dict)

    # hmac needs a byte string for it's starting key, can't be unicode.
    hashed = hmac.new(secret_key.encode('utf-8'), message.encode('utf-8'), sha256)
    signature = binascii.b2a_base64(hashed.digest()).rstrip(b'\n').decode('utf-8')
    authorization_header = f"SSI {access_key}:{signature}"

    message += '\n'
    return message, signature, authorization_header


def signing_format_message(method, headers_dict, body_dict):
    """
    Given a dictionary of headers and a dictionary of the JSON for the body,
    will return a str that represents the normalized version of this messsage
    that will be used to generate a signature.
    """
    headers_str = f"{method}\n\n{header_string(headers_dict)}"
    body_str = body_string(body_dict)
    message = headers_str + body_str

    return message


def header_string(headers_dict):
    """Given a dictionary of headers, return a canonical string representation."""
    header_list = []

    if 'Content-Type' in headers_dict:
        header_list.append(headers_dict['Content-Type'] + "\n")
    if 'Date' in headers_dict:
        header_list.append(headers_dict['Date'] + "\n")
    if 'Content-MD5' in headers_dict:
        header_list.append(headers_dict['Content-MD5'] + "\n")

    return "".join(header_list)  # Note that trailing \n's are important


def body_string(body_dict, prefix=""):
    """
    Return a canonical string representation of the body of a JSON request or
    response. This canonical representation will be used as an input to the
    hashing used to generate a signature.
    """
    body_list = []
    for key, value in sorted(body_dict.items()):
        if isinstance(value, (list, tuple)):
            for i, arr in enumerate(value):
                if isinstance(arr, dict):
                    body_list.append(body_string(arr, f"{key}.{i}."))
                else:
                    body_list.append(f"{key}.{i}:{arr}\n")
        elif isinstance(value, dict):
            body_list.append(body_string(value, key + ":"))
        else:
            if value is None:
                value = "null"
            body_list.append(f"{prefix}{key}:{value}\n")

    return "".join(body_list)  # Note that trailing \n's are important
