// 用户头像下拉菜单注入「打印模板平台」入口(Frappe v15 #toolbar-user)
$(document).on('shown.bs.dropdown', function() {
    var $menu = $('#toolbar-user');
    if (!$menu.length || $menu.find('.zhiz-print-store-link').length) return;
    $menu.append('<div class="dropdown-divider"></div>');
    $menu.append('<a class="dropdown-item zhiz-print-store-link" href="/app/print-template-store"><i class="fa fa-fw fa-print"></i> ' + __('打印模板平台') + '</a>');
});
