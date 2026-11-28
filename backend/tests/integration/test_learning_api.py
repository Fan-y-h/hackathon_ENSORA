from datetime import timedelta

from sqlalchemy import func, select

from app.modules.learning.models import Evidence
from tests.conftest import NOW, clock_of

API = "/api/v1"
QUIZ = {
    "title": "Stat — ch.1",
    "subject_id": "stat",
    "questions": [
        {"type": "single_choice", "prompt": "Estimateur sans biais ?", "options": [{"id": "a", "text": "oui"}, {"id": "b", "text": "non"}],
         "answer": "a", "concept_ids": ["c-bias"], "explanation": "E[θ̂]=θ"},
        {"type": "multiple_choice", "prompt": "Lois continues", "options": [{"id": "n", "text": "Normale"}, {"id": "p", "text": "Poisson"}, {"id": "e", "text": "Expo"}],
         "answer": ["n", "e"], "concept_ids": ["c-laws"]},
        {"type": "true_false", "prompt": "Var ≥ 0", "answer": True, "concept_ids": ["c-var"], "dimension": "memory"},
        {"type": "fill_blank", "prompt": "Loi des grands ___", "answer": ["nombres"], "concept_ids": ["c-lln"]},
        {"type": "numeric", "prompt": "1/3 ≈ ?", "answer": {"value": "0.333", "tolerance": "0.001"}, "concept_ids": ["c-num"], "points": 2},
        {"type": "open", "prompt": "Expliquer le TCL", "answer": "rubric", "concept_ids": ["c-tcl"]},
    ],
}


def evidence_count(client) -> int:
    with client.app.state.db.session_factory() as s:
        return s.scalar(select(func.count()).select_from(Evidence))


def test_quiz_payloads_never_expose_answers(client):
    quiz = client.post(f"{API}/quizzes", json=QUIZ).json()
    assert quiz["question_count"] == 6 and quiz["total_points"] == 7
    attempt = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}).json()
    for payload in (quiz, client.get(f"{API}/quizzes/{quiz['id']}").json(), attempt):
        text = str(payload)
        assert "answer" not in text and "explanation" not in text and "nombres" not in text


def test_invalid_answer_keys_rejected(client):
    bad = {"title": "x", "questions": [
        {"type": "single_choice", "prompt": "?", "options": [{"id": "a", "text": "a"}, {"id": "b", "text": "b"}], "answer": "z"},
        {"type": "true_false", "prompt": "?", "answer": "yes"},
        {"type": "numeric", "prompt": "?", "answer": {"value": "abc"}},
    ]}
    r = client.post(f"{API}/quizzes", json=bad)
    assert r.status_code == 422 and len(r.json()["error"]["details"]) == 3
    assert client.post(f"{API}/quizzes", json=QUIZ | {"type": "exam"}).status_code == 422  # needs time limit


def test_practice_attempt_scoring_evidence_and_idempotent_submit(client):
    quiz = client.post(f"{API}/quizzes", json=QUIZ).json()
    q = {x["type"]: x["id"] for x in quiz["questions"]}
    attempt = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}).json()
    r = client.patch(f"{API}/attempts/{attempt['id']}/responses", json={"responses": [
        {"question_id": q["single_choice"], "response": "a"},
        {"question_id": q["multiple_choice"], "response": ["n"]},  # incomplete set → 0
        {"question_id": q["true_false"], "response": True, "assisted": True},
        {"question_id": q["fill_blank"], "response": "  Nombres "},
        {"question_id": q["numeric"], "response": "0,3334"},
        {"question_id": q["open"], "response": "Somme de v.a. ..."},
    ]})
    assert r.status_code == 200
    assert client.get(f"{API}/attempts/{attempt['id']}/result").status_code == 403  # hidden while active

    res = client.post(f"{API}/attempts/{attempt['id']}/submit").json()
    assert res["score"] == 5 and res["max_score"] == 7 and res["pending_review"] == 1
    assert res["ratio"] == round(5 / 6, 10) or abs(res["ratio"] - 5 / 6) < 1e-9
    items = {i["question_id"]: i for i in res["items"]}
    assert items[q["single_choice"]]["correct_answer"] == "a"  # revealed only after submit
    assert items[q["open"]]["status"] == "pending_review"
    assert len(res["mastery_updates"]) == 5  # open answer creates no evidence
    n = evidence_count(client)
    assert n == 5

    again = client.post(f"{API}/attempts/{attempt['id']}/submit").json()
    assert again["score"] == 5 and again["mastery_updates"] == [] and evidence_count(client) == n
    assert client.patch(f"{API}/attempts/{attempt['id']}/responses", json={"responses": [
        {"question_id": q["single_choice"], "response": "b"}]}).status_code == 409

    mastery = {(m["concept_id"]): m for m in client.get(f"{API}/mastery").json()}
    assert mastery["c-bias"]["score"] == 1.0 and mastery["c-bias"]["status"] == "weak"  # 1 evidence only
    assert mastery["c-laws"]["score"] == 0.0 and mastery["c-laws"]["status"] == "learning"
    assert mastery["c-laws"]["interval_days"] == 1
    assert mastery["c-var"]["confidence"] == 0.1  # assisted → half weight
    assert "c-tcl" not in mastery  # unknown ≠ zero: no row at all


