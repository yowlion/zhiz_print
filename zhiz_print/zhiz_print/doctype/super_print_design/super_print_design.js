// Super Print Designer - Frontend Designer
// Ported from QmSheetDesigner and extended

class SuperPrintDesigner {
    constructor(frm) {
        this.frm = frm;
        this.designContainerId = `spd-${Date.now()}`;
        this.currentCell = null;
        this.grid = [];
        this.cellDataMap = {};
        this.pages = {};  // {1: {grid, cellDataMap}, 2: {...}}
        this.pageCount = frm.doc.page_count || 1;
        this.currentPageNo = 1;
        this.selectedCells = [];
        this.formatPainterActive = false;
        this.formatPainterSourceCss = null;
        this.formatPainterPainting = false;
        this.formatPainterLastPainted = null;

        this.rows = frm.doc.rows || 20;
        this.cols = frm.doc.columns || 15;

        this.rowStyles = {};
        this.colStyles = {};
        this.tableCss = {};

        this.paperWidth = 0;
        this.paperHeight = 0;
        this.fontFamily = frm.doc.font_family || 'Microsoft YaHei';
        this.fontSize = frm.doc.font_size || 12;
        this.pageHeaderLeft = '';
        this.pageHeaderCenter = '';
        this.pageHeaderRight = '';
        this.pageFooterLeft = '';
        this.pageFooterCenter = '';
        this.pageFooterRight = '';

        this.selectionMode = 'cell';
        this.selectedRow = null;
        this.selectedCol = null;
        this.lastActiveTab = 'style';

        this.fontFamilies = [
            { value: 'Microsoft YaHei', label: 'Microsoft YaHei' },
            { value: 'SimSun', label: 'SimSun' },
            { value: 'SimHei', label: 'SimHei' },
            { value: 'KaiTi', label: 'KaiTi' },
            { value: 'FangSong', label: 'FangSong' }
        ];

        this.cellTypes = [
            { value: 'static', label: __('Static Text'), icon: 'fa-font' },
            { value: 'logic', label: __('Logic Code'), icon: 'fa-code' },
            { value: 'data_query', label: __('Data Query'), icon: 'fa-search' },
            { value: 'barcode', label: __('Barcode'), icon: 'fa-barcode' },
            { value: 'qrcode', label: __('QR Code'), icon: 'fa-qrcode' },
            { value: 'image', label: __('Image'), icon: 'fa-image' }
        ];

        this.barcodeFormats = [
            { value: 'CODE128', label: 'CODE128' },
            { value: 'CODE39', label: 'CODE39' }
        ];
    }

    init() {
        this.initGrid();
        this.loadExistingDesign();
    }

    async loadPaperSize() {
        if (this.frm.doc.print_paper) {
            try {
                const r = await Promise.resolve(
                    frappe.db.get_value("Super Print Paper", this.frm.doc.print_paper,
                        ["width", "height", "margin_top", "margin_bottom", "margin_left", "margin_right"])
                );
                if (r && r.message) {
                    this.paperWidth = parseFloat(r.message.width) || 210;
                    this.paperHeight = parseFloat(r.message.height) || 297;
                    this.marginTop = parseInt(r.message.margin_top) || 10;
                    this.marginBottom = parseInt(r.message.margin_bottom) || 10;
                    this.marginLeft = parseInt(r.message.margin_left) || 15;
                    this.marginRight = parseInt(r.message.margin_right) || 15;
                }
            } catch (e) {
                console.error('Failed to load paper size:', e);
            }
        }
        if (!this.paperWidth) this.paperWidth = 210;
        if (!this.paperHeight) this.paperHeight = 297;
        if (this.marginTop === undefined) this.marginTop = 10;
        if (this.marginBottom === undefined) this.marginBottom = 10;
        if (this.marginLeft === undefined) this.marginLeft = 15;
        if (this.marginRight === undefined) this.marginRight = 15;
    }

    async loadDesignFromServer() {
        if (!this.frm.doc.__islocal && this.frm.doc.name) {
            try {
                const r = await frappe.call({
                    method: 'zhiz_print.api.print_designer.load_design_data',
                    args: { design_name: this.frm.doc.name }
                });
                if (r.message) {
                    return r.message;
                }
            } catch (e) {
                console.errfor('Failed to load design from server:', e);
            }
        }
        return null;
    }

    initGrid() {
        this.grid = Array(this.rows).fill().map(() => Array(this.cols).fill(null));
    }

    getDefaultCellCss() {
        return 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;';
    }

    isMergedMark(value) {
        if (!value) return false;
        const str = value.toString().trim();
        return str.startsWith("||MERGED::") && str.endsWith("||");
    }

    extractMasterId(value) {
        if (!this.isMergedMark(value)) return null;
        const str = value.toString().trim();
        return str.slice(10, -2);
    }

    // 统一 cell_id 为 P{页}_R{行}C{列}:兼容历史单页格式 R{r}C{c}、
    // 多页主格 P{p}_R{r}C{c}、占位格 P{p}R{r}C{c}(无下划线)。
    // 单页即 page 1,R1C1 与 P1_R1C1 等价 → 统一成 P1_R1C1。
    // 这样 currentCell(渲染坐标)、cell_id、cellDataMap key 三者天然对齐,单页/多页同一套逻辑。
    _normalizeCellId(rawId, pageNo) {
        const s = (rawId || '').toString().trim();
        const m = s.match(/R(\d+)C(\d+)/);
        if (!m) return `P${pageNo || 1}_R1C1`;
        const pm = s.match(/^P(\d+)_?R/);
        const p = pm ? parseInt(pm[1]) : (pageNo || 1);
        return `P${p}_R${m[1]}C${m[2]}`;
    }

    loadExistingDesign(serverData) {
        this.initGrid();
        this.cellDataMap = {};
        this.pages = {};
        this.currentPageNo = 1;
        const defaultCellStyle = this.getDefaultCellCss();

        if (serverData) {
            // Use server-parsed data
            this.rows = serverData.rows || this.rows;
            this.cols = serverData.columns || this.cols;
            this.pageCount = serverData.page_count || 1;
            this.fontFamily = serverData.font_family || this.fontFamily;
            this.fontSize = serverData.font_size || this.fontSize;
            this.rowStyles = serverData.row_styles || {};
            this.colStyles = serverData.col_styles || {};
            this.pageHeaderLeft = serverData.page_header_left || '';
            this.pageHeaderCenter = serverData.page_header_center || '';
            this.pageHeaderRight = serverData.page_header_right || '';
            this.pageFooterLeft = serverData.page_footer_left || '';
            this.pageFooterCenter = serverData.page_footer_center || '';
            this.pageFooterRight = serverData.page_footer_right || '';
            this.pageHeaderAlign = serverData.page_header_align || 'Center';
            this.pageFooterAlign = serverData.page_footer_align || 'Center';

            // Load paper info from server data
            if (serverData.paper) {
                this.paperWidth = parseFloat(serverData.paper.width) || this.paperWidth;
                this.paperHeight = parseFloat(serverData.paper.height) || this.paperHeight;
                this.marginTop = parseInt(serverData.paper.margin_top) || this.marginTop;
                this.marginBottom = parseInt(serverData.paper.margin_bottom) || this.marginBottom;
                this.marginLeft = parseInt(serverData.paper.margin_left) || this.marginLeft;
                this.marginRight = parseInt(serverData.paper.margin_right) || this.marginRight;
            }

            // Init pages dict with correct dimensions
            for (let p = 1; p <= this.pageCount; p++) {
                this.pages[p] = {
                    grid: Array(this.rows).fill().map(() => Array(this.cols).fill(null)),
                    cellDataMap: {},
                };
            }

            // Parse cells from server, grouped by page_no
            if (serverData.cells && serverData.cells.length > 0) {
                // 第一遍:放真实 cell + 占位格,cell_id/master_cell_id 统一归一化为 P{页}_R{行}C{列}
                serverData.cells.forEach(cell => {
                    const pageNo = parseInt(cell.page_no) || 1;
                    if (!this.pages[pageNo]) {
                        this.pages[pageNo] = {
                            grid: Array(this.rows).fill().map(() => Array(this.cols).fill(null)),
                            cellDataMap: {},
                        };
                    }
                    const page = this.pages[pageNo];
                    const rowIndex = cell.row - 1;
                    const colIndex = cell.col - 1;
                    if (rowIndex < 0 || rowIndex >= this.rows || colIndex < 0 || colIndex >= this.cols) return;

                    if (cell.is_merged) {
                        const masterId = this._normalizeCellId(cell.master_cell_id, pageNo);
                        const childId = this._normalizeCellId(cell.cell_id, pageNo);
                        page.grid[rowIndex][colIndex] = {
                            _merged: true,
                            master_cell_id: masterId
                        };
                        // 保留 cellDataMap entry(打散时需通过 child cell_id 找 master)
                        page.cellDataMap[childId] = { _merged: true, master_cell_id: masterId, cell_id: childId };
                    } else {
                        const cellData = {
                            cell_id: this._normalizeCellId(cell.cell_id, pageNo),
                            page_no: pageNo,
                            row: cell.row, col: cell.col,
                            rowspan: parseInt(cell.rowspan) || 1,
                            colspan: parseInt(cell.colspan) || 1,
                            cell_type: cell.cell_type || 'static',
                            cell_value: cell.cell_value || '',
                            css_style: cell.css_style || '',
                            data_key: cell.data_key || '',
                            query_name: cell.query_name || '',
                            barcode_format: cell.barcode_format || 'CODE128',
                            barcode_width: parseInt(cell.barcode_width) || 100,
                            barcode_height: parseInt(cell.barcode_height) || 40,
                            row_type: cell.row_type || '',
                            row_display: cell.row_display || '',
                        };
                        page.grid[rowIndex][colIndex] = cellData;
                        page.cellDataMap[cellData.cell_id] = cellData;
                    }
                });
                // 第二遍:所有 cell 就位后统一标记合并区(避免占位格 cell_value=''
                // 被当普通 cell 加载后覆盖主格 _markMergedInPage 写下的 _merged 标记)
                for (const pn of Object.keys(this.pages)) {
                    const pg = this.pages[pn];
                    for (const id in pg.cellDataMap) {
                        const c = pg.cellDataMap[id];
                        if (c && !c._merged && (c.rowspan > 1 || c.colspan > 1)) {
                            this._markMergedInPage(pg, c);
                        }
                    }
                }
            }
            // Active page = 1; load its grid/cellDataMap references
            this._activatePage(1);
        } else {
            // Fallback: parse from frm.doc (new document or server unavailable)
            if (this.frm.doc.row_styles) {
                try { this.rowStyles = JSON.parse(this.frm.doc.row_styles); } catch (e) { this.rowStyles = {}; }
            }
            if (this.frm.doc.col_styles) {
                try { this.colStyles = JSON.parse(this.frm.doc.col_styles); } catch (e) { this.colStyles = {}; }
            }
            if (this.frm.doc.font_family) {
                this.fontFamily = this.frm.doc.font_family;
            }
            this.pageCount = this.frm.doc.page_count || 1;
            this.pageHeaderLeft = this.frm.doc.page_header_left || '';
            this.pageHeaderCenter = this.frm.doc.page_header_center || '';
            this.pageHeaderRight = this.frm.doc.page_header_right || '';
            this.pageFooterLeft = this.frm.doc.page_footer_left || '';
            this.pageHeaderAlign = this.frm.doc.page_header_align || 'Center';
            this.pageFooterAlign = this.frm.doc.page_footer_align || 'Center';
            this.pageFooterCenter = this.frm.doc.page_footer_center || '';
            this.pageFooterRight = this.frm.doc.page_footer_right || '';

            // Init pages dict
            for (let p = 1; p <= this.pageCount; p++) {
                this.pages[p] = {
                    grid: Array(this.rows).fill().map(() => Array(this.cols).fill(null)),
                    cellDataMap: {},
                };
            }

            if (this.frm.doc.design_items && this.frm.doc.design_items.length > 0) {
                const sortedItems = [...this.frm.doc.design_items].sort((a, b) => {
                    const pa = parseInt(a.page_no) || 1;
                    const pb = parseInt(b.page_no) || 1;
                    if (pa !== pb) return pa - pb;
                    if (a.row !== b.row) return a.row - b.row;
                    return a.col - b.col;
                });

                // 第一遍:放真实 cell + 占位格,cell_id/master_cell_id 统一归一化为 P{页}_R{行}C{列}
                sortedItems.forEach(item => {
                    const pageNo = parseInt(item.page_no) || 1;
                    if (!this.pages[pageNo]) {
                        this.pages[pageNo] = {
                            grid: Array(this.rows).fill().map(() => Array(this.cols).fill(null)),
                            cellDataMap: {},
                        };
                    }
                    const page = this.pages[pageNo];
                    const rowIndex = item.row - 1;
                    const colIndex = item.col - 1;

                    if (rowIndex >= 0 && rowIndex < this.rows && colIndex >= 0 && colIndex < this.cols) {
                        if (this.isMergedMark(item.cell_value)) {
                            const masterId = this._normalizeCellId(this.extractMasterId(item.cell_value), pageNo);
                            const childId = this._normalizeCellId(item.cell_id, pageNo);
                            page.grid[rowIndex][colIndex] = {
                                _merged: true,
                                master_cell_id: masterId
                            };
                            page.cellDataMap[childId] = { _merged: true, master_cell_id: masterId, cell_id: childId };
                        } else {
                            const cellData = {
                                cell_id: this._normalizeCellId(item.cell_id, pageNo),
                                page_no: pageNo,
                                row: item.row, col: item.col,
                                rowspan: parseInt(item.rowspan) || 1,
                                colspan: parseInt(item.colspan) || 1,
                                cell_type: item.cell_type || 'static',
                                cell_value: item.cell_value || '',
                                css_style: item.css_style || '',
                                data_key: item.data_key || '',
                                query_name: item.query_name || '',
                                barcode_format: item.barcode_format || 'CODE128',
                                barcode_width: parseInt(item.barcode_width) || 100,
                                barcode_height: parseInt(item.barcode_height) || 40,
                                row_type: item.row_type || '',
                                row_display: item.row_display || '',
                            };
                            page.grid[rowIndex][colIndex] = cellData;
                            page.cellDataMap[cellData.cell_id] = cellData;
                        }
                    }
                });
                // 第二遍:所有 cell 就位后统一标记合并区(避免占位格 cell_value=''
                // 被当普通 cell 加载后覆盖主格 _markMergedInPage 写下的 _merged 标记)
                for (const pn of Object.keys(this.pages)) {
                    const pg = this.pages[pn];
                    for (const id in pg.cellDataMap) {
                        const c = pg.cellDataMap[id];
                        if (c && !c._merged && (c.rowspan > 1 || c.colspan > 1)) {
                            this._markMergedInPage(pg, c);
                        }
                    }
                }
            }
            this._activatePage(1);
        }

        // Fill empty cells for every page — inherit row_display / row_type from siblings
        // (these are row-level attributes stored per-cell; auto-fill cells must stay consistent
        // with user-configured siblings or the live preview / dropdown will read the wrong value)
        for (const pageNo of Object.keys(this.pages)) {
            const page = this.pages[pageNo];
            for (let row = 0; row < this.rows; row++) {
                let inheritedDisplay = '';
                let inheritedType = '';
                for (let col = 0; col < this.cols; col++) {
                    const c = page.grid[row][col];
                    if (c && !c._merged) {
                        if (!inheritedDisplay && c.row_display) inheritedDisplay = c.row_display;
                        if (!inheritedType && c.row_type) inheritedType = c.row_type;
                    }
                }
                for (let col = 0; col < this.cols; col++) {
                    if (!page.grid[row][col]) {
                        const cellId = `P${pageNo}_R${row + 1}C${col + 1}`;
                        const cellData = {
                            cell_id: cellId, page_no: parseInt(pageNo),
                            row: row + 1, col: col + 1,
                            rowspan: 1, colspan: 1, cell_type: 'static',
                            cell_value: '', css_style: defaultCellStyle,
                            row_type: inheritedType || '',
                            row_display: inheritedDisplay || ''
                        };
                        page.grid[row][col] = cellData;
                        page.cellDataMap[cellId] = cellData;
                    }
                }
            }
        }
    }

    _activatePage(pageNo) {
        const page = this.pages[pageNo];
        if (!page) return;
        this.currentPageNo = pageNo;
        this.grid = page.grid;
        this.cellDataMap = page.cellDataMap;
    }

    _markMergedInPage(page, cell) {
        const startRow = cell.row - 1;
        const startCol = cell.col - 1;
        const rowspan = cell.rowspan || 1;
        const colspan = cell.colspan || 1;
        for (let r = startRow; r < startRow + rowspan; r++) {
            for (let c = startCol; c < startCol + colspan; c++) {
                if (r === startRow && c === startCol) continue;
                if (r >= page.grid.length || c >= page.grid[0].length) continue;
                const existing = page.grid[r][c];
                if (existing && !existing._merged && existing.cell_id) {
                    // 不删 cellDataMap(打散时需通过 child cell_id 找 master),标 _merged
                    page.cellDataMap[existing.cell_id]._merged = true;
                    page.cellDataMap[existing.cell_id].master_cell_id = cell.cell_id;
                }
                page.grid[r][c] = { _merged: true, master_cell_id: cell.cell_id };
            }
        }
    }

    markMergedCells(cell) {
        const page = this.pages[this.currentPageNo];
        if (!page) {
            // Fallback to legacy behavior if pages dict not yet built
            this._markMergedInPage({ grid: this.grid, cellDataMap: this.cellDataMap }, cell);
            return;
        }
        this._markMergedInPage(page, cell);
    }

    async fetchDesignerHtml() {
        const PX_PER_MM = 4;
        const mTop = (this.marginTop || 0) * PX_PER_MM;
        const mBottom = (this.marginBottom || 0) * PX_PER_MM;
        const mLeft = (this.marginLeft || 0) * PX_PER_MM;
        const mRight = (this.marginRight || 0) * PX_PER_MM;

        const r = await frappe.call({
            method: 'zhiz_print.api.print_designer.get_designer_html',
            args: {
                design_name: this.frm.doc.__islocal ? null : (this.frm.doc.name || null),
                rows: this.rows,
                columns: this.cols,
                font_family: this.fontFamily,
                print_paper: this.frm.doc.print_paper || null,
                col_styles: JSON.stringify(this.colStyles),
            }
        });
        let html = r.message || '';

        // Fill dynamic content into server shell
        this.designContainerId = (html.match(/id="(spd-\d+)"/) || [])[1] || this.designContainerId;

        // Insert col headers, row headers, grid, header/footer into the shell
        html = html.replace(/<div class="super-zprint-col-headers" id="spd-col-headers"[^>]*><\/div>/,
            '<div class="super-zprint-col-headers" id="spd-col-headers" style="margin-left:' + this._getColHeaderOffset() + 'px;">' + this.generateColHeadersHtml() + '</div>');
        html = html.replace(/<div class="super-zprint-row-headers" id="spd-row-headers"[^>]*><\/div>/,
            '<div class="super-zprint-row-headers" id="spd-row-headers" style="margin-top:' + mTop + 'px;">' + this.generateRowHeadersHtml() + '</div>');
        html = html.replace(/<div id="spd-header-footer"><\/div>/,
            this.generateHeaderFooterHtml(mTop, mBottom, mLeft, mRight));
        html = html.replace(/<table class="spd-grid" id="spd-grid"[^>]*><\/table>/,
            '<table class="spd-grid" id="spd-grid" style="width:' + this._getTotalWidth() + 'px">' + this.generateGridHtml() + '</table>');

        return html;
    }

    _getTotalWidth() {
        let w = 0;
        for (let col = 1; col <= this.cols; col++) {
            w += (this.colStyles[col]?.width || 60);
        }
        return w;
    }

    _getColHeaderOffset() {
        const PX_PER_MM = 4;
        const mLeft = (this.marginLeft || 0) * PX_PER_MM;
        const mRight = (this.marginRight || 0) * PX_PER_MM;
        const paperW = this.paperWidth * PX_PER_MM;
        const contentAreaW = paperW - mLeft - mRight;
        const centeredOffset = Math.max(0, (contentAreaW - this._getTotalWidth()) / 2);
        return 22 + mLeft + centeredOffset;
    }

