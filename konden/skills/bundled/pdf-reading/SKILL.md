---
name: pdf-reading
description: 阅读 PDF 的文字、双栏、图表、扫描页和小字，按需查看整页与局部。
---

# PDF 阅读

加载 `mcp:builtin-documents`，使用 AnyMD 的原生工具。文件沿用消息中给出的材料路径，不需要转换或修改原件。参数以加载后的工具契约为准。

按任务选择文字和图片：

- `outline` 查看目录；`read` 按页提取已有文字，显式传入 `ocr: false`；`search` 定位具体内容。长结果按返回的 cursor 继续读取，不把截断结果当成全文。
- 图片、图表、双栏、公式和排版关系需要实际查看页图。使用 `inspect` 的 `render_page`，明确选择页码；它返回页面信息和真实图片，不只是图片文件路径。渲染和裁剪本身不运行 OCR，无需传入 OCR 参数。
- 扫描页即使没有可提取文字，页面仍有内容，直接渲染交给当前模型理解。不因读不到文字就安装 OCR 或下载模型。
- 双栏的阅读顺序由你结合整页判断。提取文字可能交错、遗漏或把并列正文当成表格，应以原页核对。
- 小字和细节看不清时，再用 `extract_regions` 按区域放大。先保留整页上下文，避免遗漏跨栏标题、图表、脚注和跨页延续。

例如查看第 1–2 页：

```json
{"operation":"render_page","sources":[{"path":"/absolute/path/document.pdf","pages":"1-2"}],"scale":2}
```

局部坐标使用 PDF 点（1 点 = 1/72 英寸），原点在页面左下角。可先用 `inspect` 的 `inspect` 操作查看页面几何信息。`render_page` 返回的 width/height 是图片像素，不能直接当成 PDF 点：例如未旋转的页面以 scale=2 渲染为 1000×1400 像素，对应 500×700 点。根据实际页尺寸、旋转和已看到的画面定位。例如：

```json
{"operation":"extract_regions","sources":[{"path":"/absolute/path/document.pdf","regions":[{"page":1,"bounding_box":{"left":72,"bottom":400,"right":300,"top":600}}]}],"scale":3}
```

图片由工具返回后才算实际看过；文件路径、Markdown 图片引用和文字摘要不代表看到了图片。按需要逐页查看，不默认渲染整份长文档。结论保留页码，说明尚未查看或无法辨认的部分。

核对返回结果与实际图片：工具调用结束不代表每个源文件或区域都成功；失败、空图或裁到错误区域时，应调整后重新查看，不能声称已经完成视觉核对。
