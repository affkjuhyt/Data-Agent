export interface FleetStats {
  content: { documents: number; web: number; media: number; warehouses: number };
  qdrant_collections: number;
  neo4j_nodes: number;
  neo4j_relationships: number;
  unresolved_conflict_batches: number;
}
