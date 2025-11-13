-- Skills installed per thread
CREATE TABLE IF NOT EXISTS sandbox_skills (
  thread_id TEXT NOT NULL,
  name TEXT NOT NULL,
  source TEXT NOT NULL,
  description TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (thread_id, name)
);

-- HTTP MCP servers bound per thread
CREATE TABLE IF NOT EXISTS sandbox_mcp_servers (
  thread_id TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  headers JSONB,
  auth JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (thread_id, name)
);
