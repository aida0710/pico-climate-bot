import ast
import contextlib
import io
import pathlib
import tempfile
import types
import unittest
import os

ROOT = pathlib.Path(__file__).resolve().parent.parent / 'firmware'
PERIOD = 1 << 30

class Clock:
    def __init__(self, start=0): self.now = start; self.calls = 0
    def ticks_ms(self):
        self.calls += 1
        if self.calls > 200000: raise TimeoutError('test execution budget')
        self.now += 1
        return self.now % PERIOD
    def ticks_diff(self, a, b): return (a-b+PERIOD//2) % PERIOD-PERIOD//2
    def sleep_ms(self, ms): self.now += ms
    def sleep(self, seconds):
        self.calls += 1
        if self.calls > 10000: raise TimeoutError("test execution budget")
        self.now += int(seconds*1000)
    def time(self): return self.now//1000
    def localtime(self, seconds): return (2026,9,21,22,0,0,0,0)

class Led:
    def on(self): pass
    def off(self): pass

class Watchdog:
    def __init__(self): self.feeds=0
    def feed(self): self.feeds+=1

class WLAN:
    def __init__(self, success=True): self.up=False; self.success=success; self.attempts=0
    def active(self, value=None): pass
    def connect(self, *args): self.attempts+=1; self.up=self.success
    def disconnect(self): self.up=False
    def isconnected(self): return self.up
    def status(self): return 3 if self.up else 1
    def ifconfig(self): return ('127.0.0.1',)*4

class Response:
    status_code=204
    def __init__(self): self.closed=False
    def close(self): self.closed=True
    @property
    def text(self): raise AssertionError('Do not read unbounded error body')

class Requests:
    def __init__(self, response): self.response=response; self.calls=[]
    def post(self, *args, **kwargs): self.calls.append(kwargs); return self.response

def application():
    tree=ast.parse((ROOT/'main.py').read_text())
    # Run the actual functions without hardware imports or the infinite loop.
    nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))]
    ns={'time':Clock(), 'led':Led(), 'wdt':Watchdog(), 'wlan':WLAN(),
        'config':types.SimpleNamespace(WIFI_SSID='test',WIFI_PASSWORD='test',DISCORD_WEBHOOK_URL='https://invalid.test/'),
        'json':__import__('json'), 'gc':types.SimpleNamespace(collect=lambda:None),
        'os':types.SimpleNamespace(stat=os.stat, remove=os.remove, rename=os.rename, sync=lambda:None), 'VERSION':'test', 'INTERVAL_S':300, 'WIFI_TIMEOUT_MS':30000,
        'HTTP_TIMEOUT_S':5, 'LOG_LIMIT':4096, 'LOG_INTERVAL_MS':1800000,
        '_last_log':{}, 'LOG_PATH':'health.log', 'clock_synced':False,
        'bmp':None, 'prev_pressure':None}
    ns['network']=types.SimpleNamespace(WLAN=lambda _:ns['wlan'], STA_IF=0)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'device_main','exec'),ns)
    return ns

