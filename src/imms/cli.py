"""Command-line entry point for IMMS script parsing, normalization and session assembly."""

import argparse
import json
from pathlib import Path

from . import assemble as assemble_mod
from . import boxed as boxed_mod
from . import branch as branch_mod
from . import corpus as corpus_mod
from . import db as db_mod
from . import derive as derive_mod
from . import infoguard as infoguard_mod
from . import normalize as normalize_mod
from . import ontology as ontology_mod
from . import replay as replay_mod
from . import runtime as runtime_mod
from .util import print_json, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


def _value(raw):
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def build_parser():
    p = argparse.ArgumentParser(prog="imms", description="Assemble a playable murder-mystery session")
    p.add_argument("--db", default="imms.db", help="SQLite database path")
    sub = p.add_subparsers(dest="cmd", required=True)

    parse_p = sub.add_parser("parse", help="Parse a user-provided script into Ontology and store it")
    parse_p.add_argument("--script", required=True)
    parse_p.add_argument("--out", help="Optional ontology JSON export")

    norm_p = sub.add_parser("normalize", help="Extract read-only L1 persona cards")
    norm_p.add_argument("--script-id", required=True)
    norm_p.add_argument("--report")

    asm_p = sub.add_parser("assemble", help="Assemble GM + AI agents + human seat")
    asm_p.add_argument("--script-id", required=True)
    asm_p.add_argument("--human", required=True)
    asm_p.add_argument("--difficulty", type=int, default=3)

    show_p = sub.add_parser("persona", help="Read persona attributes")
    show_p.add_argument("action", choices=["show"])
    show_p.add_argument("--script-id", required=True)
    show_p.add_argument("--char", required=True)

    der_p = sub.add_parser("derive", help="Compute a derived ability without persisting it")
    der_p.add_argument("--script-id", required=True)
    der_p.add_argument("--char", required=True)
    der_p.add_argument("--key", required=True)

    cmp_p = sub.add_parser("compare", help="Compare two characters on the canonical vector")
    cmp_p.add_argument("--script-id", required=True)
    cmp_p.add_argument("--a", required=True)
    cmp_p.add_argument("--b", required=True)

    for layer in ("assigned", "emergent"):
        lp = sub.add_parser(layer, help=f"Mutate L{'2' if layer == 'assigned' else '3'}")
        lsp = lp.add_subparsers(dest="action", required=True)
        sp = lsp.add_parser("set")
        sp.add_argument("--session", required=True)
        sp.add_argument("--char", required=True)
        sp.add_argument("--key", required=True)
        sp.add_argument("--value", required=True)
        sp.add_argument("--reason", required=True)
        sp.add_argument("--round", type=int, default=0)
        sp.add_argument("--target", default="")
        sp.add_argument("--lock", action="store_true")
        sp.add_argument("--layer-code", choices=["L2", "L3"], default="L2" if layer == "assigned" else "L3")

    aud_p = sub.add_parser("audit", help="Query attribute change events")
    aud_p.add_argument("--session", required=True)
    aud_p.add_argument("--char")
    aud_p.add_argument("--since", type=int, default=0)

    imp_p = sub.add_parser("import-boxed", help="Bulk import 盒装本 scripts into the database")
    imp_p.add_argument("--dir", default="refer/mystery-scripts-100/盒装本")
    imp_p.add_argument("--limit", type=int)
    imp_p.add_argument("--report", help="Optional JSON import report path")

    build_p = sub.add_parser("build-corpus", help="Build project-owned corpus from disposable refer sources")
    build_p.add_argument("--source-root", default=str(ROOT / "refer"))
    build_p.add_argument("--out", default=str(ROOT / "corpus"))
    build_p.add_argument("--report", help="Optional JSON manifest report path")

    import_corpus_p = sub.add_parser("import-corpus", help="Import accepted corpus scripts into the database")
    import_corpus_p.add_argument("--dir", default=str(ROOT / "corpus"))
    import_corpus_p.add_argument("--report", help="Optional JSON import report path")

    corpus_report_p = sub.add_parser("corpus-report", help="Show admission decisions from the corpus manifest")
    corpus_report_p.add_argument("--dir", default=str(ROOT / "corpus"))

    sub.add_parser("scripts", help="List stored scripts").add_argument("action", choices=["list"], nargs="?", default="list")
    seats_p = sub.add_parser("seats", help="List assembled seats")
    seats_p.add_argument("--session", required=True)

    priv_p = sub.add_parser("private", help="Private chat rooms ordered by global seq")
    priv_sp = priv_p.add_subparsers(dest="private_action", required=True)
    po = priv_sp.add_parser("open", help="Open a room for 2+ seats")
    po.add_argument("--session", required=True)
    po.add_argument("--round", type=int, default=1)
    po.add_argument("--seats", required=True, help="comma-separated seat ids")
    ps = priv_sp.add_parser("say", help="Speak inside a room")
    ps.add_argument("--session", required=True)
    ps.add_argument("--room", required=True)
    ps.add_argument("--actor", required=True)
    ps.add_argument("--utterance", required=True)
    ps.add_argument("--to", help="target seat id inside the room")
    ps.add_argument("--claimed-fact", action="append", default=[])
    ps.add_argument("--inner-intent")
    pc = priv_sp.add_parser("close", help="Close a room")
    pc.add_argument("--session", required=True)
    pc.add_argument("--room", required=True)
    pl = priv_sp.add_parser("list", help="List rooms of a session")
    pl.add_argument("--session", required=True)

    br_p = sub.add_parser("branch", help="GM branch regression: fork at a round and replay")
    br_sp = br_p.add_subparsers(dest="branch_action", required=True)
    bf = br_sp.add_parser("fork", help="Fork at the start of a round, replay and regress-check")
    bf.add_argument("--session", required=True)
    bf.add_argument("--at-round", type=int, required=True)
    bf.add_argument("--override-round", type=int, help="Round whose user utterance to replace")
    bf.add_argument("--override-utterance", help="Replacement user utterance")
    bf.add_argument("--seed", type=int, default=42)
    bf.add_argument("--llm", choices=["auto", "template", "http"], default="template")
    bf.add_argument("--out", help="JSON report path")

    ig_p = sub.add_parser("infoguard", help="Inspect the information boundary")
    ig_sp = ig_p.add_subparsers(dest="infoguard_action", required=True)
    igs = ig_sp.add_parser("scopes", help="List known/forbidden facts per character")
    igs.add_argument("--session", required=True)
    igc = ig_sp.add_parser("check", help="Check one text against a character's scope")
    igc.add_argument("--session", required=True)
    igc.add_argument("--char", required=True)
    igc.add_argument("--text", required=True)
    igc.add_argument("--direction", choices=["in", "out"], default="out")

    rep_p = sub.add_parser("replay", help="Generate five-section post-game replay")
    rep_p.add_argument("--session", required=True)
    rep_p.add_argument("--out", help="Markdown output path")

    exp_p = sub.add_parser("export", help="Export DB tables to JSON")
    exp_p.add_argument("--out", required=True)

    play_p = sub.add_parser("play", help="Run a deterministic headless session")
    play_p.add_argument("--session", required=True)
    play_p.add_argument("--rounds", type=int, default=3)
    play_p.add_argument("--scripted", help="JSON file with user utterances and optional vote")
    play_p.add_argument("--seed", type=int, default=42)
    play_p.add_argument("--llm", choices=["auto", "template", "http"], default=None,
                        help="Speech provider; auto uses http when IMMS_LLM_URL is set")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = db_mod.connect(args.db)
    try:
        if args.cmd == "parse":
            onto = ontology_mod.parse_script_dir(args.script)
            onto.setdefault("title", onto["script_id"])
            db_mod.save_script(conn, onto)
            if args.out:
                write_json(args.out, onto)
            print_json({"script_id": onto["script_id"], "checks": ontology_mod.validate_ontology(onto), "stored": True})

        elif args.cmd == "normalize":
            onto = db_mod.get_script(conn, args.script_id)
            doc, report = normalize_mod.normalize_persona(onto, CONFIG)
            version = db_mod.save_persona_l1(conn, args.script_id, doc)
            if args.report:
                write_json(args.report, report)
            print_json({"script_id": args.script_id, "extraction_version": version, "personas": len(doc["personas"]), "report": report})

        elif args.cmd == "assemble":
            session = assemble_mod.assemble(conn, args.script_id, args.human, args.difficulty)
            print_json(session)

        elif args.cmd == "persona":
            doc = db_mod.get_persona_l1(conn, args.script_id)
            found = [p for p in doc["personas"] if p["character_id"] == args.char]
            if not found:
                raise ValueError("ERR_CHAR_NOT_FOUND")
            print_json(found[0]["attributes"])

        elif args.cmd == "derive":
            value = derive_mod.derive_from_db(conn, args.script_id, args.char, args.key)
            print_json({"key": args.key, "value": round(value, 4)})

        elif args.cmd == "compare":
            print_json(derive_mod.compare_from_db(conn, args.script_id, args.a, args.b))

        elif args.cmd in {"assigned", "emergent"}:
            session = db_mod.get_session(conn, args.session)
            layer = args.layer_code
            after = db_mod.set_attribute(
                conn, session, layer, args.char, args.key, _value(args.value),
                args.reason, args.round, target=args.target, lock=args.lock,
            )
            print_json(after)

        elif args.cmd == "audit":
            print_json(db_mod.audit(conn, args.session, args.char, args.since))

        elif args.cmd == "import-boxed":
            report = boxed_mod.import_boxes(conn, args.dir, CONFIG, args.limit)
            if args.report:
                write_json(args.report, report)
            print_json({"imported": report["imported"], "ocr": "skipped", "scripts": report["scripts"][:5]})

        elif args.cmd == "build-corpus":
            manifest = corpus_mod.build_corpus(args.source_root, args.out)
            if args.report:
                write_json(args.report, manifest)
            print_json({"summary": manifest["summary"], "entries": [
                {
                    "source_id": e["source_id"],
                    "decision": e["decision"],
                    "reasons": [r["code"] for r in e.get("reasons", [])],
                }
                for e in manifest["entries"]
            ]})

        elif args.cmd == "import-corpus":
            report = corpus_mod.import_corpus(conn, args.dir, CONFIG)
            if args.report:
                write_json(args.report, report)
            print_json({
                "imported": report["imported"], "skipped": report["skipped"],
                "scripts": report["scripts"], "skipped_detail": report["skipped_detail"],
            })

        elif args.cmd == "corpus-report":
            print_json(corpus_mod.corpus_report(args.dir))

        elif args.cmd == "scripts":
            print_json(db_mod.list_scripts(conn))

        elif args.cmd == "seats":
            print_json(db_mod.get_session(conn, args.session)["participants"])

        elif args.cmd == "private":
            db_mod.get_session(conn, args.session)
            if args.private_action == "open":
                seats = [s.strip() for s in args.seats.split(",") if s.strip()]
                print_json(db_mod.open_private_room(conn, db_mod.get_session(conn, args.session), args.round, seats))
            elif args.private_action == "say":
                print_json(db_mod.private_say(
                    conn, args.session, args.room, args.actor, args.utterance,
                    target=args.to, claimed_facts=args.claimed_fact, inner_intent=args.inner_intent,
                ))
            elif args.private_action == "close":
                print_json(db_mod.close_private_room(conn, args.session, args.room))
            elif args.private_action == "list":
                print_json(db_mod.list_private_rooms(conn, args.session))

        elif args.cmd == "branch":
            report = branch_mod.fork_and_regress(
                conn, args.session, args.at_round, args.override_round,
                args.override_utterance, args.seed, args.llm,
            )
            if args.out:
                write_json(args.out, report)
            print_json(report)

        elif args.cmd == "infoguard":
            session = db_mod.get_session(conn, args.session)
            onto = db_mod.get_script(conn, session["script_id"])
            engine = infoguard_mod.build_engine(onto)
            if args.infoguard_action == "scopes":
                print_json({
                    cid: {"known": sorted(s.known_fact_ids), "forbidden": sorted(s.forbidden_fact_ids)}
                    for cid, s in engine.scopes.items()
                })
            elif args.direction == "in":
                result = engine.guard_input(args.char, args.text)
                print_json({
                    "character_id": args.char, "wrapped": bool(result.findings),
                    "text": result.text,
                    "findings": [
                        {"fact_id": h.fact_id, "matched_text": h.matched_text, "method": h.method}
                        for h in result.findings
                    ],
                })
            else:
                findings = engine.check_output(args.char, args.text)
                print_json({"character_id": args.char, "findings": [f.as_dict() for f in findings]})

        elif args.cmd == "replay":
            text = replay_mod.build_replay(conn, args.session)
            out = args.out or f"replay_{args.session}.md"
            Path(out).write_text(text, encoding="utf-8")
            print_json({"out": out, "sections": replay_mod.SECTIONS, "bytes": len(text.encode("utf-8"))})

        elif args.cmd == "export":
            print_json(db_mod.export_db(conn, args.out))

        elif args.cmd == "play":
            scripted = read_json(args.scripted) if args.scripted else None
            print_json(runtime_mod.run_session(conn, args.session, args.rounds, scripted, args.seed, args.llm))
        return 0
    except ValueError as exc:
        print_json({"error": str(exc)})
        return 1
    except Exception as exc:
        print_json({"error": f"ERR_INTERNAL: {exc}"})
        return 2
    finally:
        conn.close()
