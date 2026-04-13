frappe.ui.form.on('Zprint Setting', {
    refresh(frm) {
        frm.add_custom_button(__('Refresh Boot Cache'), function() {
            frappe.call({
                method: 'zhiz_print.api.print_setting.refresh_boot_cache',
                freeze: true,
                freeze_message: __('Refreshing cache...'),
                callback(r) {
                    if (r.message) {
                        frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                        frappe.boot.zhiz_print.print_designer = r.message;
                        frappe.show_alert({message: __('Boot cache refreshed'), indicator: 'green'});
                    }
                }
            });
        });
    },

    after_save(frm) {
        frappe.call({
            method: 'zhiz_print.api.print_setting.refresh_boot_cache',
            callback(r) {
                if (r.message) {
                    frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                    frappe.boot.zhiz_print.print_designer = r.message;
                }
            }
        });
    }
});
