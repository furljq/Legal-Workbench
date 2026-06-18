# Legal Workbench 快速开始

## 1. 配置模型

复制配置示例：

```powershell
Copy-Item app\ai_config.example.py app\ai_config.py
```

打开 `app\ai_config.py`，填写 API Key、模型名和 Base URL。

## 2. 启动工作台

Windows 下双击：

```text
start_windows.bat
```

脚本会自动安装依赖、启动本地服务并打开浏览器。

默认地址：

```text
http://127.0.0.1:8787/
```

## 3. 生成 KTS

1. 选择 `SPA/SHA KTS` 能力。
2. 上传增资协议（SPA）。
3. 上传股东协议（SHA）。
4. 点击“生成 KTS”。
5. 等待系统完成处理。
6. 逐项复核内容摘要。
7. 确认全部事项后导出 Word。

## 4. 继续上次复核

如果页面刷新或浏览器关闭，重新打开工作台后点击：

```text
继续上次复核
```

系统会恢复最近一次 KTS 中间结果。

## 5. 使用提醒

- AI 输出是 KTS 初稿，不是最终法律意见。
- 导出 Word 前请逐项复核。
- 交易文件内容会发送给你配置的模型服务。
- `app\ai_config.py` 不要提交到 GitHub。
