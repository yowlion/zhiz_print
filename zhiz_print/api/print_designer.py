# -*- coding: utf-8 -*-
# Copyright (c) 2026, Zhiz Print
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
import json
import re


@frappe.whitelist()
def get_available_designs(doctype, docname=None):
	"""获取文档类型可用的打印设计列表"""
	if not doctype:
		return []

	# 获取当前文档实例，供启用条件评估使用
	doc = None
	if docname:
		try:
			doc = frappe.get_doc(doctype, docname)
		except Exception:
			pass

	designs = frappe.get_all(
		"Super Print Design",
		filters={
			"target_doctype": doctype,
			"enabled": 1,
		},
		fields=["name", "design_name", "print_paper"],
		order_by="design_name"
	)

	result = []
	for d in designs:
		# 检查启用条件
		if not frappe.get_cached_doc("Super Print Design", d.name).check_enable_conditions(d.name, doc=doc):
			continue

		# 获取纸张信息（含边距）
		paper_info = {}
		if d.print_paper:
			paper_info = frappe.db.get_value("Super Print Paper", d.print_paper,
											 ["width", "height",
											  "margin_top", "margin_bottom",
											  "margin_left", "margin_right"], as_dict=True) or {}

		# 检查是否有参数
		has_params = frappe.db.count("Super Print Design Parameter",
									  filters={"parent": d.name, "parenttype": "Super Print Design"})

		result.append({
			"name": d.name,
			"design_name": d.design_name,
			"print_paper": d.print_paper,
			"paper_width": paper_info.get("width"),
			"paper_height": paper_info.get("height"),
			"has_parameters": has_params > 0,
			"parameters": get_design_parameters(d.name),
		})

	return result


def get_design_parameters(design_name):
	"""获取设计参数定义"""
	params = frappe.get_all(
		"Super Print Design Parameter",
		filters={"parent": design_name, "parenttype": "Super Print Design"},
		fields=["param_name", "param_label", "param_type", "default_value", "reqd", "options"],
		order_by="idx"
	)
	return params


@frappe.whitelist()
def render_print_preview(doctype, docname, design_name, params=None):
	"""渲染打印预览HTML"""
	if isinstance(params, str):
		try:
			params = json.loads(params)
		except (json.JSONDecodeError, TypeError):
			params = {}

	design = frappe.get_doc("Super Print Design", design_name)

	# 权限检查
	if not frappe.has_permission(doctype, "print", docname):
		frappe.throw(_("没有打印权限"), frappe.PermissionError)

	# 获取纸张信息（含边距）
	paper = frappe.get_doc("Super Print Paper", design.print_paper)

	# 渲染HTML
	html = design.get_preview_for_document(doc_name=docname, params=params)

	return {
		"html": html,
		"paper_width": paper.width,
		"paper_height": paper.height,
		"margin_top": paper.margin_top or 0,
		"margin_bottom": paper.margin_bottom or 0,
		"margin_left": paper.margin_left or 0,
		"margin_right": paper.margin_right or 0,
	}


@frappe.whitelist()
def record_print_log(doctype, docname, design_name, params=None, preview_html=None, export_type=None):
	"""记录打印日志"""
	if isinstance(params, str):
		try:
			params = json.loads(params)
		except (json.JSONDecodeError, TypeError):
			params = {}

	log = frappe.get_doc({
		"doctype": "Super Print Log",
		"reference_doctype": doctype,
		"reference_name": docname,
		"print_design": design_name,
		"parameters_used": json.dumps(params, ensure_ascii=False) if params else "{}",
		"print_preview_html": preview_html or "",
		"export_type": export_type or "打印",
	})
	log.insert(ignore_permissions=True)

	return {
		"log_name": log.name,
		"print_count": log.print_count,
	}


