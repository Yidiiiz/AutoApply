import argparse
import asyncio
import json
import shutil
import sys

from .config import Config, ROOT, setup_logging
from .database import Database
from .runtime import ProcessLock


def parser():
    p = argparse.ArgumentParser(description="AutoApply internship discovery and applications")
    p.add_argument("--root", default=str(ROOT), help="Project root containing private data")
    sub = p.add_subparsers(dest="command", required=True)
    listings = sub.add_parser("listings", help="Maintain listing eligibility without deleting application history")
    listings.add_argument("action", choices=["cleanup", "stats"])
    history = sub.add_parser('history', help='Maintain private application history')
    history.add_argument('action', choices=['migrate', 'validate', 'stats', 'rebuild-stats'])
    for name in ["init", "doctor", "run", "scan", "queue", "pending", "recent", "status", "pause", "resume", "privacy", "gmail-auth", "discord-check"]:
        sub.add_parser(name)
    for name in ["retry", "inspect", "stop", "resume-manual", "inspect-manual"]:
        sub.add_parser(name).add_argument("id", type=int)
    verification = sub.add_parser("verification")
    verification.add_argument("id", type=int)
    verification.add_argument("value", choices=["passed", "failed", "skipped"])
    answer = sub.add_parser("answer")
    answer.add_argument("id", type=int)
    answer.add_argument("value")
    skip = sub.add_parser("skip")
    skip.add_argument("id", type=int)
    auto = sub.add_parser("autosubmit")
    auto.add_argument("value", choices=["on", "off"])
    login = sub.add_parser("login")
    login.add_argument("url", nargs="?", default="https://accounts.google.com/")
    probe = sub.add_parser("probe", help="Inspect a public form without filling or submitting")
    probe.add_argument("url")
    once = sub.add_parser("work-once")
    once.add_argument("--application-id", type=int, help="Process only this queued application; never fall back to another job")
    once.add_argument("--allow-incomplete", action="store_true", help="Inspect/fill with missing setup; unknown fields still pause")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("id", type=int)
    reconcile.add_argument("outcome", choices=["submitted", "not-submitted"])
    reconcile.add_argument("--evidence", required=True)
    return p


async def execute(args, config, db):
    from .control import Controller
    from .engine import Engine
    controller = Controller(config, db)
    if args.command == 'listings':
        result = db.cleanup_stale_listings() if args.action == 'cleanup' else db.refresh_listing_statistics()
        print(json.dumps(result, indent=2))
    elif args.command == 'history':
        if args.action in {'migrate', 'validate'}:
            result = db.history_startup_report
        elif args.action == 'rebuild-stats':
            result = db.history.rebuild_statistics()
        else:
            result = json.loads((db.history.root / 'statistics.json').read_text(encoding='utf-8'))
        print(json.dumps(result, indent=2))
    elif args.command == "init":
        for folder in ["resumes", "writing_samples", "oauth", "application_history", "documents"]:
            (config.private / folder).mkdir(exist_ok=True)
        for source, name in [(ROOT / "templates/profile.example.yaml", "profile.yaml"), (ROOT / "config/config.example.yaml", "config.yaml")]:
            destination = config.private / name
            if not destination.exists():
                shutil.copyfile(source, destination)
        print("Private configuration initialized. Complete data/private/profile.yaml, add resume.pdf and run doctor.")
    elif args.command == "doctor":
        issues = config.setup_issues()
        print("\n".join(issues) if issues else "Required local setup is complete. Use login to verify external sessions.")
        return 1 if issues else 0
    elif args.command == "privacy":
        from .privacy import privacy_check
        findings = privacy_check(config.root)
        print("\n".join(findings) if findings else "Git index privacy check passed.")
        return 1 if findings else 0
    elif args.command == "gmail-auth":
        from .gmail import oauth_login
        await asyncio.to_thread(oauth_login, config)
        print("Gmail read-only OAuth saved in private storage.")
    elif args.command == "discord-check":
        from .discord_bot import check_connection
        await check_connection()
        print("Discord authentication and authorized-user DM delivery succeeded.")
    elif args.command == "reconcile":
        controller.reconcile(args.id, args.outcome == "submitted", args.evidence)
        print("Submission outcome reconciled.")
    elif args.command in {"run", "scan", "work-once", "login", "probe"}:
        if args.command in {"run", "work-once"} and config["browser"]["headless"]:
            print("Application processing requires browser.headless: false so protected steps can be completed in the preserved browser.")
            return 1
        if args.command in {"run", "work-once"} and not getattr(args, "allow_incomplete", False):
            issues = config.setup_issues()
            blockers = [issue for issue in issues if issue.startswith(("Missing profile", "Missing resume", "Missing environment"))]
            if blockers:
                print("Setup required before processing:\n" + "\n".join(blockers))
                return 1
            for issue in issues:
                print("Setup note: " + issue)
        with ProcessLock(config.private / "worker.lock"):
            engine = Engine(config, db)
            try:
                if args.command == "scan":
                    print(json.dumps(await engine.scan(force=True), indent=2))
                elif args.command == "login":
                    config.data["browser"]["headless"] = False
                    page = await engine.browser.new_page()
                    await engine.browser.navigate(page, args.url)
                    print("Complete login in the dedicated browser. Never choose an unintended account.")
                    await asyncio.to_thread(input, "Press Enter here when finished: ")
                elif args.command == "probe":
                    from .applications import adapter_for
                    page = await engine.browser.new_page()
                    state, evidence = await engine.browser.navigate(page, args.url)
                    adapter = adapter_for(page)
                    listing = await adapter.inspect()
                    result = {"adapter": adapter.name, "condition": state, "evidence": evidence,
                              "description_characters": len(listing["description"])}
                    if not state:
                        questions = await adapter.get_questions({"id": 0})
                        result["questions"] = [{"label": q.label, "type": q.kind, "required": q.required, "option_count": len(q.options)} for q in questions]
                    print(json.dumps(result, indent=2))
                elif args.command == "work-once":
                    db.recover()
                    bot = task = None
                    try:
                        if config["discord"]["enabled"]:
                            import os
                            from .discord_bot import DiscordBot
                            bot = DiscordBot(engine.control)
                            task = asyncio.create_task(bot.start(os.environ["DISCORD_BOT_TOKEN"]))
                            await bot.wait_for_delivery(task)
                        print("Processed one application." if await engine.process_one(args.application_id) else "No eligible queued work.")
                        if engine.handoff.pages:
                            print("Manual intervention required. Browser remains open; use resume-manual ID from another terminal or Discord.")
                            await engine.wait_for_manual()
                    finally:
                        if bot:
                            await bot.close()
                        if task:
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                else:
                    await engine.run()
            finally:
                await engine.close()
    elif args.command == "inspect":
        print(json.dumps(controller.inspect(args.id), indent=2))
    else:
        text = args.command
        if hasattr(args, "id"):
            text += " " + str(args.id)
        if hasattr(args, "value"):
            text += " " + args.value
        print(controller.command(text))
    return 0


def main():
    args = parser().parse_args()
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(args.root) / ".env", override=False)
    config = Config(args.root)
    setup_logging(config)
    db = Database(config.private / "autoapply.sqlite3", config["jobs"]["max_listing_age_days"])
    try:
        return asyncio.run(execute(args, config, db))
    except KeyboardInterrupt:
        print("Stopped. Interrupted submissions are reconciled on restart.")
        return 130
    except (ValueError, RuntimeError, OSError) as exc:
        # Only operator-directed, bounded errors are printed; provider exceptions are audited by type.
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
