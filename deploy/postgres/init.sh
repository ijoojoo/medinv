#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v mr="$MED_RUNTIME_PASSWORD" -v mm="$MED_MIGRATOR_PASSWORD" \
  -v cr="$CONTROL_RUNTIME_PASSWORD" -v cm="$CONTROL_MIGRATOR_PASSWORD" \
  -v ca="$CONTROL_AUTH_PASSWORD" -v kc="$KEYCLOAK_DB_PASSWORD" <<'SQL'
CREATE ROLE med_owner NOLOGIN;
CREATE ROLE med_migrator LOGIN NOINHERIT PASSWORD :'mm';
CREATE ROLE med_runtime LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'mr';
GRANT med_owner TO med_migrator;
CREATE ROLE control_owner NOLOGIN;
CREATE ROLE control_migrator LOGIN NOINHERIT PASSWORD :'cm';
CREATE ROLE control_runtime LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'cr';
GRANT control_owner TO control_migrator;
CREATE ROLE control_auth LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'ca';
CREATE ROLE keycloak LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'kc';
CREATE DATABASE med_identity OWNER keycloak;
REVOKE ALL ON DATABASE med_identity FROM PUBLIC;
GRANT CONNECT ON DATABASE med_identity TO keycloak;
CREATE DATABASE med_business OWNER med_owner;
CREATE DATABASE med_control OWNER control_owner;
REVOKE ALL ON DATABASE med_business, med_control FROM PUBLIC;
GRANT CONNECT ON DATABASE med_business TO med_migrator, med_runtime;
GRANT CONNECT ON DATABASE med_control TO control_migrator, control_runtime, control_auth;
\connect med_business
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA med AUTHORIZATION med_owner;
GRANT USAGE ON SCHEMA med TO med_runtime;
\connect med_control
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA control AUTHORIZATION control_owner;
GRANT USAGE ON SCHEMA control TO control_runtime;
SQL
