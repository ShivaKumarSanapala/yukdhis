# Ontology: meta-model in code, domains as packs

Replaces the hard-coded domain constants of `knowledge_graph/ontology.py` (§3.1 of `design.md`). Adding a circular on a new
topic (provisioning, KYC, treasury, capital) is now a data change, reviewed like any other, not a code change in several files.

## 1. Split

| Layer | Where | Contents | Changes |
| --- | --- | --- | --- |
| Meta-model | `knowledge_graph/ontology.py` (code) | node types, edge types, `PII_COLUMNS`, `REGULATED_ENTITY_TERMS`, `POLICY_KINDS` | rarely, by a developer |
| Domain packs | `data/ontology/packs/<pack_id>.json` | capabilities, record flags, event types, product attributes, sanitized views, glossary, workflows, process maps, applicability keywords | per domain, by data PR |
| Tenant overlay | `data/ontology/packs/tenant_<tenant_id>.json` | vocabulary a bank's reviewers promoted from LLM proposals | by `scripts/ontology_proposals.py` |

New edges: `AMENDS` and `REFERS_TO` (Policy to Policy, circular lineage; `SUPERSEDES` already existed), `IMPLEMENTS`
(Policy to Rule: an internal policy clause implements a regulatory rule) and `ENFORCES` (Policy to Control). `Policy` nodes
carry `kind`: `regulatory` or `internal`; `PolicyMapper.upsert_policy` writes it (default `regulatory`) and writes `AMENDS` /
`REFERS_TO` edges when the policy object has `amends` / `refers_to` lists. The Spanner DDL stores `edge_type` as a string, so
the schema and `deploy.sh` need no change.

## 2. Pack schema v1

`knowledge_graph/packs.py` validates with pydantic (`extra="forbid"`): an unknown key, a bad id, a bad regular expression or
a file whose name differs from its `pack_id` is a `PackError` naming the file and the path, for example
`beta.json: capabilities.x.colour: unknown key`.

```
pack_id, version (x.y.z), title, domain                      required
business_line            key of entity_profile.business_lines (null for overlays)
requires_data_domains    keys of entity_profile (not_)onboarded_data_domains
capabilities             {capability_id: {control: "control:<slug>", description, process: "process:<slug>"|null, synonyms[]}}
record_flags             {flag: description}
event_types              ["UPPER_SNAKE"]        event_processes {EVENT: "process:<slug>"}
product_attributes       ["rate_type", ...]
views                    {view: {description, columns{col: description}, data_domain?}}
glossary                 {term: {attribute: value}}
workflows                {WORKFLOW_ID: description}
applicability_signals    one signal object, or a list of them:
                         {keywords[] (regexes, case-insensitive), business_line, data_domains[], licence?, name?, label?, order?}
```

Extensions to the v1 sketch: `views.<v>.data_domain` (which entity-profile domain a view serves), signal `licence` (an
`entity_profile.licences` key that must be held), `name`, `label` and `order` (stable ordering of an assessment's reasons),
and a list of signals per pack (the treasury pack has four).

## 3. Enabling and merging

* `entity_profile.json` key `ontology_packs` (list of pack ids) selects the packs; an unknown id is an error. The demo profile
  lists `retail_lending` and `msme_lending`.
* Key absent: every pack whose business line is `active` and whose `requires_data_domains` are all onboarded, so a bank that
  onboards deposits gets `deposits_kyc` without editing the profile.
* The tenant overlay of the profile's tenant (and of the store's tenant, where code has a store) is always enabled.
* Merge (`merge_packs`): the same capability id, record flag, view, workflow, event process or glossary term in two packs with
  different content is a `PackConflictError` listing both pack ids. Identical content is accepted.
* Pack files are cached by (name, mtime, size); an edit is picked up without a restart by every caller of `get_ontology()`.

## 4. Public API of `knowledge_graph.ontology`

Source-compatible module names, computed at import from the default profile: `NODE_TYPES`, `EDGE_TYPES`, `CAPABILITIES`,
`RECORD_FLAGS`, `EVENT_TYPES`, `PRODUCT_ATTRIBUTES`, `SANITIZED_VIEWS`, `PII_COLUMNS`, `REGULATORY_GLOSSARY`,
`REGULATED_ENTITY_TERMS`, `EVENT_PROCESSES`, `CAPABILITY_PROCESSES`, `WORKFLOWS`, `ontology_summary(domain, profile=None)`.
New: `get_ontology(profile=None, pack_dir=None, tenant_ids=())` returning a frozen `Ontology`, `POLICY_KINDS`, `build_ontology`.
Code that must see promoted vocabulary or a custom profile at run time uses `get_ontology()`: the extractor prompt, the control
canonicaliser (candidates, exact match, slug ids) and the mapper do. A golden test (`tests/test_ontology_packs.py`) holds the
old constants as literals and proves the merged `retail_lending` pack reproduces them.

## 5. Applicability

`pipeline/applicability.py` no longer has keyword tables. `packs.applicability_signals()` compiles the signals of **every**
pack (enabled or not: a treasury circular must still be recognised in a bank without a desk) into regexes, sorted by
`(order, pack_id, name)`. Signal packs: `treasury_derivatives` (NDDC, IFSC Banking Unit, FERR, FX derivatives),
`capital_adequacy` (CVA, RWA, CET1, CRAR, capital charge), `deposits_kyc` (deposits; KYC, AML, CDD, CKYC), plus
`msme_lending` and `liquidity_management` for the two remaining old tables, and `retail_lending` for lending. The data
domains a check implies (a record flag, an event, a capability) come from the pack that owns that vocabulary, not from a
constant. Behaviour is unchanged for existing inputs (checked against the old module on 528 rule and profile combinations);
the only intended difference is that KYC, AML, CDD and CKYC wording now links a rule to the `deposits` domain.
Still in code: entity-class patterns (NBFC, co-operative banks, ...), AD category parsing, and the loan-specific
`ProductScope` handling.

## 6. Governed growth

The LLM may propose, never silently extend. The canonicaliser already creates a **new Control flagged `needs_review`** for a
capability it cannot place; it now also records `first_seen` and `source_rule`.

* `python scripts/ontology_proposals.py` lists pending proposals with the rules, policies and documents that produced them.
* `--promote <control_id> [--pack <pack_id> | --tenant] [--process process:<slug>] [--apply]` writes the capability into the
  pack (default: the tenant overlay, created at 1.0.0 when missing; an existing pack gets its patch version bumped), checks
  the whole pack set still merges, then clears `needs_review` on the graph node (`promoted_to`, `promoted_at`). Unknown pack
  ids are refused. Dry run unless `--apply`.
* `--reject <control_id> [--reason ...] [--apply]` sets `rejected`: the node stays for audit and is excluded from candidates.
* `GET /api/ontology` (same auth as the other GET endpoints, read-only): enabled packs with versions and counts, packs
  available but not enabled, the merged vocabulary summary, and pending proposals (`id`, `name`, `first_seen`, `source_rule`).

Pack files live in the image (`COPY data`), so promote in a checkout or against the deployed graph from a workstation, review
the pack diff, commit and redeploy; the running service reads the packs shipped with its image.

## 7. Limits

* Graph nodes are not bitemporal in the store (only edges are); clearing `needs_review` rewrites the node with `upsert_node`.
* Vocabulary that is not in a pack (a capability the extractor invents) still gets a control; it is a proposal until promoted.
* Views are data, but the secure engine's SQL for them lives in the Cloud SQL views (`infrastructure/cloudsql`); a pack that
  adds a view needs the matching database view.
