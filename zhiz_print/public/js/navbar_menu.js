// 用户头像下拉菜单注入「打印模板平台」入口(Frappe v15 #toolbar-user)
// 标记 frappe v16 供 CSS 区分(边栏左右等版本差异)
$(function() { if ((frappe.boot?.versions?.frappe || '').startsWith('16')) $('body').addClass('zhiz-v16'); });
$(document).on('shown.bs.dropdown', function() {
    var $menu = $('#toolbar-user');
    if (!$menu.length || $menu.find('.zhiz-print-store-link').length) return;
    var $theme = $menu.find('.dropdown-item[onclick*="ThemeSwitcher"]');
    if ($theme.length) {
        $theme.after('<a class="dropdown-item zhiz-print-store-link" href="/app/print-template-store">打印模板平台</a>');
    } else {
        $menu.append('<a class="dropdown-item zhiz-print-store-link" href="/app/print-template-store">打印模板平台</a>');
    }
});
