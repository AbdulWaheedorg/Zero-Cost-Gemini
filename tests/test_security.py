import logging

from src.security import SensitiveDataFilter


def test_oauth_tokens_are_redacted_from_logs():
    record = logging.LogRecord("x", logging.INFO, __file__, 1,
                               "token ya29.a0AfH6SMBx-secret and 1//0gRefresh_tok", ("ya29.arg",), None)
    SensitiveDataFilter().filter(record)
    assert "ya29.a0" not in record.msg and "1//0g" not in record.msg
    assert record.args == ("[REDACTED_TOKEN]",)
