"""Authenticated layout drafts, immutable device snapshots, and native previews."""
import base64
import json
import secrets

from flask import abort, jsonify, render_template, request
from src.scheduler.display_model import BINDINGS, text, validate_layout, validate_display
from .preview import PreviewUnavailable
from .i18n import t


def register_layout_routes(app, login_required, csrf_token, now, get_db, get_renderer):
    def device(device_id):
        result = get_db().execute('SELECT * FROM devices WHERE device_id=?', (device_id,)).fetchone()
        if result is None:
            abort(404)
        return result

    def saved(device_id, layout_id):
        result = get_db().execute('SELECT * FROM layouts WHERE device_id=? AND id=?', (device_id, layout_id)).fetchone()
        if result is None:
            abort(404)
        return result

    def serialize(row):
        return dict(id=row['id'], name=t(row['name']) if row['builtin'] else row['name'], revision=row['revision'], builtin=bool(row['builtin']),
                    layout=json.loads(row['document_json']))

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            abort(400, '请求必须为 JSON 对象。')
        return value

    def renderer():
        return get_renderer(app.config['PAGE_RENDERER_LIBRARY'])

    def validation(display):
        try:
            return renderer().validate(display)
        except PreviewUnavailable:
            abort(503, '屏幕预览暂不可用，未应用版式。')

    @app.errorhandler(409)
    def conflict(error):
        return jsonify(error=t(error.description)), 409

    @app.get('/devices/<device_id>/layouts')
    @login_required
    def layout_editor(device_id):
        row = device(device_id)
        templates = [serialize(item) for item in get_db().execute(
            'SELECT * FROM layouts WHERE device_id=? ORDER BY builtin DESC,rowid', (device_id,))]
        active = json.loads(row['active_layout_json'])
        payload = json.loads(row['latest_payload'] or '{}')
        metrics = payload.get('metrics', {})
        applied = metrics.get('applied_content_version')
        return render_template('layouts.html', device=row, templates=templates, active=active,
                               bindings=BINDINGS, applied=applied)

    @app.post('/devices/<device_id>/layouts')
    @login_required
    def layout_create(device_id):
        device(device_id)
        raw = body()
        if not isinstance(raw.get('source_id', 'editorial'), str):
            return jsonify(error=t('版式格式无效。')), 400
        source = saved(device_id, raw.get('source_id', 'editorial'))
        try:
            name = text(raw.get('name', source['name'] + ' · 副本'), 64)
        except ValueError as exc:
            return jsonify(error=t(str(exc))), 400
        layout_id = secrets.token_hex(12)
        document = dict(json.loads(source['document_json']), id=layout_id, revision=1)
        db = get_db()
        db.execute('INSERT INTO layouts VALUES(?,?,?,?,?,?)',
                   (device_id, layout_id, name, 1, 0, json.dumps(document)))
        db.commit()
        return jsonify(serialize(saved(device_id, layout_id))), 201

    @app.post('/devices/<device_id>/layouts/<layout_id>')
    @login_required
    def layout_save(device_id, layout_id):
        raw, db = body(), get_db()
        device(device_id)
        db.execute('BEGIN IMMEDIATE')
        row = saved(device_id, layout_id)
        if row['builtin']:
            abort(400, '请先复制内置模板。')
        if type(raw.get('revision')) is not int or raw['revision'] != row['revision']:
            abort(409, '版式已被其他窗口修改，请重新载入；当前编辑内容尚未覆盖服务器。')
        if raw.get('action') == 'delete':
            if json.loads(device(device_id)['active_layout_json'])['id'] == layout_id:
                abort(400, '请先切换其他版式，再删除当前使用的版式。')
            db.execute('DELETE FROM layouts WHERE device_id=? AND id=?', (device_id, layout_id))
            db.commit()
            return jsonify(deleted=True)
        try:
            name = text(raw.get('name'), 64)
            layout = validate_layout(raw.get('layout'))
            if layout['id'] != layout_id or layout['revision'] != row['revision']:
                raise ValueError('版式标识不匹配。')
        except (ValueError, TypeError) as exc:
            return jsonify(error=t(str(exc))), 400
        changed = name != row['name'] or layout != json.loads(row['document_json'])
        layout['revision'] = row['revision'] + int(changed)
        db.execute('UPDATE layouts SET name=?,revision=?,document_json=? WHERE device_id=? AND id=?',
                   (name, layout['revision'], json.dumps(layout), device_id, layout_id))
        db.commit()
        return jsonify(serialize(saved(device_id, layout_id)))

    @app.post('/devices/<device_id>/layouts/<layout_id>/apply')
    @login_required
    def layout_apply(device_id, layout_id):
        raw, db = body(), get_db()
        db.execute('BEGIN IMMEDIATE')
        current, row = device(device_id), saved(device_id, layout_id)
        if type(raw.get('revision')) is not int or raw['revision'] != row['revision']:
            abort(409, '版式已变更，请重新载入。')
        layout = json.loads(row['document_json'])
        display = dict(json.loads(current['display_json']), layout=layout)
        issues = validation(display)
        if issues:
            return jsonify(error=t('文字超出区域或字体缺字，请调整标记的组件后再应用。'), issues=issues), 400
        changed = layout != json.loads(current['active_layout_json'])
        db.execute('UPDATE devices SET active_layout_json=?,content_version=content_version+? WHERE device_id=?',
                   (json.dumps(layout), int(changed), device_id))
        db.commit()
        return jsonify(applied=True, content_version=current['content_version'] + int(changed), layout_id=layout_id,
                       revision=row['revision'], message=t('已安排在设备下次唤醒时应用。'))

    @app.post('/devices/<device_id>/layout-preview')
    @login_required
    def layout_preview(device_id):
        current, raw = device(device_id), body()
        try:
            layout = validate_layout(raw.get('layout'))
            display = dict(json.loads(current['display_json']), layout=layout)
            if 'content' in raw:
                display = dict(validate_display(raw['content'], draft=True), layout=layout)
            # Representative metadata keeps editing stable; the ordinary preview
            # remains tied to the latest trusted device report.
            image, issues = renderer().preview(display, now().strftime('%Y-%m-%d %H:%M UTC%z'), '100.0%')
            reserved = validation(display)
            unique = {(item['element_id'], item['code']): item for item in issues + reserved}
        except (ValueError, TypeError) as exc:
            return jsonify(error=t(str(exc))), 400
        except PreviewUnavailable:
            return jsonify(error='屏幕预览暂不可用。'), 503
        return jsonify(png_base64=base64.b64encode(image).decode(), issues=list(unique.values()))
