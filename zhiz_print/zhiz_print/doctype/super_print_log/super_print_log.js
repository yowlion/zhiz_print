frappe.ui.form.on('Super Print Log', {
	refresh: function(frm) {
		let field = frm.get_field('preview_render');
		if (!field) return;
		let wrapper = field.$wrapper;
		wrapper.empty();

		if (!frm.doc.print_preview_html) {
			wrapper.html('<div style="padding:32px;color:#999;text-align:center;background:#fafafa;border:1px dashed #ddd;border-radius:6px">' +
				'<i class="fa fa-eye-slash" style="font-size:28px"></i>' +
				'<p style="margin:10px 0 0;font-size:13px">' + __('No preview snapshot for this log.') + '</p>' +
				'<p style="margin:4px 0 0;font-size:11px;color:#bbb">' + __('Batch-printed logs are not captured.') + '</p></div>');
			return;
		}

		let iframe = $('<iframe>').css({
			width: '100%',
			height: '800px',
			border: '1px solid #e0e0e0',
			'border-radius': '6px',
			background: '#f5f5f5'
		});
		wrapper.append(iframe);

		let iframeDoc = iframe[0].contentDocument || iframe[0].contentWindow.document;
		iframeDoc.open();
		iframeDoc.write(frm.doc.print_preview_html);
		iframeDoc.close();

		// 动态高度:多页 HTML 完整显示(与打印预览对齐)。字体/图片加载后高度会变,多次重算。
		let resize = function() {
			try {
				let cdoc = iframe[0].contentDocument;
				if (!cdoc || !cdoc.body) return;
				let h = Math.max(cdoc.body.scrollHeight, cdoc.documentElement.scrollHeight, cdoc.body.offsetHeight);
				if (h > 0) iframe.css('height', (h + 16) + 'px');
			} catch (e) {}
		};
		iframe.on('load', resize);
		setTimeout(resize, 100);
		setTimeout(resize, 500);
		setTimeout(resize, 1500);
	}
});
