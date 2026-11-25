"""Shared fixture: parse sample script, normalize L1, assemble one session."""

from pathlib import Path

from imms import assemble, db, normalize, ontology

ROOT = Path(__file__).resolve().parents[1]


def make_session_db(path):
    conn = db.connect(path)
    onto = ontology.parse_script_dir(ROOT / "examples" / "scripts" / "snow-night-villa.yaml")
    db.save_script(conn, onto)
    doc, _ = normalize.normalize_persona(onto, ROOT / "config")
    db.save_persona_l1(conn, onto["script_id"], doc)
    session = assemble.assemble(conn, onto["script_id"], "lin_wanqiu")
    return conn, session