    generateHeaderFooterHtml(mTop, mBottom, mLeft, mRight) {
        let html = '';
        // Header area (left/center/right columns)
        if (mTop > 0) {
            const hl = this.pageHeaderLeft || '';
            const hc = this.pageHeaderCenter || '';
            const hr = this.pageHeaderRight || '';
            const hasContent = hl || hc || hr;
            const placeholder = '<span style="color:#ccc;font-size:10px;">' + __('Header Area') + '</span>';
            html += '<div class="spd-header-area" id="spd-header-area" style="' +
                'position:absolute;top:0;left:' + mLeft + 'px;right:' + mRight + 'px;height:' + mTop + 'px;' +
                'overflow:hidden;padding:2px 4px;' +
                'display:flex;align-items:' + ({'Top':'flex-start','Center':'center','Bottom':'flex-end'}[this.frm?.doc?.page_header_align||this.pageHeaderAlign||'Center']||'center') + ';' +
                'font-size:12px;color:#666;">';
            html += '<div style="flex:1;text-align:' + (this.pageHeaderLeftAlign||this.frm?.doc?.page_header_left_align || 'Left').toLowerCase() + ';padding-left:' + mLeft + 'px;">' + (hl || (hasContent ? '' : placeholder)) + '</div>';
            html += '<div style="flex:1;text-align:' + (this.pageHeaderCenterAlign||this.frm?.doc?.page_header_center_align || 'Center').toLowerCase() + ';">' + (hc || '') + '</div>';
            html += '<div style="flex:1;text-align:' + (this.pageHeaderRightAlign||this.frm?.doc?.page_header_right_align || 'Right').toLowerCase() + ';padding-right:' + mRight + 'px;">' + (hr || '') + '</div>';
            html += '</div>';
        }
        // Footer area (left/center/right columns)
        if (mBottom > 0) {
            const fl = this.pageFooterLeft || '';
            const fc = this.pageFooterCenter || '';
            const fr_ = this.pageFooterRight || '';
            const hasContent = fl || fc || fr_;
            const placeholder = '<span style="color:#ccc;font-size:10px;">' + __('Footer Area') + '</span>';
            html += '<div class="spd-footer-area" id="spd-footer-area" style="' +
                'position:absolute;bottom:0;left:' + mLeft + 'px;right:' + mRight + 'px;height:' + mBottom + 'px;' +
                'overflow:hidden;padding:2px 4px;' +
                'display:flex;align-items:' + ({'Top':'flex-start','Center':'center','Bottom':'flex-end'}[this.frm?.doc?.page_footer_align||this.pageFooterAlign||'Center']||'center') + ';' +
                'font-size:12px;color:#666;">';
            html += '<div style="flex:1;text-align:' + (this.pageFooterLeftAlign||this.frm?.doc?.page_footer_left_align || 'Left').toLowerCase() + ';padding-left:' + mLeft + 'px;">' + (fl || (hasContent ? '' : placeholder)) + '</div>';
            html += '<div style="flex:1;text-align:' + (this.pageFooterCenterAlign||this.frm?.doc?.page_footer_center_align || 'Center').toLowerCase() + ';">' + (fc || '') + '</div>';
            html += '<div style="flex:1;text-align:' + (this.pageFooterRightAlign||this.frm?.doc?.page_footer_right_align || 'Right').toLowerCase() + ';padding-right:' + mRight + 'px;">' + (fr_ || '') + '</div>';
            html += '</div>';
        }
        return html;
    }

    generateColHeadersHtml() {
        let html = '';
        for (let col = 1; col <= this.cols; col++) {
            const colStyle = this.colStyles[col] || {};
            const colWidth = colStyle.width || 60;
            const selectedClass = this.selectedCol === col ? 'super-zprint-col-header-selected' : '';
            html += '<div class="super-zprint-col-header-cell ' + selectedClass + '" data-col="' + col + '" style="width:' + colWidth + 'px; min-width:' + colWidth + 'px; max-width:' + colWidth + 'px;">' + col + '</div>';
        }
        return html;
    }

    generateRowHeadersHtml() {
        let html = '';
        for (let row = 1; row <= this.rows; row++) {
            const rowStyle = this.rowStyles[row] || {};
            const rowHeight = rowStyle.height || 20;
            const selectedClass = this.selectedRow === row ? 'super-zprint-row-header-selected' : '';

            // Row type indicator (scan first non-merged cell in row)
            let rowType = '';
            for (let c = 0; c < this.cols; c++) {
                const cell = this.grid[row - 1]?.[c];
                if (cell && !cell._merged) {
                    rowType = cell.row_type || '';
                    break;
                }
            }
            let typeIndicator = '';
            let typeClass = '';
            if (rowType === 'Repeat Title Row') {
                typeIndicator = '<span class="super-zprint-row-type-badge super-zprint-badge-repeat" title="' + __('Repeat Title Row') + '">T</span>';
                typeClass = ' super-zprint-row-header-repeat-title';
            } else if (rowType === 'Data-Driven Row') {
                typeIndicator = '<span class="super-zprint-row-type-badge super-zprint-badge-data" title="' + __('Data-Driven Row') + '">D</span>';
                typeClass = ' super-zprint-row-header-data-driven';
            }

            html += '<div class="super-zprint-row-header-cell ' + selectedClass + typeClass + '" data-row="' + row + '" style="height:' + rowHeight + 'px; min-height:' + rowHeight + 'px; max-height:' + rowHeight + 'px; line-height:' + rowHeight + 'px;">' + typeIndicator + row + '</div>';
        }
        return html;
    }

