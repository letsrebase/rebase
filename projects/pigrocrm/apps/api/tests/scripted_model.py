"""The model's network for the API tests (REB-972)."""

from pigrocrm.core.ai.transport import ModelAnswer, ModelRequest


class ScriptedModel:
    """The model's network (REB-972): answers in the order a test queues them and records what
    it was sent. With nothing queued it fails the test, so no test reaches a provider."""

    def __init__(self) -> None:
        self.answers: list[ModelAnswer] = []
        self.requests: list[ModelRequest] = []

    def say(self, text: str, *, input_tokens: int = 1000, output_tokens: int = 200) -> None:
        self.answers.append(ModelAnswer(text, input_tokens, output_tokens))

    def __call__(self, request: ModelRequest) -> ModelAnswer:
        assert self.answers, "a test reached the model with no answer queued for it"
        self.requests.append(request)
        return self.answers.pop(0)
