#!/usr/bin/env python3
# Enhance a JWK to have all of its optional performance parameters.
# Intended to be used on an RSA `JWT_PRIVATE_SIGNING_JWK`.
#
# This is needed for the change from pyjwkest to PyJWT, since the
# former accepts a partial list of optional parameters but the latter
# requires that they are either all present or all absent. The optional
# parameters provide a performance boost.
#
# Usage: Key JSON accepted on stdin; enhanced key printed to stdout.

import json
import sys

from jwt.algorithms import RSAAlgorithm

print("Paste the key's JSON, followed by a new line and Ctrl-D:\n", file=sys.stderr)
old_jwk_data = json.loads(sys.stdin.read())

# Clear out all of the precomputed private numbers
for param_key in ['p', 'q', 'dp', 'dq', 'qi']:
    if param_key in old_jwk_data:
        del old_jwk_data[param_key]

# Ensure that there aren't any unexpected parameters
expected_remaining = {'kty', 'e', 'd', 'n', 'kid', 'key_ops'}
unexpected_params = set(old_jwk_data.keys()) - expected_remaining
if len(unexpected_params):
    print(
        f"Unexpected parameters {unexpected_params} would be lost. Aborting script. "
        "If your key has additional parameters that are unrelated to the precomputed "
        "private numbers, then please add them to the `expected_remaining` variable "
        "and re-run the script. Please consider making a PR as well.",
        file=sys.stderr
    )
    sys.exit(1)

# Recompute private numbers
new_jwk_data = json.loads(RSAAlgorithm.to_jwk(RSAAlgorithm.from_jwk(old_jwk_data)))

# Restore the kid (key ID) param, which gets lost in the process. This adds it
# to the front of the dict. The params are actually in a really nice order in
# the native ordering that comes out of the JWK, with metadata first, then the
# core params (n, e, d), and then the precomputed values.
for restore_param in ['kid']:
    if restore_param in old_jwk_data:
        new_jwk_data = {restore_param: old_jwk_data[restore_param], **new_jwk_data}

# Pretty-print so that the kid and modulus can be confirmed easily
print("\n\nEnhanced private key:\n", file=sys.stderr)
print(json.dumps(new_jwk_data, indent=4))