def _fix_merged_cell_borders_for_pdf(html):
	"""PDF 专用：修复合并单元格的幽灵边线。
	1. transparent 边框 → 移除该属性，让 CSS border:none 生效
	2. 合并单元格黑边框 → 加 background:white 覆盖内部幽灵列线"""
	from bs4 import BeautifulSoup
	soup = BeautifulSoup(html, 'html.parser')
	for td in soup.find_all('td'):
		style = td.get('style', '')
		if not style:
			continue
		# Fix 1: transparent borders → 移除 border 属性
		if 'solid transparent' in style:
			style = re.sub(r'\s*border-([a-z]+):\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
			style = re.sub(r'\s*border:\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
			style = re.sub(r';\s*;', ';', style).strip(';').strip()
		# Fix 2: merged cells with black borders get background:white
		colspan = int(td.get('colspan', 1))
		rowspan = int(td.get('rowspan', 1))
		if (colspan > 1 or rowspan > 1) and 'solid black' in style:
			if 'background' not in style:
				style = style.rstrip(';') + ';background:white'
		td['style'] = style
	# 同时修复内部 div 的 transparent 边框
	for div in soup.find_all('div'):
		style = div.get('style', '')
		if not style:
			continue
		if 'solid transparent' in style:
			style = re.sub(r'\s*border-([a-z]+):\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
			style = re.sub(r'\s*border:\s*[\d.]+\s*px\s+solid\s+transparent;?', '', style)
			style = re.sub(r';\s*;', ';', style).strip(';').strip()
			div['style'] = style
	return str(soup)


def _render_print_html(doctype, docname, design_name, params=None, skip_px_scaling=False):
	"""公共函数：渲染打印 HTML 并执行 px 缩放，供各 PDF 引擎共用。
	返回 (html, design) 元组。"""
	import os

	if isinstance(params, str):
		try:
			params = json.loads(params)
		except (json.JSONDecodeError, TypeError):
			params = {}

	design = frappe.get_doc("Super Print Design", design_name)

	if not frappe.has_permission(doctype, "print", docname):
		frappe.throw(_("没有打印权限"), frappe.PermissionError)

	html = design.get_preview_for_document(doc_name=docname, params=params)

	if not skip_px_scaling:
		# WeasyPrint px→mm 转换率: 25.4/96 ≈ 0.264583 mm/px
		# 预览用 PX_PER_MM=4（即 1px = 0.25mm），与 WeasyPrint 不同
		# 解决方案：将 HTML 中所有 px 值乘以 (96/25.4)/4 ≈ 0.9449，
		# 使内容在 WeasyPrint 中恰好渲染为正确的 mm 尺寸，无需 transform/clip。
		if design.print_paper:
			wp_scale = (96 / 25.4) / 4  # ≈ 0.9449

			def _scale_px_value(match):
				value = float(match.group(1))
				scaled = round(value * wp_scale, 2)
				if scaled == int(scaled):
					return f'{int(scaled)}px'
				return f'{scaled}px'

			def _scale_style_content(text):
				urls = []
				def _save_url(m):
					urls.append(m.group(0))
					return f'__URL_{len(urls)-1}__'
				text = re.sub(r"url\([^)]*\)", _save_url, text)
				text = re.sub(r'([\d.]+)\s*px', _scale_px_value, text)
				for i, url in enumerate(urls):
					text = text.replace(f'__URL_{i}__', url)
				return text

			def _scale_style_attr(m):
				return m.group(1) + _scale_style_content(m.group(2)) + m.group(3)
			html = re.sub(r'(style=")([^"]*)(")', _scale_style_attr, html)

			def _scale_style_tag(m):
				return m.group(1) + _scale_style_content(m.group(2)) + m.group(3)
			html = re.sub(r'(<style[^>]*>)(.*?)(</style>)', _scale_style_tag, html, flags=re.DOTALL)

	return html, design


def _pdf_response(pdf_bytes, filename):
	"""构造 PDF 内联预览响应"""
	frappe.local.response.filename = filename
	frappe.local.response.filecontent = pdf_bytes
	frappe.local.response.type = "pdf"


@frappe.whitelist()
def generate_print_pdf(doctype, docname, design_name, params=None):
	"""使用 WeasyPrint 生成 PDF，浏览器直接预览（inline）"""
	from weasyprint import HTML as WeasyHTML

	html, design = _render_print_html(doctype, docname, design_name, params)

	# PDF 专用：修复合并单元格幽灵边线
	html = _fix_merged_cell_borders_for_pdf(html)

	# DEBUG: 保存最终 HTML
	import os
	debug_dir = os.path.join(frappe.get_site_path(), 'public', 'files', 'pdf_debug')
	os.makedirs(debug_dir, exist_ok=True)
	with open(os.path.join(debug_dir, '03_weasyprint_final.html'), 'w', encoding='utf-8') as f:
		f.write(html)

	try:
		pdf_bytes = WeasyHTML(string=html).write_pdf()
		_pdf_response(pdf_bytes, f"{docname}-{design.design_name}-WeasyPrint.pdf")
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'WeasyPrint PDF生成失败')
		frappe.throw(_("PDF 生成失败: {0}").format(str(e)))


def _prepare_html_for_wkhtmltopdf(html):
	"""为 wkhtmltopdf 预处理 HTML：
	1. SVG data URL -> PNG data URL
	2. background 简写 /size -> 拆分 background-size
	3. footer flex -> table
	4. border < 1px -> 1px"""
	import base64
	import cairosvg

	# 1. SVG -> PNG
	def _replace_svg(m):
		b64_data = m.group(1)
		try:
			svg_bytes = base64.b64decode(b64_data)
			png_bytes = cairosvg.svg2png(bytestring=svg_bytes, scale=2)
			png_b64 = base64.b64encode(png_bytes).decode('ascii')
			return "url('data:image/png;base64," + png_b64 + "')"
		except Exception:
			return m.group(0)

	html = re.sub(
		r"url\(['\"]?data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)['\"]?\)",
		_replace_svg, html
	)

	# 2. Fix background shorthand with /size
	def _fix_bg_shorthand(m):
		full_style = m.group(1)
		bg_match = re.search(
			r'background:\s*url\([^)]+\)\s+no-repeat\s+([\w\s]+?)/(contain|cover|[\d.]+(?:px|mm|%)?)',
			full_style
		)
		if bg_match:
			position = bg_match.group(1).strip()
			size = bg_match.group(2).strip()
			old_bg = bg_match.group(0)
			fixed = old_bg.replace(f"/{size}", "").rstrip()
			full_style = full_style.replace(old_bg, fixed)
			full_style = full_style.rstrip(";") + f";background-size:{size}"
		return 'style="' + full_style + '"'

	html = re.sub(r'style="([^"]*background:[^"]*)"', _fix_bg_shorthand, html)

	# 3. Fix footer: flex -> absolute positioned (wkhtmltopdf compatible)
	def _fix_footer(m):
		outer_style = m.group(1)
		inner_html = m.group(2)
		h_match = re.search(r'height:([\d.]+)px', outer_style)
		h_val = h_match.group(1) if h_match else '19'
		# Remove flex, keep position:absolute on outer
		outer_style = outer_style.replace('display:flex;align-items:center;', '')
		# Replace inner flex divs with absolute positioned divs
		def _fix_inner(dm):
			align = dm.group(1)
			pad_side = dm.group(2)
			pad_val = dm.group(3)
			if align == 'left':
				pos = 'position:absolute;left:0;top:0;bottom:0;'
			elif align == 'right':
				pos = 'position:absolute;right:0;top:0;bottom:0;'
			else:
				pos = 'position:absolute;left:0;right:0;top:0;bottom:0;'
			return '<div style="' + pos + 'text-align:' + align + ';padding-' + pad_side + ':' + pad_val + 'px;line-height:' + h_val + 'px;">'
		inner_html = re.sub(
			r'<div style="flex:1;text-align:(left|center|right);padding-(left|right):([\d.]+)px;">',
			_fix_inner, inner_html
		)
		return '<div class="print-page-footer" style="' + outer_style + '">' + inner_html + '</div>'

	html = re.sub(
		r'<div class="print-page-footer" style="([^"]*)">((?:<div[^>]*>[^<]*</div>\s*)+)</div>',
		_fix_footer, html, flags=re.DOTALL
	)

	# 4. Border values < 1px rounded up to 1px
	def _round_border_px(m):
		prefix = m.group(1)
		val = float(m.group(2))
		suffix = m.group(3)
		if 0 < val < 1:
			return prefix + '1' + suffix
		return m.group(0)
	html = re.sub(r'(border-[a-z]+:\s*)([\d.]+)(px)', _round_border_px, html)
	html = re.sub(r'(border:\s*)([\d.]+)(px)', _round_border_px, html)

	# 5. Ceil rounding for font-size, width and height
	import math
	def _ceil_prop_px(m):
		val = float(m.group(2))
		rounded = math.ceil(val)
		if rounded != val:
			return m.group(1) + str(rounded) + m.group(3)
		return m.group(0)
	html = re.sub(r'(font-size:\s*)([\d.]+)(px)', _ceil_prop_px, html)
	html = re.sub(r'(line-height:\s*)([\d.]+)(px)', _ceil_prop_px, html)
	html = re.sub(r'(width:\s*)([\d.]+)(px)', _ceil_prop_px, html)
	html = re.sub(r'(height:\s*)([\d.]+)(px)', _ceil_prop_px, html)


	return html


@frappe.whitelist()
def generate_print_pdf_wkhtmltopdf(doctype, docname, design_name, params=None):
	"""使用 wkhtmltopdf 生成 PDF，浏览器直接预览（inline）"""
	import pdfkit

	html, design = _render_print_html(doctype, docname, design_name, params)

	# wkhtmltopdf 预处理：SVG→PNG、background 简写修复、flex→table
	html = _prepare_html_for_wkhtmltopdf(html)

	options = {
		"quiet": "",
		"encoding": "UTF-8",
		"print-media-type": "",
		"background": "",
		"images": "",
		"disable-smart-shrinking": "",
		"margin-top": "0",
		"margin-bottom": "0",
		"margin-left": "0",
		"margin-right": "0",
	}
	if design.print_paper:
		paper = frappe.get_doc("Super Print Paper", design.print_paper)
		options["page-width"] = f"{paper.width}mm"
		options["page-height"] = f"{paper.height}mm"

	try:
		pdf_bytes = pdfkit.from_string(html, False, options=options)
		_pdf_response(pdf_bytes, f"{docname}-{design.design_name}-wkhtmltopdf.pdf")
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'wkhtmltopdf PDF生成失败')
		frappe.throw(_("PDF 生成失败: {0}").format(str(e)))


@frappe.whitelist()
def generate_print_pdf_chromium(doctype, docname, design_name, params=None):
	"""使用 Chromium headless 生成 PDF，浏览器直接预览（inline）"""
	import os
	import subprocess
	import tempfile

	html, design = _render_print_html(doctype, docname, design_name, params)

	# 查找可用的 Chromium 可执行文件
	chromium_cmd = None
	for cmd in ['/snap/bin/chromium', 'chromium-browser', 'chromium', 'google-chrome', 'google-chrome-stable']:
		try:
			result = subprocess.run(['which', cmd], capture_output=True, text=True, timeout=5)
			if result.returncode == 0 and result.stdout.strip():
				chromium_cmd = result.stdout.strip()
				break
		except Exception:
			continue

	if not chromium_cmd:
		frappe.throw(_("Chromium 未安装，请先在服务器上执行: apt-get install -y chromium-browser"))

	# Use site files dir (snap Chromium cannot write to /tmp)
	tmpdir = os.path.abspath(os.path.join(frappe.get_site_path(), 'public', 'files', 'pdf_debug'))
	os.makedirs(tmpdir, exist_ok=True)
	html_path = os.path.join(tmpdir, 'chrome_input.html')
	pdf_path = os.path.join(tmpdir, 'chrome_output.pdf')

	with open(html_path, 'w', encoding='utf-8') as f:
		f.write(html)

	try:
		# Clean up old PDF
		if os.path.exists(pdf_path):
			os.remove(pdf_path)

		# Build Chromium command
		chrome_args = [
			chromium_cmd,
			'--headless=new',
			'--disable-gpu',
			'--no-sandbox',
			'--print-to-pdf=' + pdf_path,
			'--no-pdf-header-footer',
		]
		if design.print_paper:
			paper = frappe.get_doc('Super Print Paper', design.print_paper)
			chrome_args.append('--print-to-pdf-options=' + json.dumps({
				'paperWidth': round(paper.width / 25.4, 4),
				'paperHeight': round(paper.height / 25.4, 4),
				'marginTop': 0, 'marginBottom': 0, 'marginLeft': 0, 'marginRight': 0,
			}))
		chrome_args.append('file://' + html_path)
		result = subprocess.run(chrome_args, capture_output=True, text=True, timeout=30)

		if not os.path.exists(pdf_path):
			frappe.throw(_("Chromium 生成 PDF 失败"))

		with open(pdf_path, 'rb') as f:
			pdf_bytes = f.read()

		_pdf_response(pdf_bytes, f"{docname}-{design.design_name}-Chromium.pdf")
	except subprocess.TimeoutExpired:
		frappe.throw(_("Chromium 生成 PDF 超时"))
	except frappe.exceptions.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), 'Chromium PDF生成失败')
		frappe.throw(_("PDF 生成失败: {0}").format(str(e)))


