# Zhiz Print - Advanced Print Designer

A custom print template designer built on the Frappe/ERPNext framework, providing visual table design, multi-engine PDF export, Excel export, and more.

## Features

- **Visual Print Designer** — Table-based drag-and-drop design with cell merging, style editing, data binding, and free column-resize by dragging
- **Multiple Data Sources** — Static text, data queries, barcodes (CODE128/CODE39), QR codes, images
- **Cell Value Semantics** — Four semantics: static replacement, Python logic expressions, `=` arithmetic expressions, `=rowsum(R:C)` / `=pagerowsum(R:C)` totals (page-scope subtotal supported)
- **Auto Pagination** — Client-measured pagination (browser measures real row heights; preview/print/PDF always consistent), with title row repeat
- **Header & Footer** — Header/footer with per-column alignment, vertical left-header / right-footer strips, preserved spaces
- **Row Controls** — Line spacing (0-20px), row sorting by rendered value, check-pick data presentation (choose rows before printing), print-count driver cell
- **Native Reuse** — Optional sidebar section to use native Print Formats; reuse native Letter Head header/footer per design
- **PDF Export** — Three conversion engines: WeasyPrint / wkhtmltopdf / Chromium
- **Excel Export** — CSS styles converted to Excel formats, including barcode/QR code images
- **Print Log** — Records every print/export operation for preview追溯
- **Parameterized Templates** — Custom print parameters with pre-print dialog
- **Template Platform** — Share designs to a central platform; per-template visibility (Everyone / Specific Companies / Private), quick view filters, one-click install; company names & QR/URL desensitized

## DocType List

| DocType | Type | Description |
|---------|------|-------------|
| Super Print Design | Main Doc | Print template design |
| Super Print Design Item | Child Table | Cell definitions |
| Super Print Design Parameter | Child Table | Print parameter definitions |
| Super Print Design Query | Child Table | Query definitions |
| Super Print Paper | Main Doc | Paper size management |
| Super Print Log | Main Doc | Print operation log |
| Super Print Enabled Doctype | Child Table | DocTypes with print enabled |
| Zprint Setting | Single Doc | Global print designer settings |

## Supported Environments

### Hardware Architecture

| Architecture | Status |
|-------------|--------|
| x86_64 (amd64) | Compiled |
| aarch64 (arm64) | Compiled |

### Python Versions

| Version | x86_64 | aarch64 |
|---------|--------|---------|
| Python 3.10 | .so | .so |
| Python 3.11 | .so | .so |
| Python 3.12 | .so | .so |
| Python 3.13 | .so | .so |
| Python 3.14 | .so | .so |

Python runtime automatically loads the matching `.so` file — no additional configuration needed.

## Installation

```bash
cd /home/frappe/frappe-bench
git clone git@gitee.com:gdzhiz/zhiz_print.git apps/zhiz_print
bench get-app zhiz_print ./apps/zhiz_print
bench --site <site-name> install-app zhiz_print
bench build
```

## Dependencies

### Python Dependencies

- frappe >= 15.0.0
- qrcode (QR code generation)
- beautifulsoup4 (PDF border fix)
- cssutils (Excel CSS parsing)
- openpyxl (Excel generation)

### PDF Engine (choose one)

| Engine | Description | Installation |
|--------|-------------|--------------|
| **wkhtmltopdf** (default) | Calls wkhtmltopdf binary via pdfkit | `sudo apt install wkhtmltopdf` + `pip install pdfkit` |
| **WeasyPrint** | Pure Python PDF rendering, best CSS support | `pip install weasyprint`, requires system deps: `sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev libcairo2` |
| **Chromium** | Headless browser PDF generation, most accurate rendering | Install Chromium/Chrome: `sudo apt install chromium-browser` or download Google Chrome |

Select the engine in **Zprint Setting** → "PDF Conversion Mode".

## Setup Guide

1. Go to **Zprint Setting** (Single DocType) and enable the super print page
2. Define paper sizes in **Super Print Paper**
3. Design print templates in **Super Print Design**
4. Open any document and click Print — custom template selector appears on the left

---
## Basic Usage Tutorial

### 1. Document Field Binding

Use `{doc.field_name}` placeholders in a cell's `cell_value` to bind target document field values.

**Scope:** All fields of the target document (the DocType specified in `target_doctype`).

**Syntax:** `{doc.field_name}`

**Example:** Assuming `target_doctype` is `Sales Invoice`, enter in a cell:

```
Invoice No: {doc.name}
Customer: {doc.customer}
Date: {doc.posting_date}
Amount: {doc.grand_total}
```

Placeholders are automatically replaced with actual field values at print time.

### 2. Parameter Fields

Parameters allow users to fill in dynamic data before printing. They are defined in the `design_parameters` child table.

#### Parameter Definition Fields

| Field | Description |
|-------|-------------|
| Parameter ID (`param_name`) | Unique parameter identifier, used to reference in cells |
| Display Label (`param_label`) | Label shown in the print dialog |
| Parameter Type (`param_type`) | Types: `Data`, `Int`, `Float`, `Date`, `Link`, `Select` |
| Default Value (`default_value`) | Default value |
| Required (`reqd`) | Whether the parameter is required |
| Options (`options`) | For Select type: one option per line; for Link type: target DocType name |

#### Referencing Parameters in Cells

Use `{param.param_name}` placeholders:

```
Remark: {param.remark}
Quantity: {param.qty}
```

#### Example: Define a "Remark" Parameter

| Parameter ID | Display Label | Parameter Type | Default Value | Required | Options |
|---|---|---|---|---|---|
| remark | Remark | Data | | 0 | |
| qty | Quantity | Int | 1 | 1 | |
| warehouse | Warehouse | Link | | 0 | Warehouse |

