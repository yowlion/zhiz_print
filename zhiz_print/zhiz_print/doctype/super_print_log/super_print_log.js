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

		// 注入纸张预览效果(对齐打印预览页 sp-paper-wrapper:灰底衬托 + 居中 + 阴影 + 页间距)。
		// print_preview_html 是为打印生成的(.print-page 无阴影/不居中),预览回看需补这些视觉。
		try {
			let inj = iframeDoc.createElement('style');
			inj.textContent = 'body{background:#f0f0f0 !important;margin:0 !important;padding:20px !important;}' +
				'.print-pages-wrapper{margin:0 auto !important;}' +
				'.print-page{background:#fff !important;box-shadow:0 2px 16px rgba(0,0,0,.12) !important;margin:0 0 20px 0 !important;}';
			(iframeDoc.head || iframeDoc.documentElement).appendChild(inj);
		} catch (e) {}

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