def test_mastery_ema_and_intervals_over_two_attempts(client):
    quiz = client.post(f"{API}/quizzes", json={"title": "t", "subject_id": "stat", "questions": [QUIZ["questions"][0]]}).json()
    qid = quiz["questions"][0]["id"]
    for answer in ("a", "b"):
        a = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}).json()
        client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [{"question_id": qid, "response": answer}]})
        client.post(f"{API}/attempts/{a['id']}/submit")
    m = client.get(f"{API}/mastery", params={"concept_id": "c-bias"}).json()[0]
    assert abs(m["score"] - 0.65) < 1e-9  # 1.0 then (1−0.35)·1 + 0.35·0
    assert m["evidence_count"] == 2 and m["interval_days"] == 1  # wrong answer → Again


def test_exam_mode_timer_is_server_side(client):
    quiz = client.post(f"{API}/quizzes", json=QUIZ | {"type": "exam", "time_limit_min": 30}).json()
    assert client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={"mode": "practice"}).status_code == 422
    a = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={"mode": "exam"}).json()
    qid = quiz["questions"][0]["id"]
    assisted = {"responses": [{"question_id": qid, "response": "a", "assisted": True}]}
    assert client.patch(f"{API}/attempts/{a['id']}/responses", json=assisted).status_code == 422
    client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [{"question_id": qid, "response": "a"}]})
    clock_of(client).set(NOW + timedelta(minutes=31))
    late = client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [{"question_id": qid, "response": "b"}]})
    assert late.status_code == 409 and late.json()["error"]["code"] == "attempt_expired"
    res = client.get(f"{API}/attempts/{a['id']}/result").json()
    assert res["status"] == "expired" and res["score"] == 1  # saved answer kept, unanswered exam items count 0


def test_flashcard_reviews_intervals_idempotency_and_due(client):
    deck = client.post(f"{API}/decks", json={"title": "Formules", "subject_id": "stat"}).json()
    card = client.post(f"{API}/decks/{deck['id']}/cards", json={"front": "Var(X)", "back": "E[X²]−E[X]²", "concept_id": "c-var"}).json()
    assert card["is_due"] and client.get(f"{API}/decks").json()[0]["due_count"] == 1
    assert len(client.get(f"{API}/reviews/due").json()) == 1

    h = {"Idempotency-Key": "rev-1"}
    r1 = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "good"}, headers=h).json()
    assert r1["interval_days"] == 2 and r1["mastery_updates"][0]["dimension"] == "memory"
    replay = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "good"}, headers=h).json()
    assert replay["replayed"] and replay["review_id"] == r1["review_id"]
    mismatch = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "easy"}, headers=h)
    assert mismatch.status_code == 409

    r2 = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "good"}).json()
    assert r2["interval_days"] == 4
    r3 = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "again"}).json()
    assert r3["interval_days"] == 1
    assert client.get(f"{API}/reviews/due").json() == []
    assert evidence_count(client) == 3


