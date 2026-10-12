-- routines/document.sql: what D4.15 (tab 3b) writes in words for schema document: the full-text
-- index on chunk text used for keyword search next to the Qdrant vectors.

-- covers: document.chunk#0
-- 'simple' configuration: no stemming dictionary fits Vietnamese, so words are matched as written.
CREATE INDEX chunk_text_idx ON document.chunk USING gin (to_tsvector('simple', text));
