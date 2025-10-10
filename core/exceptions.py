# core/exceptions.py
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

def exception_handler(exc, context):
    resp = drf_exception_handler(exc, context)
    if resp is None:
        return None

    # Do NOT wrap serializer/field errors. Keep {"quantity": ["..."]} shape.
    if isinstance(exc, ValidationError):
        return resp

    data = resp.data
    # If it's already a field-error dict (no "detail"), keep it.
    if isinstance(data, dict) and "detail" not in data:
        return resp

    detail = data.get("detail", data)
    code = getattr(getattr(exc, "default_code", None), "__str__", lambda: None)()
    return Response({"detail": detail, "code": code or resp.status_code}, status=resp.status_code)
