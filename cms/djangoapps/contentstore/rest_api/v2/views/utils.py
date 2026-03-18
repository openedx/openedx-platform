"""
Common utilities for V2 APIs.
"""
from rest_framework.response import Response
from rest_framework.generics import GenericAPIView
from rest_framework import permissions
from cms.djangoapps.contentstore.rest_api.v2.serializers.utils import NumericalInputValidationRequestSerializer
from xblocks_contrib.problem.capa.inputtypes import preview_numeric_input


class NumericalInputValidationView(GenericAPIView):
    """Class in charge of NumericalInputValidations"""
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = NumericalInputValidationRequestSerializer

    def post(self, request):
        """function to validate a math expression (formula) and return of the numeric input is valid or not"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        formula = serializer.validated_data['formula']
        result = preview_numeric_input(formula)
        return Response(result, status=200)
