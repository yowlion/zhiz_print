// Super Print Designer - 前端设计器
// 移植自 QmSheetDesigner 并扩展

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
            { value: 'Microsoft YaHei', label: '微软雅黑' },
            { value: 'SimSun', label: '宋体' },
            { value: 'SimHei', label: '黑体' },
            { value: 'KaiTi', label: '楷体' },
            { value: 'FangSong', label: '仿宋' }
        ];

        this.cellTypes = [
            { value: 'static', label: '静态文本', icon: 'fa-font' },
            { value: 'data_query', label: '数据查询', icon: 'fa-search' },
            { value: 'barcode', label: '条形码', icon: 'fa-barcode' },
            { value: 'qrcode', label: '二维码', icon: 'fa-qrcode' },
            { value: 'image', label: '图片', icon: 'fa-image' }
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
                console.error('加载纸张尺寸失败:', e);
            }
        }
        if (!this.paperWidth) this.paperWidth = 210;
        if (!this.paperHeight) this.paperHeight = 297;
        if (this.marginTop === undefined) this.marginTop = 10;
        if (this.marginBottom === undefined) this.marginBottom = 10;
        if (this.marginLeft === undefined) this.marginLeft = 15;
        if (this.marginRight === undefined) this.marginRight = 15;
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

    loadExistingDesign() {
        this.initGrid();
        this.cellDataMap = {};
        const defaultCellStyle = 'text-align: center; vertical-align: middle; border: 1px solid black; font-size: ' + this.fontSize + 'px;';

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

        // 填充空单元格
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

    generateDesigner() {
        // 1mm = 4px 严格比例
        const PX_PER_MM = 4;
        let totalWidth = 0;
        for (let col = 1; col <= this.cols; col++) {
            totalWidth += (this.colStyles[col]?.width || 60);
        }

        const paperW = this.paperWidth * PX_PER_MM;
        const paperH = this.paperHeight * PX_PER_MM;
        const mTop = (this.marginTop || 0) * PX_PER_MM;
        const mBottom = (this.marginBottom || 0) * PX_PER_MM;
        const mLeft = (this.marginLeft || 0) * PX_PER_MM;
        const mRight = (this.marginRight || 0) * PX_PER_MM;
        console.log('[SPD] generateDesigner paper:', this.paperWidth, 'x', this.paperHeight, 'mm =>', paperW, 'x', paperH, 'px, margins:', mTop, mRight, mBottom, mLeft, 'px');

        let html = '<div id="' + this.designContainerId + '" class="spd-container">';
        // 工具栏
        html += '<div class="spd-toolbar">' +
            '<div class="spd-controls">' +
                '<label>行:</label><input type="number" id="spd-rows" class="form-control" min="1" max="100" value="' + this.rows + '" style="width:70px">' +
                '<label>列:</label><input type="number" id="spd-cols" class="form-control" min="1" max="26" value="' + this.cols + '" style="width:70px">' +
                '<button class="btn btn-default btn-sm" id="spd-apply-grid"><i class="fa fa-refresh"></i> 应用</button>' +
            '</div>' +
            '<div class="spd-controls">' +
                '<label>字体:</label>' +
                '<select id="spd-font" class="form-control" style="width:100px">';
        this.fontFamilies.forEach(f => {
            html += '<option value="' + f.value + '" ' + (this.fontFamily === f.value ? 'selected' : '') + '>' + f.label + '</option>';
        });
        html += '</select></div>' +
            '<div class="spd-controls spd-row-type-controls" style="display:none">' +
                '<button class="btn btn-default btn-sm" id="spd-repeat-title-btn" title="设置重复标题行"><i class="fa fa-repeat" style="color:#ff9800"></i> <small style="font-size:9px;color:#ff9800">标题行</small></button>' +
                '<button class="btn btn-default btn-sm" id="spd-data-driven-btn" title="设置数据驱动行"><i class="fa fa-database" style="color:#2196f3"></i> <small style="font-size:9px;color:#2196f3">数据行</small></button>' +
                '<button class="btn btn-default btn-sm" id="spd-normal-row-btn" title="恢复为普通行"><i class="fa fa-minus"></i> <small style="font-size:9px">普通行</small></button>' +
            '</div>' +
            '<div class="spd-actions">' +
                '<button class="btn btn-info btn-sm" id="spd-query-btn"><i class="fa fa-database"></i> 查询定义</button>' +
                '<button class="btn btn-info btn-sm" id="spd-params-btn"><i class="fa fa-sliders"></i> 参数定义</button>' +
                '<button class="btn btn-warning btn-sm" id="spd-clear-btn"><i class="fa fa-trash"></i> 清空</button>' +
                '<button class="btn btn-primary btn-sm" id="spd-save-btn"><i class="fa fa-save"></i> 保存</button>' +
            '</div>' +
        '</div>';

        // 主区域：网格 + 属性面板
        // 结构: grid-wrapper > col-headers(纸张外上方) + grid-body > row-headers(纸张外左侧) + spd-paper > margin-line + table
        const rowHeaderWidth = 22;
        const contentAreaW = paperW - mLeft - mRight;
        const centeredOffset = Math.max(0, (contentAreaW - totalWidth) / 2);
        const colHeaderOffset = rowHeaderWidth + mLeft + centeredOffset;
        html += '<div class="spd-main">' +
            '<div class="spd-grid-wrap">' +
                '<div class="spd-paper-preview">' +
                    '<div class="grid-wrapper">' +
                        '<div class="col-headers" id="spd-col-headers" style="margin-left:' + colHeaderOffset + 'px;">' +
                            this.generateColHeadersHtml() +
                        '</div>' +
                        '<div class="grid-body">' +
                            '<div class="row-headers" id="spd-row-headers" style="margin-top:' + mTop + 'px;">' +
                                this.generateRowHeadersHtml() +
                            '</div>' +
                            '<div class="spd-paper" id="spd-paper" style="width:' + paperW + 'px; min-height:' + paperH + 'px; position:relative;">' +
                                '<div class="spd-margin-line" style="top:' + mTop + 'px; left:' + mLeft + 'px; right:' + mRight + 'px; bottom:' + mBottom + 'px;"></div>' +
                                this.generateHeaderFooterHtml(mTop, mBottom, mLeft, mRight) +
                                '<div class="spd-table-area" style="position:absolute; top:' + mTop + 'px; left:' + mLeft + 'px; right:' + mRight + 'px; display:flex; justify-content:center;">' +
                                    '<table class="spd-grid" id="spd-grid" style="width:' + totalWidth + 'px; font-family:\'' + this.fontFamily + '\', sans-serif">' +
                                    this.generateGridHtml() +
                                '</table>' +
                                '</div>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="spd-props" id="spd-props">' +
                '<h4><i class="fa fa-cog"></i> 单元格属性</h4>' +
                '<div id="spd-prop-form"></div>' +
            '</div>' +
        '</div></div>';
        return html;
    }

    generateHeaderFooterHtml(mTop, mBottom, mLeft, mRight) {
        let html = '';
        // 页眉区域（左/中/右三栏）
        if (mTop > 0) {
            const hl = this.pageHeaderLeft || '';
            const hc = this.pageHeaderCenter || '';
            const hr = this.pageHeaderRight || '';
            const hasContent = hl || hc || hr;
            const placeholder = '<span style="color:#ccc;font-size:10px;">页眉区域</span>';
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
        // 页脚区域（左/中/右三栏）
        if (mBottom > 0) {
            const fl = this.pageFooterLeft || '';
            const fc = this.pageFooterCenter || '';
            const fr_ = this.pageFooterRight || '';
            const hasContent = fl || fc || fr_;
            const placeholder = '<span style="color:#ccc;font-size:10px;">页脚区域</span>';
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
            const selectedClass = this.selectedCol === col ? 'col-header-selected' : '';
            html += '<div class="col-header-cell ' + selectedClass + '" data-col="' + col + '" style="width:' + colWidth + 'px; min-width:' + colWidth + 'px; max-width:' + colWidth + 'px;">' + col + '</div>';
        }
        return html;
    }

    generateRowHeadersHtml() {
        let html = '';
        for (let row = 1; row <= this.rows; row++) {
            const rowStyle = this.rowStyles[row] || {};
            const rowHeight = rowStyle.height || 20;
            const selectedClass = this.selectedRow === row ? 'row-header-selected' : '';

            // 行类型指示（扫描该行第一个非合并单元格）
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
            if (rowType === '\u91cd\u590d\u6807\u9898\u884c') {
                typeIndicator = '<span class="row-type-badge badge-repeat" title="\u91cd\u590d\u6807\u9898\u884c">T</span>';
                typeClass = ' row-header-repeat-title';
            } else if (rowType === '\u6570\u636e\u9a71\u52a8\u884c') {
                typeIndicator = '<span class="row-type-badge badge-data" title="\u6570\u636e\u9a71\u52a8\u884c">D</span>';
                typeClass = ' row-header-data-driven';
            }

            html += '<div class="row-header-cell ' + selectedClass + typeClass + '" data-row="' + row + '" style="height:' + rowHeight + 'px; min-height:' + rowHeight + 'px; max-height:' + rowHeight + 'px; line-height:' + rowHeight + 'px;">' + typeIndicator + row + '</div>';
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
            // 行显示效果
            const firstCellInRow = this.grid[row - 1]?.[0];
            const rowDisplay = firstCellInRow?.row_display || '';
            if (rowDisplay === '\u56fa\u5b9a\u884c\u9ad8') rowStyleAttr += 'overflow:hidden;white-space:nowrap;';
            else if (rowDisplay === '\u81ea\u52a8\u7f29\u5c0f\u5b57\u4f53') rowStyleAttr += 'overflow:hidden;';
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
                        content = '<div class="cell-preview"><i class="fa ' + typeInfo.icon + '" style="font-size:16px;color:#666"></i><span>' + typeInfo.label + '</span></div>';
                    } else if (!hasValue) {
                        content = '';
                    } else {
                        content = '<div class="cell-content">' + this.escapeHtml(cell_value) + '</div>';
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
        container.querySelector('#spd-repeat-title-btn')?.addEventListener('click', () => this.setRowType('\u91cd\u590d\u6807\u9898\u884c'));
        container.querySelector('#spd-data-driven-btn')?.addEventListener('click', () => this.setRowType('\u6570\u636e\u9a71\u52a8\u884c'));
        container.querySelector('#spd-normal-row-btn')?.addEventListener('click', () => this.setRowType(''));

        // 委托点击事件
        container.addEventListener('click', (e) => {
            const colHeader = e.target.closest('.col-header-cell');
            if (colHeader) {
                this.handleColClick(parseInt(colHeader.dataset.col));
                return;
            }
            const rowHeader = e.target.closest('.row-header-cell');
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

    // ==================== 行属性面板 ====================

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
            titleElement.innerHTML = '<i class="fa fa-arrows-v"></i> 行样式设置 <small style="color:#6c757d;font-weight:normal">(第' + row + '行)</small>';
        }

        let formHtml = '<form id="row-property-form" class="property-form">' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-expand"></i> 行尺寸</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<div class="layout-controls">' +
                        '<div class="layout-control-group">' +
                            '<label style="font-size:9px">行高:</label>' +
                            '<div class="number-spinner number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="row-height" data-step="5">-</button>' +
                                '<input type="number" id="row-height" class="form-control spin-input" value="' + (rowStyle.height || '') + '" min="1" max="500" step="1" placeholder="20">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="row-height" data-step="5">+</button>' +
                            '</div>' +
                        '</div>' +
                        '<div class="layout-control-group">' +
                            '<label style="font-size:9px">字体:</label>' +
                            '<div class="number-spinner number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="row-font-size" data-step="1">-</button>' +
                                '<input type="number" id="row-font-size" class="form-control spin-input" value="' + (rowStyle.font_size || '') + '" min="8" max="36" step="1" placeholder="12">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="row-font-size" data-step="1">+</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-bars"></i> 文字对齐</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px;margin-bottom:4px">垂直对齐:</label>' +
                    '<div class="btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="top" title="上对齐"><i class="fa fa-arrow-up"></i> 上</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="middle" title="居中"><i class="fa fa-arrows-v"></i> 中</button>' +
                        '<button type="button" class="btn btn-xs btn-default row-align-btn" data-align="bottom" title="下对齐"><i class="fa fa-arrow-down"></i> 下</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-tag"></i> 行类型</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<select id="row-type-select" class="form-control">' +
                        '<option value="">普通行</option>' +
                        '<option value="\u91cd\u590d\u6807\u9898\u884c">\u91cd\u590d\u6807\u9898\u884c</option>' +
                        '<option value="\u6570\u636e\u9a71\u52a8\u884c">\u6570\u636e\u9a71\u52a8\u884c</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-text-height"></i> 行显示效果</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<select id="row-display-select" class="form-control">' +
                        '<option value="">\u81ea\u52a8\u6362\u884c</option>' +
                        '<option value="\u56fa\u5b9a\u884c\u9ad8">\u56fa\u5b9a\u884c\u9ad8</option>' +
                        '<option value="\u81ea\u52a8\u7f29\u5c0f\u5b57\u4f53">\u81ea\u52a8\u7f29\u5c0f\u5b57\u4f53</option>' +
                    '</select>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-paint-brush"></i> 行样式</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px">CSS样式:</label>' +
                    '<textarea id="row-css-style" class="form-control css-editor" rows="2" placeholder="background-color: #f0f0f0;">' + cssPreview.trim() + '</textarea>' +
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

        // 行类型选择
        const rowTypeSelect = container.querySelector('#row-type-select');
        if (rowTypeSelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowTypeSelect.value = firstNonMerged?.row_type || '';
            rowTypeSelect.addEventListener('change', (e) => this.setRowTypeForRow(row, e.target.value));
        }
        // 行显示效果选择
        const rowDisplaySelect = container.querySelector('#row-display-select');
        if (rowDisplaySelect) {
            const firstNonMerged = this.getFirstNonMergedCell(row);
            rowDisplaySelect.value = firstNonMerged?.row_display || '';
            rowDisplaySelect.addEventListener('change', (e) => this.setRowDisplay(row, e.target.value));
        }
        // 垂直对齐按钮
        container.querySelectorAll('.row-align-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                this.updateRowStyle(row, 'css_style',
                    this.setCssProperty(this.rowStyles[row]?.css_style || '', 'vertical-align', btn.dataset.align));
            });
        });
    }

    // ==================== 行类型/行显示效果 ====================

    getFirstNonMergedCell(row) {
        for (let c = 0; c < this.cols; c++) {
            const cell = this.grid[row - 1]?.[c];
            if (cell && !cell._merged) return cell;
        }
        return null;
    }

    setRowType(rowType) {
        if (this.selectionMode !== 'row' || !this.selectedRow) {
            frappe.show_alert({ message: '\u8bf7\u5148\u70b9\u51fb\u5de6\u4fa7\u884c\u53f7\u9009\u62e9\u4e00\u6574\u884c', indicator: 'yellow' });
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
        const typeLabel = rowType || '\u666e\u901a\u884c';
        frappe.show_alert({ message: '\u7b2c' + row + '\u884c\u5df2\u8bbe\u7f6e\u4e3a: ' + typeLabel, indicator: 'green' });
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
        const rowHeaders = container.querySelectorAll('.row-header-cell');
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

    // ==================== 列属性面板 ====================

    renderColProperties(col) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;

        const colStyle = this.colStyles[col] || {};
        let cssPreview = '';
        if (colStyle.width) cssPreview += 'width:' + colStyle.width + 'px; ';
        if (colStyle.css_style) cssPreview += colStyle.css_style;

        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-arrows-h"></i> 列样式设置 <small style="color:#6c757d;font-weight:normal">(第' + col + '列)</small>';
        }

        let formHtml = '<form id="col-property-form" class="property-form">' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-expand"></i> 列尺寸</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<div class="layout-controls">' +
                        '<div class="layout-control-group" style="flex:1">' +
                            '<label style="font-size:9px">列宽:</label>' +
                            '<div class="number-spinner number-spinner-sm">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="col-width" data-step="10">-</button>' +
                                '<input type="number" id="col-width" class="form-control spin-input" value="' + (colStyle.width || '') + '" min="1" max="500" step="1" placeholder="60">' +
                                '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="col-width" data-step="10">+</button>' +
                            '</div>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-align-center"></i> 文字对齐</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px;margin-bottom:4px">水平对齐:</label>' +
                    '<div class="btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="left" title="左对齐"><i class="fa fa-align-left"></i> 左</button>' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="center" title="居中"><i class="fa fa-align-center"></i> 中</button>' +
                        '<button type="button" class="btn btn-xs btn-default col-align-btn" data-align="right" title="右对齐"><i class="fa fa-align-right"></i> 右</button>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="property-section">' +
                '<div class="property-section-header"><i class="fa fa-paint-brush"></i> 列样式</div>' +
                '<div class="property-section-body" style="padding:8px">' +
                    '<label style="font-size:9px">CSS样式:</label>' +
                    '<textarea id="col-css-style" class="form-control css-editor" rows="2" placeholder="text-align: center;">' + cssPreview.trim() + '</textarea>' +
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

        // 水平对齐按钮
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

    // ==================== 单元格属性面板 ====================

    renderCellProperties(cellId) {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const [row, col] = this.parseCellId(cellId);
        const cell = this.grid[row - 1]?.[col - 1];
        if (!cell || cell._merged) return;

        const titleElement = container.querySelector('.spd-props h4');
        if (titleElement) {
            titleElement.innerHTML = '<i class="fa fa-cog"></i> 单元格属性 <small style="color:#6c757d;font-weight:normal">(' + cellId + ')</small>';
        }

        const typeOptions = this.cellTypes.map(t => '<option value="' + t.value + '">' + t.label + '</option>').join('');
        const barcodeFormatOptions = this.barcodeFormats.map(f => '<option value="' + f.value + '">' + f.label + '</option>').join('');
        const queryOptions = this.generateQueryOptions();

        const activeTab = this.lastActiveTab || 'style';
        const contentTabCls = activeTab === 'content' ? ' active' : '';
        const styleTabCls = activeTab === 'style' ? ' active' : '';

        let formHtml = '<div class="prop-tabs">' +
            '<div class="prop-tab' + contentTabCls + '" data-tab="content"><i class="fa fa-edit"></i> 内容</div>' +
            '<div class="prop-tab' + styleTabCls + '" data-tab="style"><i class="fa fa-paint-brush"></i> 样式</div>' +
        '</div>' +
        '<div class="prop-tab-contents">' +
            '<div class="prop-tab-content' + contentTabCls + '" data-tab="content">' +
                '<label>类型:</label>' +
                '<select id="prop-cell-type" class="form-control">' + typeOptions + '</select>' +
                '<label>值:</label>' +
                '<textarea id="prop-cell-value" class="form-control" rows="2"></textarea>' +
                '<div id="query-group" style="display:none">' +
                    '<label>数据键:</label>' +
                    '<input id="prop-data-key" class="form-control" placeholder="如 item_code">' +
                '</div>' +
                '<div id="barcode-group" style="display:none">' +
                    '<label>条码格式:</label>' +
                    '<select id="prop-barcode-format" class="form-control">' + barcodeFormatOptions + '</select>' +
                    '<label>宽度(px):</label>' +
                    '<input type="number" id="prop-barcode-width" class="form-control" value="100">' +
                    '<label>高度(px):</label>' +
                    '<input type="number" id="prop-barcode-height" class="form-control" value="40">' +
                '</div>' +
                '<div id="qrcode-group" style="display:none">' +
                    '<p style="font-size:11px;color:#888;margin:4px 0;">二维码自动适配单元格尺寸（1:1）</p>' +
                '</div>' +
            '</div>' +
            '<div class="prop-tab-content' + styleTabCls + '" data-tab="style">' +
                '<div class="layout-controls">' +
                    '<div class="layout-control-group">' +
                        '<label>跨行:</label>' +
                        '<div class="number-spinner">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="prop-rowspan" data-step="1">-</button>' +
                            '<input type="number" id="prop-rowspan" class="form-control spin-input" min="1" max="100" value="' + (cell.rowspan || 1) + '">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="prop-rowspan" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="layout-control-group">' +
                        '<label>跨列:</label>' +
                        '<div class="number-spinner">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="prop-colspan" data-step="1">-</button>' +
                            '<input type="number" id="prop-colspan" class="form-control spin-input" min="1" max="26" value="' + (cell.colspan || 1) + '">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="prop-colspan" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="layout-controls">' +
                    '<div class="layout-control-group">' +
                        '<label>字体(px):</label>' +
                        '<div class="number-spinner">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="prop-font-size" data-step="1">-</button>' +
                            '<input type="number" id="prop-font-size" class="form-control spin-input" value="' + this.extractFontSize(cell.css_style, row) + '" min="8" max="36">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="prop-font-size" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="layout-control-group">' +
                        '<label>内边距:</label>' +
                        '<div class="number-spinner">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-minus" data-target="prop-padding" data-step="1">-</button>' +
                            '<input type="number" id="prop-padding" class="form-control spin-input" value="' + this.extractPadding(cell.css_style) + '" min="0" max="20">' +
                            '<button type="button" class="btn btn-xs spin-btn spin-plus" data-target="prop-padding" data-step="1">+</button>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="css-quick-buttons css-quick-compact">' +
                    '<label style="font-size:9px">快捷:</label>' +
                    '<div class="btn-group-wrap" style="margin-top:3px">' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:left" data-prop="text-align" title="左对齐"><i class="fa fa-align-left"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:center" data-prop="text-align" title="居中"><i class="fa fa-align-center"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="text-align:right" data-prop="text-align" title="右对齐"><i class="fa fa-align-right"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-weight:bold" data-prop="font-weight" title="粗体"><i class="fa fa-bold"></i></button>' +
                        '<button type="button" class="btn btn-xs btn-default css-quick-btn" data-css="font-style:italic" data-prop="font-style" title="斜体"><i class="fa fa-italic"></i></button>' +
                    '</div>' +
                    '<div class="btn-group-wrap" style="margin-top:3px">' +
                        '<div class="color-picker-wrapper" title="背景色"><i class="fa fa-fill-drip"></i><input type="color" id="bg-color-picker" value="#ffffff"></div>' +
                        '<div class="color-picker-wrapper" title="文字色"><i class="fa fa-font"></i><input type="color" id="text-color-picker" value="#000000"></div>' +
                        '<button type="button" class="btn btn-xs css-quick-btn" data-action="default-css" title="默认样式" style="width:auto;padding:0 6px;font-size:9px;background:#28a745;color:#fff;border-color:#28a745"><i class="fa fa-undo" style="color:#fff"></i> <span style="color:#fff">默认</span></button>' +
                        '<button type="button" class="btn btn-xs btn-danger css-quick-btn" data-action="clear-css" title="清除CSS" style="width:auto;padding:0 6px;font-size:9px"><i class="fa fa-eraser"></i> 清除</button>' +
                    '</div>' +
                '</div>' +
                '<div class="border-settings" style="margin:6px 0;padding:6px;background:#f8f9fa;border-radius:4px;border:1px solid #e9ecef">' +
                    '<label style="font-size:9px;margin-bottom:4px">边框:</label>' +
                    '<div class="btn-group-wrap" style="margin-top:2px;gap:2px">' +
                        '<div class="border-width-selector" style="display:flex;align-items:center;gap:2px">' +
                            '<button type="button" class="btn btn-xs btn-default border-width-btn bw-active" data-width="1" style="width:24px;height:20px;padding:0;font-size:8px">细</button>' +
                            '<button type="button" class="btn btn-xs btn-default border-width-btn" data-width="2" style="width:24px;height:20px;padding:0;font-size:8px">中</button>' +
                            '<button type="button" class="btn btn-xs btn-default border-width-btn" data-width="3" style="width:24px;height:20px;padding:0;font-size:8px">粗</button>' +
                            '<button type="button" class="btn btn-xs btn-default border-width-btn" data-width="0" style="width:24px;height:20px;padding:0;font-size:8px">无</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="btn-group-wrap" style="margin-top:4px;gap:2px">' +
                        '<button type="button" class="btn btn-xs btn-default border-side-btn" data-side="top" style="width:24px;height:20px;padding:0;font-size:8px">上</button>' +
                        '<button type="button" class="btn btn-xs btn-default border-side-btn" data-side="bottom" style="width:24px;height:20px;padding:0;font-size:8px">下</button>' +
                        '<button type="button" class="btn btn-xs btn-default border-side-btn" data-side="left" style="width:24px;height:20px;padding:0;font-size:8px">左</button>' +
                        '<button type="button" class="btn btn-xs btn-default border-side-btn" data-side="right" style="width:24px;height:20px;padding:0;font-size:8px">右</button>' +
                        '<button type="button" class="btn btn-xs btn-default border-side-btn" data-side="none" style="width:24px;height:20px;padding:0;font-size:8px">全</button>' +
                    '</div>' +
                '</div>' +
                '<label>CSS:</label>' +
                '<textarea id="prop-css-style" class="form-control css-editor" rows="3">' + (cell.css_style || '') + '</textarea>' +
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

        // Tab切换
        container.querySelectorAll('.prop-tab').forEach(tab => {
            tab.addEventListener('click', (e) => {
                container.querySelectorAll('.prop-tab').forEach(t => t.classList.remove('active'));
                container.querySelectorAll('.prop-tab-content').forEach(c => c.classList.remove('active'));
                tab.classList.add('active');
                const target = container.querySelector('.prop-tab-content[data-tab="' + tab.dataset.tab + '"]');
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
            // 扩展：检查新增区域是否都是独立单元格（非合并）
            for (let c = startCol + oldColspan; c < startCol + newColspan; c++) {
                if (c >= this.cols) {
                    frappe.show_alert({ message: '跨列数超出网格范围', indicator: 'red' });
                    return;
                }
                const target = this.grid[startRow]?.[c];
                if (target && !target._merged && (target.rowspan > 1 || target.colspan > 1)) {
                    frappe.show_alert({ message: '目标区域存在合并单元格，无法扩展', indicator: 'red' });
                    return;
                }
                if (target?._merged) {
                    frappe.show_alert({ message: '目标区域存在合并单元格，无法扩展', indicator: 'red' });
                    return;
                }
            }
            // 占用新增的独立单元格
            for (let c = startCol + oldColspan; c < startCol + newColspan; c++) {
                const target = this.grid[startRow][c];
                if (target && !target._merged) {
                    delete this.cellDataMap[target.cell_id];
                }
                this.grid[startRow][c] = { _merged: true, master_cell_id: cell.cell_id };
            }
        } else {
            // 收缩：释放的格子变为独立单元格
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
            // 扩展：检查新增区域
            for (let r = startRow + oldRowspan; r < startRow + newRowspan; r++) {
                if (r >= this.rows) {
                    frappe.show_alert({ message: '跨行数超出网格范围', indicator: 'red' });
                    return;
                }
                for (let c = startCol; c < startCol + (cell.colspan || 1); c++) {
                    const target = this.grid[r]?.[c];
                    if (target && !target._merged && (target.rowspan > 1 || target.colspan > 1)) {
                        frappe.show_alert({ message: '目标区域存在合并单元格，无法扩展', indicator: 'red' });
                        return;
                    }
                    if (target?._merged) {
                        frappe.show_alert({ message: '目标区域存在合并单元格，无法扩展', indicator: 'red' });
                        return;
                    }
                }
            }
            // 占用新增的独立单元格
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
            // 收缩：释放的格子变为独立单元格
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
        // 优先从单元格样式中提取，其次从行级样式，最后用设计器默认值
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
        container.querySelectorAll('.spin-btn').forEach(btn => {
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

        const widthBtns = container.querySelectorAll('.border-width-btn');
        widthBtns.forEach(btn => {
            const btnWidth = parseInt(btn.dataset.width);
            if (btnWidth === this.currentBorderWidth) btn.classList.add('bw-active');
            else btn.classList.remove('bw-active');
        });

        widthBtns.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                widthBtns.forEach(b => b.classList.remove('bw-active'));
                btn.classList.add('bw-active');
                this.currentBorderWidth = parseInt(btn.dataset.width);
            });
        });

        container.querySelectorAll('.border-side-btn').forEach(btn => {
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

        const sideNames = { top: '上', bottom: '下', left: '左', right: '右', none: '所有' };
        const allSides = ['top', 'right', 'bottom', 'left'];
        const borderVal = width > 0 ? width + 'px solid black' : '1px solid transparent';

        if (side === 'none') {
            // "全"按钮：对所有四边应用当前宽度
            allSides.forEach(s => { cssPairs['border-' + s] = borderVal; });
            const newCss = Object.entries(cssPairs).map(([k, v]) => k + ':' + v).join('; ');
            cssEditor.value = newCss;
            cssEditor.dispatchEvent(new Event('change', { bubbles: true }));
            if (width > 0) {
                frappe.show_alert({ message: '已设置所有边框 ' + width + 'px', indicator: 'green' });
            } else {
                frappe.show_alert({ message: '已将所有边框设为无', indicator: 'orange' });
            }
            return;
        }

        cssPairs['border-' + side] = borderVal;
        const newCss = Object.entries(cssPairs).map(([k, v]) => k + ':' + v).join('; ');
        cssEditor.value = newCss;
        cssEditor.dispatchEvent(new Event('change', { bubbles: true }));
        if (width > 0) {
            frappe.show_alert({ message: '已设置' + sideNames[side] + '边框 ' + width + 'px', indicator: 'green' });
        } else {
            frappe.show_alert({ message: '已将' + sideNames[side] + '边框设为无', indicator: 'orange' });
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
                    frappe.show_alert({ message: '已恢复默认样式', indicator: 'green' });
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
            frappe.show_alert({ message: '已清除 ' + propName + ' 属性', indicator: 'orange' });
        }
    }

    generateQueryOptions() {
        if (this.frm.doc.query_code) {
            return '<option value="main">主查询</option>';
        }
        return '';
    }

    saveDesign() {
        // 从工具栏输入框同步当前行列数
        const container = document.getElementById(this.designContainerId);
        if (container) {
            const inputRows = parseInt(container.querySelector('#spd-rows')?.value);
            const inputCols = parseInt(container.querySelector('#spd-cols')?.value);
            if (inputRows && inputRows > 0) this.rows = inputRows;
            if (inputCols && inputCols > 0) this.cols = inputCols;
        }

        const items = [];
        for (let row = 1; row <= this.rows; row++) {
            for (let col = 1; col <= this.cols; col++) {
                const cell = this.grid[row - 1]?.[col - 1];
                if (!cell) {
                    items.push(this.createBlankItem(row, col));
                } else if (cell._merged) {
                    items.push(this.createMergedItem(row, col, cell.master_cell_id));
                } else {
                    items.push({
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

        this.frm.set_value('rows', this.rows);
        this.frm.set_value('columns', this.cols);
        this.frm.set_value('row_styles', JSON.stringify(this.rowStyles));
        this.frm.set_value('col_styles', JSON.stringify(this.colStyles));
        this.frm.set_value('font_family', this.fontFamily);
        this.frm.set_value('design_items', items);
        this.frm.save().then(() => {
            frappe.show_alert({ message: '设计已保存，' + items.length + ' 个单元格', indicator: 'green' });
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
        if (!confirm('确定清空所有设计？此操作不可撤销。')) return;
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
        frappe.show_alert({ message: '设计已清空', indicator: 'yellow' });
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
        frappe.show_alert({ message: '网格已更新为 ' + newRows + 'x' + newCols, indicator: 'green' });
    }

    refreshGrid() {
        const container = document.getElementById(this.designContainerId);
        if (!container) return;
        const grid = container.querySelector('#spd-grid');
        if (grid) {
            grid.innerHTML = this.generateGridHtml();
            grid.style.fontFamily = "'" + this.fontFamily + "', sans-serif";
        }
        // 更新行列号
        const colHeaders = container.querySelector('#spd-col-headers');
        const rowHeaders = container.querySelector('#spd-row-headers');
        if (colHeaders) colHeaders.innerHTML = this.generateColHeadersHtml();
        if (rowHeaders) rowHeaders.innerHTML = this.generateRowHeadersHtml();
        // 偏移同步
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
        // 更新页眉页脚
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
            const placeholder = '<span style="color:#ccc;font-size:10px;">\u9875\u7709\u533a\u57df</span>';
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
            const placeholder = '<span style="color:#ccc;font-size:10px;">\u9875\u811a\u533a\u57df</span>';
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

    // 查询定义弹窗
    showQueryDialog() {
        const dialog = new frappe.ui.Dialog({
            title: '查询定义',
            fields: [
                { fieldname: 'query_code', label: 'Python查询代码', fieldtype: 'Code', options: 'Python',
                  default: this.frm.doc.query_code || '',
                  description: '将查询结果赋给变量 result' },
                { fieldname: 'query_parameters', label: '查询参数', fieldtype: 'Small Text',
                  default: this.frm.doc.query_parameters || '',
                  description: '每行一个参数，格式: key=value' }
            ],
            primary_action_label: '保存',
            primary_action: (values) => {
                this.frm.set_value('query_code', values.query_code);
                this.frm.set_value('query_parameters', values.query_parameters);
                this.frm.dirty();
                frappe.show_alert({ message: '查询定义已更新（需保存文档生效）', indicator: 'green' });
                dialog.hide();
            }
        });
        dialog.show();
    }

    // 参数定义弹窗
    showParamsDialog() {
        const params = this.frm.doc.design_parameters || [];
        let listHtml = params.length > 0
            ? params.map((p, i) => '<tr>' +
                '<td>' + p.param_name + '</td><td>' + p.param_type + '</td><td>' + (p.default_value || '') + '</td>' +
                '<td><button class="btn btn-xs btn-default edit-p" data-idx="' + i + '"><i class="fa fa-edit"></i></button></td>' +
                '<td><button class="btn btn-xs btn-danger del-p" data-idx="' + i + '"><i class="fa fa-trash"></i></button></td>' +
              '</tr>').join('')
            : '<tr><td colspan="5" class="text-muted text-center">暂无参数定义</td></tr>';

        const dialog = new frappe.ui.Dialog({
            title: '参数定义管理',
            fields: [{ fieldtype: 'HTML', options: '<div><button class="btn btn-primary btn-sm add-p">新增参数</button><table class="table table-bordered mt-2"><thead><tr><th>名称</th><th>类型</th><th>默认值</th><th>编辑</th><th>删除</th></tr></thead><tbody>' + listHtml + '</tbody></table></div>' }],
            primary_action_label: '关闭',
            primary_action: () => dialog.hide()
        });
        dialog.show();

        setTimeout(() => {
            dialog.$wrapper.find('.add-p').on('click', () => this.showParamEditDialog(dialog, -1));
            dialog.$wrapper.find('.edit-p').on('click', (e) => this.showParamEditDialog(dialog, parseInt($(e.currentTarget).data('idx'))));
            dialog.$wrapper.find('.del-p').on('click', (e) => {
                const idx = parseInt($(e.currentTarget).data('idx'));
                frappe.confirm('确定删除此参数？', () => {
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
            title: isEdit ? '编辑参数' : '新增参数',
            fields: [
                { fieldname: 'param_name', label: '参数标识', fieldtype: 'Data', reqd: 1, default: p.param_name },
                { fieldname: 'param_label', label: '显示标签', fieldtype: 'Data', default: p.param_label },
                { fieldname: 'param_type', label: '类型', fieldtype: 'Select', options: 'Data\nInt\nFloat\nDate\nLink\nSelect', default: p.param_type || 'Data' },
                { fieldname: 'default_value', label: '默认值', fieldtype: 'Data', default: p.default_value },
                { fieldname: 'reqd', label: '必填', fieldtype: 'Check', default: p.reqd },
                { fieldname: 'options', label: '选项/目标DocType', fieldtype: 'Small Text', default: p.options }
            ],
            primary_action_label: isEdit ? '更新' : '添加',
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

    addCssStyles() {
        const styleId = 'spd-styles';
        let style = document.getElementById(styleId);
        if (style) style.remove();
        style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
            .spd-container { display:flex; flex-direction:column; gap:10px; margin-top:15px; }
            .spd-toolbar { display:flex; align-items:center; gap:10px; padding:8px 12px; background:#f8f9fa; border-radius:6px; border:1px solid #dee2e6; flex-wrap:wrap; }
            .spd-controls { display:flex; align-items:center; gap:5px; }
            .spd-controls label { margin:0; font-weight:600; font-size:12px; }
            .spd-actions { display:flex; gap:6px; margin-left:auto; }
            .spd-main { display:flex; gap:12px; min-height:400px; }

            /* 网格容器 - 灰色背景 + 滚动 */
            .spd-grid-wrap { flex:1; overflow:auto; max-height:600px; border:1px solid #dee2e6; border-radius:6px; background:#e0e0e0; padding:20px; display:flex; justify-content:center; align-items:flex-start; }

            /* 纸张预览层 */
            .spd-paper-preview { }
            .grid-wrapper { display:flex; flex-direction:column; align-items:flex-start; }

            /* 行列体: 行号在左，纸张(含列号+表格)在右 */
            .grid-body { display:flex; flex-direction:row; align-items:flex-start; }

            /* 行号 - 与表格行逐行对齐 */
            .row-headers { display:flex; flex-direction:column; flex-shrink:0; }
            .row-header-cell { background:#e9ecef; border-top:1px solid #dee2e6; border-left:1px solid #dee2e6; border-bottom:1px solid #dee2e6; border-right:none; padding:0 2px; text-align:center; font-size:9px; color:#495057; font-weight:600; cursor:pointer; width:22px; box-sizing:border-box; overflow:hidden; }
            .row-header-cell:first-child { border-top-left-radius:3px; }
            .row-header-cell:last-child { border-bottom-left-radius:3px; }
            .row-header-cell:hover { background:#d0d0d0; }
            .row-header-cell.row-header-selected { background:#c8e6c9; border-color:#4caf50; }
            .row-header-cell { position:relative; }
            .row-type-badge { position:absolute; top:0; left:0; font-size:7px; font-weight:bold; padding:0 2px; border-radius:0 0 2px 0; line-height:1; z-index:2; color:#fff; }
            .badge-repeat { background:#ff9800; }
            .badge-data { background:#2196f3; }
            .row-header-repeat-title { border-left:3px solid #ff9800 !important; }
            .row-header-data-driven { border-left:3px solid #2196f3 !important; }
            .spd-row-type-controls { gap:3px; }

            /* 纸张 - 白色，无padding，严格按纸张物理尺寸渲染 */
            .spd-paper { background:#fff; box-shadow:0 4px 20px rgba(0,0,0,0.15); position:relative; overflow:visible; box-sizing:border-box; }

            /* 边距虚线 - 在纸张内部，由inline style定位到边距边界 */
            .spd-margin-line { position:absolute; border:1px dashed rgba(0,120,215,0.4); pointer-events:none; z-index:1; }

            /* 列号 - 纸张外上方 */
            .col-headers { display:flex; flex-shrink:0; }
            .col-header-cell { background:#e9ecef; border-left:1px solid #dee2e6; border-top:1px solid #dee2e6; border-right:1px solid #dee2e6; padding:2px 0; text-align:center; font-size:9px; color:#495057; font-weight:600; cursor:pointer; display:flex; align-items:center; justify-content:center; box-sizing:border-box; }
            .col-header-cell:first-child { border-top-left-radius:0; }
            .col-header-cell:last-child { border-top-right-radius:3px; }
            .col-header-cell:hover { background:#d0d0d0; }
            .col-header-cell.col-header-selected { background:#c8e6c9; border-color:#4caf50; }

            /* 网格表格 */
            .spd-grid { border-collapse:collapse; table-layout:fixed; }
            .spd-grid td { border:1px solid #dee2e6; cursor:pointer; text-align:center; overflow:hidden; padding:0; position:relative; }
            .spd-grid td:hover { background:#f0f7ff !important; }
            .spd-grid td.selected { background:#e3f2fd !important; border:2px solid #2196f3 !important; }
            .spd-grid tr.row-selected td { background-color:#fff3e0 !important; }
            .spd-grid td.col-selected { background-color:#e8f5e9 !important; }

            .cell-preview { display:flex; align-items:center; justify-content:center; gap:2px; font-size:8px; color:#999; }
            .cell-preview i { font-size:10px; }
            .cell-content { font-size:inherit; padding:2px; word-break:break-all; overflow:hidden; }

            /* 属性面板 */
            .spd-props { width:280px; min-width:280px; background:#fff; border:1px solid #ddd; border-radius:6px; padding:10px; overflow-y:auto; max-height:600px; }
            .spd-props h4 { margin:0 0 10px; padding-bottom:6px; border-bottom:1px solid #ddd; font-size:13px; }
            .spd-props label { display:block; margin:5px 0 2px; font-size:10px; font-weight:600; color:#495057; }
            .spd-props input,.spd-props select,.spd-props textarea { width:100%; padding:4px 6px; margin-bottom:5px; font-size:11px; }

            /* 属性 section */
            .property-section { margin-bottom:8px; border:1px solid #e9ecef; border-radius:4px; overflow:hidden; }
            .property-section-header { padding:6px 8px; background:#f8f9fa; font-size:10px; font-weight:600; color:#495057; cursor:pointer; display:flex; align-items:center; gap:4px; }
            .property-section-header i { font-size:10px; }

            .prop-tabs { display:flex; gap:2px; margin-top:8px; }
            .prop-tab { flex:1; padding:8px; text-align:center; cursor:pointer; font-size:11px; font-weight:600; background:#f8f9fa; border:1px solid #dee2e6; border-radius:6px 6px 0 0; }
            .prop-tab.active { background:#fff; color:#2196f3; border-bottom-color:#fff; }
            .prop-tab-contents { border:1px solid #dee2e6; border-radius:0 0 6px 6px; padding:8px; }
            .prop-tab-content { display:none; }
            .prop-tab-content.active { display:block; }
            .css-quick-buttons { display:flex; flex-wrap:wrap; gap:3px; margin:6px 0; }
            .css-quick-buttons .btn { width:28px; height:28px; padding:0; display:flex; align-items:center; justify-content:center; }

            .layout-controls { display:flex; gap:8px; margin-bottom:6px; }
            .layout-control-group { flex:1; }
            .layout-control-group label { font-size:9px; margin-bottom:2px; }

            .number-spinner { display:flex; align-items:center; gap:2px; }
            .number-spinner .spin-input { width:50px; text-align:center; padding:2px; }
            .number-spinner .spin-btn { width:24px; height:24px; padding:0; display:flex; align-items:center; justify-content:center; font-size:14px; }
            .number-spinner-sm .spin-input { width:45px; }

            .color-picker-wrapper { position:relative; display:inline-flex; align-items:center; justify-content:center; width:28px; height:28px; border:1px solid #ccc; border-radius:3px; cursor:pointer; }
            .color-picker-wrapper i { position:relative; z-index:1; pointer-events:none; font-size:12px; }
            .color-picker-wrapper input[type="color"] { position:absolute; top:0; left:0; width:100%; height:100%; opacity:0; cursor:pointer; border:none; padding:0; }

            .btn-group-wrap { display:flex; align-items:center; gap:3px; }
            .css-quick-compact .btn-group-wrap .btn { width:28px; height:28px; padding:0; display:flex; align-items:center; justify-content:center; }

            .border-settings .border-width-btn.bw-active { background:#2196f3; color:#fff; }
            .border-settings .border-side-btn:hover { background:#e3f2fd; }

            .css-editor { font-family:monospace; font-size:10px; }
        `;
        document.head.appendChild(style);
    }
}

let spd_designer = null;

frappe.ui.form.on('Super Print Design', {
    setup(frm) {
        setTimeout(() => {
            if (frm.page && frm.page.sidebar) {
                frm.page.sidebar.hide();
            }
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
        spd_designer.init();

        setTimeout(() => {
            if (frm.page && frm.page.sidebar) {
                frm.page.sidebar.hide();
            }
            // 限制页眉页脚 Code 字段高度
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
            await spd_designer.loadPaperSize();
            const html = spd_designer.generateDesigner();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) {
                    spd_designer.addCssStyles();
                    spd_designer.bindEvents();
                }
            }, 100);
        } else {
            frm.set_df_property('design_html', 'options',
                '<div class="alert alert-info" style="margin-top:15px"><h5>请先选择目标文档类型</h5></div>');
            refresh_field('design_html');
        }
    },
    async target_doctype(frm) {
        if (frm.doc.target_doctype && spd_designer) {
            spd_designer = new SuperPrintDesigner(frm);
            spd_designer.init();
            await spd_designer.loadPaperSize();
            const html = spd_designer.generateDesigner();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) { spd_designer.addCssStyles(); spd_designer.bindEvents(); }
            }, 100);
        }
    },
    async print_paper(frm) {
        if (spd_designer && frm.doc.print_paper) {
            await spd_designer.loadPaperSize();
            // 重新渲染设计器以应用新纸张尺寸
            const html = spd_designer.generateDesigner();
            frm.set_df_property('design_html', 'options', html);
            refresh_field('design_html');
            setTimeout(() => {
                if (spd_designer) { spd_designer.addCssStyles(); spd_designer.bindEvents(); }
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
