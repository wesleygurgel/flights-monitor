"""Painel web: uvicorn app.web.app:app"""
from __future__ import annotations

import hmac
import logging
import re
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete
from starlette.middleware.sessions import SessionMiddleware

from .. import pipeline
from ..collectors import get_collector
from ..config import settings
from ..notifiers import active_notifiers
from ..db import Alert, PriceSnapshot, Route, SessionLocal, init_db, utcnow
from . import fmt, queries

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

WEAK_KEYS = {"", "troque-esta-chave", "changeme", "secret"}


def secret_problem(password: str | None, key: str | None) -> str | None:
    """Com senha ligada, a chave assina o cookie de login: fraca = qualquer um forja a sessão."""
    if password and ((key or "") in WEAK_KEYS or len(key or "") < 32):
        return ("SECRET_KEY fraca ou padrão. Gere uma com "
                "python -c \"import secrets; print(secrets.token_hex(32))\" e coloque no .env.")
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    problem = secret_problem(settings.app_password, settings.secret_key)
    if problem:
        raise RuntimeError(problem)
    init_db()
    if not settings.app_password:
        log.warning("APP_PASSWORD não definido: painel aberto sem senha. Defina no .env antes de expor na internet.")
    yield


app = FastAPI(title="Flights Monitor", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(fmt=fmt, channels_label=queries.channels_label, short_error=queries.short_error,
                             run_status=queries.RUN_STATUS, run_kind=queries.RUN_KIND, run_trigger=queries.RUN_TRIGGER)

OPEN_PATHS = ("/entrar", "/static", "/saude")


@app.middleware("http")
async def require_login(request: Request, call_next):
    if settings.app_password and not request.url.path.startswith(OPEN_PATHS) and not request.session.get("ok"):
        if request.headers.get("HX-Request"):
            return Response(status_code=401, headers={"HX-Redirect": "/entrar"})
        return RedirectResponse("/entrar", status_code=303)
    return await call_next(request)


# adicionado depois do middleware de login para rodar antes dele (precisa da sessão)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=60 * 60 * 24 * 30,
                   same_site="lax", https_only=False)


def render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    nxt = queries.next_run(settings.fine_cron, fmt.TZ)
    ctx.setdefault("next_run", f"{nxt.hour}:{nxt.minute:02d}" if nxt else None)
    ctx.setdefault("nav", "")
    return templates.TemplateResponse(request, name, ctx, status_code=status)


@app.get("/saude")
def health() -> dict:
    return {"ok": True}


# ---------- login ----------

@app.get("/entrar", response_class=HTMLResponse)
def login_page(request: Request):
    return render(request, "login.html", error=None)


@app.post("/entrar")
def login(request: Request, senha: str = Form("")):
    if not settings.app_password or hmac.compare_digest(senha, settings.app_password):
        request.session["ok"] = True
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", status=401, error="Senha incorreta. Confira o valor de APP_PASSWORD no servidor.")


@app.post("/sair")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/entrar", status_code=303)


# ---------- telas ----------

@app.get("/", response_class=HTMLResponse)
def routes_page(request: Request):
    with SessionLocal() as s:
        views = [queries.route_view(s, r) for r in queries.all_routes(s)]
        hero = queries.hero(views)
    return render(request, "routes.html", nav="rotas", views=views, hero=hero)


@app.get("/rotas/{route_id}", response_class=HTMLResponse)
def detail_page(request: Request, route_id: str):
    with SessionLocal() as s:
        r = s.get(Route, route_id)
        if not r:
            return render(request, "not_found.html", status=404, nav="rotas")
        d = queries.detail(s, r)
    return render(request, "detail.html", nav="rotas", running=_search_running(route_id), **d)


@app.post("/rotas/{route_id}/pausar")
def toggle_pause(request: Request, route_id: str):
    with SessionLocal() as s:
        r = s.get(Route, route_id)
        if r:
            r.active = not r.active
            r.paused_at = None if r.active else utcnow()
            s.commit()
            s.refresh(r)
        if request.headers.get("HX-Request") and r:
            return render(request, "_pause_button.html", r=r)
    return RedirectResponse(request.headers.get("referer") or "/", status_code=303)


