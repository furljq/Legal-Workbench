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
MAX_MODEL_WORKERS = 8
```

`MAX_MODEL_WORKERS` 控制模型并发调用数，默认建议为 8，当前代码允许最高 16；如遇到 429 限流、请求超时或模型返回不稳定，可先降到 6 或 4。
处理过程中如遇到限流、超时、连接中断或 5xx/429 等瞬时错误，后台会自动降低并发并只重试失败事项；鉴权或参数配置错误不会自动重试。

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

- 分别上传增资协议（SPA）和股东协议（SHA）。
- 按上传槽位确定文件类型，并读取正文段落和表格。
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

当前为 v0.6 开发阶段。v0.5 已完成 KTS 中间表逐项复核、可编辑保存和继续复核；v0.6 的重点是将人工确认后的 KTS 结果导出为可继续编辑的 DOCX 文档。

目前已经具备：

- 本地 Python 服务。
- 静态浏览器工作台。
- 能力导航。
- 工作台连通性检查。
- 增资协议（SPA）和股东协议（SHA）双文件槽上传。
- DOCX 正文段落和表格解析。
- 上传槽位强制区分 SPA/SHA 文件类型，自动识别仅作为内部参考。
- 最新解析结果保存为可覆盖的 debug 快照。
- 最新原文证据索引保存为可覆盖的 debug 快照。
- KTS 候选证据保存为可覆盖的 debug 快照。
- 模型语义复核和补充扫描。
- KTS 抽取中间产物，包含结构化事实和内容摘要。
- 主界面展示模型可用状态。
- 主界面展示处理阶段和 KTS 事项完成数。
- 主界面以单事项翻页方式展示 KTS 中间产物，包括事项、内容摘要、来源 quote、复核要点和系统可信度。
- KTS 逐项复核支持编辑内容摘要、填写备注、确认事项，或在合同不涉及当前事项时一键清空内容并确认。
- 刷新页面后可通过“继续上次复核”恢复当前 KTS 复核工作流。
- 当前 KTS 复核结果可导出为可继续编辑的 DOCX 关键条款摘要。
- 高可信事项可显示 `AI 初核通过` 作为系统初始判断，但律师操作只围绕修改内容后确认、或清空不涉及事项展开。
- 单事项页按需展开展示来源证据，并展示当前事项已保留的全部来源证据单元。
- 来源证据按原文 block span 归并为证据单元，避免把同一原文片段的滑动窗口平移重复展示。
- 文档处理过程暴露内存进度接口：`/api/runs/{run_id}/progress`。
- 模型复核和抽取使用有限并发，可通过本地 `MAX_MODEL_WORKERS` 调整。
- Windows 启动脚本。

尚未完成：

- DOCX 导出版式和模板文风的进一步校准。
- 更完整的证据采纳历史和多人协作工作流。
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

本项目按小步迭代推进。当前分支中的开发重点是推进 v0.6：

- 将当前 KTS 复核结果导出为可继续编辑的 DOCX 文档。
- 导出时优先使用律师已确认或已修改的内容摘要。
- 明确区分 SPA/SHA 输入文件，避免 KTS 两部分候选证据混杂。
- 继续按既有 KTS 模板校准导出版式和文风。

后续版本方向：

- v0.6：DOCX 导出与导出版式校准。
- v0.7：导出质量控制、模板文风继续校准和更多法律工作台能力。
- 后续：更多法律工作台能力。
