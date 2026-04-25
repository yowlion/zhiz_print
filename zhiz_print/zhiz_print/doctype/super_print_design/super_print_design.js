// Super Print Designer - Frontend Designer
// Ported from QmSheetDesigner and extended

class SuperPrintDesigner {
    constructor(frm) {
        this.frm = frm;
        this.designContainerId = `spd-${Date.now()}`;
        this.currentCell = null;
        this.grid = [];
        this.cellDataMap = {};
        this.selectedCells = [];

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

    loadExistingDesign(serverData) {
        this.initGrid();
        this.cellDataMap = {};
        const defaultCellStyle = 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;';

        if (serverData) {
            // Use server-parsed data
            this.rows = serverData.rows || this.rows;
            this.cols = serverData.columns || this.cols;
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

            // Load paper info from server data
            if (serverData.paper) {
                this.paperWidth = parseFloat(serverData.paper.width) || this.paperWidth;
                this.paperHeight = parseFloat(serverData.paper.height) || this.paperHeight;
                this.marginTop = parseInt(serverData.paper.margin_top) || this.marginTop;
                this.marginBottom = parseInt(serverData.paper.margin_bottom) || this.marginBottom;
                this.marginLeft = parseInt(serverData.paper.margin_left) || this.marginLeft;
                this.marginRight = parseInt(serverData.paper.margin_right) || this.marginRight;
            }

            // Re-init grid with correct dimensions
            this.grid = Array(this.rows).fill().map(() => Array(this.cols).fill(null));

            // Parse cells from server
            if (serverData.cells && serverData.cells.length > 0) {
                serverData.cells.forEach(cell => {
                    const rowIndex = cell.row - 1;
                    const colIndex = cell.col - 1;
                    if (rowIndex < 0 || rowIndex >= this.rows || colIndex < 0 || colIndex >= this.cols) return;

                    if (cell.is_merged) {
                        this.grid[rowIndex][colIndex] = {
                            _merged: true,
                            master_cell_id: cell.master_cell_id || ''
                        };
                    } else {
                        const cellData = {
                            cell_id: cell.cell_id || `R${cell.row}C${cell.col}`,
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
                        this.grid[rowIndex][colIndex] = cellData;
                        this.cellDataMap[cellData.cell_id] = cellData;

                        if (cellData.rowspan > 1 || cellData.colspan > 1) {
                            this.markMergedCells(cellData);
                        }
                    }
                });
            }
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
            this.pageHeaderLeft = this.frm.doc.page_header_left || '';
            this.pageHeaderCenter = this.frm.doc.page_header_center || '';
            this.pageHeaderRight = this.frm.doc.page_header_right || '';
            this.pageFooterLeft = this.frm.doc.page_footer_left || '';
            this.pageFooterCenter = this.frm.doc.page_footer_center || '';
            this.pageFooterRight = this.frm.doc.page_footer_right || '';

            if (this.frm.doc.design_items && this.frm.doc.design_items.length > 0) {
                const sortedItems = [...this.frm.doc.design_items].sort((a, b) => {
                    if (a.row !== b.row) return a.row - b.row;
                    return a.col - b.col;
                });

                sortedItems.forEach(item => {
                    const rowIndex = item.row - 1;
                    const colIndex = item.col - 1;

                    if (rowIndex >= 0 && rowIndex < this.rows && colIndex >= 0 && colIndex < this.cols) {
                        if (this.isMergedMark(item.cell_value)) {
                            this.grid[rowIndex][colIndex] = {
                                _merged: true,
                                master_cell_id: this.extractMasterId(item.cell_value)
                            };
                        } else {
                            const cellData = {
                                cell_id: item.cell_id || `R${item.row}C${item.col}`,
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
                            this.grid[rowIndex][colIndex] = cellData;
                            this.cellDataMap[cellData.cell_id] = cellData;

                            if (cellData.rowspan > 1 || cellData.colspan > 1) {
                                this.markMergedCells(cellData);
                            }
                        }
                    }
                });
            }
        }

        // Fill empty cells
        for (let row = 0; row < this.rows; row++) {
            for (let col = 0; col < this.cols; col++) {
                if (!this.grid[row][col]) {
                    const cellId = `R${row + 1}C${col + 1}`;
                    const cellData = {
                        cell_id: cellId, row: row + 1, col: col + 1,
                        rowspan: 1, colspan: 1, cell_type: 'static',
                        cell_value: '', css_style: defaultCellStyle
                    };
                    this.grid[row][col] = cellData;
                    this.cellDataMap[cellId] = cellData;
                }
            }
        }
    }

    markMergedCells(cell) {
        const startRow = cell.row - 1;
        const startCol = cell.col - 1;
        const rowspan = cell.rowspan || 1;
        const colspan = cell.colspan || 1;
        for (let r = startRow; r < startRow + rowspan; r++) {
            for (let c = startCol; c < startCol + colspan; c++) {
                if (r === startRow && c === startCol) continue;
                if (r >= this.grid.length || c >= this.grid[0].length) continue;
                const existing = this.grid[r][c];
                if (existing && !existing._merged) {
                    delete this.cellDataMap[existing.cell_id];
                }
                this.grid[r][c] = { _merged: true, master_cell_id: cell.cell_id };
            }
        }
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
                'display:flex;align-items:center;' +
                'font-size:12px;color:#666;">';
            html += '<div style="flex:1;text-align:left;padding-left:' + mLeft + 'px;">' + (hl || (hasContent ? '' : placeholder)) + '</div>';
            html += '<div style="flex:1;text-align:center;">' + (hc || '') + '</div>';
            html += '<div style="flex:1;text-align:right;padding-right:' + mRight + 'px;">' + (hr || '') + '</div>';
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
                'display:flex;align-items:center;' +
                'font-size:12px;color:#666;">';
            html += '<div style="flex:1;text-align:left;padding-left:' + mLeft + 'px;">' + (fl || (hasContent ? '' : placeholder)) + '</div>';
            html += '<div style="flex:1;text-align:center;">' + (fc || '') + '</div>';
            html += '<div style="flex:1;text-align:right;padding-right:' + mRight + 'px;">' + (fr_ || '') + '</div>';
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
            let rowStyleAttr = 'height:' + (rowStyle.height || 20) + 'px;';
            if (rowStyle.css_style) rowStyleAttr += rowStyle.css_style;
            // Row display effect
            const firstCellInRow = this.grid[row - 1]?.[0];
            const rowDisplay = firstCellInRow?.row_display || '';
            if (rowDisplay === 'Fixed Height') rowStyleAttr += 'overflow:hidden;white-space:nowrap;';
            else if (rowDisplay === 'Auto Shrink Font') rowStyleAttr += 'overflow:hidden;';
            rowStyleAttr += 'line-height:1;';
            const rowSelectedClass = this.selectedRow === row ? ' row-selected' : '';

            html += '<tr class="' + rowSelectedClass + '" style="' + rowStyleAttr + '">';
            for (let col = 1; col <= this.cols; col++) {
                if (occupied[row][col]) continue;
                const cell = this.grid[row - 1]?.[col - 1];
                const cellId = 'R' + row + 'C' + col;
                const colSelectedClass = this.selectedCol === col ? ' col-selected' : '';

                if (cell && !cell._merged) {
                    const { rowspan = 1, colspan = 1, cell_type, cell_value, css_style } = cell;
                    const rs = rowspan > 1 ? ' rowspan="' + rowspan + '"' : '';
                    const cs = colspan > 1 ? ' colspan="' + colspan + '"' : '';

                    let cellStyle = 'line-height:1;';
                    if (rowStyle.font_size) cellStyle += 'font-size:' + rowStyle.font_size + 'px;';
                    if (css_style) cellStyle += css_style;

                    const typeInfo = this.cellTypes.find(t => t.value === cell_type) || this.cellTypes[0];
                    const hasValue = cell_value && cell_value.trim();

                    let content = '';
                    if (cell_type === 'barcode' || cell_type === 'qrcode') {
                        content = '<div class="super-zprint-cell-preview"><i class="fa ' + typeInfo.icon + '" style="font-size:16px;color:#666"></i><span>' + typeInfo.label + '</span></div>';
                    } else if (!hasValue) {
                        content = '';
                    } else {
                        content = '<div class="super-zprint-cell-content">' + this.escapeHtml(cell_value) + '</div>';
                    }

                    const selectedClass = this.currentCell === cellId ? ' selected' : '';
                    html += '<td class="spd-cell ' + cell_type + selectedClass + colSelectedClass + '" data-cell-id="' + cellId + '" data-row="' + row + '" data-col="' + col + '" style="' + cellStyle + '"' + rs + cs + '>' + content + '</td>';

                    for (let r = row; r < row + rowspan; r++) {
                        for (let c = col; c < col + colspan; c++) {
                            if (r <= this.rows && c <= this.cols) occupied[r][c] = true;
                        }
                    }
                } else {
                    let cellStyle = 'line-height:1;';
                    if (rowStyle.font_size) cellStyle += 'font-size:' + rowStyle.font_size + 'px;';
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
        container.querySelector('#spd-font')?.addEventListener('change', (e) => {
            this.fontFamily = e.target.value;
            this.frm.set_value('font_family', this.fontFamily);
            this.refreshGrid();
            this.frm.dirty();
        });
        container.querySelector('#spd-clear-btn')?.addEventListener('click', () => this.clearDesign());
        container.querySelector('#spd-save-btn')?.addEventListener('click', () => this.saveDesign());
        container.querySelector('#spd-query-btn')?.addEventListener('click', () => this.showQueryDialog());
        container.querySelector('#spd-params-btn')?.addEventListener('click', () => this.showParamsDialog());
        container.querySelector('#spd-repeat-title-btn')?.addEventListener('click', () => this.setRowType('Repeat Title Row'));
        container.querySelector('#spd-data-driven-btn')?.addEventListener('click', () => this.setRowType('Data-Driven Row'));
        container.querySelector('#spd-normal-row-btn')?.addEventListener('click', () => this.setRowType(''));

        // Delegated click events
        container.addEventListener('click', (e) => {
            const colHeader = e.target.closest('.super-zprint-col-header-cell');
            if (colHeader) {
                this.handleColClick(parseInt(colHeader.dataset.col));
                return;
            }
            const rowHeader = e.target.closest('.super-zprint-row-header-cell');
            if (rowHeader) {
                this.handleRowClick(parseInt(rowHeader.dataset.row));
                return;
            }
            const cell = e.target.closest('.spd-cell');
            if (cell) this.handleCellClick(cell.dataset.cellId);
        });

        setTimeout(() => {
            this.alignRowHeaders();
            this.syncRowHeaderHeights();
        }, 150);

        this.bindPropertyEvents();
    }

    handleCellClick(cellId) {
        this.selectionMode = 'cell';
        this.selectedRow = null;
        this.selectedCol = null;
        this.currentCell = cellId;
        const container = document.getElementById(this.designContainerId);
        container?.querySelector('.spd-row-type-controls')?.style && (container.querySelector('.spd-row-type-controls').style.display = 'none');
        const [row, col] = this.parseCellId(cellId);
        const cell = this.grid[row - 1]?.[col - 1];

        if (cell?._merged) return;
        if (!cell) this.createCellData(row, col);
        this.renderCellProperties(cellId);
        this.refreshGrid();
    }

    handleRowClick(row) {
        this.selectionMode = 'row';
        this.selectedRow = row;
        this.selectedCol = null;
        this.currentCell = null;
        this.selectedCells = [];
        const container = document.getElementById(this.designContainerId);
        const typeControls = container?.querySelector('.spd-row-type-controls');
        if (typeControls) typeControls.style.display = '';
        this.renderRowProperties(row);
        this.refreshGrid();
    }

    handleColClick(col) {
        this.selectionMode = 'col';
        this.selectedCol = col;
        this.selectedRow = null;
        this.currentCell = null;
        const container = document.getElementById(this.designContainerId);
        container?.querySelector('.spd-row-type-controls')?.style && (container.querySelector('.spd-row-type-controls').style.display = 'none');
        this.selectedCells = [];
        this.renderColProperties(col);
        this.refreshGrid();
    }

    // ==================== Row Property Panel ====================

    renderRowProperties(row) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const rowStyle = this.rowStyles[row] || {};
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
                '<div class="property-section-body" style="padding:8px">' +
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
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px;margin-bottom:4px">' + __('Vertical Align') + ':</label>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="top" title="' + __('Top Align') + '"><i class="fa fa-arrow-up"></i> ' + __('Top') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="middle" title="' + __('Center') + '"><i class="fa fa-arrows-v"></i> ' + __('Center') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="bottom" title="' + __('Bottom Align') + '"><i class="fa fa-arrow-down"></i> ' + __('Bottom') + '</button>' +
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
        if (cssInput) cssInput.addEventListener('change', (e) => {
            this.updateRowStyle(row, 'css_style', e.target.value);
        });

        // Row type selection
        const rowTypeSelect = container.querySelector('#row-type-select');
        if (rowTypeSelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowTypeSelect.value = firstNonMerged?.row_type || '';
            rowTypeSelect.addEventListener('change', (e) => this.setRowTypeForRow(row, e.target.value));
        }
        // Row display effect selection
        const rowDisplaySelect = container.querySelector('#row-display-select');
        if (rowDisplaySelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowDisplaySelect.value = firstNonMerged?.row_display || '';
            rowDisplaySelect.addEventListener('change', (e) => this.setRowDisplay(row, e.target.value));
        }
        // Vertical alignment buttons
        container.querySelectorAll('.row-align-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.updateRowStyle(row, 'css_style',
                    this.setCssProperty(this.rowStyles[row]?.css_style || '', 'vertical-align', btn.dataset.align));
            });
        });
    }

