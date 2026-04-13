frappe.ui.form.on('Zprint Setting', {
    refresh(frm) {
        // Refresh Boot Cache button
        frm.add_custom_button(__('Refresh Boot Cache'), function() {
            frappe.call({
                method: 'zhiz_print.api.print_setting.refresh_boot_cache',
                freeze: true,
                freeze_message: __('Refreshing cache...'),
                callback(r) {
                    if (r.message) {
                        frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                        frappe.boot.zhiz_print.print_designer = r.message.print_designer;
                        frappe.boot.zhiz_print.license = r.message.license;
                        frappe.show_alert({message: __('Boot cache refreshed'), indicator: 'green'});
                        frm.trigger('render_license_info');
                    }
                }
            });
        });

        // Activate License button
        frm.add_custom_button(__('Activate License'), function() {
            let key = frm.doc.license_key_input;
            if (!key || !key.trim()) {
                frappe.msgprint(__('Please enter a license key first'));
                return;
            }
            frappe.call({
                method: 'zhiz_print.api.license.activate_license',
                args: { license_key: key.trim() },
                freeze: true,
                freeze_message: __('Activating license...'),
                callback(r) {
                    if (r.message && r.message.success) {
                        frappe.show_alert({message: r.message.message, indicator: 'green'});
                        frm.set_value('license_key_input', '');
                        // Refresh boot cache after activation
                        frappe.call({
                            method: 'zhiz_print.api.print_setting.refresh_boot_cache',
                            callback(r2) {
                                if (r2.message) {
                                    frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                                    frappe.boot.zhiz_print.license = r2.message.license;
                                }
                                frm.trigger('render_license_info');
                            }
                        });
                    }
                }
            });
        }).addClass('btn-primary');

        // Render license info
        frm.trigger('render_license_info');
    },

    render_license_info(frm) {
        let container = document.getElementById('license-info-container');
        if (!container) return;

        let lic = frappe.boot.zhiz_print?.license || {};
        let html = '';

        if (lic.valid) {
            let statusColor = lic.trial ? '#f39c12' : '#27ae60';
            let statusText = lic.trial ? __('Trial') : __(lic.plan || 'Active');
            html = '<div style="display:flex;gap:20px;align-items:center;padding:10px 0">';
            html += '<span style="font-size:13px"><strong>' + __('Status') + ':</strong> ';
            html += '<span style="color:' + statusColor + ';font-weight:600">' + statusText + '</span></span>';
            if (lic.expires_at) {
                html += '<span style="font-size:13px"><strong>' + __('Expires') + ':</strong> ' + frappe.datetime.str_to_user(lic.expires_at.split(' ')[0]) + '</span>';
            }
            html += '<span style="font-size:12px;color:#999"><strong>' + __('Machine ID') + ':</strong> ' + (lic.machine_id || '') + '</span>';
            html += '</div>';
        } else {
            html = '<div style="padding:10px 0;color:#e74c3c;font-weight:600">';
            html += '<i class="fa fa-exclamation-triangle"></i> ';
            html += lic.message || __('License expired or not activated');
            html += '</div>';
        }

        container.innerHTML = html;
    },

    after_save(frm) {
        frappe.call({
            method: 'zhiz_print.api.print_setting.refresh_boot_cache',
            callback(r) {
                if (r.message) {
                    frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                    frappe.boot.zhiz_print.print_designer = r.message.print_designer;
                    frappe.boot.zhiz_print.license = r.message.license;
                }
            }
        });
    }
});
