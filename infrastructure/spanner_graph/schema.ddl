-- Spanner Graph schema for the BIAN knowledge graph (Enterprise edition).
-- Multi-tenant: every row is keyed by tenant_id. Bitemporal edges:
--   valid_from / valid_to       business time (when the relationship was true)
--   recorded_at / superseded_at system time   (when we learned / replaced it)
-- No customer PII is stored in the graph.
CREATE TABLE Nodes (
  tenant_id STRING(64) NOT NULL,
  node_id STRING(256) NOT NULL,
  node_type STRING(64) NOT NULL,
  name STRING(512) NOT NULL,
  attributes JSON,
  recorded_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
) PRIMARY KEY (tenant_id, node_id);

CREATE INDEX NodesByType ON Nodes (tenant_id, node_type);

CREATE TABLE Edges (
  tenant_id STRING(64) NOT NULL,
  source_id STRING(256) NOT NULL,
  edge_type STRING(64) NOT NULL,
  target_id STRING(256) NOT NULL,
  valid_from DATE NOT NULL,
  recorded_at TIMESTAMP NOT NULL OPTIONS (allow_commit_timestamp = true),
  valid_to DATE,
  superseded_at TIMESTAMP OPTIONS (allow_commit_timestamp = true),
  attributes JSON,
) PRIMARY KEY (tenant_id, source_id, edge_type, target_id, valid_from, recorded_at);

CREATE INDEX EdgesByTarget ON Edges (tenant_id, target_id, edge_type);

CREATE PROPERTY GRAPH BankArchitecture
  NODE TABLES (
    Nodes
      KEY (tenant_id, node_id)
      LABEL Entity PROPERTIES (tenant_id, node_id, node_type, name, attributes)
  )
  EDGE TABLES (
    Edges
      KEY (tenant_id, source_id, edge_type, target_id, valid_from, recorded_at)
      SOURCE KEY (tenant_id, source_id) REFERENCES Nodes (tenant_id, node_id)
      DESTINATION KEY (tenant_id, target_id) REFERENCES Nodes (tenant_id, node_id)
      LABEL Relationship PROPERTIES (tenant_id, edge_type, valid_from, valid_to, recorded_at, superseded_at, attributes)
  );
