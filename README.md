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

## 启用流程

1. 进入 **Zprint Setting** 单文档，启用超级打印页面
2. 在 **Super Print Paper** 中定义纸张尺寸
3. 在 **Super Print Design** 中设计打印模板
4. 打开任意文档点击打印，左侧显示自定义模板选择器

---
## 使用基础教程

### 一、文档字段绑定

在单元格的 `cell_value` 中，使用 `{doc.field_name}` 占位符绑定目标文档的字段值。

**可用范围：** 目标文档（`target_doctype` 指定的 DocType）的所有字段。

**语法：** `{doc.字段名}`

**示例：** 假设 `target_doctype` 为 `Sales Invoice`，在单元格中写入：

```
发票编号：{doc.name}
客户名称：{doc.customer}
日期：{doc.posting_date}
金额：{doc.grand_total}
```

打印时自动替换为实际文档的对应字段值。

### 二、参数字段

参数用于在打印前由用户填写，实现动态数据输入。参数在 `design_parameters` 子表中定义。

#### 参数定义字段

| 字段 | 说明 |
|------|------|
| Parameter ID (`param_name`) | 参数唯一标识，用于在查询代码和单元格中引用 |
| Display Label (`param_label`) | 打印弹窗中显示的标签 |
| Parameter Type (`param_type`) | 类型：`Data`、`Int`、`Float`、`Date`、`Link`、`Select` |
| Default Value (`default_value`) | 默认值 |
| Required (`reqd`) | 是否必填 |
| Options (`options`) | Select 类型填选项（每行一个）；Link 类型填目标 DocType 名称 |

#### 在单元格中引用参数

使用 `{param.param_name}` 占位符：

```
备注：{param.remark}
数量：{param.qty}
```

#### 示例：定义一个「备注」参数

| Parameter ID | Display Label | Parameter Type | Default Value | Required | Options |
|---|---|---|---|---|---|
| remark | 备注 | Data | | 0 | |
| qty | 数量 | Int | 1 | 1 | |
| warehouse | 仓库 | Link | | 0 | Warehouse |

打印时弹窗让用户填写，填入的值可在单元格中使用。

### 三、子表字段（数据驱动行）

子表字段用于打印文档的子表数据（如销售订单的明细行）。系统自动检测子表模式并展开行。

#### 在单元格中绑定子表字段

**语法：** `{doc.child_table_name.field_name}`

**示例：** 假设 `Sales Invoice` 有子表 `items`：

```
{doc.items.item_code}
{doc.items.qty}
{doc.items.rate}
{doc.items.amount}
```

#### 行为

- 包含子表占位符的行会自动变为「数据驱动行」，根据子表数据条数自动复制该行
- 每行对应子表的一条记录
- 无需手动设置 `row_type`，系统自动检测

#### 手动设置行类型

在设计器中可设置行的 `row_type`：

| 值 | 说明 |
|---|---|
| 空 / Normal Row | 普通行，不重复 |
| Repeat Title Row | 标题行，每页自动重复（用于表头） |
| Data-Driven Row | 数据驱动行，按数据条数自动展开 |

#### 行显示效果 (`row_display`)

| 值 | 说明 |
|---|---|
| 空 / Auto Wrap | 默认，文本自动换行，行高自适应 |
| Fixed Height | 固定行高，超出部分隐藏 |
| Auto Shrink Font | 自动缩小字号以适应固定行高 |

### 四、单元格类型

| cell_type | 说明 | 额外配置 |
|-----------|------|---------|
| `static` | 静态文本，支持 `{doc.*}` / `{param.*}` 占位符 | 无 |
| `barcode` | 条形码（CODE128 / CODE39） | 设置 `barcode_format`、`barcode_width`、`barcode_height` |
| `qrcode` | 二维码 | 设置 `barcode_width`、`barcode_height` |
| `image` | 图片，`cell_value` 填图片 URL | 无 |

### 五、页眉页脚

在 Super Print Design 的 Header & Footer 区域，可分别设置左、中、右三个位置的页眉和页脚内容，支持 HTML。

**页眉页脚占位符：**

| 占位符 | 说明 | 示例输出 |
|--------|------|---------|
| `{page}` | 当前页码 | `1`、`2`、`3` |
| `{pages}` | 总页数 | `5` |
| `{now_date}` | 当前日期 | `2026-04-16` |
| `{now_time}` | 当前时间 | `14:30:00` |
| `{date_time}` | 当前日期时间 | `2026-04-16 14:30:00` |

**文档字段占位符：** 页眉页脚中同样支持 `{doc.field_name}` 引用目标文档字段，例如 `{doc.name}` 显示单据编号。

**示例：**

页脚左侧显示单据编号：
```html
<span>{doc.name}</span>
```

页脚居中显示页码：
```html
<div style="text-align:center">第 {page} 页 / 共 {pages} 页</div>
```

页眉右侧显示打印时间：
```html
<div style="text-align:right">打印时间：{date_time}</div>
```

### 六、启用条件

在 `Enable Condition` 字段中填写 eval 表达式，控制模板是否显示。留空则始终启用。

**可用变量：** `doc`（目标文档）、`user`（当前用户名）

**示例：**

```
doc.status=='Submitted'
doc.docstatus==1
doc.grand_total > 1000
```