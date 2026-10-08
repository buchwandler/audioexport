from audioexport import encode
from audioexport.api import encode as api_encode


def test_flat_public_api() -> None:
    assert encode is api_encode
