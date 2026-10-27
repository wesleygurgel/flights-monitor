# Pendências

O que ainda não foi feito, em ordem de prioridade. Cada item diz por que importa e o que precisa para fechar.

Última revisão: 27/10/2026.

## Antes de confiar nos alertas

### 1. Confirmar a varredura do calendário em uso real
**Status:** a busca detalhada foi validada de verdade em 26/10/2026 (NAT → GRU/CGH, preços e companhias batendo com o site, link de compra certo). A varredura do calendário funciona, mas **ainda não rodou um ciclo completo sem captcha**.

O que mudou: o Google trocou o calendário de preços (`GetCalendarPicker` virou `GetCalendarGraph`) e passou a exigir um token anti-robô gerado no navegador. A biblioteca devolvia lista vazia sem erro. A varredura agora abre um Chromium headless (Playwright), clica em "Gráfico de preços" e lê a resposta. Testado: NAT → VCP e CGH, 60 datas por consulta, preço do calendário igual ao da busca detalhada (CGH 16/03, R$ 1.114). Depois de muitas consultas seguidas nos testes, o Google passou a pedir captcha; a rodada agora pausa o calendário por 30 min no primeiro captcha.

**Para fechar:**
- Com o IP descansado (no dia seguinte), rodar `docker compose exec monitor python -m app.main coarse --dry-run` e conferir no log `coarse ...: ok (15 consultas, 0 falhas, ~900 resultados)`.
- Acompanhar a primeira semana na tela da rota: se a varredura cair em captcha todo dia, reduzir destinos × durações (hoje 3 × 5 = 15 páginas por dia), aumentar `REQUEST_DELAY` ou usar `PROXY_URL`.
- Com 2 adultos, conferir se o calendário (`coarse`) devolve o preço por pessoa ou o total. A busca detalhada devolve o total; se o calendário for por pessoa, a escolha das candidatas e a comparação com o teto ficam inconsistentes.

**Risco conhecido:** o Google muda esse fluxo sem aviso (já mudou uma vez). O aviso de coleta quebrada existe para isso: a varredura vazia ou com erro conta como falha.

### 2. Ler o QR code do WhatsApp
**Status:** a Evolution API v2.3.7 subiu no compose, conectou no Postgres, criou a instância `monitor` e gerou o QR code. O endpoint e o corpo usados em `app/notifiers.py` (`POST /message/sendText/monitor` com `number` e `text`) são os dessa versão.

**Para fechar (precisa do Wesley):**
- Com o compose no ar, criar a instância e ler o QR code com um **chip secundário** (a integração é não oficial e o número pode ser bloqueado):
  `curl -X POST http://127.0.0.1:8080/instance/create -H "apikey: $EVOLUTION_APIKEY" -H 'content-type: application/json' -d '{"instanceName":"monitor","integration":"WHATSAPP-BAILEYS","qrcode":true}'`
  e abrir `http://127.0.0.1:8080/instance/connect/monitor` (com o header `apikey`) para pegar o QR em base64, ou usar o Manager em `http://127.0.0.1:8080/manager`.
- Mandar uma mensagem de teste e conferir que chega.

## Para rodar fora de casa

### 3. HTTPS e acesso pela internet
O painel sobe em HTTP na porta 8000. O código está pronto: com `HTTPS_ONLY=true` no `.env` o cookie de login só vai por conexão segura. Falta escolher e configurar o acesso (precisa de conta do Wesley):
- **Tailscale** (mais simples, só para os seus aparelhos): instalar no servidor e no celular e rodar `tailscale serve --bg 8000`.
- **Cloudflare Tunnel** (endereço público com domínio próprio): `cloudflared tunnel` apontando para `http://localhost:8000`.

## Melhorias de produto

