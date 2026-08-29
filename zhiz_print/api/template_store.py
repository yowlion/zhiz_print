# -*- coding: utf-8 -*-
"""智兆模板平台 — 客户端(推送/安装)。

share_template: 序列化当前设计 + 渲染预览 + 推送到 zhiz_licser template_api。
install_template: 从 zhiz_licser 拉模板 + 本地重建(纸张/设计重名处理 + doctype 检查)。
"""
from __future__ import unicode_literals
import json
import re
import frappe
from zhiz_print.api.license import _call_license_api, get_machine_id

# 脱敏:模板平台公开(装 app 都能看),返回客户端前把公司名替换为固定占位,保密真实客户。
# 注意:脱敏只在 get_template/list_templates 返回客户端模板平台时做;share_template 推送原版,
# 中心服务器存储与服务端预览保留真实数据(原版)。
SENSITIVE_COMPANY = "广德智兆科技有限公司"
_COMPANY_RE = re.compile(r'[一-龥A-Za-z]{2,15}(?:有限公司|科技有限公司|有限责任公司|股份有限公司|集团有限公司|公司)(?![一-龥A-Za-z])')


def _desensitize_preview(html):
    if not html:
        return html
    return _COMPANY_RE.sub(SENSITIVE_COMPANY, html)


# ==================== URL/二维码脱敏(返回客户端前;中心存原版) ====================
# 二维码里的真实客户 URL(如 http://mes.gdscnj.com:8888/app/.../{doc.name})脱敏:
# 域名(含端口) -> www.xxx.com,保留路径和 {doc.name}。preview 里已渲染成 base64 图片的
# 二维码,文本替换改不动 —— 用模块矩阵比对定位:解码 PNG 到黑白点阵,与候选值的矩阵比,
# 差异<1% 即同一码,再替换为脱敏值的新二维码。矩阵法绕过 Pillow 版本差异(不同推送方
# Pillow 编出的 PNG 字节不同,但模块布局由 qrcode 库按数据确定性生成,与编码器无关)。
# base64 字母表不含 ':' 和 '.',故 http:// 与 www. 不可能出现在 data URI 内,文本替换安全。
_URL_SCHEME_HOST_RE = re.compile(r'https?://[a-zA-Z0-9.\-]+(?::\d+)?')
_WWW_HOST_RE = re.compile(r'www\.[a-zA-Z0-9.\-]+(?::\d+)?')
_DOC_NAME_RE = re.compile(r'\{doc\.name\}')


def _scrub_urls(text):
    """URL 域名(含端口) -> www.xxx.com,保留路径和 {doc.name}。"""
    if not text:
        return text
    text = _URL_SCHEME_HOST_RE.sub('http://www.xxx.com', text)
    text = _WWW_HOST_RE.sub('www.xxx.com', text)
    return text


def _has_url(text):
    return bool(_URL_SCHEME_HOST_RE.search(text) or _WWW_HOST_RE.search(text))


