import os
import sys
import pytest
from app.errors import ServiceError


def test_stream_limit_stops_script_before_its_sleep(tmp_path, monkeypatch):
    from app.adapters import discovery as module
    monkeypatch.setattr(module, 'MAX_OUTPUT', 65536)
    command = [sys.executable, '-I', '-c', "import sys,time; sys.stdout.buffer.write(b'x'*1000000); sys.stdout.flush(); time.sleep(20)"]
    with pytest.raises(ServiceError) as error:
        module.run_bounded(command, env={**os.environ, 'TMP': str(tmp_path), 'TEMP': str(tmp_path)}, cwd=tmp_path, timeout=4, shell=False)
    assert error.value.code == 'discovery_output_too_large'