### 4. Mais fontes de preço
Companhias aéreas direto (LATAM, GOL, Azul) e Skyscanner. Exigem navegador automatizado (Playwright) por causa do anti-bot. A interface `Collector` em `app/collectors.py` já permite adicionar uma fonte implementando `coarse()` e `fine()`. Falta também guardar a fonte em cada preço e comparar entre fontes no painel.

### 5. Preço por pessoa
Com mais de um adulto, o preço que o Google devolve é o total. O teto é informado como "total ida e volta", mas o painel não mostra o valor por pessoa. Avaliar se vale exibir os dois.

### 6. Bônus: faixas por clube e categoria
O alerta usa o percentual máximo do título ("até 100%"), que normalmente exige Clube Azul ou categoria elite. Falta registrar a sua faixa (ex.: Clube Azul 5.000, categoria Safira) e calcular o bônus que vale para você. Os valores das faixas estão no texto dos posts, não no título, então o parser precisaria ler o conteúdo completo.

### 7. Bônus: posts que citam mais de uma companhia
Posts de resumo ("LATAM e Azul com até 120% terminam hoje") viram campanha da Azul com o maior percentual citado, que pode ser da outra companhia. Hoje esses casos ficam marcados com "Atenção" no painel e no alerta. Para resolver, ler o conteúdo do post e separar os percentuais por companhia.

### 8. Bônus: fonte oficial da Azul
Além dos blogs, ler a página de promoções da Azul Fidelidade. Ela é carregada por JavaScript, então exigiria Playwright. Daria confirmação oficial e as faixas exatas.

### 9. Multiusuário e cobrança
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
- Correções da revisão de 26/10/2026 (todas com teste): campanha de bônus datada pelo post; aviso não entregue tenta de novo (bônus e passagens); banco da Evolution criado pelo Postgres e porta 8080 só local; editar parâmetros de preço reinicia o preço normal; rota com período vencido é pausada e avisa; datas passadas fora da busca; uma mensagem por rodada; candidatas em datas diferentes; busca manual fora do preço normal e reaproveitando varredura recente; `--run-now` não repete varredura recente; trava de migração no Postgres; SMTP na porta 465; textos e números do formulário.
- Backup diário do Postgres em `./backups`, guardando 14 dias. Espera as migrações antes do primeiro arquivo (antes saía vazio na primeira subida).
- Validação com dados reais (26/10/2026): busca detalhada no Google Flights, os 4 feeds de bônus (todos OK, inclusive Melhores Cartões em `/feed`) e o `docker compose` completo (db, monitor, web, backup, evolution).
- Busca detalhada lê "Principais voos" além de "Outros voos": a biblioteca descartava justamente os mais baratos (R$ 1.345 contra R$ 1.964 no teste).
- Varredura do calendário com Chromium headless (Playwright), perfil persistente por serviço, pausa de 30 min depois de captcha e um navegador por vez.
- Indicador "preço baixo" do Google lido da busca. Por decisão do Wesley ele só reforça um alerta (aparece como motivo), não dispara sozinho.
- Bônus: o parser ignora programas excluídos no texto ("Livelo e Esfera não estão presentes na campanha", "exceto", "ficou de fora") e lê prazos como "até quarta-feira (28)".
- Evolution API com versão fixa (`evoapicloud/evolution-api:v2.3.7`; a imagem `atendai/evolution-api` saiu do Docker Hub).
- `PROXY_URL` para a busca e o calendário (aceita usuário e senha).
- Limpeza diária (4h30) das varreduras do calendário com mais de 90 dias; também por `python -m app.main prune`.
- Monitor de bônus de transferência para a Azul: lê 4 blogs de milhas a cada 2 horas, agrupa os posts em campanhas, avisa campanhas acima do mínimo (padrão 80%) dos programas escolhidos, avisa de novo se o bônus subir, lembra no último dia (a menos que você marque "Já transferi") e avisa se um feed parar de funcionar. Aba "Bônus Azul" no painel com campanhas ativas, histórico de 6 meses por programa e preferências.
