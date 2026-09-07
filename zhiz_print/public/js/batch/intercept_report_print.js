// Report Print Interceptor (v15.23 报表打印 P1)
// Patches frappe.views.QueryReport.get_menu_items — 启用即接管:
// Zprint Setting 开启报表打印且命中(All/Specific)时:
//   · 原生 Print / PDF 菜单项保留原标签,点击改跳超级打印预览页(路径变掉,入口不变)
//   · 原生 Export(Excel/CSV) 菜单项移除(打印预览页内提供 Excel 导出)
// 未启用/未命中:原样返回原生菜单,关开关即还原。

frappe.provide('zhiz_print.report');

(function patchReportPrint() {
    if (!frappe.views || !frappe.views.QueryReport) {
        setTimeout(patchReportPrint, 100);
        return;
    }

    const origGetMenuItems = frappe.views.QueryReport.prototype.get_menu_items;
    if (origGetMenuItems._zhiz_report_print_patched) return;

    const patched = function () {
        const items = origGetMenuItems.call(this) || [];

        if (!zhiz_print.report.is_enabled_for(this.report_name)) {
            return items;
        }

        const openPrint = () => zhiz_print.report.open_print_page(this);

        return items.map(item => {
            const label = typeof item.label === 'string' ? item.label : (item.label?.__str__ || '');

            // Print / PDF:保留原标签,替换 action 与跳转路径
            if (label === __('Print') || label === 'Print' ||
                label === __('PDF') || label === 'PDF') {
                return Object.assign({}, item, { action: openPrint });
            }
            // Export(Excel/CSV):移除(预览页内已有 Excel 导出)
            if (label === __('Export') || label === 'Export') {
                return null;
            }
            return item;
        }).filter(Boolean);
    };
    patched._zhiz_report_print_patched = true;
    frappe.views.QueryReport.prototype.get_menu_items = patched;
})();

// 报表打印启用判定 + 入口动作
zhiz_print.report.is_enabled_for = function (report_name) {
    const rd = frappe.boot.zhiz_print?.print_designer;
    if (!rd || !rd.report_enabled) return false;
    if (!rd.report_enable_mode || rd.report_enable_mode === 'Enable for All') {
        return true;
    }
    return (rd.report_enabled_list || []).includes(report_name);
};

zhiz_print.report.open_print_page = function (query_report) {
    if (!query_report || !query_report.report_name) return;
    // 筛选值经 sessionStorage 传递(不落 URL,避免长度/敏感问题)
    let filters = {};
    try {
        filters = query_report.get_filter_values
            ? query_report.get_filter_values()
            : {};
    } catch (e) {
        console.warn('zhiz_print: get_filter_values failed', e);
    }
    sessionStorage.setItem('spd_report_filters', JSON.stringify(filters || {}));
    frappe.set_route('print', 'Report', query_report.report_name);
};
