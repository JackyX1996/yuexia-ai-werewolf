# 参与贡献

谢谢你愿意改进月下 · AI 狼人杀。

## 开始之前

1. 使用 Python 3.11 创建虚拟环境，并安装 `requirements.txt`。
2. 需要改初始管理员时，复制 `.env.example` 为 `.env`。不要提交 `.env`。
3. 模型密钥只在本地页面或数据库里配置，不要写进代码、测试、Issue 或截图。

## 开发约定

- 游戏规则、信息可见性和模型决策的行为变化，需要带上对应的回归测试。
- 测试不连接真实模型，也不读取本机 `data/werewolf.db`。
- 保持 Python 3.9 可解析的类型写法：使用 `Optional`、`List`、`Dict`，或 3.9 已支持的 `list[...]` / `dict[...]`。不要使用 `X | Y`。
- 前端是原生 HTML、CSS 和 JavaScript，不引入构建步骤，除非改动确实需要。
- 不要把 API Key、登录 token、密码、个人昵称或原始数据库放进提交。

## 提交前检查

```bash
python -m pytest tests/ -q
node --check static/app.js
node --check static/game-availability.js
node --check static/model-settings.js
```

## 报告问题

说明运行方式、Python 版本、模型配置模式、出错阶段和复现步骤。附件里删掉密钥、token、个人昵称和数据库文件。安全漏洞请按 [SECURITY.md](SECURITY.md) 处理，不要先开公开 Issue。
