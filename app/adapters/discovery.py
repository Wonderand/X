"""Run a small allowlist of separately installed, user-configured search scripts."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import threading
from app.discovery import public_url
from app.errors import DomainError, ServiceError


MAX_OUTPUT = 4 * 1024 * 1024
SCRIPTS = {'router': 'route_search.py', 'aihot': 'aihot_search.py', 'anysearch': 'anysearch_adapter.py'}
ENV_KEYS = {
    'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'HOMEDRIVE',
    'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY',
    'NO_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'YICHEN_ANYSEARCH_RUNTIME_CONF',
    'YICHEN_UNIFIED_SEARCH_RECEIPT_KEY', 'YICHEN_UNIFIED_SEARCH_RECEIPT_KEY_FILE',
}



def run_bounded(command, *, env, cwd, timeout, shell=False):
    """Capture at most MAX_OUTPUT bytes in memory; terminate as soon as the cap is crossed."""
    if shell:
        raise ValueError('Shell execution is not supported')
    process = subprocess.Popen(command, shell=False, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               env=env, cwd=cwd)
    chunks = []
    overflow = threading.Event()
    stream_failed = threading.Event()

    def consume():
        size = 0
        try:
            while True:
                block = process.stdout.read(65536)
                if not block:
                    break
                size += len(block)
                if size > MAX_OUTPUT:
                    overflow.set()
                    if process.poll() is None:
                        process.kill()
                    break
                chunks.append(block)
        except OSError:
            stream_failed.set()
        finally:
            process.stdout.close()

    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        reader.join(timeout=2)
        raise
    reader.join(timeout=2)
    if overflow.is_set():
        raise ServiceError('discovery_output_too_large', uncertain=True)
    if reader.is_alive() or stream_failed.is_set():
        raise ServiceError('discovery_output_incomplete', uncertain=True)
    return returncode, b''.join(chunks)


class DiscoveryAdapter:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        if not self.settings.skills_root:
            return False
        root = Path(self.settings.skills_root).expanduser().resolve()
        return all((root / 'yichen-unified-search/scripts' / name).is_file() for name in SCRIPTS.values())

    def _script(self, kind):
        if not self.settings.skills_root:
            raise ServiceError('discovery_runtime_not_configured')
        root = Path(self.settings.skills_root).expanduser().resolve()
        script = (root / 'yichen-unified-search/scripts' / SCRIPTS[kind]).resolve()
        if not script.is_relative_to(root) or not script.is_file():
            raise ServiceError('discovery_runtime_script_missing')
        return script

    def _invoke(self, kind, arguments):
        script = self._script(kind)
        private_dir = self.settings.data_dir / 'discovery-runtime'
        private_dir.mkdir(parents=True, exist_ok=True)
        child_env = {k: v for k, v in os.environ.items() if k.upper() in ENV_KEYS}
        child_env.update(TMP=str(private_dir), TEMP=str(private_dir), PYTHONIOENCODING='utf-8')
        child_env.setdefault('YICHEN_UNIFIED_SEARCH_RECEIPT_KEY_FILE', str(private_dir / 'receipt.key'))
        if self.settings.anysearch_runtime_conf:
            config = Path(self.settings.anysearch_runtime_conf).expanduser().resolve()
            if not config.is_file():
                raise ServiceError('anysearch_runtime_conf_missing')
            child_env['YICHEN_ANYSEARCH_RUNTIME_CONF'] = str(config)
        executable = self.settings.skills_python or sys.executable
        command = [executable, '-I', str(script), *arguments]
        try:
            returncode, raw = run_bounded(command, shell=False, cwd=private_dir, timeout=90, env=child_env)
        except subprocess.TimeoutExpired:
            raise ServiceError('discovery_runtime_timeout', uncertain=True) from None
        except OSError:
            raise ServiceError('discovery_runtime_unavailable') from None
        try:
            data = json.loads(raw.decode('utf-8-sig'))
        except (UnicodeError, ValueError):
            raise ServiceError('discovery_invalid_json') from None
        if not isinstance(data, dict):
            raise ServiceError('discovery_invalid_json')
        if returncode != 0 and (returncode != 2 or not isinstance(data.get('errors'), list) or not data['errors']):
            raise ServiceError('discovery_script_failed')
        return data

    @staticmethod
    def validate_envelope(data):
        if data.get('schema_version') != '1.0' or any(not isinstance(data.get(key), list) for key in ('routes', 'candidates', 'coverage', 'errors')):
            raise ServiceError('discovery_invalid_envelope')
        if len(data['candidates']) > 100:
            raise ServiceError('discovery_result_limit_exceeded')
        return data

    def search(self, request):
        backend = request['backend']
        if backend not in {'aihot', 'anysearch'}:
            raise ServiceError('discovery_unsupported_backend')
        platform = 'aihot' if backend == 'aihot' else 'web'
        plan = self._invoke('router', ['--query=' + request['query'], '--platform', platform,
                                      '--days', str(request['days']), '--limit', str(request['limit'])])
        route = plan.get('route', {})
        if plan.get('status') != 'ready' or route.get('backend') != backend or route.get('login_state_used') is not False or plan.get('authorization') not in (None, 'not_required'):
            raise ServiceError('discovery_route_not_allowed')
        # The router's argv is never executed. Build our own fixed script contract.
        if backend == 'aihot':
            arguments = ['--query=' + request['query'], '--keyword=' + request['query'],
                         '--feed', 'selected', '--days', str(request['days']), '--limit', str(request['limit']),
                         '--timeout', '20', '--max-pages', '1']
        else:
            arguments = ['search', '--limit', str(request['limit']), '--timeout', '45', '--', request['query']]
        return self.validate_envelope(self._invoke(backend, arguments))

    def read(self, raw_candidate):
        if not isinstance(raw_candidate, dict) or raw_candidate.get('backend') != 'anysearch':
            raise ServiceError('candidate_has_no_anysearch_receipt')
        try:
            public_url(raw_candidate.get('url'))
        except DomainError:
            raise ServiceError('candidate_read_url_not_allowed') from None
        private_dir = self.settings.data_dir / 'discovery-runtime'
        private_dir.mkdir(parents=True, exist_ok=True)
        # Keep the complete original candidate intact for the upstream signature check.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.json', dir=private_dir, delete=False) as handle:
            path = Path(handle.name)
            json.dump(raw_candidate, handle, ensure_ascii=False)
        try:
            data = self.validate_envelope(self._invoke('anysearch', ['verify', '--timeout', '45', '--candidate-from-search', '@' + str(path)]))
        finally:
            path.unlink(missing_ok=True)
        if data['errors'] or len(data['candidates']) != 1:
            raise ServiceError('candidate_original_read_failed')
        candidate = data['candidates'][0]
        if not isinstance(candidate, dict) or candidate.get('candidate_id') != raw_candidate.get('candidate_id') or candidate.get('url') != raw_candidate.get('url'):
            raise ServiceError('candidate_original_mismatch')
        try:
            body = candidate['platform_fields']['anysearch']['extract_markdown']
            opened = candidate['verification']['opened_original']
        except (KeyError, TypeError):
            raise ServiceError('candidate_original_missing') from None
        if opened is not True or not isinstance(body, str) or not 200 <= len(body.strip()) <= 100000:
            raise ServiceError('candidate_original_invalid')
        return body.strip()
