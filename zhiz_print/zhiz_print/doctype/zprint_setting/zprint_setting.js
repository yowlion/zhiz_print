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

        // Activate License button — opens dialog
        frm.add_custom_button(__('Activate License'), function() {
            let dialog = new frappe.ui.Dialog({
                title: __('Activate License'),
                fields: [
                    {
                        fieldname: 'license_key',
                        fieldtype: 'Data',
                        label: __('License Key'),
                        reqd: 1,
                        description: __('Enter the license key provided by the vendor')
                    }
                ],
                primary_action_label: __('Activate'),
                primary_action(values) {
                    if (!values.license_key || !values.license_key.trim()) {
                        frappe.msgprint(__('Please enter a license key'));
                        return;
                    }
                    frappe.call({
                        method: 'zhiz_print.api.license.activate_license',
                        args: { license_key: values.license_key.trim() },
                        freeze: true,
                        freeze_message: __('Activating license...'),
                        callback(r) {
                            if (r.message && r.message.success) {
                                dialog.hide();
                                frappe.show_alert({message: r.message.message, indicator: 'green'});
                                // Refresh boot cache and update UI
                                frappe.call({
                                    method: 'zhiz_print.api.print_setting.refresh_boot_cache',
                                    callback(r2) {
                                        if (r2.message) {
                                            frappe.boot.zhiz_print = frappe.boot.zhiz_print || {};
                                            frappe.boot.zhiz_print.license = r2.message.license;
                                        }
                                        frm.trigger('render_license_info');
                                        // Update current license display
                                        frm.trigger('load_current_license');
                                    }
                                });
                            }
                        }
                    });
                }
            });
            dialog.show();
        }).addClass('btn-primary');

        // Render license info
        frm.trigger('render_license_info');
        frm.trigger('load_current_license');
        frm.trigger('render_contact_info');
    },

    load_current_license(frm) {
        // Load current active license key to display in read-only field
        frappe.db.get_value('Zprint License', {'status': 'Active'}, 'license_key')
            .then(r => {
                if (r && r.message && r.message.license_key) {
                    frm.doc.license_key_input = r.message.license_key;
                } else {
                    frm.doc.license_key_input = '';
                }
                // Directly update display without triggering dirty flag
                frm.get_field('license_key_input').refresh();
            });
    },

    render_license_info(frm) {
        let container = document.getElementById('license-info-container');
        if (!container) return;

        // Always fetch fresh license status from server
        frappe.call({
            method: 'zhiz_print.api.license.get_license_status',
            callback(r) {
                let lic = (r.message || {});
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
                } else if (lic.status === 'Locked') {
                    let expiry_str = lic.expires_at ? frappe.datetime.str_to_user(lic.expires_at.split(' ')[0]) : '';
                    html = '<div style="padding:10px 0;color:#e67e22;font-weight:600">';
                    html += '<i class="fa fa-lock"></i> ';
                    html += __('License expires at {0}, status is locked, please contact vendor to unlock.').replace('{0}', expiry_str);
                    html += '</div>';
                } else if (lic.status === 'Expired') {
                    let expiry_str = lic.expires_at ? frappe.datetime.str_to_user(lic.expires_at.split(' ')[0]) : '';
                    html = '<div style="padding:10px 0;color:#e74c3c;font-weight:600">';
                    html += '<i class="fa fa-exclamation-triangle"></i> ';
                    html += __('License expired on {0}. Please renew your subscription.').replace('{0}', expiry_str);
                    html += '</div>';
                } else if (lic.status === 'Revoked') {
                    html = '<div style="padding:10px 0;color:#e74c3c;font-weight:600">';
                    html += '<i class="fa fa-ban"></i> ';
                    html += __('License has been revoked.');
                    html += '</div>';
                } else {
                    html = '<div style="padding:10px 0;color:#e74c3c;font-weight:600">';
                    html += '<i class="fa fa-exclamation-triangle"></i> ';
                    html += __('Not activated. Please refresh boot cache to get a trial license or purchase a full license.');
                    html += '</div>';
                }

                container.innerHTML = html;
            }
        });
    },

    render_contact_info(frm) {
        let container = document.getElementById('contact-info-container');
        if (!container) return;

        frappe.call({
            method: 'zhiz_print.zhiz_print.doctype.zprint_setting.zprint_setting.get_contact_qr',
            callback(r) {
                let data = r.message || {};
                let html = '<div style="margin-top:15px;padding:15px 0;border-top:1px solid #ddd">';
                html += '<div style="text-align:center;margin-bottom:12px;font-size:13px;color:#666">';
                html += __('如需授权或技术支持，请通过以下方式联系') + ':</div>';
                html += '<div style="display:flex;justify-content:center;gap:60px;flex-wrap:wrap">';
                html += '<div style="text-align:center">';
                html += '<img src="' + (data.wechat_qr || '') + '" style="width:140px;height:140px;border-radius:8px;border:1px solid #e0e0e0;object-fit:cover">';
                html += '<div style="margin-top:6px;font-size:12px;color:#888">' + __('微信') + '</div>';
                html += '</div>';
                html += '<div style="text-align:center">';
                html += '<img src="' + (data.wechat_work_qr || '') + '" style="width:140px;height:140px;border-radius:8px;border:1px solid #e0e0e0;object-fit:cover">';
                html += '<div style="margin-top:6px;font-size:12px;color:#888">' + __('企业微信') + '</div>';
                html += '</div>';
                html += '<div style="text-align:center">';
                html += '<img src="' + (data.whatsapp_qr || '') + '" style="width:140px;height:140px;border-radius:8px;border:1px solid #e0e0e0;object-fit:cover">';
                html += '<div style="margin-top:6px;font-size:12px;color:#888">WhatsApp</div>';
                html += '</div>';
                html += '</div>';
                html += '<div style="text-align:center;margin-top:12px;font-size:13px;color:#555">';
                html += '<i class="fa fa-phone" style="margin-right:4px"></i> +86 13913019935';
                html += '</div>';
                html += '</div>';
                container.innerHTML = html;
            }
        });
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
