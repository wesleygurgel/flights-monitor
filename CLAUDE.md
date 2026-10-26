# flights-monitor

Contexto do projeto para o Claude. Leia antes de mudar qualquer coisa. O que falta fazer está em [PENDENCIAS.md](PENDENCIAS.md); como rodar está no [README.md](README.md).

## A ideia

Robô que monitora preços de passagens aéreas várias vezes ao dia e avisa por e-mail e WhatsApp, com link para comprar, quando aparece um preço muito barato.

Segunda funcionalidade: **monitor de bônus de transferência de pontos para a Azul Fidelidade** (Livelo, Esfera, bancos → Azul), com aviso quando aparece uma campanha boa (ver "Bônus Azul" abaixo).

- Dono: Wesley (Natal, RN). Uso pessoal por enquanto; pode virar produto (SaaS para o mercado brasileiro) se funcionar bem.
- Caso de teste: Natal (NAT) para São Paulo (GRU, CGH ou VCP), ida em março ou abril de 2027, viagem de 7 dias com 2 de tolerância.
- Datas flexíveis são o ponto central: a pessoa informa uma janela de ida e a duração, e o robô descobre sozinho quais dias saem mais baratos.

### O que é "barato" (decisão do Wesley)

1. **Fase 1:** enquanto há pouco histórico, vale um teto fixo definido pela pessoa (ex.: R$ 900).
2. **Fase 2:** com amostras suficientes (`min_samples`, padrão 20 buscas, cerca de 1 semana), o critério principal vira "X% abaixo do preço normal da rota" (`drop_pct`, padrão 20%). O teto fixo continua valendo como gatilho absoluto.

O **preço normal** é a mediana do menor preço de cada busca detalhada dos últimos 30 dias: **uma amostra por busca**, nunca todas as ofertas. Usar todas inflava o normal com tarifas caras de outras datas e companhias, gerando alertas falsos (bug corrigido; não reintroduzir). Mediana em vez de média para um valor isolado absurdo não distorcer.

## Como funciona

```
agendador (APScheduler)
  ├─ varredura grossa 1x/dia (COARSE_CRON) ─ calendário de preços do Google por destino × duração
  │     → escolhe as N datas mais baratas (candidates)
  └─ busca fina 3x/dia (FINE_CRON) ─ voos reais nas datas candidatas
        → grava preços → regras (app/rules.py) → alerta (e-mail/WhatsApp) → dedup 24h
cada rodada → search_runs (status, consultas, falhas, melhor preço)
  → alimenta o preço normal, o status no painel e o aviso de coleta quebrada (2 falhas seguidas)
painel web (FastAPI) lê o mesmo banco
```

- Fonte de preços: Google Flights pela biblioteca não oficial `fast-flights` (fork `jumping2000/google-flights`, que tem `get_calendar_graph`). Pode quebrar sem aviso e o Google bloqueia IP de datacenter (HTTP 429). Por isso o coletor é um adaptador trocável e existe o monitoramento de saúde.
- Amadeus Self-Service foi descartado: a API foi encerrada em julho de 2026.
- Skyscanner e companhias aéreas direto ficaram para depois (exigem Playwright por causa do anti-bot).

## Bônus Azul

- A Azul não tem API de promoções e a página oficial é carregada por JavaScript. A fonte são os **feeds RSS de blogs de milhas** (Passageiro de Primeira, Pontos pra Voar, Melhores Destinos, Melhores Cartões), que publicam toda campanha com título padronizado ("Azul Fidelidade oferece até 90% de bônus nas transferências de pontos da Livelo até 12 de agosto de 2026").
- `app/bonus/parser.py` extrai do título o programa de origem, o percentual máximo e a data final. Ignora posts em que a Azul é a origem ("da Azul para ALL Accor") e posts só de outras companhias. Casos reais ficam em `tests/test_bonus_parser.py`: **ao mexer no parser, adicione o título que motivou a mudança como caso de teste**.
- Os posts de vários blogs sobre a mesma campanha (mesmo programa, data final igual ou próxima, até 21 dias) viram uma `BonusCampaign`.
- Regras de aviso (decisão do Wesley; padrão: todos os programas, mínimo 80%): avisa uma vez por campanha, de novo se o percentual subir e lembra no último dia, a menos que ele marque "Já transferi". Preferências na tabela `bonus_settings`, editáveis na aba Bônus Azul.
- Cada feed é monitorado como uma rota: `search_runs` com `watch_id = "bonus:<fonte>"` e `kind = "feed"`, e aviso depois de 2 falhas seguidas.
- Leitura a cada 2 horas (`BONUS_CRON`); fontes trocáveis por `BONUS_FEEDS` no `.env`.

## Mapa do código

