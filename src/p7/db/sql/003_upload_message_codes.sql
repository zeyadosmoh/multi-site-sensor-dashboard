-- Upload log messages as code + parameters (p7.ingest.messages), rendered in the viewer's language.
-- uploads.warnings now holds [{"code": ..., "params": {...}}]; older rows may hold plain English strings, which
-- the UI shows as they are. `error` keeps the English text of the file-level error.
ALTER TABLE uploads
    ADD COLUMN error_code   text,
    ADD COLUMN error_params jsonb NOT NULL DEFAULT '{}';