def _iter_cells(node):
    """递归产出所有含 cell_type 的单元格 dict(design_items 及任意嵌套结构)。"""
    if isinstance(node, dict):
        if "cell_type" in node:
            yield node
        for v in node.values():
            yield from _iter_cells(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_cells(v)


def _regen_qr_images(html, qr_cells, sample_doc, is_design_preview):
    """重生成 preview 里已渲染的二维码图片(模块矩阵匹配,绕过 Pillow 版本差异)。

    qr_cells: [(原始 cell_value, 脱敏后 cell_value)] —— 仅 qrcode/barcode 单元格。
    用原始值复现渲染值 -> 生成二维码模块矩阵 -> 解码 HTML 里每张 PNG 到同尺寸矩阵比对
    -> 差异<1% 即同一码 -> 替换为脱敏值的新二维码。匹配不上则跳过(绝不盲改、不破坏)。
    设计预览(doc=None):{doc.name} 保持字面量;实际预览:代入 sample_doc 名。
    """
    try:
        import qrcode, io, base64
        from PIL import Image
        from zhiz_print.utils.query_executor import generate_qrcode_base64
    except Exception:
        return html  # 缺库,放弃重生成(文本 URL 仍已替换)

    imgs = []  # [(data_uri, size)] 去重
    seen = set()
    for tag in re.findall(r'<img\b[^>]*>', html or ''):
        m = re.search(r'src="(data:image/png;base64,[A-Za-z0-9+/=]+)"', tag)
        if not m:
            continue
        uri = m.group(1)
        if uri in seen:
            continue
        seen.add(uri)
        mw = re.search(r'max-width:(\d+)px', tag)
        imgs.append((uri, int(mw.group(1)) if mw else None))
    if not imgs:
        return html

    def _resolve(value):
        if is_design_preview or not sample_doc:
            return value or ''
        return _DOC_NAME_RE.sub(str(sample_doc), value or '')

    def _candidate_matrix(value):
        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        qr.add_data(str(value))
        qr.make(fit=True)
        n = len(qr.modules)
        total = n + 2  # border=1 上下各一行白边
        mat = [[0] * total for _ in range(total)]
        for r in range(n):
            for c in range(n):
                mat[r + 1][c + 1] = 1 if qr.modules[r][c] else 0
        return mat, total

    _mat_cache = {}  # (uri, total) -> matrix

    def _stored_matrix(uri, total):
        key = (uri, total)
        if key in _mat_cache:
            return _mat_cache[key]
        b64 = uri.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
        w, h = img.size
        px = img.load()
        step = w / float(total)
        mat = [[1 if px[min(w - 1, int((c + 0.5) * step)), min(h - 1, int((r + 0.5) * step))] < 128 else 0
               for c in range(total)] for r in range(total)]
        _mat_cache[key] = mat
        return mat

    repl = []  # [(old_uri, new_uri)]
    matched = 0
    for orig_val, scrubbed_val in qr_cells:
        resolved_orig = _resolve(orig_val)
        if not resolved_orig:
            continue
        try:
            cmat, total = _candidate_matrix(resolved_orig)
        except Exception:
            continue
        resolved_new = _resolve(scrubbed_val)
        for uri, size in imgs:
            try:
                smat = _stored_matrix(uri, total)
            except Exception:
                continue
            diff = sum(1 for r in range(total) for c in range(total) if cmat[r][c] != smat[r][c])
            if diff / float(total * total) <= 0.01:  # <1% 差异 = 同一二维码
                if size:
                    try:
                        new_qr = generate_qrcode_base64(resolved_new, size, size)
                    except Exception:
                        new_qr = None
                    if new_qr and new_qr != uri:
                        repl.append((uri, new_qr))
                matched += 1
                break
    for old_uri, new_uri in repl:
        html = html.replace(old_uri, new_uri)
    if qr_cells and matched < len(qr_cells):
        frappe.logger("zhiz_print").warning(
            "[模板脱敏] {0} 个二维码单元格未在 preview 定位到,已跳过".format(len(qr_cells) - matched))
    return html


def _desensitize_template_urls(tpl):
    """对返回客户端的模板数据做 URL/二维码脱敏(中心服务器存原版,此处不改存储)。"""
    if not isinstance(tpl, dict):
        return tpl

    dd_raw = tpl.get("design_data") or ""
    design = None
    if dd_raw:
        try:
            design = json.loads(dd_raw)
        except Exception:
            design = None

    qr_cells = []  # [(原始 cell_value, 脱敏后 cell_value)]
    if design is not None:
        for cell in _iter_cells(design):
            cv = cell.get("cell_value")
            if isinstance(cv, str) and _has_url(cv):
                scrubbed = _scrub_urls(cv)
                if scrubbed != cv:
                    cell["cell_value"] = scrubbed
                    if str(cell.get("cell_type", "")).lower() in ("qrcode", "barcode"):
                        qr_cells.append((cv, scrubbed))
        tpl["design_data"] = json.dumps(design, ensure_ascii=False, default=str)

    sample_doc = design.get("sample_doc") if isinstance(design, dict) else None

    for k in ("preview_html", "preview_html_design"):
        html = tpl.get(k)
        if not html:
            continue
        html = _scrub_urls(html)  # 文本 URL 替换
        if qr_cells:
            html = _regen_qr_images(html, qr_cells, sample_doc, is_design_preview=(k == "preview_html_design"))
        tpl[k] = html
    return tpl


# Frappe 内部字段(清洗时剔除)
_INTERNAL_FIELDS = ["name", "owner", "creation", "modified", "modified_by",
    "docstatus", "idx", "parent", "parentfield", "parenttype", "doctype",
    "__last_sync_on", "__unsaved", "_user_tags", "_assign", "_comments",
    "_liked_by", "lft", "rgt"]


def _my_company():
    """本机激活公司名 — 模板平台的请求方身份(可见性过滤/作者判定都按它)。"""
    lic = frappe.get_all("Zprint License", limit=1, order_by="activated_at desc",
        fields=["company_name"])
    return (lic[0]["company_name"] if lic else "") or ""


def _clean_doc(d):
    """剔除 Frappe 内部字段(递归子表)。"""
    for f in _INTERNAL_FIELDS:
        d.pop(f, None)
    for k, v in list(d.items()):
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    _clean_doc(item)
    return d


@frappe.whitelist()
def share_template(design_name):
    """序列化设计 + 渲染预览 + 推送到模板平台。"""
    if not design_name:
        frappe.throw("Design name required")

    design = frappe.get_doc("Super Print Design", design_name)
    design_data = _clean_doc(design.as_dict(no_nulls=True))

    # 实际打印预览(sample_doc 数据渲染)
    preview_html = ""
    try:
        preview_html = design.get_preview_for_document(doc_name=design.sample_doc) or ""
    except Exception:
        preview_html = ""
    # 设计渲染(None 占位符原样,纯模板结构)
    preview_html_design = ""
    try:
        preview_html_design = design.get_preview_for_document(doc_name=None) or ""
    except Exception:
        preview_html_design = ""

    # 纸张配置
    paper_config = {}
    try:
        paper = frappe.get_doc("Super Print Paper", design.print_paper).as_dict()
        paper_config = {k: paper.get(k) for k in [
            "paper_name", "width", "height",
            "margin_top", "margin_bottom", "margin_left", "margin_right"]}
    except Exception:
        pass

    # 授权信息(author identity)
    lic = frappe.get_all("Zprint License", limit=1, order_by="activated_at desc",
        fields=["license_key", "company_name"])
    license_key = lic[0]["license_key"] if lic else ""
    company = lic[0]["company_name"] if lic else ""

    # 图片转 base64 嵌入(跨服务器自包含,解决推送后图片 URL 指向客户服务器不可达)
    import re
    from zhiz_print.utils.query_executor import image_to_base64_src
    def _embed_img(_html):
        if not _html:
            return _html
        def _r(m):
            s = m.group(1).strip()
            if not s or s.startswith('data:'):
                return m.group(0)  # 已 base64/空,保留
            b = image_to_base64_src(s)
            return 'src="' + b + '"' if (b and b != s) else m.group(0)  # 转失败保留原 src
        return re.sub(r'src="(https?://[^"]+|/[^"]+)"', _r, _html)
    preview_html = _embed_img(preview_html)
    preview_html_design = _embed_img(preview_html_design)

    # 推送原版 preview(不脱敏):中心服务器存储与服务端预览需看真实数据;
    # 脱敏改由 get_template/list_templates 返回客户端模板平台时做(_desensitize_preview)。

    import zhiz_print
    body = {
        "template_name": design.design_name,
        "target_doctype": design.target_doctype,
        "print_paper_name": design.print_paper or "",
        "print_paper_config": json.dumps(paper_config, ensure_ascii=False),
        "design_data": json.dumps(design_data, ensure_ascii=False, default=str),
        "preview_html": preview_html,
        "preview_html_design": preview_html_design,
        "zhiz_print_version": getattr(zhiz_print, "__version__", ""),
        "author_license_key": license_key,
        "author_machine_id": get_machine_id(),
        "author_company": company,
    }

    result = _call_license_api("push_template", body, module="template_api")
    if not result or not result.get("success"):
        return {"success": False, "error": (result or {}).get("error", "Push failed")}
    return {
        "success": True,
        "template_id": result.get("template_id"),
        "is_new": result.get("is_new"),
        "version": result.get("version"),
    }


@frappe.whitelist()
def list_templates(target_doctype=None):
    """从模板平台拉模板列表(带本机公司名,服务端按可见性过滤+标记 is_mine)。"""
    body = {"company_name": _my_company()}
    if target_doctype:
        body["target_doctype"] = target_doctype
    result = _call_license_api("list_templates", body, module="template_api")
    if not result:
        return {"templates": [], "error": "Cannot reach template platform"}
    templates = result.get("templates", []) or []
    # 列表精简:剥离 preview_html(MB级,体积大)。卡片缩略改懒加载——可见时 get_template
    # 单独拉 preview + 本地缓存。脱敏在 get_template 返回时做(点详情/卡片懒加载拉 preview 时脱敏)。
    for tpl in templates:
        if isinstance(tpl, dict):
            tpl.pop("preview_html", None)
            tpl.pop("preview_html_design", None)
    return {"templates": templates, "count": result.get("count", 0)}


@frappe.whitelist()
def get_template(template_id):
    """从模板平台拉单个模板完整数据(带本机公司名,服务端校验可见性)。"""
    try:
        result = _call_license_api("get_template", {
            "template_id": template_id,
            "company_name": _my_company(),
        }, module="template_api", timeout=30)
    except Exception:
        return {"error": "拉取模板预览超时,请稍后重试"}
    if not result or "template" not in result:
        return {"error": (result or {}).get("error", "Template not found")}
    tpl = result.get("template")
    # 返回客户端前脱敏(中心存原版,只在返回客户端模板平台时脱敏)
    if isinstance(tpl, dict):
        for _k in ("preview_html", "preview_html_design"):
            if tpl.get(_k):
                tpl[_k] = _desensitize_preview(tpl[_k])
        # URL/二维码脱敏:install_template 也走 get_template,故安装下来的设计一并脱敏
        tpl = _desensitize_template_urls(tpl)
    return tpl


@frappe.whitelist()
def set_template_visibility(template_id, visible_mode, visible_companies=None):
    """设置自己上传模板的分享对象(服务端校验:仅作者=company_name 匹配可改)。

    visible_mode: Everyone(全体) / Specific(指定公司,visible_companies 一行一个公司全称)
    """
    result = _call_license_api("set_visibility", {
        "template_id": template_id,
        "company_name": _my_company(),
        "visible_mode": visible_mode,
        "visible_companies": visible_companies or "",
    }, module="template_api")
    if not result or not result.get("success"):
        return {"success": False, "error": (result or {}).get("error", "Save failed")}
    return {"success": True, "visible_mode": result.get("visible_mode")}


@frappe.whitelist()
def add_template_comment(template_id, content):
    """提交评论。"""
    lic = frappe.get_all("Zprint License", limit=1, order_by="activated_at desc",
        fields=["license_key", "company_name"])
    license_key = lic[0]["license_key"] if lic else ""
    company = lic[0]["company_name"] if lic else ""
    result = _call_license_api("add_comment", {
        "template_id": template_id,
        "author_license_key": license_key,
        "author_display": company or "匿名",
        "content": content,
    }, module="template_api")
    return result or {"success": False}


@frappe.whitelist()
def list_template_comments(template_id):
    """拉模板评论。"""
    result = _call_license_api("list_comments", {"template_id": template_id}, module="template_api")
    return {"comments": (result or {}).get("comments", [])}


@frappe.whitelist()
def install_template(template_id, new_design_name=None, new_paper_name=None):
    """从模板平台拉模板 + 本地安装。

    1. doctype 检查(target_doctype 本地存在?)
    2. 纸张重名(同名同配置复用 / 同名异配置用 new_paper_name / 无则建)
    3. 设计重名(用 new_design_name 或原名)
    4. 剔 sample_doc
    5. 创建 Super Print Design
    6. 下载计数 +1
    """
    tpl = get_template(template_id)
    if not tpl or tpl.get("error"):
        frappe.throw((tpl or {}).get("error", "Cannot fetch template"))

    target_doctype = tpl.get("target_doctype")
    # 1. doctype 检查
    if not frappe.db.exists("DocType", target_doctype):
        frappe.throw("本地不存在 DocType「{0}」,无法安装此模板".format(target_doctype))

    # 2. 纸张处理
    paper_name = tpl.get("print_paper_name") or ""
    paper_config = {}
    try:
        paper_config = json.loads(tpl.get("print_paper_config") or "{}")
    except Exception:
        paper_config = {}

    if new_paper_name:
        # 用户指定新纸张名 → 用新名(已存在则复用,不存在则建新)
        final_paper = new_paper_name
        if not frappe.db.exists("Super Print Paper", new_paper_name):
            _create_paper(new_paper_name, paper_config)
    elif paper_name and frappe.db.exists("Super Print Paper", paper_name):
        # 检查配置是否一致
        local_paper = frappe.db.get_value("Super Print Paper", paper_name, [
            "width", "height", "margin_top", "margin_bottom",
            "margin_left", "margin_right"], as_dict=True)
        config_match = all(
            (local_paper.get(k) == paper_config.get(k))
            for k in ["width", "height", "margin_top", "margin_bottom", "margin_left", "margin_right"])
        if not config_match:
            frappe.throw("纸张「{0}」已存在但配置不同,请指定新纸张名(new_paper_name)".format(paper_name))
        final_paper = paper_name  # 复用同名同配置
    elif paper_name:
        # 无同名 → 建原纸张
        final_paper = paper_name
        _create_paper(final_paper, paper_config)
    else:
        final_paper = ""

    # 3. 设计重名
    design_name = new_design_name or tpl.get("template_name")
    if frappe.db.exists("Super Print Design", design_name):
        frappe.throw("设计「{0}」已存在,请指定新名称(new_design_name)".format(design_name))

    # 4. 还原 design_data + 剔 sample_doc + 新名 + 新纸张
    try:
        design_data = json.loads(tpl.get("design_data") or "{}")
    except Exception:
        frappe.throw("模板设计数据损坏")

    design_data["doctype"] = "Super Print Design"
    design_data["design_name"] = design_name
    design_data["print_paper"] = final_paper
    design_data["sample_doc"] = ""  # 剔除演示单据
    design_data.pop("name", None)

    # 5. 创建
    doc = frappe.get_doc(design_data)
    frappe.flags.skip_zhiz_print_license = True
    try:
        # ignore_mandatory: sample_doc 已剔空(本地无原单据),跳过必填校验强制安装;
        # 安装后用户在设计器补充本地演示单据
        doc.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.commit()
    finally:
        frappe.flags.skip_zhiz_print_license = False

    # 6. 下载计数 +1
    _call_license_api("increment_download", {"template_id": template_id}, module="template_api")

    return {"success": True, "design_name": design_name}


def _create_paper(paper_name, config):
    """用模板纸张配置创建本地纸张(若不存在)。"""
    if frappe.db.exists("Super Print Paper", paper_name):
        return
    doc = frappe.get_doc({
        "doctype": "Super Print Paper",
        "paper_name": paper_name,
        "width": config.get("width") or 210,
        "height": config.get("height") or 297,
        "margin_top": config.get("margin_top") or 10,
        "margin_bottom": config.get("margin_bottom") or 10,
        "margin_left": config.get("margin_left") or 10,
        "margin_right": config.get("margin_right") or 10,
    })
    doc.insert(ignore_permissions=True)
