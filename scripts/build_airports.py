"""Gera app/data/airports.csv a partir do OurAirports (domínio público).

Uso: python scripts/build_airports.py [pasta com airports.csv]
Sem pasta, baixa https://davidmegginson.github.io/ourairports-data/airports.csv.
Nomes de países em português vêm do pacote iso-codes do sistema (apt install iso-codes);
sem ele ficam em inglês. Só roda à mão, para atualizar a base: nunca em teste nem em produção.
"""
from __future__ import annotations

import csv
import gettext
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

SRC = "https://davidmegginson.github.io/ourairports-data/airports.csv"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "airports.csv"
SIZES = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}

# nomes curtos já usados nas telas (cidade, aeroporto)
SHORT = {
    "NAT": ("Natal", "Natal"),
    "GRU": ("São Paulo", "Guarulhos"), "CGH": ("São Paulo", "Congonhas"), "VCP": ("São Paulo", "Viracopos"),
    "GIG": ("Rio de Janeiro", "Galeão"), "SDU": ("Rio de Janeiro", "Santos Dumont"),
    "BSB": ("Brasília", "Brasília"), "CNF": ("Belo Horizonte", "Confins"), "PLU": ("Belo Horizonte", "Pampulha"),
    "REC": ("Recife", "Recife"), "FOR": ("Fortaleza", "Fortaleza"), "SSA": ("Salvador", "Salvador"),
    "JPA": ("João Pessoa", "João Pessoa"), "MCZ": ("Maceió", "Maceió"), "AJU": ("Aracaju", "Aracaju"),
    "POA": ("Porto Alegre", "Salgado Filho"), "CWB": ("Curitiba", "Afonso Pena"),
    "FLN": ("Florianópolis", "Hercílio Luz"), "BEL": ("Belém", "Belém"), "MAO": ("Manaus", "Manaus"),
    "GYN": ("Goiânia", "Goiânia"), "CXJ": ("Caxias do Sul", "Caxias do Sul"),
    "FEN": ("Fernando de Noronha", "Noronha"), "IGU": ("Foz do Iguaçu", "Cataratas"),
    "LIS": ("Lisboa", "Lisboa"), "OPO": ("Porto", "Porto"), "MAD": ("Madri", "Barajas"),
    "CDG": ("Paris", "Charles de Gaulle"), "ORY": ("Paris", "Orly"), "MIA": ("Miami", "Miami"),
    "MCO": ("Orlando", "Orlando"), "JFK": ("Nova York", "JFK"), "EWR": ("Nova York", "Newark"),
    "LGA": ("Nova York", "LaGuardia"), "EZE": ("Buenos Aires", "Ezeiza"), "AEP": ("Buenos Aires", "Aeroparque"),
    "SCL": ("Santiago", "Santiago"), "MVD": ("Montevidéu", "Carrasco"), "LIM": ("Lima", "Jorge Chávez"),
    "LHR": ("Londres", "Heathrow"), "LGW": ("Londres", "Gatwick"), "STN": ("Londres", "Stansted"),
    "LTN": ("Londres", "Luton"), "LCY": ("Londres", "London City"),
    "NRT": ("Tóquio", "Narita"), "HND": ("Tóquio", "Haneda"), "FCO": ("Roma", "Fiumicino"),
    "BCN": ("Barcelona", "El Prat"), "MEX": ("Cidade do México", "Benito Juárez"),
}

