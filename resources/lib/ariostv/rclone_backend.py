"""rclone binary backend for Rclone Nexus on Kodi Android.

Design goals:
- Never download rclone from the network.
- Prefer a user-supplied path or a binary bundled in the addon.
- Keep playback storage bounded; disk VFS caching is disabled by default.
- Use small JSON caches only for interactive folder navigation.
"""

import gzip
import hashlib
import json
import os
import platform
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.parse

try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
except Exception:  # local syntax/tests outside Kodi
    class xbmc:
        LOGDEBUG = 0
        LOGINFO = 1
        LOGWARNING = 2
        LOGERROR = 3
        @staticmethod
        def log(msg, level=0):
            print(msg)
    class _Addon:
        def getAddonInfo(self, key):
            if key == 'profile':
                return '/tmp/plugin.ariostv-profile/'
            if key == 'path':
                return os.getcwd()
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
            return path.replace('special://profile/', '/tmp/kodi-profile/').replace('special://home/', '/tmp/kodi-home/')
        @staticmethod
        def exists(path):
            return os.path.exists(path)
        @staticmethod
        def mkdirs(path):
            os.makedirs(path, exist_ok=True)

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id') or 'plugin.ariostv'
ANDROID_API_LEVEL = 21
_LAST_REPORT = []


def _report(msg, level=xbmc.LOGINFO):
    text = str(msg)
    _LAST_REPORT.append(text)
    if len(_LAST_REPORT) > 120:
        del _LAST_REPORT[:-120]
    try:
        xbmc.log('[Rclone Nexus/rclone] ' + text, level)
    except Exception:
        pass


def get_last_report():
    return '\n'.join(_LAST_REPORT[-100:])


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


def exists(path):
    try:
        return bool(path) and (os.path.exists(path) or xbmcvfs.exists(path))
    except Exception:
        return bool(path) and os.path.exists(path)


def addon_profile():
    path = translate(ADDON.getAddonInfo('profile') or ('special://profile/addon_data/%s/' % ADDON_ID))
    mkdirs(path)
    return path


def addon_path():
    return translate(ADDON.getAddonInfo('path') or '')


def _setting_bool(key, default=False):
    value = ADDON.getSetting(key)
    if value in (None, ''):
        return bool(default)
    return str(value).strip().lower() in ('true', '1', 'yes', 'on', 'si', 'sí')


def _setting_int(key, default, minimum=None, maximum=None):
    try:
        value = int(ADDON.getSetting(key)) if ADDON.getSetting(key) not in (None, '') else int(default)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _setting_text(key, default=''):
    value = ADDON.getSetting(key)
    return value if value not in (None, '') else default


