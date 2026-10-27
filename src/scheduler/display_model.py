"""Shared UTF-8 content and declarative, hardware-independent layout contract."""
import copy
import json
import math
import re
import unicodedata

WIDTH, HEIGHT = 1872, 1404
MAX_ELEMENTS = 32
BINDINGS = ('', 'title', 'status', 'clock', 'date', 'battery', *(
    f'rows.{i}.{field}' for i in range(6) for field in ('label', 'value')))
FONTS = ('sans', 'serif')


def text(raw, limit, empty=False, multiline=False):
    if (not isinstance(raw, str) or len(raw) > limit or (not empty and not raw)
            or any(unicodedata.category(c).startswith('C') and not (multiline and c == '\n') for c in raw)):
        raise ValueError(f'文字须为有效 Unicode，长度 {0 if empty else 1}–{limit} 个字符。')
    return raw


def validate_display(value, *, draft=False):
    if not isinstance(value, dict) or set(value) - {'title', 'status', 'rows', 'layout'}:
        raise ValueError('屏幕内容必须包含标题、状态和信息行。')
    rows = value.get('rows')
    if not isinstance(rows, list) or len(rows) > 6:
        raise ValueError('最多可设置六条显示信息。')
    result = {'title': text(value.get('title'), 40, draft),
              'status': text(value.get('status'), 24, draft), 'rows': []}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'label', 'value'}:
            raise ValueError('每条显示信息都需要名称和内容。')
        result['rows'].append({'label': text(row['label'], 20, draft),
                               'value': text(row['value'], 48, True, True)})
    if 'layout' in value:
        result['layout'] = validate_layout(value['layout'])
    return result


def validate_layout(raw):
    if not isinstance(raw, dict) or set(raw) != {'schema_version', 'id', 'revision', 'elements'}:
        raise ValueError('版式格式无效。')
    if raw['schema_version'] != 1 or type(raw['schema_version']) is not int:
        raise ValueError('不支持此版式格式版本。')
    if not isinstance(raw['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', raw['id']):
        raise ValueError('版式标识无效。')
    if type(raw['revision']) is not int or not 1 <= raw['revision'] <= 2147483647:
        raise ValueError('版式版本无效。')
    elements = raw['elements']
    if not isinstance(elements, list) or not 1 <= len(elements) <= MAX_ELEMENTS:
        raise ValueError('每个版式须包含 1–32 个组件。')
    result, ids = [], set()
    defaults = dict(binding='', text='', prefix='', suffix='', font='sans', weight=400,
                    size=40, line_height=1.35, align='left', gray=0)
    for item in elements:
        if not isinstance(item, dict) or set(item) - {'id', 'type', 'x', 'y', 'width', 'height', *defaults}:
            raise ValueError('组件属性无效。')
        item = dict(defaults, **item)
        if (not isinstance(item.get('id'), str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', item['id'])
                or item['id'] in ids or item.get('type') not in ('text', 'line')):
            raise ValueError('组件标识重复或类型无效。')
        ids.add(item['id'])
        for key, maximum in (('x', WIDTH - 1), ('y', HEIGHT - 1), ('width', WIDTH), ('height', HEIGHT)):
            if type(item.get(key)) is not int or not (0 if key in ('x', 'y') else 1) <= item[key] <= maximum:
                raise ValueError('组件坐标或尺寸超出画布。')
        if item['x'] + item['width'] > WIDTH or item['y'] + item['height'] > HEIGHT:
            raise ValueError('组件超出画布。')
        if (item['binding'] not in BINDINGS or item['font'] not in FONTS or
                item['align'] not in ('left', 'center', 'right') or type(item['weight']) is not int or
                item['weight'] not in (400, 500) or type(item['size']) is not int or not 16 <= item['size'] <= 160 or
                type(item['gray']) is not int or not 0 <= item['gray'] <= 15 or
                type(item['line_height']) not in (int, float) or not math.isfinite(item['line_height']) or
                not 1 <= item['line_height'] <= 2):
            raise ValueError('组件字体、绑定或颜色无效。')
        text(item['text'], 256, True, True)
        text(item['prefix'], 32, True)
        text(item['suffix'], 32, True)
        result.append(item)
    cleaned = dict(raw, elements=result)
    if len(json.dumps(cleaned, ensure_ascii=False).encode()) > 24 * 1024:
        raise ValueError('版式配置过大。')
    return cleaned


def _text(id, binding, x, y, width, height, size=40, **options):
    return dict(id=id, type='text', binding=binding, x=x, y=y, width=width, height=height,
                size=size, **options)


def _line(id, x, y, width, height=2, **options):
    return dict(id=id, type='line', x=x, y=y, width=width, height=height, **options)


def builtin_layouts():
    paper = [_text('masthead', '', 96, 64, 1100, 52, 30, text='EPAPER / 日常记录'),
             _text('date', 'date', 1276, 64, 500, 52, 30, align='right'),
             _line('top-rule', 96, 156, 1680),
             _text('title', 'title', 96, 218, 1680, 132, 96, font='serif'),
             _text('status', 'status', 96, 360, 1680, 58, 34, gray=5)]
    for i in range(6):
        x, y = 96 + (i % 2) * 888, 460 + (i // 2) * 252
        paper += [_line(f'rule-{i}', x, y, 792, 1, gray=9, binding=f'rows.{i}.label'),
                  _text(f'label-{i}', f'rows.{i}.label', x, y + 20, 792, 48, 30, gray=5),
                  _text(f'value-{i}', f'rows.{i}.value', x, y + 82, 792, 164, 40, line_height=1.3)]
    paper += [_line('footer-rule', 96, 1274, 1680),
              _text('clock', 'clock', 96, 1300, 1240, 58, 30, prefix='更新于 ', gray=5),
              _text('battery', 'battery', 1400, 1300, 376, 58, 30, prefix='电量 ', align='right', gray=5)]
    minimal = [_text('title', 'title', 96, 76, 1680, 100, 64, weight=500),
               _text('status', 'status', 96, 190, 1680, 60, 34, gray=5),
               _line('top-rule', 96, 278, 1680)]
    for i in range(6):
        y = 320 + i * 148
        minimal += [_text(f'label-{i}', f'rows.{i}.label', 96, y, 390, 118, 30, gray=5),
                    _text(f'value-{i}', f'rows.{i}.value', 542, y, 1234, 118, 40, line_height=1.3),
                    _line(f'rule-{i}', 96, y + 136, 1680, 1, gray=10, binding=f'rows.{i}.label')]
    minimal += copy.deepcopy(paper[-3:])
    return [(name, validate_layout(dict(schema_version=1, id=id, revision=1, elements=elements)))
            for id, name, elements in [('editorial', '纸刊排版', paper), ('minimal', '极简信息板', minimal)]]


def default_layout():
    return builtin_layouts()[0][1]
