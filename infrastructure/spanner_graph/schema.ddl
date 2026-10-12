-- Deploy with gcloud spanner databases ddl update <database> --instance=<instance> --ddl-file=schema.ddl
-- Entity properties intentionally exclude direct customer PII.
CREATE TABLE Nodes (
  node_id STRING(128) NOT NULL,
  node_type STRING(64) NOT NULL,
  name STRING(256) NOT NULL,
  attributes JSON
) PRIMARY KEY (node_id);

CREATE TABLE Edges (
  source_id STRING(128) NOT NULL,
  target_id STRING(128) NOT NULL,
  edge_type STRING(64) NOT NULL,
  attributes JSON
) PRIMARY KEY (source_id, target_id, edge_type);

-- Spanner Graph exposes the same topology used by knowledge_graph/store.py.
CREATE PROPERTY GRAPH BankArchitecture
  NODE TABLES (
    Nodes
      KEY (node_id)
      LABEL Entity
        PROPERTIES (node_id, node_type, name, attributes)
  )
  EDGE TABLES (
    Edges
      KEY (source_id, target_id, edge_type)
      SOURCE KEY (source_id) REFERENCES Nodes (node_id)
      DESTINATION KEY (target_id) REFERENCES Nodes (node_id)
      LABEL Relationship
        PROPERTIES (edge_type, attributes)
  );
