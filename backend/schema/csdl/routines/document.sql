-- routines/document.sql: what D4.15 (tab 3b) writes in words for schema document: the full-text
-- index on chunk text used for keyword search next to the Qdrant vectors.

-- covers: document.chunk#0
-- 'simple' configuration: no stemming dictionary fits Vietnamese, so words are matched as written.
CREATE INDEX chunk_text_idx ON document.chunk USING gin (to_tsvector('simple', text));

-- covers: document.chunk#1
-- DI-11: a passage found by a question typed without diacritics ("lai suat cho vay"). The
-- folded form is the text with its marks taken off, lower case, đ as d, its context header
-- (title, number, section) first so the keyword half reads the header too (§5.3 rule 3). An
-- expression index rather than a stored column: the same match, no column for the ORM to carry.
-- unaccent() is STABLE (its dictionary could change), so the IMMUTABLE wrapper names the
-- dictionary and schema explicitly, as an index expression requires.
CREATE FUNCTION public.da_fold(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT lower(public.unaccent('public.unaccent'::regdictionary,
                               replace(replace(t, 'đ', 'd'), 'Đ', 'D')))
$$;
CREATE INDEX chunk_text_folded_idx ON document.chunk
  USING gin (to_tsvector('simple', public.da_fold(coalesce(context_header, '') || ' ' || text)));
