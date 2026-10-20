class BusinessError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