    generateGridHtml() {
        let html = '<colgroup>';
        for (let col = 1; col <= this.cols; col++) {
            html += '<col style="width:' + (this.colStyles[col]?.width || 60) + 'px">';
        }
        html += '</colgroup>';

        const occupied = Array(this.rows + 1).fill().map(() => Array(this.cols + 1).fill(false));
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                const cell = this.grid[r - 1]?.[c - 1];
                if (cell?._merged) occupied[r][c] = true;
            }
        }

        for (let row = 1; row <= this.rows; row++) {
            const rowStyle = this.rowStyles[row] || {};
            // Extract vertical-align from row css — only works on <td>, not <tr>
            let rowCss = rowStyle.css_style || '';
            let rowVa = '';
            const vaMatch = rowCss.match(/vertical-align\s*:\s*(top|middle|bottom)/);
            if (vaMatch) {
                rowVa = vaMatch[1];
                rowCss = rowCss.replace(/vertical-align\s*:\s*\w+\s*;?/, '').trim();
            }
            let rowStyleAttr = 'height:' + (rowStyle.height || 20) + 'px;';
            if (rowCss) rowStyleAttr += rowCss;
            // Row display effect — use getFirstNonMergedCell which prefers cells with
            // non-empty row_display, so auto-filled blank cells don't mask the row's actual setting
            const rowDisplayCell = this.getFirstNonMergedCell(row);
            const rowDisplay = rowDisplayCell?.row_display || '';
            if (rowDisplay === 'Fixed Height') rowStyleAttr += 'overflow:hidden;white-space:nowrap;';
            else if (rowDisplay === 'Auto Shrink Font') rowStyleAttr += 'overflow:hidden;';
            // Auto Wrap(空值)时按行间距设 line-height;其他模式 line-height:1
            const _lineSpacing = parseInt(rowStyle.line_spacing, 10) || 0;
            rowStyleAttr += (rowDisplay === '' && _lineSpacing > 0)
                ? 'line-height:calc(1em + ' + _lineSpacing + 'px);'
                : 'line-height:1;';
            const rowSelectedClass = this.selectedRow === row ? ' row-selected' : '';

            html += '<tr class="' + rowSelectedClass + '" style="' + rowStyleAttr + '">';
            for (let col = 1; col <= this.cols; col++) {
                if (occupied[row][col]) continue;
                const cell = this.grid[row - 1]?.[col - 1];
                const cellId = `P${this.currentPageNo}_R${row}C${col}`;
                const colSelectedClass = this.selectedCol === col ? ' col-selected' : '';

                if (cell && !cell._merged) {
                    const { rowspan = 1, colspan = 1, cell_type, cell_value, css_style } = cell;
                    const rs = rowspan > 1 ? ' rowspan="' + rowspan + '"' : '';
                    const cs = colspan > 1 ? ' colspan="' + colspan + '"' : '';

                    let cellStyle = 'line-height:inherit;';
                    let fontSize = rowStyle.font_size || this.fontSize;
                    cellStyle += 'font-size:' + fontSize + 'px;';
                    if (rowVa) cellStyle += 'vertical-align:' + rowVa + ';';
                    if (css_style) {
                        let _cs = css_style.trim();
                        if (_cs && !_cs.endsWith(';')) _cs += ';';
                        cellStyle += _cs;
                    }

                    // Auto Shrink Font preview
                    if (rowDisplay === 'Auto Shrink Font' && cell_value) {
                        let cellH = 0;
                        for (let rr = row; rr < row + rowspan; rr++) cellH += (this.rowStyles[rr]?.height || 20);
                        let cellW = 0;
                        for (let cc = col; cc < col + colspan; cc++) cellW += (this.colStyles[cc]?.width || 60);
                        const shrunk = this._estimateFontSize(cell_value, cellW, cellH, fontSize);
                        if (shrunk < fontSize) {
                            cellStyle = cellStyle.replace('font-size:' + fontSize + 'px;', 'font-size:' + shrunk + 'px;');
                        }
                        cellStyle += 'overflow:hidden;';
                    }

                    const typeInfo = this.cellTypes.find(t => t.value === cell_type) || this.cellTypes[0];
                    const hasValue = cell_value && cell_value.trim();

                    let content = '';
                    if ((cell_type === 'data_query' || cell_type === 'image') && cell.query_name && cell.data_key) {
                        content = '<div class="super-zprint-cell-content" style="color:#6a5acd;font-style:italic;">{' + this.escapeHtml(cell.query_name) + '.' + this.escapeHtml(cell.data_key) + '}</div>';
                    } else if (cell_type === 'barcode' || cell_type === 'qrcode') {
                        content = '<div class="super-zprint-cell-preview"><i class="fa ' + typeInfo.icon + '" style="font-size:16px;color:#666"></i><span>' + typeInfo.label + '</span></div>';
                    } else if (!hasValue) {
                        content = '';
                    } else {
                        content = '<div class="super-zprint-cell-content">' + this.escapeHtml(cell_value).replace(/ {2,}/g, m => '&nbsp;'.repeat(m.length)).replace(/\r\n|\r|\n/g, '<br>') + '</div>';
                    }

                    const selectedClass = this.currentCell === cellId ? ' selected' : '';
                    html += '<td class="spd-cell ' + cell_type + selectedClass + colSelectedClass + '" data-cell-id="' + cellId + '" data-row="' + row + '" data-col="' + col + '" style="' + cellStyle + '"' + rs + cs + '>' + content + '</td>';

                    for (let r = row; r < row + rowspan; r++) {
                        for (let c = col; c < col + colspan; c++) {
                            if (r <= this.rows && c <= this.cols) occupied[r][c] = true;
                        }
                    }
                } else {
                    let cellStyle = 'line-height:inherit;';
                    if (rowStyle.font_size) cellStyle += 'font-size:' + rowStyle.font_size + 'px;';
                    if (rowVa) cellStyle += 'vertical-align:' + rowVa + ';';
                    const colStyle = this.colStyles[col] || {};
                    if (colStyle.css_style) cellStyle += colStyle.css_style;
                    html += '<td class="spd-cell empty' + colSelectedClass + '" data-cell-id="' + cellId + '" data-row="' + row + '" data-col="' + col + '" style="' + cellStyle + '"></td>';
                    occupied[row][col] = true;
                }
            }
            html += '</tr>';
        }
        return html;
    }

    // ===== 客户端实测分页(与 print.js render_preview 一致;演示预览用,避免后端估算遮挡) =====
    _sp_timeout(ms) { return new Promise(r => setTimeout(r, ms)); }

    async _sp_measure_row_heights(measurement_html, content_w_px) {
        const mframe = document.createElement('iframe');
        mframe.style.cssText = 'position:absolute;left:-99999px;top:0;width:' + (content_w_px || 800) + 'px;height:0;border:0;opacity:0;pointer-events:none;';
        document.body.appendChild(mframe);
        try {
            const mdoc = mframe.contentDocument || mframe.contentWindow.document;
            mdoc.open(); mdoc.write(measurement_html); mdoc.close();
            if (mdoc.fonts && mdoc.fonts.ready) { await Promise.race([mdoc.fonts.ready, this._sp_timeout(2000)]); } else { await this._sp_timeout(300); }
            await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
            // Auto Shrink 精确字号(measureText)应用到 iframe cell,让行高基于精确字号
            const shrinks = {};
            const fontFamily = this.fontFamily || 'Microsoft YaHei';
            const _ctx = document.createElement('canvas').getContext('2d');
            mdoc.querySelectorAll('td[data-shrink-cell]').forEach(td => {
                const key = td.dataset.shrinkCell; const baseFs = parseInt(td.dataset.baseFs, 10) || 12;
                const cellW = parseInt(td.dataset.cellW, 10) || 0; const text = (td.textContent || '').trim();
                if (!key || !text || !cellW) return;
                _ctx.font = baseFs + 'px ' + fontFamily;
                const textW = _ctx.measureText(text).width;
                if (textW > cellW) {
                    const fs = Math.max(6, Math.floor(baseFs * cellW / textW) - 1);
                    if (fs < baseFs) { td.style.fontSize = fs + 'px'; shrinks[key] = fs; }
                }
            });
            const heights = {}, kinds = {}, order = {}, links = {};
            mdoc.querySelectorAll('tr[data-serial]').forEach(tr => {
                const pg = parseInt(tr.dataset.pg, 10); const serial = parseInt(tr.dataset.serial, 10);
                if (!heights[pg]) { heights[pg] = {}; kinds[pg] = {}; order[pg] = []; links[pg] = []; }
                heights[pg][serial] = tr.offsetHeight; kinds[pg][serial] = tr.dataset.kind || 'data'; order[pg].push(serial);
                tr.querySelectorAll('td[rowspan]').forEach(td => { const rs = parseInt(td.getAttribute('rowspan') || '1', 10); if (rs > 1) links[pg].push([serial, rs]); });
            });
            return { heights, kinds, order, links, shrinks };
        } finally { if (mframe.parentNode) mframe.parentNode.removeChild(mframe); }
    }

    _sp_compute_break_map(measured, msg) {
        const { heights, kinds, order, links } = measured;
        const content_h_px = msg.content_h_px; const page_count = msg.page_count || Object.keys(heights).length || 1;
        const page_break_map = {}; const row_heights = {};
        for (let pg = 1; pg <= page_count; pg++) {
            const H = heights[pg]; if (!H) continue;
            const ord = order[pg] || [];
            const titleSerials = ord.filter(s => kinds[pg][s] === 'title');
            const dataSerials = ord.filter(s => kinds[pg][s] !== 'title');
            const titleH = titleSerials.reduce((a, s) => a + (H[s] || 0), 0);
            const rh = {}; ord.forEach(s => { rh[s] = H[s] || 0; }); row_heights[pg] = rh;
            const groupOf = this._sp_build_rowspan_groups(dataSerials, links[pg] || []);
            const avail = content_h_px - titleH - 1;
            const pages = []; let cur = [], curH = 0, consumed = new Set();
            for (let i = 0; i < dataSerials.length; i++) {
                const s = dataSerials[i]; if (consumed.has(s)) continue;
                const grp = groupOf[s] || [s]; grp.forEach(g => consumed.add(g));
                const grpH = grp.reduce((a, g) => a + (H[g] || 0), 0);
                if (cur.length && curH + grpH > avail) { pages.push(cur); cur = []; curH = 0; }
                cur = cur.concat(grp); curH += grpH;
            }
            if (cur.length) pages.push(cur);
            if (!pages.length) pages.push([]);
            page_break_map[pg] = pages;
        }
        return { page_break_map, row_heights, shrink_map: measured.shrinks || {} };
    }

    _sp_build_rowspan_groups(dataSerials, links) {
        const parent = {}; dataSerials.forEach(s => { parent[s] = s; });
        const find = x => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
        const union = (a, b) => { parent[find(a)] = find(b); };
        links.forEach(([start, span]) => { if (!parent.hasOwnProperty(start)) return; for (let k = 1; k < span; k++) { const nxt = start + k; if (parent.hasOwnProperty(nxt)) union(start, nxt); } });
        const byRoot = {}; dataSerials.forEach(s => { const r = find(s); (byRoot[r] = byRoot[r] || []).push(s); });
        const groupOf = {}; dataSerials.forEach(s => { groupOf[s] = byRoot[find(s)]; });
        return groupOf;
    }

    alignRowHeaders() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const rowHeaders = container.querySelector('#spd-row-headers');
        if (rowHeaders) {
            const PX_PER_MM = 4;
            const mTop = (this.marginTop || 0) * PX_PER_MM;
            rowHeaders.style.marginTop = mTop + 'px';
        }
    }

    bindEvents() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        container.querySelector('#spd-apply-grid')?.addEventListener('click', () => this.applyGridSize());
        container.querySelector('#spd-insert-row-btn')?.addEventListener('click', () => this.insertRowAt(this.selectedRow));
        container.querySelector('#spd-delete-row-btn')?.addEventListener('click', () => this.deleteRowAt(this.selectedRow));
        container.querySelector('#spd-insert-col-btn')?.addEventListener('click', () => this.insertColAt(this.selectedCol));
        container.querySelector('#spd-delete-col-btn')?.addEventListener('click', () => this.deleteColAt(this.selectedCol));
        container.querySelector('#spd-font')?.addEventListener('change', (e) => {
            this.fontFamily = e.target.value;
            this.frm.set_value('font_family', this.fontFamily);
            this.refreshGrid();
            this.frm.dirty();
        });
        container.querySelector('#spd-clear-btn')?.addEventListener('click', () => this.clearDesign());
        container.querySelector('#spd-query-btn')?.addEventListener('click', () => this.showQueryDefinitionDialog());
        container.querySelector('#spd-params-btn')?.addEventListener('click', () => this.showParamsDialog());
        container.querySelector('#spd-repeat-title-btn')?.addEventListener('click', () => this.setRowType('Repeat Title Row'));
        container.querySelector('#spd-data-driven-btn')?.addEventListener('click', () => this.setRowType('Data-Driven Row'));
        container.querySelector('#spd-normal-row-btn')?.addEventListener('click', () => this.setRowType(''));
        container.querySelector('#btn-unmerge-left')?.addEventListener('click', () => this._doUnmerge(false));
        container.querySelector('#btn-unmerge-inherit')?.addEventListener('click', () => this._doUnmerge(true));

        // Multi-page tab events
        container.querySelector('#spd-add-page-btn')?.addEventListener('click', () => this.addPage());
        container.querySelector('#spd-duplicate-page-btn')?.addEventListener('click', () => this.duplicatePage());
        container.querySelector('#spd-paper-setting-btn')?.addEventListener('click', () => {
            const pname = this.frm.doc.print_paper;
            if (!pname) { frappe.msgprint(__('请先在设计中设置纸张')); return; }
            const dlg = new frappe.ui.Dialog({ title: __('纸张设置') + ' - ' + pname });
            dlg.$body.html('<iframe src="/app/super-print-paper/' + encodeURIComponent(pname) + '" style="width:100%;height:70vh;border:0;"></iframe>');
            dlg.$wrapper.find('.modal-dialog').css('max-width', '900px');
            dlg.show();
        });
        container.querySelector('#spd-remove-page-btn')?.addEventListener('click', () => this.removePage(this.currentPageNo));
        const tabsEl = container.querySelector('#spd-page-tabs');
        if (tabsEl) {
            tabsEl.addEventListener('click', (e) => {
                const tab = e.target.closest('.spd-page-tab');
                if (!tab) return;
                this.switchPage(tab.dataset.page);
                this.showPageProperties();
            });
        }
        this.renderPageTabs();

        // Delegated click events
        container.addEventListener('click', (e) => {
            const colHeader = e.target.closest('.super-zprint-col-header-cell');
            if (colHeader) {
                const col = parseInt(colHeader.dataset.col);
                if (this.formatPainterActive) {
                    this.paintFormatToCol(col);
                    return;
                }
                this.handleColClick(col);
                return;
            }
            const rowHeader = e.target.closest('.super-zprint-row-header-cell');
            if (rowHeader) {
                const row = parseInt(rowHeader.dataset.row);
                if (this.formatPainterActive) {
                    this.paintFormatToRow(row);
                    return;
                }
                this.handleRowClick(row);
                return;
            }
            const cell = e.target.closest('.spd-cell');
            if (cell) {
                if (this.formatPainterActive) {
                    this.paintFormatToCell(cell.dataset.cellId);
                    return;
                }
                this.handleCellClick(cell.dataset.cellId);
            } else if (!e.target.closest('.spd-props,.spd-toolbar,.spd-page-bar,button,input,select,textarea')) {
                const paper = container.querySelector('#spd-paper');
                let preferTab = 'header';
                if (paper) {
                    const rect = paper.getBoundingClientRect();
                    preferTab = (e.clientY - rect.top) < rect.height / 2 ? 'header' : 'footer';
                }
                this.showPageProperties();
                const tabBtn = container.querySelector('.super-zprint-prop-tab[data-tab="' + preferTab + '"]');
                if (tabBtn) tabBtn.click();
            }
        });

        // Format painter: drag painting
        container.addEventListener('mousedown', (e) => {
            if (!this.formatPainterActive) return;
            const cell = e.target.closest('.spd-cell');
            if (!cell) return;
            e.preventDefault();
            this.formatPainterPainting = true;
            this.formatPainterLastPainted = null;
            this.paintFormatToCell(cell.dataset.cellId);
        });
        container.addEventListener('mousemove', (e) => {
            if (!this.formatPainterActive || !this.formatPainterPainting) return;
            const cell = e.target.closest('.spd-cell');
            if (cell) this.paintFormatToCell(cell.dataset.cellId);
        });
        this._fpMouseUp = () => {
            if (!this.formatPainterActive || !this.formatPainterPainting) return;
            this.formatPainterPainting = false;
            this.formatPainterLastPainted = null;
            // Clear painted highlights then full refresh
            const container = document.getElementById(this.designContainerId);
            if (container) {
                container.querySelectorAll('.spd-cell.fp-painted').forEach(el => {
                    el.classList.remove('fp-painted');
                });
            }
            this.refreshGrid();
        };
        document.addEventListener('mouseup', this._fpMouseUp);
        this._fpEsc = (e) => {
            if (e.key === 'Escape' && this.formatPainterActive) this.deactivateFormatPainter();
        };
        document.addEventListener('keydown', this._fpEsc);

        setTimeout(() => {
            this.alignRowHeaders();
            this.syncRowHeaderHeights();
        }, 150);

        this.bindPropertyEvents();
    }

    _setToolbarState(mode) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const allBtns = ['spd-rows','spd-cols','spd-apply-grid','spd-font','spd-insert-row-btn','spd-delete-row-btn','spd-insert-col-btn','spd-delete-col-btn','btn-unmerge-left','btn-unmerge-inherit','spd-repeat-title-btn','spd-data-driven-btn','spd-normal-row-btn'];
        const modeMap = {
            'none': [],
            'page': ['spd-rows','spd-cols','spd-apply-grid','spd-font'],
            'cell': ['btn-unmerge-left','btn-unmerge-inherit'],
            'row': ['spd-insert-row-btn','spd-delete-row-btn','spd-repeat-title-btn','spd-data-driven-btn','spd-normal-row-btn'],
            'col': ['spd-insert-col-btn','spd-delete-col-btn'],
        };
        const enabled = modeMap[mode] || [];
        allBtns.forEach(id => {
            const btn = container.querySelector('#' + id);
            if (!btn) return;
            const on = enabled.includes(id);
            btn.disabled = !on;
            btn.style.opacity = on ? '' : '0.4';
            btn.style.cursor = on ? '' : 'not-allowed';
            btn.style.display = '';
        });
        const rtc = container.querySelector('.spd-row-type-controls');
        if (rtc) rtc.style.display = (mode === 'row') ? '' : 'none';
    }

    handleCellClick(cellId) {
        this.selectionMode = 'cell';
        this.selectedRow = null;
        this.selectedCol = null;
        this.currentCell = cellId;
        const container = document.getElementById(this.designContainerId);
        this._setToolbarState('cell');
        const [row, col] = this.parseCellId(cellId);
        const cell = this.grid[row - 1]?.[col - 1];
        // 非合并单元格(rowspan/colspan都<=1)禁用打散
        const _isMerged = cell && (cell.rowspan > 1 || cell.colspan > 1);
        if (!_isMerged) {
            ['btn-unmerge-left', 'btn-unmerge-inherit'].forEach(id => {
                const btn = container.querySelector('#' + id);
                if (btn) { btn.disabled = true; btn.style.opacity = '0.4'; btn.style.cursor = 'not-allowed'; }
            });
        }

        if (cell?._merged) return;
        if (!cell) this.createCellData(row, col);
        this.renderCellProperties(cellId);
        container?.querySelector('.spd-props')?.classList.add('visible');
        this.refreshGrid();
    }

    handleRowClick(row) {
        this.selectionMode = 'row';
        this.selectedRow = row;
        this.selectedCol = null;
        this.currentCell = null;
        this.selectedCells = [];
        const container = document.getElementById(this.designContainerId);
        container?.querySelector('.spd-props')?.classList.add('visible');
        this._setToolbarState('row');
        this.renderRowProperties(row);
        this.refreshGrid();
    }

    handleColClick(col) {
        this.selectionMode = 'col';
        this.selectedCol = col;
        this.selectedRow = null;
        this.currentCell = null;
        const container = document.getElementById(this.designContainerId);
        this.selectedCells = [];
        this._setToolbarState('col');
        this.renderColProperties(col);
        container?.querySelector('.spd-props')?.classList.add('visible');
        this.refreshGrid();
    }

    // ==================== Row Property Panel ====================

    renderRowProperties(row) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const rowStyle = this.rowStyles[row] || {};
        const _firstCellForRow = this.getFirstNonMergedCell(row);
        const rowType = _firstCellForRow?.row_type || '';
        const rowDisplay = _firstCellForRow?.row_display || '';
        let cssPreview = '';
        if (rowStyle.height) cssPreview += 'height:' + rowStyle.height + 'px; ';
        if (rowStyle.font_size) cssPreview += 'font-size:' + rowStyle.font_size + 'px; ';
        if (rowStyle.css_style) cssPreview += rowStyle.css_style;

        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-arrows-v"></i> ' + __('Row Style Settings') + ' <small style="color:#6c757d;font-weight:normal">(' + __('Row') + ' ' + row + ')</small>';
        }

        let formHtml = '<form id="row-property-form" class="property-form">' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-expand"></i> ' + __('Row Dimensions') + '</div>' +
                '<div class="property-section-body" style="padding:3px 8px">' +
                    '<div class="super-zprint-layout-controls">' +
                        '<div class="super-zprint-layout-control-group">' +
                            '<label style="font-size:9px">' + __('Row Height') + ':</label>' +
                            '<div class="super-zprint-number-spinner super-zprint-number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="row-height" data-step="5">-</button>' +
                                '<input type="number" id="row-height" class="form-control super-zprint-spin-input" value="' + (rowStyle.height || '') + '" min="1" max="500" step="1" placeholder="20">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="row-height" data-step="5">+</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="super-zprint-layout-control-group">' +
                            '<label style="font-size:9px">' + __('Font') + ':</label>' +
                            '<div class="super-zprint-number-spinner super-zprint-number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="row-font-size" data-step="1">-</button>' +
                                '<input type="number" id="row-font-size" class="form-control super-zprint-spin-input" value="' + (rowStyle.font_size || '') + '" min="8" max="36" step="1" placeholder="12">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="row-font-size" data-step="1">+</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-bars"></i> ' + __('Text Alignment') + '</div>' +
                '<div class="property-section-body" style="padding:3px 8px">' +
                    '<label style="font-size:9px;margin-bottom:4px">' + __('Vertical Align') + ':</label>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" style="font-size:9px" data-align="top" title="' + __('Top Align') + '"><i class="fa fa-arrow-up"></i> ' + __('Top') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" style="font-size:9px" data-align="middle" title="' + __('Center') + '"><i class="fa fa-arrows-v"></i> ' + __('Center') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" style="font-size:9px" data-align="bottom" title="' + __('Bottom Align') + '"><i class="fa fa-arrow-down"></i> ' + __('Bottom') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default" id="row-format-painter-btn" title="' + __('Format Painter') + '" style="width:auto;padding:0 6px;font-size:9px;margin-left:8px"><i class="fa fa-paint-brush"></i> ' + __('Format Painter') + '</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-tag"></i> ' + __('Row Type') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<select id="row-type-select" class="form-control">' +
                        '<option value="">' + __('Normal Row') + '</option>' +
                        '<option value="Repeat Title Row">' + __('Repeat Title Row') + '</option>' +
                        '<option value="Data-Driven Row">' + __('Data-Driven Row') + '</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section" id="spd-row-sort-section" style="display:' + (rowType === 'Data-Driven Row' ? '' : 'none') + '">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-sort"></i> ' + __('Data Sort') + ' <small style="color:#6c757d;font-weight:normal">(' + __('Data-Driven Row') + ')</small></div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<input type="text" id="row-sorts" class="form-control input-sm" value="' + (rowStyle.sorts || '') + '" placeholder="row.3 ASC, row.5 DESC">' +
                    '<div style="font-size:9px;color:#6c757d;margin-top:4px">' + __('row.N = display value of column N. Comma = multi-level. Empty = natural order.') + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-text-height"></i> ' + __('Row Display Effect') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<select id="row-display-select" class="form-control">' +
                        '<option value="">' + __('Auto Wrap') + '</option>' +
                        '<option value="Fixed Height">' + __('Fixed Height') + '</option>' +
                        '<option value="Auto Shrink Font">' + __('Auto Shrink Font') + '</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section" id="spd-row-spacing-section" style="display:' + (rowDisplay === '' ? '' : 'none') + '">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-arrows-v"></i> ' + __('Line Spacing') + ' <small style="color:#6c757d;font-weight:normal">(' + __('Auto Wrap') + ')</small></div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<div class="super-zprint-number-spinner super-zprint-number-spinner-sm">' +
                        '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="row-line-spacing" data-step="1">-</button>' +
                        '<input type="number" id="row-line-spacing" class="form-control super-zprint-spin-input" value="' + (rowStyle.line_spacing || '') + '" min="0" max="20" step="1" placeholder="0">' +
                        '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="row-line-spacing" data-step="1">+</button>' +
                    '</div>' +
                    '<div style="font-size:9px;color:#6c757d;margin-top:4px">' + __('Extra spacing between wrapped lines, 0-20px (0=default)') + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-paint-brush"></i> ' + __('Row Style') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px">' + __('CSS Style') + ':</label>' +
                    '<textarea id="row-css-style" class="form-control super-zprint-css-editor" rows="2" placeholder="background-color: #f0f0f0;">' + cssPreview.trim() + '</textarea>' +
                '</div>' +
            '</div>' +
        '</form>';

        container.querySelector('#spd-prop-form').innerHTML = formHtml;
        this.bindSpinnerButtons();
        this.bindRowPropertyEvents(row);
    }

    bindRowPropertyEvents(row) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const heightInput = container.querySelector('#row-height');
        const fontInput = container.querySelector('#row-font-size');
        const cssInput = container.querySelector('#row-css-style');

        if (heightInput) heightInput.addEventListener('change', (e) => {
            this.updateRowStyle(row, 'height', parseInt(e.target.value) || undefined);
        });
        if (fontInput) fontInput.addEventListener('change', (e) => {
            this.updateRowStyle(row, 'font_size', parseInt(e.target.value) || undefined);
        });
        if (cssInput) {
            cssInput.addEventListener('input', (e) => {
                this.updateRowStyle(row, 'css_style', e.target.value);
            });
            cssInput.addEventListener('change', (e) => {
                this.updateRowStyle(row, 'css_style', e.target.value);
            });
        }

        // Row data sort (per Data-Driven Row template)
        const sortsInput = container.querySelector('#row-sorts');
        if (sortsInput) {
            sortsInput.addEventListener('change', (e) => {
                this.updateRowStyle(row, 'sorts', e.target.value || undefined);
            });
        }

        // Row type selection
        const rowTypeSelect = container.querySelector('#row-type-select');
        if (rowTypeSelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowTypeSelect.value = firstNonMerged?.row_type || '';
            rowTypeSelect.addEventListener('change', (e) => {
                this.setRowTypeForRow(row, e.target.value);
                const sortSection = container.querySelector('#spd-row-sort-section');
                if (sortSection) sortSection.style.display = (e.target.value === 'Data-Driven Row') ? '' : 'none';
            });
        }
        // Row display effect selection
        const rowDisplaySelect = container.querySelector('#row-display-select');
        if (rowDisplaySelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowDisplaySelect.value = firstNonMerged?.row_display || '';
            rowDisplaySelect.addEventListener('change', (e) => {
                this.setRowDisplay(row, e.target.value);
                const spacingSection = container.querySelector('#spd-row-spacing-section');
                if (spacingSection) spacingSection.style.display = (e.target.value === '') ? '' : 'none';
            });
        }
        // Line spacing (Auto Wrap only)
        const lineSpacingInput = container.querySelector('#row-line-spacing');
        if (lineSpacingInput) {
            lineSpacingInput.addEventListener('change', (e) => {
                this.updateRowStyle(row, 'line_spacing', parseInt(e.target.value) || undefined);
            });
        }
        // Vertical alignment buttons
        container.querySelectorAll('.row-align-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.updateRowStyle(row, 'css_style',
                    this.setCssProperty(this.rowStyles[row]?.css_style || '', 'vertical-align', btn.dataset.align));
            });
        });
        // Row format painter button
        const rowFormatPainterBtn = container.querySelector('#row-format-painter-btn');
        if (rowFormatPainterBtn) {
            rowFormatPainterBtn.addEventListener('click', () => {
                this.formatPainterActive = true;
                this.formatPainterSourceCss = this.rowStyles[row]?.css_style || '';
                this.formatPainterPainting = false;
                this.formatPainterLastPainted = null;
                rowFormatPainterBtn.style.background = '#ff9800';
                rowFormatPainterBtn.style.color = '#fff';
                rowFormatPainterBtn.style.borderColor = '#ff9800';
                const grid = container?.querySelector('#spd-grid');
                if (grid) grid.classList.add('format-painter-cursor');
                frappe.show_alert({ message: __('Format Painter activated'), indicator: 'blue' });
            });
        }
    }

    // ==================== Page Properties (header/footer) ====================

    showPageProperties() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        this.selectionMode = 'page';
        this.selectedRow = null;
        this.selectedCol = null;
        this.currentCell = null;
        // 不调 refreshGrid(会重建 spd-header-area/spd-footer-area 导致 per-栏 align 重置为默认)
        container.querySelectorAll('.spd-cell-selected,.row-selected,.col-selected').forEach(el => el.classList.remove('spd-cell-selected','row-selected','col-selected'));
        this._setToolbarState('page');
        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-file-o"></i> ' + __('Page Settings') + ' <small style="color:#6c757d;font-weight:normal">(' + __('Page') + ' ' + this.currentPageNo + ')</small>';
        }
        const hAlign = this.frm?.doc?.page_header_align || this.pageHeaderAlign || 'Center';
        const fAlign = this.frm?.doc?.page_footer_align || this.pageFooterAlign || 'Center';
        const ph = __('Placeholders: {page} {pages} {now_date} {now_time} {date_time}');
        const _row = (id, label, val, aid, aval) => '<div style="display:flex;flex-direction:column;gap:2px;margin-bottom:4px"><label style="font-size:9px;margin:0">' + label + '对齐</label><select id="' + aid + '" class="form-control input-sm" style="width:100% !important;font-size:10px;padding:2px"><option value="Left"' + (aval==='Left'?' selected':'') + '>左对齐</option><option value="Center"' + (aval==='Center'?' selected':'') + '>居中对齐</option><option value="Right"' + (aval==='Right'?' selected':'') + '>右对齐</option></select><label style="font-size:9px;margin:0">' + label + '内容</label><textarea id="' + id + '" class="form-control input-sm" rows="1" placeholder="' + label + '内容" style="width:100% !important;font-size:11px;height:36px !important;padding:2px 4px !important;line-height:16px">' + (val || '') + '</textarea></div>';
        const _align = (id, cur) => '<select id="' + id + '" class="form-control input-sm" style="margin-bottom:6px">' +
            '<option value="Top"' + (cur==='Top'?' selected':'') + '>' + __('Top') + '</option>' +
            '<option value="Center"' + (cur==='Center'?' selected':'') + '>' + __('Center') + '</option>' +
            '<option value="Bottom"' + (cur==='Bottom'?' selected':'') + '>' + __('Bottom') + '</option></select>';
        const formHtml = '<form id="row-property-form" class="property-form">' +
            '<div style="display:flex">' +
                '<div class="super-zprint-prop-tab active" data-tab="header"><i class="fa fa-arrow-up"></i> ' + __('Page Header') + '</div>' +
                '<div class="super-zprint-prop-tab" data-tab="footer"><i class="fa fa-arrow-down"></i> ' + __('Page Footer') + '</div>' +
            '</div>' +
            '<div class="super-zprint-prop-tab-contents">' +
                '<div class="super-zprint-prop-tab-content active" data-tab="header">' +
                    '<div class="property-section-body" style="padding:8px">' +
                        '<label style="font-size:9px">' + __('Vertical Align') + ':</label>' + _align('page-header-align', hAlign) +
                        '<div style="display:flex;flex-direction:column;gap:4px">' +
                            _row('page-header-left', '左区', this.pageHeaderLeft, 'ph-la', this.pageHeaderLeftAlign||this.frm?.doc?.page_header_left_align || 'Left') +
                            _row('page-header-center', '中区', this.pageHeaderCenter, 'ph-ca', this.pageHeaderCenterAlign||this.frm?.doc?.page_header_center_align || 'Center') +
                            _row('page-header-right', '右区', this.pageHeaderRight, 'ph-ra', this.pageHeaderRightAlign||this.frm?.doc?.page_header_right_align || 'Right') +
                        '</div>' +
                        '<div style="font-size:9px;color:#6c757d;margin-top:4px">' + ph + '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-prop-tab-content" data-tab="footer">' +
                    '<div class="property-section-body" style="padding:8px">' +
                        '<label style="font-size:9px">' + __('Vertical Align') + ':</label>' + _align('page-footer-align', fAlign) +
                        '<div style="display:flex;flex-direction:column;gap:4px">' +
                            _row('page-footer-left', '左区', this.pageFooterLeft, 'pf-la', this.pageFooterLeftAlign||this.frm?.doc?.page_footer_left_align || 'Left') +
                            _row('page-footer-center', '中区', this.pageFooterCenter, 'pf-ca', this.pageFooterCenterAlign||this.frm?.doc?.page_footer_center_align || 'Center') +
                            _row('page-footer-right', '右区', this.pageFooterRight, 'pf-ra', this.pageFooterRightAlign||this.frm?.doc?.page_footer_right_align || 'Right') +
                        '</div>' +
                        '<div style="font-size:9px;color:#6c757d;margin-top:4px">' + ph + '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
        '</form>';
        container.querySelector('#spd-prop-form').innerHTML = formHtml;
        this.bindPagePropertyEvents();
        this._renderHeaderFooterPreview();
        container?.querySelector('.spd-props')?.classList.add('visible');
    }

    bindPagePropertyEvents() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        // 页眉/页脚 tab 切换
        container.querySelectorAll('.super-zprint-prop-tab').forEach(t => {
            t.addEventListener('click', () => {
                const tab = t.dataset.tab;
                container.querySelectorAll('.super-zprint-prop-tab').forEach(x => x.classList.remove('active'));
                t.classList.add('active');
                container.querySelectorAll('.super-zprint-prop-tab-content').forEach(c => c.classList.remove('active'));
                const target = container.querySelector('.super-zprint-prop-tab-content[data-tab="' + tab + '"]');
                if (target) target.classList.add('active');
            });
        });
        const fields = [
            ['#page-header-left', 'page_header_left', 'pageHeaderLeft'],
            ['#page-header-center', 'page_header_center', 'pageHeaderCenter'],
            ['#page-header-right', 'page_header_right', 'pageHeaderRight'],
            ['#page-footer-left', 'page_footer_left', 'pageFooterLeft'],
            ['#page-footer-center', 'page_footer_center', 'pageFooterCenter'],
            ['#page-footer-right', 'page_footer_right', 'pageFooterRight'],
        ];
        const _childMap = {
            pageHeaderLeft: '#spd-header-area > div:nth-child(1)',
            pageHeaderCenter: '#spd-header-area > div:nth-child(2)',
            pageHeaderRight: '#spd-header-area > div:nth-child(3)',
            pageFooterLeft: '#spd-footer-area > div:nth-child(1)',
            pageFooterCenter: '#spd-footer-area > div:nth-child(2)',
            pageFooterRight: '#spd-footer-area > div:nth-child(3)',
        };
        fields.forEach(([sel, docKey, memKey]) => {
            const el = container.querySelector(sel);
            if (el) el.addEventListener('change', (e) => {
                this[memKey] = e.target.value;
                const target = document.getElementById(this.designContainerId)?.querySelector(_childMap[memKey]);
                if (target) target.textContent = e.target.value;
                if (this.frm) this.frm.dirty();
            });
        });
        const _onAlignChange = (memKey, docKey, isHeader) => (e) => {
            this[memKey] = e.target.value;
            if (this.frm) this.frm.dirty();
            const _am = { 'Top': 'flex-start', 'Center': 'center', 'Bottom': 'flex-end' };
            const aid = isHeader ? '#spd-header-area' : '#spd-footer-area';
            const ael = document.getElementById(this.designContainerId)?.querySelector(aid);
            if (ael) ael.style.alignItems = _am[e.target.value] || 'center';
        };
        const hAlignSel = container.querySelector('#page-header-align');
        if (hAlignSel) hAlignSel.addEventListener('change', _onAlignChange('pageHeaderAlign', 'page_header_align', true));
        const fAlignSel = container.querySelector('#page-footer-align');
        if (fAlignSel) fAlignSel.addEventListener('change', _onAlignChange('pageFooterAlign', 'page_footer_align', false));
        // per-栏水平对齐: select change → 写 frm.doc + 实时更新画布对应栏 text-align
        const _hAligns = [
            ['#ph-la', 'page_header_left_align', '#spd-header-area > div:nth-child(1)'],
            ['#ph-ca', 'page_header_center_align', '#spd-header-area > div:nth-child(2)'],
            ['#ph-ra', 'page_header_right_align', '#spd-header-area > div:nth-child(3)'],
            ['#pf-la', 'page_footer_left_align', '#spd-footer-area > div:nth-child(1)'],
            ['#pf-ca', 'page_footer_center_align', '#spd-footer-area > div:nth-child(2)'],
            ['#pf-ra', 'page_footer_right_align', '#spd-footer-area > div:nth-child(3)'],
        ];
        _hAligns.forEach(([sel, docKey, targetSel]) => {
            const el = container.querySelector(sel);
            if (el) el.addEventListener('change', (e) => {
                if (this.frm) this.frm.dirty();
                const target = document.getElementById(this.designContainerId)?.querySelector(targetSel);
                if (target) target.style.textAlign = e.target.value.toLowerCase();
            });
        });
        // textarea/select change 后实时刷新画布的页眉页脚预览
        container.querySelectorAll('#page-header-left,#page-header-center,#page-header-right,#page-footer-left,#page-footer-center,#page-footer-right,#page-header-align,#page-footer-align').forEach(el => {
            el.addEventListener('change', () => this._renderHeaderFooterPreview());
        });
    }

    _renderHeaderFooterPreview() {
        const container = document.getElementById(this.designContainerId);
        const hf = container?.querySelector('#spd-header-footer');
        if (!hf) return;
        const ta = container.querySelector('.spd-table-area');
        const ml = container.querySelector('.spd-margin-line');
        const mTop = parseInt(ta?.style.top) || 0;
        const mLeft = parseInt(ta?.style.left) || 0;
        const mRight = parseInt(ta?.style.right) || 0;
        const mBottom = parseInt(ml?.style.bottom) || 0;
        const _am = { 'Top': 'flex-start', 'Center': 'center', 'Bottom': 'flex-end' };
        const d = this.frm?.doc || {};
        const hA = _am[d.page_header_align || this.pageHeaderAlign || 'Center'] || 'center';
        const fA = _am[d.page_footer_align || this.pageFooterAlign || 'Center'] || 'center';
        const _sec = (l, c, r) => `<div style="flex:1;text-align:left;padding:0 2px">${l||''}</div><div style="flex:1;text-align:center;padding:0 2px">${c||''}</div><div style="flex:1;text-align:right;padding:0 2px">${r||''}</div>`;
        hf.innerHTML =
            `<div style="position:absolute;top:0;left:${mLeft}px;right:${mRight}px;height:${mTop}px;display:flex;align-items:${hA};overflow:hidden;font-size:11px;color:#888;">` +
                _sec(d.page_header_left||this.pageHeaderLeft, d.page_header_center||this.pageHeaderCenter, d.page_header_right||this.pageHeaderRight) +
            `</div>` +
            `<div style="position:absolute;bottom:0;left:${mLeft}px;right:${mRight}px;height:${mBottom}px;display:flex;align-items:${fA};overflow:hidden;font-size:11px;color:#888;">` +
                _sec(d.page_footer_left||this.pageFooterLeft, d.page_footer_center||this.pageFooterCenter, d.page_footer_right||this.pageFooterRight) +
            `</div>`;
    }

    // ==================== Row Type / Row Display Effect ====================

    getFirstNonMergedCell(row) {
        // Pass 1: prefer a cell whose row_display is non-empty — auto-filled blank cells
        // (e.g. R{row}C1 under a rowspan) often carry empty row_display and would
        // otherwise make the dropdown / live preview read the wrong value.
        for (let c = 0; c < this.cols; c++) {
            const cell = this.grid[row - 1]?.[c];
            if (cell && !cell._merged && cell.row_display) return cell;
        }
        // Pass 2: fall back to any non-merged cell
        for (let c = 0; c < this.cols; c++) {
            const cell = this.grid[row - 1]?.[c];
            if (cell && !cell._merged) return cell;
        }
        return null;
    }

    setRowType(rowType) {
        if (this.selectionMode !== 'row' || !this.selectedRow) {
            frappe.show_alert({ message: __('Please click a row number on the left to select an entire row'), indicator: 'yellow' });
            return;
        }
        this.setRowTypeForRow(this.selectedRow, rowType);
    }

    setRowTypeForRow(row, rowType) {
        for (let col = 1; col <= this.cols; col++) {
            const cell = this.grid[row - 1]?.[col - 1];
            if (cell && !cell._merged) {
                cell.row_type = rowType || '';
            }
        }
        this.refreshGrid();
        this.frm.dirty();
        const typeLabel = rowType || __('Normal Row');
        frappe.show_alert({ message: __('Row') + ' ' + row + ' ' + __('set to') + ': ' + typeLabel, indicator: 'green' });
    }

    setRowDisplay(row, display) {
        for (let col = 1; col <= this.cols; col++) {
            const cell = this.grid[row - 1]?.[col - 1];
            if (cell && !cell._merged) {
                cell.row_display = display || '';
            }
        }
        this.refreshGrid();
        this.frm.dirty();
    }

    syncRowHeaderHeights() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const table = container.querySelector('#spd-grid');
        const rowHeaders = container.querySelectorAll('.super-zprint-row-header-cell');
        if (!table || !rowHeaders.length) return;
        const tableRows = table.querySelectorAll('tr');
        tableRows.forEach((tr, idx) => {
            if (idx < rowHeaders.length) {
                const actualHeight = tr.getBoundingClientRect().height;
                if (actualHeight > 0) {
                    rowHeaders[idx].style.height = actualHeight + 'px';
                    rowHeaders[idx].style.minHeight = actualHeight + 'px';
                    rowHeaders[idx].style.maxHeight = actualHeight + 'px';
                    rowHeaders[idx].style.lineHeight = actualHeight + 'px';
                }
            }
        });
    }

    updateRowStyle(row, property, value) {
        if (!this.rowStyles[row]) this.rowStyles[row] = {};
        this.rowStyles[row][property] = value;
        this.refreshGrid();
        this.frm.dirty();
    }

    // ==================== Column Property Panel ====================

    renderColProperties(col) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const colStyle = this.colStyles[col] || {};
        let cssPreview = '';
        if (colStyle.width) cssPreview += 'width:' + colStyle.width + 'px; ';
        if (colStyle.css_style) cssPreview += colStyle.css_style;

        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-arrows-h"></i> ' + __('Column Style Settings') + ' <small style="color:#6c757d;font-weight:normal">(' + __('Col') + ' ' + col + ')</small>';
        }

        let formHtml = '<form id="col-property-form" class="property-form">' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-expand"></i> ' + __('Column Dimensions') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<div class="super-zprint-layout-controls">' +
                        '<div class="super-zprint-layout-control-group" style="flex:1">' +
                            '<label style="font-size:9px">' + __('Column Width') + ':</label>' +
                            '<div class="super-zprint-number-spinner super-zprint-number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="col-width" data-step="10">-</button>' +
                                '<input type="number" id="col-width" class="form-control super-zprint-spin-input" value="' + (colStyle.width || '') + '" min="1" max="500" step="1" placeholder="60">' +
                                '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="col-width" data-step="10">+</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-align-center"></i> ' + __('Text Alignment') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px;margin-bottom:4px">' + __('Horizontal Align') + ':</label>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="left" title="' + __('Left Align') + '"><i class="fa fa-align-left"></i> ' + __('Left') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="center" title="' + __('Center') + '"><i class="fa fa-align-center"></i> ' + __('Center') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="right" title="' + __('Right Align') + '"><i class="fa fa-align-right"></i> ' + __('Right') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default" id="col-format-painter-btn" title="' + __('Format Painter') + '" style="width:auto;padding:0 6px;font-size:9px;margin-left:8px"><i class="fa fa-paint-brush"></i> ' + __('Format Painter') + '</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-property-section">' +
                '<div class="super-zprint-property-section-header"><i class="fa fa-paint-brush"></i> ' + __('Column Style') + '</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px">' + __('CSS Style') + ':</label>' +
                    '<textarea id="col-css-style" class="form-control super-zprint-css-editor" rows="2" placeholder="text-align: center;">' + cssPreview.trim() + '</textarea>' +
                '</div>' +
            '</div>' +
        '</form>';

        container.querySelector('#spd-prop-form').innerHTML = formHtml;
        this.bindSpinnerButtons();
        this.bindColPropertyEvents(col);
    }

    bindColPropertyEvents(col) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const widthInput = container.querySelector('#col-width');
        const cssInput = container.querySelector('#col-css-style');

        if (widthInput) widthInput.addEventListener('change', (e) => {
            this.updateColStyle(col, 'width', parseInt(e.target.value) || undefined);
        });
        if (cssInput) cssInput.addEventListener('change', (e) => {
            this.updateColStyle(col, 'css_style', e.target.value);
        });

        // Horizontal alignment buttons
        container.querySelectorAll('.col-align-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.updateColStyle(col, 'css_style',
                    this.setCssProperty(this.colStyles[col]?.css_style || '', 'text-align', btn.dataset.align));
            });
        });
        // Column format painter button
        const colFormatPainterBtn = container.querySelector('#col-format-painter-btn');
        if (colFormatPainterBtn) {
            colFormatPainterBtn.addEventListener('click', () => {
                this.formatPainterActive = true;
                this.formatPainterSourceCss = this.colStyles[col]?.css_style || '';
                this.formatPainterPainting = false;
                this.formatPainterLastPainted = null;
                colFormatPainterBtn.style.background = '#ff9800';
                colFormatPainterBtn.style.color = '#fff';
                colFormatPainterBtn.style.borderColor = '#ff9800';
                const grid = container?.querySelector('#spd-grid');
                if (grid) grid.classList.add('format-painter-cursor');
                frappe.show_alert({ message: __('Format Painter activated'), indicator: 'blue' });
            });
        }
    }

    updateColStyle(col, property, value) {
        if (!this.colStyles[col]) this.colStyles[col] = {};
        this.colStyles[col][property] = value;
        this.refreshGrid();
    }

    setCssProperty(cssString, prop, value) {
        const pairs = this.parseCssString(cssString);
        pairs[prop] = value;
        return Object.entries(pairs).map(([k, v]) => k + ':' + v).join('; ');
    }

    // ==================== Cell Property Panel ====================

    renderCellProperties(cellId) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const [row, col] = this.parseCellId(cellId);
        let cell = this.grid[row - 1]?.[col - 1];
        if (!cell) return;
        // 如果选的是 merged child,找 master cell(渲染属性面板 + show 打散按钮;否则 child _merged 直接 return 用户看不到打散按钮)
        if (cell._merged && cell.master_cell_id) {
            cell = this.cellDataMap[cell.master_cell_id];
            if (!cell) return;
        } else if (cell._merged) {
            return;
        }

        const isMerged = cell.rowspan > 1 || cell.colspan > 1;
        const btnLeft = container.querySelector('#btn-unmerge-left');
        const btnInherit = container.querySelector('#btn-unmerge-inherit');
        if (btnLeft) btnLeft.style.display = isMerged ? 'inline-block' : 'none';
        if (btnInherit) btnInherit.style.display = isMerged ? 'inline-block' : 'none';

        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-cog"></i> ' + __('Cell Properties') + ' <small style="color:#6c757d;font-weight:normal">(' + cellId + ')</small>';
        }

        const typeOptions = this.cellTypes.map(t => '<option value="' + t.value + '">' + t.label + '</option>').join('');
        const barcodeFormatOptions = this.barcodeFormats.map(f => '<option value="' + f.value + '">' + f.label + '</option>').join('');
        const queryOptions = this.generateQueryOptions();

        const activeTab = this.lastActiveTab || 'style';
        const contentTabCls = activeTab === 'content' ? ' active' : '';
        const styleTabCls = activeTab === 'style' ? ' active' : '';

        let formHtml = '<div class="super-zprint-prop-tabs">' +
            '<div class="super-zprint-prop-tab' + contentTabCls + '" data-tab="content"><i class="fa fa-edit"></i> ' + __('Content') + '</div>' +
            '<div class="super-zprint-prop-tab' + styleTabCls + '" data-tab="style"><i class="fa fa-paint-brush"></i> ' + __('Style') + '</div>' +
        '</div>' +
        '<div class="super-zprint-prop-tab-contents">' +
            '<div class="super-zprint-prop-tab-content' + contentTabCls + '" data-tab="content">' +
                '<label>' + __('Type') + ':</label>' +
                '<select id="prop-cell-type" class="form-control">' + typeOptions + '</select>' +
                '<label>' + __('Value') + ':</label>' +
                '<textarea id="prop-cell-value" class="form-control" rows="2"></textarea>' +
                '<div id="query-group" style="display:none">' +
                    '<label>' + __('Bound Query') + ':</label>' +
                    '<select id="prop-query-name" class="form-control"><option value="">--</option>' + queryOptions + '</select>' +
                    '<label>' + __('Data Key') + ':</label>' +
                    '<select id="prop-data-key" class="form-control"><option value="">--</option></select>' +
                '</div>' +
                '<div id="barcode-group" style="display:none">' +
                    '<label>' + __('Barcode Format') + ':</label>' +
                    '<select id="prop-barcode-format" class="form-control">' + barcodeFormatOptions + '</select>' +
                    '<label>' + __('Width (px)') + ':</label>' +
                    '<input type="number" id="prop-barcode-width" class="form-control" value="100">' +
                    '<label>' + __('Height (px)') + ':</label>' +
                    '<input type="number" id="prop-barcode-height" class="form-control" value="40">' +
                '</div>' +
                '<div id="qrcode-group" style="display:none">' +
                    '<p style="font-size:11px;color:#888;margin:4px 0;">' + __('QR code auto-fits cell dimensions (1:1)') + '</p>' +
                '</div>' +
            '</div>' +
            '<div class="super-zprint-prop-tab-content' + styleTabCls + '" data-tab="style">' +
                '<div class="super-zprint-layout-controls">' +
                    '<div class="super-zprint-layout-control-group">' +
                        '<label>' + __('Rowspan') + ':</label>' +
                        '<div class="super-zprint-number-spinner">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="prop-rowspan" data-step="1">-</button>' +
                            '<input type="number" id="prop-rowspan" class="form-control super-zprint-spin-input" min="1" max="100" value="' + (cell.rowspan || 1) + '">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="prop-rowspan" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="super-zprint-layout-control-group">' +
                        '<label>' + __('Colspan') + ':</label>' +
                        '<div class="super-zprint-number-spinner">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="prop-colspan" data-step="1">-</button>' +
                            '<input type="number" id="prop-colspan" class="form-control super-zprint-spin-input" min="1" max="26" value="' + (cell.colspan || 1) + '">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="prop-colspan" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-layout-controls">' +
                    '<div class="super-zprint-layout-control-group">' +
                        '<label>' + __('Font (px)') + ':</label>' +
                        '<div class="super-zprint-number-spinner">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="prop-font-size" data-step="1">-</button>' +
                            '<input type="number" id="prop-font-size" class="form-control super-zprint-spin-input" value="' + this.extractFontSize(cell.css_style, row) + '" min="8" max="36">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="prop-font-size" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="super-zprint-layout-control-group">' +
                        '<label>' + __('Padding') + ':</label>' +
                        '<div class="super-zprint-number-spinner">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-minus" data-target="prop-padding" data-step="1">-</button>' +
                            '<input type="number" id="prop-padding" class="form-control super-zprint-spin-input" value="' + this.extractPadding(cell.css_style) + '" min="0" max="20">' +
                            '<button type="button" class="btn btn-xs super-zprint-spin-btn spin-plus" data-target="prop-padding" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-css-quick-buttons super-zprint-css-quick-compact">' +
                    '<label style="font-size:9px">' + __('Quick') + ':</label>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:left" data-prop="text-align" title="' + __('Left Align') + '"><i class="fa fa-align-left"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:center" data-prop="text-align" title="' + __('Center') + '"><i class="fa fa-align-center"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:right" data-prop="text-align" title="' + __('Right Align') + '"><i class="fa fa-align-right"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default cell-valign-btn" data-valign="top" title="' + __('Top') + '"><i class="fa fa-arrow-up"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default cell-valign-btn" data-valign="middle" title="' + __('Center') + '"><i class="fa fa-arrows-v"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default cell-valign-btn" data-valign="bottom" title="' + __('Bottom') + '"><i class="fa fa-arrow-down"></i></button>' +
                    '</div>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-weight:bold" data-prop="font-weight" title="' + __('Bold') + '"><i class="fa fa-bold"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-style:italic" data-prop="font-style" title="' + __('Italic') + '"><i class="fa fa-italic"></i></button>' +
                        '<div class="super-zprint-color-picker-wrapper" title="' + __('Background Color') + '"><i class="fa fa-fill-drip"></i><input type="color" id="bg-color-picker" value="#ffffff"></div>' +
                        '<div class="super-zprint-color-picker-wrapper" title="' + __('Text Color') + '"><i class="fa fa-font"></i><input type="color" id="text-color-picker" value="#000000"></div>' +
                        '<button type="button" class="btn btn-xs css-quick-btn" data-action="default-css" title="' + __('Default Style') + '" style="width:auto;padding:0 6px;font-size:9px;background:#28a745;color:#fff;border-color:#28a745"><i class="fa fa-undo" style="color:#fff"></i> <span style="color:#fff">' + __('Default') + '</span></button>' +
                        '<button type="button" class="btn btn-xs btn-danger css-quick-btn" data-action="clear-css" title="' + __('Clear CSS') + '" style="width:auto;padding:0 6px;font-size:9px"><i class="fa fa-eraser"></i> ' + __('Clear') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" id="format-painter-btn" data-action="format-painter" title="' + __('Format Painter') + '" style="width:auto;padding:0 6px;font-size:9px"><i class="fa fa-paint-brush"></i> ' + __('Format Painter') + '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-border-settings" style="margin:6px 0;padding:6px;background:#f8f9fa;border-radius:4px;border:1px solid #e9ecef">' +
                    '<label style="font-size:9px;margin-bottom:4px">' + __('Border') + ':</label>' +
                    '<div style="display:flex;align-items:center;gap:6px;margin-top:4px">' +
                        '<span style="font-size:8px;color:#888;min-width:28px">' + __('Position') + ':</span>' +
                        '<div class="super-zprint-btn-group-wrap" style="gap:2px">' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn super-zprint-bs-active" data-side="top" title="' + __('Top') + '" style="width:24px;height:22px;padding:0"><i class="fa fa-arrow-up"></i></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="bottom" title="' + __('Bottom') + '" style="width:24px;height:22px;padding:0"><i class="fa fa-arrow-down"></i></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="left" title="' + __('Left') + '" style="width:24px;height:22px;padding:0"><i class="fa fa-arrow-left"></i></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="right" title="' + __('Right') + '" style="width:24px;height:22px;padding:0"><i class="fa fa-arrow-right"></i></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="all" title="' + __('All') + '" style="width:24px;height:22px;padding:0"><i class="fa fa-border-all"></i></button>' +
                        '</div>' +
                    '</div>' +
                    '<div style="display:flex;align-items:center;gap:6px;margin-top:4px">' +
                        '<span style="font-size:8px;color:#888;min-width:28px">' + __('Width') + ':</span>' +
                        '<div class="super-zprint-btn-group-wrap" style="gap:2px">' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn super-zprint-bw-active" data-width="1" title="' + __('Thin') + ' (1px)" style="width:24px;height:22px;padding:0"><span class="border-icon-thin"></span></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="2" title="' + __('Medium') + ' (2px)" style="width:24px;height:22px;padding:0"><span class="border-icon-medium"></span></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="3" title="' + __('Thick') + ' (3px)" style="width:24px;height:22px;padding:0"><span class="border-icon-thick"></span></button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="0" title="' + __('None') + '" style="width:24px;height:22px;padding:0"><span class="border-icon-none"></span></button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<label>' + __('CSS') + ':</label>' +
                '<textarea id="prop-css-style" class="form-control super-zprint-css-editor" rows="3">' + (cell.css_style || '') + '</textarea>' +
            '</div>' +
        '</div>';

        container.querySelector('#spd-prop-form').innerHTML = formHtml;
        this.populatePropertyForm(cell);
        this.bindPropertyFormEvents(cell);
    }

    populatePropertyForm(cell) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const setValue = (id, val) => { const el = container.querySelector('#' + id); if (el) el.value = val || ''; };
        setValue('prop-cell-type', cell.cell_type);
        setValue('prop-cell-value', cell.cell_value);
        setValue('prop-query-name', cell.query_name);
        setValue('prop-data-key', cell.data_key);
        setValue('prop-barcode-format', cell.barcode_format || 'CODE128');
        setValue('prop-barcode-width', cell.barcode_width || 100);
        setValue('prop-barcode-height', cell.barcode_height || 40);
        this.togglePropertyGroups(cell.cell_type);
        if ((cell.cell_type === 'data_query' || cell.cell_type === 'image') && cell.query_name) {
            this.loadDataKeyOptions(cell.query_name);
        }
    }

    togglePropertyGroups(cellType) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const queryGroup = container.querySelector('#query-group');
        const barcodeGroup = container.querySelector('#barcode-group');
        const qrcodeGroup = container.querySelector('#qrcode-group');
        if (queryGroup) queryGroup.style.display = (cellType === 'data_query' || cellType === 'image') ? 'block' : 'none';
        if (barcodeGroup) barcodeGroup.style.display = (cellType === 'barcode') ? 'block' : 'none';
        if (qrcodeGroup) qrcodeGroup.style.display = (cellType === 'qrcode') ? 'block' : 'none';
    }

    loadDataKeyOptions(queryName) {
        const container = document.getElementById(this.designContainerId);
        const select = container?.querySelector('#prop-data-key');
        if (!select) return;
        select.innerHTML = '<option value="">--</option>';
        if (!queryName) return;

        frappe.call({
            method: 'zhiz_print.zhiz_print.doctype.super_print_design.super_print_design.get_query_keys',
            args: { design_name: this.frm.doc.name, query_name: queryName },
            callback: (r) => {
                const keys = r.message || [];
                keys.forEach(k => {
                    const opt = document.createElement('option');
                    opt.value = k;
                    opt.textContent = k;
                    select.appendChild(opt);
                });
                const cell = this.cellDataMap[this.currentCell];
                if (cell && cell.data_key) select.value = cell.data_key;
            }
        });
    }

    bindPropertyFormEvents(cell) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        container.querySelector('#prop-cell-type')?.addEventListener('change', (e) => {
            this.updateCellProperty('cell_type', e.target.value);
            this.togglePropertyGroups(e.target.value);
        });
        container.querySelector('#prop-cell-value')?.addEventListener('change', (e) => {
            this.updateCellProperty('cell_value', e.target.value);
        });
        container.querySelector('#prop-rowspan')?.addEventListener('change', (e) => {
            this.updateCellProperty('rowspan', parseInt(e.target.value) || 1);
        });
        container.querySelector('#prop-colspan')?.addEventListener('change', (e) => {
            this.updateCellProperty('colspan', parseInt(e.target.value) || 1);
        });
        container.querySelector('#prop-css-style')?.addEventListener('change', (e) => {
            this.updateCellProperty('css_style', e.target.value);
        });
        container.querySelector('#prop-data-key')?.addEventListener('change', (e) => {
            this.updateCellProperty('data_key', e.target.value);
        });
        container.querySelector('#prop-query-name')?.addEventListener('change', (e) => {
            this.updateCellProperty('query_name', e.target.value);
            this.loadDataKeyOptions(e.target.value);
        });
        container.querySelector('#prop-barcode-format')?.addEventListener('change', (e) => {
            this.updateCellProperty('barcode_format', e.target.value);
        });
        container.querySelector('#prop-barcode-width')?.addEventListener('change', (e) => {
            this.updateCellProperty('barcode_width', parseInt(e.target.value) || 100);
        });
        container.querySelector('#prop-barcode-height')?.addEventListener('change', (e) => {
            this.updateCellProperty('barcode_height', parseInt(e.target.value) || 40);
        });
        container.querySelector('#prop-font-size')?.addEventListener('change', (e) => {
            this.applyFontSize(parseInt(e.target.value) || 12);
        });
        container.querySelector('#prop-padding')?.addEventListener('change', (e) => {
            this.applyPadding(parseInt(e.target.value) || 0);
        });

        this.bindCssQuickButtons(cell);
        this.bindBorderButtons();
        this.bindSpinnerButtons();

        // Cell vertical alignment buttons
        container.querySelectorAll('.cell-valign-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const newCss = this.setCssProperty(cell.css_style || '', 'vertical-align', btn.dataset.valign);
                this.updateCellProperty('css_style', newCss);
            });
        });

        // Tab switching
        container.querySelectorAll('.super-zprint-prop-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                container.querySelectorAll('.super-zprint-prop-tab').forEach(t => t.classList.remove('active'));
                container.querySelectorAll('.super-zprint-prop-tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                const target = container.querySelector('.super-zprint-prop-tab-content[data-tab="' + tab.dataset.tab + '"]');
                if (target) target.classList.add('active');
                this.lastActiveTab = tab.dataset.tab;
            });
        });
    }

    updateCellProperty(property, value) {
        if (!this.currentCell) return;
        const [row, col] = this.parseCellId(this.currentCell);
        let cell = this.grid[row - 1]?.[col - 1];
        if (!cell || cell._merged) return;

        if (property === 'colspan' || property === 'rowspan') {
            const oldVal = parseInt(cell[property]) || 1;
            const newVal = parseInt(value) || 1;
            if (oldVal === newVal) return;

            if (property === 'colspan') {
                this._adjustColspan(cell, row, col, oldVal, newVal);
            } else {
                this._adjustRowspan(cell, row, col, oldVal, newVal);
            }
            this.refreshGrid();
            this.renderCellProperties(this.currentCell);
            return;
        }

        cell[property] = value;
        this.cellDataMap[this.currentCell] = cell;
        this.refreshGrid();
        this.frm.dirty();
    }

    _adjustColspan(cell, row, col, oldColspan, newColspan) {
        const startRow = row - 1;
        const startCol = col - 1;

        if (newColspan > oldColspan) {
            // Expand: check if new area cells are independent (non-merged)
            for (let c = startCol + oldColspan; c < startCol + newColspan; c++) {
                if (c >= this.cols) {
                    frappe.show_alert({ message: __('Colspan exceeds grid range'), indicator: 'red' });
                    return;
                }
                const target = this.grid[startRow]?.[c];
                if (target && !target._merged && (target.rowspan > 1 || target.colspan > 1)) {
                    frappe.show_alert({ message: __('Target area contains merged cells, cannot expand'), indicator: 'red' });
                    return;
                }
                if (target?._merged) {
                    frappe.show_alert({ message: __('Target area contains merged cells, cannot expand'), indicator: 'red' });
                    return;
                }
            }
            // Occupy new independent cells
            for (let c = startCol + oldColspan; c < startCol + newColspan; c++) {
                const target = this.grid[startRow][c];
                if (target && !target._merged) {
                    delete this.cellDataMap[target.cell_id];
                }
                this.grid[startRow][c] = { _merged: true, master_cell_id: cell.cell_id };
            }
        } else {
            // Shrink: released cells become independent
            const pageNo = this.currentPageNo || 1;
            for (let c = startCol + newColspan; c < startCol + oldColspan; c++) {
                if (c < this.cols) {
                    const newCellId = `P${pageNo}_R${row}C${c + 1}`;
                    const newCell = {
                        cell_id: newCellId, page_no: pageNo, row: row, col: c + 1,
                        rowspan: 1, colspan: 1, cell_type: 'static',
                        cell_value: '',
                        css_style: this.getDefaultCellCss()
                    };
                    this.grid[startRow][c] = newCell;
                    this.cellDataMap[newCellId] = newCell;
                }
            }
        }
        cell.colspan = newColspan;
        this.cellDataMap[this.currentCell] = cell;
        this.frm.dirty();
    }

    _adjustRowspan(cell, row, col, oldRowspan, newRowspan) {
        const startRow = row - 1;
        const startCol = col - 1;

        if (newRowspan > oldRowspan) {
            // Expand: check new area
            for (let r = startRow + oldRowspan; r < startRow + newRowspan; r++) {
                if (r >= this.rows) {
                    frappe.show_alert({ message: __('Rowspan exceeds grid range'), indicator: 'red' });
                    return;
                }
                for (let c = startCol; c < startCol + (cell.colspan || 1); c++) {
                    const target = this.grid[r]?.[c];
                    if (target && !target._merged && (target.rowspan > 1 || target.colspan > 1)) {
                        frappe.show_alert({ message: __('Target area contains merged cells, cannot expand'), indicator: 'red' });
                        return;
                    }
                    if (target?._merged) {
                        frappe.show_alert({ message: __('Target area contains merged cells, cannot expand'), indicator: 'red' });
                        return;
                    }
                }
            }
            // Occupy new independent cells
            for (let r = startRow + oldRowspan; r < startRow + newRowspan; r++) {
                for (let c = startCol; c < startCol + (cell.colspan || 1); c++) {
                    const target = this.grid[r][c];
                    if (target && !target._merged) {
                        delete this.cellDataMap[target.cell_id];
                    }
                    this.grid[r][c] = { _merged: true, master_cell_id: cell.cell_id };
                }
            }
        } else {
            // Shrink: released cells become independent
            const pageNo = this.currentPageNo || 1;
            for (let r = startRow + newRowspan; r < startRow + oldRowspan; r++) {
                for (let c = startCol; c < startCol + (cell.colspan || 1); c++) {
                    if (r < this.rows && c < this.cols) {
                        const newCellId = `P${pageNo}_R${r + 1}C${c + 1}`;
                        const newCell = {
                            cell_id: newCellId, page_no: pageNo, row: r + 1, col: c + 1,
                            rowspan: 1, colspan: 1, cell_type: 'static',
                            cell_value: '',
                            css_style: this.getDefaultCellCss()
                        };
                        this.grid[r][c] = newCell;
                        this.cellDataMap[newCellId] = newCell;
                    }
                }
            }
        }
        cell.rowspan = newRowspan;
        this.cellDataMap[this.currentCell] = cell;
        this.frm.dirty();
    }

    _createFreedCell(row, col, sourceCell, copyContent) {
        const pageNo = sourceCell.page_no || this.currentPageNo || 1;
        const newCellId = `P${pageNo}_R${row}C${col}`;
        const newCell = {
            cell_id: newCellId, page_no: pageNo, row: row, col: col,
            rowspan: 1, colspan: 1, cell_type: 'static',
            cell_value: copyContent ? sourceCell.cell_value : '',
            css_style: sourceCell.css_style || ''
        };
        if (copyContent) {
            newCell.cell_type = sourceCell.cell_type || 'static';
            if (sourceCell.barcode_format) newCell.barcode_format = sourceCell.barcode_format;
            if (sourceCell.barcode_width) newCell.barcode_width = sourceCell.barcode_width;
            if (sourceCell.barcode_height) newCell.barcode_height = sourceCell.barcode_height;
        }
        return newCell;
    }

    _doUnmerge(inherit) {
        if (!this.currentCell) return;
        let cell = this.cellDataMap[this.currentCell];
        if (!cell) return;
        // 如果选的是 merged child,找 master cell(否则 child rowspan/colspan=1 直接 return 无法打散)
        if (cell._merged && cell.master_cell_id) {
            cell = this.cellDataMap[cell.master_cell_id];
            if (!cell) return;
        }
        if (cell.rowspan <= 1 && cell.colspan <= 1) return;

        const startRow = cell.row - 1;
        const startCol = cell.col - 1;
        const oldRowspan = cell.rowspan;
        const oldColspan = cell.colspan;

        for (let r = startRow; r < startRow + oldRowspan; r++) {
            for (let c = startCol; c < startCol + oldColspan; c++) {
                if (r === startRow && c === startCol) continue;
                if (r < this.rows && c < this.cols) {
                    const newCell = this._createFreedCell(r + 1, c + 1, cell, inherit);
                    this.grid[r][c] = newCell;
                    this.cellDataMap[newCell.cell_id] = newCell;
                }
            }
        }

        cell.rowspan = 1;
        cell.colspan = 1;
        this.cellDataMap[cell.cell_id] = cell;
        this.refreshGrid();
        this.renderCellProperties(this.currentCell);
        this.frm.dirty();
        frappe.show_alert({ message: inherit ? __('Unmerged with content inherited') : __('Unmerged, content kept in first cell'), indicator: 'green' });
    }

    toggleFormatPainter() {
        if (this.formatPainterActive) {
            this.deactivateFormatPainter();
        } else {
            this.activateFormatPainter();
        }
    }

    activateFormatPainter() {
        if (!this.currentCell) {
            frappe.show_alert({ message: __('Select a cell first'), indicator: 'yellow' });
            return;
        }
        const [row, col] = this.parseCellId(this.currentCell);
        const cell = this.grid[row - 1]?.[col - 1];
        if (!cell || cell._merged) return;

        this.formatPainterActive = true;
        this.formatPainterSourceCss = cell.css_style || '';
        this.formatPainterPainting = false;
        this.formatPainterLastPainted = null;

        const container = document.getElementById(this.designContainerId);
        const btn = container?.querySelector('#format-painter-btn');
        if (btn) {
            btn.style.background = '#ff9800';
            btn.style.color = '#fff';
            btn.style.borderColor = '#ff9800';
        }
        const grid = container?.querySelector('#spd-grid');
        if (grid) grid.classList.add('format-painter-cursor');
        frappe.show_alert({ message: __('Format Painter activated'), indicator: 'blue' });
    }

    deactivateFormatPainter() {
        this.formatPainterActive = false;
        this.formatPainterSourceCss = null;
        this.formatPainterPainting = false;
        this.formatPainterLastPainted = null;

        const container = document.getElementById(this.designContainerId);
        // Reset all format painter buttons
        const btn = container?.querySelector('#format-painter-btn');
        const rowBtn = container?.querySelector('#row-format-painter-btn');
        const colBtn = container?.querySelector('#col-format-painter-btn');
        [btn, rowBtn, colBtn].forEach(b => {
            if (b) {
                b.style.background = '';
                b.style.color = '';
                b.style.borderColor = '';
            }
        });
        const grid = container?.querySelector('#spd-grid');
        if (grid) grid.classList.remove('format-painter-cursor');
    }

    paintFormatToCell(cellId) {
        if (!this.formatPainterActive || !this.formatPainterSourceCss) return;
        if (cellId === this.formatPainterLastPainted) return;

        const [row, col] = this.parseCellId(cellId);
        const cell = this.grid[row - 1]?.[col - 1];
        if (!cell || cell._merged) return;

        // Apply style to data model
        cell.css_style = this.formatPainterSourceCss;
        this.cellDataMap[cellId] = cell;
        this.frm.dirty();

        // Visual feedback: apply style directly to the DOM cell element + painted highlight
        const container = document.getElementById(this.designContainerId);
        if (container) {
            const td = container.querySelector('.spd-cell[data-cell-id="' + cellId + '"]');
            if (td) {
                // Remove highlight from previously painted cells
                container.querySelectorAll('.spd-cell.fp-painted').forEach(el => {
                    el.classList.remove('fp-painted');
                });
                // Apply the actual CSS style to the cell so user sees the change
                let cellStyle = 'line-height:inherit;';
                if (this.formatPainterSourceCss) cellStyle += this.formatPainterSourceCss;
                td.setAttribute('style', cellStyle);
                td.classList.add('fp-painted');
            }
        }

        this.formatPainterLastPainted = cellId;
    }

    paintFormatToRow(row) {
        if (!this.formatPainterActive || !this.formatPainterSourceCss) return;
        for (let col = 1; col <= this.cols; col++) {
            const cell = this.grid[row - 1]?.[col - 1];
            if (cell && !cell._merged) {
                cell.css_style = this.formatPainterSourceCss;
            }
        }
        this.frm.dirty();
        this.deactivateFormatPainter();
        this.refreshGrid();
        frappe.show_alert({ message: __('Row') + ' ' + row + ' ' + __('format painted'), indicator: 'green' });
    }

    paintFormatToCol(col) {
        if (!this.formatPainterActive || !this.formatPainterSourceCss) return;
        for (let row = 1; row <= this.rows; row++) {
            const cell = this.grid[row - 1]?.[col - 1];
            if (cell && !cell._merged) {
                cell.css_style = this.formatPainterSourceCss;
            }
        }
        this.frm.dirty();
        this.deactivateFormatPainter();
        this.refreshGrid();
        frappe.show_alert({ message: __('Col') + ' ' + col + ' ' + __('format painted'), indicator: 'green' });
    }

    setColorProperty(prop, color) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const pairs = this.parseCssString(cssEditor.value);
        pairs[prop] = color;
        const newCss = Object.entries(pairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        this.updateCellProperty('css_style', newCss);
    }

    toggleCssProperty(cssProp) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const [prop, val] = cssProp.split(':');
        const pairs = this.parseCssString(cssEditor.value);
        if (pairs[prop] === val) delete pairs[prop];
        else pairs[prop] = val;
        const newCss = Object.entries(pairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        this.updateCellProperty('css_style', newCss);
    }

    extractFontSize(cssStyle, row) {
        // Priority: cell style > row style > designer default
        if (cssStyle) {
            const pairs = this.parseCssString(cssStyle);
            if (pairs['font-size']) {
                const val = pairs['font-size'].toString().replace(/px|em|pt/g, '');
                const parsed = parseInt(val);
                if (parsed) return parsed;
            }
        }
        if (row && this.rowStyles[row]?.font_size) {
            return parseInt(this.rowStyles[row].font_size) || this.fontSize;
        }
        return this.fontSize;
    }

    extractPadding(cssStyle) {
        if (!cssStyle) return 0;
        const pairs = this.parseCssString(cssStyle);
        if (pairs['padding']) {
            const val = pairs['padding'].toString().replace(/px|em|pt/g, '');
            return parseInt(val) || 0;
        }
        return 0;
    }

    applyFontSize(size) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const pairs = this.parseCssString(cssEditor.value);
        pairs['font-size'] = size + 'px';
        const newCss = Object.entries(pairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        this.updateCellProperty('css_style', newCss);
    }

    applyPadding(size) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const pairs = this.parseCssString(cssEditor.value);
        pairs['padding'] = size + 'px';
        const newCss = Object.entries(pairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        this.updateCellProperty('css_style', newCss);
    }

    bindSpinnerButtons() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        container.querySelectorAll('.super-zprint-spin-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const targetId = btn.dataset.target;
                const step = parseInt(btn.dataset.step) || 1;
                const isPlus = btn.classList.contains('spin-plus');
                const input = container.querySelector('#' + targetId);
                if (input) {
                    let value = parseInt(input.value) || 0;
                    value = isPlus ? value + step : value - step;
                    const min = parseInt(input.min) || 0;
                    const max = parseInt(input.max) || 500;
                    value = Math.max(min, Math.min(max, value));
                    input.value = value;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
            });
        });
    }

    bindBorderButtons() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        if (this.currentBorderWidth === undefined) this.currentBorderWidth = 1;
        if (this.currentBorderSide === undefined) this.currentBorderSide = 'all';

        const widthBtns = container.querySelectorAll('.super-zprint-border-width-btn');
        const sideBtns = container.querySelectorAll('.super-zprint-border-side-btn');

        widthBtns.forEach(btn => {
            const btnWidth = parseInt(btn.dataset.width);
            if (btnWidth === this.currentBorderWidth) btn.classList.add('super-zprint-bw-active');
            else btn.classList.remove('super-zprint-bw-active');
        });

        sideBtns.forEach(btn => {
            if (btn.dataset.side === this.currentBorderSide) btn.classList.add('super-zprint-bs-active');
            else btn.classList.remove('super-zprint-bs-active');
        });

        sideBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                sideBtns.forEach(b => b.classList.remove('super-zprint-bs-active'));
                btn.classList.add('super-zprint-bs-active');
                this.currentBorderSide = btn.dataset.side;
                this.applySingleBorder(this.currentBorderSide, this.currentBorderWidth);
            });
        });

        widthBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                widthBtns.forEach(b => b.classList.remove('super-zprint-bw-active'));
                btn.classList.add('super-zprint-bw-active');
                this.currentBorderWidth = parseInt(btn.dataset.width);
                this.applySingleBorder(this.currentBorderSide, this.currentBorderWidth);
            });
        });
    }

    applySingleBorder(side, width) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const cssPairs = this.parseCssString(cssEditor.value);

        if (cssPairs['border']) {
            const borderValue = cssPairs['border'];
            delete cssPairs['border'];
            cssPairs['border-top'] = borderValue;
            cssPairs['border-right'] = borderValue;
            cssPairs['border-bottom'] = borderValue;
            cssPairs['border-left'] = borderValue;
        }

        const sideNames = { top: __('Top'), bottom: __('Bottom'), left: __('Left'), right: __('Right'), all: __('All') };
        const allSides = ['top', 'right', 'bottom', 'left'];
        const borderVal = width > 0 ? width + 'px solid black' : '1px solid transparent';

        if (side === 'all') {
            // "All" button: apply current width to all four sides
            allSides.forEach(s => { cssPairs['border-' + s] = borderVal; });
            const newCss = Object.entries(cssPairs).map(([k, v]) => k + ':' + v).join('; ');
            cssEditor.value = newCss;
            cssEditor.dispatchEvent(new Event('change', { bubbles: true }));
            if (width > 0) {
                frappe.show_alert({ message: __('All borders set to') + ' ' + width + 'px', indicator: 'green' });
            } else {
                frappe.show_alert({ message: __('All borders removed'), indicator: 'orange' });
            }
            return;
        }

        cssPairs['border-' + side] = borderVal;
        const newCss = Object.entries(cssPairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        cssEditor.dispatchEvent(new Event('change', { bubbles: true }));
        if (width > 0) {
            frappe.show_alert({ message: sideNames[side] + ' ' + __('border set to') + ' ' + width + 'px', indicator: 'green' });
        } else {
            frappe.show_alert({ message: sideNames[side] + ' ' + __('border removed'), indicator: 'orange' });
        }
    }

    bindCssQuickButtons(cell) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        container.querySelectorAll('.css-quick-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                const cssProp = btn.dataset.css;
                const action = btn.dataset.action;

                if (action === 'clear-css') {
                    const cssEditor = container.querySelector('#prop-css-style');
                    if (cssEditor) cssEditor.value = '';
                    const bgPicker = container.querySelector('#bg-color-picker');
                    const textPicker = container.querySelector('#text-color-picker');
                    if (bgPicker) bgPicker.value = '#ffffff';
                    if (textPicker) textPicker.value = '#000000';
                    const fontSizeInput = container.querySelector('#prop-font-size');
                    const paddingInput = container.querySelector('#prop-padding');
                    if (fontSizeInput) fontSizeInput.value = this.fontSize;
                    if (paddingInput) paddingInput.value = 0;
                    this.updateCellProperty('css_style', '');
                } else if (action === 'default-css') {
                    const defaultCss = this.getDefaultCellCss();
                    const cssEditor = container.querySelector('#prop-css-style');
                    if (cssEditor) cssEditor.value = defaultCss;
                    const bgPicker = container.querySelector('#bg-color-picker');
                    const textPicker = container.querySelector('#text-color-picker');
                    if (bgPicker) bgPicker.value = '#ffffff';
                    if (textPicker) textPicker.value = '#000000';
                    const fontSizeInput = container.querySelector('#prop-font-size');
                    const paddingInput = container.querySelector('#prop-padding');
                    if (fontSizeInput) fontSizeInput.value = this.fontSize;
                    if (paddingInput) paddingInput.value = 0;
                    this.updateCellProperty('css_style', defaultCss);
                    frappe.show_alert({ message: __('Default style restored'), indicator: 'green' });
                } else if (action === 'format-painter') {
                    this.toggleFormatPainter();
                } else if (cssProp) {
                    this.toggleCssProperty(cssProp);
                }
            });
        });

        const bgColorPicker = container.querySelector('#bg-color-picker');
        const textColorPicker = container.querySelector('#text-color-picker');
        if (bgColorPicker) {
            bgColorPicker.addEventListener('input', (e) => {
                this.setColorProperty('background-color', e.target.value);
            });
        }
        if (textColorPicker) {
            textColorPicker.addEventListener('input', (e) => {
                this.setColorProperty('color', e.target.value);
            });
        }
    }

    clearSingleCssProperty(propName) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const cssEditor = container.querySelector('#prop-css-style');
        if (!cssEditor) return;
        const cssPairs = this.parseCssString(cssEditor.value);
        if (cssPairs.hasOwnProperty(propName)) {
            delete cssPairs[propName];
            const newCss = Object.entries(cssPairs).map(([k, v]) => k + ':' + v).join('; ');
            cssEditor.value = newCss;
            cssEditor.dispatchEvent(new Event('change', { bubbles: true }));
            frappe.show_alert({ message: __('Cleared') + ' ' + propName + ' ' + __('property'), indicator: 'orange' });
        }
    }

    generateQueryOptions() {
        const queries = this.frm.doc.design_queries || [];
        if (!queries.length) return '';
        return queries.map(q =>
            '<option value="' + q.query_name + '">' + __(q.query_name) + '</option>'
        ).join('');
    }

    syncToForm() {
        // 收集 designItems(遍历所有页 grid cells) + 同步状态到 frm.doc
        // 供 saveDesign + before_save(原生保存) 调用,确保 frm.doc 有最新设计器状态
        const designItems = [];
        const pageNumbers = Object.keys(this.pages).map(p => parseInt(p)).sort((a, b) => a - b);
        for (const pageNo of pageNumbers) {
            const page = this.pages[pageNo];
            for (let row = 1; row <= this.rows; row++) {
                for (let col = 1; col <= this.cols; col++) {
                    const cell = page.grid[row - 1]?.[col - 1];
                    if (!cell) {
                        designItems.push(this.createBlankItem(row, col, pageNo));
                    } else if (cell._merged) {
                        continue;
                    } else {
                        designItems.push({
                            cell_id: cell.cell_id, page_no: pageNo, row, col,
                            rowspan: cell.rowspan || 1, colspan: cell.colspan || 1,
                            cell_type: cell.cell_type || 'static', cell_value: cell.cell_value || '',
                            cell_options: cell.cell_options || '',
                            css_style: cell.css_style || '', query_name: cell.query_name || '',
                            data_key: cell.data_key || '', barcode_format: cell.barcode_format || 'CODE128',
                            barcode_width: cell.barcode_width || 100, barcode_height: cell.barcode_height || 40,
                            row_type: cell.row_type || '', row_display: cell.row_display || '',
                        });
                    }
                }
            }
        }

        this.frm.set_value('rows', this.rows);
        this.frm.set_value('columns', this.cols);
        this.frm.set_value('page_count', pageNumbers.length);
        this.frm.set_value('row_styles', JSON.stringify(this.rowStyles));
        this.frm.set_value('col_styles', JSON.stringify(this.colStyles));
        this.frm.set_value('font_family', this.fontFamily);
        this.frm.set_value('font_size', this.fontSize);
        this.frm.set_value('page_header_left', this.pageHeaderLeft);
        this.frm.set_value('page_header_center', this.pageHeaderCenter);
        this.frm.set_value('page_header_right', this.pageHeaderRight);
        this.frm.set_value('page_footer_left', this.pageFooterLeft);
        this.frm.set_value('page_footer_center', this.pageFooterCenter);
        this.frm.set_value('page_footer_right', this.pageFooterRight);
        this.frm.set_value('page_header_align', this.pageHeaderAlign || 'Center');
        this.frm.set_value('page_footer_align', this.pageFooterAlign || 'Center');
        ['page_header_left_align','page_header_center_align','page_header_right_align',
         'page_footer_left_align','page_footer_center_align','page_footer_right_align'].forEach(f => {
            this.frm.set_value(f, this.frm.doc[f] || '');
        });
        this.frm.set_value('design_items', designItems);
        return { cells: designItems.length, pages: pageNumbers.length };
    }

    saveDesign() {
        // Sync current row/col count from toolbar inputs
        const container = document.getElementById(this.designContainerId);
        if (container) {
            const inputRows = parseInt(container.querySelector('#spd-rows')?.value);
            const inputCols = parseInt(container.querySelector('#spd-cols')?.value);
            if (inputRows && inputRows > 0) this.rows = inputRows;
            if (inputCols && inputCols > 0) this.cols = inputCols;
        }

        const info = this.syncToForm();
        this.frm.save().then(() => {
            frappe.show_alert({
                message: __('Design saved') + ', ' + info.cells + ' ' + __('cells') + ', ' + info.pages + ' ' + __('pages'),
                indicator: 'green'
            });
        });
    }

    createBlankItem(row, col, pageNo) {
        pageNo = pageNo || this.currentPageNo || 1;
        return {
            cell_id: `P${pageNo}_R${row}C${col}`, page_no: pageNo, row, col,
            rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '',
            css_style: this.getDefaultCellCss(), row_type: '', row_display: ''
        };
    }

    createMergedItem(row, col, masterId) {
        const pageNo = this.currentPageNo || 1;
        return {
            cell_id: `P${pageNo}_R${row}C${col}`, page_no: pageNo, row, col, rowspan: 1, colspan: 1,
            cell_type: 'static', cell_value: '||MERGED::' + (masterId || `P${pageNo}_R${row}C${col}`) + '||', css_style: ''
        };
    }

    clearDesign() {
        if (!confirm(__('Are you sure you want to clear all designs? This action cannot be undone.'))) return;
        this.pages = {};
        this.pageCount = 1;
        this.currentPageNo = 1;
        this.rowStyles = {};
        this.colStyles = {};
        this.pages[1] = { grid: Array(this.rows).fill().map(() => Array(this.cols).fill(null)), cellDataMap: {} };
        const defaultCss = this.getDefaultCellCss();
        const page = this.pages[1];
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                const id = `P1_R${r}C${c}`;
                const data = { cell_id: id, page_no: 1, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                page.grid[r - 1][c - 1] = data;
                page.cellDataMap[id] = data;
            }
        }
        this._activatePage(1);
        this.renderPageTabs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Design cleared'), indicator: 'yellow' });
    }

    applyGridSize() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const newRows = parseInt(container.querySelector('#spd-rows')?.value) || 20;
        const newCols = parseInt(container.querySelector('#spd-cols')?.value) || 15;
        const oldRows = this.rows;
        const oldCols = this.cols;
        this.rows = newRows;
        this.cols = newCols;
        this.frm.set_value('rows', newRows);
        this.frm.set_value('columns', newCols);

        // Resize grid for every page (preserve existing cells when shrinking/growing)
        const defaultCss = this.getDefaultCellCss();
        for (const pageNo of Object.keys(this.pages)) {
            const page = this.pages[pageNo];
            const newGrid = Array.from({ length: newRows }, () => Array(newCols).fill(null));
            const newMap = {};
            for (let r = 0; r < newRows; r++) {
                for (let c = 0; c < newCols; c++) {
                    const existing = page.grid[r]?.[c];
                    if (existing && !existing._merged) {
                        // Clamp rowspan/colspan to new bounds
                        const maxRs = Math.min(existing.rowspan || 1, newRows - r);
                        const maxCs = Math.min(existing.colspan || 1, newCols - c);
                        existing.rowspan = Math.max(1, maxRs);
                        existing.colspan = Math.max(1, maxCs);
                        newGrid[r][c] = existing;
                        newMap[existing.cell_id] = existing;
                    } else if (existing && existing._merged) {
                        // Will be re-marked below if master still spans here
                    }
                }
            }
            page.grid = newGrid;
            page.cellDataMap = newMap;
            // Re-mark merged cells
            for (const cell of Object.values(newMap)) {
                if (cell.rowspan > 1 || cell.colspan > 1) {
                    this._markMergedInPage(page, cell);
                }
            }
            // Fill blanks
            for (let r = 0; r < newRows; r++) {
                for (let c = 0; c < newCols; c++) {
                    if (!page.grid[r][c]) {
                        const id = `P${pageNo}_R${r + 1}C${c + 1}`;
                        const cellData = {
                            cell_id: id, page_no: parseInt(pageNo), row: r + 1, col: c + 1,
                            rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '',
                            css_style: defaultCss, row_type: '', row_display: ''
                        };
                        page.grid[r][c] = cellData;
                        page.cellDataMap[id] = cellData;
                    }
                }
            }
        }
        // row_styles: trim/keep keys, do not shift (rows are positional)
        if (newRows < oldRows) {
            for (const k of Object.keys(this.rowStyles)) {
                if (parseInt(k) > newRows) delete this.rowStyles[k];
            }
        }
        if (newCols < oldCols) {
            for (const k of Object.keys(this.colStyles)) {
                if (parseInt(k) > newCols) delete this.colStyles[k];
            }
        }
        this._activatePage(this.currentPageNo);
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Grid updated to') + ' ' + newRows + 'x' + newCols, indicator: 'green' });
    }

    switchPage(pageNo) {
        pageNo = parseInt(pageNo);
        if (!this.pages[pageNo]) return;
        // Clear selection state to avoid stale references
        this.selectedCells = [];
        this.selectedRow = null;
        this.selectedCol = null;
        this.currentCell = null;
        this._activatePage(pageNo);
        this.renderPageTabs();
        this.refreshGrid();
        this.frm.dirty();
    }

    addPage() {
        const next = Object.keys(this.pages).map(p => parseInt(p)).reduce((a, b) => Math.max(a, b), 0) + 1;
        const defaultCss = this.getDefaultCellCss();
        const grid = Array(this.rows).fill().map(() => Array(this.cols).fill(null));
        const cellDataMap = {};
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                const id = `P${next}_R${r}C${c}`;
                const data = {
                    cell_id: id, page_no: next, row: r, col: c,
                    rowspan: 1, colspan: 1, cell_type: 'static',
                    cell_value: '', css_style: defaultCss, row_type: '', row_display: ''
                };
                grid[r - 1][c - 1] = data;
                cellDataMap[id] = data;
            }
        }
        this.pages[next] = { grid, cellDataMap };
        this.pageCount = Object.keys(this.pages).length;
        this.frm.set_value('page_count', this.pageCount);
        this._activatePage(next);
        this.renderPageTabs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Page {0} added').replace('{0}', next), indicator: 'green' });
    }

    duplicatePage() {
        const srcPageNo = parseInt(this.currentPageNo);
        const src = this.pages[srcPageNo];
        if (!src) return;
        const next = Object.keys(this.pages).map(p => parseInt(p)).reduce((a, b) => Math.max(a, b), 0) + 1;

        // Build new grid — deep-copy every cell with new page_no and cell_id prefix
        const grid = Array.from({ length: this.rows }, () => Array(this.cols).fill(null));
        const cellDataMap = {};
        const idMap = {};  // old cell_id → new cell_id, for merged master_cell_id rewrite

        // Pass 1: clone master cells (non-merged)
        for (let r = 0; r < this.rows; r++) {
            for (let c = 0; c < this.cols; c++) {
                const cell = src.grid[r][c];
                if (!cell || cell._merged) continue;
                const newId = `P${next}_R${r + 1}C${c + 1}`;
                idMap[cell.cell_id] = newId;
                const copy = {
                    ...cell,
                    cell_id: newId,
                    page_no: next,
                    row: r + 1,
                    col: c + 1,
                };
                grid[r][c] = copy;
                cellDataMap[newId] = copy;
            }
        }
        // Pass 2: clone merged markers, rewriting master_cell_id to the new prefix
        for (let r = 0; r < this.rows; r++) {
            for (let c = 0; c < this.cols; c++) {
                const cell = src.grid[r][c];
                if (cell && cell._merged) {
                    const newMasterId = idMap[cell.master_cell_id] || cell.master_cell_id;
                    grid[r][c] = { _merged: true, master_cell_id: newMasterId };
                }
            }
        }

        this.pages[next] = { grid, cellDataMap };
        this.pageCount = Object.keys(this.pages).length;
        this.frm.set_value('page_count', this.pageCount);
        this._activatePage(next);
        this.renderPageTabs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({
            message: __('Page {0} duplicated from page {1}').replace('{0}', next).replace('{1}', srcPageNo),
            indicator: 'green'
        });
    }

    removePage(pageNo) {
        pageNo = parseInt(pageNo);
        const keys = Object.keys(this.pages).map(p => parseInt(p)).sort((a, b) => a - b);
        if (keys.length <= 1) {
            frappe.show_alert({ message: __('Cannot remove the last page'), indicator: 'red' });
            return;
        }
        if (!confirm(__('Remove page {0}? Its cells will be lost.').replace('{0}', pageNo))) return;
        delete this.pages[pageNo];
        // Renumber remaining pages so they stay contiguous starting at 1
        const sorted = Object.keys(this.pages).map(p => parseInt(p)).sort((a, b) => a - b);
        const newPages = {};
        sorted.forEach((oldNo, idx) => {
            const newNo = idx + 1;
            const page = this.pages[oldNo];
            // Rewrite page_no + cell_id prefixes on every cell
            for (let r = 0; r < this.rows; r++) {
                for (let c = 0; c < this.cols; c++) {
                    const cell = page.grid[r][c];
                    if (cell && !cell._merged) {
                        cell.page_no = newNo;
                        const newId = `P${newNo}_R${r + 1}C${c + 1}`;
                        // Update master_cell_id references in merged markers
                        for (let rr = 0; rr < this.rows; rr++) {
                            for (let cc = 0; cc < this.cols; cc++) {
                                const mc = page.grid[rr][cc];
                                if (mc && mc._merged && mc.master_cell_id === cell.cell_id) {
                                    mc.master_cell_id = newId;
                                }
                            }
                        }
                        delete page.cellDataMap[cell.cell_id];
                        cell.cell_id = newId;
                        page.cellDataMap[newId] = cell;
                    }
                }
            }
            newPages[newNo] = page;
        });
        this.pages = newPages;
        this.pageCount = Object.keys(this.pages).length;
        this.frm.set_value('page_count', this.pageCount);
        this._activatePage(1);
        this.renderPageTabs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Page removed'), indicator: 'yellow' });
    }

    renderPageTabs() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const tabsEl = container.querySelector('#spd-page-tabs');
        if (!tabsEl) return;
        const keys = Object.keys(this.pages).map(p => parseInt(p)).sort((a, b) => a - b);
        tabsEl.innerHTML = keys.map(p => {
            const active = (p === this.currentPageNo) ? 'spd-page-tab-active' : '';
            return `<button type="button" class="btn btn-xs btn-default spd-page-tab ${active}" data-page="${p}" style="margin-right:4px;">
                <i class="fa fa-file-o" style="margin-right:3px;"></i>${__('Page')} ${p}
            </button>`;
        }).join('');
        const removeBtn = container.querySelector('#spd-remove-page-btn');
        if (removeBtn) removeBtn.style.display = keys.length > 1 ? '' : 'none';
    }

    refreshGrid() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const grid = container.querySelector('#spd-grid');
        if (grid) {
            grid.innerHTML = this.generateGridHtml();
            grid.style.fontFamily = "'" + this.fontFamily + "', sans-serif";
        }
        // Update row/col headers
        const colHeaders = container.querySelector('#spd-col-headers');
        const rowHeaders = container.querySelector('#spd-row-headers');
        if (colHeaders) colHeaders.innerHTML = this.generateColHeadersHtml();
        if (rowHeaders) rowHeaders.innerHTML = this.generateRowHeadersHtml();
        // Offset sync
        const PX_PER_MM = 4;
        const mTop = (this.marginTop || 0) * PX_PER_MM;
        const mLeft = (this.marginLeft || 0) * PX_PER_MM;
        const mRight = (this.marginRight || 0) * PX_PER_MM;
        if (rowHeaders) {
            rowHeaders.style.marginTop = mTop + 'px';
        }
        if (colHeaders) {
            let totalWidth = 0;
            for (let col = 1; col <= this.cols; col++) {
                totalWidth += (this.colStyles[col]?.width || 60);
            }
            const paperW = (this.paperWidth || 210) * PX_PER_MM;
            const contentAreaW = paperW - mLeft - mRight;
            const centeredOffset = Math.max(0, (contentAreaW - totalWidth) / 2);
            colHeaders.style.marginLeft = (22 + mLeft + centeredOffset) + 'px';
        }
        // Update header/footer
        this.pageHeaderLeft = this.frm.doc.page_header_left || '';
        this.pageHeaderCenter = this.frm.doc.page_header_center || '';
        this.pageHeaderRight = this.frm.doc.page_header_right || '';
        this.pageFooterLeft = this.frm.doc.page_footer_left || '';
        this.pageFooterCenter = this.frm.doc.page_footer_center || '';
        this.pageFooterRight = this.frm.doc.page_footer_right || '';
        this.pageHeaderAlign = this.frm.doc.page_header_align || 'Center';
        this.pageFooterAlign = this.frm.doc.page_footer_align || 'Center';
        this.pageHeaderLeftAlign = this.frm.doc.page_header_left_align || 'Left';
        this.pageHeaderCenterAlign = this.frm.doc.page_header_center_align || 'Center';
        this.pageHeaderRightAlign = this.frm.doc.page_header_right_align || 'Right';
        this.pageFooterLeftAlign = this.frm.doc.page_footer_left_align || 'Left';
        this.pageFooterCenterAlign = this.frm.doc.page_footer_center_align || 'Center';
        this.pageFooterRightAlign = this.frm.doc.page_footer_right_align || 'Right';
        this._renderHeaderFooterPreview();
        const headerArea = container.querySelector('#spd-header-area');
        const footerArea = container.querySelector('#spd-footer-area');
        const mBottom = (this.marginBottom || 0) * PX_PER_MM;
        if (headerArea) {
            const hl = this.pageHeaderLeft || '';
            const hc = this.pageHeaderCenter || '';
            const hr = this.pageHeaderRight || '';
            const hasH = hl || hc || hr;
            const placeholder = '<span style="color:#ccc;font-size:10px;">' + __('Header Area') + '</span>';
            headerArea.innerHTML =
                '<div style="flex:1;text-align:left;padding-left:' + mLeft + 'px;">' + (hl || (hasH ? '' : placeholder)) + '</div>' +
                '<div style="flex:1;text-align:center;">' + (hc || '') + '</div>' +
                '<div style="flex:1;text-align:right;padding-right:' + mRight + 'px;">' + (hr || '') + '</div>';
        }
        if (footerArea) {
            const fl = this.pageFooterLeft || '';
            const fc = this.pageFooterCenter || '';
            const fr_ = this.pageFooterRight || '';
            const hasF = fl || fc || fr_;
            const placeholder = '<span style="color:#ccc;font-size:10px;">' + __('Footer Area') + '</span>';
            footerArea.innerHTML =
                '<div style="flex:1;text-align:left;padding-left:' + mLeft + 'px;">' + (fl || (hasF ? '' : placeholder)) + '</div>' +
                '<div style="flex:1;text-align:center;">' + (fc || '') + '</div>' +
                '<div style="flex:1;text-align:right;padding-right:' + mRight + 'px;">' + (fr_ || '') + '</div>';
        }
        setTimeout(() => this.syncRowHeaderHeights(), 50);
    }

    parseCellId(cellId) {
        const m = cellId?.match(/R(\d+)C(\d+)/);
        return m ? [parseInt(m[1]), parseInt(m[2])] : [0, 0];
    }

    parseCssString(str) {
        const pairs = {};
        if (!str) return pairs;
        str.split(';').forEach(pair => {
            const trimmed = pair.trim();
            if (trimmed && trimmed.includes(':')) {
                const [k, ...v] = trimmed.split(':');
                pairs[k.trim()] = v.join(':').trim();
            }
        });
        return pairs;
    }

    escapeHtml(text) {
        if (!text) return '';
        const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
        return text.toString().replace(/[&<>"']/g, m => map[m]);
    }

    // Query definition dialog
    showQueryDefinitionDialog() {
        const queries = this.frm.doc.design_queries || [];

        let queryListHtml = '';
        if (queries.length > 0) {
            queryListHtml = queries.map((query, index) => {
                return '<tr data-index="' + index + '">' +
                    '<td style="padding:8px;border:1px solid #dee2e6;">' + frappe.utils.escape_html(query.query_name || '') + '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-default btn-edit-query" data-index="' + index + '" title="' + __('Edit') + '"><i class="fa fa-edit"></i></button>' +
                    '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-info btn-preview-query" data-index="' + index + '" title="' + __('Preview') + '"><i class="fa fa-eye"></i></button>' +
                    '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-danger btn-delete-query" data-index="' + index + '" title="' + __('Delete') + '"><i class="fa fa-trash"></i></button>' +
                    '</td>' +
                '</tr>';
            }).join('');
        } else {
            queryListHtml = '<tr><td colspan="4" style="padding:20px;text-align:center;color:#6c757d;">' + __('No query definitions') + '</td></tr>';
        }

        const dialogContent = '<div class="query-definition-dialog">' +
            '<div style="margin-bottom:15px;">' +
                '<button type="button" class="btn btn-primary btn-sm btn-add-query"><i class="fa fa-plus"></i> ' + __('Add Query') + '</button>' +
            '</div>' +
            '<table class="table table-bordered" style="margin-bottom:0;">' +
                '<thead><tr style="background:#f8f9fa;">' +
                    '<th style="padding:8px;border:1px solid #dee2e6;width:40%;">' + __('Query Name') + '</th>' +
                    '<th style="padding:8px;border:1px solid #dee2e6;width:20%;">' + __('Edit') + '</th>' +
                    '<th style="padding:8px;border:1px solid #dee2e6;width:20%;">' + __('Preview') + '</th>' +
                    '<th style="padding:8px;border:1px solid #dee2e6;width:20%;">' + __('Delete') + '</th>' +
                '</tr></thead>' +
                '<tbody id="query-list-body">' + queryListHtml + '</tbody>' +
            '</table>' +
        '</div>';

        const dialog = new frappe.ui.Dialog({
            title: __('Query Definition'),
            fields: [{ fieldtype: 'HTML', options: dialogContent }],
            size: 'large',
            primary_action_label: __('Close'),
            primary_action: () => { dialog.hide(); }
        });

        dialog.show();

        setTimeout(() => {
            dialog.$wrapper.find('.btn-add-query').on('click', () => {
                this.showQueryEditDialog(dialog, -1);
            });
            dialog.$wrapper.find('.btn-edit-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                this.showQueryEditDialog(dialog, index);
            });
            dialog.$wrapper.find('.btn-preview-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                this.previewQueryByIndex(index);
            });
            dialog.$wrapper.find('.btn-delete-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                frappe.confirm(__('Are you sure you want to delete this query?'), () => {
                    this.frm.doc.design_queries.splice(index, 1);
                    this.refreshQueryList(dialog);
                    frappe.show_alert({ message: __('Query deleted'), indicator: 'orange' });
                });
            });
        }, 100);
    }

    previewQueryByIndex(index) {
        const queries = this.frm.doc.design_queries || [];
        if (index < 0 || index >= queries.length) {
            frappe.msgprint(__('Query not found'));
            return;
        }

        const query = queries[index];
        const queryCode = query.query_code || '';
        const parameters = query.parameters || '';

        if (!queryCode || !queryCode.trim()) {
            frappe.msgprint(__('No query code defined'));
            return;
        }

        const previewDialog = new frappe.ui.Dialog({
            title: __('Preview Query') + ' - ' + query.query_name,
            size: 'large',
            fields: [{
                fieldname: 'result_content',
                fieldtype: 'HTML',
                options: '<div class="query-result-preview" style="max-height:400px;overflow-y:auto;background:#f8f9fa;border:1px solid #dee2e6;border-radius:4px;padding:10px;">' +
                    '<div id="preview-loading" style="text-align:center;padding:20px;"><i class="fa fa-spinner fa-spin"></i> ' + __('Loading...') + '</div>' +
                    '<pre id="preview-content" style="display:none;margin:0;white-space:pre-wrap;word-break:break-all;font-size:12px;"></pre>' +
                '</div>'
            }],
            primary_action_label: __('Close'),
            primary_action: () => { previewDialog.hide(); }
        });

        previewDialog.show();

        const targetDoctype = this.frm.doc.target_doctype;
        const docName = this.frm.doc.sample_doc || '';
        frappe.call({
            method: 'zhiz_print.zhiz_print.doctype.super_print_design.super_print_design.preview_query_with_doc',
            args: {
                query_code: queryCode,
                parameters: parameters,
                target_doctype: targetDoctype,
                doc_name: docName,
            },
            callback: (response) => {
                const $loading = previewDialog.$wrapper.find('#preview-loading');
                const $content = previewDialog.$wrapper.find('#preview-content');

                $loading.hide();
                $content.show();

                if (response.message !== undefined) {
                    let resultStr;
                    try {
                        resultStr = JSON.stringify(response.message, null, 2);
                    } catch (e) {
                        resultStr = String(response.message);
                    }
                    if (resultStr.length > 5000) {
                        resultStr = resultStr.substring(0, 5000) + '\n\n... (' + __('truncated') + ')';
                    }
                    $content.text(resultStr);
                } else {
                    $content.html('<span class="text-muted">' + __('Query returned empty result') + '</span>');
                }
            },
            error: (err) => {
                const $loading = previewDialog.$wrapper.find('#preview-loading');
                const $content = previewDialog.$wrapper.find('#preview-content');
                $loading.hide();
                $content.show();
                let errorMsg = __('Query execution failed');
                if (err && err.message) errorMsg = err.message;
                else if (err && err.responseJSON && err.responseJSON.message) errorMsg = err.responseJSON.message;
                $content.html('<span class="text-danger"><i class="fa fa-exclamation-triangle"></i> ' + frappe.utils.escape_html(errorMsg) + '</span>');
            }
        });
    }

    showQueryEditDialog(parentDialog, editIndex) {
        const queries = this.frm.doc.design_queries || [];
        const isEdit = editIndex >= 0;
        const query = isEdit ? queries[editIndex] : { query_name: '', query_code: '', parameters: '' };

        const editDialog = new frappe.ui.Dialog({
            title: isEdit ? __('Edit Query') : __('Add Query'),
            fields: [
                {
                    fieldname: 'query_name',
                    label: __('Query Name'),
                    fieldtype: 'Data',
                    reqd: 1,
                    default: query.query_name,
                    description: __('English letters, digits and underscores only, must start with a letter')
                },
                {
                    fieldname: 'query_code',
                    label: __('Python Query Code'),
                    fieldtype: 'Code',
                    options: 'Python',
                    default: query.query_code || '',
                    description: __('Assign query result to variable result')
                },
                {
                    fieldname: 'parameters',
                    label: __('Query Parameters'),
                    fieldtype: 'Code',
                    options: 'Python',
                    default: query.parameters || '',
                    description: __('One parameter per line, format: key=value')
                },
                {
                    fieldname: 'preview_result',
                    label: '',
                    fieldtype: 'HTML',
                    options: '<div id="query-preview-container" style="display:none;margin-top:10px;"><div class="alert alert-info" style="max-height:200px;overflow-y:auto;"><strong>' + __('Preview Result') + ':</strong><pre id="query-preview-content" style="margin:5px 0;white-space:pre-wrap;word-break:break-all;"></pre></div></div>'
                }
            ],
            size: 'large',
            primary_action_label: isEdit ? __('Update') : __('Add'),
            primary_action: (values) => {
                if (!/^[a-zA-Z][a-zA-Z0-9_]*$/.test(values.query_name)) {
                    frappe.msgprint(__('Query name must start with a letter and contain only letters, digits and underscores'));
                    return;
                }

                if (isEdit) {
                    queries[editIndex] = values;
                } else {
                    const existingNames = queries.map(q => q.query_name);
                    if (existingNames.includes(values.query_name)) {
                        frappe.msgprint(__('Query name already exists'));
                        return;
                    }
                    if (!this.frm.doc.design_queries) {
                        this.frm.doc.design_queries = [];
                    }
                    this.frm.doc.design_queries.push(values);
                }
                this.refreshQueryList(parentDialog);
                editDialog.hide();
                frappe.show_alert({ message: isEdit ? __('Query updated') : __('Query added'), indicator: 'green' });
            }
        });

        // Add preview button as secondary action
        editDialog.get_secondary_btn().show();
        editDialog.set_secondary_action_label(__('Preview Result'));
        editDialog.set_secondary_action(() => {
            this.previewQueryResult(editDialog);
        });

        // Layout: query_code + parameters on same row
        setTimeout(() => {
            const $form = editDialog.$wrapper.find('.form-layout');

            // Row: query_code + parameters
            const $queryCode = $form.find('[data-fieldname="query_code"]').closest('.frappe-control');
            const $parameters = $form.find('[data-fieldname="parameters"]').closest('.frappe-control');

            if ($queryCode.length && $parameters.length) {
                const $row2 = $('<div class="query-edit-row" style="display:flex;gap:15px;margin-bottom:0;"></div>');
                $queryCode.before($row2);
                $queryCode.css({ flex: '6', 'min-width': '0' });
                $parameters.css({ flex: '4', 'min-width': '0' });
                $row2.append($queryCode).append($parameters);

                setTimeout(() => {
                    const $codeEditor = $queryCode.find('.ace_editor, .CodeMirror, textarea');
                    const $paramTextarea = $parameters.find('textarea');
                    if ($codeEditor.length) $codeEditor.css({ 'min-height': '200px' });
                    if ($paramTextarea.length) $paramTextarea.css({ 'min-height': '200px', height: '200px' });
                }, 200);
            }
        }, 100);

        editDialog.show();
    }

    previewQueryResult(dialog) {
        const queryCode = dialog.get_value('query_code');
        const parameters = dialog.get_value('parameters') || '';

        if (!queryCode || !queryCode.trim()) {
            frappe.msgprint(__('Please enter query code first'));
            return;
        }

        const $container = dialog.$wrapper.find('#query-preview-container');
        const $content = dialog.$wrapper.find('#query-preview-content');

        $container.show();
        $content.html('<i class="fa fa-spinner fa-spin"></i> ' + __('Executing query...'));

        const targetDoctype = this.frm.doc.target_doctype;
        const docName = this.frm.doc.sample_doc || '';
        frappe.call({
            method: 'zhiz_print.zhiz_print.doctype.super_print_design.super_print_design.preview_query_with_doc',
            args: {
                query_code: queryCode,
                parameters: parameters,
                target_doctype: targetDoctype,
                doc_name: docName,
            },
            callback: (response) => {
                if (response.message !== undefined) {
                    let resultStr;
                    try {
                        resultStr = JSON.stringify(response.message, null, 2);
                    } catch (e) {
                        resultStr = String(response.message);
                    }
                    if (resultStr.length > 5000) {
                        resultStr = resultStr.substring(0, 5000) + '\n\n... (' + __('truncated') + ')';
                    }
                    $content.text(resultStr);
                } else {
                    $content.html('<span class="text-muted">' + __('Query returned empty result') + '</span>');
                }
            },
            error: (err) => {
                let errorMsg = __('Query execution failed');
                if (err && err.message) errorMsg = err.message;
                else if (err && err.responseJSON && err.responseJSON.message) errorMsg = err.responseJSON.message;
                $content.html('<span class="text-danger"><i class="fa fa-exclamation-triangle"></i> ' + frappe.utils.escape_html(errorMsg) + '</span>');
            }
        });
    }

    refreshQueryList(dialog) {
        const queries = this.frm.doc.design_queries || [];
        const tbody = dialog.$wrapper.find('#query-list-body');

        if (queries.length > 0) {
            let queryListHtml = queries.map((query, index) => {
                return '<tr data-index="' + index + '">' +
                    '<td style="padding:8px;border:1px solid #dee2e6;">' + frappe.utils.escape_html(query.query_name || '') + '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-default btn-edit-query" data-index="' + index + '" title="' + __('Edit') + '"><i class="fa fa-edit"></i></button>' +
                    '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-info btn-preview-query" data-index="' + index + '" title="' + __('Preview') + '"><i class="fa fa-eye"></i></button>' +
                    '</td>' +
                    '<td style="padding:8px;border:1px solid #dee2e6;text-align:center;">' +
                        '<button type="button" class="btn btn-xs btn-danger btn-delete-query" data-index="' + index + '" title="' + __('Delete') + '"><i class="fa fa-trash"></i></button>' +
                    '</td>' +
                '</tr>';
            }).join('');
            tbody.html(queryListHtml);

            tbody.find('.btn-edit-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                this.showQueryEditDialog(dialog, index);
            });
            tbody.find('.btn-preview-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                this.previewQueryByIndex(index);
            });
            tbody.find('.btn-delete-query').on('click', (e) => {
                const index = parseInt($(e.currentTarget).data('index'));
                frappe.confirm(__('Are you sure you want to delete this query?'), () => {
                    this.frm.doc.design_queries.splice(index, 1);
                    this.refreshQueryList(dialog);
                    frappe.show_alert({ message: __('Query deleted'), indicator: 'orange' });
                });
            });
        } else {
            tbody.html('<tr><td colspan="4" style="padding:20px;text-align:center;color:#6c757d;">' + __('No query definitions') + '</td></tr>');
        }
    }

    // Parameter definition dialog
    showParamsDialog() {
        const params = this.frm.doc.design_parameters || [];
        let listHtml = params.length > 0
            ? params.map((p, i) => '<tr>' +
                '<td>' + p.param_name + '</td><td>' + p.param_type + '</td><td>' + (p.default_value || '') + '</td>' +
                '<td><button class="btn btn-xs btn-default edit-p" data-idx="' + i + '"><i class="fa fa-edit"></i></button></td>' +
                '<td><button class="btn btn-xs btn-danger del-p" data-idx="' + i + '"><i class="fa fa-trash"></i></button></td>' +
              '</tr>').join('')
            : '<tr><td colspan="5" class="text-muted text-center">' + __('No parameter definitions') + '</td></tr>';

        const dialog = new frappe.ui.Dialog({
            title: __('Parameter Management'),
            fields: [{ fieldtype: 'HTML', options: '<div><button class="btn btn-primary btn-sm add-p">' + __('Add Parameter') + '</button><table class="table table-bordered mt-2"><thead><tr><th>' + __('Name') + '</th><th>' + __('Type') + '</th><th>' + __('Default') + '</th><th>' + __('Edit') + '</th><th>' + __('Delete') + '</th></tr></thead><tbody>' + listHtml + '</tbody></table></div>' }],
            primary_action_label: __('Close'),
            primary_action: () => dialog.hide()
        });
        dialog.show();

        setTimeout(() => {
            dialog.$wrapper.find('.add-p').on('click', () => this.showParamEditDialog(dialog, -1));
            dialog.$wrapper.find('.edit-p').on('click', (e) => this.showParamEditDialog(dialog, parseInt($(e.currentTarget).data('idx'))));
            dialog.$wrapper.find('.del-p').on('click', (e) => {
                const idx = parseInt($(e.currentTarget).data('idx'));
                frappe.confirm(__('Are you sure you want to delete this parameter?'), () => {
                    this.frm.doc.design_parameters.splice(idx, 1);
                    this.refreshParamsList(dialog);
                });
            });
        }, 100);
    }

    showParamEditDialog(parentDialog, editIndex) {
        const params = this.frm.doc.design_parameters || [];
        const isEdit = editIndex >= 0;
        const p = isEdit ? params[editIndex] : { param_name: '', param_label: '', param_type: 'Data', default_value: '', reqd: 0, options: '' };

        const editDialog = new frappe.ui.Dialog({
            title: isEdit ? __('Edit Parameter') : __('Add Parameter'),
            fields: [
                { fieldname: 'param_name', label: __('Parameter Id'), fieldtype: 'Data', reqd: 1, default: p.param_name },
                { fieldname: 'param_label', label: __('Display Label'), fieldtype: 'Data', default: p.param_label },
                { fieldname: 'param_type', label: __('Type'), fieldtype: 'Select', options: 'Data\nInt\nFloat\nDate\nLink\nSelect', default: p.param_type || 'Data' },
                { fieldname: 'default_value', label: __('Default Value'), fieldtype: 'Data', default: p.default_value },
                { fieldname: 'reqd', label: __('Required'), fieldtype: 'Check', default: p.reqd },
                { fieldname: 'options', label: __('Options / Target DocType'), fieldtype: 'Small Text', default: p.options }
            ],
            primary_action_label: isEdit ? __('Update') : __('Add'),
            primary_action: (values) => {
                if (isEdit) params[editIndex] = values;
                else {
                    if (!this.frm.doc.design_parameters) this.frm.doc.design_parameters = [];
                    this.frm.doc.design_parameters.push(values);
                }
                this.refreshParamsList(parentDialog);
                editDialog.hide();
            }
        });
        editDialog.show();
    }

    refreshParamsList(dialog) {
        this.showParamsDialog();
        dialog.hide();
    }

    insertRowAt(row) {
        if (!row || row < 1 || row > this.rows) return;

        const oldRows = this.rows;
        const oldCols = this.cols;

        // Step 1: For each column, check if the cell above the inserted row
        // is part of a merged cell whose rowspan spans across the insertion point.
        // If so, that merged cell's rowspan increases by 1 (new row is also merged).
        // If not, the cell above stays independent (new row cell is also independent).
        const mergedCellsExpanded = new Set(); // cellIds of merged cells that got rowspan+1

        const masters = [];
        for (const [cellId, cell] of Object.entries(this.cellDataMap)) {
            if (cell._merged) continue;
            const endRow = cell.row + cell.rowspan - 1;
            let newRow = cell.row;
            let newRowspan = cell.rowspan;

            // Check: does this merged cell span across the insertion row?
            // A cell spans across 'row' if cell.row < row AND endRow >= row.
            // Strict < (not <=): inserting at the master's own start row means
            // "insert above this cell" — the whole master shifts down, rowspan
            // must NOT grow. Only rows inserted strictly inside the merge area
            // get absorbed (rowspan+1).
            if (cell.rowspan > 1 && cell.row < row && endRow >= row) {
                newRowspan += 1;
                mergedCellsExpanded.add(cellId);
            }

            if (cell.row >= row) {
                newRow += 1;
            }

            masters.push({ ...cell, newRow, newRowspan });
        }

        // Step 2: Rebuild grid
        this.rows = oldRows + 1;
        this.grid = Array.from({ length: this.rows }, () => Array(oldCols).fill(null));
        this.cellDataMap = {};

        for (const m of masters) {
            m.row = m.newRow;
            m.rowspan = m.newRowspan;
            delete m.newRow;
            delete m.newRowspan;
            const newId = `P${m.page_no || this.currentPageNo}_R${m.row}C${m.col}`;
            m.cell_id = newId;
            this.cellDataMap[newId] = m;
            this.grid[m.row - 1][m.col - 1] = m;
        }

        const defaultCss = this.getDefaultCellCss();
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= oldCols; c++) {
                if (!this.grid[r - 1][c - 1]) {
                    const id = `P${this.currentPageNo}_R${r}C${c}`;
                    const data = { cell_id: id, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                    this.grid[r - 1][c - 1] = data;
                    this.cellDataMap[id] = data;
                }
            }
        }

        for (const [cellId, master] of Object.entries(this.cellDataMap)) {
            if (master.rowspan > 1 || master.colspan > 1) {
                for (let mr = master.row; mr < master.row + master.rowspan; mr++) {
                    for (let mc = master.col; mc < master.col + master.colspan; mc++) {
                        if (mr === master.row && mc === master.col) continue;
                        if (mr <= this.rows && mc <= this.cols) {
                            this.grid[mr - 1][mc - 1] = { _merged: true, master_cell_id: cellId };
                        }
                    }
                }
            }
        }

        const newRowStyles = {};
        for (const [key, val] of Object.entries(this.rowStyles)) {
            const r = parseInt(key);
            if (r >= row) { newRowStyles[r + 1] = val; }
            else { newRowStyles[r] = val; }
        }
        this.rowStyles = newRowStyles;

        // Re-sync current page's grid/map references (insert/rebuild breaks reference)
        this._syncCurrentPage();

        this.frm.set_value('rows', this.rows);
        this._updateToolbarInputs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Row {0} inserted').replace('{0}', row), indicator: 'green' });
    }

    _syncCurrentPage() {
        if (!this.pages[this.currentPageNo]) return;
        this.pages[this.currentPageNo].grid = this.grid;
        this.pages[this.currentPageNo].cellDataMap = this.cellDataMap;
    }

    insertColAt(col) {
        if (!col || col < 1 || col > this.cols) return;

        const oldRows = this.rows;
        const oldCols = this.cols;

        const masters = [];
        for (const [cellId, cell] of Object.entries(this.cellDataMap)) {
            if (cell._merged) continue;
            const endCol = cell.col + cell.colspan - 1;
            let newCol = cell.col;
            let newColspan = cell.colspan;

            // Check: does this merged cell span across the insertion col?
            // Strict < (not <=): inserting at the master's own start col means
            // "insert left of this cell" — the whole master shifts right,
            // colspan must NOT grow. Only cols inserted strictly inside the
            // merge area get absorbed (colspan+1).
            if (cell.colspan > 1 && cell.col < col && endCol >= col) {
                newColspan += 1;
            }

            if (cell.col >= col) {
                newCol += 1;
            }

            masters.push({ ...cell, newCol, newColspan });
        }

        this.cols = oldCols + 1;
        this.grid = Array.from({ length: oldRows }, () => Array(this.cols).fill(null));
        this.cellDataMap = {};

        for (const m of masters) {
            m.col = m.newCol;
            m.colspan = m.newColspan;
            delete m.newCol;
            delete m.newColspan;
            const newId = `P${m.page_no || this.currentPageNo}_R${m.row}C${m.col}`;
            m.cell_id = newId;
            this.cellDataMap[newId] = m;
            this.grid[m.row - 1][m.col - 1] = m;
        }

        const defaultCss = this.getDefaultCellCss();
        for (let r = 1; r <= oldRows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                if (!this.grid[r - 1][c - 1]) {
                    const id = `P${this.currentPageNo}_R${r}C${c}`;
                    const data = { cell_id: id, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                    this.grid[r - 1][c - 1] = data;
                    this.cellDataMap[id] = data;
                }
            }
        }

        for (const [cellId, master] of Object.entries(this.cellDataMap)) {
            if (master.rowspan > 1 || master.colspan > 1) {
                for (let mr = master.row; mr < master.row + master.rowspan; mr++) {
                    for (let mc = master.col; mc < master.col + master.colspan; mc++) {
                        if (mr === master.row && mc === master.col) continue;
                        if (mr <= this.rows && mc <= this.cols) {
                            this.grid[mr - 1][mc - 1] = { _merged: true, master_cell_id: cellId };
                        }
                    }
                }
            }
        }

        const newColStyles = {};
        for (const [key, val] of Object.entries(this.colStyles)) {
            const c = parseInt(key);
            if (c >= col) { newColStyles[c + 1] = val; }
            else { newColStyles[c] = val; }
        }
        this.colStyles = newColStyles;

        this._syncCurrentPage();

        this.frm.set_value('columns', this.cols);
        this._updateToolbarInputs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Column {0} inserted').replace('{0}', col), indicator: 'green' });
    }

    _updateToolbarInputs() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const rowsInput = container.querySelector('#spd-rows');
        const colsInput = container.querySelector('#spd-cols');
        if (rowsInput) rowsInput.value = this.rows;
        if (colsInput) colsInput.value = this.cols;
    }

    _estimateFontSize(text, cellW, cellH, baseFontSize) {
        if (!text) return baseFontSize;
        const charCount = text.length;
        if (charCount === 0) return baseFontSize;
        const avgCharW = baseFontSize * 0.7;
        const charsPerLine = Math.max(1, cellW / avgCharW);
        const linesNeeded = Math.max(1, charCount / charsPerLine);
        const totalHeight = linesNeeded * baseFontSize * 1.2;
        if (totalHeight <= cellH) return baseFontSize;
        const ratio = cellH / totalHeight;
        return Math.max(6, Math.floor(baseFontSize * ratio));
    }

    deleteRowAt(row) {
        if (!row || row < 1 || row > this.rows) return;
        if (this.rows <= 1) {
            frappe.show_alert({ message: __('Cannot delete the last row'), indicator: 'red' });
            return;
        }
        const deleteIdx = row - 1;

        // Check: if any master cell starts at this row and has rowspan > 1, shrink rowspan
        // If any master cell spans across this row (starts above, ends at or below), shrink rowspan
        // If any master cell is entirely below, shift up
        const oldRows = this.rows;
        const oldCols = this.cols;
        const masters = [];
        for (const [cellId, cell] of Object.entries(this.cellDataMap)) {
            if (cell._merged) continue;
            const endRow = cell.row + cell.rowspan - 1;
            let newRow = cell.row;
            let newRowspan = cell.rowspan;

            if (cell.row === row && cell.row === endRow) {
                // Single-row cell at the deleted row — skip it entirely
                continue;
            }
            if (cell.row <= row && endRow >= row) {
                // Merge spans across or touches the deleted row — shrink rowspan
                newRowspan -= 1;
            }
            if (cell.row > row) {
                // Entirely below — shift up
                newRow -= 1;
            }
            masters.push({ ...cell, newRow, newRowspan });
        }

        this.rows = oldRows - 1;
        this.grid = Array.from({ length: this.rows }, () => Array(oldCols).fill(null));
        this.cellDataMap = {};

        for (const m of masters) {
            m.row = m.newRow;
            m.rowspan = m.newRowspan;
            delete m.newRow;
            delete m.newRowspan;
            if (m.rowspan < 1) m.rowspan = 1;
            const newId = `P${m.page_no || this.currentPageNo}_R${m.row}C${m.col}`;
            m.cell_id = newId;
            this.cellDataMap[newId] = m;
            this.grid[m.row - 1][m.col - 1] = m;
        }

        const defaultCss = this.getDefaultCellCss();
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= oldCols; c++) {
                if (!this.grid[r - 1][c - 1]) {
                    const id = `P${this.currentPageNo}_R${r}C${c}`;
                    const data = { cell_id: id, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                    this.grid[r - 1][c - 1] = data;
                    this.cellDataMap[id] = data;
                }
            }
        }

        for (const [cellId, master] of Object.entries(this.cellDataMap)) {
            if (master.rowspan > 1 || master.colspan > 1) {
                for (let mr = master.row; mr < master.row + master.rowspan; mr++) {
                    for (let mc = master.col; mc < master.col + master.colspan; mc++) {
                        if (mr === master.row && mc === master.col) continue;
                        if (mr <= this.rows && mc <= this.cols) {
                            this.grid[mr - 1][mc - 1] = { _merged: true, master_cell_id: cellId };
                        }
                    }
                }
            }
        }

        const newRowStyles = {};
        for (const [key, val] of Object.entries(this.rowStyles)) {
            const r = parseInt(key);
            if (r === row) continue;
            if (r > row) { newRowStyles[r - 1] = val; }
            else { newRowStyles[r] = val; }
        }
        this.rowStyles = newRowStyles;

        this.selectedRow = null;
        this._syncCurrentPage();

        this.frm.set_value('rows', this.rows);
        this._updateToolbarInputs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Row {0} deleted').replace('{0}', row), indicator: 'green' });
    }

    deleteColAt(col) {
        if (!col || col < 1 || col > this.cols) return;
        if (this.cols <= 1) {
            frappe.show_alert({ message: __('Cannot delete the last column'), indicator: 'red' });
            return;
        }
        const deleteIdx = col - 1;

        const oldRows = this.rows;
        const oldCols = this.cols;
        const masters = [];
        for (const [cellId, cell] of Object.entries(this.cellDataMap)) {
            if (cell._merged) continue;
            const endCol = cell.col + cell.colspan - 1;
            let newCol = cell.col;
            let newColspan = cell.colspan;

            if (cell.col === col && cell.col === endCol) {
                // Single-col cell at the deleted column — skip it
                continue;
            }
            if (cell.col <= col && endCol >= col) {
                // Merge spans across or touches the deleted column — shrink colspan
                newColspan -= 1;
            }
            if (cell.col > col) {
                // Entirely to the right — shift left
                newCol -= 1;
            }
            masters.push({ ...cell, newCol, newColspan });
        }

        this.cols = oldCols - 1;
        this.grid = Array.from({ length: oldRows }, () => Array(this.cols).fill(null));
        this.cellDataMap = {};

        for (const m of masters) {
            m.col = m.newCol;
            m.colspan = m.newColspan;
            delete m.newCol;
            delete m.newColspan;
            if (m.colspan < 1) m.colspan = 1;
            const newId = `P${m.page_no || this.currentPageNo}_R${m.row}C${m.col}`;
            m.cell_id = newId;
            this.cellDataMap[newId] = m;
            this.grid[m.row - 1][m.col - 1] = m;
        }

        const defaultCss = this.getDefaultCellCss();
        for (let r = 1; r <= oldRows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                if (!this.grid[r - 1][c - 1]) {
                    const id = `P${this.currentPageNo}_R${r}C${c}`;
                    const data = { cell_id: id, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                    this.grid[r - 1][c - 1] = data;
                    this.cellDataMap[id] = data;
                }
            }
        }

        for (const [cellId, master] of Object.entries(this.cellDataMap)) {
            if (master.rowspan > 1 || master.colspan > 1) {
                for (let mr = master.row; mr < master.row + master.rowspan; mr++) {
                    for (let mc = master.col; mc < master.col + master.colspan; mc++) {
                        if (mr === master.row && mc === master.col) continue;
                        if (mr <= this.rows && mc <= this.cols) {
                            this.grid[mr - 1][mc - 1] = { _merged: true, master_cell_id: cellId };
                        }
                    }
                }
            }
        }

        const newColStyles = {};
        for (const [key, val] of Object.entries(this.colStyles)) {
            const c = parseInt(key);
            if (c === col) continue;
            if (c > col) { newColStyles[c - 1] = val; }
            else { newColStyles[c] = val; }
        }
        this.colStyles = newColStyles;

        this.selectedCol = null;
        this._syncCurrentPage();

        this.frm.set_value('columns', this.cols);
        this._updateToolbarInputs();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Column {0} deleted').replace('{0}', col), indicator: 'green' });
    }

    bindPropertyEvents() { /* handled in bindEvents */ }
}

