-- Runs once, on the first start with an empty data volume.
-- Integration tests use this separate database, so they never touch development data.
CREATE DATABASE deskpilot_test OWNER deskpilot;