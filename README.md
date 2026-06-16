# Legal Workbench

Legal Workbench 是一个面向交易律师个人研究和个人使用的本地法律 AI 工作台。

目标不是做一个单一任务脚本，而是搭建一个可持续扩展的浏览器工作台：律师选择一个能力，上传交易文件，补充必要背景，然后生成可复核、可导出的法律工作成果。

第一项能力是 `SPA/SHA KTS`：将融资交易中的增资协议和股东协议总结为关键条款摘要，并最终导出为可继续编辑的 Word 文档。

## 产品方向

- Windows 优先。
- 浏览器工作台优先。
- 能力以侧边栏、标签页或能力卡片方式逐步扩展。
- `SPA/SHA KTS` 是第一个能力，不是最终边界。
- 后续可继续加入模板填充、对方修改影响总结、立场化条款审查、定义一致性检查、交割清单生成等能力。
- Markdown / JSON 可以作为中间检查产物，但最终 KTS 交付应为 DOCX。

## 本地模型配置

GitHub 仓库不会包含真实 API Key，因此拉取仓库后不能直接调用模型。需要在本地创建私有配置文件：

```text
app/ai_config.py
```

可以先复制示例文件：

```powershell
Copy-Item app\ai_config.example.py app\ai_config.py
```

然后在 `app/ai_config.py` 中填写本地模型服务信息：

```python
API_KEY = "填入你的 API Key"
MODEL = "填入模型名"
BASE_URL = "填入兼容 OpenAI 接口的 Base URL"
API_TYPE = "responses"  # 可选：responses 或 chat_completions
TEMPERATURE = 0.1
TIMEOUT_SECONDS = 180
MAX_MODEL_WORKERS = 4
```

`app/ai_config.py` 已被 `.gitignore` 排除，不应提交到 GitHub。

也可以用环境变量覆盖本地配置：

```text
LEGAL_WORKBENCH_API_KEY
LEGAL_WORKBENCH_MODEL
LEGAL_WORKBENCH_BASE_URL
LEGAL_WORKBENCH_API_TYPE
LEGAL_WORKBENCH_TEMPERATURE
LEGAL_WORKBENCH_TIMEOUT_SECONDS
LEGAL_WORKBENCH_MODEL_MAX_WORKERS
```

工作台启动后，可以通过页面顶部的模型状态查看模型是否可用；后端也保留诊断接口：

```text
GET /api/ai/test
```

## 第一项能力：SPA/SHA KTS

P0 目标：

- 上传或导入增资协议和股东协议。
- 识别文件类型、正文段落和表格。
- 建立可追溯的原文块、检索切片和候选证据窗口。
- 按 KTS taxonomy 逐项检索候选证据，并由后台模型进行语义复核和有限补充扫描。
- 将候选证据绑定到可验真的原文 quote。
- 先生成单轮 KTS（事项 / 内容），避免过早引入多轮对比复杂度。
- 按已有 KTS 模板的行项、颗粒度和文风生成摘要。
- 标记未见约定、模糊事项、冲突事项和需要律师确认的内容。
- 最终导出可继续编辑的 DOCX KTS 文档。

第一批验收材料：

- A 套材料：复现“上轮约定”列，用于验证同一抽取能力；不在 P0 界面上暴露多轮模板选择。
- B 套材料：复现单轮 `事项 / 内容` KTS。

## 当前版本

当前为 v0.4 开发阶段，已经具备：

- 本地 Python 服务。
- 静态浏览器工作台。
- 能力导航。
- 工作台连通性检查。
- 多文件 Word 上传。
- DOCX 正文段落和表格解析。
- 粗略文件类型识别。
- 最新解析结果保存为可覆盖的 debug 快照。
- 最新原文证据索引保存为可覆盖的 debug 快照。
- KTS 候选证据保存为可覆盖的 debug 快照。
- 模型语义复核和补充扫描。
- KTS 抽取中间产物，包含结构化事实和内容摘要。
- 主界面展示模型可用状态。
- 主界面展示处理阶段和 KTS 事项完成数。
- 主界面展示 KTS 中间表，包括事项、状态、内容摘要、证据数量、来源 quote 和复核要点。
- 文档处理过程暴露内存进度接口：`/api/runs/{run_id}/progress`。
- 模型复核和抽取使用有限并发，可通过本地 `MAX_MODEL_WORKERS` 调整。
- Windows 启动脚本。

尚未完成：

- 最终 KTS DOCX 导出。
- 来源证据的人工确认和采纳工作流。
- 与模板文风的进一步校准。
- 对未见约定、冲突事项、需确认事项的更细颗粒度质量控制。

## 本地运行

Windows 下可以双击：

```text
start_windows.bat
```

也可以在终端中运行：

```powershell
python -m pip install -r requirements.txt
python app/server.py --host 127.0.0.1 --port 8787
```

然后访问：

```text
http://127.0.0.1:8787/
```

如果该端口已被占用，可以换一个端口，例如：

```powershell
python app/server.py --host 127.0.0.1 --port 8788
```

## 调试文件

工作台会在 `debug/` 下覆盖保存当前最新结果：

```text
debug/current_parse.json
debug/current_source_index.json
debug/current_kts_candidates.json
debug/current_kts_extraction.json
```

这些文件用于本地调试，不会提交到 GitHub。

## 开发节奏

本项目按小步迭代推进。当前分支中的开发重点是收口 v0.4：

- 提升 KTS 抽取事实的稳定性。
- 改善主界面 KTS 中间表。
- 继续校准模板文风。
- 为后续 DOCX 导出做结构准备。

后续版本方向：

- v0.5：KTS 表格视图、证据复核和人工确认体验。
- v0.6：DOCX 导出。
- 后续：更多法律工作台能力。