@frappe.whitelist()
def get_print_log_count(doctype, docname, design_name=None):
	"""获取打印次数"""
	filters = {
		"reference_doctype": doctype,
		"reference_name": docname,
	}
	if design_name:
		filters["print_design"] = design_name

	return frappe.db.count("Super Print Log", filters=filters)


@frappe.whitelist()
def get_print_log_list(doctype, docname):
	"""获取打印日志列表"""
	filters = {"reference_doctype": doctype, "reference_name": docname}
	total_count = frappe.db.count("Super Print Log", filters=filters)
	logs = frappe.get_all("Super Print Log",
		filters=filters,
		fields=["name", "print_design", "print_time", "print_user", "print_count", "export_type"],
		order_by="print_time desc",
		limit=50
	)
	# 注入用户全名
	for log in logs:
		if log.print_user:
			log.user_fullname = frappe.db.get_value("User", log.print_user, "full_name") or log.print_user
		else:
			log.user_fullname = ''

	return {"logs": logs, "total_count": total_count}


# ==================== Excel导出辅助函数 ====================

def _parse_inline_style(style_str):
	"""解析CSS内联样式为字典，正确处理url()中的分号"""
	styles = {}
	if not style_str:
		return styles
	import re
	# 保护 url(...) 中的内容，避免被 ; 分割
	_url_holder = []
	def _save(m):
		_url_holder.append(m.group(0))
		return '__URL_%d__' % (len(_url_holder) - 1)
	protected = re.sub(r'url\([^)]*\)', _save, style_str)
	for item in protected.split(';'):
		item = item.strip()
		if ':' in item:
			key, value = item.split(':', 1)
			value = value.strip()
			for i, url in enumerate(_url_holder):
				value = value.replace('__URL_%d__' % i, url)
			styles[key.strip().lower()] = value
	return styles