def _run_getprop(prop):
    getprop = '/system/bin/getprop'
    if not os.path.exists(getprop):
        return ''
    try:
        proc = subprocess.Popen([getprop, prop], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, _ = proc.communicate(timeout=3)
        except TypeError:
            out, _ = proc.communicate()
    except Exception:
        return ''
    if isinstance(out, bytes):
        out = out.decode('utf-8', 'replace')
    return (out or '').strip()


def android_props():
    return {
        'sdk': _run_getprop('ro.build.version.sdk'),
        'release': _run_getprop('ro.build.version.release'),
        'model': _run_getprop('ro.product.model'),
        'device': _run_getprop('ro.product.device'),
        'manufacturer': _run_getprop('ro.product.manufacturer'),
        'cpu_abi': _run_getprop('ro.product.cpu.abi'),
        'cpu_abilist': _run_getprop('ro.product.cpu.abilist'),
    }


def _arch_from_abi_text(text):
    abi = (text or '').lower()
    # The Kodi application ABI matters more than the physical CPU architecture.
    if 'armeabi-v7a' in abi or 'armv7' in abi or abi == 'arm':
        return 'armv7a'
    if 'arm64-v8a' in abi or 'aarch64' in abi or 'arm64' in abi:
        return 'armv8a'
    if 'x86_64' in abi or 'amd64' in abi:
        return 'x64'
    if 'x86' in abi or 'i686' in abi or 'i386' in abi:
        return 'x86'
    return ''


def detected_android_arch():
    forced = (_setting_text('rclone_arch', 'auto') or 'auto').strip().lower()
    # Spanish aliases are retained for backward compatibility with existing settings.
    aliases = {
        'auto': '', 'automatico': '', 'automático': '',
        'armv7': 'armv7a', 'armeabi-v7a': 'armv7a', '32': 'armv7a', '32bit': 'armv7a',
        'armv8': 'armv8a', 'arm64': 'armv8a', 'arm64-v8a': 'armv8a', 'aarch64': 'armv8a',
        '64': 'armv8a', '64bit': 'armv8a', 'x86_64': 'x64', 'amd64': 'x64',
    }
    forced = aliases.get(forced, forced)
    if forced in ('armv7a', 'armv8a', 'x86', 'x64'):
        return forced
    props = android_props()
    primary = _arch_from_abi_text(props.get('cpu_abi') or '')
    if primary:
        return primary
    listed = _arch_from_abi_text(props.get('cpu_abilist') or '')
    if listed:
        return listed
    machine = (platform.machine() or '').lower()
    arch = _arch_from_abi_text(machine)
    if arch:
        return arch
    return 'armv8a'


def _is_android_like():
    lower = (sys.platform + ' ' + platform.platform()).lower()
    return 'android' in lower or os.path.exists('/system/bin/getprop') or os.path.exists('/system/build.prop')


def android_package_name():
    try:
        raw = open('/proc/self/cmdline', 'rb').read().split(b'\0')[0].decode('utf-8', 'replace')
        name = raw.split(':', 1)[0].strip()
        if '.' in name:
            return name
    except Exception:
        pass
    return ''


def _can_write_dir(path):
    try:
        mkdirs(path)
        test = os.path.join(path, '.rclone_nexus_write_test')
        with open(test, 'wb') as fh:
            fh.write(b'x')
        os.remove(test)
        return True
    except Exception:
        return False


def executable_base_dir():
    custom = _setting_text('rclone_install_dir')
    candidates = []
    if custom:
        candidates.append(translate(custom))
    pkg = android_package_name()
    if pkg:
        candidates.extend([
            '/data/user/0/%s/files/rclone-nexus' % pkg,
            '/data/data/%s/files/rclone-nexus' % pkg,
        ])
    candidates.append(os.path.join(addon_profile(), 'exec'))
    for path in candidates:
        if _can_write_dir(path):
            return path
    return os.path.join(addon_profile(), 'exec')


def rclone_managed_path(arch=None):
    arch = arch or detected_android_arch()
    return os.path.join(executable_base_dir(), 'bin', 'android-%s' % arch, 'rclone')


def rclone_legacy_managed_paths():
    return [
        os.path.join(executable_base_dir(), 'bin', 'rclone'),
        os.path.join(addon_profile(), 'bin', 'rclone'),
    ]


def _abi_dirs(arch):
    if arch == 'armv7a':
        return ['armeabi-v7a', 'armv7a', 'arm']
    if arch == 'armv8a':
        return ['arm64-v8a', 'armv8a', 'arm64']
    if arch == 'x64':
        return ['x86_64', 'x64']
    return ['x86']


def _candidate_paths():
    arch = detected_android_arch()
    candidates = []
    setting = _setting_text('rclone_path')
    if setting:
        candidates.append(translate(setting))
    candidates.append(rclone_managed_path(arch))
    candidates.extend(rclone_legacy_managed_paths())
    base = addon_path()
    for directory in _abi_dirs(arch):
        candidates.extend([
            os.path.join(base, 'resources', 'bin', 'android', directory, 'rclone'),
            os.path.join(base, 'resources', 'bin', 'android', directory, 'rclone.gz'),
        ])
    candidates.extend([
        '/data/data/com.termux/files/usr/bin/rclone',
        '/system/bin/rclone',
        '/system/xbin/rclone',
    ])
    for folder in os.environ.get('PATH', '').split(os.pathsep):
        if folder:
            candidates.append(os.path.join(folder, 'rclone'))
    seen, result = set(), []
    for path in candidates:
        if path and path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _manual_import_candidates():
    arch = detected_android_arch()
    names = [
        'rclone-android-%d-%s.gz' % (ANDROID_API_LEVEL, arch),
        'rclone-android-%d-%s' % (ANDROID_API_LEVEL, arch),
        'rclone.gz', 'rclone', 'rclone.bin',
    ]
    roots = ['/sdcard/Download', '/storage/emulated/0/Download', '/sdcard/Downloads', '/storage/emulated/0/Downloads']
    return [os.path.join(root, name) for root in roots for name in names]


def _looks_gzip(path):
    try:
        with open(path, 'rb') as fh:
            return fh.read(2) == b'\x1f\x8b'
    except Exception:
        return False


def _make_executable(path):
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as exc:
        _report('Could not apply chmod to %s: %s' % (path, exc), xbmc.LOGWARNING)


def _install_binary_from_file(src_path, gzipped=None):
    if not exists(src_path):
        raise RuntimeError('Does not exist: %s' % src_path)
    dest = rclone_managed_path()
    tmp = dest + '.tmp'
    mkdirs(os.path.dirname(dest))
    if gzipped is None:
        gzipped = src_path.endswith('.gz') or _looks_gzip(src_path)
    _report('Copying local rclone %s -> %s (gz=%s)' % (src_path, dest, gzipped), xbmc.LOGINFO)
    if gzipped:
        with gzip.open(src_path, 'rb') as src, open(tmp, 'wb') as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    else:
        shutil.copyfile(src_path, tmp)
    _make_executable(tmp)
    if exists(dest):
        try:
            os.remove(dest)
        except Exception:
            pass
    os.replace(tmp, dest)
    _make_executable(dest)
    return dest


def _run_raw(args, timeout=30):
    env = os.environ.copy()
    base = addon_profile()
    env.setdefault('HOME', base)
    env.setdefault('TMPDIR', os.path.join(base, 'tmp'))
    mkdirs(env['TMPDIR'])
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
    except TypeError:
        out, err = proc.communicate()
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        proc.communicate()
        raise RuntimeError('Timeout while running %s' % args[0])
    out_s = out.decode('utf-8', 'replace') if isinstance(out, bytes) else (out or '')
    err_s = err.decode('utf-8', 'replace') if isinstance(err, bytes) else (err or '')
    return proc.returncode, out_s, err_s


def _try_exec(path):
    _make_executable(path)
    code, out, err = _run_raw([path, 'version'], timeout=15)
    text = (out + '\n' + err).strip()
    return (code == 0 and 'rclone' in text.lower()), (text or ('code %s' % code))


def _needs_internal_copy(path):
    try:
        absolute = os.path.abspath(path)
        addon_absolute = os.path.abspath(addon_path())
        if absolute.startswith(addon_absolute + os.sep):
            return True
    except Exception:
        pass
    low = (path or '').lower()
    return low.startswith('/sdcard/') or low.startswith('/storage/emulated/') or low.endswith('.gz')


def _prepare_candidate(path):
    if _needs_internal_copy(path):
        return _install_binary_from_file(path, gzipped=None)
    return path


def import_manual_rclone():
    for src in _manual_import_candidates():
        if not exists(src):
            continue
        try:
            path = _install_binary_from_file(src, gzipped=None)
            ok, text = _try_exec(path)
            if ok:
                _report('rclone imported successfully from %s' % src, xbmc.LOGINFO)
                return path
            _report('Imported but not executable: %s :: %s' % (src, text[:800]), xbmc.LOGWARNING)
        except Exception as exc:
            _report('Import failed for %s: %s' % (src, exc), xbmc.LOGWARNING)
    return ''


def embedded_binary_locations():
    return [
        'resources/bin/android/armeabi-v7a/rclone',
        'resources/bin/android/arm64-v8a/rclone',
        'resources/bin/android/x86/rclone',
        'resources/bin/android/x86_64/rclone',
    ]


def find_rclone(allow_download=False):
    # allow_download is kept only for compatibility; network downloads are intentionally disabled.
    del _LAST_REPORT[:]
    props = android_props()
    _report('Searching for local rclone. AndroidLike=%s arch=%s sdk=%s model=%s abi=%s abilist=%s package=%s' % (
        _is_android_like(), detected_android_arch(), props.get('sdk'), props.get('model'),
        props.get('cpu_abi'), props.get('cpu_abilist'), android_package_name()
    ), xbmc.LOGINFO)
    for path in _candidate_paths():
        if not exists(path):
            continue
        try:
            run_path = _prepare_candidate(path)
            ok, text = _try_exec(run_path)
            if ok:
                _report('rclone OK: %s' % run_path, xbmc.LOGINFO)
                return run_path
            _report('Invalid candidate: %s :: %s' % (run_path, text[:800]), xbmc.LOGWARNING)
        except Exception as exc:
            _report('Candidate is not executable %s: %s' % (path, exc), xbmc.LOGWARNING)
    return ''


class RcloneError(RuntimeError):
    pass


def _iter_files(folder):
    if not os.path.isdir(folder):
        return
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            try:
                stat_result = os.stat(path)
                yield path, stat_result.st_size, stat_result.st_mtime
            except Exception:
                continue


def _remove_empty_dirs(folder):
    if not os.path.isdir(folder):
        return
    for root, dirs, _files in os.walk(folder, topdown=False):
        for name in dirs:
            try:
                os.rmdir(os.path.join(root, name))
            except Exception:
                pass


def _trim_file(path, max_bytes):
    try:
        size = os.path.getsize(path)
        if size <= max_bytes:
            return 0
        with open(path, 'rb') as fh:
            fh.seek(-max_bytes, os.SEEK_END)
            data = fh.read()
        with open(path, 'wb') as fh:
            fh.write(data)
        return size - len(data)
    except Exception:
        return 0


def _cap_directory(folder, max_bytes, max_age_seconds=0):
    files = list(_iter_files(folder) or [])
    removed_bytes = 0
    now = time.time()
    kept = []
    for path, size, mtime in files:
        if max_age_seconds and now - mtime > max_age_seconds:
            try:
                os.remove(path)
                removed_bytes += size
                continue
            except Exception:
                pass
        kept.append((path, size, mtime))
    total = sum(x[1] for x in kept)
    if total > max_bytes:
        for path, size, _mtime in sorted(kept, key=lambda x: x[2]):
            if total <= max_bytes:
                break
            try:
                os.remove(path)
                total -= size
                removed_bytes += size
            except Exception:
                pass
    _remove_empty_dirs(folder)
    return removed_bytes


class RcloneBackend:
    def __init__(self, conf_path):
        self.conf_path = conf_path
        self.profile = addon_profile()
        self.cache_dir = os.path.join(self.profile, 'list-cache')
        self.pid_dir = os.path.join(self.profile, 'servers')
        self.rclone_cache_dir = os.path.join(self.profile, 'rclone-vfs-cache')
        self.tmp_dir = os.path.join(self.profile, 'tmp')
        for folder in (self.cache_dir, self.pid_dir, self.rclone_cache_dir, self.tmp_dir):
            mkdirs(folder)
        # Cleanup is intentionally performed before validating the binary so an
        # upgrade can reclaim old multi-GB cache data even if rclone was moved.
        self.cleanup_storage(automatic=True)
        self.rclone = find_rclone(allow_download=False)
        if not self.rclone:
            locations = '\n'.join('  - ' + x for x in embedded_binary_locations())
            raise RcloneError(
                'No executable rclone binary was found.\n\n%s\n\n'
                'Set the binary path in Settings or include it in the ZIP at one of these locations:\n%s' % (
                    get_last_report(), locations
                )
            )

    def base_args(self):
        return [
            self.rclone,
            '--config', self.conf_path,
            '--cache-dir', self.rclone_cache_dir,
            '--retries', '2',
            '--low-level-retries', '5',
            '--contimeout', '15s',
            '--timeout', '1m',
        ]

    def run(self, args, timeout=60):
        cmd = self.base_args() + args
        _report('run: %s' % ' '.join(cmd[:12] + (['...'] if len(cmd) > 12 else [])), xbmc.LOGDEBUG)
        code, out, err = _run_raw(cmd, timeout=timeout)
        if code != 0:
            raise RcloneError((err or out or 'rclone exited with code %s' % code).strip())
        return out

    def version(self):
        code, out, err = _run_raw([self.rclone, 'version'], timeout=15)
        if code != 0:
            raise RcloneError((err or out).strip())
        return (out or err).strip()

    def list_remotes(self):
        out = self.run(['listremotes'], timeout=45)
        result = []
        for line in out.splitlines():
            name = line.strip().rstrip(':')
            if name:
                result.append(name)
        return result

    def _cache_key(self, remote, path):
        try:
            mtime = str(os.path.getmtime(self.conf_path))
        except Exception:
            mtime = '0'
        raw = (self.conf_path + '\0' + mtime + '\0' + remote + '\0' + (path or '')).encode('utf-8', 'replace')
        return hashlib.sha1(raw).hexdigest() + '.json'

    def _cache_path(self, remote, path):
        return os.path.join(self.cache_dir, self._cache_key(remote, path))

    def clear_list_cache(self):
        count = 0
        if os.path.isdir(self.cache_dir):
            for name in os.listdir(self.cache_dir):
                if not name.endswith('.json'):
                    continue
                try:
                    os.remove(os.path.join(self.cache_dir, name))
                    count += 1
                except Exception:
                    pass
        return count

    def invalidate_list_cache(self, remote, path=''):
        cache_path = self._cache_path(remote, path)
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
                return 1
        except Exception:
            pass
        return 0

    @staticmethod
    def join_path(parent, child):
        parent = (parent or '').strip('/')
        child = (child or '').strip('/')
        return parent + '/' + child if parent and child else (parent or child)

    @staticmethod
    def remote_target(remote, path=''):
        remote = (remote or '').rstrip(':')
        path = (path or '').strip('/')
        return '%s:%s' % (remote, path) if path else '%s:' % remote

    def list_folder(self, remote, path=''):
        cache_minutes = _setting_int('list_cache_minutes', 30, minimum=0, maximum=1440)
        cache_path = self._cache_path(remote, path)
        if cache_minutes > 0 and exists(cache_path):
            try:
                age = time.time() - os.path.getmtime(cache_path)
                if age < cache_minutes * 60:
                    with open(cache_path, 'r', encoding='utf-8') as fh:
                        return json.load(fh)
            except Exception:
                pass
        args = ['lsjson', self.remote_target(remote, path), '--no-mimetype']
        if _setting_bool('skip_modtime', True):
            args.append('--no-modtime')
        out = self.run(args, timeout=_setting_int('list_timeout', 120, minimum=30, maximum=900))
        try:
            raw_items = json.loads(out or '[]')
        except Exception as exc:
            raise RcloneError('rclone lsjson returned invalid JSON: %s' % exc)
        items = []
        for item in raw_items:
            raw_path = item.get('Path') or item.get('Name') or ''
            name = item.get('Name') or os.path.basename(raw_path.rstrip('/')) or raw_path
            if not name:
                continue
            items.append({
                'name': name,
                'path': self.join_path(path, name),
                'is_folder': bool(item.get('IsDir')),
                'size': int(item.get('Size', 0) or 0),
                'modified': item.get('ModTime', '') or '',
            })
        items.sort(key=lambda x: (not x['is_folder'], x['name'].lower()))
        if cache_minutes > 0:
            try:
                with open(cache_path, 'w', encoding='utf-8') as fh:
                    json.dump(items, fh, ensure_ascii=False, separators=(',', ':'))
            except Exception as exc:
                _report('Could not write listing cache: %s' % exc, xbmc.LOGWARNING)
        return items

    def list_recursive(self, remote, path='', video_only=False):
        args = ['lsjson', self.remote_target(remote, path), '--recursive', '--files-only', '--no-mimetype']
        if _setting_bool('library_fast_list', True):
            args.append('--fast-list')
        out = self.run(args, timeout=_setting_int('library_scan_timeout', 900, minimum=60, maximum=7200))
        try:
            raw_items = json.loads(out or '[]')
        except Exception as exc:
            raise RcloneError('The recursive scan returned invalid JSON: %s' % exc)
        video_extensions = {
            '.mkv', '.mp4', '.avi', '.m4v', '.mov', '.ts', '.iso', '.m2ts', '.mpg',
            '.mpeg', '.wmv', '.flv', '.webm', '.vob', '.3gp', '.ogm', '.ogv'
        }
        items = []
        for item in raw_items:
            raw_path = (item.get('Path') or item.get('Name') or '').strip('/')
            full_path = self.join_path(path, raw_path)
            if not full_path:
                continue
            if video_only and os.path.splitext(full_path.lower())[1] not in video_extensions:
                continue
            items.append({
                'name': item.get('Name') or os.path.basename(full_path),
                'path': full_path,
                'is_folder': False,
                'size': int(item.get('Size', 0) or 0),
                'modified': item.get('ModTime', '') or '',
            })
        items.sort(key=lambda x: x['path'].lower())
        return items

    def search_videos(self, remote, path, query, limit=300):
        query = (query or '').strip().lower()
        if not query:
            return []
        results = []
        for item in self.list_recursive(remote, path, video_only=True):
            if query in item.get('name', '').lower() or query in item.get('path', '').lower():
                results.append(item)
                if len(results) >= limit:
                    break
        return results

    def _server_id(self, remote):
        raw = (self.conf_path + '\0' + remote).encode('utf-8', 'replace')
        return hashlib.sha1(raw).hexdigest()[:12]

    def _pid_file(self, remote):
        return os.path.join(self.pid_dir, self._server_id(remote) + '.json')

    def _port_for_remote(self, remote):
        start = _setting_int('serve_port_start', 28980, minimum=1024, maximum=64000)
        hashed = int(hashlib.sha1((self.conf_path + '\0' + remote).encode('utf-8', 'replace')).hexdigest()[:6], 16)
        return start + (hashed % 800)

    @staticmethod
    def _is_port_open(port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            return sock.connect_ex(('127.0.0.1', int(port))) == 0
        finally:
            sock.close()

    @staticmethod
    def _pid_alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def _server_logs_tail(self, remote, n=3000):
        sid = self._server_id(remote)
        parts = []
        for suffix in ('.err.log', '.out.log'):
            path = os.path.join(self.pid_dir, sid + suffix)
            try:
                if exists(path):
                    with open(path, 'rb') as fh:
                        parts.append(fh.read()[-n:].decode('utf-8', 'replace'))
            except Exception:
                pass
        return '\n'.join(x for x in parts if x)

    def _serve_flags(self, addr):
        mode = (_setting_text('vfs_cache_mode', 'off') or 'off').strip().lower()
        if mode not in ('off', 'minimal', 'writes', 'full'):
            mode = 'off'
        cache_mb = _setting_int('vfs_cache_max_size_mb', 256, minimum=64, maximum=4096)
        buffer_mb = _setting_int('buffer_size_mb', 8, minimum=0, maximum=128)
        chunk_mb = _setting_int('read_chunk_size_mb', 16, minimum=1, maximum=256)
        chunk_limit_mb = max(chunk_mb, _setting_int('read_chunk_limit_mb', 128, minimum=16, maximum=2048))
        flags = [
            '--read-only',
            '--addr', addr,
            '--vfs-cache-mode', mode,
            '--vfs-cache-max-size', '%dM' % cache_mb,
            '--vfs-cache-max-age', '1h',
            '--vfs-cache-poll-interval', '1m',
            '--vfs-read-chunk-size', '%dM' % chunk_mb,
            '--vfs-read-chunk-size-limit', '%dM' % chunk_limit_mb,
            '--buffer-size', '%dM' % buffer_mb,
            '--dir-cache-time', '10m',
            '--poll-interval', '0',
            '--stats', '0',
        ]
        extra = _setting_text('rclone_serve_extra_args', '')
        if extra:
            try:
                import shlex
                flags.extend(shlex.split(extra))
            except Exception:
                flags.extend(extra.split())
        return flags

    def ensure_http_server(self, remote):
        port = self._port_for_remote(remote)
        if self._is_port_open(port):
            return port
        target = self.remote_target(remote, '')
        addr = '127.0.0.1:%d' % port
        cmd = self.base_args() + ['serve', 'http', target] + self._serve_flags(addr)
        env = os.environ.copy()
        env.setdefault('HOME', self.profile)
        env.setdefault('TMPDIR', self.tmp_dir)
        mkdirs(env['TMPDIR'])
        sid = self._server_id(remote)
        stdout_path = os.path.join(self.pid_dir, sid + '.out.log')
        stderr_path = os.path.join(self.pid_dir, sid + '.err.log')
        # Start each server with fresh bounded logs instead of appending forever.
        stdout = open(stdout_path, 'wb')
        stderr = open(stderr_path, 'wb')
        _report('Starting rclone serve http: %s at %s' % (target, addr), xbmc.LOGINFO)
        try:
            proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, env=env, close_fds=True)
        finally:
            stdout.close()
            stderr.close()
        meta = {'pid': proc.pid, 'remote': remote, 'port': port, 'started': time.time()}
        try:
            with open(self._pid_file(remote), 'w', encoding='utf-8') as fh:
                json.dump(meta, fh)
        except Exception:
            pass
        timeout = _setting_int('serve_start_timeout', 30, minimum=5, maximum=120)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RcloneError('rclone serve http exited during startup.\n%s' % self._server_logs_tail(remote))
            if self._is_port_open(port):
                return port
            time.sleep(0.25)
        raise RcloneError('rclone serve http did not open port %d within %ds.\n%s' % (port, timeout, self._server_logs_tail(remote)))

    def playback_url(self, remote, path):
        port = self.ensure_http_server(remote)
        encoded = urllib.parse.quote((path or '').strip('/'), safe='/')
        return 'http://127.0.0.1:%d/%s' % (port, encoded)

    def stop_servers(self):
        stopped = 0
        if not os.path.isdir(self.pid_dir):
            return 0
        for name in os.listdir(self.pid_dir):
            if not name.endswith('.json'):
                continue
            path = os.path.join(self.pid_dir, name)
            try:
                with open(path, 'r', encoding='utf-8') as fh:
                    meta = json.load(fh)
                pid = int(meta.get('pid') or 0)
                if pid and self._pid_alive(pid):
                    try:
                        os.kill(pid, 15)
                        stopped += 1
                    except Exception:
                        pass
                os.remove(path)
            except Exception:
                pass
        return stopped

    def cleanup_storage(self, automatic=False, aggressive=False):
        """Remove old unlimited-cache leftovers and enforce small hard caps."""
        removed = 0
        marker = os.path.join(self.profile, '.storage_policy_v3')
        mode = (_setting_text('vfs_cache_mode', 'off') or 'off').strip().lower()
        first_run_policy = not os.path.exists(marker)
        if aggressive or first_run_policy or mode == 'off':
            # This specifically clears multi-gigabyte data left by version 1.2.0.
            for path, size, _mtime in list(_iter_files(self.rclone_cache_dir) or []):
                try:
                    os.remove(path)
                    removed += size
                except Exception:
                    pass
            _remove_empty_dirs(self.rclone_cache_dir)
            try:
                with open(marker, 'w', encoding='utf-8') as fh:
                    fh.write('v3\n')
            except Exception:
                pass
        else:
            cap_mb = _setting_int('vfs_cache_max_size_mb', 256, minimum=64, maximum=4096)
            removed += _cap_directory(self.rclone_cache_dir, cap_mb * 1024 * 1024, max_age_seconds=2 * 3600)
        # Folder listing JSON should remain tiny even on devices with very limited storage.
        removed += _cap_directory(self.cache_dir, 12 * 1024 * 1024, max_age_seconds=7 * 86400)
        removed += _cap_directory(self.tmp_dir, 32 * 1024 * 1024, max_age_seconds=6 * 3600)
        for filename in os.listdir(self.pid_dir) if os.path.isdir(self.pid_dir) else []:
            if filename.endswith('.log'):
                removed += _trim_file(os.path.join(self.pid_dir, filename), 256 * 1024)
        if removed:
            _report('Storage cleanup: %.1f MB freed' % (removed / 1024.0 / 1024.0), xbmc.LOGINFO)
        return removed

    def storage_usage(self):
        result = {'list_cache': 0, 'vfs_cache': 0, 'logs': 0, 'tmp': 0, 'total': 0}
        for key, folder in (
            ('list_cache', self.cache_dir), ('vfs_cache', self.rclone_cache_dir),
            ('logs', self.pid_dir), ('tmp', self.tmp_dir),
        ):
            result[key] = sum(size for _path, size, _mtime in (_iter_files(folder) or []))
        result['total'] = sum(result[key] for key in ('list_cache', 'vfs_cache', 'logs', 'tmp'))
        return result


def diagnostic_text():
    props = android_props()
    lines = [
        'Rclone Nexus - diagnostics',
        'Android-like: %s' % _is_android_like(),
        'Package/process: %s' % (android_package_name() or '(not detected)'),
        'Selected architecture: %s' % detected_android_arch(),
        'SDK: %s' % props.get('sdk'),
        'Android/Fire OS: %s' % props.get('release'),
        'Model: %s' % props.get('model'),
        'Primary ABI: %s' % props.get('cpu_abi'),
        'ABI list: %s' % props.get('cpu_abilist'),
        'Executable base: %s' % executable_base_dir(),
        'Managed path: %s' % rclone_managed_path(),
        'Kodi profile: %s' % addon_profile(),
        'Automatic download: permanently disabled',
        'VFS cache mode: %s' % _setting_text('vfs_cache_mode', 'off'),
        'Accepted bundled locations:',
    ]
    lines.extend('  - ' + item for item in embedded_binary_locations())
    lines.append('Existing local candidates:')
    found = False
    for path in _candidate_paths() + _manual_import_candidates():
        if exists(path):
            lines.append('  - %s' % path)
            found = True
    if not found:
        lines.append('  - none')
    if _LAST_REPORT:
        lines.append('\nLast report:')
        lines.append(get_last_report())
    return '\n'.join(lines)
