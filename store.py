import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.directory = directory
        self.db = sqlite3.connect(directory / 'life.sqlite3', timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS images(id TEXT PRIMARY KEY,path TEXT NOT NULL,source_chat TEXT NOT NULL,
                source_message TEXT NOT NULL,reason TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,state TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,
                result TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS seen(chat TEXT NOT NULL,message TEXT NOT NULL,PRIMARY KEY(chat,message));
            CREATE TABLE IF NOT EXISTS post_notifications(
                job_id INTEGER NOT NULL,owner_id TEXT NOT NULL,bot_uin TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',result TEXT NOT NULL DEFAULT '{}',
                updated REAL NOT NULL,PRIMARY KEY(job_id,owner_id));
        ''')
        self.db.execute("UPDATE post_notifications SET state='unknown',result=? WHERE state='sending'", (json.dumps({'reason': '通知发送中断，避免重复提醒'}),))
        self.db.execute("UPDATE jobs SET state='unknown',result=? WHERE state='running'", (json.dumps({'reason': '上次执行中断，需要核对外部结果'}),))
        self.db.commit()

    def close(self):
        self.db.close()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def mark_seen(self, chat, mid):
        with self.db:
            cur = self.db.execute('INSERT OR IGNORE INTO seen VALUES (?,?)', (chat, mid))
            self.db.execute('DELETE FROM seen WHERE rowid NOT IN (SELECT rowid FROM seen ORDER BY rowid DESC LIMIT 20000)')
        return cur.rowcount == 1

    def add_image(self, blob: bytes, chat: str, mid: str, reason: str, limit: int):
        digest = hashlib.sha256(blob).hexdigest()
        existing = self.image(digest)
        if existing:
            return existing
        if self.db.execute('SELECT COUNT(*) FROM images').fetchone()[0] >= limit:
            raise ValueError('本地收藏已达到容量限制')
        folder = self.directory / 'images'
        folder.mkdir(exist_ok=True)
        path = folder / (digest + '.png')
        path.write_bytes(blob)
        with self.db:
            self.db.execute('INSERT INTO images VALUES (?,?,?,?,?,?)', (digest, str(path), chat, mid, reason[:500], time.time()))
        return self.image(digest)

    def image(self, image_id):
        row = self.db.execute('SELECT * FROM images WHERE id=?', (image_id,)).fetchone()
        return dict(row) if row else None

    def images(self, count=10):
        return [dict(r) for r in self.db.execute('SELECT * FROM images ORDER BY created DESC LIMIT ?', (count,))]

    def queue(self, kind: str, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256((kind + ':' + raw).encode()).hexdigest()
        now = time.time()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs(kind,payload,fingerprint,state,created,updated) VALUES (?,?,?,'draft',?,?)", (kind, raw, digest, now, now))
        return self.job(self.db.execute('SELECT id FROM jobs WHERE fingerprint=?', (digest,)).fetchone()[0])

    def job(self, job_id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('任务不存在')
        item = dict(row)
        item['payload'] = json.loads(item['payload'])
        item['result'] = json.loads(item['result'])
        return item

    def jobs(self, state='draft', count=10):
        return [self.job(r[0]) for r in self.db.execute('SELECT id FROM jobs WHERE state=? ORDER BY id DESC LIMIT ?', (state, count))]

    def claim(self, job_id, limit, gap_seconds, now=None):
        now = time.time() if now is None else now
        midnight = datetime.fromtimestamp(now, ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            job = self.job(job_id)
            if job['state'] != 'draft':
                raise ValueError('任务已处理或结果待核对，不能重复提交')
            rows = self.db.execute("SELECT updated FROM jobs WHERE kind=? AND state IN ('running','confirmed','unknown')", (job['kind'],)).fetchall()
            if any(now-r[0] < gap_seconds for r in rows):
                raise ValueError('操作尚在冷却时间内')
            if sum(r[0] >= midnight for r in rows) >= limit:
                raise ValueError('今日操作次数已达到上限')
            self.db.execute("UPDATE jobs SET state='running',updated=? WHERE id=?", (now, job_id))
            self.db.commit()
            return job
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, job_id, state, result, *, notify_owners=(), bot_uin=''):
        if state not in {'confirmed', 'failed', 'unknown', 'cancelled'}:
            raise ValueError('非法结果状态')
        now = time.time()
        with self.db:
            self.db.execute('UPDATE jobs SET state=?,result=?,updated=? WHERE id=?', (state, json.dumps(result, ensure_ascii=False), now, job_id))
            job = self.job(job_id)
            if state == 'confirmed' and job['kind'] == 'post' and result.get('tid'):
                for owner in dict.fromkeys(notify_owners):
                    self.db.execute('INSERT OR IGNORE INTO post_notifications(job_id,owner_id,bot_uin,updated) VALUES (?,?,?,?)', (job_id, str(owner), bot_uin, now))

    def pending_notifications(self, limit=10):
        return [dict(row) for row in self.db.execute(
            "SELECT n.* FROM post_notifications n JOIN jobs j ON j.id=n.job_id WHERE n.state='pending' AND j.state='confirmed' AND j.kind='post' ORDER BY n.updated LIMIT ?", (limit,))]

    def claim_notification(self, job_id, owner_id):
        with self.db:
            result = self.db.execute("UPDATE post_notifications SET state='sending',updated=? WHERE job_id=? AND owner_id=? AND state='pending'", (time.time(), job_id, owner_id))
        return result.rowcount == 1

    def finish_notification(self, job_id, owner_id, state, result):
        if state not in {'sent', 'failed', 'unknown', 'cancelled'}:
            raise ValueError('非法通知状态')
        with self.db:
            self.db.execute('UPDATE post_notifications SET state=?,result=?,updated=? WHERE job_id=? AND owner_id=?', (state, json.dumps(result, ensure_ascii=False), time.time(), job_id, owner_id))

    def notification_status(self, limit=10):
        return [dict(row) for row in self.db.execute('SELECT job_id,owner_id,state FROM post_notifications ORDER BY updated DESC LIMIT ?', (limit,))]
