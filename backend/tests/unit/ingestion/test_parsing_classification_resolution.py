"""Unit tests for modular document parsing, classification, entity resolution, and Codex Runtime bridge."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from backend.app.contracts.authentication import ConnectionStatus
from backend.app.contracts.sources import Document, Provenance, SourceKind as CodexSourceKind, SyncScope
from backend.app.core.runtime import Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.classification.classifier import DocumentClassifier
from backend.app.modules.ingestion.codex_bridge import (
    CodexClassifierBridge,
    CodexEntityMatcherBridge,
    CodexParserBridge,
    build_components,
)
from backend.app.modules.ingestion.connectors.local_import import LocalFileConnector
from backend.app.modules.ingestion.contracts import DocumentCategory, SourceKind, SourceRecord
from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry
from backend.app.modules.ingestion.pipeline import IngestionPipelineAdapter
from backend.app.modules.ingestion.resolution.entity_resolver import (
    AcademicEntityResolver,
    CanonicalSubject,
)


def _build_minimal_pdf(text_lines: list[str]) -> bytes:
    """Create a valid minimal single-page PDF with text operators."""
    ops = ["BT", "/F1 12 Tf", "72 720 Td"]
    for idx, line in enumerate(text_lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if idx > 0:
            ops.append("0 -16 Td")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode("ascii")
        + stream
        + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(buf))
        buf.extend(obj)
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(buf)


def _build_synthetic_pptx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "docProps/core.xml",
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Deep Learning Slides</dc:title></cp:coreProperties>',
        )
        zf.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree>"
            '<p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
            "<p:txBody><a:p><a:r><a:t>Chapitre 1 : Backpropagation</a:t></a:r></a:p></p:txBody></p:sp>"
            "<p:sp><p:txBody><a:p><a:r><a:t>Gradient formula: $\\nabla L(\\theta)$</a:t></a:r></a:p></p:txBody></p:sp>"
            "<a:tbl><a:tr><a:tc><a:t>Layer</a:t></a:tc><a:tc><a:t>Activation</a:t></a:tc></a:tr>"
            "<a:tr><a:tc><a:t>Dense</a:t></a:tc><a:tc><a:t>ReLU</a:t></a:tc></a:tr></a:tbl>"
            "</p:spTree></p:cSld></p:sld>",
        )
        zf.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Explain chain rule carefully.</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>",
        )
        zf.writestr("ppt/media/image1.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


def _build_synthetic_docx() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body>"
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>TD 2 - Estimateurs</w:t></w:r></w:p>'
            "<w:p><w:r><w:t>Soit $X_1, ..., X_n$ i.i.d.</w:t></w:r></w:p>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Estimateur</w:t></w:r></w:p></w:tc>"
            "<w:tc><w:p><w:r><w:t>Biais</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "</w:body></w:document>",
        )
    return buf.getvalue()


def test_document_parser_registry_all_formats() -> None:
    registry = DocumentParserRegistry()

    # 1. PDF (valid + malformed)
    pdf_bytes = _build_minimal_pdf(
        [
            "Chapitre 1 : Estimation statistique",
            "Formula: $E[X] = mu$",
            "Figure 1: Convergence plot",
        ]
    )
    parsed_pdf = registry.parse_bytes(pdf_bytes, filename="CM1_Stat.pdf", mime_type="application/pdf")
    assert parsed_pdf.error is None
    assert parsed_pdf.extraction_quality > 0.5
    assert parsed_pdf.blocks[0].locator.page == 1
    assert any("E[X] = mu" in m for m in parsed_pdf.math_expressions)

    bad_pdf = registry.parse_bytes(b"NOT_A_PDF", filename="broken.pdf", mime_type="application/pdf")
    assert bad_pdf.error is not None
    assert bad_pdf.extraction_quality == 0.0

    # 2. Jupyter Notebook (inert, preserves indentation & language, NEVER executes code)
    nb_payload = {
        "nbformat": 4,
        "metadata": {"language_info": {"name": "python"}},
        "cells": [
            {"cell_type": "markdown", "source": ["# TP1 Gradient Descent\n", "Minimize $$f(x) = x^2$$"]},
            {
                "cell_type": "code",
                "source": ["def step(x):\n", "    if x > 0:\n", "        return x - 0.1\n", "    raise SystemExit('MUST NOT EXECUTE')\n"],
                "outputs": [
                    {"output_type": "stream", "text": ["loss=0.01\n"]},
                    {"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo="}},
                ],
            },
        ],
    }
    parsed_nb = registry.parse_bytes(
        json.dumps(nb_payload).encode("utf-8"), filename="TP1_GD.ipynb"
    )
    assert parsed_nb.error is None
    assert parsed_nb.headings == ("TP1 Gradient Descent",)
    code_block = next(b for b in parsed_nb.blocks if b.kind == "code")
    assert code_block.code_language == "python"
    assert code_block.locator.cell == 2
    assert "        return x - 0.1" in code_block.text  # indentation preserved!
    assert any(b.kind == "output" and "loss=0.01" in b.text for b in parsed_nb.blocks)
    assert len(parsed_nb.figures) == 1

    # 3. PPTX (slides, title, speaker notes, table, media figure)
    parsed_pptx = registry.parse_bytes(_build_synthetic_pptx(), filename="DL_Cours.pptx")
    assert parsed_pptx.error is None
    assert parsed_pptx.title == "Deep Learning Slides"
    assert parsed_pptx.blocks[0].locator.slide == 1
    assert any(b.kind == "note" and "chain rule" in b.text for b in parsed_pptx.blocks)
    assert len(parsed_pptx.tables) == 1
    assert len(parsed_pptx.figures) == 1

    # 4. DOCX (headings, math, table)
    parsed_docx = registry.parse_bytes(_build_synthetic_docx(), filename="TD2.docx")
    assert parsed_docx.error is None
    assert "TD 2 - Estimateurs" in parsed_docx.headings
    assert len(parsed_docx.tables) == 1

    # 5. Markdown & sanitized HTML & Python source code & unsupported format
    md = b"# Fiche Resume\n\n```python\ndef f(x):\n    return x * 2\n```\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"
    parsed_md = registry.parse_bytes(md, filename="fiche.md")
    assert parsed_md.headings == ("Fiche Resume",)
    assert any(b.kind == "code" and "    return x * 2" in b.text for b in parsed_md.blocks)
    assert len(parsed_md.tables) == 1

    html = b"<html><head><title>Page</title><script>alert('xss')</script></head><body><h1>Intro</h1><p>Safe text</p></body></html>"
    parsed_html = registry.parse_bytes(html, filename="page.html")
    assert "alert" not in parsed_html.full_text
    assert "Safe text" in parsed_html.full_text

    py_src = b"def solve(a):\n    return a + 1\n"
    parsed_py = registry.parse_bytes(py_src, filename="solver.py")
    assert parsed_py.blocks[0].code_language == "python"
    assert parsed_py.blocks[0].locator.line_start == 1

    unsup = registry.parse_bytes(b"\x00\x01\x02", filename="archive.bin")
    assert unsup.error is not None
    assert unsup.unsupported_elements


def test_classifier_all_categories_and_semantic_hook() -> None:
    clf = DocumentClassifier()
    cases = [
        ("CM1_Regression.pdf", "Ensora/2A/ML/Cours", DocumentCategory.LECTURE),
        ("TD3_Exercices.pdf", "Moodle/Stat", DocumentCategory.TUTORIAL),
        ("TP2_Spark.pdf", "Moodle/BigData", DocumentCategory.PRACTICAL_WORK),
        ("Partiel_ML_2024.pdf", "Ensora/2A/ML/Annales", DocumentCategory.EXAMINATION),
        ("Corrige_Partiel_ML_2024.pdf", "Ensora/2A/ML/Annales", DocumentCategory.EXAMINATION_CORRECTION),
        ("Projet_Consignes_DM1.pdf", "Moodle/ID", DocumentCategory.ASSIGNMENT),
        ("Fiche_Synthese_Proba.pdf", "Ensora/1A/Proba", DocumentCategory.COURSE_SUMMARY),
        ("Exploration.ipynb", "Moodle/ML", DocumentCategory.NOTEBOOK),
        ("kmeans.py", "Local/code", DocumentCategory.SOURCE_CODE),
        ("Reglement_Scolarite_2026.pdf", "Ensora/Global", DocumentCategory.ADMINISTRATIVE),
        ("misc_notes_file.txt", "Local", DocumentCategory.OTHER),
    ]
    for fname, folder, expected in cases:
        res = clf.classify(title=fname, filename=fname, folder_path=folder)
        assert res.category == expected, f"Expected {expected} for {fname}, got {res.category}"
        assert res.confidence > 0.0
        assert len(res.evidence) >= 1

    # Optional P2 semantic hook boosts low-confidence fallback
    class _FakeSemantic:
        def classify_text(self, *, title: str, excerpt: str, metadata: dict):
            return (DocumentCategory.COURSE_SUMMARY, 0.85, ("P2 semantic match",))

    hybrid_clf = DocumentClassifier(semantic_classifier=_FakeSemantic())
    h_res = hybrid_clf.classify(title="notes_diverses.txt", filename="notes_diverses.txt")
    assert h_res.category == DocumentCategory.COURSE_SUMMARY
    assert h_res.method == "hybrid_semantic"


def test_entity_resolution_aliases_overrides_and_exam_correction_pairing() -> None:
    resolver = AcademicEntityResolver(
        subjects=[
            CanonicalSubject(
                subject_id="subj_ppdp",
                title="Privacy-Preserving Data Publishing",
                code="PPDP",
                aliases=("PPDP", "Confidentialite des donnees"),
                cohort="2A",
                semester="S4",
                specialization="ID",
                teaching_unit="UE Data Governance",
            ),
            CanonicalSubject(
                subject_id="subj_ml",
                title="Machine Learning",
                code="ML2A",
                aliases=("Apprentissage Automatique", "ML"),
                cohort="2A",
                semester="S4",
                teaching_unit="UE Data Science",
            ),
            CanonicalSubject(
                subject_id="subj_stat1",
                title="Statistique Mathematique",
                code="STAT1",
                cohort="1A",
            ),
            CanonicalSubject(
                subject_id="subj_stat2",
                title="Statistique Mathematique Avancee",
                code="STAT2",
                cohort="2A",
            ),
        ],
        current_academic_year="2026-2027",
    )

    rec_exam = SourceRecord(
        source_kind=SourceKind.ENSORA_DRIVE,
        external_id="ensora_drive:ex2024",
        title="Partiel_PPDP_2023-2024.pdf",
        folder_path="Ensora/2A/ID/PPDP/Annales",
        content_hash="hash_exam_1",
    )
    rec_corr = SourceRecord(
        source_kind=SourceKind.ENSORA_DRIVE,
        external_id="ensora_drive:corr2024",
        title="Corrige_Partiel_PPDP_2023-2024.pdf",
        folder_path="Ensora/2A/ID/PPDP/Annales",
        content_hash="hash_corr_1",
    )
    rec_dup = SourceRecord(
        source_kind=SourceKind.MOODLE,
        external_id="moodle:course:1:resource:9",
        title="PPDP_Annale_Copie.pdf",
        content_hash="hash_exam_1",
    )

    res_exam = resolver.resolve_record(rec_exam)
    res_corr = resolver.resolve_record(rec_corr)
    res_dup = resolver.resolve_record(rec_dup)

    assert res_exam.canonical_subject_id == "subj_ppdp"
    assert res_exam.needs_review is False
    assert res_exam.historical_year == "2023-2024"
    assert res_exam.academic_year == "2026-2027"
    assert res_exam.cohort == "2A"
    assert res_exam.specialization == "ID"
    assert res_dup.duplicate_of_external_id == "ensora_drive:ex2024"

    # Exam & correction pairing
    pairs = resolver.match_exams_and_corrections(
        [
            (rec_exam, DocumentCategory.EXAMINATION, res_exam),
            (rec_corr, DocumentCategory.EXAMINATION_CORRECTION, res_corr),
        ]
    )
    assert len(pairs) == 1
    assert pairs[0].exam_external_id == "ensora_drive:ex2024"
    assert pairs[0].correction_external_id == "ensora_drive:corr2024"
    assert pairs[0].needs_review is False

    # Manual override preserved and never overwritten
    resolver.manual_store.set_subject_override(rec_exam.external_id, "subj_ml", chapter="Chapter 3")
    res_override = resolver.resolve_record(rec_exam)
    assert res_override.canonical_subject_id == "subj_ml"
    assert res_override.matched_by == "manual_override"
    assert res_override.chapter == "Chapter 3"


def test_pipeline_and_codex_runtime_integration(tmp_path: Path) -> None:
    # 1. Standalone IngestionPipelineAdapter run on local synthetic folder
    docs_dir = tmp_path / "docs_in" / "2A" / "ML"
    docs_dir.mkdir(parents=True)
    (docs_dir / "Partiel_ML_2024.pdf").write_bytes(_build_minimal_pdf(["Partiel ML 2024", "Exercice 1"]))
    (docs_dir / "Corrige_Partiel_ML_2024.pdf").write_bytes(
        _build_minimal_pdf(["Corrige Partiel ML 2024", "Elements de correction"])
    )

    resolver = AcademicEntityResolver(
        [CanonicalSubject(subject_id="ml", title="Machine Learning", code="ML", aliases=("ML",))]
    )
    pipeline = IngestionPipelineAdapter(resolver=resolver)
    batch = pipeline.process_connector_page(LocalFileConnector(), scope={"root_path": tmp_path / "docs_in"})
    assert len(batch.documents) == 2
    assert len(batch.exam_correction_matches) == 1

    # 2. Direct integration with Codex's Runtime + Pipeline + SyncEngine via build_components!
    settings = Settings(
        config_dir=tmp_path / "cfg",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "cfg" / "sessions" / "ensai",
        google_credentials_path=tmp_path / "cfg" / "secrets" / "google_credentials.json",
        google_token_path=tmp_path / "cfg" / "secrets" / "google_token.json",
        adapter_factory="backend.app.modules.ingestion.codex_bridge:build_components",
    )
    with Runtime(settings) as runtime:
        # Grant read consent for LOCAL source and run Codex's SyncEngine end-to-end!
        runtime.consent.record(CodexSourceKind.LOCAL, "read", True)
        auth_res = runtime.auth.validate(CodexSourceKind.LOCAL)
        assert auth_res.status == ConnectionStatus.CONNECTED

        # Verify parser, classifier, and matcher bridges work cleanly inside Codex Pipeline
        doc = Document(
            provenance=Provenance(external_id="local:Partiel_ML_2024.pdf", folder="2A/ML"),
            title="Partiel_ML_2024.pdf",
            mime_type="application/pdf",
        )
        parsed = CodexParserBridge().parse(doc, _build_minimal_pdf(["Partiel ML"]))
        assert parsed is not None and len(parsed.chunks) == 1
        clf = CodexClassifierBridge().classify(doc, parsed)
        assert clf.resource_type == "examination"
        match = CodexEntityMatcherBridge(resolver).match(doc, clf)
        assert match.subject_id == "ml" and match.confirmed is True

        # Run Codex's real SyncEngine against our bridged LocalFileConnector!
        run_id = runtime.sync.submit(SyncScope(source=CodexSourceKind.LOCAL, folder_id=str(tmp_path / "docs_in")))
        # Wait for Codex's sync job to reach a terminal status
        import time
        from sqlalchemy import func, select
        from backend.app.core.database import documents as db_documents, runs as db_runs

        deadline = time.monotonic() + 5.0
        status = None
        while time.monotonic() < deadline:
            with runtime.engine.connect() as db:
                status = db.scalar(select(db_runs.c.status).where(db_runs.c.id == run_id))
            if status in ("succeeded", "partial", "failed", "cancelled"):
                break
            time.sleep(0.05)

        with runtime.engine.connect() as db:
            count = db.scalar(select(func.count()).select_from(db_documents))
        assert run_id is not None
        assert status == "succeeded"
        assert count == 2


def test_unified_browser_lock_unscoped_local_and_default_factory_api(tmp_path: Path) -> None:
    """Verify Codex reconciliation: shared `.ensora.lock` lease, unscoped LOCAL safety, and FastAPI wiring."""
    import asyncio
    import pytest
    from httpx import ASGITransport, AsyncClient

    from backend.app.contracts.authentication import ProfileBusy
    from backend.app.core.security import LockedBrowserSession
    from backend.app.main import create_app
    from backend.app.modules.ingestion.auth.ensai import (
        BrowserProfileLock,
        BrowserProfileLockError,
        EnsaiAuthAdapter,
    )
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge, CodexEnsaiAuthBridge
    from backend.app.modules.ingestion.config import SourcesRuntimeConfig
    from backend.app.modules.ingestion.credentials import InMemoryCredentialStore, SecureCredentialManager

    # 1. Unified `.ensora.lock` mutual exclusion between Codex LockedBrowserSession & Gemini BrowserProfileLock
    profile_dir = tmp_path / "sessions" / "ensai"
    codex_sessions = LockedBrowserSession(profile_dir)
    with codex_sessions.lease():
        with pytest.raises(BrowserProfileLockError):
            BrowserProfileLock(profile_dir, timeout_sec=0.05).acquire()

    with BrowserProfileLock(profile_dir, timeout_sec=0.05):
        with pytest.raises(ProfileBusy), codex_sessions.lease():
            pytest.fail("LockedBrowserSession must block when BrowserProfileLock holds .ensora.lock")

    # 2. CodexEnsaiAuthBridge propagates ProfileBusy directly when shared session is already leased
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensai_profile_dir = profile_dir
    profile_dir.mkdir(parents=True, exist_ok=True)
    ensai_adapter = EnsaiAuthAdapter(
        cfg,
        SecureCredentialManager(InMemoryCredentialStore()),
        session_manager=codex_sessions,
    )
    bridge = CodexEnsaiAuthBridge(
        ensai_adapter,
        SourceKind.MOODLE,
        codex_sessions,
        SecureCredentialManager(InMemoryCredentialStore()),
    )
    with codex_sessions.lease():
        with pytest.raises(ProfileBusy):
            bridge.validate()

    # 3. Unscoped LOCAL sync returns empty complete page safely (excluded from unscoped/automatic runs)
    local_bridge = CodexConnectorBridge(LocalFileConnector())
    empty_page = local_bridge.list_changes(SyncScope(source=CodexSourceKind.LOCAL), None)
    assert empty_page.items == []
    assert empty_page.complete is True

    # 4. Codex FastAPI app boots cleanly using Codex's default auto-registered build_components factory
    settings = Settings(
        config_dir=tmp_path / "api_cfg",
        local_data_dir=tmp_path / "api_data",
        ensai_profile_dir=tmp_path / "api_cfg" / "sessions" / "ensai",
    )
    app = create_app(settings)

    async def _smoke() -> None:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                token = (settings.local_data_dir / "api-token").read_text()
                client.headers["Authorization"] = f"Bearer {token}"
                st = (await client.get("/api/v1/pipeline/status")).json()
                assert set(st["registered_sources"]) == {
                    "moodle",
                    "pamplemousse",
                    "ensora_drive",
                    "google_calendar",
                    "local",
                }
                # Upload a synthetic PDF via Codex API and verify Gemini's PdfDocumentParser chunks persist!
                conns = {
                    c["source"]: c["id"]
                    for c in (await client.get("/api/v1/connections")).json()["items"]
                }
                await client.put(
                    f"/api/v1/connections/{conns['local']}/consent",
                    json={"purpose": "read", "granted": True},
                )
                pdf_bytes = _build_minimal_pdf(["Chapitre 1 : Regression", "Formula: $y = X beta$"])
                up = await client.post(
                    "/api/v1/documents/import",
                    files={"file": ("CM1_Regression.pdf", pdf_bytes, "application/pdf")},
                )
                assert up.status_code == 201
                doc_row = (await client.get("/api/v1/documents")).json()["items"][0]
                assert doc_row["metadata_json"]["classification"]["resource_type"] == "lecture"
                ch = (
                    await client.get(
                        f"/api/v1/documents/{doc_row['id']}/versions/{doc_row['active_version_id']}/chunks"
                    )
                ).json()
                assert len(ch["items"]) >= 1
                assert ch["items"][0]["payload"]["locator"]["page"] == 1

    asyncio.run(_smoke())
