# Zhiz Print - 高级打印设计器

基于 Frappe/ERPNext 框架的自定义打印模板设计器，提供可视化表格设计、多引擎PDF导出、Excel导出等功能。

## 功能特性

- **可视化打印设计器** — 表格式拖拽设计，支持单元格合并、样式编辑、数据绑定
- **多数据源** — 支持静态文本、数据查询、条形码（CODE128/CODE39）、二维码、图片
- **自动分页** — 根据纸张尺寸和行高自动分页，支持标题行重复
- **页眉页脚** — 支持页码、日期时间等占位符
- **PDF 导出** — 支持 WeasyPrint / wkhtmltopdf / Chromium 三种转换引擎
- **Excel 导出** — CSS 样式转 Excel 格式，含条码/二维码图片
- **打印日志** — 记录每次打印/导出操作，支持预览回溯
- **参数化模板** — 支持自定义打印参数，打印前弹窗填写

## DocType 清单

| DocType | 类型 | 说明 |
|---------|------|------|
| Super Print Design | 主文档 | 打印模板设计 |
| Super Print Design Item | 子表 | 单元格定义 |
| Super Print Design Parameter | 子表 | 打印参数定义 |
| Super Print Design Query | 子表 | 查询定义 |
| Super Print Paper | 主文档 | 纸张尺寸管理 |
| Super Print Log | 主文档 | 打印操作日志 |
| Super Print Enabled Doctype | 子表 | 启用打印的单据类型 |
| Zprint Setting | 单文档 | 打印设计器全局设置 |

## 支持环境

### 硬件架构

| 架构 | 状态 |
|------|------|
| x86_64 (amd64) | 已编译 |
| aarch64 (arm64) | 已编译 |

### Python 版本

| 版本 | x86_64 | aarch64 |
|------|--------|---------|
| Python 3.10 | .so | - |
| Python 3.11 | .so | .so |
| Python 3.12 | .so | - |

Python 运行时自动加载匹配当前版本的 `.so` 文件，无需额外配置。

## 安装

```bash
# 进入 bench 目录
cd /home/frappe/frappe-bench

# 克隆并指定目录名（仓库含编译产物，目录名必须为 zhiz_print）
git clone git@gitee.com:gdzhiz/zhiz_print-dist.git apps/zhiz_print

# 安装到站点
bench --site <site-name> install-app zhiz_print
```

## 依赖

### Python 依赖

- frappe >= 15.0.0
- qrcode（二维码生成）
- beautifulsoup4（PDF 边框修复）
- cssutils（Excel CSS 解析）
- openpyxl（Excel 生成）

### PDF 引擎（三选一）

| 引擎 | 说明 | 安装方式 |
|------|------|---------|
| **wkhtmltopdf**（默认） | 通过 pdfkit 调用 wkhtmltopdf 二进制 | `sudo apt install wkhtmltopdf` + `pip install pdfkit` |
| **WeasyPrint** | 纯 Python PDF 渲染，CSS 支持最好 | `pip install weasyprint`，需安装系统依赖：`sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev libcairo2` |
| **Chromium** | 通过无头浏览器生成 PDF，渲染最精确 | 安装 Chromium/Chrome：`sudo apt install chromium-browser` 或下载 Google Chrome |

在 **Zprint Setting** 的「PDF转换模式」中选择要使用的引擎。

## 使用

1. 进入 **Zprint Setting** 单文档，启用超级打印页面
2. 在 **Super Print Paper** 中定义纸张尺寸
3. 在 **Super Print Design** 中设计打印模板
4. 打开任意文档点击打印，左侧显示自定义模板选择器
