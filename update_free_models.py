#!/usr/bin/env python3
"""update_free_models.py — 每日免费模型清单同步 (Hermes cron, --no-agent)

流程:
1. 实时抓 OpenRouter /api/v1/models, 过滤出 $0 免费模型 (含 :free 后缀)
2. 与 ~/.hermes/scripts/free_sources.json (静态免费源种子) 合并
3. 写 ~/.hermes/free_models.json (供查询/排行/skill 使用)
4. 把 model_aliases 中 free-* 别名块同步进 ~/.hermes/config.yaml
   (围栏注释块 # >>> free-models (auto) >>> ... <<<, 只动围栏内, 保留其余内容)

输出纪律: 名单无变化 → 静默 (cron --no-agent 空 stdout 不打扰);
名单变化 → 打印变更摘要 (cron 会把 stdout 送达 chat)。
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
HERMES = os.path.join(HOME, ".hermes")
SCRIPTS = os.path.join(HERMES, "scripts")
SEED = os.path.join(SCRIPTS, "free_sources.json")
STATE = os.path.join(HERMES, "free_models.json")
CONFIG = os.path.join(HERMES, "config.yaml")

BEGIN = "# >>> free-models (auto) >>>  本块由 update_free_models.py 管理, 勿手改"
END = "# <<< free-models (auto) <<<"

EXCLUDE_RE = re.compile(r"embed|rerank|whisper|tts|audio|guard|content-safety|decide|lyria|veo|imagen|music", re.I)


def fetch_openrouter_free():
    """OpenRouter 在线免费名单 (公开 API, 无需 key)。"""
    url = "https://openrouter.ai/api/v1/models"
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hermes-free-sync/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
            out = []
            for m in data.get("data", []):
                pricing = m.get("pricing") or {}
                mid = m.get("id", "")
                try:
                    p0 = float(pricing.get("prompt", 1)) == 0.0
                    c0 = float(pricing.get("completion", 1)) == 0.0
                except (TypeError, ValueError):
                    p0 = c0 = False
                if not (p0 and c0 or mid.endswith(":free")):
                    continue
                if EXCLUDE_RE.search(mid):
                    continue
                out.append({
                    "alias": "free-auto-router" if mid == "openrouter/free" else slug_alias(mid),
                    "model": mid,
                    "provider": "openrouter",
                    "source": "openrouter-free",
                    "context": m.get("context_length"),
                    "name": m.get("name"),
                })
            return out
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"OpenRouter 拉取失败(3 次): {last_err}")


def slug_alias(mid):
    base = mid[:-5] if mid.endswith(":free") else mid
    base = base.rsplit("/", 1)[-1]
    s = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "model"
    return f"free-{s}"


def load_seed():
    with open(SEED, encoding="utf-8") as f:
        return json.load(f).get("entries", [])


def render_block(entries):
    lines = ["model_aliases:", f"  {BEGIN}"]
    seen = set()
    for e in entries:
        alias = e["alias"]
        n = 2
        while alias in seen:
            alias = f"{e['alias']}-{n}"
            n += 1
        seen.add(alias)
        lines.append(f"  {alias}:")
        lines.append(f"    model: {e['model']}")
        lines.append(f"    provider: {e['provider']}")
        if e.get("base_url"):
            lines.append(f'    base_url: "{e["base_url"]}"')
        if e.get("key_env"):
            lines.append(f"    key_env: {e['key_env']}")
    lines.append(f"  {END}")
    return "\n".join(lines) + "\n"


def sync_config(block_text):
    """把围栏块合并进 config.yaml; 只在需要时写入; 原子替换+备份。返回是否写入。"""
    with open(CONFIG, encoding="utf-8") as f:
        cfg = f.read()
    if BEGIN in cfg and END in cfg:
        # 匹配含前导缩进与行尾换行; 替换体保留 BEGIN..END 围栏本身 (供下次再替换)
        pat = re.compile(rf"[ \t]*{re.escape(BEGIN)}.*{re.escape(END)}[ \t]*\n?", re.S)
        body = "\n".join(block_text.splitlines()[1:]) + "\n"
        new = pat.sub(lambda _: body, cfg)
        if new == cfg:
            return False
    elif re.search(r"^model_aliases:", cfg, re.M):
        return _write_with_insert(cfg, block_text)
    else:
        sep = "" if cfg.endswith("\n\n") else ("\n" if cfg.endswith("\n") else "\n\n")
        new = cfg + sep + block_text
    if new == cfg:
        return False
    _atomic_write(new)
    return True


def _write_with_insert(cfg, block_text):
    m = re.search(r"^(model_aliases:.*)$", cfg, re.M)
    entry_lines = "\n".join(block_text.splitlines()[2:])  # 去掉 model_aliases: 行
    new = cfg[: m.end()] + "\n" + entry_lines + cfg[m.end():]
    _atomic_write(new)
    return True


def _atomic_write(text):
    bak = CONFIG + ".bak-free"
    if os.path.exists(CONFIG):
        with open(CONFIG, encoding="utf-8") as f:
            prev = f.read()
        with open(bak, "w", encoding="utf-8") as f:
            f.write(prev)
    tmp = CONFIG + ".tmp-free"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, CONFIG)


# ---------------------------------------------------------------------------
# 健康自检 (最简单检查, 不做修复) + 需重启时的智能重启
# ---------------------------------------------------------------------------
DASHBOARD_UNIT = "hermes-dashboard.service"
RESTART_CMD = ["sudo", "-n", "/usr/bin/systemctl", "restart", DASHBOARD_UNIT]
PENDING = os.path.join(SCRIPTS, "restart_pending")
BUSY_DIRS = [os.path.join(HERMES, "logs", "agent.log"),  # agent 会话活动信号 (文件)
             os.path.join(HERMES, "cache", "delegation", "live")]
BUSY_WINDOW = 600          # 10 分钟内有会话活动 → 视为"任务进行中"
WATCHER_INTERVAL = 300     # 后台监视器轮询间隔
WATCHER_MAX_TICKS = 24     # 最长等 2 小时
FREE_PLUGINS = ("free-nim", "free-go", "free-gemini")


def _hermes_install_ws():
    import glob
    hits = sorted(glob.glob(os.path.join(HERMES, "installs", "*", "environments", "*", "workspace")))
    return hits[-1] if hits else None


def _newest_mtime(path, suffix=None):
    newest = 0.0
    if os.path.isfile(path):
        newest = os.path.getmtime(path)
    elif os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for fn in files:
                if suffix and not fn.endswith(suffix):
                    continue
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(root, fn)))
                except OSError:
                    pass
    return newest


def _dashboard_start_time():
    try:
        out = subprocess.run(["systemctl", "show", DASHBOARD_UNIT, "-p", "MainPID", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        pid = int(out)
        if pid > 0:
            return os.stat(f"/proc/{pid}").st_mtime  # /proc/<pid> 目录 mtime ≈ 进程启动时刻
    except Exception:
        pass
    return 0.0


def _is_busy():
    import time
    now = time.time()
    for d in BUSY_DIRS:
        if os.path.isfile(d):
            try:
                if now - os.path.getmtime(d) < BUSY_WINDOW:
                    return True
            except OSError:
                pass
            continue
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                try:
                    if now - os.path.getmtime(os.path.join(root, fn)) < BUSY_WINDOW:
                        return True
                except OSError:
                    pass
    return False


def _smoke_test_plugins(allowed_ids):
    """在全新解释器里验证 free-* 插件目录正确、清单无污染。返回问题列表。"""
    ws = _hermes_install_ws()
    if not ws:
        return ["cannot locate hermes install workspace"]
    code = (
        "import json, os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from hermes_cli.models import provider_model_ids\n"
        f"allowed = set(m['model'] for m in json.load(open({STATE!r}))['models'])\n"
        "bad = []\n"
        f"for p in {FREE_PLUGINS!r}:\n"
        "    ids = provider_model_ids(p)\n"
        "    if not ids: bad.append(p + ': empty')\n"
        "    elif not set(ids) <= allowed: bad.append(p + ': pollution ' + str(sorted(set(ids) - allowed)[:3]))\n"
        "print('OK' if not bad else ';'.join(bad))\n"
    )
    try:
        env = dict(os.environ, PYTHONPATH=ws)
        r = subprocess.run([sys.executable, "-c", code], cwd=ws, env=env,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout or "").strip().splitlines()
        line = out[-1] if out else ""
        if r.returncode == 0 and line == "OK":
            return []
        return [f"plugin smoke: {line or r.stderr.strip()[-200:]}"]
    except Exception as e:
        return [f"plugin smoke error: {e}"]


def _do_restart(reason):
    try:
        r = subprocess.run(RESTART_CMD, capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f"[free-models][health] 已自动重启 {DASHBOARD_UNIT} ({reason})")
            if os.path.exists(PENDING):
                os.remove(PENDING)
            return
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        print(f"[free-models][health] 重启失败 ({reason}): {detail[-1][:150] if detail else r.returncode}")
    except Exception as e:
        print(f"[free-models][health] 重启失败 ({reason}): {e}")
    with open(PENDING, "w", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {reason}\n")


def maybe_restart(reason):
    if _is_busy():
        print(f"[free-models][health] 检测到进行中的任务 ({reason}), 暂不重启;"
              f" 已启动后台监视器, 任务静默 {BUSY_WINDOW // 60} 分钟后自动重启")
        try:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch-restart"],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[free-models][health] 监视器启动失败: {e}; 将在下次 cron 重试")
        return
    _do_restart(reason)


def run_health(changed):
    """返回问题列表; 需要重启时按策略执行。任何问题都会打印并让 cron 记为失败。"""
    problems = []
    # 1. OpenRouter API 可达且返回非空 (fetch 已在 main 完成, 这里看结果)
    # 2. config 围栏完整
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = f.read()
        n_begin, n_end = cfg.count(BEGIN), cfg.count(END)
        if n_begin != 1 or n_end != 1:
            problems.append(f"config fence broken (begin={n_begin}, end={n_end})")
    except Exception as e:
        problems.append(f"config unreadable: {e}")
    # 3. free_models.json 可解析且非空
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
        if not state.get("models"):
            problems.append("free_models.json empty")
    except Exception as e:
        problems.append(f"free_models.json unreadable: {e}")
        state = {"models": []}
    # 4. 插件目录 + 全新解释器冒烟 (捕获 hermes 代码变更 / 插件退化)
    for p in FREE_PLUGINS:
        if not os.path.isdir(os.path.join(HERMES, "plugins", "model-providers", p)):
            problems.append(f"plugin dir missing: {p}")
    allowed = {m["model"] for m in state.get("models", [])}
    problems += _smoke_test_plugins(allowed)
    # 5. 需要重启的更新 (配置/插件/清单比 dashboard 进程新)
    start = _dashboard_start_time()
    watched = [os.path.join(HERMES, "plugins", "model-providers"), STATE, CONFIG, SCRIPTS]
    newest = max(_newest_mtime(w) for w in watched)
    stale = start > 0 and newest > start + 5
    if problems:
        for p in problems:
            print(f"[free-models][health] 问题: {p}")
    if stale:
        maybe_restart(f"artifacts newer than dashboard start ({datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec='seconds')})")
    elif os.path.exists(PENDING):
        maybe_restart("pending restart from previous run")
    elif changed:
        print("[free-models][health] 名单已更新, 新会话/重启后生效 (dashboard 进程比产物新, 无需重启)")
    return problems


def watch_restart():
    import time
    for _ in range(WATCHER_MAX_TICKS):
        time.sleep(WATCHER_INTERVAL)
        if not _is_busy():
            _do_restart("watcher: tasks finished")
            return
    print("[free-models][health] watcher 超时放弃, 留待下次 cron", file=sys.stderr)



def main():
    or_free = fetch_openrouter_free()
    seed = load_seed()
    merged = seed + or_free

    prev = None
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            try:
                prev = json.load(f)
            except json.JSONDecodeError:
                prev = None

    snapshot = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(merged),
        "sources": {
            "openrouter-free": len(or_free),
            "static-seed": len(seed),
        },
        "models": merged,
    }
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    prev_ids = sorted(m["model"] for m in (prev or {}).get("models", []))
    cur_ids = sorted(m["model"] for m in merged)
    changed = prev_ids != cur_ids
    config_changed = sync_config(render_block(merged))
    changed = changed or config_changed

    if changed:
        added = sorted(set(cur_ids) - set(prev_ids))
        removed = sorted(set(prev_ids) - set(cur_ids))
        print(f"[free-models] 免费名单更新 ({len(merged)} 款; OpenRouter {len(or_free)} + 静态 {len(seed)})")
        for a in added:
            print(f"  + {a}")
        for r in removed:
            print(f"  - {r}")
        if config_changed:
            print("  config.yaml model_aliases 块已同步 (备份: config.yaml.bak-free; 需重启会话/面板生效)")

    problems = run_health(changed)
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    if "--watch-restart" in sys.argv[1:]:
        watch_restart()
        sys.exit(0)
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[free-models] 失败: {e}", file=sys.stderr)
        sys.exit(1)
