from __future__ import annotations

import audioexport
from audioexport import (
    AudiobookMetadata,
    AudiobookTrack,
    ResolvedOutput,
    build_audiobook,
    encode,
    preflight_profile,
    resolve_output,
)
from audioexport.api import (
    ResolvedOutput as ApiResolvedOutput,
)
from audioexport.api import (
    build_audiobook as api_build_audiobook,
)
from audioexport.api import (
    encode as api_encode,
)
from audioexport.api import (
    preflight_profile as api_preflight_profile,
)
from audioexport.api import (
    resolve_output as api_resolve_output,
)


def test_flat_public_api() -> None:
    assert encode is api_encode
    assert build_audiobook is api_build_audiobook
    assert AudiobookTrack.__name__ == "AudiobookTrack"
    assert AudiobookMetadata.__name__ == "AudiobookMetadata"
    assert ResolvedOutput is ApiResolvedOutput
    assert resolve_output is api_resolve_output
    assert preflight_profile is api_preflight_profile
    public_symbols = {"ResolvedOutput", "resolve_output", "preflight_profile"}
    assert public_symbols.issubset(audioexport.__all__)
    from audioexport import api

    assert public_symbols.issubset(api.__all__)
