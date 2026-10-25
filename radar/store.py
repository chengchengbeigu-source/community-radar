# -*- coding: utf-8 -*-
"""SQLite 存档：帖子、运行记录、例行同步记录。"""
import os, json, sqlite3, datetime
from .util import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
  uid TEXT PRIMARY KEY,             -- source:id
  source TEXT, ext_id TEXT, title TEXT, url TEXT,
  board TEXT, author TEXT, excerpt TEXT,
  age_text TEXT, age_days REAL, created_ts TEXT,
  score REAL, stars INTEGER, tags TEXT, reason TEXT,
  first_seen TEXT, reported_at TEXT, raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_posts_seen ON posts(first_seen);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT,
  fetched INTEGER, fresh INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS checkins (
  site TEXT, day TEXT, status TEXT, detail TEXT, ts TEXT,
  PRIMARY KEY (site, day)
);
"""


def connect():
    ensure_dirs()
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def upsert_post(c, p):
    """返回 True 表示是新帖（以前没入库）。"""
    row = c.execute("SELECT uid FROM posts WHERE uid=?", (p["uid"],)).fetchone()
    is_new = row is None
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if is_new:
        c.execute("""INSERT INTO posts
          (uid,source,ext_id,title,url,board,author,excerpt,age_text,age_days,created_ts,
           score,stars,tags,reason,first_seen,reported_at,raw)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)""",
          (p["uid"], p["source"], str(p.get("ext_id", "")), p.get("title", ""), p.get("url", ""),
           p.get("board", ""), p.get("author", ""), p.get("excerpt", ""), p.get("age_text", ""),
           p.get("age_days"), p.get("created_ts"), p.get("score", 0), p.get("stars", 0),
           json.dumps(p.get("tags", []), ensure_ascii=False),
           json.dumps(p.get("reason", []), ensure_ascii=False), now,
           json.dumps(p.get("raw", {}), ensure_ascii=False)[:4000]))
    else:
        # 老帖也要更新 —— 2026-10-26 实锤：只 INSERT 不 UPDATE 的话，
        # 补抓到的正文、重算的星级、修过的标题全都进不了库，
        # 库里永远是第一次抓到的那份（当时 excerpt 还是空的）。
        # excerpt 用「更长者胜」：宁可留长正文，也别被后来的空值覆盖。
        old_ex = c.execute("SELECT excerpt FROM posts WHERE uid=?", (p["uid"],)).fetchone()
        old_len = len((old_ex["excerpt"] or "") if old_ex else "")
        new_ex = p.get("excerpt", "") or ""
        keep_ex = new_ex if len(new_ex) >= old_len else (old_ex["excerpt"] if old_ex else "")
        c.execute("""UPDATE posts SET title=?,url=?,board=?,author=?,excerpt=?,
                     age_text=?,age_days=?,created_ts=?,score=?,stars=?,tags=?,reason=?,raw=?
                     WHERE uid=?""",
                  (p.get("title", ""), p.get("url", ""), p.get("board", ""), p.get("author", ""),
                   keep_ex, p.get("age_text", ""), p.get("age_days"), p.get("created_ts"),
                   p.get("score", 0), p.get("stars", 0),
                   json.dumps(p.get("tags", []), ensure_ascii=False),
                   json.dumps(p.get("reason", []), ensure_ascii=False),
                   json.dumps(p.get("raw", {}), ensure_ascii=False)[:4000], p["uid"]))
    c.commit()
    return is_new


def unreported(c, limit=500):
    return c.execute("""SELECT * FROM posts WHERE reported_at IS NULL
                        ORDER BY stars DESC, score DESC, first_seen DESC LIMIT ?""", (limit,)).fetchall()


def mark_reported(c, uids):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    c.executemany("UPDATE posts SET reported_at=? WHERE uid=?", [(now, u) for u in uids])
    c.commit()


def log_run(c, source, fetched, fresh, note=""):
    c.execute("INSERT INTO runs(ts,source,fetched,fresh,note) VALUES(?,?,?,?,?)",
              (datetime.datetime.now().isoformat(timespec="seconds"), source, fetched, fresh, note))
    c.commit()


def stats(c):
    tot = c.execute("SELECT COUNT(*) n FROM posts").fetchone()["n"]
    by = c.execute("SELECT source, COUNT(*) n, MAX(first_seen) last FROM posts GROUP BY source").fetchall()
    runs = c.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
    return tot, [dict(r) for r in by], runs


def checked_today(c, site, day):
    r = c.execute("SELECT status,detail FROM checkins WHERE site=? AND day=?", (site, day)).fetchone()
    return (r["status"], r["detail"]) if r else None


def log_checkin(c, site, day, status, detail):
    c.execute("""INSERT INTO checkins(site,day,status,detail,ts) VALUES(?,?,?,?,?)
                 ON CONFLICT(site,day) DO UPDATE SET status=excluded.status,
                 detail=excluded.detail, ts=excluded.ts""",
              (site, day, status, detail[:500],
               datetime.datetime.now().isoformat(timespec="seconds")))
    c.commit()
