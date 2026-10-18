"""Single-process durable event outbox; SQLite never stores keys or raw BLE bytes."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3


class Outbox:
    def __init__(self, path, capacity=1000, history=2048):
        self.capacity, self.history = capacity, history
        self.lock = None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = os.fdopen(os.open(str(path) + '.lock', os.O_RDWR | os.O_CREAT, 0o600), 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Another bridge owns this outbox') from None
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            os.close(fd)
            os.chmod(path, 0o600)
            self.db = sqlite3.connect(path)
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('''CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_hash TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL,
                delivered INTEGER NOT NULL DEFAULT 0)''')
            self.db.commit()
        except Exception:
            self.lock.close()
            raise

    def bind_topic(self, topic):
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            row = self.db.execute("SELECT value FROM metadata WHERE name='topic'").fetchone()
            if row and row[0] != topic:
                raise ValueError('Outbox belongs to a different topic; use a different path')
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('topic', ?)", (topic,))

    def enqueue(self, frame, event):
        fingerprint = hashlib.sha256(frame).hexdigest()
        with self.db:
            if self.db.execute('SELECT 1 FROM events WHERE frame_hash=?', (fingerprint,)).fetchone():
                return None
            if self.pending_count() >= self.capacity:
                raise BufferError('Outbox capacity exhausted')
            cursor = self.db.execute('INSERT INTO events(frame_hash,event_id,payload) VALUES(?,?,?)',
                                     (fingerprint, event['eventId'], '{}'))
            event = dict(event, sequence=cursor.lastrowid)
            self.db.execute('UPDATE events SET payload=? WHERE sequence=?',
                            (json.dumps(event), event['sequence']))
        return event

    def next_event(self):
        row = self.db.execute('SELECT payload FROM events WHERE delivered=0 ORDER BY sequence LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None

    def pending_count(self):
        return self.db.execute('SELECT count(*) FROM events WHERE delivered=0').fetchone()[0]

    def acknowledge(self, event_id):
        with self.db:
            self.db.execute('UPDATE events SET delivered=1 WHERE event_id=?', (event_id,))
            self.db.execute('''DELETE FROM events WHERE delivered=1 AND sequence NOT IN
                (SELECT sequence FROM events WHERE delivered=1 ORDER BY sequence DESC LIMIT ?)''', (self.history,))

    def close(self):
        self.db.close()
        self.lock.close()
