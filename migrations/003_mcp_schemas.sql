-- Add schemas column to store tool input schemas for validation
ALTER TABLE sandbox_mcp_servers
ADD COLUMN IF NOT EXISTS schemas JSONB;

-- schemas format: {
--   "tool_name": {
--     "type": "object",
--     "properties": {...},
--     "required": [...]
--   },
--   ...
-- }