# ---------- buscar agora ----------

_manual: set[str] = set()  # rotas com busca manual disparada neste processo
_manual_lock = threading.Lock()


def _search_running(route_id: str) -> bool:
    return route_id in _manual or pipeline.is_running(route_id)


def _manual_search(watch) -> None:
    try:
        pipeline.run_watch(watch, get_collector(settings.collector), active_notifiers(False), trigger="manual")
    except Exception:
        log.exception("busca manual de %s falhou", watch.id)
    finally:
        with _manual_lock:
            _manual.discard(watch.id)


@app.post("/rotas/{route_id}/buscar")
def search_now(request: Request, route_id: str):
    with SessionLocal() as s:
        r = s.get(Route, route_id)
    if not r:
        return RedirectResponse("/", status_code=303)
    with _manual_lock:
        if not _search_running(route_id):
            _manual.add(route_id)
            threading.Thread(target=_manual_search, args=(r.to_watch(),), daemon=True).start()
    if request.headers.get("HX-Request"):
        return render(request, "_search_button.html", r=r, running=True)
    return RedirectResponse(f"/rotas/{route_id}", status_code=303)


@app.get("/rotas/{route_id}/busca")
def search_status(request: Request, route_id: str):
    with SessionLocal() as s:
        r = s.get(Route, route_id)
    if not r:
        return Response(status_code=404)
    if _search_running(route_id):
        return render(request, "_search_button.html", r=r, running=True)
    resp = render(request, "_search_button.html", r=r, running=False)
    resp.headers["HX-Refresh"] = "true"  # terminou: recarrega a página com os novos preços
    return resp


# ---------- bônus azul ----------

def _bonus_reading() -> bool:
    return "bonus" in _manual


def _bonus_read() -> None:
    from ..bonus.service import run_bonus
    try:
        run_bonus(active_notifiers(False), trigger="manual")
    except Exception:
        log.exception("leitura manual dos blogs falhou")
    finally:
        with _manual_lock:
            _manual.discard("bonus")


@app.get("/bonus", response_class=HTMLResponse)
def bonus_page(request: Request, salvo: int = 0):
    from ..bonus import service
    from ..bonus.parser import ALL_PROGRAMS

    with SessionLocal() as s:
        d = service.campaigns_for_panel(s)
        feeds = []
        for name, key in service.feed_keys():
            runs = queries.runs_for(s, key, 1)
            feeds.append({"name": name, "run": runs[0] if runs else None})
    cfg = d["cfg"]
    top = next((c for c in d["active"] if not c.transferred and cfg.wants(c.program)
                and (c.bonus_pct or 0) >= cfg.min_pct), None)
    chans = [n for n, on in (("e-mail", cfg.notify_email), ("WhatsApp", cfg.notify_whatsapp)) if on]
    nxt = queries.next_run(settings.bonus_cron, fmt.TZ)

    def left_text(c) -> str:
        n = service.days_left(c)
        if n is None:
            return "Prazo não informado nos posts"
        if n <= 0:
            return "Termina hoje"
        return "Termina amanhã" if n == 1 else f"Até {fmt.d_short(c.ends_on)}, faltam {n} dias"

    def left_short(c) -> str:
        n = service.days_left(c)
        if n is None:
            return "Prazo não informado nos posts"
        return "Termina hoje" if n <= 0 else ("Termina amanhã" if n == 1 else f"Faltam {n} dias")

    return render(request, "bonus.html", nav="bonus", top=top, left_text=left_text, left_short=left_short,
                  channels_text=" e ".join(chans), next_bonus=f"{nxt.hour}:{nxt.minute:02d}" if nxt else None,
                  feeds=feeds, failing_feeds=[f for f in feeds if f["run"] and f["run"].is_bad],
                  all_programs=ALL_PROGRAMS, saved=bool(salvo), reading=_bonus_reading(), **d)