| Arquivo | Papel |
|---|---|
| `app/config.py` | `Settings` (variáveis de ambiente) e o dataclass `Watch` (uma rota em memória) |
| `app/db.py` | Modelos SQLAlchemy (`Route`, `PriceSnapshot`, `SearchRun`, `Alert`), `baseline()` (preço normal), `migrate()`/`init_db()` |
| `app/collectors.py` | Interface `Collector` com `coarse()` e `fine()`; `GoogleFlightsCollector` (real) e `MockCollector` (testes/demo) |
| `app/rules.py` | `evaluate()`: decide se uma oferta vira alerta; fases 1 e 2, dedup de 24h |
| `app/pipeline.py` | `run_watch()` (ciclo completo de uma rota), `tracked_run()` (registra a rodada), `check_health()` (aviso de falha/recuperação) |
| `app/bonus/parser.py` | Lê títulos de posts e extrai programa, % de bônus e data final |
| `app/bonus/sources.py` | Feeds RSS (`RssSource`), leitura de RSS/Atom, fontes simuladas para testes |
| `app/bonus/service.py` | `run_bonus()`: lê fontes, agrupa campanhas, alertas, lembrete do último dia, saúde dos feeds |
| `app/notifiers.py` | E-mail (SMTP), WhatsApp (Evolution API), console; textos das mensagens |
| `app/main.py` | CLI: `run`, `coarse`, `fine`, `bonus`, `serve` (agendador), `history`, `demo`, `web` |
| `app/demo.py` | Popula 30 dias de dados fictícios para ver o painel |
| `app/web/app.py` | Rotas HTTP do painel, login, "Buscar agora" (thread em background) |
| `app/web/queries.py` | Monta os dados de cada tela (`route_view`, `hero`, `detail`) |
| `app/web/charts.py` | Gráficos SVG calculados no servidor (sparkline, histórico 30 dias, calendário de preços) |
| `app/web/fmt.py` | Formatação pt-BR (R$, datas), nomes de aeroportos e códigos de cidade (SAO, RIO…) |
| `app/web/templates/`, `app/web/static/app.css` | Jinja2 + HTMX; CSS com os tokens do design |
| `migrations/` | Alembic; rodado sozinho ao iniciar |
| `tests/` | Pytest; `conftest.py` define SQLite temporário e `COLLECTOR=mock` (que também troca os feeds por fontes simuladas) |

As rotas vivem na tabela `routes`. O `watchlist.yaml` só popula o banco na primeira execução.

## Painel e design

Telas: **Rotas** (`/`), **Detalhe** (`/rotas/<id>`), **Gerenciar** (`/gerenciar`), **Bônus Azul** (`/bonus`) e login. As três primeiras foram aprovadas em design pelo Wesley; a de bônus segue o mesmo padrão (cada campanha é uma fita). Ele opera muito pelo celular, então toda tela precisa funcionar a 390 px de largura.

Direção visual: **carta aeronáutica + fitas de progresso de voo** (as tiras que o controle de tráfego aéreo usa). Cada rota é uma fita horizontal dividida em compartimentos.

- Cores: fundo `#EEF2F1`, papel `#FBFCFB`, tinta `#1B2A3A`, texto secundário `#4E5D6C`, azul de carta `#1F5D99`, magenta `#A3195B`.
- **Magenta significa "barato" e só isso** (mais os erros). Não usar magenta para decoração.
- Tipografia: Barlow Condensed (títulos, códigos IATA, preços) + Barlow (texto).
- Evitar: rótulos em CAIXA ALTA, "→" em links, fonte monoespaçada em rótulos, gradientes e grade de cards arredondados iguais.
- Textos em português do Brasil, frases curtas, sem jargão técnico para o usuário ("preço normal", não "mediana"; "buscas", não "snapshots").

## Comandos

```bash
pip install -r requirements.txt pytest pyflakes
python -m pytest -q                      # testes (usam mock, sem internet)
python -m pyflakes app tests             # lint usado no CI

# painel com dados fictícios
export DATABASE_URL=sqlite:///./demo.db COLLECTOR=mock
python -m app.main demo && python -m app.main web     # http://localhost:8000

python -m app.main run --dry-run         # coleta real, alertas só no console
python -m app.main bonus --dry-run       # lê os feeds de bônus de verdade, alertas só no console
alembic revision --autogenerate -m "..." # nova migração depois de mudar app/db.py
docker compose up -d --build             # db, monitor, web, backup, evolution
```

## Convenções

- **Planejar e aprovar antes de implementar:** o Wesley prefere ver o plano antes do código. Mudanças pequenas e óbvias podem ir direto.
- Comunicação direta e concisa, em português.
- Toda mudança no modelo (`app/db.py`) vem com migração Alembic.
- Testes novos usam `COLLECTOR=mock`. Nunca bater no Google em teste.
- Commits em português, na branch `main` (projeto pessoal, sem PR por enquanto).
- Um canal de alerta falhar não pode derrubar os outros, e uma rota com problema não pode parar as outras.
- Não guardar segredos no repositório: tudo via `.env` (modelo em `.env.example`).

## Estado atual

Implementado e testado com dados simulados. **A coleta real no Google Flights ainda não foi validada**, nem a leitura real dos feeds de bônus, nem o WhatsApp pela Evolution API, nem o `docker compose` completo. Esses são os primeiros itens de `PENDENCIAS.md`. Ao concluir uma pendência, mova-a para a seção "Feito" desse arquivo.
