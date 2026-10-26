# flights-monitor

Robô que monitora preços de passagens no Google Flights com datas flexíveis e alerta por e-mail/WhatsApp quando encontra um preço bom.

## Como funciona

1. **Varredura grossa** (1x/dia): para cada rota da `watchlist.yaml`, consulta o calendário de preços do Google (uma chamada por destino x duração) e escolhe as N datas mais baratas.
2. **Busca fina** (3x/dia): nas datas candidatas, busca os voos reais, grava o histórico e avalia as regras.
3. **Regras ("o que é barato")**, em duas fases:
   - **Fase 1** — histórico curto: alerta se preço ≤ `max_price` (teto fixo).
   - **Fase 2** — a partir de `min_samples` amostras da busca fina: o critério principal passa a ser preço ≥ `drop_pct`% abaixo da mediana dos últimos 30 dias. O teto fixo continua valendo como gatilho absoluto.
   - Bônus: se o Google marcar o preço como "baixo", também alerta.
   - Mesma oferta não repete alerta em 24h, salvo nova queda ≥ 5%.
4. **Alerta**: e-mail (SMTP) e/ou WhatsApp (Evolution API), com link direto para o Google Flights.

## Painel web

Três telas, pensadas para funcionar bem no celular:

- **Rotas** (`/`): a melhor oportunidade do momento e uma fita por rota (status, menor preço, últimos 30 dias, teto).
- **Detalhe** (`/rotas/<id>`): preço normal, gráfico de 30 dias com alertas, calendário de qual dia de ida sai mais barato, melhores voos da última busca com link para comprar e histórico de alertas.
- **Gerenciar rotas** (`/gerenciar`): criar, editar, pausar e excluir rotas.

As rotas ficam no banco. O `watchlist.yaml` só é lido na primeira execução, para popular o banco.
O acesso é protegido por `APP_PASSWORD` (sem ela o painel fica aberto e um aviso aparece no log).

Para ver o painel com dados fictícios, sem esperar a coleta:

```bash
DATABASE_URL=sqlite:///./demo.db APP_PASSWORD=teste python -m app.main demo
DATABASE_URL=sqlite:///./demo.db APP_PASSWORD=teste python -m app.main web   # http://localhost:8000
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

1. Suba o compose; abra `http://localhost:8080/manager` com a `EVOLUTION_APIKEY`.
2. Crie a instância `monitor` e leia o QR code com o celular que vai **enviar** os alertas (pode ser um chip secundário — é integração não oficial, há risco de bloqueio).
3. `WHATSAPP_TO` é o número que **recebe**, com DDI+DDD sem símbolos (ex.: `5584999999999`).

## Ajustes

- Rotas: pelo painel, em Gerenciar rotas.
- `COARSE_CRON` / `FINE_CRON` no `.env`: horários (fuso `TZ`).
- `REQUEST_DELAY`: pausa entre chamadas. Se aparecer HTTP 429, aumente e reduza `candidates`.

## Limitações conhecidas

- A biblioteca usa o RPC interno do Google Flights (não oficial); pode quebrar sem aviso. O coletor é um adaptador: trocar/adicionar fontes é implementar `coarse()` e `fine()` em `app/collectors.py`.
- IPs de datacenter costumam receber 429. Rode em casa, numa VPS pequena, ou use proxy residencial.
- A regra relativa só entra após `min_samples` amostras (padrão 60 ≈ 4 dias com 5 candidatas x 3 buscas/dia).

## Testes

```bash
pip install pytest
python -m pytest -q
```
