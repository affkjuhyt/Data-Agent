# 12. Banking data classification and upload test

This guide separates the downloaded banking data into raw source archives,
upload-ready structured samples, and the test cases that should be exercised
against `/v1/uploads`.

## 1. Classification

| Category | Current source | Banking use case | Data shape | Recommended handling |
| --- | --- | --- | --- | --- |
| `customer_complaints` | CFPB `complaints.csv.zip` | complaint trends, product/service quality, response timeliness | one wide CSV | upload a sample first; keep the raw archive immutable |
| `market_news_and_events` | daily GDELT GKG ZIPs | external news context, market risk and macro signals | daily tab-separated rows | convert one day to CSV for upload; keep daily archives as raw history |
| `regulatory_financial_filings` | SEC `sub.tsv`, `num.tsv`, `tag.tsv`, `dim.tsv`, `cal.tsv`, `pre.tsv`, `txt.tsv`, `ren.tsv` | issuer identity, reported facts, accounting concepts and filing context | related TSV tables keyed by fields such as `adsh`, `cik`, `tag` | upload related tables separately; do not flatten the filing package into one file |

The SEC package is not bank-only. Use `sic`, `cik`, issuer name and filing
form/period to create a banking subset after ingestion. CFPB is consumer-
protection data, while GDELT is external context; neither is authoritative
internal ledger data.

## 2. Create upload-ready samples

The API accepts CSV and Excel files up to 200 MiB. ZIP and TSV files are raw
landing artifacts, not upload payloads. Prepare samples outside the repository:

```bash
python3 tools/banking_upload_prep.py inventory \
  --root /private/tmp/data-agent-banking \
  > /private/tmp/data-agent-banking/DATASET_INVENTORY.json

python3 tools/banking_upload_prep.py prepare \
  --root /private/tmp/data-agent-banking \
  --output /private/tmp/data-agent-banking-upload
```

The command creates bounded samples under:

```text
/private/tmp/data-agent-banking-upload/
├── DATASET_CATALOG.json
├── customer_complaints/cfpb_complaints_sample.csv
├── market_news_and_events/20261016_gkg_sample.csv
└── regulatory_financial_filings/
    ├── sec_submissions_sample.csv
    └── sec_numeric_facts_sample.csv
```

It does not modify `/private/tmp/data-agent-banking`. Increase row limits only
after checking free disk and the API's 200 MiB limit.

## 3. Upload flow

1. `POST /v1/uploads` stores the immutable original and performs a first schema read.
2. `GET /v1/uploads/{id}/preview` checks headers, inferred types and sample rows.
3. `PATCH /v1/uploads/{id}` supplies the final mapping and parsing options.
4. `POST /v1/uploads/{id}:commit` writes accepted rows and registers the manual dataset.

The request needs a bearer token and the selected `X-Data-Space`:

```bash
export API_BASE_URL="http://localhost:30100"
export TOKEN="replace-with-a-short-lived-token"
export DATA_SPACE="replace-with-data-space-id-or-code"

UPLOAD_JSON=$(curl --fail-with-body -sS -X POST "$API_BASE_URL/v1/uploads" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Data-Space: $DATA_SPACE" \
  -F "file=@/private/tmp/data-agent-banking-upload/customer_complaints/cfpb_complaints_sample.csv;type=text/csv")
printf '%s\n' "$UPLOAD_JSON" | python3 -m json.tool
UPLOAD_ID=$(printf '%s' "$UPLOAD_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

curl --fail-with-body -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Data-Space: $DATA_SPACE" \
  "$API_BASE_URL/v1/uploads/$UPLOAD_ID/preview?limit=10" | python3 -m json.tool
```

Repeat the flow for the GDELT sample and the two SEC samples. Keep one upload
per logical table so schema inference and quality failures are isolated.

## 3a. Bulk ingestion from the raw crawl directory

For a large test load, use the resumable CLI instead of the browser. It reads
each ZIP member, converts it to CSV parts below the API payload limit, waits for
analysis, commits the part, and records the upload/dataset IDs in a manifest:

```bash
export DATA_AGENT_TOKEN='short-lived bearer token'
make banking-data-bulk-ingest \
  BANKING_DATA_ROOT=/private/tmp/data-agent-banking \
  BANKING_DATA_SPACE=DS-DEMO \
  BANKING_DATA_TOKEN="$DATA_AGENT_TOKEN"
```

The default part size is 128 MiB. Resume uses
`/private/tmp/data-agent-banking-bulk-ingest.json`; completed parts are skipped.
The raw ZIPs are never modified. Check free disk first: the API stores the
converted part in object storage and materializes it into the workspace, so the
expanded size is materially larger than the compressed crawl directory.

## 4. Test cases

| Case | Input | Expected result |
| --- | --- | --- |
| happy path | CFPB CSV sample | preview works and commit creates an `origin=upload` dataset |
| delimiter conversion | prepared GDELT sample | preview sees comma-separated columns, not one tab-delimited column |
| related tables | SEC submissions and numeric facts | each upload has explicit mapping; `adsh`/`cik` remain available for joins |
| invalid extension | original `.zip` | `422 validation_failed` |
| oversize payload | CSV larger than 200 MiB | `413 payload_too_large` |
| bad encoding | non-UTF-8 CSV | `422 validation_failed` |
| bad mapping | commit with more than 5% invalid rows | `422 business_rule_violated`; no dataset committed |
| tenant isolation | upload in Data Space A, read from B | B cannot list, preview, patch or commit it |
| invalid state | commit twice or before analysis | request rejected; original object unchanged |

For existing automated coverage, use the repository service-image targets and
start with the API ingest suite. The detailed deployed flow is in
`tests/integration/api/test_ingest.py`.

## 5. Data governance

- Keep `/private/tmp/data-agent-banking` as the raw, checksummed source layer.
- Treat prepared CSVs as disposable derivatives and regenerate them from raw.
- Do not upload the full CFPB archive or SEC package without a storage and
  retention decision; `/v1/uploads` is a manual-table path, not bulk ingestion.
- Label these as external/reference or uploaded authority, not the bank's
  certified ledger. Certification requires a separate governed dataset workflow.

### 5a. Banking demo answer contract

The local banking demo seed also registers `cfpb_complaints` as an active context
Data Product, binds `COMPLAINT_COUNT` to the consolidated dataset, and records a
dataset version plus freshness fixture. A new question inside this scope must show
`Biên nhận` with the product/version and the `Cách tính` panel must show the
qualified workspace view and freshness/classification facts. An older answer created
before the seed remains out of scope; rerun the question after applying the seed.
