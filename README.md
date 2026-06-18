# Legal Workbench

Legal Workbench 是一个本地运行的法律 AI 工作台，用于把融资交易文件整理成律师可复核、可继续编辑的工作成果。

当前版本的第一项能力是 `SPA/SHA KTS`：上传增资协议和股东协议后，工作台会提炼关键条款，生成逐项可复核的 KTS 中间表，并导出 Word 版《交易文件主要条款摘要》。

本工具定位为律师工作辅助系统，不替代律师判断。系统输出应作为初稿或核对材料，由律师复核、修改并确认后再使用。

## 适用场景

- 融资交易文件初步梳理。
- 增资协议、股东协议关键条款摘要起草。
- 按事项复核 KTS 内容是否完整、清楚、可向客户或投资人汇报。
- 将 AI 生成的中间结果导出为可继续编辑的 Word 文档。

当前最适合处理一套增资协议加一套股东协议的单轮融资文件。复杂多轮交易、对方修订稿影响分析、模板自动填充等能力尚未作为稳定入口开放。

## 使用流程

1. 配置本地模型服务。
2. 启动工作台并打开浏览器页面。
3. 在 `SPA/SHA KTS` 能力中分别上传增资协议和股东协议。
4. 等待系统完成文件解析、证据索引、模型复核、摘要生成和文风润色。
5. 在工作台中逐项复核 KTS 摘要。
6. 对需要调整的事项直接编辑内容摘要。
7. 对合同不涉及的事项点击“不涉及，清空内容”。
8. 确认全部事项后导出 Word 版 KTS。

页面刷新后，可以通过“继续上次复核”恢复当前最新的复核工作。

## 本地模型配置

仓库不会包含真实 API Key。首次使用前，需要在本地创建私有配置文件：

```text
app/ai_config.py
```

可以复制示例文件：

```powershell
Copy-Item app\ai_config.example.py app\ai_config.py
```

然后填写模型服务信息：

```python
API_KEY = "填入你的 API Key"
MODEL = "填入模型名"
BASE_URL = "填入兼容 OpenAI 接口的 Base URL"
API_TYPE = "responses"  # 可选：responses 或 chat_completions
TEMPERATURE = 0.1
TIMEOUT_SECONDS = 180
MAX_MODEL_WORKERS = 8
```

`MAX_MODEL_WORKERS` 控制模型并发调用数，建议先使用 8。当前代码允许最高 16；如遇到限流、超时或模型返回不稳定，可以降到 6 或 4。

也可以使用环境变量覆盖本地配置：

```text
LEGAL_WORKBENCH_API_KEY
LEGAL_WORKBENCH_MODEL
LEGAL_WORKBENCH_BASE_URL
LEGAL_WORKBENCH_API_TYPE
LEGAL_WORKBENCH_TEMPERATURE
LEGAL_WORKBENCH_TIMEOUT_SECONDS
LEGAL_WORKBENCH_MODEL_MAX_WORKERS
```

`app/ai_config.py` 已被 `.gitignore` 排除，不应提交到 GitHub。

## Windows 启动

双击：

```text
start_windows.bat
```

脚本会检查 Python、创建本地虚拟环境、安装依赖、启动服务并打开浏览器。

默认访问地址：

```text
http://127.0.0.1:8787/
```

如需手动启动：

```powershell
python -m pip install -r requirements.txt
python app/server.py --host 127.0.0.1 --port 8787 --open-browser
```

如果端口被占用，可以换一个端口：

```powershell
python app/server.py --host 127.0.0.1 --port 8788 --open-browser
```

## 工作台能力

当前 `SPA/SHA KTS` 能力包括：

- 双文件槽上传：增资协议（SPA）和股东协议（SHA）分开上传。
- DOCX 正文和表格解析。
- 定位相关条款和来源线索。
- AI 复核来源线索并补充可能遗漏的相关内容。
- 按 KTS 事项逐字段抽取事实。
- 生成内容摘要并进行最后文风润色。
- 标记缺失、模糊或需要律师确认的事项。
- 展示系统可信度、需关注字段、条款定位和来源证据线索。
- 单事项翻页复核。
- 导出可继续编辑的 DOCX KTS。

当前 KTS 分为 SPA 和 SHA 两部分。“其他”事项也按 SPA/SHA 分开处理，避免增资协议杂项条款和股东协议剩余投资人权利混在一起。

## 复核方式

工作台不会要求律师直接阅读 JSON。KTS 中间结果会被解析为单事项页面：

- `内容摘要`：最终会进入导出 Word 的正文内容。
- `系统可信度`：系统对当前摘要可靠性的初步判断。
- `需关注字段`：缺失、模糊或风险较高的字段。
- `条款定位`：系统识别到的条款编号或标题。
- `来源证据`：按需展开，用于辅助定位原文。

律师确认时有两种主要操作：

- 直接修改内容摘要，然后点击确认。
- 当前合同不涉及该事项时，清空内容并确认。

## 导出结果

导出的 Word 文件为《交易文件主要条款摘要》，包含：

- SPA 事项。
- SHA 事项。
- 每个事项的摘要内容。
- 可读性优化后的分段和编号。
- 必要的信息来源提示。

导出文件用于继续编辑和人工复核，不代表系统已经完成最终法律判断。

## 本地排查文件

工作台会在本地 `debug/` 目录覆盖保存当前最新结果：

```text
debug/current_parse.json
debug/current_source_index.json
debug/current_kts_candidates.json
debug/current_kts_extraction.json
```

这些文件用于排查和复现问题，不会提交到 GitHub。每次重新处理文件时，当前快照会被覆盖。

需要注意：交易文件内容会被发送给已配置的模型服务用于语义复核、事实抽取和摘要润色。请仅在你认可的模型服务和数据处理边界内使用。

## 常见问题

### 页面显示模型需检查

检查 `app/ai_config.py` 是否存在，API Key、Base URL、模型名和 `API_TYPE` 是否正确。也可以访问：

```text
GET /api/ai/test
```

### 处理很慢

可以适当调高 `MAX_MODEL_WORKERS`，但过高可能触发限流或请求失败。遇到 429、5xx、超时或连接中断时，后台会降低并发并重试失败事项。

### 刷新后结果不见了

点击“继续上次复核”。工作台会从当前本地快照恢复最近一次 KTS 复核工作。

### 导出前是否必须全部确认

建议全部确认后再导出。未确认事项也可能出现在导出结果中，但它们仍应由律师复核。

## 当前边界

- 目前只提供 SPA/SHA KTS 这一项稳定入口。
- 当前版本不做多轮融资差异对比。
- 当前版本不自动分析对方修订稿影响。
- 来源定位仍以条款定位、检索线索和证据片段为主，尚未实现点击直达 Word 原文具体位置。
- 系统会尽力避免新增事实，但 AI 生成内容仍必须人工核对。
