class BoltzFixError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        pause: bool = False,
        unknown_remote: bool = False
    ):
        super().__init__(message)
        self.code, self.message = code, message
        self.pause, self.unknown_remote = pause, unknown_remote

    def public(self):
        return {"code": self.code, "message": self.message}
