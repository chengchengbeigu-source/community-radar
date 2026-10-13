# -*- coding: utf-8 -*-
"""配置读写 + 连通性探测 + YAML↔JSON 转换 + 敏感字段清洗。

宿主半通过 stdin/stdout JSON 调它，避免在插件里引 YAML 依赖。

命令：
  read     从本地 config.yaml 读
  write    写回本地 config.yaml（先留 .bak）
  probe    探一条 URL 的连通性
  parse    YAML 文本 → JSON（读远端用）
  dump     JSON → YAML 文本（写远端用）
  sanitize 把敏感字段清空（推公开仓库前必过这道闸）
"""
import json, os, re, shutil, sys, time

WS = "/sdcard/Download/DSH工作区"
CONFIG = os.path.join(WS, "radar", "config.yaml")

# 这些字段的真值只走 GitHub Secrets，绝不允许进公开仓库
SECRET_FIELDS = ("access_token", "token", "user_id", "username",
                 "cookie", "password", "bot_token", "chat_id")


def read_config(path=None):
    import yaml
    p = path or CONFIG
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_config(data, path=None):
    import yaml
    p = path or CONFIG
    if os.path.exists(p):
        shutil.copyfile(p, p + ".bak")
    head = ("# 本文件由「配置面板」插件 UI 写入 %s\n"
            "# 每次保存前会自动留一份 config.yaml.bak；手工改也可以，UI 下次读的就是它\n"
            % time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(p, "w", encoding="utf-8") as f:
        f.write(head)
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=1000)
    return p


def parse_yaml(text):
    import yaml
    return yaml.safe_load(text) or {}


def dump_yaml(data):
    import yaml
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=1000)


def sanitize(data):
    """递归清空敏感字段。返回 (洗净的数据, 被清掉的字段路径列表)。"""
    hit = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k in list(o.keys()):
                v = o[k]
                if k in SECRET_FIELDS:
                    if v not in ("", None, [], {}):
                        hit.append(path + "/" + str(k))
                    o[k] = ""
                elif k == "cookie_file":
                    continue          # 这是路径不是密钥，留着
                else:
                    walk(v, path + "/" + str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + "/" + str(i))

    walk(data)
    return data, hit


def probe(url, use_proxy=None):
    sys.path.insert(0, WS)
    from radar.util import http_get, get_proxy
    cfg = read_config()
    prx = get_proxy(cfg) if use_proxy is None else (use_proxy or None)
    t0 = time.time()
    out = {"url": url, "proxy": prx, "ms": None, "ok": False}
    try:
        body = http_get(url, proxy=prx, timeout=25)
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
        out.update(ok=True, ms=int((time.time() - t0) * 1000),
                   bytes=len(body.encode("utf-8")),
                   title=(m.group(1).strip()[:90] if m else ""),
                   head=body[:180].replace("\n", " "))
    except Exception as e:
        out.update(ms=int((time.time() - t0) * 1000),
                   error=f"{type(e).__name__}: {e}"[:200])
    return out


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "read"
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        arg = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        print(json.dumps({"ok": False, "error": "bad json: %s" % e}, ensure_ascii=False))
        return

    if cmd == "read":
        print(json.dumps({"ok": True, "path": CONFIG, "config": read_config()},
                         ensure_ascii=False))
    elif cmd == "write":
        p = write_config(arg.get("config") or {})
        print(json.dumps({"ok": True, "path": p}, ensure_ascii=False))
    elif cmd == "probe":
        print(json.dumps(probe(arg.get("url") or "", arg.get("proxy")), ensure_ascii=False))
    elif cmd == "parse":
        try:
            print(json.dumps({"ok": True, "config": parse_yaml(arg.get("text") or "")},
                             ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                             ensure_ascii=False))
    elif cmd == "dump":
        try:
            print(json.dumps({"ok": True, "text": dump_yaml(arg.get("config") or {})},
                             ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                             ensure_ascii=False))
    elif cmd == "sanitize":
        cfg, hit = sanitize(arg.get("config") or {})
        print(json.dumps({"ok": True, "config": cfg, "removed": hit}, ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "error": "unknown cmd " + cmd}, ensure_ascii=False))


if __name__ == "__main__":
    main()
