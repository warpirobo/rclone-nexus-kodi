"""STRM library export and incremental discovery for Rclone Nexus.

The module intentionally stores only tiny JSON manifests and .strm shortcuts.
Media files are never downloaded.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
except Exception:  # local tests
    class xbmc:
        LOGDEBUG = 0
        LOGINFO = 1
        LOGWARNING = 2
        LOGERROR = 3
        @staticmethod
        def log(msg, level=0):
            print(msg)
        @staticmethod
        def executebuiltin(command):
            print(command)
        @staticmethod
        def executeJSONRPC(payload):
            return '{}'
    class _Addon:
        def getAddonInfo(self, key):
            if key == 'profile':
                return '/tmp/plugin.ariostv-profile/'
            if key == 'id':
                return 'plugin.ariostv'
            return ''
        def getSetting(self, key):
            return ''
    class xbmcaddon:
        @staticmethod
        def Addon():
            return _Addon()
    class xbmcvfs:
        @staticmethod
        def translatePath(path):
            return path.replace('special://profile/', '/tmp/kodi-profile/')
        @staticmethod
        def mkdirs(path):
            os.makedirs(path, exist_ok=True)
        @staticmethod
        def exists(path):
            return os.path.exists(path)

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id') or 'plugin.ariostv'
VIDEO_EXTENSIONS = {
    '.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.iso', '.m2ts', '.mpg',
    '.mpeg', '.wmv', '.flv', '.webm', '.vob', '.3gp', '.ogm', '.ogv'
}
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _log(message, level=xbmc.LOGINFO):
    try:
        xbmc.log('[Rclone Nexus/library] ' + str(message), level)
    except Exception:
        pass


def translate(path):
    try:
        return xbmcvfs.translatePath(path)
    except Exception:
        return path


def mkdirs(path):
    if not path:
        return
    try:
        xbmcvfs.mkdirs(path)
    except Exception:
        os.makedirs(path, exist_ok=True)


def profile_dir():
    path = translate(ADDON.getAddonInfo('profile') or ('special://profile/addon_data/%s/' % ADDON_ID))
    mkdirs(path)
    return path


def registry_path():
    return os.path.join(profile_dir(), 'library_registry.json')


def manifests_dir():
    path = os.path.join(profile_dir(), 'library-manifests')
    mkdirs(path)
    return path


def discovery_dir():
    path = os.path.join(profile_dir(), 'discovery')
    mkdirs(path)
    return path


def recent_path():
    return os.path.join(profile_dir(), 'recent_content.json')


def favorites_path():
    return os.path.join(profile_dir(), 'favorites.json')


def default_library_root():
    configured = ADDON.getSetting('library_root')
    root = translate(configured) if configured else os.path.join(profile_dir(), 'library')
    mkdirs(root)
    return root


def _load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            value = json.load(fh)
        return value
    except Exception:
        return default


def _save_json(path, value):
    mkdirs(os.path.dirname(path))
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def load_registry():
    data = _load_json(registry_path(), {'version': 1, 'exports': []})
    if not isinstance(data, dict):
        return {'version': 1, 'exports': []}
    data.setdefault('version', 1)
    data.setdefault('exports', [])
    return data


def save_registry(data):
    _save_json(registry_path(), data)


def export_id(remote, path, destination=''):
    raw = ('%s\0%s\0%s' % (remote or '', path or '', destination or '')).encode('utf-8', 'replace')
    return hashlib.sha1(raw).hexdigest()[:16]


def manifest_path(entry):
    return os.path.join(manifests_dir(), entry['id'] + '.json')


def get_exports():
    return list(load_registry().get('exports') or [])


def find_export(entry_id):
    for entry in get_exports():
        if entry.get('id') == entry_id:
            return entry
    return None


def upsert_export(entry):
    data = load_registry()
    replaced = False
    for index, current in enumerate(data['exports']):
        if current.get('id') == entry.get('id'):
            data['exports'][index] = entry
            replaced = True
            break
    if not replaced:
        data['exports'].append(entry)
    save_registry(data)
    return entry


def remove_export(entry_id, delete_files=False):
    data = load_registry()
    entry = None
    remaining = []
    for current in data.get('exports', []):
        if current.get('id') == entry_id:
            entry = current
        else:
            remaining.append(current)
    data['exports'] = remaining
    save_registry(data)
    if entry:
        try:
            os.remove(manifest_path(entry))
        except Exception:
            pass
        if delete_files:
            _remove_tree(entry.get('destination', ''))
    return entry


def _remove_tree(path):
    if not path or not os.path.isdir(path):
        return
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files:
            try:
                os.remove(os.path.join(root, name))
            except Exception:
                pass
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except Exception:
                pass
    try:
        os.rmdir(path)
    except Exception:
        pass


def sanitize_component(name):
    value = INVALID_FILENAME.sub('_', str(name or '')).strip().rstrip('. ')
    return value or '_'


def relative_from_root(root_path, full_path):
    root = (root_path or '').strip('/')
    full = (full_path or '').strip('/')
    if root and full.startswith(root + '/'):
        return full[len(root) + 1:]
    if full == root:
        return os.path.basename(full)
    return full


def strm_relative_path(root_path, remote_file_path):
    relative = relative_from_root(root_path, remote_file_path)
    parts = [sanitize_component(part) for part in relative.split('/') if part]
    if not parts:
        parts = ['video']
    keep_extension = str(ADDON.getSetting('strm_keep_extension') or '').lower() in ('true', '1', 'yes', 'on')
    filename = parts[-1]
    if not keep_extension:
        filename = os.path.splitext(filename)[0]
    parts[-1] = filename + '.strm'
    return os.path.join(*parts)


def plugin_play_url(remote, path, name=''):
    query = urllib.parse.urlencode({
        'action': 'play',
        'remote': remote or '',
        'path': path or '',
        'name': name or os.path.basename(path or ''),
    })
    return 'plugin://%s/?%s' % (ADDON_ID, query)


def is_video(path):
    return os.path.splitext((path or '').lower())[1] in VIDEO_EXTENSIONS


def create_export(remote, path, display_name, media_type='videos', destination=''):
    root = destination or os.path.join(default_library_root(), sanitize_component(display_name or os.path.basename(path) or remote))
    root = translate(root)
    mkdirs(root)
    entry = {
        'id': export_id(remote, path, root),
        'remote': remote,
        'path': (path or '').strip('/'),
        'name': display_name or os.path.basename((path or '').rstrip('/')) or remote,
        'media_type': media_type or 'videos',
        'destination': root,
        'created_at': int(time.time()),
        'last_sync': 0,
        'file_count': 0,
        'last_added': 0,
        'last_removed': 0,
        'last_error': '',
    }
    upsert_export(entry)
    return entry


def _file_signature(item):
    return {
        'size': int(item.get('size', 0) or 0),
        'modified': item.get('modified', '') or '',
    }


def _prune_empty_dirs(path, stop):
    current = os.path.dirname(path)
    stop = os.path.abspath(stop)
    while current and os.path.abspath(current).startswith(stop) and os.path.abspath(current) != stop:
        try:
            os.rmdir(current)
        except OSError:
            break
        current = os.path.dirname(current)


def sync_export(backend, entry, delete_missing=None):
    """Synchronize one registered folder and return a small result dictionary."""
    if not entry:
        raise ValueError('Export not found')
    if delete_missing is None:
        delete_missing = str(ADDON.getSetting('library_delete_missing') or 'true').lower() in ('true', '1', 'yes', 'on')

    old_manifest = _load_json(manifest_path(entry), {'version': 1, 'files': {}})
    old_files = old_manifest.get('files', {}) if isinstance(old_manifest, dict) else {}
    items = backend.list_recursive(entry['remote'], entry.get('path', ''), video_only=True)
    current = {}
    added = []
    updated = 0
    unchanged = 0
    used_local_paths = {}

    for item in items:
        remote_path = item.get('path', '')
        if not remote_path or not is_video(remote_path):
            continue
        old = old_files.get(remote_path)
        rel = old.get('strm', '').replace('/', os.sep) if old and old.get('strm') else strm_relative_path(entry.get('path', ''), remote_path)
        collision_key = rel.lower()
        if collision_key in used_local_paths and used_local_paths[collision_key] != remote_path:
            stem, suffix = os.path.splitext(rel)
            source_ext = os.path.splitext(remote_path)[1].lower().lstrip('.') or hashlib.sha1(remote_path.encode('utf-8', 'replace')).hexdigest()[:6]
            rel = '%s [%s]%s' % (stem, source_ext, suffix)
            collision_key = rel.lower()
            if collision_key in used_local_paths:
                stem, suffix = os.path.splitext(rel)
                rel = '%s [%s]%s' % (stem, hashlib.sha1(remote_path.encode('utf-8', 'replace')).hexdigest()[:6], suffix)
                collision_key = rel.lower()
        used_local_paths[collision_key] = remote_path
        local_path = os.path.join(entry['destination'], rel)
        signature = _file_signature(item)
        current[remote_path] = {
            'strm': rel.replace(os.sep, '/'),
            'size': signature['size'],
            'modified': signature['modified'],
        }
        needs_write = not old or not os.path.exists(local_path)
        if needs_write:
            mkdirs(os.path.dirname(local_path))
            with open(local_path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(plugin_play_url(entry['remote'], remote_path, item.get('name', '')) + '\n')
            if not old:
                added.append(item)
            else:
                updated += 1
        elif (old.get('size') != signature['size'] or old.get('modified') != signature['modified']):
            # The STRM target is stable; only the manifest needs updating.
            updated += 1
        else:
            unchanged += 1

    removed = 0
    if delete_missing:
        for remote_path, old in old_files.items():
            if remote_path in current:
                continue
            rel = old.get('strm', '')
            local_path = os.path.join(entry['destination'], *rel.split('/'))
            try:
                if os.path.isfile(local_path):
                    os.remove(local_path)
                    removed += 1
                    _prune_empty_dirs(local_path, entry['destination'])
            except Exception as exc:
                _log('Could not delete stale STRM file %s: %s' % (local_path, exc), xbmc.LOGWARNING)

    now = int(time.time())
    manifest = {
        'version': 2,
        'remote': entry['remote'],
        'path': entry.get('path', ''),
        'synced_at': now,
        'files': current,
    }
    _save_json(manifest_path(entry), manifest)

    first_sync = not bool(old_files)
    if added and not first_sync:
        add_recent_items(entry['remote'], added, entry_id=entry['id'])

    entry = dict(entry)
    entry.update({
        'last_sync': now,
        'file_count': len(current),
        'last_added': 0 if first_sync else len(added),
        'last_removed': removed,
        'last_error': '',
    })
    upsert_export(entry)
    return {
        'entry': entry,
        'first_sync': first_sync,
        'total': len(current),
        'added': 0 if first_sync else len(added),
        'updated': updated,
        'removed': removed,
        'unchanged': unchanged,
        'destination': entry['destination'],
    }


def mark_export_error(entry, exc):
    if not entry:
        return
    current = dict(entry)
    current['last_error'] = str(exc)[:1000]
    current['last_attempt'] = int(time.time())
    upsert_export(current)


def sync_all(backend_factory):
    results = []
    for entry in get_exports():
        try:
            backend = backend_factory()
            results.append(sync_export(backend, entry))
        except Exception as exc:
            mark_export_error(entry, exc)
            results.append({'entry': entry, 'error': str(exc)})
    return results


def _folder_scan_path(remote, path):
    raw = ('%s\0%s' % (remote or '', path or '')).encode('utf-8', 'replace')
    return os.path.join(discovery_dir(), hashlib.sha1(raw).hexdigest() + '.json')


def scan_new_content(backend, remote, path=''):
    state_path = _folder_scan_path(remote, path)
    old = _load_json(state_path, {'files': {}})
    old_files = old.get('files', {}) if isinstance(old, dict) else {}
    items = backend.list_recursive(remote, path, video_only=True)
    current = {item['path']: _file_signature(item) for item in items if is_video(item.get('path', ''))}
    first_scan = not bool(old_files)
    added = [item for item in items if item.get('path') not in old_files]
    removed = [item_path for item_path in old_files if item_path not in current]
    _save_json(state_path, {'remote': remote, 'path': path, 'scanned_at': int(time.time()), 'files': current})
    if added and not first_scan:
        add_recent_items(remote, added)
    return {
        'first_scan': first_scan,
        'total': len(current),
        'added': 0 if first_scan else len(added),
        'removed': len(removed),
        'items': [] if first_scan else added,
    }


def add_recent_items(remote, items, entry_id=''):
    data = _load_json(recent_path(), {'version': 1, 'items': []})
    existing = {(x.get('remote'), x.get('path')): x for x in data.get('items', []) if isinstance(x, dict)}
    now = int(time.time())
    for item in items:
        key = (remote, item.get('path'))
        existing[key] = {
            'remote': remote,
            'path': item.get('path', ''),
            'name': item.get('name') or os.path.basename(item.get('path', '')),
            'size': int(item.get('size', 0) or 0),
            'modified': item.get('modified', ''),
            'discovered_at': now,
            'entry_id': entry_id,
        }
    max_items = 500
    values = sorted(existing.values(), key=lambda x: int(x.get('discovered_at', 0)), reverse=True)[:max_items]
    _save_json(recent_path(), {'version': 1, 'items': values})


def get_recent_items():
    data = _load_json(recent_path(), {'items': []})
    days = 30
    try:
        days = max(1, int(ADDON.getSetting('new_content_days') or 30))
    except Exception:
        pass
    cutoff = int(time.time()) - days * 86400
    items = [x for x in data.get('items', []) if int(x.get('discovered_at', 0) or 0) >= cutoff]
    return sorted(items, key=lambda x: int(x.get('discovered_at', 0)), reverse=True)


def clear_recent_items():
    _save_json(recent_path(), {'version': 1, 'items': []})


def load_favorites():
    data = _load_json(favorites_path(), {'items': []})
    return data.get('items', []) if isinstance(data, dict) else []


def is_favorite(remote, path):
    return any(x.get('remote') == remote and x.get('path', '') == (path or '') for x in load_favorites())


def toggle_favorite(remote, path, name=''):
    items = load_favorites()
    found = False
    kept = []
    for item in items:
        if item.get('remote') == remote and item.get('path', '') == (path or ''):
            found = True
        else:
            kept.append(item)
    if not found:
        kept.append({'remote': remote, 'path': path or '', 'name': name or os.path.basename((path or '').rstrip('/')) or remote})
    _save_json(favorites_path(), {'version': 1, 'items': kept})
    return not found


def _ensure_sources_tree(path):
    if os.path.exists(path):
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            if root.tag == 'sources':
                return tree, root
        except Exception as exc:
            _log('sources.xml is invalid; a new file will be created: %s' % exc, xbmc.LOGWARNING)
    root = ET.Element('sources')
    for section in ('programs', 'video', 'music', 'pictures', 'files', 'games'):
        element = ET.SubElement(root, section)
        default = ET.SubElement(element, 'default')
        default.set('pathversion', '1')
    return ET.ElementTree(root), root


def register_kodi_video_source(entry):
    """Add the local STRM folder to sources.xml without touching scraper settings."""
    sources_path = translate('special://profile/sources.xml')
    mkdirs(os.path.dirname(sources_path))
    tree, root = _ensure_sources_tree(sources_path)
    video = root.find('video')
    if video is None:
        video = ET.SubElement(root, 'video')
    destination = os.path.abspath(entry['destination'])
    destination_with_sep = destination if destination.endswith(os.sep) else destination + os.sep
    for source in video.findall('source'):
        path_node = source.find('path')
        if path_node is not None and os.path.abspath((path_node.text or '').rstrip('/\\')) == destination.rstrip('/\\'):
            return False
    source = ET.SubElement(video, 'source')
    name = ET.SubElement(source, 'name')
    name.text = 'Rclone Nexus - %s' % entry.get('name', 'Library')
    path_node = ET.SubElement(source, 'path')
    path_node.set('pathversion', '1')
    path_node.text = destination_with_sep
    allow = ET.SubElement(source, 'allowsharing')
    allow.text = 'true'
    try:
        ET.indent(tree, space='  ')
    except Exception:
        pass
    tree.write(sources_path, encoding='UTF-8', xml_declaration=True)
    return True



def unregister_kodi_video_source(entry):
    sources_path = translate('special://profile/sources.xml')
    if not os.path.exists(sources_path):
        return False
    try:
        tree = ET.parse(sources_path)
        root = tree.getroot()
        video = root.find('video')
        if video is None:
            return False
        destination = os.path.abspath(entry.get('destination', '')).rstrip('/\\')
        removed = False
        for source in list(video.findall('source')):
            path_node = source.find('path')
            candidate = os.path.abspath((path_node.text or '').rstrip('/\\')) if path_node is not None else ''
            if candidate == destination:
                video.remove(source)
                removed = True
        if removed:
            try:
                ET.indent(tree, space='  ')
            except Exception:
                pass
            tree.write(sources_path, encoding='UTF-8', xml_declaration=True)
        return removed
    except Exception as exc:
        _log('Could not remove the Kodi source: %s' % exc, xbmc.LOGWARNING)
        return False

def request_library_update(path=''):
    try:
        if path:
            safe = path.replace('"', '')
            xbmc.executebuiltin('UpdateLibrary(video,"%s")' % safe)
        else:
            xbmc.executebuiltin('UpdateLibrary(video)')
    except Exception as exc:
        _log('Could not request UpdateLibrary: %s' % exc, xbmc.LOGWARNING)


def format_bytes(size):
    value = float(size or 0)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024.0 or unit == 'TB':
            return ('%.1f %s' % (value, unit)) if unit != 'B' else ('%d B' % value)
        value /= 1024.0
