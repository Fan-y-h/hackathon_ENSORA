# Retrieval-augmented generation

Requirements R06–R08, R14, R18–R19. Use source-grounded answers respecting ENSAI terminology, notation and methods. The index is derived, local and versioned; retrieval must not leak across profiles or source permissions.

## Indexing pipeline [RECOMMENDATION]
`DocumentVersion → ParsedBlock[] → Chunk[] → embedding + FTS → publish active revision`. Block contains text, kind, hierarchy, locator, language, optional original asset. Prefer semantic section boundaries, 350–700 tokens target and 60–100 overlap as tunable defaults; keep code cells, proofs, equations and tables intact where possible, split long units with parent anchor and continuation markers. Never synthesize content during parsing. Store exact quoted spans separate from optional concept summaries.

Use a local multilingual embedding model chosen after checking license, memory and French/math performance; pin model ID/revision, dimension, tokenizer and normalization. Do not compare embeddings of different versions. Batch writes, stable chunk IDs derived from version/ordinal/parser config, incremental rebuild per changed document. Maintain vector index namespace per user or enforce prefilter. Failed index publication retains previous valid version only if still authorized; expose pending/stale state.

## Query pipeline
1. Resolve authenticated user, optional subject/chapter/concepts, selected document IDs, requested activity, language and exam-mode policy.
2. Normalize query without losing formulas and code identifiers. Filter by access and requested document versions before search. Current official materials prioritized for curriculum; historical exams for practice, solutions only when allowed.
3. Retrieve lexical candidates via SQLite FTS5 (BM25, escaped query syntax) and semantic candidates via cosine similarity. Proposed top 20 each; fuse with reciprocal rank fusion `sum(1/(60+rank))`, stable tie by chunk ID. Deduplicate overlap, diversify documents and optionally rerank top 12; choose 4–8 chunks within budget. Values are tuning defaults, not guarantees.
4. Expand neighbor chunks for proof/code context, preserve complete equations; use source IDs only. For comprehensive sheets or full-course summaries, enumerate all selected sections and use per-section extraction/map-reduce rather than top-k alone. Report omissions and coverage.
5. Assemble prompt with explicit untrusted-source delimiters and source metadata; no document instructions can override system or user scope. External transfer allowed only by policy. AI Router chooses compatible model; validate structured result and citations against supplied chunks.
6. Return answer + citations + evidence status (`grounded`, `partial`, `insufficient`); if weak/no evidence, state missing source, request clarification or offer explicitly labeled general explanation only when permitted. Do not fabricate page references.

## Citation schema
`{citation_id, document_id, version_id, chunk_id, title, locator:{kind,page,slide,cell,line_start,line_end,section}, excerpt, source_url?}`. One-based page/slide/cell UI labels; store a consistent convention. Excerpt must match accessible source span. A citation opens exact locally stored version and highlight when possible; never expose filesystem path. Persist citations with answers and artifacts; if revoked, show unavailable source rather than revealing excerpt.

## Quality checks
Use a synthetic multilingual course set with expected chunk IDs and unanswerable questions. Measure recall@k, MRR, citation precision (human verification), completeness for summary, answer grounding and retrieval latency; distinguish retrieved evidence from generated claims. Tests: selected-doc restriction, exact formula/code search, synonym query, missing evidence, injection, stale index, version change, source revocation, no secret disclosure. Initial target (proposed): all golden citations resolve, >=80% recall@5 on a small curated set, no unsupported claims in demo answers after review. Do not claim these measured until run.

Interfaces: `retrieve(QueryScope, query, purpose) -> RetrievalResult`; `index(version_id) -> IndexReceipt`; `resolve_citation(user, id)`. AI module depends on these interfaces, not vector client. See [AI_ENGINE](AI_ENGINE.md), [API_CONTRACTS](API_CONTRACTS.md).
