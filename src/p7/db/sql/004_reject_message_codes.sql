-- Row reject messages as code + parameters (p7.ingest.messages, codes row_*), so rejects.csv is written in the
-- viewer's language. `message` keeps the English text (CLI, psql); rows stored before this have no code and are
-- exported with that English text.
ALTER TABLE row_rejects
    ADD COLUMN message_code   text,
    ADD COLUMN message_params jsonb NOT NULL DEFAULT '{}';