@app.post("/bonus/config")
async def bonus_config(request: Request):
    from ..bonus import service
    from ..bonus.parser import ALL_PROGRAMS

    form = await request.form()
    chosen = [p for p in form.getlist("programs") if p in ALL_PROGRAMS]
    with SessionLocal() as s:
        cfg = service.get_settings(s)
        cfg.programs = "" if len(chosen) == len(ALL_PROGRAMS) else ",".join(chosen)
        try:
            cfg.min_pct = max(1, min(400, int(str(form.get("min_pct", "80")).strip() or 80)))
        except ValueError:
            pass
        cfg.notify_email = form.get("notify_email") == "on"
        cfg.notify_whatsapp = form.get("notify_whatsapp") == "on"
        cfg.active = form.get("active") == "on"
        s.commit()
    return RedirectResponse("/bonus?salvo=1", status_code=303)


@app.post("/bonus/{campaign_id}/transferi")
def bonus_transferred(request: Request, campaign_id: int):
    from ..db import BonusCampaign
    with SessionLocal() as s:
        c = s.get(BonusCampaign, campaign_id)
        if c:
            c.transferred = not c.transferred
            s.commit()
            s.refresh(c)
        if request.headers.get("HX-Request") and c:
            resp = render(request, "_transfer_button.html", c=c)
            resp.headers["HX-Refresh"] = "true"  # a fita muda de cor; recarrega para refletir
            return resp
    return RedirectResponse("/bonus", status_code=303)


@app.post("/bonus/buscar")
def bonus_read_now(request: Request):
    with _manual_lock:
        if not _bonus_reading():
            _manual.add("bonus")
            threading.Thread(target=_bonus_read, daemon=True).start()
    if request.headers.get("HX-Request"):
        return render(request, "_bonus_read_button.html", reading=True)
    return RedirectResponse("/bonus", status_code=303)


@app.get("/bonus/leitura")
def bonus_read_status(request: Request):
    if _bonus_reading():
        return render(request, "_bonus_read_button.html", reading=True)
    resp = render(request, "_bonus_read_button.html", reading=False)
    resp.headers["HX-Refresh"] = "true"
    return resp


@app.post("/rotas/{route_id}/excluir")
def delete_route(route_id: str):
    with SessionLocal() as s:
        s.execute(delete(PriceSnapshot).where(PriceSnapshot.watch_id == route_id))
        s.execute(delete(Alert).where(Alert.watch_id == route_id))
        s.execute(delete(Route).where(Route.id == route_id))
        s.commit()
    return RedirectResponse("/gerenciar", status_code=303)


# ---------- gerenciar ----------

def _form_defaults(r: Route | None) -> dict:
    if r:
        return {"id": r.id, "origin": r.origin, "destinations": ", ".join(r.dest_list),
                "depart_from": r.depart_from, "depart_to": r.depart_to, "duration_days": r.duration_days,
                "duration_tolerance": r.duration_tolerance, "adults": r.adults,
                "max_stops": "" if r.max_stops is None else r.max_stops,
                "max_price": "" if r.max_price is None else round(r.max_price),
                "drop_pct": "" if r.drop_pct is None else round(r.drop_pct),
                "min_samples": r.min_samples, "candidates": r.candidates,
                "notify_email": r.notify_email, "notify_whatsapp": r.notify_whatsapp, "active": r.active}
    return {"id": "", "origin": "NAT", "destinations": "", "depart_from": "", "depart_to": "",
            "duration_days": 7, "duration_tolerance": 2, "adults": 1, "max_stops": 1, "max_price": "",
            "drop_pct": 20, "min_samples": 20, "candidates": 5, "notify_email": True,
            "notify_whatsapp": True, "active": True}


def _manage(request: Request, selected: Route | None, form: dict, errors: dict | None = None, status: int = 200,
            saved: bool = False):
    with SessionLocal() as s:
        routes = [(r, fmt.route_code(r.origin, r.dest_list)) for r in queries.all_routes(s)]
    return render(request, "manage.html", status=status, nav="gerenciar", routes=routes, selected=selected,
                  f=form, errors=errors or {}, saved=saved,
                  title=f"Editar {fmt.route_code(selected.origin, selected.dest_list)}" if selected else "Nova rota")


@app.get("/gerenciar", response_class=HTMLResponse)
def manage_index(request: Request):
    with SessionLocal() as s:
        routes = queries.all_routes(s)
    if routes:
        return RedirectResponse(f"/gerenciar/{routes[0].id}", status_code=303)
    return _manage(request, None, _form_defaults(None))


