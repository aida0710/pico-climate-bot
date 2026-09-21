"""Install firmware and private Bot settings as one controlled USB operation."""
import ast
import json
import subprocess
import sys
from pathlib import Path
from .device import Device, select_port
from .workflow import deploy, firmware_files, load_backup

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ('BOT_TOKEN', 'BOT_CHANNEL_ID', 'BOT_APPLICATION_ID')


def updated_config(source, settings):
    # Preserve Wi-Fi/webhook and any unrelated configuration verbatim.
    text = source.decode('utf-8')
    lines = text.splitlines(keepends=True)
    remove = set()
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in FIELDS for t in node.targets):
            remove.update(range(node.lineno-1, node.end_lineno))
    result = ''.join(line for i,line in enumerate(lines) if i not in remove).rstrip()+'\n\n'
    for key,value in zip(FIELDS, (settings['bot_token'], settings['channel_id'], settings['application_id'])):
        result += key+' = '+repr(str(value))+'\n'
    compile(result, 'config.py', 'exec')
    return result.encode('utf-8')


def main():
    private = ROOT/'credentials/bot.json'
    settings = json.loads(private.read_text())
    if not all(settings.get(k) for k in ('bot_token','channel_id','application_id')):
        raise ValueError('Bot credential file needs bot_token, channel_id, application_id')
    if private.stat().st_mode & 0o077:
        raise ValueError('Bot credential file must be chmod 600')
    subprocess.run([sys.executable, str(ROOT/'pico.py'), 'test'], check=True)
    files = firmware_files(ROOT/'firmware')
    device = Device(select_port())
    snapshot = None
    original = None
    try:
        original = device.read('config.py')
        if original is None:
            raise ValueError('Existing Pico config.py is required')
        content = updated_config(original, settings)
        snapshot, changed = deploy(device, files, ROOT/'backups')
        try:
            device.stage('config.py', content)
            device.install('config.py')
            if device.read('config.py') != content:
                raise OSError('Bot settings readback failed')
        except BaseException:
            device.stage('config.py', original)
            device.install('config.py')
            device.remove('config.py.new')
            deploy(device, load_backup(snapshot), ROOT/'backups')
            raise
        print('Private Bot settings and firmware verified. Backup:', snapshot)
        print('Updated firmware files:', ', '.join(changed))
    finally:
        device.close()
        print('Restart requested; allow about 1 minute for first Bot update.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Config/parser tracebacks can contain source secrets; omit their text.
        print('Bot setup failed:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
