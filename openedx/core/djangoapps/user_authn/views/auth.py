""" Views related to auth. """

import json

from django.conf import settings

from common.djangoapps.util.json_request import JsonResponse


def get_public_signing_jwks(request):
    """
    View to provide the auth related public signing JWK set as json.
    """
    jwt_dict = settings.JWT_AUTH
    if not jwt_dict.get('JWT_PUBLIC_SIGNING_JWK_SET'):
        return JsonResponse({'error': 'JWK set is not found'}, status=400)
    jwks = jwt_dict['JWT_PUBLIC_SIGNING_JWK_SET']
    # jwks is a string here, need to convert it to dict
    return JsonResponse(json.loads(jwks), status=200)
