"""The one error the typesetting toolchain raises."""

from rebase_core.errors import DomainError


class ContractFailed(DomainError):
    """A PDF that could not be typeset: a binary missing, a font that could not be built. The
    detail stays in English; the sentence around it is what an admin reads. The anonymous CV's
    print (`anonymous_cv.render`) reads the brand through `contracts.brand` and answers this
    error as a 503, as the contracts did when the hub typeset them (REB-1108 moved those to the
    CRM)."""

    code = "contract_failed"

    def __init__(self, detail: str) -> None:
        super().__init__(f"La generazione del contratto non è riuscita: {detail}", detail=detail)
        self.detail = detail