A dialog prompts the user to fill in values at print time, which can then be used in cells.

### 3. Child Table Fields (Data-Driven Rows)

Child table fields are used to print sub-table data (e.g., line items of a sales order). The system auto-detects child table patterns and expands rows.

#### Binding Child Table Fields in Cells

**Syntax:** `{doc.child_table_name.field_name}`

**Example:** Assuming `Sales Invoice` has a child table `items`:

```
{doc.items.item_code}
{doc.items.qty}
{doc.items.rate}
{doc.items.amount}
```

#### Behavior

- Rows containing child table placeholders automatically become "Data-Driven Rows", duplicated based on the number of child table records
- Each row corresponds to one child table record
- No need to manually set `row_type` — auto-detected by the system

#### Manual Row Type Setting

You can set a row's `row_type` in the designer:

| Value | Description |
|-------|-------------|
| Empty / Normal Row | Normal row, not repeated |
| Repeat Title Row | Title row, automatically repeated on every page (for table headers) |
| Data-Driven Row | Data-driven row, auto-expanded based on data count |

#### Row Display Mode (`row_display`)

| Value | Description |
|-------|-------------|
| Empty / Auto Wrap | Default — text wraps, row height adjusts automatically |
| Fixed Height | Fixed row height, overflow is hidden |
| Auto Shrink Font | Font size shrinks automatically to fit fixed row height |

### 4. Cell Types

| cell_type | Description | Additional Config |
|-----------|-------------|-------------------|
| `static` | Static text, supports `{doc.*}` / `{param.*}` placeholders | None |
| `barcode` | Barcode (CODE128 / CODE39) | Set `barcode_format`, `barcode_width`, `barcode_height` |
| `qrcode` | QR code | Set `barcode_width`, `barcode_height` |
| `image` | Image, set `cell_value` to image URL | None |

### 5. Header / Footer / Left Header / Right Footer

The four edge bands of the paper (top/bottom/left/right margin bands) hold company name, page numbers, signature lines, copy markers, etc. Configure them in **Designer → Page Settings**; switch among the **Header / Footer / Left Header / Right Footer** tabs — the corresponding paper area **flashes and highlights** to show the current selection. All four areas **share one set of settings per page**.

> Active area = paper margin band. Reserve the corresponding margin (`margin_top/bottom/left/right > 0`) in **Paper Setting / Super Print Paper** first; otherwise the area is not rendered.

#### 5.1 Placeholders (common to all four areas)

| Placeholder | Description | Example Output |
|-------------|-------------|----------------|
| `{page}` | Current page number | `1`, `2`, `3` |
| `{pages}` | Total pages | `5` |
| `{now_date}` | Current date | `2026-04-16` |
| `{now_time}` | Current time | `14:30:00` |
| `{date_time}` | Current date and time | `2026-04-16 14:30:00` |

**Document Field Placeholders:** Header and footer also support `{doc.field_name}` to reference target document fields, e.g., `{doc.name}` displays the document number.

**Examples:**

Footer left — document number:
```html
<span>{doc.name}</span>
```

Footer center — page numbers:
```html
<div style="text-align:center">Page {page} of {pages}</div>
```

Header right — print time:
```html
<div style="text-align:right">Printed: {date_time}</div>
```

#### 5.2 Header / Footer (horizontal · top/bottom margin bands)

Each area is split horizontally into **Left / Center / Right** columns; each accepts HTML and is aligned independently:

| Setting | Field | Values |
|---|---|---|
| Vertical align (whole row) | `page_header_align` / `page_footer_align` | Top / Center / Bottom |
| Left column align | `page_header_left_align` / `page_footer_left_align` | Left / Center / Right |
| Center column align | `page_header_center_align` / `page_footer_center_align` | Left / Center / Right |
| Right column align | `page_header_right_align` / `page_footer_right_align` | Left / Center / Right |

```
Footer·Center:  Page {page} of {pages}        (center align = Center)
Footer·Right:   Printed: {date_time}          (right align = Right)
Header·Left:    No: {doc.name}                (left align = Left)
Header vertical align: Bottom (row sits at the bottom of the margin band)
```

#### 5.3 Left Header / Right Footer (vertical · left/right margin bands)

Text is rendered **vertically** (`writing-mode: vertical-rl`) along the left margin band (Left Header) or right margin band (Right Footer). Typical use: multi-copy form markers arranged vertically along the paper edge — e.g. a delivery note's right edge `⑴ Stub ⑵ Finance ⑶ Accounting ⑷ Warehouse ⑸ Receiver`.

| Setting | Field | Values |
|---|---|---|
| Horizontal align (across band width) | `page_left_header_h_align` / `page_right_footer_h_align` | Left (toward edge) / Center / Right (toward table) |
| Vertical align (along band length) | `page_left_header_v_align` / `page_right_footer_v_align` | Top / Center / Bottom |

```
Right Footer content:   ⑴ Stub   ⑵ Finance   ⑶ Accounting   ⑷ Warehouse   ⑸ Receiver
Right Footer h-align:   Center (centered across the right margin band)
Right Footer v-align:   Center (centered vertically)
```

#### 5.4 Spaces & layout (common to all four areas)

All four areas **preserve consecutive spaces** (`white-space: pre-wrap`); you can pad with spaces to tune spacing — they are not collapsed by HTML. In horizontal areas (Header/Footer) spaces are horizontal gaps; in vertical areas (Left Header/Right Footer) spaces are vertical gaps along the writing direction.

### 6. Enable Condition

Enter an eval expression in the `Enable Condition` field to control template visibility. Leave empty to always enable.

**Available variables:** `doc` (target document), `user` (current username)

**Examples:**

```
doc.status=='Submitted'
doc.docstatus==1
doc.grand_total > 1000
```
