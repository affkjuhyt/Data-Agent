-- CHECK value_constraints_ok of acquire.crawl_schema_version (BR-168)
-- The domain of every extracted field: when value_constraints is given it is an object with
-- min <= max; with `require_numeric_domain` (an active schema) every number, percent or money
-- field declares min, max and unit. IMMUTABLE so it can sit in a CHECK.
CREATE FUNCTION acquire.value_constraints_ok(fields jsonb, require_numeric_domain boolean)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT jsonb_typeof(fields) = 'array' AND NOT EXISTS (
    SELECT 1
    FROM jsonb_array_elements(fields) AS f(field)
    WHERE (
        field ? 'value_constraints' AND jsonb_typeof(field->'value_constraints') <> 'null' AND (
          jsonb_typeof(field->'value_constraints') <> 'object'
          OR (field->'value_constraints' ? 'min' AND field->'value_constraints' ? 'max'
              AND (field->'value_constraints'->>'min')::numeric > (field->'value_constraints'->>'max')::numeric)
        )
      )
      OR (
        require_numeric_domain
        AND field->>'type' IN ('number', 'percent', 'money')
        AND NOT (coalesce(jsonb_typeof(field->'value_constraints') = 'object', false)
                 AND field->'value_constraints' ?& ARRAY['min', 'max', 'unit'])
      )
  )
$$;
