"""Command-line entry point for IMMS script parsing, normalization and session assembly."""

import argparse
import json
from pathlib import Path

from . import assemble as assemble_mod
from . import db as db_mod
from . import derive as derive_mod
from . import normalize as normalize_mod
from . import ontology as ontology_mod
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

    sub.add_parser("scripts", help="List stored scripts").add_argument("action", choices=["list"], nargs="?", default="list")
    seats_p = sub.add_parser("seats", help="List assembled seats")
    seats_p.add_argument("--session", required=True)

    exp_p = sub.add_parser("export", help="Export DB tables to JSON")
    exp_p.add_argument("--out", required=True)

    play_p = sub.add_parser("play", help="Run a deterministic headless session")
    play_p.add_argument("--session", required=True)
    play_p.add_argument("--rounds", type=int, default=3)
    play_p.add_argument("--scripted", help="JSON file with user utterances and optional vote")
    play_p.add_argument("--seed", type=int, default=42)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    conn = db_mod.connect(args.db)
    try:
        if args.cmd == "parse":
            onto = ontology_mod.parse_script_dir(args.script)
            onto["title"] = onto["script_id"]
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

        elif args.cmd == "scripts":
            print_json(db_mod.list_scripts(conn))

        elif args.cmd == "seats":
            print_json(db_mod.get_session(conn, args.session)["participants"])

        elif args.cmd == "export":
            print_json(db_mod.export_db(conn, args.out))

        elif args.cmd == "play":
            scripted = read_json(args.scripted) if args.scripted else None
            print_json(runtime_mod.run_session(conn, args.session, args.rounds, scripted, args.seed))
        return 0
    except ValueError as exc:
        print_json({"error": str(exc)})
        return 1
    except Exception as exc:
        print_json({"error": f"ERR_INTERNAL: {exc}"})
        return 2
    finally:
        conn.close()
