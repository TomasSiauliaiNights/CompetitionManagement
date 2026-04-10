-- Robotics Tournament Database Schema
-- Run this once: psql -U postgres -f schema.sql -d tournament

CREATE TABLE IF NOT EXISTS robots (
    id SERIAL PRIMARY KEY,
    number VARCHAR(20) NOT NULL,
    name VARCHAR(100) NOT NULL DEFAULT '',
    category VARCHAR(30) NOT NULL,  -- 'line_following', 'fire_sister', 'folkrace'
    inspection BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(number, category)
);

CREATE TABLE IF NOT EXISTS trials (
    id SERIAL PRIMARY KEY,
    robot_id INTEGER REFERENCES robots(id) ON DELETE CASCADE,
    trial_num INTEGER NOT NULL,
    value VARCHAR(50),             -- time string '0:07.699', integer points '500', or 'DNF'
    value_ms INTEGER,              -- parsed time in ms (NULL for points/DNF)
    value_points INTEGER,          -- parsed points (NULL for time/DNF)
    is_dnf BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS folkrace_groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    sort_order INTEGER DEFAULT 0,
    section VARCHAR(10) DEFAULT 'L'   -- 'L' for left/quals, 'R' for right/semis/finals
);

CREATE TABLE IF NOT EXISTS folkrace_entries (
    id SERIAL PRIMARY KEY,
    group_id INTEGER REFERENCES folkrace_groups(id) ON DELETE CASCADE,
    robot_id INTEGER REFERENCES robots(id) ON DELETE CASCADE,
    r1 INTEGER DEFAULT 0,
    r2 INTEGER DEFAULT 0,
    r3 INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0
);

-- Useful views
CREATE OR REPLACE VIEW v_lf_scoreboard AS
SELECT r.number, r.name, r.id as robot_id,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 1 LIMIT 1) AS t1,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 2 LIMIT 1) AS t2,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 3 LIMIT 1) AS t3,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 4 LIMIT 1) AS t4,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 5 LIMIT 1) AS t5,
    (SELECT MIN(value_ms) FROM trials WHERE robot_id = r.id AND value_ms IS NOT NULL) AS best_ms
FROM robots r WHERE r.category = 'line_following'
ORDER BY best_ms NULLS LAST, r.number;

CREATE OR REPLACE VIEW v_fs_scoreboard AS
SELECT r.number, r.name, r.id as robot_id,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 1 LIMIT 1) AS t1,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 2 LIMIT 1) AS t2,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 3 LIMIT 1) AS t3,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 4 LIMIT 1) AS t4,
    (SELECT value FROM trials WHERE robot_id = r.id AND trial_num = 5 LIMIT 1) AS t5,
    (SELECT MAX(value_points) FROM trials WHERE robot_id = r.id AND value_points IS NOT NULL) AS best_pts
FROM robots r WHERE r.category = 'fire_sister'
ORDER BY best_pts DESC NULLS LAST, r.number;
