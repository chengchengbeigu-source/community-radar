# -*- coding: utf-8 -*-
"""GitHub Actions 上线时把 Secrets 灌进运行时文件；仓库里永远不放明文。"""
import os, yaml

def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sec = os.path.join(root, "radar", "secrets")
    os.makedirs(sec, exist_ok=True)
    try:
        os.chmod(sec, 0o700)
    except Exception:
        pass
    ck = (os.environ.get("NODELOC_COOKIE") or "").strip()
    if ck:
        with open(os.path.join(sec, "nodeloc.cookie"), "w", encoding="utf-8") as f:
            f.write(ck)
        print("[secrets] nodeloc.cookie 写入完成（%d 字节）" % len(ck))
    p = os.path.join(root, "radar", "config.yaml")
    d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    for e in d.get("checkins") or []:
        k = (e.get("kind") or "").lower()
        if k in ("newapi", "new-api", "new_api"):
            if os.environ.get("XSG_TOKEN"):
                e["access_token"] = os.environ["XSG_TOKEN"].strip()
            if os.environ.get("XSG_UID"):
                e["user_id"] = str(os.environ["XSG_UID"]).strip()
        if k in ("discourse", "nodeloc", "forum"):
            # 手动触发时可用 workflow_dispatch 的 read_minutes 覆盖当日阅读上限，
            # 便于低成本实测（例如只读 5 分钟验证链路）。
            _rm = (os.environ.get("RADAR_READ_MINUTES") or "").strip()
            if _rm:
                try:
                    e.setdefault("daily", {})["read_minutes"] = float(_rm)
                except ValueError:
                    pass
            if os.environ.get("NODELOC_USER"):
                e["username"] = os.environ["NODELOC_USER"].strip()
            if os.environ.get("NODELOC_COOKIE"):
                e["cookie_file"] = "radar/secrets/nodeloc.cookie"
            if os.environ.get("DISCORD_READ", "1") == "0":
                e.setdefault("daily", {})["read"] = False
    yaml.safe_dump(d, open(p, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)
    print("[secrets] config.yaml 覆盖完成")

if __name__ == "__main__":
    main()
