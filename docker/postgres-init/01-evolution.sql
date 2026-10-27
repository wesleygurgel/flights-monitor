-- Banco da Evolution API (WhatsApp). O Postgres só roda este script ao criar o volume pela primeira vez;
-- num volume que já existe: docker compose exec db psql -U monitor -c "CREATE DATABASE evolution"
CREATE DATABASE evolution;
