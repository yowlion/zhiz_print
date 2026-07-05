// 用户头像下拉菜单注入「打印模板平台」入口
$(document).on('shown.bs.dropdown', function(e) {
    var $menu = $(e.target).find('.dropdown-menu');
    if (!$menu.length) return;
    // 确认是用户菜单(含 My Settings/Logout/退出)
    if (!$menu.find('a:contains("My Settings"), a:contains("Logout"), a:contains("退出"), a:contains("设置")').length) return;
    if ($menu.find('.zhiz-print-store-link').length) return;
    $menu.append('<li class="divider zhiz-print-store-divider"></li>');
    $menu.append('<li class="zhiz-print-store-link"><a href="/app/print-template-store"><i class="fa fa-fw fa-print"></i> ' + __('打印模板平台') + '</a></li>');
});
