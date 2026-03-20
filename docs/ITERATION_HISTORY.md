# Iteration History: From Prototype to General Edition

## Objective Evolution

### v1: Local Search Baseline
- Built local-first search with FastAPI + SQLite + TF-IDF baseline.
- Added Outlook COM and Graph ingestion into local tables.
- Focused on privacy-first, laptop-local execution.

### v2/v3: RAG and Orchestration
- Introduced local-LLM RAG with execution transparency.
- Added orchestration flow for SQL, RAG, and hybrid paths.
- Improved intent classification and topic-focused retrieval.

### v4/v5: Retrieval Precision and Routing Corrections
- Improved query routing accuracy (especially participant and count queries).
- Fixed behavioral regression where numeric/person queries could route to pure RAG.
- Strengthened SQL path reliability for deterministic outputs.

### v6: Chunked Chroma Semantic Memory
- Added chunking strategy and persistent Chroma vector store.
- Added metadata-rich retrieval and grounded generation.
- Added architecture visibility in UI and versioned controls.
- Isolated optional v6 dependency failures to protect v1-v5 startup.

## Major Improvements Achieved

1. Better retrieval relevance through chunking and reranking.
2. Better reliability through guarded imports and endpoint isolation.
3. Better explainability through architecture and metadata panels.
4. Better operator controls with explicit memory rebuild flow.

## Lessons Learned

1. Keep SQL authoritative for counts and numeric facts.
2. Keep semantic retrieval optimized for narrative and context recall.
3. Mixed questions need hybrid orchestration, not single-path forcing.
4. Build replayable pipelines and auditable data lineage from day one.

## General Edition (Current Restart)

The General Edition repo starts a productized architecture with cleaner boundaries:
- Direct-source semantic chunking for historical data confidence.
- SQL analytics lane retained for deterministic fact queries.
- Hybrid response lane for mixed fact + summary user questions.
- Product UI reset for a clearer first-edition identity.
