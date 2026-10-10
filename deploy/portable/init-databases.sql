-- First boot of an EMPTY PostgreSQL 16 volume only. Never a rotation procedure.
\set ON_ERROR_STOP on
\getenv siege_password SIEGE_DB_PASSWORD
\getenv mom_password MOM_DB_PASSWORD
CREATE ROLE siege_app LOGIN PASSWORD :'siege_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE mom_app LOGIN PASSWORD :'mom_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE siege OWNER siege_app;
CREATE DATABASE mom_bot OWNER mom_app;
REVOKE ALL ON DATABASE siege FROM PUBLIC;
REVOKE ALL ON DATABASE mom_bot FROM PUBLIC;
