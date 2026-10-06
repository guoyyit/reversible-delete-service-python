"""领域异常。"""


class DomainError(Exception):
    """领域错误基类，携带 HTTP 状态码。"""

    status_code: int = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class ForbiddenError(DomainError):
    status_code = 403


class GoneError(DomainError):
    """保留期已过，资源不可恢复。"""

    status_code = 410