def _extract_css_rules(soup):
	"""从<style>标签提取CSS规则"""
	try:
		import cssutils
		import logging
		cssutils.log.setLevel(logging.CRITICAL)
	except ImportError:
		return []

	rules = []
	for style_tag in soup.find_all('style'):
		css_text = style_tag.string or ''
		if not css_text.strip():
			continue
		try:
			sheet = cssutils.parseString(css_text)
			for rule in sheet:
				if rule.type == rule.STYLE_RULE:
					prop_dict = {}
					for prop in rule.style:
						prop_dict[prop.name.lower()] = prop.value
					rules.append({
						'selector': rule.selectorText,
						'styles': prop_dict
					})
		except Exception:
			pass
	return rules


def _element_matches_selector(element, selector):
	"""检查元素是否匹配CSS选择器（简化版，支持后代/类/标签/ID选择器）"""
	parts = selector.strip().split()
	target = parts[-1] if parts else selector

	tag = element.name
	elem_classes = element.get('class', []) or []
	elem_id = element.get('id', '') or ''

	if not target or target == '*':
		return True

	# .class1.class2
	if target.startswith('.') and '#' not in target:
		classes = [c for c in target.split('.') if c]
		return all(c in elem_classes for c in classes)

	# tag.class
	if '.' in target and not target.startswith('.') and '#' not in target:
		t, cls = target.split('.', 1)
		if t and tag != t:
			return False
		return cls in elem_classes

	# #id
	if target.startswith('#'):
		return elem_id == target[1:]

	# tag
	return target == tag


