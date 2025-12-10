-- 001_create_change_requests.sql
CREATE TABLE IF NOT EXISTS change_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device TEXT NOT NULL,
    interface TEXT NOT NULL,
    requester TEXT NOT NULL,
    config TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    approver TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_change_requests_device ON change_requests(device);
CREATE INDEX IF NOT EXISTS idx_change_requests_status ON change_requests(status);
