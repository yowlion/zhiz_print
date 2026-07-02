frappe.listview_settings['Super Print Design'] = {
	onload: function(listview) {
		listview.page.add_actions_menu_item(__('模板商店'), function() {
			frappe.set_route('print-template-store');
		});
		listview.page.add_inner_button(__('模板商店'), function() {
			frappe.set_route('print-template-store');
		});
	}
};
