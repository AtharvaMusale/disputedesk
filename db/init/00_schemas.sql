-- Real and synthetic data live in separate schemas so they can never be mixed by accident.
CREATE SCHEMA IF NOT EXISTS real;
CREATE SCHEMA IF NOT EXISTS synthetic;
CREATE SCHEMA IF NOT EXISTS derived;
CREATE SCHEMA IF NOT EXISTS audit;
