# Legal Workbench

Legal Workbench 是一个面向交易律师个人研究和个人使用的本地法律 AI 工作台。

目标不是做一个单一任务脚本，而是搭建一个可持续扩展的浏览器工作台：律师选择一个能力，上传交易文件，补充必要背景，然后生成可复核、可导出的工作成果。

第一项能力将是 `SPA/SHA KTS`：将融资交易中的增资协议和股东协议总结为关键条款摘要，并最终导出为可继续编辑的 Word 文档。

## Product Direction

- Windows 优先。
- 浏览器工作台优先。
- 能力以 tabs、侧边栏或能力卡片方式逐步扩展。
- `SPA/SHA KTS` 是第一个 capability，不是最终边界。
- 后续可继续加入模板填充、对方修改影响总结、立场化条款审查、定义一致性检查、交割清单生成等能力。
- 律师不需要配置 AI Key、Base URL、模型名或接口类型。
- Markdown / JSON 可以作为中间检查产物，但最终 KTS 交付应为 DOCX。

## First Capability: SPA/SHA KTS

P0 目标：

- 上传或导入增资协议和股东协议。
- 识别文件类型、章节、条款编号和表格。
- 建立 clause inventory。
- 将条款映射到 KTS taxonomy。
- 按已有 KTS 模板的行项、颗粒度和文风生成摘要。
- 标记未见约定、模糊事项、冲突事项和需要律师确认的内容。
- 导出可继续编辑的 DOCX KTS 文档。

第一批验收材料：

- A 套材料：复现“上轮约定”列。
- B 套材料：复现单轮 `事项 / 内容` KTS。

## Development Style

本项目从 README 开始，以小步迭代方式推进。

每一版应尽量保持清晰的单一目标，例如：

- v0.1：README 和产品方向。
- v0.2：工作台空壳和能力导航。
- v0.3：文件上传和 DOCX 解析。
- v0.4：SPA/SHA KTS schema、taxonomy 和中间产物。
- v0.5：KTS 表格视图和来源证据。
- v0.6：DOCX 导出。

每次迭代都应保持可运行或可检查，并按合适频率提交和推送。
