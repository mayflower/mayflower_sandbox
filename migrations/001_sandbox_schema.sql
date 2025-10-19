-- Sandbox Sessions Table
-- Tracks active sandbox sessions and their expiration
CREATE TABLE IF NOT EXISTS sandbox_sessions (
    thread_id TEXT PRIMARY KEY,
    last_accessed TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sandbox_sessions_expires_at
    ON sandbox_sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sandbox_sessions_last_accessed
    ON sandbox_sessions(last_accessed);

-- Sandbox Filesystem Table
-- Stores all files for all threads with 20MB limit per file
CREATE TABLE IF NOT EXISTS sandbox_filesystem (
    thread_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    content BYTEA NOT NULL,
    content_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    modified_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, file_path),
    CONSTRAINT fk_thread FOREIGN KEY (thread_id)
        REFERENCES sandbox_sessions(thread_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_size CHECK (size <= 20971520)  -- 20 MB in bytes
);

CREATE INDEX IF NOT EXISTS idx_sandbox_filesystem_thread_id
    ON sandbox_filesystem(thread_id);
CREATE INDEX IF NOT EXISTS idx_sandbox_filesystem_modified_at
    ON sandbox_filesystem(modified_at);

-- Session Bytes Storage (for Pyodide state)
-- Separate table to avoid bloating main sessions table
CREATE TABLE IF NOT EXISTS sandbox_session_bytes (
    thread_id TEXT PRIMARY KEY,
    session_bytes BYTEA,
    session_metadata JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP DEFAULT NOW(),
    CONSTRAINT fk_session FOREIGN KEY (thread_id)
        REFERENCES sandbox_sessions(thread_id)
        ON DELETE CASCADE
);
