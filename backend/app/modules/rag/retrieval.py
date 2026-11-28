"""Hybrid lexical (FTS5 BM25) + dense semantic retrieval with RRF, reranking, and diversity.

Implements ``docs/RAG_ARCHITECTURE.md``:
- Safe FTS5 query escaping preserving formulas and code identifiers.
- Pre-filtering by live authorization, academic year, subject, chapter, resource type,
  source, document IDs, and exam-mode solution exclusion BEFORE scoring.
- Reciprocal Rank Fusion ``sum(1 / (60 + rank))`` with deterministic ``chunk_id`` tie-break.
- Near-duplicate suppression, per-document diversity capping, deterministic academic
  cross-feature reranking (formula/code match, heading overlap, syllabus authority),
  neighbor-chunk expansion for proof/code continuity, and calibrated evidence status.
"""

from __future__ import annotations

from dataclasses import replace
from heapq import nsmallest
import json
import re
import sqlite3
import time
import unicodedata
from typing import Sequence

from .contracts import (
    Citation,
    EvidenceStatus,
    QueryScope,
    Reranker,
    RetrievalResult,
    RetrievedChunk,
)
from .embeddings import _stem_token, cosine_similarity_normalized
from .store import LocalRagIndexStore

RRF_K = 60
_QUERY_STOPWORDS = frozenset(
    {
        "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou", "en", "dans", "pour",
        "sur", "avec", "est", "sont", "que", "qui", "quoi", "quel", "quelle", "comment",
        "pourquoi", "explique", "expliquer", "donne", "donner", "define", "explain", "what",
        "how", "why", "the", "and", "for", "with", "this", "that", "from", "are", "is", "of",
        "to", "in", "on", "a", "an", "me", "moi", "ce", "cette", "ces", "au", "aux", "par",
    }
)
# Generic academic structural words excluded from distinctive abstention coverage so
# out-of-scope queries like "quantum chromodynamics confinement theorem" do not falsely
# pass evidence gating merely because a math lecture contains the word "theorem".
_GENERIC_ACADEMIC_TERMS = frozenset(
    {
        "theorem", "theorems", "theoreme", "theoremes",
        "definition", "definitions", "proposition", "propositions",
        "lemma", "lemme", "corollary", "corollaire",
        "example", "examples", "exemple", "exemples",
        "formula", "formulas", "formule", "formules",
        "exercise", "exercises", "exercice", "exercices",
        "course", "cours", "chapter", "chapitre", "section",
        "method", "methods", "methode", "methodes",
        "proof", "preuve", "demonstration", "property", "propriete",
        "principle", "principe", "problem", "probleme",
    }
)
_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_àâäéèêëîïôöùûüç\-]+")
_CODE_OR_MATH_RE = re.compile(
    r"(?:\\[a-zA-Z]+|[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+|[A-Za-z_][A-Za-z0-9_]*\([^)]*\)|[∀∃∈∉⊂⊆∪∩∑∏∫√∞≈≠≤≥±×÷→⇒⇔λμσθβαγΩΔ∇])"
)

# Conservative bilingual expansion for sparse-hit query reformulation (French <-> English)
_BILINGUAL_QUERY_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "econometrie": ("econometrics", "regression", "ols", "mco"),
    "econometrics": ("econometrie", "regression", "mco", "ols"),
    "ols": ("moindres", "carres", "mco"),
    "mco": ("moindres", "carres", "ols"),
    "biais": ("bias", "estimateur"),
    "bias": ("biais", "estimator"),
    "estimateur": ("estimator", "vraisemblance", "mle"),
    "estimator": ("estimateur", "likelihood"),
    "stationnarite": ("stationarity", "arima", "temporelle"),
    "stationarity": ("stationnarite", "arima", "series"),
    "sondage": ("sampling", "horvitz", "thompson"),
    "sampling": ("sondage", "echantillon"),
    "anonymat": ("anonymity", "confidentialite", "privacy"),
    "anonymity": ("anonymat", "k-anonymity", "confidentialite"),
    "jointure": ("join", "sql"),
    "join": ("jointure", "sql"),
    "gradient": ("descente", "optimisation", "backpropagation"),
    "vraisemblance": ("likelihood", "mle"),
    "likelihood": ("vraisemblance", "estimateur"),
}


