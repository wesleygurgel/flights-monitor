"""Leitura das respostas do Google Flights. Sem rede: usa uma resposta real gravada e respostas montadas."""
import json
from datetime import date
from pathlib import Path

from app.collectors import calendar_refs, parse_calendar, parse_search_payload

FIXTURES = Path(__file__).parent / "fixtures"


def search_payload():
    # busca real NAT -> GRU, 10/03 a 17/03/2027, gravada em 26/10/2026
    return json.loads((FIXTURES / "google_search_nat_gru.json").read_text(encoding="utf-8"))


def test_busca_inclui_principais_voos():
    """O mais barato (R$ 1.345, Azul) fica em "Principais voos"; a biblioteca só lia "Outros voos"."""
    flights, _ = parse_search_payload(search_payload())
    prices = sorted(f.price for f in flights)
    assert prices[0] == 1345
    assert len(flights) == 13  # 3 principais + 10 outros
    cheapest = min(flights, key=lambda f: f.price)
    assert cheapest.airlines == "Azul" and cheapest.stops == 1


def test_busca_le_indicador_de_preco():
    """A página dizia "Os preços estão baixos no momento"."""
    _, level = parse_search_payload(search_payload())
    assert level == "low"


def test_busca_sem_resultados():
    payload = search_payload()
    payload[2] = None
    payload[3] = [None]
    payload[5] = None
    assert parse_search_payload(payload) == ([], None)


def test_calendario_uma_pagina_cobre_60_dias():
    # o gráfico mostra de (ida de referência - 7) até +59 dias
    assert calendar_refs(("2027-03-01", "2027-04-29"), 7) == [("2027-03-08", "2027-03-15")]


def test_calendario_janela_longa_vira_varias_paginas():
    refs = calendar_refs(("2027-03-01", "2027-06-30"), 5)
    assert refs == [("2027-03-08", "2027-03-13"), ("2027-05-07", "2027-05-12"), ("2027-07-06", "2027-07-11")]


def calendar_body(entries):
    inner = [[None, []], [[out, ret, [[None, price], "token"], 1] for out, ret, price in entries]]
    line = json.dumps([["wrb.fr", None, json.dumps(inner)]])
    return f")]}}'\n\n{len(line)}\n{line}\n"


def test_calendario_filtra_janela_e_junta_paginas():
    a = calendar_body([("2027-02-28", "2027-03-07", 700), ("2027-03-01", "2027-03-08", 1116),
                       ("2027-03-02", "2027-03-09", 1108)])
    b = calendar_body([("2027-03-02", "2027-03-09", 1108), ("2027-03-03", "2027-03-10", 990)])
    out = parse_calendar([a, b], ("2027-03-01", "2027-03-02"))
    assert [(c.outbound_date, c.price) for c in out] == [("2027-03-01", 1116), ("2027-03-02", 1108)]


def test_calendario_com_erro_do_google_fica_vazio():
    erro = ")]}'\n\n39\n[[\"wrb.fr\",null,null,null,null,[13]]]\n"
    assert parse_calendar([erro], ("2027-03-01", "2027-03-30")) == []


def test_janela_ja_passou_nao_abre_navegador(monkeypatch):
    from app import collectors
    from app.config import Watch

    def boom(*a, **k):
        raise AssertionError("não devia abrir o navegador")

    monkeypatch.setattr(collectors, "_browser_calendar", boom)
    w = Watch(id="x", origin="NAT", destinations=["GRU"], depart_from="2020-01-01", depart_to="2020-01-31")
    assert collectors.GoogleFlightsCollector().coarse(w, "GRU", 7) == []
    assert date.today().year > 2020


def test_proxy_com_senha_para_o_navegador():
    from app.collectors import _playwright_proxy

    assert _playwright_proxy(None) is None
    assert _playwright_proxy("http://host:8080") == {"server": "http://host:8080"}
    assert _playwright_proxy("http://us%40r:s3nha@proxy.exemplo:3128") == {
        "server": "http://proxy.exemplo:3128", "username": "us@r", "password": "s3nha"}


def test_calendario_completo_ignora_resposta_parcial():
    from app.collectors import calendar_complete

    parcial = calendar_body([("2027-03-01", "2027-03-07", 1405)])
    cheia = calendar_body([(f"2027-03-{d:02d}", f"2027-03-{d + 6:02d}", 1000 + d) for d in range(1, 20)])
    assert not calendar_complete([parcial])
    assert calendar_complete([parcial, cheia])


def test_depois_do_captcha_pausa_o_calendario(monkeypatch):
    from app import collectors
    from app.config import Watch

    calls = []

    def captcha(urls):
        calls.append(urls)
        raise collectors.Blocked("Google pediu captcha")

    monkeypatch.setattr(collectors, "_browser_calendar", captcha)
    monkeypatch.setattr(collectors, "_blocked_until", 0.0)
    monkeypatch.setattr(collectors.settings, "request_delay", 0)
    w = Watch(id="x", origin="NAT", destinations=["GRU"], depart_from="2099-03-01", depart_to="2099-03-30")
    c = collectors.GoogleFlightsCollector()
    for _ in range(3):
        try:
            c.coarse(w, "GRU", 7)
        except collectors.Blocked:
            pass
    assert len(calls) == 1  # as outras tentativas nem abrem o navegador


def test_um_navegador_por_vez(monkeypatch):
    """Agendador e "Buscar agora" no mesmo processo dividem o perfil do Chromium, que só abre uma vez."""
    import threading
    import time as _time

    from app import collectors
    from app.config import Watch

    state = {"now": 0, "max": 0}

    def slow(urls):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        _time.sleep(0.05)
        state["now"] -= 1
        return []

    monkeypatch.setattr(collectors, "_browser_calendar", slow)
    monkeypatch.setattr(collectors.settings, "request_delay", 0)
    w = Watch(id="x", origin="NAT", destinations=["GRU"], depart_from="2099-03-01", depart_to="2099-03-30")
    threads = [threading.Thread(target=collectors.GoogleFlightsCollector().coarse, args=(w, "GRU", 7))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1


def test_calendario_ignora_resposta_malformada():
    """A biblioteca só trata erro de JSON; uma linha "[[]]" derrubava a varredura com IndexError."""
    ruim = ")]}'\n\n4\n[[]]\n"
    boa = calendar_body([("2027-03-01", "2027-03-08", 1116), ("2027-03-02", "2027-03-09", 1108)])
    from app.collectors import calendar_complete
    assert parse_calendar([ruim, boa], ("2027-03-01", "2027-03-02"))[0].price == 1116
    assert not calendar_complete([ruim])
