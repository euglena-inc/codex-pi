"""Transactional plugin-owned board storage (never the Codex application's DB).

The small board.json is a format marker. Cards are bounded projections; complete
events, exact decisions and queue claims are indexed rows, with cursor queries.
Legacy JSON is imported only by an explicit, writer-free recovery operation.
"""
from __future__ import annotations

from collections.abc import MutableMapping
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from pi_core import TASK_RE, atomic
from pi_takeover import _records, review_policy

STORE_FILE = "board.store.sqlite3"
STORE_SCHEMA = 2
APPLICATION_ID = 0x43504932
MAX_RECORD_BYTES = 262_144
PAGE_SIZE = 50
EVENT_WINDOW = 50


def encode(value) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("individual board record exceeds the bounded record limit")
    return text


def decode(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_RECORD_BYTES:
        raise ValueError("oversized or unreadable board record")
    return json.loads(text)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        default=lambda v:v.loaded if isinstance(v,Phases) else None).encode()).hexdigest()


@contextmanager
def connection(board_file, write=False, create=False):
    path = Path(board_file).with_name(STORE_FILE)
    if path.is_symlink():
        raise ValueError("board store must not be a symlink")
    if create:
        db = sqlite3.connect(path, timeout=5)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
    else:
        from urllib.parse import quote
        db = sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode="
                             + ("rw" if write else "ro"), uri=True, timeout=5)
        if db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            db.close()
            raise ValueError("not a Codex-Pi board store")
    until = time.monotonic() + 5
    if hasattr(db,'setlimit'):
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_RECORD_BYTES*2+4096)
    db.set_progress_handler(lambda: int(time.monotonic() > until), 1000)
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        else:
            db.execute("BEGIN")
        yield db
        if write or create:
            db.commit()
    except sqlite3.Error as exc:
        db.rollback()
        raise ValueError(f"board store transaction unavailable: {exc}") from None
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards (task TEXT PRIMARY KEY, body TEXT NOT NULL, policy TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (task TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER NOT NULL,
 handled INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(task,id));
CREATE INDEX IF NOT EXISTS event_window ON events(task,handled,seq);
CREATE TABLE IF NOT EXISTS decisions (task TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY(task,id));
CREATE TABLE IF NOT EXISTS queue_tasks (task TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (task TEXT NOT NULL, id TEXT NOT NULL, status TEXT,
 body TEXT NOT NULL, PRIMARY KEY(task,id));
CREATE INDEX IF NOT EXISTS claim_status ON claims(task,status,id);
CREATE TABLE IF NOT EXISTS claim_history (task TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
 body TEXT NOT NULL, PRIMARY KEY(task,id,digest));
CREATE TABLE IF NOT EXISTS notify_phases (task TEXT NOT NULL, phase TEXT NOT NULL, body TEXT NOT NULL,
 PRIMARY KEY(task,phase));
"""


class Phases(MutableMapping):
    """Per-phase notification quota/history; current phase reads one indexed row."""
    def __init__(self,board_file,task_id):
        self.board_file=Path(board_file);self.task_id=task_id;self.loaded={};self.present=set()
    def __getitem__(self,key):
        if key not in self.loaded:
            with connection(self.board_file) as db:
                row=db.execute('SELECT body FROM notify_phases WHERE task=? AND phase=?',
                               (self.task_id,key)).fetchone()
            if row is None:raise KeyError(key)
            self.loaded[key]=decode(row[0]);self.present.add(key)
        return self.loaded[key]
    def __setitem__(self,key,value):self.loaded[key]=value
    def __delitem__(self,key):raise ValueError('notification quota history cannot be deleted')
    def __iter__(self):
        cursor=''
        while True:
            with connection(self.board_file) as db:
                rows=db.execute('SELECT phase FROM notify_phases WHERE task=? AND phase>? ORDER BY phase LIMIT ?',
                                (self.task_id,cursor,PAGE_SIZE)).fetchall()
            if not rows:return
            yield from (r[0] for r in rows);cursor=rows[-1][0]
    def __len__(self):
        with connection(self.board_file) as db:
            count=db.execute('SELECT count(*) FROM notify_phases WHERE task=?',(self.task_id,)).fetchone()[0]
            existing=sum(bool(db.execute('SELECT 1 FROM notify_phases WHERE task=? AND phase=?',
                                        (self.task_id,key)).fetchone()) for key in set(self.loaded)-self.present)
        return count+len(set(self.loaded)-self.present)-existing


class StoredCard(dict):
    def __init__(self, board_file, projection, events, handled, policy, pending_count, handled_count):
        super().__init__(projection, events=events, handled=handled)
        self.board_file = Path(board_file)
        self.policy_base = policy
        self.baseline_decisions = _records(self)
        self.initial_events = {e["id"]: encode(e) for e in events}
        self.initial_handled = {k: encode(v) for k,v in handled.items()}
        self.initial_pending = pending_count
        self.initial_handled_count = handled_count
        notify=self.get('notify')
        if isinstance(notify,dict):notify['phases']=Phases(self.board_file,self['taskId'])
        self.baseline = fingerprint(self)

    def lookup_event(self, event_id):
        with connection(self.board_file) as db:
            row = db.execute("SELECT body FROM events WHERE task=? AND id=?",
                             (self["taskId"], event_id)).fetchone()
            decision = db.execute("SELECT body FROM decisions WHERE task=? AND id=?",
                                  (self["taskId"], event_id)).fetchone()
        if row:
            event = decode(row[0])
            self["events"].append(event)
            self.initial_events[event_id] = encode(event)
            if decision:
                record = decode(decision[0])
                self["handled"][event_id] = record
                self.initial_handled[event_id] = encode(record)
                self.baseline_decisions.update(_records({"handled":{event_id:record},
                                                        "events":[event]}))
            return event
        if decision:
            record = decode(decision[0])
            # A legacy handled-only record still supports exact immutable replay.
            self["handled"][event_id] = record
            self.initial_handled[event_id] = encode(record)
            self.baseline_decisions.update(_records({"handled":{event_id:record}}))
        return None

    def pending_count(self):
        count = self.initial_pending
        for event in self["events"]:
            previous = self.initial_events.get(event["id"])
            old_pending = previous is not None and not decode(previous).get("handled")
            count += int(not event.get("handled")) - int(old_pending)
        return count

    def handled_count(self):
        return self.initial_handled_count + len(set(self["handled"]) - set(self.initial_handled))


def load_card(board_file, task_id):
    with connection(board_file) as db:
        row = db.execute("SELECT body,policy FROM cards WHERE task=?", (task_id,)).fetchone()
        if row is None:
            return None
        # Both ends of the pending window remain reachable; current-round events
        # are at its newest end. All other events are accessible by id or cursor.
        events={};loaded_bytes=0
        # Prefer the newest terminal event; add the oldest pending end with
        # whatever remains. Byte limits bound the working set as well as count.
        for statement in ["SELECT body FROM events WHERE task=? ORDER BY seq DESC LIMIT ?",
                          "SELECT body FROM events WHERE task=? AND handled=0 ORDER BY seq LIMIT ?"]:
            cursor=db.execute(statement,(task_id,EVENT_WINDOW//2))
            for (body,) in cursor:
                event=decode(body)
                if event['id'] in events:continue
                size=len(body.encode())
                if loaded_bytes+size>MAX_RECORD_BYTES:continue
                events[event['id']]=event;loaded_bytes+=size
        handled = {}
        for event_id in events:
            record = db.execute("SELECT body FROM decisions WHERE task=? AND id=?",
                                (task_id,event_id)).fetchone()
            if record:
                handled[event_id] = decode(record[0])
        pending = db.execute("SELECT count(*) FROM events WHERE task=? AND handled=0",(task_id,)).fetchone()[0]
        nhandled = db.execute("SELECT count(*) FROM decisions WHERE task=?",(task_id,)).fetchone()[0]
    projection=decode(row[0])
    if not isinstance(projection,dict) or projection.get('taskId')!=task_id:
        raise ValueError('stored card identity mismatch')
    return StoredCard(board_file, projection, sorted(events.values(),key=lambda e:e["seq"]),
                      handled, decode(row[1]), pending, nhandled)


class Cards(MutableMapping):
    def __init__(self, board_file, revision):
        self.board_file = Path(board_file)
        self.revision = revision
        self.loaded = {}

    def __getitem__(self, task_id):
        if task_id not in self.loaded:
            card = load_card(self.board_file, task_id)
            if card is None:
                raise KeyError(task_id)
            self.loaded[task_id] = card
        return self.loaded[task_id]

    def __setitem__(self, key, value):
        self.loaded[key] = value

    def __delitem__(self, key):
        raise ValueError("board task history cannot be deleted")

    def __iter__(self):
        cursor = ""
        while True:
            ids = self.page(cursor)
            if not ids:
                return
            yield from ids
            cursor = ids[-1]

    def __len__(self):
        with connection(self.board_file) as db:
            return db.execute("SELECT count(*) FROM cards").fetchone()[0]

    def page(self, cursor="", thread=None):
        with connection(self.board_file) as db:
            if thread:
                # owner is a projection field; selection is still a bounded page.
                rows = db.execute("SELECT task FROM cards WHERE task>? AND "
                                  "json_extract(body,'$.ownerThread')=? ORDER BY task LIMIT ?",
                                  (cursor,thread,PAGE_SIZE)).fetchall()
            else:
                rows = db.execute("SELECT task FROM cards WHERE task>? ORDER BY task LIMIT ?",
                                  (cursor,PAGE_SIZE)).fetchall()
        return [r[0] for r in rows]


def read_store(board_file):
    with connection(board_file) as db:
        row = db.execute("SELECT body FROM meta WHERE key='board'").fetchone()
        if not row:
            raise ValueError("board store metadata is missing")
        meta = decode(row[0])
        if meta.get('schemaVersion')!=STORE_SCHEMA or isinstance(meta.get('revision'),bool) \
                or not isinstance(meta.get('revision'),int) or meta['revision']<0:
            raise ValueError('stored board metadata is unverified')
    return dict(meta, schemaVersion=2, cards=Cards(board_file,meta["revision"]))


def _save_card(db, card):
    task_id = card["taskId"]
    if not TASK_RE.fullmatch(task_id):
        raise ValueError("unsafe stored task id")
    policy = review_policy(card)
    projection = {k:v for k,v in card.items() if k not in ("events","handled")}
    notify=projection.get('notify')
    if isinstance(notify,dict):
        phases=notify.get('phases')
        phase_rows=phases.loaded if isinstance(phases,Phases) else phases or {}
        for phase_id,ledger in phase_rows.items():
            db.execute('INSERT INTO notify_phases VALUES(?,?,?) ON CONFLICT(task,phase) DO UPDATE SET body=excluded.body',
                       (task_id,phase_id,encode(ledger)))
        projection['notify']={k:v for k,v in notify.items() if k!='phases'}|{'phases':{}}
    for event in card.get("events",[]):
        text = encode(event)
        row = db.execute("SELECT body FROM events WHERE task=? AND id=?",(task_id,event["id"])).fetchone()
        if row:
            old = decode(row[0])
            if old.get("handled") and text != row[0]:
                raise ValueError("stored handled event is immutable")
            for key in ("id","seq","round","kind","fingerprint","candidate","createdAt"):
                if old.get(key) != event.get(key):
                    raise ValueError(f"stored event identity changed ({key})")
        db.execute("INSERT INTO events VALUES(?,?,?,?,?) ON CONFLICT(task,id) DO UPDATE SET "
                   "handled=excluded.handled,body=excluded.body",
                   (task_id,event["id"],event["seq"],int(bool(event.get("handled"))),text))
    for event_id,record in (card.get("handled") or {}).items():
        text = encode(record)
        old = db.execute("SELECT body FROM decisions WHERE task=? AND id=?",(task_id,event_id)).fetchone()
        if old and old[0] != text:
            raise ValueError("stored decision is immutable")
        db.execute("INSERT OR IGNORE INTO decisions VALUES(?,?,?)",(task_id,event_id,text))
    db.execute("INSERT INTO cards VALUES(?,?,?) ON CONFLICT(task) DO UPDATE SET "
               "body=excluded.body,policy=excluded.policy",(task_id,encode(projection),encode(policy)))


def write_store(board_file, board):
    cards = board["cards"]
    if not isinstance(cards, Cards):
        raise ValueError("a stored board needs its exact loaded revision")
    changes = [c for c in cards.loaded.values()
               if not isinstance(c,StoredCard) or fingerprint(c) != c.baseline]
    with connection(board_file,write=True) as db:
        row = db.execute("SELECT body FROM meta WHERE key='board'").fetchone()
        previous = decode(row[0])
        if previous["revision"] != cards.revision:
            raise ValueError("stale board projection; read the current revision before writing")
        for card in changes:
            _save_card(db,card)
        meta = {k:v for k,v in board.items() if k != "cards"}
        meta["schemaVersion"] = 2
        db.execute("UPDATE meta SET body=? WHERE key='board'",(encode(meta),))
    cards.revision = board["revision"]


def event_page(board_file, task_id, after=0, pending=False):
    if isinstance(after,bool) or not isinstance(after,int) or after<0:
        raise ValueError("event cursor must be a nonnegative sequence")
    with connection(board_file) as db:
        rows = db.execute("SELECT body FROM events WHERE task=? AND seq>? "
                          + ("AND handled=0 " if pending else "")
                          + "ORDER BY seq LIMIT ?",(task_id,after,PAGE_SIZE+1)).fetchall()
    events=[decode(r[0]) for r in rows[:PAGE_SIZE]]
    return {"events":events,"nextCursor":events[-1]["seq"] if len(rows)>PAGE_SIZE else None}


def decision_page(board_file,task_id,after=''):
    with connection(board_file) as db:
        rows=db.execute('SELECT id,body FROM decisions WHERE task=? AND id>? ORDER BY id LIMIT ?',
                        (task_id,after,PAGE_SIZE+1)).fetchall()
    return {'decisions':[dict(decode(body),eventId=key) for key,body in rows[:PAGE_SIZE]],
            'nextCursor':rows[PAGE_SIZE-1][0] if len(rows)>PAGE_SIZE else None}


def decision_metrics(board_file,task_id):
    with connection(board_file) as db:
        rows=db.execute("SELECT coalesce(json_extract(body,'$.eventKind'),'unknown'),"
                        "json_extract(body,'$.decision'),count(*) FROM decisions WHERE task=? "
                        "GROUP BY 1,2 LIMIT ?",(task_id,PAGE_SIZE+1)).fetchall()
        failures=db.execute("SELECT coalesce(json_extract(body,'$.failureKind'),'quality'),count(*) "
                            "FROM decisions WHERE task=? AND json_extract(body,'$.decision') "
                            "IN ('rejected','changes_requested') GROUP BY 1 LIMIT ?",(task_id,PAGE_SIZE+1)).fetchall()
    if len(rows)>PAGE_SIZE or len(failures)>PAGE_SIZE:
        raise ValueError('decision metric dimensions exceeded the bounded view; use decisions cursor pages')
    counts={}
    for kind,decision,count in rows:
        if decision:counts.setdefault(kind,{})[decision]=count
    return counts,dict(failures)


class Claims(MutableMapping):
    def __init__(self, board_file, task_id):
        self.board_file,self.task_id=Path(board_file),task_id
        self.loaded={}; self.deleted=set()
        with connection(board_file) as db:
            rows=db.execute("SELECT id,body FROM claims WHERE task=? AND status='inflight' LIMIT ?",
                            (task_id,PAGE_SIZE+1)).fetchall()
            if len(rows)>PAGE_SIZE:
                raise ValueError("too many inflight claims; recover exact event ids")
            rows+=db.execute("SELECT id,body FROM claims WHERE task=? ORDER BY id LIMIT ?",
                             (task_id,PAGE_SIZE)).fetchall()
        self.loaded.update({key:decode(body) for key,body in rows})
        self.baseline={k:encode(v) for k,v in self.loaded.items()}

    def __getitem__(self,key):
        if key in self.deleted:raise KeyError(key)
        if key not in self.loaded:
            with connection(self.board_file) as db:
                row=db.execute("SELECT body FROM claims WHERE task=? AND id=?",(self.task_id,key)).fetchone()
            if not row:raise KeyError(key)
            self.loaded[key]=decode(row[0]); self.baseline[key]=row[0]
        return self.loaded[key]

    def __setitem__(self,key,value):
        self.loaded[key]=value; self.deleted.discard(key)

    def __delitem__(self,key):
        self[key]; self.deleted.add(key)

    def __iter__(self):
        return iter(k for k in self.loaded if k not in self.deleted)

    def __len__(self):
        with connection(self.board_file) as db:
            count=db.execute("SELECT count(*) FROM claims WHERE task=?",(self.task_id,)).fetchone()[0]
        return count+len(set(self.loaded)-set(self.baseline))-len(self.deleted)

    def counts(self):
        with connection(self.board_file) as db:
            result=dict(db.execute("SELECT status,count(*) FROM claims WHERE task=? GROUP BY status",
                                   (self.task_id,)).fetchall())
        for key,value in self.loaded.items():
            old=self.baseline.get(key)
            if old:
                st=decode(old).get("status");result[st]=result.get(st,0)-1
            if key not in self.deleted:
                st=value.get("status");result[st]=result.get(st,0)+1
        return result


class QueueTasks(MutableMapping):
    def __init__(self,board_file):
        self.board_file=Path(board_file);self.loaded={}

    def __getitem__(self,key):
        if key not in self.loaded:
            with connection(self.board_file) as db:
                row=db.execute("SELECT body FROM queue_tasks WHERE task=?",(key,)).fetchone()
            if not row:raise KeyError(key)
            self.loaded[key]=dict(decode(row[0]),claims=Claims(self.board_file,key))
        return self.loaded[key]

    def __setitem__(self,key,value):self.loaded[key]=value
    def __delitem__(self,key):raise ValueError("queue task history cannot be deleted")
    def __iter__(self):
        cursor=""
        while True:
            with connection(self.board_file) as db:
                rows=db.execute("SELECT task FROM queue_tasks WHERE task>? ORDER BY task LIMIT ?",
                                (cursor,PAGE_SIZE)).fetchall()
            if not rows:return
            yield from (r[0] for r in rows);cursor=rows[-1][0]
    def __len__(self):
        with connection(self.board_file) as db:
            return db.execute("SELECT count(*) FROM queue_tasks").fetchone()[0]


def _save_queue(db,task_id,entry):
    claims=entry.get("claims")
    if claims is None:claims={}
    loaded=claims.loaded if isinstance(claims,Claims) else claims
    deleted=claims.deleted if isinstance(claims,Claims) else set()
    for event_id in set(loaded)|deleted:
        old=db.execute("SELECT body FROM claims WHERE task=? AND id=?",(task_id,event_id)).fetchone()
        text=None if event_id in deleted else encode(loaded[event_id])
        if old and old[0]!=text:
            digest=hashlib.sha256(old[0].encode()).hexdigest()
            db.execute("INSERT OR IGNORE INTO claim_history VALUES(?,?,?,?)",(task_id,event_id,digest,old[0]))
        if text is None:
            db.execute("DELETE FROM claims WHERE task=? AND id=?",(task_id,event_id))
        else:
            db.execute("INSERT INTO claims VALUES(?,?,?,?) ON CONFLICT(task,id) DO UPDATE SET "
                       "status=excluded.status,body=excluded.body",
                       (task_id,event_id,loaded[event_id].get("status"),text))
    projection={k:v for k,v in entry.items() if k!="claims"}
    db.execute("INSERT INTO queue_tasks VALUES(?,?) ON CONFLICT(task) DO UPDATE SET body=excluded.body",
               (task_id,encode(projection)))


def write_queue_store(board_file,queue):
    tasks=queue["tasks"]
    with connection(board_file,write=True) as db:
        for task_id,entry in tasks.loaded.items():
            _save_queue(db,task_id,entry)


def initialize_store(board_file,board,queue=None,source_hash=None):
    """Called with admission, board, queue and all legacy writer locks held.

    Publish the marker last. A crash before publication leaves JSON authoritative;
    repeating an import can only use the exact same immutable source hash.
    """
    path=Path(board_file)
    store=path.with_name(STORE_FILE)
    populate=True
    if store.exists():
        with connection(path) as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row=db.execute("SELECT body FROM meta WHERE key='sourceHash'").fetchone() if 'meta' in tables else None
            if row is not None:
                if decode(row[0])!=source_hash:
                    raise ValueError("unpublished board store has a different source; inspect recovery evidence")
                populate=False
            elif any(db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in
                     tables & {'meta','cards','events','decisions','queue_tasks','claims','claim_history','notify_phases'}):
                raise ValueError('unverified partial import contains records; preserve it before recovery')
    if populate:
        with connection(path,create=not store.exists(),write=store.exists()) as db:
            # executescript starts its own transaction, so populate within a new
            # explicit transaction after defining the schema.
            db.executescript(SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            meta={k:v for k,v in board.items() if k!="cards"};meta["schemaVersion"]=2
            db.execute("INSERT INTO meta VALUES('board',?)",(encode(meta),))
            db.execute("INSERT INTO meta VALUES('sourceHash',?)",(encode(source_hash),))
            for card in board["cards"].values():_save_card(db,card)
            for task_id,entry in (queue or {}).get("tasks",{}).items():_save_queue(db,task_id,entry)
    # SQLite transactions and fsync complete before the small marker switches.
    # Obsolete core helpers read JSON directly. Pause sentinels prevent them
    # from continuing any imported task while the actual card is in SQLite.
    guards={key:{"paused":True} for key in board["cards"]}
    marker={"schemaVersion":2,"storage":"sqlite","storeFile":STORE_FILE,
            "legacyControlPaused":True,"cards":guards}
    temp=path.with_name(path.name+'.recovery-'+uuid.uuid4().hex+'.tmp')
    with temp.open('xb') as stream:
        stream.write(encode(marker).encode()+b'\n');stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return read_store(path)