def _get_element_styles(element, css_rules):
	"""获取元素的合并样式（CSS规则 + 内联样式），内联优先"""
	merged = {}
	for rule in css_rules:
		if _element_matches_selector(element, rule['selector']):
			merged.update(rule['styles'])
	inline = _parse_inline_style(element.get('style', ''))
	merged.update(inline)
	return merged


def _css_color_to_hex(color_str):
	"""将CSS颜色值转换为hex（不含#）"""
	import re
	if not color_str or color_str.strip().lower() in ('transparent', 'none', 'inherit', 'initial'):
		return None
	color_str = color_str.strip()

	if color_str.startswith('#'):
		h = color_str[1:]
		if len(h) == 3:
			h = ''.join(c * 2 for c in h)
		return h.upper()

	m = re.match(r'rgb[a]?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', color_str)
	if m:
		return '{:02X}{:02X}{:02X}'.format(int(m.group(1)), int(m.group(2)), int(m.group(3)))

	named = {
		'black': '000000', 'white': 'FFFFFF', 'red': 'FF0000',
		'green': '008000', 'blue': '0000FF', 'yellow': 'FFFF00',
		'gray': '808080', 'grey': '808080', 'silver': 'C0C0C0',
		'orange': 'FFA500', 'purple': '800080',
	}
	return named.get(color_str.lower())


def _css_px_value(value_str):
	"""从CSS值中提取数字"""
	import re
	if not value_str:
		return None
	m = re.match(r'([\d.]+)', value_str.strip())
	return float(m.group(1)) if m else None


def _css_width_to_excel(width_str):
	"""CSS宽度(px) → Excel列宽(字符单位)，设计器px/4=mm"""
	val = _css_px_value(width_str)
	if val is None:
		return None
	mm = val / 4.0  # 设计器 PX_PER_MM=4，px/4得到mm
	# openpyxl column width 以字符宽度为单位
	# 直接使用mm值，因为 openpyxl 1个字符宽≈1.85mm
	# mm值 / 1.85 转换为字符宽度
	width = mm / 1.85
	return max(round(width, 2), 2)


def _css_height_to_excel(height_str):
	"""CSS高度(px) → Excel行高(pt)，设计器px/4=mm"""
	val = _css_px_value(height_str)
	if val is None:
		return None
	mm = val / 4.0  # 设计器 PX_PER_MM=4，px/4得到mm
	return mm * 2.8346  # mm → pt (72/25.4)


def _css_font_size_to_pt(size_str):
	"""CSS font-size → pt"""
	import re
	if not size_str:
		return None
	m = re.match(r'([\d.]+)\s*(px|pt|em|rem)?', size_str.strip())
	if not m:
		return None
	val = float(m.group(1))
	unit = (m.group(2) or 'px').lower()
	if unit == 'pt':
		return val
	if unit in ('em', 'rem'):
		return val * 12
	return val * 0.75


