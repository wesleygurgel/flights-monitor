# Pendências

O que ainda não foi feito, em ordem de prioridade. Cada item diz por que importa e o que precisa para fechar.

Última revisão: 26/10/2026.

## Antes de confiar nos alertas

### 1. Validar a coleta real no Google Flights
**Status:** não testado. O coletor real (`GoogleFlightsCollector`) nunca rodou com sucesso. No ambiente de desenvolvimento o Google respondeu HTTP 429 (bloqueio de IP de datacenter). Todo o resto foi testado com o coletor simulado (`--collector mock`).

**Para fechar:**
- Rodar `python -m app.main run --dry-run` de uma rede doméstica e conferir no log se chegam preços na varredura (`coarse`) e na busca detalhada (`fine`).
- Conferir se `get_calendar_graph` devolve preços para NAT → GRU/CGH/VCP e se os valores batem com o site do Google Flights.
- Conferir se o link de compra (`Query.url()`) abre a busca certa.
- Se vier 429: aumentar `REQUEST_DELAY`, reduzir `candidates` e o número de destinos × durações por rota.

**Risco conhecido:** a biblioteca usada (fork `jumping2000/google-flights` do `fast-flights`) é não oficial e lê o RPC interno do Google. Pode quebrar sem aviso. O aviso de coleta quebrada (ver "Feito", no fim) existe justamente para isso.

### 2. Configurar e fixar a versão da Evolution API (WhatsApp)
**Status:** o `docker-compose.yml` usa `atendai/evolution-api:latest`, e a configuração nunca foi testada.

**Para fechar:**
- Subir o compose, criar a instância `monitor`, ler o QR code e mandar uma mensagem de teste.
- Trocar `latest` pela versão que funcionou e conferir se as variáveis de ambiente do serviço batem com a documentação dessa versão (a v2 pode exigir Redis ou outras chaves).
- Conferir se o endpoint `POST /message/sendText/{instancia}` e o formato do corpo usados em `app/notifiers.py` são os dessa versão.
- Usar um chip secundário para enviar: a integração é não oficial e o número pode ser bloqueado pelo WhatsApp.

### 3. Validar o `docker compose` completo
**Status:** os serviços (`db`, `monitor`, `web`, `backup`, `evolution`) foram escritos mas não executados juntos.

**Para fechar:** `docker compose up -d --build`, conferir os logs de cada serviço, abrir o painel em `:8000` e verificar se o primeiro arquivo aparece em `./backups`.

### 4. Indicador de preço baixo do Google
A regra "Google indica preço baixo" depende do campo `current_price` (low/typical/high). O fork da biblioteca usado hoje não devolve esse campo, então essa regra nunca dispara. Verificar na primeira coleta real se há como obter esse dado; se não houver, remover a regra.

## Para rodar fora de casa

### 5. Proxy residencial
Se o robô rodar numa VPS, o Google tende a bloquear o IP. `get_flights()` aceita `proxy=`; falta expor isso como variável (`PROXY_URL`) e passar para as duas chamadas do coletor. Custo típico: serviço de proxy residencial pago por GB.

### 6. HTTPS e acesso pela internet
O painel sobe em HTTP na porta 8000. Para acessar pelo celular fora de casa, colocar atrás de um proxy reverso com HTTPS (Caddy ou Traefik) ou de um túnel (Cloudflare Tunnel, Tailscale). Com HTTPS, ligar `https_only=True` no `SessionMiddleware` em `app/web/app.py`.

### 7. Limpeza do histórico
Cada varredura grava um preço por data × destino × duração (na rota de exemplo, cerca de 900 linhas por dia). Não é problema no primeiro ano, mas falta uma rotina que apague snapshots de varredura com mais de 90 dias, mantendo `search_runs` e `alerts`.

## Melhorias de produto

### 8. Mais fontes de preço
Companhias aéreas direto (LATAM, GOL, Azul) e Skyscanner. Exigem navegador automatizado (Playwright) por causa do anti-bot. A interface `Collector` em `app/collectors.py` já permite adicionar uma fonte implementando `coarse()` e `fine()`. Falta também guardar a fonte em cada preço e comparar entre fontes no painel.

### 9. Preço por pessoa
Com mais de um adulto, o preço que o Google devolve é o total. O teto é informado como "total ida e volta", mas o painel não mostra o valor por pessoa. Avaliar se vale exibir os dois.

### 10. Multiusuário e cobrança
Só se o uso pessoal der certo: contas de usuário, rotas por usuário, limites por plano e cobrança. Exige trocar o login de senha única por autenticação de verdade.

## Feito (para referência)

- Coletor Google Flights com varredura de calendário + busca detalhada nas datas mais baratas.
- Regra de alerta em duas fases: teto fixo e depois queda sobre o preço normal da rota.
- Preço normal calculado com uma amostra por busca (o menor preço de cada rodada), não com todas as ofertas.
- Registro de cada busca (`search_runs`), aviso por e-mail/WhatsApp quando a coleta de uma rota falha 2 vezes seguidas e quando volta a funcionar, e status das buscas no painel.
- Botão "Buscar agora" no detalhe da rota.
- Painel web: rotas, detalhe da rota e gerenciamento de rotas, com login.
- Migrações de banco com Alembic, aplicadas sozinhas ao iniciar.
- O painel recusa subir com `SECRET_KEY` fraca quando a senha está ligada.
- Testes rodando no GitHub Actions a cada push.
- Backup diário do Postgres em `./backups`, guardando 14 dias.
