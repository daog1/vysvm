# Simple Vyper contract for testing SVM compilation

event LogMessage:
    message: String[32]

@external
def entrypoint() -> uint256:
    log LogMessage(message="Hello from Vyper on SVM!")
    return 0
