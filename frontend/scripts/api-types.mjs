// Generates src/lib/v1/schema.ts from src/lib/v1/openapi.json (make api-types).
//
// openapi-typescript alone makes every field with a default required (defaultNonNullable), which
// is right for an answer — the api always serialises the default — and wrong for a request body,
// where leaving the field out is how a caller asks for the default (HitlResolve.remember_as_rule,
// ProposalDecision.reviewed_regressions, …). FastAPI writes one schema per model and lists neither
// side as required, so the two are told apart here: a schema reached from a response gets its
// defaulted fields added to `required`, and the generator then leaves defaults alone. The few
// schemas used by both sides (4 on 23/10/2026) keep them required, as before.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import openapiTS, { astToString, COMMENT_HEADER } from 'openapi-typescript';

const V1 = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'lib', 'v1');
const spec = JSON.parse(readFileSync(resolve(V1, 'openapi.json'), 'utf8'));
const schemas = spec.components?.schemas ?? {};

function reach(node, seen) {
  if (Array.isArray(node)) node.forEach((item) => reach(item, seen));
  else if (node && typeof node === 'object') {
    const name = typeof node.$ref === 'string' ? node.$ref.split('/').pop() : null;
    if (name && !seen.has(name)) {
      seen.add(name);
      reach(schemas[name], seen);
    }
    Object.values(node).forEach((value) => reach(value, seen));
  }
  return seen;
}

const answered = new Set();
for (const item of Object.values(spec.paths ?? {}))
  for (const operation of Object.values(item))
    if (operation && typeof operation === 'object') reach(operation.responses, answered);

for (const name of answered) {
  const schema = schemas[name];
  const defaulted = Object.entries(schema?.properties ?? {})
    .filter(([, property]) => property && 'default' in property)
    .map(([key]) => key);
  if (defaulted.length) schema.required = [...new Set([...(schema.required ?? []), ...defaulted])];
}

const ast = await openapiTS(spec, { defaultNonNullable: false });
writeFileSync(resolve(V1, 'schema.ts'), COMMENT_HEADER + astToString(ast));
