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
    config_changed = sync_config(render_block(merged))

    if prev_ids == cur_ids and not config_changed:
        return  # 无变化 → 静默

    added = sorted(set(cur_ids) - set(prev_ids))
    removed = sorted(set(prev_ids) - set(cur_ids))
    print(f"[free-models] 免费名单更新 ({len(merged)} 款; OpenRouter {len(or_free)} + 静态 {len(seed)})")
    for a in added:
        print(f"  + {a}")
    for r in removed:
        print(f"  - {r}")
    if config_changed:
        print("  config.yaml model_aliases 块已同步 (备份: config.yaml.bak-free; 需重启会话/面板生效)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[free-models] 失败: {e}", file=sys.stderr)
        sys.exit(1)
