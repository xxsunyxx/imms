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
from . import gm as gm_mod
from . import infoguard as infoguard_mod
from . import ingest as ingest_mod
from . import memory as memory_mod
from . import normalize as normalize_mod
from . import ontology as ontology_mod
from . import registry as registry_mod
from . import replay as replay_mod
from . import retro as retro_mod
from . import runtime as runtime_mod
from . import solver as solver_mod
from . import vote as vote_mod
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

    ing_p = sub.add_parser("ingest", help="Convert arbitrary script sources into canonical Ontology JSON")
    ing_sp = ing_p.add_subparsers(dest="ingest_action", required=True)
    ing_d = ing_sp.add_parser("detect", help="Detect a source format without converting it")
    ing_d.add_argument("--path", required=True)
    ing_c = ing_sp.add_parser("convert", help="Dry-run a conversion and optionally export canonical JSON")
    ing_c.add_argument("--path", required=True)
    ing_c.add_argument("--out")
    ing_c.add_argument("--llm", choices=["auto", "template", "http"], default="auto")
    ing_s = ing_sp.add_parser("skeleton", help="Show the fixed skeleton for a source without storing it")
    ing_s.add_argument("--path", required=True)
    ing_s.add_argument("--out")
    ing_s.add_argument("--llm", choices=["auto", "template", "http"], default="auto")
    ing_a = ing_sp.add_parser("add", help="Ingest one source into the self-contained corpus")
    ing_a.add_argument("--path", required=True)
    ing_a.add_argument("--root", default=str(ROOT / "corpus"))
    ing_a.add_argument("--force", action="store_true")
    ing_a.add_argument("--llm", choices=["auto", "template", "http"], default="auto")
    ing_b = ing_sp.add_parser("batch", help="Ingest every recognised source under a directory")
    ing_b.add_argument("--path", required=True)
    ing_b.add_argument("--root", default=str(ROOT / "corpus"))
    ing_b.add_argument("--limit", type=int)
    ing_b.add_argument("--force", action="store_true")
    ing_b.add_argument("--llm", choices=["auto", "template", "http"], default="auto")
    ing_r = ing_sp.add_parser("report", help="Summarise ingestion decisions")
    ing_r.add_argument("--root", default=str(ROOT / "corpus"))
    ing_v = ing_sp.add_parser("validate", help="Validate a canonical Ontology JSON file")
    ing_v.add_argument("--file", required=True)

    sub.add_parser("scripts", help="List stored scripts").add_argument("action", choices=["list"], nargs="?", default="list")

    sc_p = sub.add_parser("script", help="Manage script lifecycle, skeletons and history")
    sc_sp = sc_p.add_subparsers(dest="script_action", required=True)
    sc_l = sc_sp.add_parser("list", help="List registered scripts")
    sc_l.add_argument("--status")
    sc_s = sc_sp.add_parser("show", help="Show one registry record")
    sc_s.add_argument("--script-id", required=True)
    sc_k = sc_sp.add_parser("skeleton", help="Show the latest or a specific skeleton version")
    sc_k.add_argument("--script-id", required=True)
    sc_k.add_argument("--version", type=int)
    sc_h = sc_sp.add_parser("history", help="Show lifecycle events")
    sc_h.add_argument("--script-id", required=True)
    sc_m = sc_sp.add_parser("promote", help="Move a script to another lifecycle status")
    sc_m.add_argument("--script-id", required=True)
    sc_m.add_argument("--status", required=True)
    sc_m.add_argument("--notes", default="")

    seats_p = sub.add_parser("seats", help="List assembled seats")
    seats_p.add_argument("--session", required=True)

    mem_p = sub.add_parser("memory", help="Inspect the listener-aware memory ledger")
    mem_sp = mem_p.add_subparsers(dest="memory_action", required=True)
    ms = mem_sp.add_parser("show", help="Show remembered statements or F30 recall")
    ms.add_argument("--session", required=True)
    ms.add_argument("--seat")
    ms.add_argument("--round", type=int)
    ms.add_argument("--cue")
    ms.add_argument("--limit", type=int, default=10)
    mc = mem_sp.add_parser("commitments", help="Show the commitment state machine")
    mc.add_argument("--session", required=True)
    mc.add_argument("--seat")

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

    gm_p = sub.add_parser("gm", help="GM decision core: policy, branch tree, merge and rollback")
    gm_sp = gm_p.add_subparsers(dest="gm_action", required=True)
    gd = gm_sp.add_parser("decide", help="Suggest the GM decision for a round")
    gd.add_argument("--session", required=True)
    gd.add_argument("--round", type=int, required=True)
    gd.add_argument("--policy", choices=["heat", "information", "adaptive"], default="adaptive")
    gb = gm_sp.add_parser("branch", help="Create a replayed branch and register it in the tree")
    gb.add_argument("--session", required=True)
    gb.add_argument("--at-round", type=int, required=True)
    gb.add_argument("--label", default="")
    gb.add_argument("--override-round", type=int)
    gb.add_argument("--override-utterance")
    gb.add_argument("--policy", choices=["heat", "information", "adaptive"], default="adaptive")
    gb.add_argument("--release-clue", help="Force the GM to release this clue (must be in clue_pool)")
    gb.add_argument("--seed", type=int, default=42)
    gt = gm_sp.add_parser("tree", help="List the branch tree")
    gt.add_argument("--session", required=True)
    gdf = gm_sp.add_parser("diff", help="Show narrative diff of one branch")
    gdf.add_argument("--branch", required=True)
    ga = gm_sp.add_parser("activate", help="Set a branch as canonical")
    ga.add_argument("--branch", required=True)
    gmrg = gm_sp.add_parser("merge", help="Merge a branch into the canonical line")
    gmrg.add_argument("--branch", required=True)
    gmrg.add_argument("--rationale", default="")
    grb = gm_sp.add_parser("rollback", help="Roll a branch back to its parent")
    grb.add_argument("--branch", required=True)

    ig_p = sub.add_parser("infoguard", help="Inspect the information boundary")
    ig_sp = ig_p.add_subparsers(dest="infoguard_action", required=True)
    igs = ig_sp.add_parser("scopes", help="List known/forbidden facts per character")
    igs.add_argument("--session", required=True)
    igc = ig_sp.add_parser("check", help="Check one text against a character's scope")
    igc.add_argument("--session", required=True)
    igc.add_argument("--char", required=True)
    igc.add_argument("--text", required=True)
    igc.add_argument("--direction", choices=["in", "out"], default="out")

    audit_script_p = sub.add_parser("audit-script", help="Audit script solvability and consistency")
    audit_script_p.add_argument("--script-id", required=True)
    audit_script_p.add_argument("--out", help="Optional JSON report path")

    audit_all_p = sub.add_parser("audit-all", help="Audit every stored script (summary only)")
    audit_all_p.add_argument("--limit", type=int)
    audit_all_p.add_argument("--out", help="Optional JSON report path")

    vote_p = sub.add_parser("vote", help="Run and audit the voting phase")
    vote_sp = vote_p.add_subparsers(dest="vote_action", required=True)
    vr = vote_sp.add_parser("run", help="Run a voting phase against the current runtime")
    vr.add_argument("--session", required=True)
    vr.add_argument("--scripted", help="JSON with vote / votes / vote_config")
    vr.add_argument("--seed", type=int, default=42)
    vc = vote_sp.add_parser("cast", help="Record one manual ballot")
    vc.add_argument("--session", required=True)
    vc.add_argument("--voter", required=True)
    vc.add_argument("--target", required=True)
    vc.add_argument("--reason", default="")
    vc.add_argument("--round", type=int)
    vre = vote_sp.add_parser("replay", help="Recompute the tally from the stored ledger")
    vre.add_argument("--session", required=True)
    vre.add_argument("--out", help="Optional JSON output path")
    va = vote_sp.add_parser("audit", help="Audit ballot legality and evidence support")
    va.add_argument("--session", required=True)
    va.add_argument("--out", help="Optional JSON output path")
    vcfg = vote_sp.add_parser("config", help="Show effective voting rules")
    vcfg.add_argument("--session", required=True)

    retro_p = sub.add_parser("retro", help="Evaluate a completed session and render the assessment")
    retro_p.add_argument("--session", required=True)
    retro_p.add_argument("--out", help="Optional JSON evaluation path")
    retro_p.add_argument("--markdown", help="Optional Markdown evaluation path")

    rep_p = sub.add_parser("replay", help="Generate five-section post-game replay")
    rep_p.add_argument("--session", required=True)
    rep_p.add_argument("--out", help="Markdown output path")
    rep_p.add_argument("--with-eval", action="store_true", help="Append the retrospective evaluation section")

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
            result = ingest_mod.canonicalize(args.script)
            if result["decision"] != "import":
                raise ValueError("ERR_SCRIPT_NOT_PLAYABLE: " + ", ".join(
                    r["code"] for r in result["reasons"]
                ))
            onto = result["onto"]
            db_mod.save_script(conn, onto)
            registry_mod.register(
                conn, onto["script_id"], title=onto.get("title"),
                source_kind=result["detected"]["kind"],
                canonical_version=onto.get("schema_version", ""),
            )
            if result["skeleton"]:
                registry_mod.save_skeleton(conn, onto["script_id"], result["skeleton"])
            if args.out:
                write_json(args.out, onto)
            print_json({
                "script_id": onto["script_id"], "detected": result["detected"]["kind"],
                "checks": result["checks"], "stored": True,
            })

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

        elif args.cmd == "ingest":
            if args.ingest_action == "detect":
                print_json(ingest_mod.detect(args.path))
            elif args.ingest_action == "convert":
                result = ingest_mod.canonicalize(args.path, llm=args.llm)
                if args.out and result["onto"]:
                    ingest_mod.write_canonical(args.out, result["onto"])
                print_json({
                    "detected": result["detected"]["kind"],
                    "decision": result["decision"],
                    "reasons": [r["code"] for r in result["reasons"]],
                    "script_id": (result["onto"] or {}).get("script_id"),
                    "completions": [c["rule"] for c in result["completions"]],
                    "checks": result["checks"],
                    "skeleton_gaps": [g["slot"] for g in (result["skeleton"] or {}).get("gaps", [])],
                    "llm": result["llm"],
                    "out": args.out,
                })
            elif args.ingest_action == "skeleton":
                result = ingest_mod.canonicalize(args.path, llm=args.llm)
                skel = result["skeleton"]
                if args.out and skel:
                    ingest_mod.write_canonical(args.out, skel)
                print_json({
                    "decision": result["decision"],
                    "skeleton": skel, "llm": result["llm"], "out": args.out,
                })
            elif args.ingest_action == "add":
                result = ingest_mod.ingest_source(args.path, args.root, args.force, args.llm)
                print_json({
                    "detected": result["detected"]["kind"],
                    "decision": result["decision"],
                    "script_id": (result.get("entry") or {}).get("script_id"),
                    "reasons": [r["code"] for r in result["reasons"]],
                    "raw_files": (result.get("entry") or {}).get("raw_files", []),
                    "skeleton_file": (result.get("entry") or {}).get("skeleton_file"),
                })
            elif args.ingest_action == "batch":
                print_json(ingest_mod.ingest_tree(
                    args.path, args.root, args.limit, args.force, args.llm
                ))
            elif args.ingest_action == "report":
                print_json(ingest_mod.ingest_report(args.root))
            elif args.ingest_action == "validate":
                print_json(ingest_mod.validate_canonical(args.file))

        elif args.cmd == "scripts":
            print_json(db_mod.list_scripts(conn))

        elif args.cmd == "script":
            if args.script_action == "list":
                print_json(registry_mod.list_scripts(conn, args.status))
            elif args.script_action == "show":
                print_json(registry_mod.get(conn, args.script_id))
            elif args.script_action == "skeleton":
                print_json(registry_mod.skeleton(conn, args.script_id, args.version))
            elif args.script_action == "history":
                print_json(registry_mod.history(conn, args.script_id))
            elif args.script_action == "promote":
                print_json(registry_mod.promote(conn, args.script_id, args.status, args.notes))

        elif args.cmd == "seats":
            print_json(db_mod.get_session(conn, args.session)["participants"])

        elif args.cmd == "memory":
            db_mod.get_session(conn, args.session)
            runtime_state = db_mod.get_game_state(conn, args.session, "runtime", {})
            seats = [args.seat] if args.seat else sorted(runtime_state)
            if args.memory_action == "show":
                out = {}
                for seat in seats:
                    state = runtime_state.get(seat, {})
                    if args.cue:
                        out[seat] = memory_mod.recall(
                            state, args.round or db_mod.get_game_state(conn, args.session, "round", 0),
                            cue=args.cue, limit=args.limit,
                        )
                    else:
                        items = [m for m in state.get("memory", []) if isinstance(m, dict)]
                        if args.round is not None:
                            items = [m for m in items if m.get("round") == args.round]
                        out[seat] = items[-args.limit:]
                print_json(out)
            elif args.memory_action == "commitments":
                print_json({seat: runtime_state.get(seat, {}).get("commitments", {}) for seat in seats})

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

        elif args.cmd == "gm":
            if args.gm_action == "decide":
                session_doc = db_mod.get_session(conn, args.session)
                onto = db_mod.get_script(conn, session_doc["script_id"])
                state = db_mod.get_game_state(conn, args.session, "runtime", {})
                print_json(gm_mod.suggest_decision(
                    conn, args.session, args.round, onto, state, args.policy
                ))
            elif args.gm_action == "branch":
                decision = {"policy": args.policy}
                if args.release_clue:
                    decision["overrides"] = {"release_clue": args.release_clue}
                print_json(gm_mod.create_branch(
                    conn, args.session, args.at_round, args.label,
                    args.override_round, args.override_utterance, decision, args.seed,
                ))
            elif args.gm_action == "tree":
                print_json(gm_mod.branch_tree(conn, args.session))
            elif args.gm_action == "diff":
                print_json(gm_mod.branch_diff(conn, args.branch))
            elif args.gm_action == "activate":
                print_json(gm_mod.activate(conn, args.branch))
            elif args.gm_action == "merge":
                print_json(gm_mod.merge(conn, args.branch, args.rationale))
            elif args.gm_action == "rollback":
                print_json(gm_mod.rollback(conn, args.branch))

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

        elif args.cmd == "audit-script":
            report = solver_mod.audit_script_by_id(conn, args.script_id)
            if args.out:
                write_json(args.out, report)
            print_json({
                "script_id": report["script_id"], "verdict": report["verdict"],
                "checks": report["checks"], "solution": report["solution"],
                "summary": report["summary"],
            })

        elif args.cmd == "audit-all":
            report = solver_mod.audit_all(conn, args.limit)
            if args.out:
                write_json(args.out, report)
            print_json(report)

        elif args.cmd == "vote":
            if args.vote_action == "run":
                scripted = read_json(args.scripted) if args.scripted else None
                result = vote_mod.run_vote(conn, args.session, scripted=scripted, seed=args.seed)
                result.pop("_changes", None)
                db_mod.set_game_state(conn, args.session, "result", result)
                print_json(result)
            elif args.vote_action == "cast":
                print_json(vote_mod.cast_ballot(
                    conn, args.session, args.voter, args.target,
                    reason=args.reason, round_no=args.round,
                ))
            elif args.vote_action == "replay":
                report = vote_mod.replay_tally(conn, args.session)
                if args.out:
                    write_json(args.out, report)
                print_json(report)
            elif args.vote_action == "audit":
                report = vote_mod.audit(conn, args.session)
                if args.out:
                    write_json(args.out, report)
                print_json(report)
            elif args.vote_action == "config":
                state = db_mod.get_game_state(conn, args.session, "vote_state", {}) or {}
                print_json(state.get("rules") or vote_mod.normalize_rules())

        elif args.cmd == "retro":
            report, markdown = retro_mod.evaluate_and_render(conn, args.session)
            if args.out:
                write_json(args.out, report)
            if args.markdown:
                Path(args.markdown).write_text(markdown, encoding="utf-8")
            print_json({
                "session_id": args.session,
                "metrics": report["metrics"],
                "solvability": report["retrospective"]["solvability"]["verdict"],
            })

        elif args.cmd == "replay":
            text = replay_mod.build_replay(conn, args.session)
            sections = list(replay_mod.SECTIONS)
            if args.with_eval:
                _, evaluation_md = retro_mod.evaluate_and_render(conn, args.session)
                text = text.rstrip() + "\n\n" + evaluation_md
                sections.append("回溯评估")
            out = args.out or f"replay_{args.session}.md"
            Path(out).write_text(text, encoding="utf-8")
            print_json({"out": out, "sections": sections, "bytes": len(text.encode("utf-8"))})

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
