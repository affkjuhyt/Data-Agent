# 11. Banking data crawl

The repository keeps banking test data outside Git. The crawler downloads public
archives, resumes partial files, and writes `MANIFEST.json` with the observed
size and SHA-256 checksum for every artifact.

## Quick start

The default root is `/private/tmp/data-agent-banking`. Override it when a larger
volume is available:

```bash
make banking-data-plan BANKING_DATA_PROFILE=test
make banking-data-download BANKING_DATA_PROFILE=test
make banking-data-verify BANKING_DATA_ROOT=/private/tmp/data-agent-banking
```

The `test` profile covers the CFPB complaints export, GDELT GKG from
2026-07-09 through 2026-10-16, and SEC Financial Statement and Notes data for
2025 Q2. `lite` is intended for a quick smoke run; `standard` expands
the GDELT and SEC windows.

The CFPB ZIP contains a much larger CSV than its compressed size. Process it as
a stream or extract it on a volume with sufficient free space. The crawler does
not upload data and does not put archives in the checkout.

## Sources

- [CFPB Consumer Complaint Database](https://www.consumerfinance.gov/data-research/consumer-complaints/)
- [GDELT GKG files](https://data.gdeltproject.org/gkg/index.html)
- [SEC Financial Statement and Notes Data Sets](https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets)

The SEC files are broad XBRL-derived datasets, not bank-only snapshots. Filter
by SIC/CIK during normalization to create the banking subset.
