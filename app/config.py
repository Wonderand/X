from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit
import os


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path('data')
    bind_host: str = '127.0.0.1'
    bind_port: int = 8000
    admin_password_hash: str = ''
    session_secret: str = ''
    secure_cookie: bool = False
    allowed_hosts: str = '127.0.0.1,localhost,testserver'
    llm_base_url: str = ''
    llm_api_key: str = ''
    llm_model: str = ''
    postiz_base_url: str = ''
    postiz_api_key: str = ''
    postiz_integration_id: str = ''
    skills_root: str = ''
    skills_python: str = ''
    anysearch_runtime_conf: str = ''

    def __post_init__(self):
        path = Path(self.data_dir).resolve()
        if path.exists() and not path.is_dir():
            raise ValueError('Data root must be a directory')
        object.__setattr__(self, 'data_dir', path)
        if not 1 <= self.bind_port <= 65535:
            raise ValueError('Invalid bind port')
        for value in (self.llm_base_url, self.postiz_base_url):
            if value:
                url = urlsplit(value)
                local = url.hostname in {'localhost', '127.0.0.1', '::1'}
                if not url.hostname or url.username or url.password or url.query or url.fragment or (url.scheme != 'https' and not (local and url.scheme == 'http')):
                    raise ValueError('API base must be HTTPS (HTTP permitted only on loopback)')

    @property
    def generator_ready(self):
        return all((self.llm_base_url, self.llm_api_key, self.llm_model))

    @property
    def publisher_ready(self):
        return all((self.postiz_base_url, self.postiz_api_key, self.postiz_integration_id))

    @property
    def auth_ready(self):
        return self.admin_password_hash.startswith('scrypt$') and len(self.session_secret) >= 32

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv()
        values = {}
        for field in fields(cls):
            value = os.getenv('XOPS_' + field.name.upper())
            if value is not None:
                values[field.name] = value
        if 'bind_port' in values:
            values['bind_port'] = int(values['bind_port'])
        if 'secure_cookie' in values:
            values['secure_cookie'] = values['secure_cookie'].lower() == 'true'
        return cls(**values)
