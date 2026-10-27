# flights-monitor

Robô que monitora preços de passagens no Google Flights com datas flexíveis e bônus de transferência de pontos para a Azul, e avisa por e-mail/WhatsApp quando encontra algo bom.

## Como funciona

1. **Varredura grossa** (1x/dia): para cada rota da `watchlist.yaml`, consulta o calendário de preços do Google (uma chamada por destino x duração) e escolhe as N datas mais baratas.
2. **Busca fina** (3x/dia): nas datas candidatas, busca os voos reais, grava o histórico e avalia as regras.
3. **Regras ("o que é barato")**, em duas fases:
   - **Fase 1** — histórico curto: alerta se preço ≤ `max_price` (teto fixo).
   - **Fase 2** — a partir de `min_samples` buscas detalhadas (padrão 20, cerca de 1 semana): o critério principal passa a ser preço ≥ `drop_pct`% abaixo do preço normal da rota, que é a mediana do menor preço de cada busca dos últimos 30 dias. O teto fixo continua valendo como gatilho absoluto.
   - Extra: se o Google marcar o preço como "baixo", também alerta.
   - Mesma oferta não repete alerta em 24h, salvo nova queda ≥ 5%.
4. **Alerta**: e-mail (SMTP) e/ou WhatsApp (Evolution API), com link direto para o Google Flights.
5. **Saúde da coleta**: cada busca fica registrada. Se uma rota passar 2 rodadas seguidas sem preços (bloqueio do Google, mudança na página), chega um aviso pelos mesmos canais, e outro quando voltar a funcionar.

O que ainda falta está em [PENDENCIAS.md](PENDENCIAS.md). A ideia do projeto, as decisões e o mapa do código estão em [CLAUDE.md](CLAUDE.md).

## Bônus de transferência para a Azul

A cada 2 horas o robô lê os feeds de 4 blogs de milhas, reconhece os posts sobre bônus de transferência para a Azul Fidelidade e junta os posts sobre a mesma campanha. Você recebe aviso quando aparece uma campanha acima do seu mínimo (padrão 80%) para os programas que você usa, de novo se o bônus subir e um lembrete no último dia, a menos que marque "Já transferi" no painel.

```bash
python -m app.main bonus --dry-run   # lê os blogs agora; alertas só no console
```

Fontes, horário e preferências: `BONUS_FEEDS` e `BONUS_CRON` no `.env`, e a aba Bônus Azul do painel.

## Painel web

Três telas, pensadas para funcionar bem no celular:

- **Rotas** (`/`): a melhor oportunidade do momento e uma fita por rota (status, menor preço, últimos 30 dias, teto).
- **Detalhe** (`/rotas/<id>`): botão "Buscar agora", preço normal, gráfico de 30 dias com alertas, calendário de qual dia de ida sai mais barato, melhores voos da última busca com link para comprar, histórico de alertas e das últimas buscas.
- **Gerenciar rotas** (`/gerenciar`): criar, editar, pausar e excluir rotas.
- **Bônus Azul** (`/bonus`): campanhas ativas, histórico de 6 meses por programa, preferências de aviso e status das fontes.

As rotas ficam no banco. O `watchlist.yaml` só é lido na primeira execução, para popular o banco.
O acesso é protegido por `APP_PASSWORD` (sem ela o painel fica aberto e um aviso aparece no log). Com a senha ligada, o painel só sobe com uma `SECRET_KEY` de pelo menos 32 caracteres.

Para ver o painel com dados fictícios, sem esperar a coleta:

```bash
export DATABASE_URL=sqlite:///./demo.db COLLECTOR=mock   # mock: "Buscar agora" usa preços simulados
python -m app.main demo
python -m app.main web   # http://localhost:8000 (sem APP_PASSWORD, abre sem senha)
```

## Rodar sem Docker (teste rápido)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # ajuste DATABASE_URL=sqlite:///./monitor.db para começar
python -m app.main run --collector mock --dry-run   # valida o pipeline sem internet
python -m app.main run --dry-run                    # primeira consulta real (alertas só no console)
python -m app.main history
```

## Rodar com Docker

```bash
cp .env.example .env   # preencha SMTP e/ou Evolution
docker compose up -d --build
docker compose logs -f monitor
# painel em http://localhost:8000
```

### WhatsApp (Evolution API)

1. Suba o compose; abra `http://localhost:8080/manager` com a `EVOLUTION_APIKEY`. A porta só escuta na própria máquina; numa VPS, use um túnel SSH (`ssh -L 8080:localhost:8080 servidor`).
   O banco `evolution` é criado pelo Postgres na primeira subida (`docker/postgres-init/`). Se o volume já existia antes disso: `docker compose exec db psql -U monitor -c "CREATE DATABASE evolution"`.
2. Crie a instância `monitor` e leia o QR code com o celular que vai **enviar** os alertas (pode ser um chip secundário — é integração não oficial, há risco de bloqueio).
3. `WHATSAPP_TO` é o número que **recebe**, com DDI+DDD sem símbolos (ex.: `5584999999999`).

## Ajustes

- Rotas: pelo painel, em Gerenciar rotas.
- `COARSE_CRON` / `FINE_CRON` no `.env`: horários (fuso `TZ`).
- `REQUEST_DELAY`: pausa entre chamadas. Se aparecer HTTP 429, aumente e reduza `candidates`.

## Limitações conhecidas

- A biblioteca usa o RPC interno do Google Flights (não oficial); pode quebrar sem aviso. O coletor é um adaptador: trocar/adicionar fontes é implementar `coarse()` e `fine()` em `app/collectors.py`.
- IPs de datacenter costumam receber 429. Rode em casa, numa VPS pequena, ou use proxy residencial.
- A regra relativa só entra após `min_samples` buscas (padrão 20 ≈ 1 semana com 3 buscas/dia).

## Banco de dados

As migrações (Alembic) rodam sozinhas quando o painel ou o robô iniciam. Para criar uma nova depois de mudar `app/db.py`:

```bash
alembic revision --autogenerate -m "o que mudou"
```

O serviço `backup` do compose grava um dump diário em `./backups` e guarda os últimos 14 dias. Para restaurar:

```bash
gunzip -c backups/monitor-AAAA-MM-DD.sql.gz | docker compose exec -T db psql -U monitor monitor
```

## Testes

```bash
pip install pytest
python -m pytest -q
```

Rodam também no GitHub Actions a cada push (`.github/workflows/tests.yml`).