def _parse_border_side(border_str):
	"""CSS border → openpyxl Side"""
	from openpyxl.styles import Side
	import re

	if not border_str or border_str.strip().lower() in ('none', '0', '0px', 'hidden'):
		return Side(style=None)

	s = border_str.strip().lower()

	m = re.search(r'([\d.]+)\s*px', s)
	width = float(m.group(1)) if m else 1

	style = 'thin'
	if 'double' in s:
		style = 'double'
	elif 'dotted' in s:
		style = 'dotted'
	elif 'dashed' in s:
		style = 'dashed'
	elif width >= 3:
		style = 'thick'
	elif width >= 2:
		style = 'medium'

	color = '000000'
	for part in s.split():
		c = _css_color_to_hex(part)
		if c:
			color = c
			break

	return Side(style=style, color=color)


def _parse_cell_value(text):
	"""尝试将文本解析为数字"""
	import re
	if not text:
		return ''
	clean = text.strip().replace(',', '')
	if re.match(r'^-?[\d]+(\.\d+)?$', clean):
		try:
			return float(clean) if '.' in clean else int(clean)
		except ValueError:
			return text
	return text


def _apply_cell_format(cell, styles):
	"""将CSS样式应用到openpyxl单元格"""
	from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

	if not styles:
		return

	# 字体
	font_kw = {}
	if 'font-size' in styles:
		s = _css_font_size_to_pt(styles['font-size'])
		if s:
			font_kw['size'] = s
	if 'font-weight' in styles:
		w = str(styles['font-weight']).strip()
		if w in ('bold', 'bolder', '700', '800', '900'):
			font_kw['bold'] = True
	if 'font-family' in styles:
		font_kw['name'] = styles['font-family'].strip('"\'').split(',')[0].strip()
	if 'color' in styles:
		c = _css_color_to_hex(styles['color'])
		if c:
			font_kw['color'] = c
	if font_kw:
		cell.font = Font(**font_kw)

	# 对齐
	align_kw = {}
	if 'text-align' in styles:
		align_kw['horizontal'] = styles['text-align']
	if 'vertical-align' in styles:
		v = styles['vertical-align']
		align_kw['vertical'] = 'center' if v == 'middle' else v
	if styles.get('white-space') in ('pre-wrap', 'pre', 'normal'):
		align_kw['wrap_text'] = True
	elif styles.get('white-space') == 'nowrap':
		align_kw['wrap_text'] = False
	if align_kw:
		cell.alignment = Alignment(**align_kw)

	# 背景色
	if 'background-color' in styles:
		c = _css_color_to_hex(styles['background-color'])
		if c:
			cell.fill = PatternFill(start_color=c, end_color=c, fill_type='solid')

	# 边框 - 先应用简写border，再用各边border-X覆盖
	border_kw = {}
	if 'border' in styles:
		s = _parse_border_side(styles['border'])
		border_kw = {'top': s, 'right': s, 'bottom': s, 'left': s}
	for side_name in ('top', 'right', 'bottom', 'left'):
		prop = f'border-{side_name}'
		if prop in styles:
			border_kw[side_name] = _parse_border_side(styles[prop])
	if border_kw:
		cell.border = Border(**border_kw)


def _extract_background_image(styles):
	"""从CSS样式中提取背景图片数据（base64或URL）"""
	import re
	import base64

	bg = styles.get('background', '') or styles.get('background-image', '')
	if not bg:
		return None

	m = re.search(r"url\(['\"]?(.*?)['\"]?\)", bg)
	if not m:
		return None

	url = m.group(1).strip()

	if url.startswith('data:'):
		m2 = re.match(r'data:(image/[\w+]+);base64,(.*)', url, re.DOTALL)
		if m2:
			img_type = m2.group(1)
			try:
				img_bytes = base64.b64decode(m2.group(2))
				return {'type': img_type, 'data': img_bytes}
			except Exception:
				return None
	else:
		# 普通 URL 图片
		try:
			if url.startswith('/'):
				import os
				site_path = frappe.get_site_path()
				file_path = os.path.join(site_path, url.lstrip('/'))
				if os.path.exists(file_path):
					with open(file_path, 'rb') as f:
						img_bytes = f.read()
					ext = os.path.splitext(url)[1].lower()
					type_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.bmp': 'image/bmp'}
					return {'type': type_map.get(ext, 'image/png'), 'data': img_bytes}
			else:
				import requests
				resp = requests.get(url, timeout=10)
				if resp.status_code == 200:
					ct = resp.headers.get('Content-Type', 'image/png')
					return {'type': ct, 'data': resp.content}
		except Exception:
			pass

	return None


def _svg_to_png_bytes(svg_bytes):
	"""将SVG转换为PNG字节"""
	try:
		import cairosvg
		return cairosvg.svg2png(bytestring=svg_bytes)
	except Exception:
		return None


