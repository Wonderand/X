class DomainError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status
        super().__init__(message)


class ServiceError(Exception):
    """Only safe, fixed messages cross the adapter boundary."""

    def __init__(self, code, *, uncertain=False, retry_after=300):
        self.code = code
        self.uncertain = uncertain
        self.retry_after = retry_after
        super().__init__(code)