def _fold_ascii(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def normalize_query(query: str) -> str:
    """Normalize whitespace while preserving mathematical symbols and code identifiers."""
    return " ".join((query or "").strip().split())


def extract_query_terms(query: str) -> list[str]:
    """Extract distinctive folded terms for scoring and coverage analysis."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _QUERY_TOKEN_RE.findall(query or ""):
        folded = _fold_ascii(raw).strip("-")
        if len(folded) < 2 or folded in _QUERY_STOPWORDS:
            continue
        if folded not in seen:
            seen.add(folded)
            terms.append(folded)
    return terms


def build_fts5_query(query: str, *, expand_bilingual: bool = False) -> str:
    """Build an injection-safe SQLite FTS5 ``MATCH`` expression from raw user text.

    Every token is double-quote escaped so FTS5 operators (``NOT``, ``NEAR``, ``*``,
    ``:``, ``^``, quotes) in math/code queries never cause syntax errors.
    """
    terms = extract_query_terms(query)
    if not terms:
        raw = normalize_query(query)
        if not raw:
            return ""
        safe = raw.replace('"', "")
        return f'"{safe}"' if safe else ""

    clauses: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        clean = tok.replace('"', "").strip()
        if not clean or clean in seen:
            return
        seen.add(clean)
        # Prefix match on >=5-char stems improves French/English plural & inflection recall
        if len(clean) >= 5 and clean.isalnum():
            clauses.append(f'"{clean}"*')
        else:
            clauses.append(f'"{clean}"')

    for t in terms[:14]:
        _add(t)
        st = _stem_token(t)
        if st != t and len(st) >= 4:
            _add(st)
        if expand_bilingual:
            for alt in _BILINGUAL_QUERY_EXPANSIONS.get(t, ()) or _BILINGUAL_QUERY_EXPANSIONS.get(st, ()):
                _add(alt)

    return " OR ".join(clauses)


def _row_to_chunk(
    row: sqlite3.Row,
    *,
    lexical_score: float = 0.0,
    semantic_score: float = 0.0,
    fused_score: float = 0.0,
    is_neighbor_expansion: bool = False,
) -> RetrievedChunk:
    section = tuple(str(s) for s in json.loads(row["section_json"] or "[]") if s)
    locator = json.loads(row["locator_json"] or "{}")
    structured = json.loads(row["structured_json"] or "{}")
    return RetrievedChunk(
        chunk_id=str(row["chunk_id"]),
        document_id=str(row["document_id"]),
        version_id=str(row["version_id"]),
        ordinal=int(row["ordinal"]),
        title=str(row["title"]),
        text=str(row["text"]),
        section=section,
        locator=locator,
        block_kind=str(row["block_kind"] or "paragraph"),
        structured=structured if isinstance(structured, dict) else {},
        source=str(row["source"] or ""),
        subject_id=str(row["subject_id"]) if row["subject_id"] else None,
        chapter_id=str(row["chapter_id"]) if row["chapter_id"] else None,
        academic_year=str(row["academic_year"]) if row["academic_year"] else None,
        resource_type=str(row["resource_type"]) if row["resource_type"] else None,
        source_url=str(row["source_url"]) if row["source_url"] else None,
        lexical_score=lexical_score,
        semantic_score=semantic_score,
        fused_score=fused_score,
        is_neighbor_expansion=is_neighbor_expansion,
    )


def _jaccard_tokens(a: str, b: str) -> float:
    sa = set(extract_query_terms(a))
    sb = set(extract_query_terms(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class AcademicCrossFeatureReranker:
    """Deterministic second-stage academic reranker aligned with ENSAI curriculum policy.

    Combines base retrieval score with:
    - Query-term & stemmed-term coverage in chunk text.
    - Exact math formula / code identifier hits.
    - Title & section-heading alignment.
    - Curriculum authority boost (prioritizing official lectures/tutorials/sheets over
      historical exam papers unless ``scope.purpose`` is exam practice).
    """

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        scope: QueryScope,
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        q_terms = extract_query_terms(query)
        q_stems = {_stem_token(t) for t in q_terms}
        q_symbols = [m.group(0) for m in _CODE_OR_MATH_RE.finditer(query or "")]
        exam_practice = scope.purpose in ("exam_prep", "annale_search", "practice")

        scored: list[tuple[float, str, RetrievedChunk]] = []
        for ch in candidates:
            base = ch.fused_score if ch.fused_score > 0 else max(ch.lexical_score, ch.semantic_score)
            text_folded = _fold_ascii(ch.text)
            text_stems = {_stem_token(t) for t in extract_query_terms(ch.text)}
            header_folded = _fold_ascii(" ".join((ch.title, *ch.section)))
            header_stems = {_stem_token(t) for t in extract_query_terms(header_folded)}

            coverage = (len(q_stems & text_stems) / len(q_stems)) if q_stems else 0.0
            header_cov = (len(q_stems & header_stems) / len(q_stems)) if q_stems else 0.0

            symbol_bonus = 0.0
            if q_symbols:
                hits = sum(1 for sym in q_symbols if sym in ch.text)
                symbol_bonus = 0.18 * (hits / len(q_symbols))

            # Curriculum authority policy from docs/RAG_ARCHITECTURE.md:12
            authority_bonus = 0.0
            rtype = (ch.resource_type or "").lower()
            if exam_practice:
                if rtype in ("examination", "examination_correction"):
                    authority_bonus = 0.05
            else:
                if rtype in ("lecture", "summary", "tutorial", "practical_work"):
                    authority_bonus = 0.04
                elif rtype == "examination":
                    authority_bonus = -0.02

            # Direct phrase bonus when normalized multi-word query appears verbatim
            phrase_bonus = 0.06 if (len(q_terms) >= 2 and " ".join(q_terms) in text_folded) else 0.0

            final_score = (
                base * 1.4
                + 0.35 * coverage
                + 0.16 * header_cov
                + symbol_bonus
                + authority_bonus
                + phrase_bonus
            )
            scored.append((round(final_score, 6), ch.chunk_id, replace(ch, rerank_score=round(final_score, 6))))

        # Deterministic sort: highest rerank_score first, stable tie-break by chunk_id
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:top_k]]


class HybridRetriever:
    """Scoped hybrid retriever combining SQLite FTS5 BM25, dense cosine, RRF, and reranking."""

    def __init__(
        self,
        store: LocalRagIndexStore,
        *,
        reranker: Reranker | None = None,
        candidate_pool_size: int = 24,
        max_chunks_per_document: int = 3,
    ) -> None:
        self._store = store
        self._reranker: Reranker = reranker or AcademicCrossFeatureReranker()
        self._candidate_pool_size = max(8, int(candidate_pool_size))
        self._max_chunks_per_document = max(1, int(max_chunks_per_document))

    def retrieve(self, scope: QueryScope, query: str) -> RetrievalResult:
        """Execute authorized lexical, semantic, or hybrid retrieval for ``query``.

        Args:
            scope: Authorization, academic filter, exam-mode, and retrieval mode settings.
            query: Raw student or system query (may contain LaTeX formulas or code).

        Returns:
            ``RetrievalResult`` with ranked chunks, verifiable ``Citation`` objects,
            and calibrated ``evidence_status`` (``grounded`` / ``partial`` / ``insufficient``).
        """
        started = time.perf_counter()
        norm_q = normalize_query(query)
        warnings: list[str] = []
        space_id = self._store.embedding_space_id

        if not norm_q:
            return RetrievalResult(
                query=query,
                normalized_query="",
                mode=scope.mode,
                hits=(),
                citations=(),
                evidence_status="insufficient",
                total_candidates=0,
                embedding_space_id=space_id,
                embedding_kind=self._store.embedder.embedding_kind,
                warnings=("empty_query",),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )

        # Live authorization & active-version check against P1 DB (blocks revoked/Global/tombstoned/superseded docs before reindex)
        allowed_versions = self._store.live_scope_revisions(scope)
        allowed_docs = set(allowed_versions.keys()) if allowed_versions is not None else None
        index_stale = False

        def _is_active_version(row: sqlite3.Row) -> bool:
            nonlocal index_stale
            if allowed_versions is None:
                return True
            expected = allowed_versions.get(str(row["document_id"]))
            if expected != (row["version_id"], row["extraction_revision"]):
                index_stale = True
                return False
            return True

        lexical_hits: list[RetrievedChunk] = []
        semantic_hits: list[RetrievedChunk] = []

        if scope.mode in ("lexical", "hybrid"):
            fts_q = build_fts5_query(norm_q, expand_bilingual=False)
            raw_lex = self._store.search_fts5(
                fts_q,
                scope,
                allowed_doc_ids=allowed_docs,
                limit=self._candidate_pool_size,
            )
            # Reformulate with conservative bilingual expansion when strict lexical returns < 3 hits
            if len(raw_lex) < 3:
                expanded_q = build_fts5_query(norm_q, expand_bilingual=True)
                if expanded_q and expanded_q != fts_q:
                    extra = self._store.search_fts5(
                        expanded_q,
                        scope,
                        allowed_doc_ids=allowed_docs,
                        limit=self._candidate_pool_size,
                    )
                    seen_lex = {str(r["chunk_id"]) for r, _ in raw_lex}
                    for r, sc in extra:
                        if str(r["chunk_id"]) not in seen_lex:
                            seen_lex.add(str(r["chunk_id"]))
                            raw_lex.append((r, sc * 0.85))
            lexical_hits = [
                _row_to_chunk(r, lexical_score=round(sc, 6), fused_score=round(sc, 6))
                for r, sc in raw_lex
                if _is_active_version(r)
            ]

        if scope.mode in ("semantic", "hybrid"):
            q_vec = self._store.embedder.embed_texts([norm_q])[0]
            scoped_vecs = self._store.load_scoped_vectors(scope, allowed_doc_ids=allowed_docs)
            sem_scored: list[tuple[float, str, sqlite3.Row]] = []
            for row, vec in scoped_vecs:
                if not _is_active_version(row):
                    continue
                sim = cosine_similarity_normalized(q_vec, vec)
                if sim > 0.05:
                    sem_scored.append((sim, str(row["chunk_id"]), row))
            best_scored = nsmallest(
                self._candidate_pool_size, sem_scored, key=lambda item: (-item[0], item[1])
            )
            semantic_hits = [
                _row_to_chunk(r, semantic_score=round(sim, 6), fused_score=round(sim, 6))
                for sim, _, r in best_scored
            ]

        # Fuse candidates via Reciprocal Rank Fusion (RRF) with deterministic chunk_id tie-break
        if scope.mode == "lexical":
            fused_candidates = lexical_hits
        elif scope.mode == "semantic":
            fused_candidates = semantic_hits
        else:
            by_id: dict[str, RetrievedChunk] = {}
            rrf_scores: dict[str, float] = {}
            lex_map: dict[str, float] = {}
            sem_map: dict[str, float] = {}

            for rank, ch in enumerate(lexical_hits, start=1):
                by_id[ch.chunk_id] = ch
                lex_map[ch.chunk_id] = ch.lexical_score
                rrf_scores[ch.chunk_id] = rrf_scores.get(ch.chunk_id, 0.0) + (1.0 / (RRF_K + rank))

            for rank, ch in enumerate(semantic_hits, start=1):
                if ch.chunk_id not in by_id:
                    by_id[ch.chunk_id] = ch
                sem_map[ch.chunk_id] = ch.semantic_score
                rrf_scores[ch.chunk_id] = rrf_scores.get(ch.chunk_id, 0.0) + (1.0 / (RRF_K + rank))

            # Normalize RRF into [0, 1] relative to theoretical top-1 hybrid score (2 / 61)
            max_rrf = 2.0 / (RRF_K + 1)
            fused_list = [
                replace(
                    by_id[cid],
                    lexical_score=lex_map.get(cid, 0.0),
                    semantic_score=sem_map.get(cid, 0.0),
                    fused_score=round(min(1.0, score / max_rrf), 6),
                )
                for cid, score in rrf_scores.items()
            ]
            fused_list.sort(key=lambda c: (-c.fused_score, c.chunk_id))
            fused_candidates = fused_list

        total_candidates = len(fused_candidates)

        # Section paths already carry the heading on content chunks. Keep heading
        # citations addressable, but avoid presenting the label twice as evidence.
        content_sections = {
            (ch.document_id, ch.version_id, ch.section)
            for ch in fused_candidates
            if ch.section and ch.block_kind != "heading"
        }

        # Deduplicate near-identical overlapping chunks & apply per-document diversity cap
        per_doc_cap = (
            max(scope.top_k, self._max_chunks_per_document)
            if len(scope.document_ids) == 1
            else self._max_chunks_per_document
        )
        deduped: list[RetrievedChunk] = []
        per_doc_counts: dict[str, int] = {}
        seen_hashes: set[str] = set()
        for cand in fused_candidates:
            if cand.block_kind == "heading" and (
                cand.document_id, cand.version_id, cand.section
            ) in content_sections:
                continue
            norm_text_key = " ".join(cand.text.split()).lower()
            if norm_text_key in seen_hashes:
                continue
            if any(_jaccard_tokens(cand.text, prev.text) >= 0.85 for prev in deduped):
                continue
            if per_doc_counts.get(cand.document_id, 0) >= per_doc_cap:
                continue
            seen_hashes.add(norm_text_key)
            per_doc_counts[cand.document_id] = per_doc_counts.get(cand.document_id, 0) + 1
            deduped.append(cand)
            if len(deduped) >= max(12, scope.top_k * 2):
                break

        # Optional second-stage reranking
        if scope.rerank and deduped:
            selected = self._reranker.rerank(norm_q, deduped, scope=scope, top_k=scope.top_k)
        else:
            selected = deduped[: scope.top_k]

        # Neighbor-chunk expansion for proof/code/equation continuity (docs/RAG_ARCHITECTURE.md:14)
        final_hits: list[RetrievedChunk] = list(selected)
        if scope.expand_neighbors and selected:
            seen_ids = {c.chunk_id for c in final_hits}
            extra_neighbors: list[RetrievedChunk] = []
            for ch in selected[:3]:
                needs_neighbor = (
                    ch.block_kind in ("code", "formula")
                    or bool(ch.structured.get("math_expressions"))
                    or any(kw in ch.text.lower() for kw in ("preuve", "proof", "démonstration", "demonstration"))
                )
                if not needs_neighbor:
                    continue
                adj_ords = [o for o in (ch.ordinal - 1, ch.ordinal + 1) if o >= 0]
                for nrow in self._store.load_neighbor_chunks(ch.document_id, adj_ords):
                    if nrow["version_id"] != ch.version_id or not _is_active_version(nrow):
                        continue
                    neighbor = _row_to_chunk(
                        nrow,
                        fused_score=round(ch.fused_score * 0.75, 6),
                        is_neighbor_expansion=True,
                    )
                    if neighbor.block_kind == "heading" and (
                        neighbor.document_id, neighbor.version_id, neighbor.section
                    ) in content_sections:
                        continue
                    nid = str(nrow["chunk_id"])
                    if nid in seen_ids:
                        continue
                    seen_ids.add(nid)
                    extra_neighbors.append(neighbor)
                    if len(extra_neighbors) >= 2:
                        break
                if len(extra_neighbors) >= 2:
                    break
            final_hits.extend(extra_neighbors)

        if index_stale:
            warnings.append("stale_indexed_version_excluded")

        ranked_hits = tuple(
            replace(ch, rank=idx) for idx, ch in enumerate(final_hits, start=1)
        )
        citations = tuple(
            ch.to_citation(f"C{idx}") for idx, ch in enumerate(ranked_hits, start=1)
        )
        evidence_status = self._assess_evidence_status(norm_q, ranked_hits)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return RetrievalResult(
            query=query,
            normalized_query=norm_q,
            mode=scope.mode,
            hits=ranked_hits,
            citations=citations,
            evidence_status=evidence_status,
            total_candidates=total_candidates,
            embedding_space_id=space_id,
            embedding_kind=self._store.embedder.embedding_kind,
            index_stale=index_stale,
            warnings=tuple(warnings),
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _assess_evidence_status(
        norm_query: str,
        hits: Sequence[RetrievedChunk],
    ) -> EvidenceStatus:
        """Calibrate ``grounded`` / ``partial`` / ``insufficient`` status to prevent hallucinations.

        Excludes generic structural academic nouns (``theorem``, ``definition``, ``exercice``,
        etc.) when measuring distinctive topic coverage so out-of-scope queries sharing only
        generic vocabulary abstain reliably, while preserving French/English cross-lingual
        concept matches via ``_BILINGUAL_QUERY_EXPANSIONS``.
        """
        if not hits:
            return "insufficient"
        q_terms = extract_query_terms(norm_query)
        distinctive_terms = [t for t in q_terms if t not in _GENERIC_ACADEMIC_TERMS]
        eval_terms = distinctive_terms or q_terms
        if not eval_terms:
            return "insufficient"

        top = hits[0]
        top_text_stems = {
            _stem_token(t)
            for t in extract_query_terms(" ".join((top.title, *top.section, top.text)))
        }
        all_hit_stems: set[str] = set()
        for h in hits[:4]:
            all_hit_stems.update(
                _stem_token(t)
                for t in extract_query_terms(" ".join((h.title, *h.section, h.text)))
            )

        def _term_matched(term: str, candidate_stems: set[str]) -> bool:
            st = _stem_token(term)
            if st in candidate_stems:
                return True
            for alt in _BILINGUAL_QUERY_EXPANSIONS.get(term, ()) or _BILINGUAL_QUERY_EXPANSIONS.get(st, ()):
                if _stem_token(alt) in candidate_stems:
                    return True
            return False

        top_matched = sum(1 for t in eval_terms if _term_matched(t, top_text_stems))
        union_matched = sum(1 for t in eval_terms if _term_matched(t, all_hit_stems))
        top_cov = top_matched / len(eval_terms)
        union_cov = union_matched / len(eval_terms)
        strong_signal = max(top.lexical_score, top.semantic_score, top.fused_score)

        # Strict abstention guards:
        # 1. Zero distinctive topic terms matched anywhere in top hits unless strong neural cosine
        if union_matched == 0 and top.semantic_score < 0.45:
            return "insufficient"
        # 2. Multi-term topic query with < 25% distinctive term coverage and weak semantic alignment
        if len(eval_terms) >= 2 and union_cov < 0.25 and top.semantic_score < 0.40:
            return "insufficient"
        if union_cov < 0.25 and top.semantic_score < 0.35 and top.lexical_score < 0.20:
            return "insufficient"
        if (top_cov >= 0.50 or union_cov >= 0.65) and strong_signal >= 0.30:
            return "grounded"
        if union_cov >= 0.30 or (union_matched >= 1 and strong_signal >= 0.22):
            return "partial"
        return "insufficient"
