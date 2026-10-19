from __future__ import annotations

import audioexport
from audioexport import (
    AudiobookMetadata,
    AudiobookTrack,
    BookExportContext,
    BookExportResult,
    ComposedChapter,
    ComposedDocument,
    DocumentExportContext,
    DocumentExportResult,
    api,
    build_audiobook,
    encode,
    export_book,
    export_document,
    preflight_profile,
    resolve_book_export,
    resolve_document_export,
    resolve_output,
)


def test_flat_public_api() -> None:
    public_pairs = (
        (encode, api.encode),
        (build_audiobook, api.build_audiobook),
        (resolve_output, api.resolve_output),
        (preflight_profile, api.preflight_profile),
        (BookExportContext, api.BookExportContext),
        (BookExportResult, api.BookExportResult),
        (ComposedChapter, api.ComposedChapter),
        (ComposedDocument, api.ComposedDocument),
        (DocumentExportContext, api.DocumentExportContext),
        (DocumentExportResult, api.DocumentExportResult),
        (export_book, api.export_book),
        (export_document, api.export_document),
        (resolve_book_export, api.resolve_book_export),
        (resolve_document_export, api.resolve_document_export),
    )
    assert all(root_symbol is api_symbol for root_symbol, api_symbol in public_pairs)
    assert AudiobookTrack.__name__ == "AudiobookTrack"
    assert AudiobookMetadata.__name__ == "AudiobookMetadata"

    public_symbols = {
        "ResolvedOutput",
        "resolve_output",
        "preflight_profile",
        "BookExportContext",
        "BookExportResult",
        "ComposedChapter",
        "ComposedDocument",
        "DocumentExportContext",
        "DocumentExportResult",
        "export_book",
        "export_document",
        "resolve_book_export",
        "resolve_document_export",
    }
    assert public_symbols.issubset(audioexport.__all__)
    assert public_symbols.issubset(api.__all__)
