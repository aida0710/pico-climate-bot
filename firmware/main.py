"""Five-minute sensor reports with bounded waits and watchdog recovery."""
import machine
from machine import Pin, I2C
import dht
import time
import ntptime
import network
import urequests
import json
import gc
import os
from libs.bmp180 import BMP180
import config

VERSION = '2026-09-21.1'
INTERVAL_S = 300
WIFI_TIMEOUT_MS = 30000
HTTP_TIMEOUT_S = 5
LOG_PATH = 'health.log'
LOG_LIMIT = 4096
LOG_INTERVAL_MS = 1800000
_last_log = {}
wdt = None
bmp = None
prev_pressure = None
clock_synced = False


def feed():
    if wdt is not None:
        wdt.feed()


def sleep_safe(seconds):
    # Only feed during intentional, bounded idle time, never in a timer IRQ.
    for _ in range(int(seconds)):
        feed()
        time.sleep_ms(1000)
    feed()


def error_code(exc):
    # Exception messages may contain a credential-bearing URL.
    args = getattr(exc, 'args', ())
    return args[0] if args and isinstance(args[0], int) else type(exc).__name__


def log_event(event, detail=None):
    now = time.ticks_ms()
    print('EVENT', event, detail)
    previous = _last_log.get(event)
    if previous is not None:
        elapsed = time.ticks_diff(now, previous)
        if 0 <= elapsed < LOG_INTERVAL_MS:
            return
    _last_log[event] = now
    try:
        line = json.dumps({'time': time.time(), 'ticks': now, 'event': event,
                           'detail': detail, 'version': VERSION}) + '\n'
        try:
            size = os.stat(LOG_PATH)[6]
        except OSError:
            size = 0
        if size + len(line) > LOG_LIMIT:
            try:
                os.remove(LOG_PATH + '.1')
            except OSError:
                pass
            os.rename(LOG_PATH, LOG_PATH + '.1')
        with open(LOG_PATH, 'a') as f:
            f.write(line)
        os.sync()
    except Exception:
        print('EVENT log_write_failed')


def connect_wifi():
    if wlan.isconnected():
        return True
    feed()
    try:
        wlan.active(False)
        wlan.active(True)
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        start = time.ticks_ms()
        while not wlan.isconnected():
            if time.ticks_diff(time.ticks_ms(), start) >= WIFI_TIMEOUT_MS:
                wlan.disconnect()
                log_event('wifi_timeout', wlan.status())
                return False
            if wlan.status() < 0:
                log_event('wifi_error', wlan.status())
                return False
            feed()
            time.sleep_ms(250)
        print('WIFI connected')
        return True
    except Exception as exc:
        log_event('wifi_error', error_code(exc))
        return False


def send_to_discord(message):
    if not connect_wifi():
        return False  # Do not enter DNS while disconnected.
    response = None
    led.off()
    feed()
    try:
        data = json.dumps({'content': message}).encode('utf-8')
        response = urequests.post(config.DISCORD_WEBHOOK_URL,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            data=data, timeout=HTTP_TIMEOUT_S)
        status = response.status_code
        print('HTTP', status)
        if status not in (200, 204):
            log_event('http_status', status)
            return False
        return True
    except Exception as exc:
        log_event('http_error', error_code(exc))
        return False
    finally:
        if response is not None:
            try:
                response.close()
            except Exception as exc:
                log_event('http_close', error_code(exc))
        led.on()
        gc.collect()
        feed()


def sync_clock():
    global clock_synced
    feed()
    try:
        ntptime.host = 'ntp.nict.jp'
        ntptime.timeout = 3
        ntptime.settime()
        clock_synced = True
        print('NTP synced')
        return True
    except Exception as exc:
        log_event('ntp_error', error_code(exc))
        return False
    finally:
        feed()


def now_jst():
    if not clock_synced:
        return '--- 時刻未同期'
    t = time.localtime(time.time() + 9 * 3600)
    return '--- {}/{:02d}/{:02d} {:02d}:{:02d}:{:02d}'.format(*t[:6])


def collect_message():
    global bmp, prev_pressure
    lines = [now_jst()]
    feed()
    try:
        dht11.measure()
        lines.append('🌡 DHT11温度: {}°C  💧 湿度: {}%'.format(
            dht11.temperature(), dht11.humidity()))
    except Exception as exc:
        log_event('dht_error', error_code(exc))
        lines.append('⚠️ DHT11読み取り失敗')
    feed()
    try:
        if bmp is None:
            bmp = BMP180(i2c)
            bmp.oversample_sett = 3
        bmp.blocking_read()
        pressure = bmp.pressure / 100
        delta = '--' if prev_pressure is None else '{:+.2f}hPa'.format(pressure-prev_pressure)
        prev_pressure = pressure
        lines.append('🌡 BMP180温度: {:.1f}°C  🌬 気圧: {:.2f}hPa  (変化: {})'.format(
            bmp.temperature, pressure, delta))
    except Exception as exc:
        log_event('bmp_error', error_code(exc))
        lines.append('⚠️ BMP180読み取り失敗')
        # An exception can close the driver's generator. Recreate it next cycle.
        bmp = None
        prev_pressure = None
    feed()
    return '\n'.join(lines)


def main():
    global wdt, led, wlan, dht11, i2c
    # A short maintenance window allows USB recovery before enabling the WDT.
    print('START', VERSION, 'reset', machine.reset_cause())
    time.sleep_ms(3000)
    wdt = machine.WDT(timeout=8000)
    led = Pin('LED', Pin.OUT)
    led.on()
    wlan = network.WLAN(network.STA_IF)
    dht11 = dht.DHT11(Pin(13))
    i2c = I2C(1, sda=Pin(14), scl=Pin(15), freq=100000)
    log_event('boot', machine.reset_cause())
    # Back off after a hard hang; avoid rapid reboot/write loops during outages.
    skip_ntp = machine.reset_cause() == machine.WDT_RESET
    if skip_ntp:
        sleep_safe(60)
    failures = 0
    while True:
        try:
            if not connect_wifi():
                sleep_safe(30)
                continue
            if not clock_synced and not skip_ntp:
                sync_clock()
            skip_ntp = False
            message = collect_message()
            print(message)
            if send_to_discord(message):
                failures = 0
            else:
                failures += 1
                if failures >= 3:
                    wlan.disconnect()
                    wlan.active(False)
                    failures = 0
            gc.collect()
            print('CYCLE_DONE', time.ticks_ms(), 'free', gc.mem_free())
            sleep_safe(INTERVAL_S)
        except Exception as exc:
            log_event('loop_error', error_code(exc))
            gc.collect()
            sleep_safe(30)


if __name__ == '__main__':
    main()