def test_study_needs_from_weak_concepts_and_due_reviews(client):
    quiz = client.post(f"{API}/quizzes", json={"title": "t", "subject_id": "stat", "questions": [QUIZ["questions"][0]]}).json()
    a = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}).json()
    client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [{"question_id": quiz["questions"][0]["id"], "response": "b"}]})
    client.post(f"{API}/attempts/{a['id']}/submit")
    needs = client.get(f"{API}/study-needs", params={"horizon_days": 7}).json()
    review = [n for n in needs if n["kind"] == "review"]
    assert review and review[0]["concept_ids"] == ["c-bias"] and review[0]["reason"] == "REVIEW_DUE"
    assert review[0]["earliest_at"] == (NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z")


def test_learning_isolation(client, as_bob):
    quiz = client.post(f"{API}/quizzes", json=QUIZ).json()
    deck = client.post(f"{API}/decks", json={"title": "d"}).json()
    assert client.get(f"{API}/quizzes/{quiz['id']}", headers=as_bob).status_code == 404
    assert client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}, headers=as_bob).status_code == 404
    assert client.get(f"{API}/decks/{deck['id']}/cards", headers=as_bob).status_code == 404
    a = client.post(f"{API}/quizzes/{quiz['id']}/attempts", json={}).json()
    assert client.post(f"{API}/attempts/{a['id']}/submit", headers=as_bob).status_code == 404


def test_learning_numeric_comma_dedupe_and_invalid_responses(client):
    # French decimal comma in numeric answer key must score cleanly without 500 crash
    q = client.post(
        f"{API}/quizzes",
        json={
            "title": "Calcul numérique",
            "subject_id": "stat",
            "questions": [
                {
                    "type": "numeric",
                    "prompt": "1/3 ≈ ?",
                    "answer": {"value": "0,333", "tolerance": "0,01"},
                    "concept_ids": ["c-comma"],
                },
                {
                    "type": "single_choice",
                    "prompt": "Choix ?",
                    "options": [{"id": "a", "text": "A"}, {"id": "b", "text": "B"}],
                    "answer": "a",
                    "concept_ids": ["c-choice"],
                },
            ],
        },
    ).json()
    q_num, q_sc = q["questions"][0]["id"], q["questions"][1]["id"]
    a = client.post(f"{API}/quizzes/{q['id']}/attempts", json={}).json()

    # Invalid response shapes rejected with 422
    assert client.patch(
        f"{API}/attempts/{a['id']}/responses",
        json={"responses": [{"question_id": q_sc, "response": "nonexistent"}]},
    ).status_code == 422
    assert client.patch(
        f"{API}/attempts/{a['id']}/responses",
        json={"responses": [{"question_id": q_num, "response": "pas-un-nombre"}]},
    ).status_code == 422

    # Duplicate question_id in one PATCH deduplicates safely (last wins)
    r = client.patch(
        f"{API}/attempts/{a['id']}/responses",
        json={
            "responses": [
                {"question_id": q_sc, "response": "b"},
                {"question_id": q_sc, "response": "a"},
                {"question_id": q_num, "response": "0,334"},
            ]
        },
    )
    assert r.status_code == 200
    res = client.post(f"{API}/attempts/{a['id']}/submit").json()
    assert res["score"] == 2.0 and res["max_score"] == 2.0

    # Blank fill_blank answer keys & whitespace-only flashcards rejected
    assert client.post(
        f"{API}/quizzes",
        json={"title": "Bad", "questions": [{"type": "fill_blank", "prompt": "x", "answer": ["   "]}]},
    ).status_code == 422
    deck = client.post(f"{API}/decks", json={"title": "Deck", "subject_id": "stat"}).json()
    assert client.post(
        f"{API}/decks/{deck['id']}/cards", json={"front": "   ", "back": "ok"}
    ).status_code == 422

    # Due flashcard study needs carry concept_ids and real forgetting risk
    client.post(
        f"{API}/decks/{deck['id']}/cards",
        json={"front": "Gauss-Markov", "back": "BLUE", "concept_id": "c-gm"},
    )
    needs = client.get(f"{API}/study-needs", params={"horizon_days": 7}).json()
    fc_need = next(n for n in needs if n["kind"] == "flashcards")
    assert fc_need["concept_ids"] == ["c-gm"]
