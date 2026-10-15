# hermes-free-model-pools

为 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 提供的免费模型池插件与每日同步脚本。

在模型 picker 里为每个免费源生成**独立分组**（Free · NVIDIA NIM / Free · OpenCode Go / Free · Gemini API），组内**只含免费模型**——原理是 model-provider 插件的 `supports_model_listing=False` + `fallback_models`（curated fallback 即整个目录），live 全量目录（含付费款）不会混入。

## 组成

| 组件 | 作用 |
|---|---|
| `plugins/free-nim` | NVIDIA NIM 免费端点（DeepSeek V4 Pro/Flash、Kimi K3、MiniMax M3、Nemotron 3 Ultra，40 RPM），复用 `NVIDIA_API_KEY` |
| `plugins/free-go` | OpenCode Go 订阅内免费无限档（LongCat 2.5 Preview、Step 5 Preview、Cat Jam），复用 `OPENCODE_GO_API_KEY` |
| `plugins/free-gemini` | Gemini API 免费档（Gemini 3.5/3.8 Flash、Gemma 4），EEA 免费档数据不用于训练，复用 `GEMINI_API_KEY` |
| `update_free_models.py` | 每日拉取 OpenRouter `/api/v1/models` 免费模型 + 静态种子合并，写 `~/.hermes/free_models.json`；插件在进程启动时读取该文件动态构建目录，同步后无需改插件代码 |

## 安装

```bash
# 1. 生成首次免费清单（写 ~/.hermes/free_models.json + config.yaml 免费别名块）
python3 update_free_models.py

# 2. 安装插件
mkdir -p ~/.hermes/plugins/model-providers
cp -R plugins/* ~/.hermes/plugins/model-providers/

# 3. 设置对应 key（只设你有的源；未配 key 的分组不会出现在 picker）
#   NVIDIA_API_KEY   https://build.nvidia.com
#   OPENCODE_GO_API_KEY（已有 Go 订阅则已配好）
#   GEMINI_API_KEY   https://aistudio.google.com/apikey

# 4.（可选）每日 02:10 自动同步（欧洲中部时间，OpenRouter 配额 UTC 零点重置后 10 分钟）
hermes cron create "10 2 * * *" --name free-models-sync \
  --script /path/to/update_free_models.py --no-agent
```

重启/新会话后，picker 中出现 `Free · …` 分组。

## 兼容性

- 与 `hermes-openrouter-free-rotator`（OpenRouter :free 自动轮换默认模型）和 `hermes-clinepass`（Cline 免费 block）**不冲突**：本插件只管 NIM/Go/Gemini 三个源，OpenRouter 免费模型交给 rotator。
- 不修改 Hermes 任何核心源码，纯插件模式，升级安全。

## 注意

- 免费名单会轮换（OpenRouter 月级轮换、Go 实时目录随时变化）；插件组显示的是**最近一次同步的快照**，轮换掉的 id 可能调用报错，删掉 `free_models.json` 对应条目或等下次同步即可。
- 免费源永远只建议当副档使用；无敏感内容才发给"数据可能用于训练"的源。
