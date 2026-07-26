from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException
from rest_framework import status

class CustomBusinessException(APIException):
    def __init__(self, code, message, status_code=status.HTTP_400_BAD_REQUEST, details=None):
        self.status_code = status_code
        self.detail = {
            "code": code,
            "message": message,
            "details": details or {}
        }
        super().__init__(detail=self.detail, code=code)

def global_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        if isinstance(response.data, dict) and "code" in response.data:
            return response
        standard_errors = response.data
        code = "VALIDATION_ERROR"
        message = "Ошибка валидации входных данных"
        if response.status_code == 404:
            code = "NOT_FOUND"
            message = "Объект не найден"
        elif response.status_code == 403:
            code = "ACCESS_DENIED"
            message = "Доступ запрещен"
        response.data = {"code": code, "message": message, "details": standard_errors}
    return response