    // ==================== Row Type / Row Display Effect ====================

    getFirstNonMergedCell(row) {
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
        const cell = this.grid[row - 1]?.[col - 1];
        if (!cell || cell._merged) return;

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
                    '<label>' + __('Data Key') + ':</label>' +
                    '<input id="prop-data-key" class="form-control" placeholder="' + __('e.g. item_code') + '">' +
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
                    '<div class="super-zprint-layout-control-group" id="unmerge-btn-group" style="display:' + ((cell.rowspan > 1 || cell.colspan > 1) ? 'flex' : 'none') + ';gap:4px;align-items:center;">' +
                        '<button type="button" class="btn btn-xs btn-default" id="btn-unmerge-left" title="' + __('Content stays in first cell, others become empty') + '">' + __('Unmerge Left') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default" id="btn-unmerge-inherit" title="' + __('Content is copied to every freed cell') + '">' + __('Unmerge Inherit') + '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-layout-controls">' +
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
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-weight:bold" data-prop="font-weight" title="' + __('Bold') + '"><i class="fa fa-bold"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-style:italic" data-prop="font-style" title="' + __('Italic') + '"><i class="fa fa-italic"></i></button>' +
                    '</div>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:3px">' +
                        '<div class="super-zprint-color-picker-wrapper" title="' + __('Background Color') + '"><i class="fa fa-fill-drip"></i><input type="color" id="bg-color-picker" value="#ffffff"></div>' +
                        '<div class="super-zprint-color-picker-wrapper" title="' + __('Text Color') + '"><i class="fa fa-font"></i><input type="color" id="text-color-picker" value="#000000"></div>' +
                        '<button type="button" class="btn btn-xs css-quick-btn" data-action="default-css" title="' + __('Default Style') + '" style="width:auto;padding:0 6px;font-size:9px;background:#28a745;color:#fff;border-color:#28a745"><i class="fa fa-undo" style="color:#fff"></i> <span style="color:#fff">' + __('Default') + '</span></button>' +
                        '<button type="button" class="btn btn-xs btn-danger css-quick-btn" data-action="clear-css" title="' + __('Clear CSS') + '" style="width:auto;padding:0 6px;font-size:9px"><i class="fa fa-eraser"></i> ' + __('Clear') + '</button>' +
                    '</div>' +
                '</div>' +
                '<div class="super-zprint-border-settings" style="margin:6px 0;padding:6px;background:#f8f9fa;border-radius:4px;border:1px solid #e9ecef">' +
                    '<label style="font-size:9px;margin-bottom:4px">' + __('Border') + ':</label>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:2px;gap:2px">' +
                        '<div class="border-width-selector" style="display:flex;align-items:center;gap:2px">' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn super-zprint-bw-active" data-width="1" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Thin') + '</button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="2" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Medium') + '</button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="3" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Thick') + '</button>' +
                            '<button type="button" class="btn btn-xs btn-default super-zprint-border-width-btn" data-width="0" style="width:24px;height:20px;padding:0;font-size:8px">' + __('None') + '</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="super-zprint-btn-group-wrap" style="margin-top:4px;gap:2px">' +
                        '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="top" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Top') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="bottom" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Bottom') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="left" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Left') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="right" style="width:24px;height:20px;padding:0;font-size:8px">' + __('Right') + '</button>' +
                        '<button type="button" class="btn btn-xs btn-default super-zprint-border-side-btn" data-side="none" style="width:24px;height:20px;padding:0;font-size:8px">' + __('All') + '</button>' +
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
        setValue('prop-data-key', cell.data_key);
        setValue('prop-barcode-format', cell.barcode_format || 'CODE128');
        setValue('prop-barcode-width', cell.barcode_width || 100);
        setValue('prop-barcode-height', cell.barcode_height || 40);
        this.togglePropertyGroups(cell.cell_type);
    }

    togglePropertyGroups(cellType) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const queryGroup = container.querySelector('#query-group');
        const barcodeGroup = container.querySelector('#barcode-group');
        const qrcodeGroup = container.querySelector('#qrcode-group');
        if (queryGroup) queryGroup.style.display = (cellType === 'data_query') ? 'block' : 'none';
        if (barcodeGroup) barcodeGroup.style.display = (cellType === 'barcode') ? 'block' : 'none';
        if (qrcodeGroup) qrcodeGroup.style.display = (cellType === 'qrcode') ? 'block' : 'none';
        if (cellType === 'data_query') {
            this.updateCellProperty('query_name', 'main');
        }
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
            for (let c = startCol + newColspan; c < startCol + oldColspan; c++) {
                if (c < this.cols) {
                    const newCellId = 'R' + row + 'C' + (c + 1);
                    const newCell = {
                        cell_id: newCellId, row: row, col: c + 1,
                        rowspan: 1, colspan: 1, cell_type: 'static',
                        cell_value: '',
                        css_style: 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;'
                    };
                    this.grid[startRow][c] = newCell;
                    this.cellDataMap[newCellId] = newCell;
                }
            }
        }
        cell.colspan = newColspan;
        this.cellDataMap[this.currentCell] = cell;
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
            for (let r = startRow + newRowspan; r < startRow + oldRowspan; r++) {
                for (let c = startCol; c < startCol + (cell.colspan || 1); c++) {
                    if (r < this.rows && c < this.cols) {
                        const newCellId = 'R' + (r + 1) + 'C' + (c + 1);
                        const newCell = {
                            cell_id: newCellId, row: r + 1, col: c + 1,
                            rowspan: 1, colspan: 1, cell_type: 'static',
                            cell_value: '',
                            css_style: 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;'
                        };
                        this.grid[r][c] = newCell;
                        this.cellDataMap[newCellId] = newCell;
                    }
                }
            }
        }
        cell.rowspan = newRowspan;
        this.cellDataMap[this.currentCell] = cell;
    }

    _createFreedCell(row, col, sourceCell, copyContent) {
        const newCellId = 'R' + row + 'C' + col;
        const newCell = {
            cell_id: newCellId, row: row, col: col,
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
        const cell = this.cellDataMap[this.currentCell];
        if (!cell || (cell.rowspan <= 1 && cell.colspan <= 1)) return;

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
        this.cellDataMap[this.currentCell] = cell;
        this.refreshGrid();
        this.renderCellProperties(this.currentCell);
        frappe.show_alert({ message: inherit ? __('Unmerged with content inherited') : __('Unmerged, content kept in first cell'), indicator: 'green' });
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
        const btnLeft = container.querySelector('#btn-unmerge-left');
        if (btnLeft) {
            btnLeft.addEventListener('click', () => this._doUnmerge(false));
        }
        const btnInherit = container.querySelector('#btn-unmerge-inherit');
        if (btnInherit) {
            btnInherit.addEventListener('click', () => this._doUnmerge(true));
        }
    }

    bindBorderButtons() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        if (this.currentBorderWidth === undefined) this.currentBorderWidth = 1;

        const widthBtns = container.querySelectorAll('.super-zprint-border-width-btn');
        widthBtns.forEach(btn => {
            const btnWidth = parseInt(btn.dataset.width);
            if (btnWidth === this.currentBorderWidth) btn.classList.add('super-zprint-bw-active');
            else btn.classList.remove('super-zprint-bw-active');
        });

        widthBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                widthBtns.forEach(b => b.classList.remove('super-zprint-bw-active'));
                btn.classList.add('super-zprint-bw-active');
                this.currentBorderWidth = parseInt(btn.dataset.width);
            });
        });

        container.querySelectorAll('.super-zprint-border-side-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.applySingleBorder(btn.dataset.side, this.currentBorderWidth);
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

        const sideNames = { top: __('Top'), bottom: __('Bottom'), left: __('Left'), right: __('Right'), none: __('All') };
        const allSides = ['top', 'right', 'bottom', 'left'];
        const borderVal = width > 0 ? width + 'px solid black' : '1px solid transparent';

        if (side === 'none') {
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
                    const defaultCss = 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;';
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
        if (this.frm.doc.query_code) {
            return '<option value="main">' + __('Main Query') + '</option>';
        }
        return '';
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

        // Collect only non-merged cells — backend validate_cells() handles merge markers
        const cells = [];
        for (let row = 1; row <= this.rows; row++) {
            for (let col = 1; col <= this.cols; col++) {
                const cell = this.grid[row - 1]?.[col - 1];
                if (!cell) {
                    // Empty cell
                    cells.push(this.createBlankItem(row, col));
                } else if (cell._merged) {
                    // Skip merged cells — backend will generate markers automatically
                    continue;
                } else {
                    cells.push({
                        cell_id: cell.cell_id, row, col,
                        rowspan: cell.rowspan || 1, colspan: cell.colspan || 1,
                        cell_type: cell.cell_type || 'static', cell_value: cell.cell_value || '',
                        css_style: cell.css_style || '', query_name: cell.query_name || '',
                        data_key: cell.data_key || '', barcode_format: cell.barcode_format || 'CODE128',
                        barcode_width: cell.barcode_width || 100, barcode_height: cell.barcode_height || 40,
                        row_type: cell.row_type || '', row_display: cell.row_display || '',
                    });
                }
            }
        }

        // Send to backend API
        frappe.call({
            method: 'zhiz_print.api.print_designer.save_design',
            args: {
                design_name: this.frm.doc.name,
                rows: this.rows,
                columns: this.cols,
                row_styles: JSON.stringify(this.rowStyles),
                col_styles: JSON.stringify(this.colStyles),
                font_family: this.fontFamily,
                font_size: this.fontSize,
                page_header_left: this.pageHeaderLeft,
                page_header_center: this.pageHeaderCenter,
                page_header_right: this.pageHeaderRight,
                page_footer_left: this.pageFooterLeft,
                page_footer_center: this.pageFooterCenter,
                page_footer_right: this.pageFooterRight,
                cells: cells,
            },
            freeze: true,
            callback: (r) => {
                if (r.message && r.message.success) {
                    frappe.show_alert({
                        message: __('Design saved') + ', ' + r.message.item_count + ' ' + __('cells'),
                        indicator: 'green'
                    });
                    this.frm.reload_doc();
                }
            }
        });
    }

    createBlankItem(row, col) {
        return {
            cell_id: 'R' + row + 'C' + col, row, col, rowspan: 1, colspan: 1,
            cell_type: 'static', cell_value: '', css_style: 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;'
        };
    }

    createMergedItem(row, col, masterId) {
        return {
            cell_id: 'R' + row + 'C' + col, row, col, rowspan: 1, colspan: 1,
            cell_type: 'static', cell_value: '||MERGED::' + (masterId || 'R' + row + 'C' + col) + '||', css_style: ''
        };
    }

    clearDesign() {
        if (!confirm(__('Are you sure you want to clear all designs? This action cannot be undone.'))) return;
        this.initGrid();
        this.cellDataMap = {};
        this.rowStyles = {};
        this.colStyles = {};
        const defaultCss = 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;';
        for (let r = 1; r <= this.rows; r++) {
            for (let c = 1; c <= this.cols; c++) {
                const id = 'R' + r + 'C' + c;
                const data = { cell_id: id, row: r, col: c, rowspan: 1, colspan: 1, cell_type: 'static', cell_value: '', css_style: defaultCss };
                this.grid[r - 1][c - 1] = data;
                this.cellDataMap[id] = data;
            }
        }
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Design cleared'), indicator: 'yellow' });
    }

    applyGridSize() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const newRows = parseInt(container.querySelector('#spd-rows')?.value) || 20;
        const newCols = parseInt(container.querySelector('#spd-cols')?.value) || 15;
        this.rows = newRows;
        this.cols = newCols;
        this.frm.set_value('rows', newRows);
        this.frm.set_value('columns', newCols);
        this.loadExistingDesign();
        this.refreshGrid();
        this.frm.dirty();
        frappe.show_alert({ message: __('Grid updated to') + ' ' + newRows + 'x' + newCols, indicator: 'green' });
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
    showQueryDialog() {
        const dialog = new frappe.ui.Dialog({
            title: __('Query Definition'),
            fields: [
                { fieldname: 'query_code', label: __('Python Query Code'), fieldtype: 'Code', options: 'Python',
                  default: this.frm.doc.query_code || '',
                  description: __('Assign query result to variable result') },
                { fieldname: 'query_parameters', label: __('Query Parameters'), fieldtype: 'Small Text',
                  default: this.frm.doc.query_parameters || '',
                  description: __('One parameter per line, format: key=value') }
            ],
            primary_action_label: __('Save'),
            primary_action: (values) => {
                this.frm.set_value('query_code', values.query_code);
                this.frm.set_value('query_parameters', values.query_parameters);
                this.frm.dirty();
                frappe.show_alert({ message: __('Query definition updated (save document to take effect)'), indicator: 'green' });
                dialog.hide();
            }
        });
        dialog.show();
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

    async refresh(frm) {
        if (spd_designer) spd_designer = null;
        spd_designer = new SuperPrintDesigner(frm);

        // Load design data from server for existing documents
        let serverData = null;
        if (!frm.is_new() && frm.doc.name) {
            serverData = await spd_designer.loadDesignFromServer();
        }
        spd_designer.loadExistingDesign(serverData);

        setTimeout(() => {
            if (frm.page.sidebar) frm.page.sidebar.hide();
            if (!frm.is_new()) {
                $('.page-head .primary-action').hide();
            }
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