def _add_image_to_excel_cell(ws, row, col, rowspan, colspan, img_info, align='left'):
	"""添加图片到Excel单元格，自动适应合并区域尺寸"""
	from openpyxl.drawing.image import Image as XlImage
	from openpyxl.utils import get_column_letter
	from io import BytesIO

	img_data = img_info['data']
	img_type = img_info.get('type', '')

	# SVG → PNG
	if 'svg' in img_type:
		png_data = _svg_to_png_bytes(img_data)
		if png_data:
			img_data = png_data
		else:
			return  # 无法转换则跳过

	try:
		img_stream = BytesIO(img_data)
		xl_img = XlImage(img_stream)
	except Exception:
		return

	# 计算合并区域的总高度(pt → mm → px@96DPI)
	total_h_pt = 0
	for r in range(row, row + rowspan):
		h = ws.row_dimensions[r].height
		if h:
			total_h_pt += h
	total_h_mm = total_h_pt / 2.8346 if total_h_pt else 10
	total_h_px = total_h_mm * 96.0 / 25.4

	# 计算合并区域的总宽度(chars → mm → px@96DPI)
	mm_per_char = 7.0 * 25.4 / 96.0
	total_w_chars = 0
	for c in range(col, col + colspan):
		letter = get_column_letter(c)
		w = ws.column_dimensions[letter].width
		if w:
			total_w_chars += w
	total_w_mm = total_w_chars * mm_per_char if total_w_chars else 30
	total_w_px = total_w_mm * 96.0 / 25.4

	# 保持长宽比缩放到单元格区域内
	orig_w = xl_img.width or 1
	orig_h = xl_img.height or 1
	cell_w = max(total_w_px, 10)
	cell_h = max(total_h_px, 10)
	scale = min(cell_w / orig_w, cell_h / orig_h)
	img_w = int(orig_w * scale)
	img_h = int(orig_h * scale)
	xl_img.width = img_w
	xl_img.height = img_h

	# 对齐处理：根据 align 调整锚定列位置
	if align == 'right' and colspan > 1:
		anchor_col = col + colspan - 1
	elif align == 'center' and colspan > 1:
		anchor_col = col + (colspan - 1) // 2
	else:
		anchor_col = col
	cell_ref = '%s%d' % (get_column_letter(anchor_col), row)
	ws.add_image(xl_img, cell_ref)


def _set_column_widths(ws, table, css_rules):
	"""从colgroup或首行单元格的CSS width设置Excel列宽"""
	from openpyxl.utils import get_column_letter

	col_widths = {}

	# 优先从colgroup获取
	colgroup = table.find('colgroup')
	if colgroup:
		for idx, col in enumerate(colgroup.find_all('col'), 1):
			styles = _get_element_styles(col, css_rules)
			if 'width' in styles:
				w = _css_width_to_excel(styles['width'])
				if w:
					col_widths[idx] = w

	# 其次从首行单元格获取
	if not col_widths:
		first_tr = table.find('tr')
		if first_tr:
			col_idx = 1
			for cell_elem in first_tr.find_all(['td', 'th']):
				styles = _get_element_styles(cell_elem, css_rules)
				if 'width' in styles:
					w = _css_width_to_excel(styles['width'])
					if w:
						col_widths[col_idx] = w
				col_idx += int(cell_elem.get('colspan', 1))

	for idx, w in col_widths.items():
		letter = get_column_letter(idx)
		ws.column_dimensions[letter].width = w