class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.cwd=os.getcwd();os.chdir(self.tmp.name)
        self.sink=contextlib.redirect_stdout(io.StringIO());self.sink.__enter__()
    def tearDown(self):
        self.sink.__exit__(None,None,None);os.chdir(self.cwd);self.tmp.cleanup()
    def test_bmp_temperature_and_pressure_waits_survive_wrap(self):
        cls=next(n for n in ast.parse((ROOT/'libs/bmp180.py').read_text()).body if isinstance(n,ast.ClassDef))
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='makegauge')
        for start in (1000,PERIOD-3,PERIOD-20):
            clock=Clock(start);ns={'time':clock}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'bmp','exec'),ns)
            bus=types.SimpleNamespace(writeto_mem=lambda *a:None,readfrom_mem=lambda a,r,n:bytes(n))
            sensor=types.SimpleNamespace(_bmp_i2c=bus,_bmp_addr=119,oversample_setting=3)
            g=ns['makegauge'](sensor)
            self.assertTrue(any(next(g) is True for _ in range(100)),f'wait did not complete at {start}')
    def test_bmp_stuck_conversion_has_deadline(self):
        cls=next(n for n in ast.parse((ROOT/'libs/bmp180.py').read_text()).body if isinstance(n,ast.ClassDef))
        fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='blocking_read')
        clock=Clock(PERIOD-100)
        ns={'time':clock}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'bmp','exec'),ns)
        def stuck():
            while True: yield None
        with self.assertRaises(OSError) as exc:
            ns['blocking_read'](types.SimpleNamespace(gauge=stuck()))
        self.assertEqual(exc.exception.args[0],110)
        self.assertLess(clock.calls,2000)

    def test_response_status_exception_still_closes(self):
        ns=application()
        class BrokenResponse(Response):
            @property
            def status_code(self): raise OSError(5)
        response=BrokenResponse();ns['urequests']=Requests(response)
        self.assertIs(ns['send_to_discord']('test'),False)
        self.assertTrue(response.closed)

    def test_log_throttle_survives_tick_wrap(self):
        ns=application();ns['time']=Clock(PERIOD-100)
        ns['log_event']('http',503)
        initial=pathlib.Path('health.log').stat().st_size
        ns['time'].now+=200
        ns['log_event']('http',503)
        self.assertEqual(pathlib.Path('health.log').stat().st_size,initial)

    def test_wifi_failure_is_bounded_across_wrap(self):
        ns=application();ns['wlan']=WLAN(False);ns['time']=Clock(PERIOD-10000)
        try:result=ns['connect_wifi']()
        except TimeoutError:self.fail('Wi-Fi wait has no deadline')
        self.assertFalse(result);self.assertLess(ns['time'].calls,1000)
    def test_send_reconnects_and_has_timeout(self):
        ns=application();r=Response();ns['urequests']=Requests(r)
        self.assertTrue(ns['send_to_discord']('test'))
        self.assertEqual(ns['wlan'].attempts,1)
        self.assertEqual(ns['urequests'].calls[0]['timeout'],5)
        self.assertTrue(r.closed)
    def test_offline_does_not_enter_dns_http(self):
        ns=application();ns['wlan']=WLAN(False);ns['urequests']=Requests(Response())
        try:result=ns['send_to_discord']('test')
        except TimeoutError:self.fail('offline send never returned')
        self.assertFalse(result);self.assertEqual(ns['urequests'].calls,[])
    def test_http_error_closes_without_reading_body(self):
        ns=application();r=Response();r.status_code=503;ns['urequests']=Requests(r)
        self.assertFalse(ns['send_to_discord']('test'));self.assertTrue(r.closed)
    def test_transport_exception_returns_failure_without_secrets_in_log(self):
        ns=application()
        def fail(*a,**kw):raise OSError('secret-webhook-value')
        ns['urequests']=types.SimpleNamespace(post=fail)
        self.assertFalse(ns['send_to_discord']('test'))
        for p in pathlib.Path('.').glob('*.log*'):self.assertNotIn('secret-webhook-value',p.read_text())
    def test_five_minute_sleep_feeds_watchdog(self):
        ns=application();self.assertIn('sleep_safe',ns)
        ns['sleep_safe'](300);self.assertGreaterEqual(ns['wdt'].feeds,300)
        self.assertGreaterEqual(ns['time'].now,300000)
    def test_ntp_failure_is_nonfatal(self):
        ns=application();self.assertIn('sync_clock',ns)
        def fail():raise OSError('ntp unavailable')
        ns['ntptime']=types.SimpleNamespace(settime=fail)
        self.assertFalse(ns['sync_clock']())
    def test_log_is_rate_limited_and_rotated(self):
        ns=application();self.assertIn('log_event',ns)
        ns['log_event']('http',503)
        initial=pathlib.Path('health.log').stat().st_size
        for _ in range(100):ns['log_event']('http',503)
        self.assertEqual(pathlib.Path('health.log').stat().st_size,initial)
        for _ in range(300):
            ns['time'].now+=1800001;ns['log_event']('http',503)
        self.assertLessEqual(sum(p.stat().st_size for p in pathlib.Path('.').glob('health.log*')),8192)
    def test_sensor_failure_does_not_stop_other_measurements(self):
        ns=application();self.assertIn('collect_message',ns)
        def fail():raise OSError(5)
        ns['dht11']=types.SimpleNamespace(measure=fail)
        ns['BMP180']=lambda bus:types.SimpleNamespace(blocking_read=fail)
        ns['i2c']=object()
        result=ns['collect_message']()
        self.assertIn('DHT11',result);self.assertIn('BMP180',result);self.assertIsNone(ns['bmp'])

if __name__=='__main__':unittest.main(verbosity=2)
