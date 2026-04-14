""" HTTP-related entities. """


from rest_framework.status import HTTP_200_OK, HTTP_500_INTERNAL_SERVER_ERROR

from common.djangoapps.util.json_request import JsonResponse


class DetailResponse(JsonResponse):
    """ JSON response that simply contains a detail field. """

    def __init__(self, message, status=HTTP_200_OK):
        data = {'detail': message}
        super().__init__(resp_obj=data, status=status)


class InternalRequestErrorResponse(DetailResponse):
    """ Response returned when an internal service request fails. """

    def __init__(self, internal_message):
        message = (
            'Call to E-Commerce API failed. Internal Service Message: [{internal_message}]'  # noqa: UP032
            .format(internal_message=internal_message)
        )
        super().__init__(message=message, status=HTTP_500_INTERNAL_SERVER_ERROR)
