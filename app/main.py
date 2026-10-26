"""CLI.

  python -m app.main run        # grosso + fino agora, para todas as rotas
  python -m app.main coarse     # só varredura grossa
  python -m app.main fine       # só busca fina nas últimas candidatas
  python -m app.main serve      # scheduler (modo contêiner)
  python -m app.main history    # últimos preços gravados
  python -m app.main bonus      # lê os feeds de bônus de transferência para a Azul agora
  python -m app.main demo       # popula 30 dias de histórico fictício (só para testar o painel)
  python -m app.main web        # painel em http://localhost:8000

Flags: --collector mock|google_flights   --dry-run (alertas só no console)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .collectors import get_collector
from .config import settings
from .db import active_watches, init_db
from .notifiers import active_notifiers
from .pipeline import run_watch

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def _each(args, coarse: bool, fine: bool, trigger: str) -> None:
    collector = get_collector(args.collector)
    notifiers = active_notifiers(args.dry_run)
    for w in active_watches():
        try:
            out = run_watch(w, collector, notifiers, trigger=trigger, coarse=coarse, fine=fine)
            log.info("%s: %d candidatas, %d alertas", w.id, out["candidates"], out["alerts"])
        except Exception:  # uma rota com problema não impede as outras
            log.exception("%s: rodada interrompida", w.id)


def cmd_run(args) -> None:
    _each(args, coarse=True, fine=True, trigger="cli")


def cmd_coarse(args) -> None:
    _each(args, coarse=True, fine=False, trigger=args.trigger)


def cmd_fine(args) -> None:
    _each(args, coarse=False, fine=True, trigger=args.trigger)


def cmd_bonus(args) -> None:
    from .bonus.service import run_bonus
    out = run_bonus(active_notifiers(args.dry_run), trigger=args.trigger)
    log.info("bônus: %s", out)


def cmd_serve(args) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=settings.tz)
    args.trigger = "schedule"
    sched.add_job(lambda: cmd_coarse(args), CronTrigger.from_crontab(settings.coarse_cron), id="coarse")
    sched.add_job(lambda: cmd_fine(args), CronTrigger.from_crontab(settings.fine_cron), id="fine")
    sched.add_job(lambda: cmd_bonus(args), CronTrigger.from_crontab(settings.bonus_cron), id="bonus")
    log.info("scheduler ativo: coarse='%s' fine='%s' bonus='%s' tz=%s", settings.coarse_cron, settings.fine_cron,
             settings.bonus_cron, settings.tz)
    if args.run_now:
        cmd_run(args)
    sched.start()


def cmd_history(args) -> None:
    from sqlalchemy import select
    from .db import PriceSnapshot, SessionLocal

    with SessionLocal() as s:
        rows = s.execute(
            select(PriceSnapshot).where(PriceSnapshot.source == "fine")
            .order_by(PriceSnapshot.observed_at.desc()).limit(args.limit)
        ).scalars().all()
    for r in rows:
        print(f"{r.observed_at:%Y-%m-%d %H:%M} {r.watch_id} {r.origin}→{r.destination} "
              f"{r.outbound_date}..{r.return_date} {r.currency} {r.price:.0f} {r.airlines or ''}")


def cmd_demo(args) -> None:
    from .demo import run
    run()


def cmd_web(args) -> None:
    import uvicorn
    uvicorn.run("app.web.app:app", host="0.0.0.0", port=args.port, reload=False)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="monitor-passagens")
    p.add_argument("cmd", choices=["run", "coarse", "fine", "serve", "history", "demo", "web", "bonus"])
    p.add_argument("--collector", default=settings.collector)
    p.add_argument("--trigger", default="cli", help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--run-now", action="store_true", help="serve: executa um ciclo completo ao iniciar")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    init_db()
    {"run": cmd_run, "coarse": cmd_coarse, "fine": cmd_fine, "serve": cmd_serve, "history": cmd_history,
     "demo": cmd_demo, "web": cmd_web, "bonus": cmd_bonus}[args.cmd](args)


if __name__ == "__main__":
    main(sys.argv[1:])
