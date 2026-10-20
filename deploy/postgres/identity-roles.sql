\set ON_ERROR_STOP on
SELECT format('CREATE ROLE control_auth LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', :'auth')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='control_auth') \gexec
SELECT format('CREATE ROLE keycloak LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD %L', :'kc')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname='keycloak') \gexec
SELECT 'CREATE DATABASE med_identity OWNER keycloak'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='med_identity') \gexec
REVOKE ALL ON DATABASE med_identity FROM PUBLIC;
GRANT CONNECT ON DATABASE med_identity TO keycloak;
GRANT CONNECT ON DATABASE med_control TO control_auth;
