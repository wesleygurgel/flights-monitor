"""Busca de aeroportos por nome de cidade, aeroporto, país ou código."""
from app import airports
from app.db import Route, SessionLocal


def codes(q, **kw):
    return [o["codes"] for o in airports.search(q, **kw)]


def test_busca_pelo_nome_do_aeroporto():
    assert codes("guarulhos")[0] == ["GRU"]


def test_busca_ignora_acento_e_maiusculas():
    assert codes("foz do iguacu")[0] == ["IGU"]
    assert codes("FOZ DO IGUAÇU")[0] == ["IGU"]


def test_busca_cidade_internacional_e_pais():
    assert codes("lima")[0] == ["LIM"]
    assert ["LIM"] in codes("lima peru")
    assert ["LIS"] in codes("lisboa")
    assert ["JFK"] in codes("nova york")


def test_codigo_exato_vem_primeiro():
    assert codes("gru")[0] == ["GRU"]
    assert codes("NAT")[0] == ["NAT"]


def test_cidade_com_varios_aeroportos_oferece_todos():
    res = airports.search("sao paulo")
    assert res[0]["codes"] == ["GRU", "CGH", "VCP"]
    assert "todos os aeroportos" in res[0]["label"]
    assert ["GRU"] in [o["codes"] for o in res]


def test_sem_grupos_para_origem():
    assert all(len(c) == 1 for c in codes("sao paulo", groups=False))


def test_brasil_antes_de_fora():
    # Natal fica no Brasil; não pode perder para cidades homônimas de fora
    assert codes("natal")[0] == ["NAT"]


def test_busca_vazia_ou_curta():
    assert airports.search("") == []
    assert airports.search("a") == []


def test_limite_de_resultados():
    assert len(airports.search("international", limit=8)) <= 8


def test_dados_do_aeroporto():
    a = airports.get("IGU")
    assert a.city == "Foz do Iguaçu" and a.region == "PR"
    assert airports.get("LIM").region == "Peru"
    assert airports.get("XYZ") is None


def test_rota_de_aeroportos(logged):
    r = logged.get("/aeroportos", params={"q": "guarulhos"})
    assert r.status_code == 200 and r.json()[0]["codes"] == ["GRU"]
    r = logged.get("/aeroportos", params={"q": "sao paulo", "grupos": "0"})
    assert all(len(o["codes"]) == 1 for o in r.json())


def _form(**kw):
    base = {"depart_from": "2030-01-10", "depart_to": "2030-02-10", "duration_days": "5",
            "duration_tolerance": "1", "adults": "1", "max_stops": "1", "max_price": "900", "drop_pct": "",
            "min_samples": "20", "candidates": "3"}
    return base | kw


def test_salvar_recusa_codigo_que_nao_existe(logged):
    r = logged.post("/gerenciar/salvar", data=_form(origin="XYZ", destinations="QQQ"))
    assert r.status_code == 400
    assert r.text.count("Escolha o aeroporto na lista") == 2


def test_salvar_com_codigos_da_lista(logged):
    r = logged.post("/gerenciar/salvar", data=_form(origin="NAT", destinations="IGU,LIM"), follow_redirects=False)
    assert r.status_code == 303
    rid = r.headers["location"].split("/")[2].split("?")[0]
    with SessionLocal() as s:
        assert s.get(Route, rid).destinations == "IGU,LIM"
    page = logged.get(f"/gerenciar/{rid}").text
    assert "Foz do Iguaçu" in page and "Lima" in page
    logged.post(f"/rotas/{rid}/excluir")


def test_nomes_da_base_aparecem_nas_telas():
    from app.web import fmt
    assert fmt.city("IGU") == "Foz do Iguaçu"
    assert fmt.airport("GRU") == "Guarulhos"
    assert fmt.city("ZZZ") == "ZZZ"