@app.get("/gerenciar/nova", response_class=HTMLResponse)
def manage_new(request: Request):
    return _manage(request, None, _form_defaults(None))


@app.get("/gerenciar/{route_id}", response_class=HTMLResponse)
def manage_edit(request: Request, route_id: str, salvo: int = 0):
    with SessionLocal() as s:
        r = s.get(Route, route_id)
    if not r:
        return RedirectResponse("/gerenciar", status_code=303)
    return _manage(request, r, _form_defaults(r), saved=bool(salvo))


IATA = re.compile(r"^[A-Z]{3}$")


def _int(v: str, name: str, errors: dict, lo: int = 0, hi: int = 10_000, optional: bool = False) -> int | None:
    v = (v or "").strip()
    if not v and optional:
        return None
    try:
        n = int(float(v.replace(".", "").replace(",", ".")))
        if not lo <= n <= hi:
            raise ValueError
        return n
    except ValueError:
        errors[name] = f"Use um número entre {lo} e {hi}."
        return None


@app.post("/gerenciar/salvar")
async def manage_save(request: Request):
    data = await request.form()
    f = {k: data.get(k, "") for k in _form_defaults(None)}
    f["notify_email"] = data.get("notify_email") == "on"
    f["notify_whatsapp"] = data.get("notify_whatsapp") == "on"
    errors: dict[str, str] = {}

    origin = str(f["origin"]).strip().upper()
    if not IATA.match(origin):
        errors["origin"] = "Informe o código de 3 letras do aeroporto, como NAT."
    dests = [d.strip().upper() for d in re.split(r"[,\s]+", str(f["destinations"])) if d.strip()]
    if not dests or not all(IATA.match(d) for d in dests):
        errors["destinations"] = "Informe um ou mais códigos de 3 letras separados por vírgula, como GRU, CGH."
    try:
        d_from, d_to = date.fromisoformat(str(f["depart_from"])), date.fromisoformat(str(f["depart_to"]))
        if d_to < d_from:
            errors["depart_to"] = "A data final precisa ser igual ou depois da inicial."
        elif d_to < date.today():
            errors["depart_to"] = "O período já passou. Escolha datas futuras."
    except ValueError:
        errors["depart_from"] = "Escolha as duas datas do período de ida."
    days = _int(f["duration_days"], "duration_days", errors, 1, 60)
    tol = _int(f["duration_tolerance"], "duration_tolerance", errors, 0, 7)
    if days is not None and tol is not None and tol >= days:
        errors["duration_tolerance"] = "A variação precisa ser menor que a duração."
    adults = _int(f["adults"], "adults", errors, 1, 9)
    max_stops = _int(f["max_stops"], "max_stops", errors, 0, 3, optional=True)
    max_price = _int(f["max_price"], "max_price", errors, 1, 100_000, optional=True)
    drop = _int(f["drop_pct"], "drop_pct", errors, 1, 90, optional=True)
    samples = _int(f["min_samples"], "min_samples", errors, 1, 10_000)
    cands = _int(f["candidates"], "candidates", errors, 1, 20)
    if max_price is None and drop is None and "max_price" not in errors:
        errors["max_price"] = "Defina um teto, uma queda em relação ao normal, ou os dois."

    with SessionLocal() as s:
        r = s.get(Route, str(f["id"])) if f["id"] else None
        if errors:
            return _manage(request, r, f, errors, status=400)
        if r is None:
            r = Route(id=f"{origin}-{dests[0]}-{secrets.token_hex(3)}".lower())
            s.add(r)
        r.origin, r.destinations = origin, ",".join(dests)
        r.depart_from, r.depart_to = d_from.isoformat(), d_to.isoformat()
        r.duration_days, r.duration_tolerance, r.adults = days, tol, adults
        r.max_stops, r.max_price, r.drop_pct = max_stops, max_price, drop
        r.min_samples, r.candidates = samples, cands
        r.notify_email, r.notify_whatsapp = f["notify_email"], f["notify_whatsapp"]
        s.commit()
        rid = r.id
    return RedirectResponse(f"/gerenciar/{rid}?salvo=1", status_code=303)
