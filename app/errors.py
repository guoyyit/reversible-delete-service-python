"""Domain errors mapped to HTTP responses."""


class ServiceError(Exception):
    """Base error carrying an HTTP status code."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", *, code: str | None = None):
        super().__init__(message or self.code)
        if code is not None:
            self.code = code
        self.message = message or self.code


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class UnauthorizedError(ServiceError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(ServiceError):
    status_code = 403
    code = "forbidden"


class ValidationError(ServiceError):
    status_code = 422
    code = "validation_error"
