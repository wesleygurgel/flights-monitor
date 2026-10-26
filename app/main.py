"""CLI.

  python -m app.main run        # grosso + fino agora, para todas as rotas
  python -m app.main coarse     # só varredura grossa
  python -m app.main fine       # só busca fina nas últimas candidatas
  python -m app.main serve      # scheduler (modo contêiner)
  python -m app.main history    # últimos preços gravados
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
from .pipeline import last_candidates, run_coarse, run_fine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


def cmd_run(args) -> None:
    collector = get_collector(args.collector)
    notifiers = active_notifiers(args.dry_run)
    for w in active_watches():
        cands = run_coarse(w, collector)
        n = run_fine(w, collector, cands, notifiers)
        log.info("%s: %d candidatas, %d alertas", w.id, len(cands), n)


def cmd_coarse(args) -> None:
    collector = get_collector(args.collector)
    for w in active_watches():
        cands = run_coarse(w, collector)
        log.info("%s: candidatas %s", w.id, [(d, c.outbound_date, c.price) for d, c in cands])


def cmd_fine(args) -> None:
    collector = get_collector(args.collector)
    notifiers = active_notifiers(args.dry_run)
    for w in active_watches():
        cands = last_candidates(w, w.candidates)
        if not cands:
            log.warning("%s: sem varredura grossa anterior; rode 'coarse' antes", w.id)
            continue
        n = run_fine(w, collector, cands, notifiers)
        log.info("%s: %d alertas", w.id, n)


def cmd_serve(args) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler(timezone=settings.tz)
    sched.add_job(lambda: cmd_coarse(args), CronTrigger.from_crontab(settings.coarse_cron), id="coarse")
    sched.add_job(lambda: cmd_fine(args), CronTrigger.from_crontab(settings.fine_cron), id="fine")
    log.info("scheduler ativo: coarse='%s' fine='%s' tz=%s", settings.coarse_cron, settings.fine_cron, settings.tz)
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
    p.add_argument("cmd", choices=["run", "coarse", "fine", "serve", "history", "demo", "web"])
    p.add_argument("--collector", default="google_flights")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--run-now", action="store_true", help="serve: executa um ciclo completo ao iniciar")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args(argv)
    init_db()
    {"run": cmd_run, "coarse": cmd_coarse, "fine": cmd_fine, "serve": cmd_serve, "history": cmd_history,
     "demo": cmd_demo, "web": cmd_web}[args.cmd](args)


if __name__ == "__main__":
    main(sys.argv[1:])
