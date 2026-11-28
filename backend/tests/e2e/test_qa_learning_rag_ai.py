"""QA Section 7 — Document pipeline, RAG, AI, Learning (Quizzes/Flashcards/FSRS), Privacy & Exam Mode."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from backend.app.core.runtime import Components
from backend.app.core.settings import Settings as P1Settings
from tests.conftest import NOW, clock_of

API = "/api/v1"


def test_document_upload_parse_chunk_and_content_retrieval(tmp_path):
    p1_settings = P1Settings(
        config_dir=tmp_path / "config",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "config/sessions/ensai",
    )
    app = create_app(
        f"sqlite:///{(tmp_path / 'planner.db').as_posix()}",
        enable_sources=True,
        p1_settings=p1_settings,
        p1_components=Components(),
    )
    with TestClient(app) as tc:
        token = (p1_settings.local_data_dir / "api-token").read_text()
        headers = {"Authorization": f"Bearer {token}"}
        conns = {c["source"]: c for c in tc.get("/api/v1/sources/connections", headers=headers).json()["items"]}
        loc_id = conns["local"]["id"]
        tc.put(f"/api/v1/sources/connections/{loc_id}/consent", json={"purpose": "read", "granted": True}, headers=headers)
        tc.post(f"/api/v1/sources/connections/{loc_id}/authorize", headers=headers)

        md = b"# Econometrie\n\nSous les hypotheses de Gauss-Markov, l'estimateur MCO est BLUE (Best Linear Unbiased Estimator).\n"
        up = tc.post(
            "/api/v1/sources/documents/import",
            files={"file": ("econometrie_ch1.md", md, "text/markdown")},
            headers=headers,
        )
        assert up.status_code == 201
        docs = tc.get("/api/v1/sources/documents", headers=headers).json()["items"]
        assert len(docs) == 1
        doc = docs[0]
        chunks = tc.get(
            f"/api/v1/sources/documents/{doc['id']}/versions/{doc['active_version_id']}/chunks",
            headers=headers,
        ).json()["items"]
        assert chunks, "Parsed document must produce indexed chunks"
        assert any("Gauss-Markov" in str(c["payload"].get("text", "")) for c in chunks)


def test_learning_flashcards_fsrs_quiz_mastery_and_user_isolation(client, as_bob):
    # Enable FSRS-4.5 in profile
    assert client.patch(f"{API}/profile", json={"preferences": {"repetition_algorithm": "fsrs", "desired_retention": 0.9}}).status_code == 200

    # Deck + Card + FSRS review with Idempotency-Key
    deck = client.post(f"{API}/decks", json={"title": "Statistiques", "subject_id": "stat"}).json()
    card_resp = client.post(f"{API}/decks/{deck['id']}/cards", json={
        "front": "Estimateur BLUE ?", "back": "MCO sous Gauss-Markov", "concept_id": "c-gm",
    })
    assert card_resp.status_code == 201, card_resp.text
    card = card_resp.json()
    rev1 = client.post(
        f"{API}/cards/{card['id']}/review",
        json={"rating": "good"},
        headers={"Idempotency-Key": "qa-review-1"},
    ).json()
    rev1_dup = client.post(
        f"{API}/cards/{card['id']}/review",
        json={"rating": "good"},
        headers={"Idempotency-Key": "qa-review-1"},
    ).json()
    assert rev1["review_id"] == rev1_dup["review_id"]
    assert rev1_dup["replayed"] is True
    assert "fsrs" in rev1["algorithm"]

    # Quiz + exam mode hides answers until submit + updates mastery
    quiz_resp = client.post(f"{API}/quizzes", json={
        "title": "Partiel blanc",
        "subject_id": "stat",
        "time_limit_min": 30,
        "questions": [
            {
                "type": "single_choice",
                "prompt": "MCO est ?",
                "options": [{"id": "a", "text": "BLUE"}, {"id": "b", "text": "Biaisé"}],
                "answer": "a",
                "concept_ids": ["c-gm"],
            },
            {
                "type": "numeric",
                "prompt": "2 + 2 ?",
                "answer": {"value": 4, "tolerance": 0.01},
                "concept_ids": ["c-calc"],
            },
        ],
    })
    assert quiz_resp.status_code == 201, quiz_resp.text
    quiz = quiz_resp.json()
    # Answers never exposed in quiz GET
    q_get = client.get(f"{API}/quizzes/{quiz['id']}").json()
    assert all("answer" not in q for q in q_get["questions"])

    att = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={"mode": "exam"}).json()
    assert client.get(f"{API}/exam-mode").json()["active"] is True
    # Cannot read result before submit
    assert client.get(f"{API}/attempts/{att['id']}/result").status_code in (400, 403, 409)

    client.patch(f"{API}/attempts/{att['id']}/responses", json={
        "responses": [
            {"question_id": quiz["questions"][0]["id"], "response": "a"},
            {"question_id": quiz["questions"][1]["id"], "response": 5},  # wrong
        ]
    })
    res = client.post(f"{API}/attempts/{att['id']}/submit").json()
    assert res["score"] == 1.0 and res["max_score"] == 2.0
    assert client.get(f"{API}/exam-mode").json()["active"] is False

    # Mastery reflects evidence (c-gm > c-calc)
    mastery = {m["concept_id"]: m for m in client.get(f"{API}/mastery").json()}
    assert mastery["c-gm"]["score"] > mastery["c-calc"]["score"]

    # User isolation: Bob cannot see Alice's decks, quizzes, mastery, or notes
    nb = client.post(f"{API}/notebooks", json={"title": "Cahier Alice"}).json()
    note_resp = client.post(f"{API}/notes", json={"notebook_id": nb["id"], "title": "Secret Alice", "markdown": "Privé"})
    assert note_resp.status_code == 201, note_resp.text
    note = note_resp.json()
    assert client.get(f"{API}/decks", headers=as_bob).json() == []
    assert client.get(f"{API}/quizzes", headers=as_bob).json() == []
    assert client.get(f"{API}/mastery", headers=as_bob).json() == []
    assert client.get(f"{API}/notes/{note['id']}", headers=as_bob).status_code == 404


def test_qa_repro_exam_mode_blocks_notes_on_backend(client):
    nb = client.post(f"{API}/notebooks", json={"title": "Cahier"}).json()
    client.post(f"{API}/notes", json={"notebook_id": nb["id"], "title": "Antiseche", "markdown": "Formules"})
    quiz = client.post(f"{API}/quizzes", json={
        "title": "Examen chronométré", "time_limit_min": 60,
        "questions": [{"type": "true_false", "prompt": "1=1", "answer": True}],
    }).json()
    client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={"mode": "exam"})
    assert client.get(f"{API}/exam-mode").json()["active"] is True
    # Must return 403 exam_mode_active while exam is running!
    blocked = client.get(f"{API}/notes")
    assert blocked.status_code == 403


def test_qa_repro_rag_and_ai_endpoints_mounted(client):
    assert client.get(f"{API}/search", params={"q": "Gauss-Markov"}).status_code == 200
    assert client.get(f"{API}/ai/providers").status_code == 200
    assert client.post(f"{API}/tutor/answer", json={"question": "Explique Gauss-Markov"}).status_code == 200
    assert client.post(f"{API}/artifacts/generate", json={"type": "revision_sheet"}).status_code == 200


def test_qa_repro_numeric_quiz_french_comma_does_not_crash(client):
    quiz = client.post(f"{API}/quizzes", json={
        "title": "Calcul proba",
        "questions": [
            {"type": "numeric", "prompt": "P(pile) ?", "answer": {"value": "0,5", "tolerance": "0,01"}},
        ],
    })
    assert quiz.status_code == 201, quiz.text
    q = quiz.json()
    att = client.post(f"{API}/quizzes/{q['id']}/attempts", json={"mode": "practice"}).json()
    client.patch(f"{API}/attempts/{att['id']}/responses", json={
        "responses": [{"question_id": q["questions"][0]["id"], "response": "0,5"}]
    })
    sub = client.post(f"{API}/attempts/{att['id']}/submit")
    assert sub.status_code == 200 and sub.json()["score"] == 1.0
