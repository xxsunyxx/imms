"""Headless interactive driver: step-by-step rounds, resume and event stream.

G13 / #34.  This is an adapter, not a second game engine: each ``step`` calls
the existing runtime for exactly one discussion round.  The final step runs the
unchanged vote / scoring / long-term settlement.  No Web UI or new transport
protocol is introduced; callers supply input through a Python callback or a
JSON file and consume newline-delimited JSON events.
"""

import json

from . import db, journal, llm, phases, runtime

DEFAULT_ROUNDS = 3


class EventStream:
    """Append-only JSONL view of host-side game events."""

    def __init__(self, writer=None):
        self.writer = writer
        self.items = []
        self.cursor = 0

    def emit(self, event):
        item = dict(event)
        item["stream_seq"] = self.cursor + 1
        self.cursor += 1
        self.items.append(item)
        if self.writer is not None:
            self.writer.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
            flush = getattr(self.writer, "flush", None)
            if flush:
                flush()
        return item

    def drain(self, after=0):
        return [item for item in self.items if item["stream_seq"] > after]


class HeadlessDriver:
    """Drive one session one round at a time; safe to resume from stored state."""

    def __init__(self, conn, session_id, rounds=None, seed=42, llm_name="template",
                 speaker=None, gm_agent=None, gm_voice=None, stream=None):
        self.conn = conn
        self.session_id = session_id
        self.session = db.get_session(conn, session_id)
        self.seed = seed
        self.speaker = speaker if speaker is not None else llm.build_provider(llm_name)
        self.gm_agent = gm_agent if gm_agent is not None else self.speaker
        self.gm_voice = gm_voice
        self.stream = stream if stream is not None else EventStream()
        stored = db.get_game_state(conn, session_id, "total_rounds")
        self.rounds = int(rounds or stored or DEFAULT_ROUNDS)
        db.set_game_state(conn, session_id, "total_rounds", self.rounds)
        self.resume()

    def resume(self):
        """Re-derive the cursor from persisted round/events; no in-memory-only state."""
        self.cursor = int(db.get_game_state(self.conn, self.session_id, "round", 0) or 0)
        rows = self.conn.execute(
            "SELECT MAX(seq) AS last FROM game_events WHERE session_id=?",
            (self.session_id,),
        ).fetchone()
        self.last_seq = int(rows["last"] or 0)
        self.finished = bool(
            db.get_game_state(self.conn, self.session_id, "result", {})
            and self.cursor >= self.rounds
        )
        return {
            "session_id": self.session_id, "cursor": self.cursor,
            "rounds": self.rounds, "last_seq": self.last_seq,
            "finished": self.finished,
        }

    def status(self):
        return self.resume()

    @property
    def _seat_names(self):
        return {p["id"]: p.get("seat_label") or p["id"]
                for p in self.session["participants"]}

    def start_phases(self, scripted=None):
        """Run intro/self-intro/investigate once, if not already present."""
        already = any(
            item.get("phase") == "intro" for item in phases.phase_log(self.conn, self.session_id)
        )
        if already:
            return {"status": "already_started"}
        names = {c["name"]: c["id"] for c in self._script()["characters"]}
        log = runtime._run_pre_discussion(
            self.conn, self.session_id, scripted or {}, self.speaker, self.gm_agent,
            self.gm_voice, self.seed, self._seat_names, names,
        )
        phases.set_phase(self.conn, self.session_id, "discuss", round_no=1)
        self._emit_new_events()
        return {"status": "started", "log": log}

    def _script(self):
        return db.get_script(self.conn, self.session["script_id"])

    def _emit_new_events(self):
        emitted = []
        for event in journal.audit_events(self.conn, self.session_id):
            if int(event.get("seq") or 0) <= self.last_seq:
                continue
            self.stream.emit({
                "session_id": self.session_id,
                "round": event.get("round"),
                "seq": event.get("seq"),
                "actor": event.get("actor"),
                "act_type": event.get("act_type"),
                "visibility": event.get("visibility"),
                "payload": event.get("payload"),
            })
            emitted.append(event["seq"])
            self.last_seq = int(event["seq"])
        return emitted

    def step(self, utterance=None, vote_target=None, metadata=None):
        """Advance exactly one round; the last round also finalizes the game."""
        if self.finished or self.cursor >= self.rounds:
            self.finished = True
            return {"status": "finished", "round": self.cursor}
        round_no = self.cursor + 1
        final = round_no == self.rounds
        # ``user_input_index`` is persisted across steps; pad earlier rounds so
        # the supplied line lands at the current index instead of exhausting.
        scripted = {
            "utterances": [""] * (round_no - 1) + [str(utterance or "")],
            "vote": vote_target,
            "metadata": metadata or {},
        }
        out = runtime.run_session(
            self.conn, self.session_id, rounds=round_no, scripted=scripted,
            seed=self.seed, start_round=round_no, llm_name=None,
            speaker=self.speaker, gm_agent=self.gm_agent, gm_voice=self.gm_voice,
            finalize=final,
        )
        emitted = self._emit_new_events()
        self.cursor = int(db.get_game_state(self.conn, self.session_id, "round", round_no))
        self.finished = final
        return {
            "status": "finalized" if final else "advanced",
            "round": round_no,
            "events": emitted,
            "log": out["log"],
            "result": out["result"] if final else {},
            "scoring": out["scoring"] if final else {},
        }

    def run(self, inputs=None, phases=False):
        """Consume one input per round; ``inputs`` items may be str or dict."""
        if phases:
            self.start_phases()
        inputs = list(inputs or [])
        steps = []
        for index in range(self.cursor, self.rounds):
            raw = inputs[index - self.cursor] if index - self.cursor < len(inputs) else {}
            if isinstance(raw, str):
                item = {"utterance": raw}
            else:
                item = dict(raw or {})
            steps.append(self.step(
                utterance=item.get("utterance"),
                vote_target=item.get("vote_target") or item.get("vote"),
                metadata=item.get("metadata"),
            ))
            if steps[-1]["status"] == "finished":
                break
        return steps


def load_inputs(path):
    """Read a JSON array of per-round user inputs."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("inputs") or []
    if not isinstance(data, list):
        raise ValueError("ERR_DRIVER_INPUTS_NOT_LIST")
    return data
