// Report Print Interceptor (v15.23 报表打印 P1)
// Patches frappe.views.QueryReport.get_menu_items — 启用即接管:
// Zprint Setting 开启报表打印且命中(All/Specific)时:
//   · 原生 Print 菜单项保留原标签,点击改跳超级打印预览页(路径变掉,入口不变)
//   · 原生 PDF / Export(Excel/CSV) 菜单项移除(打印预览页内提供 PDF/Excel 导出)
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

            // Print:保留原标签,替换 action 与跳转路径
            if (label === __('Print') || label === 'Print') {
                return Object.assign({}, item, { action: openPrint });
            }
            // PDF / Export(Excel/CSV):移除(PDF/Excel 导出都在打印预览页内)
            if (label === __('PDF') || label === 'PDF' ||
                label === __('Export') || label === 'Export') {
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
    let filters = {};
    try {
        filters = query_report.get_filter_values
            ? query_report.get_filter_values()
            : {};
    } catch (e) {
        console.warn('zhiz_print: get_filter_values failed', e);
    }
    filters = filters || {};

    // 筛选双通道:URL 直传(可见/可分享/可收藏,预览页优先读取)
    // + 按报表名分键的 sessionStorage 兜底(超长筛选或直接打开页面场景)
    try {
        sessionStorage.setItem('spd_report_filters:' + query_report.report_name,
            JSON.stringify(filters));
    } catch (e) { /* ignore */ }

    const qs = Object.keys(filters)
        .filter(k => filters[k] !== '' && filters[k] != null)
        .map(k => encodeURIComponent(k) + '=' + encodeURIComponent(filters[k]))
        .join('&');

    // 整页跳转(非 SPA set_route):确保 print 页面全新加载并携带 query;
    // spd_report_print=1 标记打印意图来自报表查询页 —— print.js 只认此标记进报表模式,
    // Report 文档表单页(/app/report/<name>)的原生打印不受影响(v15.22.39)
    window.location.href = '/app/print/Report/' + encodeURIComponent(query_report.report_name)
        + (qs ? '?' + qs + '&spd_report_print=1' : '?spd_report_print=1');
};
