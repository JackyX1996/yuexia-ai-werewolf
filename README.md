# 月下 · AI 狼人杀

12 人文字狼人杀，预女猎白。可以自己入座，和 11 个 AI 一起打；也可以不入座，看 12 个 AI 打完一整局。

每位 AI 有自己的身份、性格和只属于自己的记忆。模型在页面里配置，一局游戏可以从模型池里随机分配。

![登录界面：月下村庄](docs/screenshots/login.jpg)

![白天讨论](docs/screenshots/gameplay.jpg)

![对局复盘](docs/screenshots/review.jpg)

## 两种开局

大厅可以切换：

- **入座**：你坐一个随机座位，身份只有自己知道。夜间技能、发言和投票都由你操作。
- **上帝模式**：12 个座位全是 AI。你能看到全部身份、狼队讨论、查验、用药和各座位的内心，不发言也不投票。

两种模式都至少要有一个可用模型。座位多于可用模型时，模型会重复分配。

## 开始

需要 Python 3.11。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。新数据库的初始账号是 `admin` / `admin`。要改的话，复制 `.env.example` 为 `.env`，设置 `WW_ADMIN_USERNAME` 和 `WW_ADMIN_PASSWORD`。这两项只在第一次建账号时生效。不要用默认口令把服务开到公网。

管理员登录后打开「模型配置」，填写模型名和 API Key，需要时再填 Base URL，测试连通后加入对局池。回到大厅选择开局方式。

| 模式 | 必填 | 地址 |
| --- | --- | --- |
| 百炼 | 模型名、API Key | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 火山方舟 | 模型名、API Key | `https://ark.cn-beijing.volces.com/api/v3` |
| 自定义 | 模型名、服务地址、API Key、接口格式 | Chat Completions、Responses 或 Anthropic Messages |

同一平台下每行一个模型名，共用地址和密钥。Thinking 固定关闭；关不掉思考的模型不会进池。密钥存在服务端数据库，页面只显示脱敏摘要，数据库本身不加密。

## 规则

| 项目 | 规则 |
| --- | --- |
| 阵容 | 4 狼人、4 平民、预言家、女巫、猎人、白痴 |
| 胜负 | 屠边。狼全灭则好人胜；神职或平民全灭则狼人胜 |
| 首日 | 第一夜 → 竞选警长 → 公布死亡 → 遗言与技能 → 发言 → 放逐 |
| 警长 | 白天 1.5 票，决定发言方向并最后发言；死亡可移交或撕徽 |
| 平票 | 平票玩家 PK 后再投；仍平则无人出局，或本轮没有警长 |
| 女巫 | 解药、毒药各一次，每晚最多一瓶；只有首夜可以自救 |
| 猎人 | 被毒不能开枪，其余死亡可以开枪 |
| 白痴 | 第一次被放逐时翻牌免死，之后不能投票 |
| 遗言 | 首夜死亡和白天放逐有遗言 |

夜间只公布死亡座位，不公布死因。狼人可以自刀，发言或竞选时可以自爆。投票可以弃票，不能投自己。

## 预览和 Docker

不配置模型时，可以用模拟服务看界面：

```bash
python -m uvicorn scripts.ui_preview:app --host 127.0.0.1 --port 8787
```

模拟发言不代表真实模型。预览默认使用临时目录里的 `ww-ui-preview.db`；如果环境里已经有 `WW_DB_URL`，请让它指向单独的库。

```bash
docker build -f docker/Dockerfile -t yuexia-ai-werewolf .
docker volume create yuexia-ai-werewolf-data
docker run --name yuexia-ai-werewolf \
  --publish 127.0.0.1:8001:8000 \
  --mount type=volume,source=yuexia-ai-werewolf-data,target=/app/data \
  yuexia-ai-werewolf
```

镜像基于 Python 3.9。`docker/docker-compose.yml` 把端口绑在 `127.0.0.1:8001`，`.env` 可选，默认不挂数据卷。

## 配置

模型在页面里管理。其余参数用 `WW_` 环境变量，见 [config.py](config.py)。

| 变量 | 默认 | 用途 |
| --- | --- | --- |
| `WW_ADMIN_USERNAME` / `WW_ADMIN_PASSWORD` | `admin` | 新库的管理员账号 |
| `WW_DB_URL` | `data/werewolf.db` | 异步数据库地址 |
| `WW_LLM_TIMEOUT_SECONDS` | `45` | 单次读取超时 |
| `WW_LLM_DECISION_TIMEOUT_SECONDS` | `120` | 一次决策含重试的总时限 |
| `WW_LLM_AUDIT_PATH` | `data/ai-decisions.jsonl` | 诊断日志，留空则关闭 |
| `WW_HUMAN_ACTION_TIMEOUT_SECONDS` | `2400` | 入座模式等待真人的上限 |

`data/` 里有密钥、密码哈希、会话和对局。不要把数据库、`.env` 或诊断日志提交到仓库。

## 限制

- 入座模式只有一位真人。上帝模式真人不入座。没有多真人同桌，也没有语音。
- 一局游戏跑在单个进程里，只用一个 uvicorn worker。
- 重连可以恢复当前画面。进程丢了之后，只有完整白天结束、没有待处理技能时能续局。
- 账号和会话按本地使用设计，不适合直接暴露到公网。

## 开发

```bash
python -m pytest tests/ -q
node --check static/app.js
node --check static/game-availability.js
node --check static/model-settings.js
```

```text
ai/        模型、调用和每个座位的视角
api/       认证、对局和 WebSocket
engine/    规则、阶段和恢复
db/        数据库
static/    页面
tests/     回归测试
```

贡献约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。设计记录见 [游戏审查](docs/gameplay-audit.md) 和 [视觉设计](docs/art-direction-v2.md)。

本项目使用 [MIT 许可证](LICENSE)。安全问题见 [SECURITY.md](SECURITY.md)。
