class ExternalError(Exception):
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def classify_external_error(error: ExternalError) -> str:
    return "retryable" if error.retryable else "human_review"
