class Rejected(Exception):
    """SES refused the address itself (malformed, or not allowed while in the sandbox). Not our failure."""
