# zhiz_print · 高级打印设计器 使用说明书

> **适用版本** v15.10.14+ ｜ **截图来源** EXAMPLE_CLIENT生产环境 ｜ **更新** 2026-07-02
>
> 基于 Frappe / ERPNext 的可视化打印模板设计器 — 表格设计 · 数据绑定 · 表达式运算 · 多引擎 PDF · 批量打印 · 持久化日志。

---

## 目录

1. [概述与架构](#一概述与架构)
2. [安装与启用](#二安装与启用)
3. [设计器入门](#三设计器入门)
4. [数据绑定(占位符)](#四数据绑定占位符)
5. [单元格值的四种语义](#五单元格值的四种语义-核心)
6. [行控制与排序](#六行控制与排序)
7. [单元格类型](#七单元格类型)
8. [分页与多页设计](#八分页与多页设计)
9. [页眉页脚](#九页眉页脚)
10. [启用条件](#十启用条件-enable_condition)
11. [草稿拦截](#十一草稿拦截-draft_no_print)
12. [输出(预览/PDF/Excel/批量)](#十二输出预览pdfexcel批量)
13. [打印日志](#十三打印日志持久化)
14. [全局设置](#十四全局设置-zprint-setting)
15. [打印模板平台](#十五打印模板平台)
16. [附录·速查表与常见问题](#十六附录速查表与常见问题)

---

## 一、概述与架构

zhiz_print 用表格式网格设计打印版面,支持单元格合并、四种取值语义(静态 / 逻辑 / 表达式 / 合计)、按列排序、自动分页、二维码条码图片,以及多引擎 PDF / Excel 导出。所有打印操作记录为持久化日志(渲染快照,不随后续设计改动而变化)。

### 数据流

| 阶段 | 说明 | 关键代码 |
|---|---|---|
| **1. 数据获取** | 从目标单据(doc)读主表字段、子表(如 `items`)、自定义查询(`design_queries`)、用户参数(`param`) | `frappe.get_doc` · `_execute_query_with_doc_context` |
| **2. 运算** | 四种单元格值语义:静态替换、logic 条件表达式、`=` 算术表达式、`=rowsum()` 合计 | `_eval_logic_code` · `_eval_expression_cell` · `_eval_rowsum` |
| **3. 渲染** | 展开数据驱动行 → 按行级 `sorts` 排序 → 列值计算 → 客户端实测分页 → 组装 HTML | `_build_expanded_rows_v2` · `_build_row_html` · `build_measurement_html` |
| **4. 输出** | 打印预览 / PDF(wkhtmltopdf·WeasyPrint·Chromium) / Excel(openpyxl) / 批量 | `render_print_preview` · `generate_print_pdf` · `export_print_excel` |

### DocType 清单

| DocType | 类型 | 作用 |
|---|---|---|
| Super Print Design | 主文档 | 打印模板设计(网格 + 单元格 + 样式) |
| Super Print Design Item | 子表 | 单元格定义(row/col/span/类型/值/**page_no**) |
| Super Print Design Parameter | 子表 | 打印参数定义(打印前弹窗填) |
| Super Print Design Query | 子表 | 数据查询定义(SQL/函数) |
| Super Print Paper | 主文档 | 纸张尺寸(mm) |
| Super Print Log | 主文档 | 打印日志(含渲染快照) |
| Super Print Enabled Doctype | 子表 | 启用超级打印的单据类型 |
| Zprint Setting | 单文档 | 全局设置(PDF 引擎等) |

---

## 二、安装与启用

### 安装

```bash
cd /home/frappe/frappe-bench
git clone git@gitee.com:gdzhiz/zhiz_print-dist.git apps/zhiz_print
bench get-app zhiz_print ./apps/zhiz_print
bench --site <site-name> install-app zhiz_print
bench build
```

### 启用流程

1. **Zprint Setting** → 勾选「启用超级打印页面」
2. **Super Print Paper** → 定义纸张尺寸(如 A4 = 210×297mm)
3. **Super Print Enabled Doctype** → 把要用的单据类型(如 Sales Invoice)加入启用列表
4. **Super Print Design** → 设计模板,填 `target_doctype`、`print_paper`
5. 打开任意目标单据,点打印 → 左侧显示自定义模板选择器

---

## 三、设计器入门

设计器是一个表格式网格编辑器:左侧行号、顶部列号,中间是单元格。上方工具栏控制行列增删、字体、行类型;选中行/列/单元格时右侧出现属性面板。

### 网格操作

| 操作 | 说明 |
|---|---|
| 选中整行 / 整列 | 点击左侧行号 / 顶部列号 → 右侧出现行/列属性面板 |
| 选中单元格 | 点击单元格 → 出现单元格属性面板(类型/值/样式) |
| 插入行 / 列 | 选中行/列后点工具栏「插入行」「插入列」(在选中位置左侧/上方插入) |
| 合并单元格 | 单元格属性面板设 `rowspan` / `colspan`(被合并的格子自动标记 `||MERGED::R#C#||`) |

### 三个核心区域(表单 Tab)

- **Design View(设计视图)** — 可视化网格编辑器
- **Settings(设置)** — 启用条件 / 草稿拦截 / 优先级 / 纸张 等
- **Design Items** — 单元格明细(只读,序列化的格子数据)

---

## 四、数据绑定(占位符)

在单元格的 `cell_value` 里用占位符绑定数据。占位符打印时被替换为实际值。

### 四种占位符

| 语法 | 数据来源 | 示例 |
|---|---|---|
| `{doc.字段名}` | 目标单据主表字段 | `{doc.name}` · `{doc.customer}` · `{doc.posting_date}` |
| `{doc.子表.字段}` | 子表行字段(**自动触发数据驱动行**) | `{doc.items.item_code}` · `{doc.items.qty}` |
| `{param.参数名}` | 用户打印前填入的参数 | `{param.remark}` |
| `{查询名.列名}` | 自定义查询结果(首行) | `{bom_list.qty}` |

### 子表 → 数据驱动行

只要某行有单元格含 `{doc.子表.字段}`,该行自动变成**数据驱动行**,按子表条数自动复制;每行对应一条子表记录。无需手动设 `row_type`。

```
{doc.items.idx}              ← 行号
{doc.items.item_code}        ← 物料
{doc.items.qty}              ← 数量
{doc.items.rate}             ← 单价
{doc.items.amount}           ← 金额
```

### 参数(打印前弹窗)

在 **design_parameters** 子表定义参数,打印前弹窗让用户填写,用 `{param.参数名}` 引用。

| Parameter ID | Display Label | Type | 示例用途 |
|---|---|---|---|
| remark | 备注 | Data | 打印时手输一句话 |
| warehouse | 仓库 | Link → Warehouse | 选择仓库 |
| copies | 份数 | Int (reqd=1, default=1) | 打印份数 |

### 数据查询 (design_queries)

当需要**跨表取数、聚合统计、复杂 SQL** 而 `{doc.字段}` 和子表不够用时,在设计的 **design_queries** 子表定义查询,结果用 `{query_name.column}` 引用。

#### 查询定义字段(Super Print Design Query)

| 字段 | 说明 |
|---|---|
| Query Name (`query_name`) | 查询名,唯一标识,用于 `{query_name.column}` 引用 |
| Python Code (`query_code`) | Python 代码或函数路径,**结果必须赋给变量 `result`** |
| Parameters (`parameters`) | 参数映射,每行 `key=value` |

#### query_code 两种形式

**① frappe.qb 查询构建器(Python 代码)** — 把结果赋给 `result`

代码在 safe_exec 沙箱里执行,暴露以下对象:`qb` · `DocType` · `desc`/`asc` · `Sum`/`Count`/`Avg`/`Max`/`Min`/`Round`/`Concat`/`Coalesce`/`Abs`/`GROUP_CONCAT` · `filters`(参数字典)。`result` 必须是 list(每行 dict) 或 dict。

```python
# 示例:按物料查库存汇总(filters.item_code 由 parameters 注入)
bin = DocType("Bin")
result = (qb.from_(bin)
          .select(bin.item_code, Sum(bin.actual_qty).as_("qty"))
          .where(bin.item_code == filters.get("item_code"))
          .groupby(bin.item_code)
          .run(as_dict=True))
```

```python
# 示例:查客户最近 5 笔销售(聚合 + 排序 + 限制)
si = DocType("Sales Invoice")
result = (qb.from_(si)
          .select(si.name, si.grand_total, si.posting_date)
          .where(si.customer == filters.get("customer"))
          .orderby(si.posting_date, order=desc())
          .limit(5)
          .run(as_dict=True))
```

**② 函数路径(`app.module.file.function`)** — 调用项目里的 Python 函数,parameters 作为 kwargs 传入

```
query_code: zhiz_gy.api.report.get_sales_summary
```

函数返回 `list[dict]`(每行一个字典) 或 `list[list]`(自动转为 c0/c1/.. 列名)。适合复杂业务逻辑(多表 JOIN、Python 计算)封装在项目 app 里复用。

#### 参数(parameters)

每行 `key=value`,value 支持三种:

```
company=广优                                  ← 字面量
company=doc.company                           ← 单据字段(打印时动态替换)
from_date={{doc.posting_date}}                ← 双花括号写法(等价上一行)
item_code=doc.items.item_code                 ← 子表字段(数据驱动行展开时按行取)
```

参数注入到 `filters` 字典,在 query_code 里用 `filters.get("key")` 读取。

#### 引用查询结果

**单值(取首行)** — `{query_name.column}`:

```
当前库存:{stock.qty}
本月汇总:{summary.total_amount}
```

**数据驱动行(按查询结果展开)** — 在单元格属性面板设 `query_name` + `data_key`,该行按查询结果条数自动展开,每行一条结果:

| cell 配置 | 说明 |
|---|---|
| `query_name` = `bom_detail` | 绑定查询名 |
| `data_key` = `item_code` | 每行取该列作为主键 |
| cell_value `{bom_detail.qty}` | 自动按行匹配该行的 qty |

#### 完整示例:打印销售订单的 BOM 子件清单

1. **design_queries** 新增查询:
   - query_name = `bom_detail`
   - query_code(查 BOM 子件):
     ```python
     bom = DocType("BOM")
     bi = DocType("BOM Item")
     result = (qb.from_(bi)
               .select(bi.item_code, bi.qty, bi.stock_uom)
               .left_join(bom).on(bom.name == bi.parent)
               .where(bom.name == filters.get("bom_no"))
               .run(as_dict=True))
     ```
   - parameters:`bom_no=doc.bom_no`
2. **数据驱动行** cell 设 query_name=`bom_detail`、data_key=`item_code`
3. **单元格引用**:`{bom_detail.item_code}` / `{bom_detail.qty}` / `{bom_detail.stock_uom}`

> 💡 查询执行失败会记 Error Log 并返回空结果(单元格显示空),不会中断整个打印。

---

## 五、单元格值的四种语义 (核心)

这是 zhiz_print 最强大的能力:`cell_value` 不只是文本替换,而是根据写法自动选择四种运算语义之一。

### ① static — 直接替换(默认)

普通文本 + `{占位符}`,逐一替换。无 `=` 前缀、cell_type 非 logic。

```
客户:{doc.customer}
金额:{doc.grand_total}
备注:{doc.items.additional_notes}
```

### ② logic — 条件表达式(Python) · cell_type = logic

把单元格类型设为 **logic**,`cell_value` 写 Python 表达式。用于跨表取值、条件判断。暴露变量与助手:

| 可用 | 说明 |
|---|---|
| `doc` / `row` | 目标单据 / 当前子表行(数据驱动行展开时) |
| `get_value(doctype, name, field)` | 跨表单字段查询(返回字符串,空时返回 '') |
| `fmt(value, precision=2)` | 数字格式化(`str.format` 被 safe_eval 禁,必须用 `fmt`) |
| `flt` · `max` · `min` · `round` | 数值与内置函数(safe_eval 默认禁内置,需显式暴露) |

```
get_value("Item", row.item_code, "classification")     ← 取物料的"分类"字段
"滑板" if get_value("Item", row.item_code, "classification") == "滑板" else "其他"
fmt(row.qty * row.weight_per_unit, 3)
```

> ⚠️ **注意**:safe_eval 禁用 `str.format`、`frappe.utils.*` 属性访问、内置函数。务必用上表的 `fmt`/`flt`/`get_value`,不要写 `frappe.utils.flt(x)`。

### ③ =expression — 算术表达式 (新)

`cell_value` 以 **`=`** 开头:先做占位符替换,再对整个表达式 `safe_eval` 求值。用于行内运算。

```
={doc.items.qty}*{doc.items.weight_per_unit}     ← 总重 = 数量 × 件重
={doc.items.rate}*{doc.items.qty}               ← 金额 = 单价 × 数量
=round({doc.items.weight_per_unit}, 4)          ← 件重保留 4 位
```

求值结果用 `_fmt_val` 格式化(去尾零:`50.0 → "50"`、`5.10 → "5.1"`)。字段为空导致表达式非法时(如 `100*`)记录错误日志并显示原式,便于发现缺数据。

### ④ =rowsum(R:C) — 合计函数 (新)

对**第 R 行(数据驱动行)所有展开项的第 C 列**显示值求和。用于合计行。兼容中英文括号 `()` / `()`。

```
=rowsum(5:5)     ← 第5行第5列(数量)合计
=rowsum(5:7)     ← 第5行第7列(总重)合计 —— 第7列本身是 =round(...) 表达式也能正确累加
=rowsum(5:9)     ← 第5行第9列(金额)合计
```

> 💡 **关键**:rowsum 复用列值计算逻辑,所以合计对象即使是 `=` 表达式列、logic 列,都能算对。非数字单元格自动跳过。

### 四种语义对照表

| 写法 | 触发条件 | 示例 | 结果 |
|---|---|---|---|
| static | 无 `=` 前缀,非 logic | `客户:{doc.customer}` | 替换占位符 |
| logic | cell_type = logic | `get_value("Item", row.item_code, "x")` | Python 表达式求值 |
| =expression | `=` 开头(非 rowsum) | `={doc.items.qty}*{doc.items.rate}` | 替换后算术求值 |
| =rowsum | `=rowsum(R:C)` | `=rowsum(5:7)` | 第R行第C列合计 |

---

## 六、行控制与排序

### 行类型 (row_type)

| 值 | 说明 |
|---|---|
| Normal Row(空) | 普通行,不重复 |
| Repeat Title Row | 标题行,**每页自动重复**(用于表头) |
| Data-Driven Row | 数据驱动行,按子表条数自动展开 |

含 `{doc.子表.字段}` 的行会自动识别为数据驱动行,无需手动设。

### 行显示效果 (row_display)

| 值 | 说明 |
|---|---|
| Auto Wrap(空) | 默认,文本自动换行,行高自适应 |
| Fixed Height | 固定行高,超出隐藏(`white-space:nowrap`) |
| Auto Shrink Font | 自动缩小字号以适应固定行高 |

### 行级排序 (新)

选中**数据驱动行**整行 → 右侧行属性面板出现「**数据排序**」输入框(只有数据驱动行才显示)。填 `row.N` 按该行第 N 列的**渲染后显示值**排序(覆盖 logic / 表达式 / 拼接列,因为按最终显示值排)。

#### 语法

```
row.3 ASC               ← 按第3列升序
row.3 ASC, row.5 DESC   ← 多级:先第3列升序,相同再第5列降序
row.1                   ← 方向可省略,默认 ASC
```

| 特性 | 说明 |
|---|---|
| 多级排序 | 逗号分隔,每级可各自方向 |
| 多子表 | 每个数据驱动行各自配(行5=items、行8=taxes 独立排序) |
| 智能分型 | 数字按数值排、字符串按字典序、空值排末尾,不冲突 |
| 排序依据 | 第 N 列的**显示值**(即 logic/表达式算后的值),非底层字段 |

排序配置存储在 `row_styles` JSON 的 `sorts` 键:

```json
{
  "5": { "height": 20, "sorts": "row.2 ASC" },
  "6": { "height": 25 }
}
```

---

## 七、单元格类型

| cell_type | 说明 | 额外配置 |
|---|---|---|
| static | 静态文本 + 占位符 / `=`表达式 / `=rowsum` | — |
| logic | Python 条件表达式 | — |
| barcode | 条形码(CODE128 / CODE39) | `barcode_format` · `barcode_width` · `barcode_height` |
| qrcode | 二维码 | `barcode_width` · `barcode_height` |
| image | 图片,`cell_value` 填 URL 或 `/files/xxx.png` | — |

### 对齐(text-align)

图片 / 二维码 / 条码单元格支持 `text-align` 对齐(在 `css_style` 里写)。设计器网格演示与打印预览**表现一致**(v15.10.10 修复:演示容器从 flex 改 block,使 text-align 经继承生效)。

```
css_style: text-align:right; vertical-align:middle;     ← 二维码靠右居中
```

---

## 八、分页与多页设计

### 8.1 数据分页(自动)

#### 标题行重复

把表头行设为 **Repeat Title Row**,每页自动重复出现。数据驱动行(明细)在标题行之下展开。

#### 客户端实测分页(v15.10.01+) · 核心

zhiz_print 把**行高测量交给浏览器**:首次渲染一个不锁高的「量高架」,JS 读取每行真实 `offsetHeight`(含字体加载后的实际换行高度),贪心装箱成页,再带着精确行高二次渲染。这样预览 / 打印 / PDF 三路分页完全一致,不再出现「预览看到第 12 行、PDF 却被截断」的估算偏差。

> 💡 底层仍有估算兜底(`_get_row_height`),客户端实测失败或直连 API 时走估算,保证不回归。

### 8.2 多页设计(page_count) · 逻辑多页

**`page_count` 字段**(Settings 区,默认 1)= 一个打印模板的**逻辑页数**。当需要「一个打印任务输出多个不同版面」时使用,例如:

- **第 1 页** = 送货单(客户签字)
- **第 2 页** = 回执联(仓库留存)
- **第 3 页** = 结算明细

每页是**独立的网格**(各自的 rows × columns、各自的单元格内容),单元格通过 `page_no` 字段标记属于哪一页。

#### 工作机制

| 层面 | 说明 |
|---|---|
| 设计器 | 顶部「页面」控件切换(page 1 / 2 / …),当前页的网格可独立编辑 |
| 数据存储 | 每个 `Super Print Design Item`(单元格)有 `page_no` 字段(1, 2, …),标记归属 |
| 渲染 | `build_preview_html` 循环 `page_no = 1..page_count`,每页独立 `_build_cell_map_for_page(page_no)` 渲染成一个 `.print-page` 区域 |
| 单元格隔离 | 第 1 页的单元格不会出现在第 2 页(按 page_no 过滤) |

#### 与「数据分页」的区别(重要)

| 概念 | 触发 | 性质 |
|---|---|---|
| **逻辑页 (page_count)** | 设计师手动设 `page_count > 1` | 版面数 — 每页不同布局 |
| **物理页 (数据分页)** | 数据驱动行多,自动跨页 | 同一版面被数据撑成多页 |

一个逻辑页(如 page_no=1)的数据驱动行很多时,仍会**自动分到多个物理页**(每物理页重复该逻辑页的标题行)。两者是正交的:`page_count × 每页数据分页 = 总物理页数`。

#### 配置示例

```
page_count = 2
# 第1页(page_no=1):送货单版面 —— logo + 表头 + 明细 + 签字栏
# 第2页(page_no=2):回执版面 —— 简化表头 + 明细 + 仓库留存签字
```

切换到第 2 页编辑时,设计器只显示 page_no=2 的单元格;新建格子自动标当前页号。

> ⚠️ 单页模板保持 `page_count = 1`(默认),无需关心此字段。

---

## 九、页眉页脚

设计的 Header & Footer 区域,可分别设置左 / 中 / 右三个位置,支持 HTML。

### 占位符

| 占位符 | 含义 | 示例输出 |
|---|---|---|
| `{page}` | 当前页码 | 1 |
| `{pages}` | 总页数 | 3 |
| `{now_date}` | 当前日期 | 2026-07-02 |
| `{now_time}` | 当前时间 | 14:30:00 |
| `{date_time}` | 日期时间 | 2026-07-02 14:30:00 |
| `{doc.字段}` | 单据字段(同正文) | SRT-2606-00117 |

```
页脚居中:  第 {page} 页 / 共 {pages} 页
页脚右侧:  打印时间:{date_time}
页眉左侧:  单号:{doc.name}
```

---

## 十、启用条件 (enable_condition)

控制模板在哪些单据上显示。留空 = 始终启用。表达式为真时模板出现在打印选择器里。

### 可用变量与助手

| 变量 | 说明 |
|---|---|
| `doc` | 目标单据(可读所有字段) |
| `user` | 当前用户邮箱 |
| `get_value` / `fmt` / `flt` | 同 logic 单元格助手 |

支持 JS 风格运算符:`==` `!=` `&&` `||` `&` `|`(带空格)自动转 Python。

### 示例:按公司 + 物料分类匹配模板

```
doc.company == '嘉善县广优轴承有限公司'

doc.company == '安徽索尔精密科技有限公司'

doc.company == '广优' && get_value("Item", doc.production_item, "classification") == "滑板"
```

同一个单据类型可挂多个设计,用 `enable_condition` 按公司 / 物料 / 状态分流,打印时只显示匹配的。

---

## 十一、草稿拦截 (draft_no_print)

勾选「**Draft No Print**」后,该设计对**草稿单据(docstatus=0)**禁止打印 / 导出,只允许预览。已提交单据(docstatus=1)不受影响。

| draft_no_print | docstatus=0(草稿) | docstatus≥1(已提交) |
|---|---|---|
| 1(勾选) | 仅预览,隐藏打印/PDF/Excel 按钮 | 正常打印 |
| 0(未勾) | 不限制 | 不限制 |

用于「设计还在调整,不想被误打印成正式单据」的场景。

---

## 十二、输出(预览/PDF/Excel/批量)

### 打印预览

目标单据点「打印」进入超级打印预览:左侧选设计、右侧按纸张渲染(白底纸张 + 阴影 + 灰底衬托 + 缩放控件)。预览采用客户端实测分页,与最终打印/PDF 完全一致。

### PDF 导出(三引擎)

| 引擎 | 特点 | 选择位置 |
|---|---|---|
| wkhtmltopdf(默认) | 通过 pdfkit 调二进制,速度快 | Zprint Setting → PDF 转换模式 |
| WeasyPrint | 纯 Python,CSS 支持最好 | 同上 |
| Chromium | 无头浏览器,渲染最精确 | 同上 |

### Excel 导出

CSS 样式转 Excel 格式(openpyxl),条码 / 二维码以图片形式嵌入单元格。

### 批量打印

批量打印页面支持一次选择多个单据,自动匹配模板,批量生成 PDF / Excel,并逐条记录日志。

打印输出的纸张渲染效果(灰底 + 白纸 + 阴影 + 分页)与下方日志预览一致。

---

## 十三、打印日志(持久化)

每次打印 / 导出 / 批量都记录一条 **Super Print Log**。**日志是持久化的** — 创建时把当时的渲染 HTML 快照存进 `print_preview_html`,后续改设计**不影响历史日志**。打开日志后切换到「**打印预览**」tab,即可看到当时打印的实际纸张效果。

### 字段

| 字段 | 说明 |
|---|---|
| reference_doctype / reference_name | 目标单据类型 / 单号 |
| print_design | 设计名(Data 类型,删除设计不被引用阻止) |
| export_type | Print / Export PDF / Export Excel |
| print_user / print_time / print_count | 操作人 / 时间 / 该单据累计打印次数 |
| parameters_used | 打印时填的参数(JSON) |
| print_preview_html | 渲染快照(预览页读它显示,持久化) |

> 💡 **批量日志也有快照**:批量打印日志创建时同样渲染 html 存快照(后端估算分页)。单文档日志存客户端精确分页快照,批量存后端估算分页,内容一致、分页位置可能略有出入。

---

## 十四、全局设置 Zprint Setting

| 配置项 | 说明 |
|---|---|
| 启用超级打印页面 | 总开关,关闭后单据恢复原生打印 |
| PDF 转换模式 | wkhtmltopdf / WeasyPrint / Chromium 三选一 |
| explicit_image_preview | 勾选后图片单元格转 base64 嵌入 PDF(自包含);不勾保持 URL 引用 |

---

## 十五、打印模板平台

打印模板平台是一个**跨用户共享打印设计**的中心化市场:一个用户设计好的模板可推送到平台,其他用户从平台浏览、下载安装到本地使用。适合总部统一分发标准模板、各分子公司按需安装。

### 架构

| 角色 | 说明 |
|---|---|
| **中心服务器** | 模板存储(跑 zhiz_licser);存原版设计数据 + 预览快照 |
| **客户端** | 各用户服务器(装 zhiz_print);设计 → 推送到中心,或从中心拉取安装 |

### 推送模板到平台(share)

在设计器(Super Print Design 表单)点「**分享到模板平台**」按钮:

1. 序列化设计数据 + 渲染预览(sample_doc 实际预览 + 模板结构预览)
2. 图片转 base64 嵌入(跨服务器自包含,解决推送后图片 URL 指向原服务器不可达)

### 浏览模板平台

打开「**模板平台**」页面:

- 列表显示所有已上传的模板,左侧按 **DocType 分类筛选**(如 Sales Order / Delivery Note / Work Order 等)
- 每张卡片显示模板缩略预览、名称、目标单据类型、版本、下载次数
- 点击卡片弹出详情对话框,可切换 **设计效果**(模板结构,占位符原样显示)和**打印效果**(实际数据渲染)两个页签

### 下载安装模板(install)

点卡片打开详情(打印效果 + 设计效果双 tab),点「**下载安装**」:

1. 检查本地是否存在目标 DocType
2. 纸张处理(同名同配置复用 / 同名异配置指定新名 / 无则新建)
3. 创建本地 Super Print Design:`sample_doc` 剔空(本地无原单据),`insert` 加 `ignore_mandatory` 跳过必填校验,安装后用户在设计器补充本地演示单据
4. 下载计数 +1

### 脱敏

上传到平台的模板会对公司名称进行必要的脱敏处理。

### 评论

每个模板详情页可留言(评论存中心,所有用户可见)。

---

## 十六、附录·速查表与常见问题

### ① 占位符速查

| 占位符 | 用于 | 示例 |
|---|---|---|
| `{doc.字段}` | 主表字段 | `{doc.name}` |
| `{doc.子表.字段}` | 子表行(触发数据驱动行) | `{doc.items.qty}` |
| `{param.名}` | 用户参数 | `{param.remark}` |
| `{query.列}` | 查询结果首行 | `{bom.qty}` |
| `{page}` `{pages}` `{date_time}` 等 | 页眉页脚 | `第{page}页` |

### ② cell_value 写法速查

| 写法 | 语义 | 条件 |
|---|---|---|
| `xxx{doc.f}xxx` | static 直接替换 | 非 = 开头,非 logic |
| `get_value(...)` | logic Python 表达式 | cell_type = logic |
| `=算术` | expression 求值 | = 开头 |
| `=rowsum(R:C)` | 合计第R行第C列 | =rowsum 开头 |
| `row.N ASC` | 行排序(写 row_styles.sorts) | 数据驱动行属性面板 |

### ③ 常见问题

**Q:删除某个打印设计,提示被引用无法删?**
A:Super Print Log 的 `print_design` 已从 Link 改为 Data(v15.10.11),不再阻止删除。直接删设计即可,日志保留设计名(纯文本)。

**Q:logic 单元格显示空白?**
A:safe_eval 禁 `str.format` / `frappe.utils.*` / 内置函数。改用 `fmt()` / `flt()` / `get_value()`。错误会被 catch 并记 Error Log。

**Q:打印预览分页和 PDF 不一致?**
A:v15.10.01+ 已改客户端实测分页,三路一致。若仍不一致,确认浏览器字体已加载(`document.fonts.ready`),实测依赖字体度量。

**Q:批量打印日志预览空白?**
A:v15.10.13 前的批量日志没存快照(历史数据无法回填);v15.10.13+ 新建的批量日志有快照,可预览。

**Q:二维码 / 图片在设计器里对齐没反应?**
A:v15.10.10 已修复(演示容器 flex→block)。强刷浏览器(Ctrl+Shift+R)清 CSS 缓存。

**Q:打印方向 orientation?**
A:~~已废弃~~ 该功能已从代码移除,改用纸张宽高数值控制方向。

**Q:多页设计(page_count)和数据分页什么关系?**
A:`page_count` 是**逻辑页**(版面数,手动设),每页不同布局;数据分页是**物理页**(数据多自动跨页),同一版面被数据撑成多页。两者正交:`page_count × 每页数据分页 = 总物理页数`。
