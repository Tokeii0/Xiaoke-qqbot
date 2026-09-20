# 开源提交检查

发布的是源码、测试、文档和配置模板。正在运行的机器人目录还包含账号状态和聊天数据，不要将整个目录直接压缩上传。

## 可以提交

| 路径 | 条件 / 用途 |
| --- | --- |
| `xiaoke_bot/` | Python 源码与后台 HTML / JS / CSS；不含缓存 |
| `tests/` | 虚构账号、模拟接口与临时数据库测试 |
| `bot.py`、`run.py`、`start.ps1` | 启动入口，地址和密钥从本地配置读取 |
| `pyproject.toml`、`MANIFEST.in` | Python 依赖、元数据与资源打包规则 |
| `package.json`、`package-lock.json` | Node 依赖清单与锁定信息，不包含依赖安装目录 |
| `.env.example`、`tdai-gateway.example.json` | 已脱敏模板，密钥为空或使用环境变量占位符 |
| `.gitignore`、`.gitattributes`、`.github/workflows/ci.yml` | 忽略规则、文本格式与 CI |
| `README.md`、`docs/`、`CONTRIBUTING.md`、`SECURITY.md`、`LICENSE` | 部署、功能、贡献与许可说明 |

源码默认超管、允许群和私人入群接口均为空。测试只使用虚构标识；不要为了方便调试又填回自己的实际号码或服务地址。

## 不提交

| 路径 / 内容 | 原因 |
| --- | --- |
| `.env`、`.env.prod`、其他 `.env.*` | 运行环境、账号、令牌、Key 与私人提示词 |
| `tdai-gateway.json` / `.yaml` / `.yml` | 本地网关配置可能包含内网地址和数据库凭证；公开模板即可 |
| 整个 `data/` | 配置、密钥、QQ / 群号、聊天、记忆、提醒、起床理由、照片、语音测试、模型请求、日志及备份 |
| `NapCat.Shell.Windows.Node/` 等 NapCat 安装目录 | 第三方程序及可能的账号配置、登录状态 |
| NapCat ZIP 或其他本地安装包 | 第三方分发物，体积较大；应让用户从上游获取 |
| `.venv/`、`node_modules/` | 平台相关的依赖安装结果，由清单重新安装 |
| `__pycache__/`、`*.egg-info/`、`build/`、`dist/` | 缓存、构建输出和本地元数据 |
| `.claude/`、`.codex/`、编辑器本地状态 | 开发工具状态及附带的外部技能，不是机器人运行所需代码 |
| 真实聊天截图、后台截图、请求导出、手工备份 | 可能在文字、图片或元数据中带出个人信息；需要单独审查和脱敏 |

`.gitignore` 只是一层防护：它不会移除已跟踪文件、清理 Git 历史，也不能阻止 `git add -f`。配置了自定义数据路径时，同步补充忽略规则。

## 发布前怎么核对

如果目录尚未使用 Git，可在准备发布的源码目录执行 `git init`；已有仓库跳过。分组选择要提交的文件：

```bash
git add -- xiaoke_bot tests docs .github
git add -- bot.py run.py start.ps1 pyproject.toml MANIFEST.in package.json package-lock.json
git add -- .env.example tdai-gateway.example.json .gitignore .gitattributes
git add -- README.md LICENSE CONTRIBUTING.md SECURITY.md
git status --short
git diff --cached --stat
git diff --cached --check
```

在本机检查暂存的配置模板、源码常量、测试和文档。搜索 Key、Token、私钥标记、实际 QQ / 群号、邮箱、私人域名及用户目录；查找结果应只包含空值、变量名、虚构测试值或经确认可公开的内容。长数字也可能是时间戳和限制参数，需要人工区分。

验证忽略规则，例如：

```bash
git check-ignore -v .env .env.prod tdai-gateway.json data/bot_secrets.json
git check-ignore -v NapCat.Shell.Windows.Node.zip node_modules .venv
git ls-files -- .env .env.prod tdai-gateway.json data node_modules .venv
```

前两条应显示忽略规则，最后一条不应列出私有文件。如果私有文件已经在暂存区或历史中，先停止发布；确认范围后移出索引，并根据实际泄漏情况轮换凭证及处理历史。不要删除正在运行的配置来“脱敏”。

运行自动测试并在一份干净源码副本中安装：

```bash
python -m unittest discover -s tests
node --check xiaoke_bot/web/app.js
python -m pip install build
python -m build
```

确认 wheel 包含 `xiaoke_bot/web/index.html`、`app.js` 和 `styles.css`；源码包包含配置模板和启动脚本。检查压缩包成员列表，确保没有 `.env`、本地网关配置、运行数据、账号目录或私钥。真实服务联调应另用自己的测试账号完成，不能用单元测试通过来承诺所有模型接口都兼容。

发布 README 截图时优先在空数据的演示实例中使用虚构资料。最终审查覆盖暂存区、拟发布压缩包、文档附件；已有仓库还要检查历史。

## 依赖与授权

本项目代码采用 [MIT](../LICENSE)。下面列出直接依赖的许可证概况；安装时仍应以对应版本的许可证文件为准，二进制整包或容器分发还需审查实际打入的所有传递依赖。

| 依赖 | 许可证 / 说明 |
| --- | --- |
| NoneBot2、OneBot V11 Adapter | MIT |
| HTTPX、python-dotenv、websockets | BSD-3-Clause |
| typesafe-sdk | MIT；JEV 在线服务另有使用条款 |
| tzdata | Apache-2.0；时区数据及附带说明见上游分发 |
| TencentDB Agent Memory、tsx | MIT |
| setuptools | MIT，构建时依赖 |
| QQ / NapCat、模型、搜索、云数据库 | 分别遵循上游软件许可或服务条款；不随本源码仓库分发 |

参考项目：[NoneBot2](https://github.com/nonebot/nonebot2)、[OneBot Adapter](https://github.com/nonebot/adapter-onebot)、[TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)、[NapCat](https://github.com/NapNeko/NapCatQQ)。

MIT 许可不授权公开他人的聊天资料、商业账号数据或第三方素材。新增图片、字体、图标或复制的代码时，分别确认授权并保留所需版权声明。