let spd_designer = null;

frappe.ui.form.on('Super Print Design', {
    setup(frm) {
        setTimeout(() => {
            if (frm.page.sidebar) frm.page.sidebar.hide();
        }, 300);
    },

    onload(frm) {
        if (frm.is_new()) {
            frm.set_df_property('design_view_tab', 'hidden', 1);
        }
    },

    before_save(frm) {
        // 原生保存(右上角)前同步设计器状态到 frm.doc(去掉自定义保存按钮后由原生接管)
        if (spd_designer) spd_designer.syncToForm();
    },

    async refresh(frm) {
        // 分享到模板平台(form 头部 .custom-actions 按钮,弹窗确认 + 推送)
        if (!frm.is_new() && frm.doc.design_name) {
            frm.add_custom_button(__('分享到模板平台'), () => {
                frappe.confirm(__('确认将此设计推送到模板平台?'), () => {
                    frappe.dom.freeze(__('推送中...'));
                    frappe.call({
                        method: 'zhiz_print.api.template_store.share_template',
                        args: { design_name: frm.doc.design_name },
                        callback: (r) => {
                            frappe.dom.unfreeze();
                            const m = r.message || {};
                            if (m.success) {
                                frappe.show_alert({ message: m.is_new ? __('已分享到模板平台') : __('模板已更新(v{0})').replace('{0}', m.version), indicator: 'green' });
                            } else {
                                frappe.show_alert({ message: __('分享失败: ') + (m.error || ''), indicator: 'red' });
                            }
                        },
                        error: () => {
                            frappe.dom.unfreeze();
                            frappe.show_alert({ message: __('分享失败'), indicator: 'red' });
                        },
                    });
                });
            });
            // 第三参是 category(会变下拉),改 filter 上色
            $('.custom-actions .btn').filter(function () { return $(this).text().trim() === __('分享到模板平台'); }).removeClass('btn-default').addClass('btn-primary');
        }
        if (spd_designer) spd_designer = null;
        spd_designer = new SuperPrintDesigner(frm);

        // 演示预览按钮 + toolbar 回设计(同步绑委托/事件,document 级不依赖按钮 DOM 时序,解决首次点击赶不上 setTimeout 100ms)
        const ptsPreviewBtn = document.getElementById('spd-preview-sample-btn');
        // 模板关联单据按钮:document 事件委托(不依赖按钮 DOM 重建,同演示预览)
        if (!document._pts_sampledoc_delegated) {
            document._pts_sampledoc_delegated = true;
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('#spd-sample-doc-btn');
                if (!btn) return;
                const frm2 = (typeof cur_frm !== 'undefined' && cur_frm) ? cur_frm : null;
                if (!frm2 || !frm2.doc.target_doctype) { frappe.msgprint(__('请先设置目标单据类型')); return; }
                const d = new frappe.ui.Dialog({
                    title: __('选择模板演示单据'),
                    fields: [{ fieldtype: 'Link', fieldname: 'sample_doc', label: __('Sample Document'), options: frm2.doc.target_doctype, reqd: 1, default: frm2.doc.sample_doc }],
                    primary_action_label: __('保存'),
                    primary_action(v) {
                        if (!frm2.doc.name || frm2.doc.__islocal) { frappe.msgprint(__('请先保存设计文档')); return; }
                        frappe.db.set_value('Super Print Design', frm2.doc.name, 'sample_doc', v.sample_doc).then(() => {
                            d.hide();
                            frm2.doc.sample_doc = v.sample_doc;
                            frappe.show_alert({ message: __('已保存,刷新中'), indicator: 'green' });
                            setTimeout(() => location.reload(), 600);
                        }).catch(() => frappe.show_alert({ message: __('保存失败'), indicator: 'red' }));
                    }
                });
                d.show();
            });
        }
        // 演示预览用事件委托(document 级,不依赖 onclick 绑定时序/按钮重建,解决点击没反应)
        if (!document._pts_preview_delegated) {
            document._pts_preview_delegated = true;
            document.addEventListener('click', (e) => {
                const btn = e.target.closest('#spd-preview-sample-btn');
                if (!btn) return;
                const frm2 = (typeof cur_frm !== 'undefined' && cur_frm) ? cur_frm : null;
                const overlay = document.getElementById('spd-preview-overlay');
                if (!frm2) return;
                if (!overlay) return;
                if (window.getComputedStyle(overlay).display !== 'none') {  // 已预览→回设计(用 computed:初始 display:none 是 CSS 类设的,inline style 为空,''.!==.'none' 会误判)
                    overlay.style.display = 'none';
                    btn.classList.remove('active');
                    btn.innerHTML = '<i class="fa fa-eye"></i> ' + __('演示预览');
                    const c = document.querySelector('.spd-container');
                    if (c) c.classList.remove('spd-preview-active');  // 恢复所有按钮可点
                    return;
                }
                if (!frm2.doc.sample_doc) return;
                // 即时反馈:按钮立即变 + overlay 显示 spinner
                btn.classList.add('active');
                btn.innerHTML = '<i class="fa fa-pencil"></i> ' + __('回到设计');
                overlay.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:60vh;color:#666;font-size:14px;"><i class="fa fa-spinner fa-spin" style="margin-right:8px;font-size:20px;"></i>' + __('渲染预览中...') + '</div>';
                overlay.style.display = 'block';
                const c2 = document.querySelector('.spd-container');
                if (c2) c2.classList.add('spd-preview-active');  // 禁用 toolbar/page-bar 按钮(除回到设计)
                // 演示预览走客户端实测分页(与实际打印预览一致),避免后端估算遮挡
                const _writePreviewIframe = (html) => {
                    overlay.innerHTML = '<iframe id="spd-preview-iframe" style="width:100%;min-height:calc(100vh - 220px);border:0;"></iframe>';
                    const ifr = overlay.querySelector('#spd-preview-iframe');
                    if (ifr) {
                        try {
                            const d = ifr.contentWindow.document;
                            d.open(); d.write(html || ''); d.close();
                            const st = d.createElement('style');
                            st.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;} .print-pages-wrapper{margin:0 auto !important;} .print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
                            d.head.appendChild(st);
                        } catch (e) {}
                    }
                };
                (async () => {
                    const baseArgs = { doctype: frm2.doc.target_doctype, docname: frm2.doc.sample_doc, design_name: frm2.doc.design_name, params: {} };
                    try {
                        // 第一轮:量行架(measurement_only)
                        const r1 = await frappe.call({ method: 'zhiz_print.api.print_designer.render_print_preview', args: Object.assign({}, baseArgs, { measurement_only: 1 }) });
                        if (r1.message && r1.message.measurement_html && r1.message.content_h_px) {
                            const measured = await spd_designer._sp_measure_row_heights(r1.message.measurement_html, r1.message.content_w_px);
                            if (measured && Object.keys(measured.heights).length) {
                                const bm = spd_designer._sp_compute_break_map(measured, r1.message);
                                if (bm) {
                                    // 第二轮:带 break_map 精确渲染
                                    const r2 = await frappe.call({ method: 'zhiz_print.api.print_designer.render_print_preview', args: Object.assign({}, baseArgs, { page_break_map: bm.page_break_map, row_heights: bm.row_heights, shrink_map: bm.shrink_map }) });
                                    if (r2.message && r2.message.precise) { _writePreviewIframe(r2.message.html); return; }
                                }
                            }
                        }
                        // fallback:估算
                        const rf = await frappe.call({ method: 'zhiz_print.api.print_designer.render_print_preview', args: baseArgs });
                        _writePreviewIframe(rf.message && rf.message.html);
                    } catch (e) {
                        // 兜底:旧 preview_with_sample(后端估算)
                        const rb = await frappe.call({ method: 'zhiz_print.zhiz_print.doctype.super_print_design.super_print_design.preview_with_sample', args: { design_name: baseArgs.design_name, doc_name: baseArgs.docname } });
                        _writePreviewIframe(rb.message);
                    }
                })();
            });
        }
        // 预览模式点任意 toolbar 按钮(除演示预览)→ 回设计(capture 阶段 + stop 阻止 action)
        const ptsToolbar = document.querySelector('.spd-toolbar');
        if (ptsToolbar && !ptsToolbar._pts_hide_bound) {
            ptsToolbar._pts_hide_bound = true;
            ptsToolbar.addEventListener('click', (e) => {
                const ov = document.getElementById('spd-preview-overlay');
                if (ov && window.getComputedStyle(ov).display !== 'none' && !e.target.closest('#spd-preview-sample-btn')) {
                    ov.style.display = 'none';
                    ptsPreviewBtn.classList.remove('active');
                    ptsPreviewBtn.innerHTML = '<i class="fa fa-eye"></i> ' + __('演示预览');
                    e.stopPropagation(); e.preventDefault();
                }
            }, true);
        };

        // Load design data from server for existing documents
        let serverData = null;
        if (!frm.is_new() && frm.doc.name) {
            serverData = await spd_designer.loadDesignFromServer();
        }
        spd_designer.loadExistingDesign(serverData);

        setTimeout(() => {
            if (frm.page.sidebar) frm.page.sidebar.hide();
            // Limit header/footer Code field height
            const headerFooterFields = [
                'page_header_left', 'page_header_center', 'page_header_right',
                'page_footer_left', 'page_footer_center', 'page_footer_right'
            ];
            headerFooterFields.forEach(f => {
                const wrapper = frm.get_field(f)?.$wrapper;
                if (wrapper) {
                    const editor = wrapper.find('.ace_editor, .CodeMirror, .cm-editor');
                    if (editor.length) {
                        editor.css('height', '100px');
                    }
                    // fallback: textarea
                    const textarea = wrapper.find('textarea');
                    if (textarea.length) {
                        textarea.css({ height: '100px', 'max-height': '100px' });
                    }
                }
            });
        }, 300);

        if (frm.is_new() && !frm.doc.target_doctype) {
            frm.set_df_property('design_view_tab', 'hidden', 1);
        } else {
            frm.set_df_property('design_view_tab', 'hidden', 0);
        }

        if (frm.doc.target_doctype) {
            // Skip loadPaperSize if server data already has paper info
            if (!serverData || !serverData.paper || !serverData.paper.width) {
                await spd_designer.loadPaperSize();
            }
            const html = await spd_designer.fetchDesignerHtml();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) {
                    spd_designer.bindEvents();
                }
            }, 100);
        } else {
            frm.set_df_property('design_html', 'options',
                '<div class="alert alert-info" style="margin-top:15px"><h5>' + __('Please select a target DocType first') + '</h5></div>');
            refresh_field('design_html');
        }
    },
    async target_doctype(frm) {
        if (frm.doc.target_doctype && spd_designer) {
            spd_designer = new SuperPrintDesigner(frm);
            spd_designer.init();
            await spd_designer.loadPaperSize();
            const html = await spd_designer.fetchDesignerHtml();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) { spd_designer.bindEvents(); }
            }, 100);
        }
    },
    async print_paper(frm) {
        if (spd_designer && frm.doc.print_paper) {
            await spd_designer.loadPaperSize();
            const html = await spd_designer.fetchDesignerHtml();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) { spd_designer.bindEvents(); }
            }, 100);
        }
    },
    page_header_left(frm) {
        if (spd_designer) {
            spd_designer.pageHeaderLeft = frm.doc.page_header_left || '';
            spd_designer.refreshGrid();
        }
    },
    page_header_center(frm) {
        if (spd_designer) {
            spd_designer.pageHeaderCenter = frm.doc.page_header_center || '';
            spd_designer.refreshGrid();
        }
    },
    page_header_right(frm) {
        if (spd_designer) {
            spd_designer.pageHeaderRight = frm.doc.page_header_right || '';
            spd_designer.refreshGrid();
        }
    },
    page_footer_left(frm) {
        if (spd_designer) {
            spd_designer.pageFooterLeft = frm.doc.page_footer_left || '';
            spd_designer.refreshGrid();
        }
    },
    page_footer_center(frm) {
        if (spd_designer) {
            spd_designer.pageFooterCenter = frm.doc.page_footer_center || '';
            spd_designer.refreshGrid();
        }
    },
    page_footer_right(frm) {
        if (spd_designer) {
            spd_designer.pageFooterRight = frm.doc.page_footer_right || '';
            spd_designer.refreshGrid();
        }
    }
});
