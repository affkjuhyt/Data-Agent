-- routines/workspace.sql: what D4.11 (tab 3b) writes in words for schema workspace: the tile
-- position uniqueness, deferred so a reorder can swap positions inside one transaction.

-- covers: workspace.tile#0
ALTER TABLE workspace.tile ADD CONSTRAINT tile_workspace_id_position_key
  UNIQUE (workspace_id, position) DEFERRABLE INITIALLY DEFERRED;
