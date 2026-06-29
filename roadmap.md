# Roadmap

## Goal

Move the project from "vector search over 1C export" to a practical MCP toolkit for day-to-day 1C development.

## Priority 1. Hybrid Retrieval

- Keep `search_code` and `search_metadata` as broad semantic tools.
- Continue improving lexical reranking, type detection, and fallback retrieval from payload fields.
- Add universal semantic query expansion for common 1C business phrasing, without binding search to concrete objects from one configuration.
- Target outcome: natural-language queries should stay convenient without losing precision.

## Priority 2. Deterministic Lookup Tools

- Add `find_metadata_object` for exact or near-exact metadata object lookup.
- Add `find_method` for exact or near-exact method lookup in BSL modules.
- Next candidates: `find_module`, `find_form`, `find_common_module`.
- Target outcome: an MCP client can reliably jump to concrete entities, not only "similar" ones.

## Priority 3. Dependencies and Where-Used

- Add `get_dependencies` for metadata relationships.
- Add `find_usages` or `where_used` for object and method usage lookup.
- Store a lightweight dependency graph locally as JSON during indexing.
- Target outcome: the assistant can reason about impact analysis before suggesting changes.

## Priority 4. Call Graph

- Extract calls between procedures and functions during indexing.
- Add `get_callers` and `get_callees`.
- Start with heuristic parsing, then move to stricter parsing if needed.
- Target outcome: faster code navigation through real call chains.

## Priority 5. Index Diagnostics

- Add `index_status` with collection counts, cache stats, and index freshness signals.
- Add `reindex_file` or `reindex_path` for targeted repair.
- Add `explain_search_result` for search debugging.
- Target outcome: the system becomes easier to trust and maintain.

## Priority 6. Docs and Regression Cases

- Document recommended workflows for agents: semantic search -> exact lookup -> snippet -> dependencies.
- Maintain a set of hard MCP test cases for metadata, code, and cross-object scenarios.
- Add concise usage examples for each MCP tool.

## Current Step

1. Implement deterministic lookup tools. Done.
2. Add `index_status`. Done.
3. Add metadata dependency retrieval. Done.
4. Add universal semantic query expansion for natural metadata lookup. Done.
5. Next: implement `where_used` / `find_usages`.
