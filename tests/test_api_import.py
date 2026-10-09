from audioexport import AudiobookMetadata, AudiobookTrack, build_audiobook, encode
from audioexport.api import build_audiobook as api_build_audiobook
from audioexport.api import encode as api_encode


def test_flat_public_api() -> None:
    assert encode is api_encode
    assert build_audiobook is api_build_audiobook
    assert AudiobookTrack.__name__ == "AudiobookTrack"
    assert AudiobookMetadata.__name__ == "AudiobookMetadata"
