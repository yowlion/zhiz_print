# Zhiz Print - Advanced Print Designer

A custom print template designer for Frappe/ERPNext, featuring visual grid-based design, multi-engine PDF export, and Excel export.

## Features

- **Visual Print Designer** — Grid-based drag-and-drop design with cell merging, style editing, and data binding
- **Multiple Data Sources** — Static text, data queries, barcodes (CODE128/CODE39), QR codes, images
- **Auto Pagination** — Automatic page breaks based on paper size and row heights, with repeating title rows
- **Headers & Footers** — Page numbers, date/time placeholders support
- **PDF Export** — Three engines: WeasyPrint / wkhtmltopdf / Chromium
- **Excel Export** — CSS-to-Excel format conversion with barcode/QR images
- **Print Logging** — Track all print/export operations with preview playback
- **Parameterized Templates** — Custom print parameters with pre-print dialog

## DocType List

| DocType | Type | Description |
|---------|------|-------------|
| Super Print Design | Document | Print template design |
| Super Print Design Item | Child Table | Cell definitions |
| Super Print Design Parameter | Child Table | Print parameter definitions |
| Super Print Design Query | Child Table | Query definitions |
| Super Print Paper | Document | Paper size management |
| Super Print Log | Document | Print operation log |
| Super Print Enabled Doctype | Child Table | Enabled doctype filter |
| Zprint Setting | Single Doc | Global print designer settings |

## Installation

```bash
cd /home/frappe/frappe-bench
bench get-app zhiz_print <repo-url>
bench --site <site-name> install-app zhiz_print
```

## Dependencies

### Python Dependencies

- frappe >= 15.0.0
- qrcode (QR code generation)
- beautifulsoup4 (PDF border fixes)
- cssutils (Excel CSS parsing)
- openpyxl (Excel generation)

### PDF Engines (choose one)

| Engine | Description | Installation |
|--------|-------------|-------------|
| **wkhtmltopdf** (default) | Uses wkhtmltopdf binary via pdfkit | `sudo apt install wkhtmltopdf` + `pip install pdfkit` |
| **WeasyPrint** | Pure Python PDF rendering, best CSS support | `pip install weasyprint`, requires system deps: `sudo apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev libcairo2` |
| **Chromium** | Headless browser PDF generation, most accurate rendering | Install Chromium/Chrome: `sudo apt install chromium-browser` or download Google Chrome |

Select the engine in **Zprint Setting** under "PDF Engine Mode".

## Usage

1. Go to **Zprint Setting** and enable the super print page
2. Define paper sizes in **Super Print Paper**
3. Design print templates in **Super Print Design**
4. Print any document to see the custom template selector on the left sidebar