# cidade em inglês (como vem na fonte) -> português
CITY_PT = {
    "New York": "Nova York", "London": "Londres", "Lisbon": "Lisboa", "Madrid": "Madri", "Seville": "Sevilha",
    "Mexico City": "Cidade do México", "Rome": "Roma", "Milan": "Milão", "Munich": "Munique",
    "Geneva": "Genebra", "Zurich": "Zurique", "Brussels": "Bruxelas", "Copenhagen": "Copenhague",
    "Stockholm": "Estocolmo", "Vienna": "Viena", "Prague": "Praga", "Warsaw": "Varsóvia", "Athens": "Atenas",
    "Istanbul": "Istambul", "Moscow": "Moscou", "Saint Petersburg": "São Petersburgo", "Cairo": "Cairo",
    "Cape Town": "Cidade do Cabo", "Tokyo": "Tóquio", "Beijing": "Pequim", "Shanghai": "Xangai",
    "Seoul": "Seul", "Singapore": "Singapura", "Montevideo": "Montevidéu", "Panama City": "Cidade do Panamá",
    "Philadelphia": "Filadélfia", "New Orleans": "Nova Orleans", "Edinburgh": "Edimburgo",
    "Amsterdam": "Amsterdã", "Frankfurt am Main": "Frankfurt", "Florence": "Florença", "Venice": "Veneza",
    "Naples": "Nápoles", "Berlin": "Berlim", "Hamburg": "Hamburgo", "Cologne": "Colônia",
    "Marseille": "Marselha", "New Delhi": "Nova Délhi", "Bangkok": "Bangcoc", "Montréal": "Montreal",
    "San Carlos de Bariloche": "Bariloche", "Havana": "Havana", "Lima": "Lima", "Dubai": "Dubai",
    "Barcelona": "Barcelona", "Porto": "Porto", "Turin": "Turim", "Bucharest": "Bucareste",
    "Budapest": "Budapeste", "Dublin": "Dublin", "Oslo": "Oslo", "Helsinki": "Helsinque",
    "Johannesburg": "Joanesburgo", "Casablanca": "Casablanca", "Marrakesh": "Marrakech",
    "Jerusalem": "Jerusalém", "Washington": "Washington", "Saint Petersburg (Leningrad)": "São Petersburgo",
}

SUFFIX = re.compile(r"\s+(International\s+)?(Air(port|field|base)|Aerodrome)$", re.I)


def short_name(name: str) -> str:
    """'São Paulo/Guarulhos–Governor André Franco Montoro International Airport' -> 'Guarulhos'."""
    if "/" in name:
        name = name.split("/", 1)[1]
    name = re.split(r"\s*[–—]\s*", name, maxsplit=1)[0]
    name = SUFFIX.sub("", name)
    return re.sub(r"^(Aeroporto|Aeropuerto)\s+(Internacional\s+)?(de\s+|del\s+)?", "", name).strip() or name


def countries_pt() -> dict[str, str]:
    try:
        data = json.loads(Path("/usr/share/iso-codes/json/iso_3166-1.json").read_text())["3166-1"]
        tr = gettext.translation("iso_3166-1", languages=["pt_BR"])
    except (OSError, KeyError):
        return {}
    return {c["alpha_2"]: tr.gettext(c["name"]) for c in data}


def main() -> None:
    if len(sys.argv) > 1:
        text = (Path(sys.argv[1]) / "airports.csv").read_text(encoding="utf-8")
    else:
        text = urllib.request.urlopen(SRC, timeout=60).read().decode("utf-8")
    countries = countries_pt()
    rows: dict[str, tuple] = {}
    for r in csv.DictReader(io.StringIO(text)):
        code = r["iata_code"].strip().upper()
        if r["scheduled_service"] != "yes" or r["type"] not in SIZES or not re.fullmatch(r"[A-Z]{3}", code):
            continue
        town = r["municipality"].strip() or short_name(r["name"])
        city, name = SHORT.get(code, (CITY_PT.get(town, town), short_name(r["name"])))
        cc = r["iso_country"]
        region = r["iso_region"].split("-", 1)[1] if cc == "BR" else countries.get(cc, cc)
        aliases = {r["name"], town, r["keywords"]} - {city, name, ""}
        row = (code, city, name, region, cc, SIZES[r["type"]], " | ".join(sorted(aliases)))
        if code not in rows or row[5] < rows[code][5]:  # código repetido: fica o aeroporto maior
            rows[code] = row
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(("code", "city", "name", "region", "country", "size", "aliases"))
        w.writerows(rows[c] for c in sorted(rows))
    print(f"{len(rows)} aeroportos em {OUT}")


if __name__ == "__main__":
    main()
