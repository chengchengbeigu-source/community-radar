# -*- coding: utf-8 -*-
"""Serv00 免费主机申请结果监测 —— 跑在常驻轮里，出结果就推通知。

## 为什么要这么绕（三条更直白的路，实测全死）

注册页提交后被明说「人工审核，几小时到 72 小时」，而且站上**没有任何查状态入口**。
下面三条零凭据的探针逐一实测排除：

  1. **DNS**：`*.serv00.net` 和 `*.serv00.com` 都是 wildcard ——
     随手编 `zzdefinitelynotexist4477.serv00.net` 也照样解析成
     `CNAME serv00.net.` + `A 128.204.218.48`，跟真账号的子域一模一样。
  2. **面板登录**：DevilWEB（Django，panel0..16.serv00.com）对
     「用户名不存在」和「密码错」回**同一句**
     `Podany uzytkownik lub haslo jest nieprawidlowe.`，区分不了。
  3. **找回密码** `/resetpass`：表单挂图片验证码（`captcha_0` / `captcha_1`），
     没法无人值守地过。

所以唯一可靠的信号只剩**注册邮箱收到的邮件**，用 IMAP 读 Gmail。

## 凭据

只从环境变量读，仓库里不留任何明文；没配就静默跳过、不推通知。

    SERV00_GMAIL_USER  注册 serv00 时填的 Gmail 地址
    SERV00_GMAIL_PASS  该地址的「应用专用密码」（16 位，不是登录密码）
                       → https://myaccount.google.com/apppasswords

## 去重

已推过的邮件按 `Message-ID` 记进 `radar/data/serv00.json`，
和 `scheduler.json` 一样走 actions/cache 在轮次之间接续。
"""
import datetime
import email
import email.header
import imaplib
import json
import os
import re
import time

from radar.util import DATA_DIR, notify

STATE_PATH = os.path.join(DATA_DIR, "serv00.json")

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
LOOKBACK_DAYS = 20          # 往回翻多少天
MAX_FETCH = 80              # 最多逐封读多少个头部（收件箱巨大时不拖垮一轮）
MATCH = ("serv00", "hostuno", "admin.net.pl")


# -------------------------- 状态 --------------------------

def _state_load():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _state_save(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


# -------------------------- IMAP --------------------------

def _hdr(v):
    """把 MIME 折叠过的头部（=?UTF-8?B?...?=）解成可读文本。"""
    if not v:
        return ""
    out = []
    for part, enc in email.header.decode_header(v):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", "replace"))
        else:
            out.append(part)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _scan(user, pw, days=LOOKBACK_DAYS, log=print):
    """扫收件箱，返回命中的 [(msgid, from, subject, date, uid)]。"""
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        since = (datetime.date.today()
                 - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = M.search(None, "(SINCE %s)" % since)
        if typ != "OK" or not data or not data[0]:
            log("[serv00] 收件箱近 %d 天没有邮件" % days)
            return []
        uids = data[0].split()
        log("[serv00] 收件箱近 %d 天 %d 封，逐封看发件人/主题" % (days, len(uids)))
        hits = []
        for uid in uids[-MAX_FETCH:]:
            try:
                typ, d = M.fetch(
                    uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
            except Exception:
                continue
            if typ != "OK" or not d or not d[0]:
                continue
            raw = d[0][1] if isinstance(d[0], tuple) else b""
            if not isinstance(raw, bytes):
                continue
            m = email.message_from_bytes(raw)
            frm = _hdr(m.get("From"))
            sub = _hdr(m.get("Subject"))
            mid = (m.get("Message-ID") or "").strip() or "uid:%s" % uid.decode()
            if any(k in (frm + " " + sub).lower() for k in MATCH):
                hits.append((mid, frm, sub, _hdr(m.get("Date")), uid.decode()))
        return hits
    finally:
        try:
            M.logout()
        except Exception:
            pass


# -------------------------- 入口 --------------------------

def check(cfg=None, log=print):
    """查一次；有新命中就推通知。返回值只用于日志/调试。"""
    user = (os.environ.get("SERV00_GMAIL_USER") or "").strip()
    pw = (os.environ.get("SERV00_GMAIL_PASS") or "").strip()
    if not (user and pw):
        log("[serv00] 未配置 SERV00_GMAIL_USER / SERV00_GMAIL_PASS，跳过")
        return "no-cred"

    st = _state_load()
    seen = set(st.get("seen") or [])

    try:
        hits = _scan(user, pw, log=log)
    except imaplib.IMAP4.error as e:
        log("[serv00] IMAP 认证/协议失败：%s（应用专用密码对吗？）" % e)
        return "imap-error"
    except Exception as e:
        log("[serv00] IMAP 异常：%s: %s" % (type(e).__name__, e))
        return "error"

    fresh = [h for h in hits if h[0] not in seen]
    if not fresh:
        log("[serv00] 命中 %d 封，都是已推过的" % len(hits))
        return "no-new"

    log("[serv00] 有 %d 封新命中，开始推" % len(fresh))
    for mid, frm, sub, date, uid in fresh:
        body = ("发件人：%s\n主题：%s\n时间：%s\n\n"
                "去邮箱看正文 —— 通过的话里面是账号和初始密码，"
                "没通过的话是拒绝说明。" % (frm, sub, date))
        err = notify("Serv00 有回音了", body)
        log("[serv00] 推「%s」→ %s" % (sub[:40], err))
        seen.add(mid)
        time.sleep(1)

    st["seen"] = sorted(seen)[-400:]
    st["last_push"] = datetime.datetime.now().isoformat(timespec="seconds")
    st["last_subject"] = fresh[-1][2][:120]
    _state_save(st)
    return "pushed:%d" % len(fresh)
