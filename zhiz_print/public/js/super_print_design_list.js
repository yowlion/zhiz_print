frappe.listview_settings['Super Print Design'] = {
	onload: function(listview) {
		listview.page.add_actions_menu_item(__('模板平台'), function() {
			frappe.set_route('print-template-store');
		});
		listview.page.add_inner_button(__('模板平台'), function() {
			frappe.set_route('print-template-store');
		});
		// 第三参是 category(会变下拉),改 filter 上色
		$('.btn').filter(function () { return $(this).text().trim() === __('模板平台'); }).removeClass('btn-default').addClass('btn-primary');
	}
};