def _write_table_to_excel(ws, table, start_row, css_rules, skip_rows=0):
	"""将HTML表格写入Excel，解析CSS样式，返回写入的行数"""
	occupied = {}
	_set_column_widths(ws, table, css_rules)

	images_to_add = []  # 收集图片信息，最后统一添加（需要行高列宽已设置）

	trs = table.find_all('tr')
	excel_row = start_row

	for tr_idx, tr in enumerate(trs):
		if tr_idx < skip_rows:
			continue

		col = 1
		for cell_elem in tr.find_all(['td', 'th']):
			while occupied.get((excel_row, col)):
				col += 1

			colspan = int(cell_elem.get('colspan', 1))
			rowspan = int(cell_elem.get('rowspan', 1))

			# 获取文本
			text = cell_elem.get_text(strip=True)
			if not text and cell_elem.find('img'):
				text = cell_elem.find('img').get('alt', '')

			# 获取样式
			cell_styles = _get_element_styles(cell_elem, css_rules)
			if cell_elem.name == 'th' and 'font-weight' not in cell_styles:
				cell_styles['font-weight'] = 'bold'

			# 检查背景图片（二维码/条形码/图片单元格）
			bg_img = _extract_background_image(cell_styles)
			if bg_img:
				text = ''  # 图片单元格不输出文本
				images_to_add.append({
					'row': excel_row, 'col': col,
					'rowspan': rowspan, 'colspan': colspan,
					'img_info': bg_img,
					'align': cell_styles.get('text-align', 'left'),
				})

			# 写入值和样式
			value = _parse_cell_value(text)
			cell = ws.cell(row=excel_row, column=col, value=value)
			_apply_cell_format(cell, cell_styles)

			# 合并单元格
			if colspan > 1 or rowspan > 1:
				ws.merge_cells(
					start_row=excel_row, start_column=col,
					end_row=excel_row + rowspan - 1,
					end_column=col + colspan - 1
				)
				for r in range(excel_row, excel_row + rowspan):
					for c in range(col, col + colspan):
						if (r, c) != (excel_row, col):
							_apply_cell_format(ws.cell(row=r, column=c), cell_styles)

			# 标记被占用的单元格
			for r in range(excel_row, excel_row + rowspan):
				for c in range(col, col + colspan):
					if r != excel_row or c != col:
						occupied[(r, c)] = True

			col += colspan

		# 行高
		row_styles = _get_element_styles(tr, css_rules)
		if 'height' in row_styles:
			h = _css_height_to_excel(row_styles['height'])
			if h:
				ws.row_dimensions[excel_row].height = h

		excel_row += 1

	# 第二遍：添加图片（此时行高列宽已全部设置完毕）
	for item in images_to_add:
		_add_image_to_excel_cell(
			ws, item['row'], item['col'],
			item['rowspan'], item['colspan'],
			item['img_info'],
			item.get('align', 'left')
		)

	return excel_row - start_row


# ==================== Excel导出主函数 ====================

@frappe.whitelist()
def export_print_excel(doctype, docname, design_name=None, params=None):
	"""导出打印设计数据为 Excel，解析CSS样式，完整复现打印预览内容"""
	import base64
	from io import BytesIO
	from frappe.utils.xlsxutils import make_xlsx
	from bs4 import BeautifulSoup
	import openpyxl

	if isinstance(params, str):
		try:
			params = json.loads(params)
		except (json.JSONDecodeError, TypeError):
			params = {}

	if not design_name:
		# 无设计名称时回退到简单字段导出
		doc = frappe.get_doc(doctype, docname)
		rows = [["字段标签", "字段名", "字段值"]]
		meta = frappe.get_meta(doctype)
		for df in meta.fields:
			if df.fieldtype in ('Section Break', 'Column Break', 'HTML', 'Button', 'Fold'):
				continue
			value = doc.get(df.fieldname)
			rows.append([df.label or df.fieldname, df.fieldname, str(value) if value else ''])
		xlsx_data = make_xlsx(rows, doctype)
		xlsx_base64 = base64.b64encode(xlsx_data.getvalue()).decode('utf-8')
		return {"xlsx_base64": xlsx_base64, "filename": f"{docname}.xlsx"}

	design = frappe.get_doc("Super Print Design", design_name)

	if not frappe.has_permission(doctype, "print", docname):
		frappe.throw(_("没有打印权限"), frappe.PermissionError)

	# 渲染 HTML（与打印预览一致）
	html = design.get_preview_for_document(doc_name=docname, params=params)

	# 解析 HTML
	soup = BeautifulSoup(html, 'html.parser')
	css_rules = _extract_css_rules(soup)

	wb = openpyxl.Workbook()
	ws = wb.active
	ws.title = "Sheet1"

	# 收集所有页面的表格
	pages = []
	for page_div in soup.find_all('div', class_='print-page'):
		content_div = page_div.find('div', class_='print-page-content')
		if not content_div:
			continue
		table = content_div.find('table', class_='print-form-table')
		if table:
			pages.append(table)

	if not pages:
		ws.cell(row=1, column=1, value="无数据")
	else:
		# 跨页去重：比较第一页和第二页的前N行文本，确定重复标题行数
		header_count = 0
		if len(pages) > 1:
			rows_1 = [tr.get_text(strip=True) for tr in pages[0].find_all('tr')]
			rows_2 = [tr.get_text(strip=True) for tr in pages[1].find_all('tr')]
			for i in range(min(len(rows_1), len(rows_2))):
				if rows_1[i] == rows_2[i]:
					header_count = i + 1
				else:
					break

		# 逐页写入Excel
		current_row = 1
		for page_idx, table in enumerate(pages):
			skip = header_count if page_idx > 0 else 0
			rows_written = _write_table_to_excel(ws, table, current_row, css_rules, skip_rows=skip)
			current_row += rows_written

	# 生成Excel文件
	output = BytesIO()
	wb.save(output)
	output.seek(0)

	xlsx_base64 = base64.b64encode(output.read()).decode('utf-8')

	return {
		"xlsx_base64": xlsx_base64,
		"filename": f"{docname}-{design.design_name}.xlsx"
	}
