from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oida.lifecycle import _gateway_environment


def test_macos_prefers_compatible_keg_without_changing_parent_environment():
    environment = {'DYLD_LIBRARY_PATH': '/custom/lib'}
    def prefix(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout='/brew/' + command[-1])
    with patch('oida.lifecycle.sys.platform', 'darwin'), \
         patch('oida.lifecycle.os.environ', environment), \
         patch('oida.lifecycle.importlib.util.find_spec', return_value=None), \
         patch('oida.lifecycle.shutil.which', return_value='/brew/bin/brew'), \
         patch('oida.lifecycle.subprocess.run', side_effect=prefix), \
         patch.object(Path, 'is_dir', return_value=True):
        result = _gateway_environment('mac-mps')
    assert result['DYLD_LIBRARY_PATH'].split(':')[:2] == ['/brew/ffmpeg@8/lib', '/brew/ffmpeg/lib']
    assert result['DYLD_LIBRARY_PATH'].endswith('/custom/lib')
    assert environment == {'DYLD_LIBRARY_PATH': '/custom/lib'}


def test_other_profiles_leave_library_environment_unchanged():
    with patch('oida.lifecycle.os.environ', {'EXAMPLE': 'value'}), \
         patch('oida.lifecycle.subprocess.run') as run:
        assert _gateway_environment('stub') == {'EXAMPLE': 'value'}
        run.assert_not_called()
