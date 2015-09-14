# -*- coding: utf-8 -*-
import os
import atexit
import re
import sys
import logging
import serial
import json
import argparse
import datetime
from signal import SIGTERM
from wsgiref.simple_server import make_server
from webob import Request, exc
from time import sleep
from multiprocessing import Process, Queue
from subprocess import Popen, PIPE
from essp_api import EsspApi

RESP_HEADERS = [('Access-Control-Allow-Origin', '*')]
LPR_PATH = '/usr/bin/lpr'
PRINTER_NAME = 'CUSTOM_Engineering_VKP80'
LOG_FILE = '/tmp/kiosk_server.log'
BIND_PORT = 8080
BIND_ADDRESS = '127.0.0.1'

HOLD_AND_WAIT_ACCEPT_CMD = False

CHECK_TEMPLATE = u'''
"Sistema" ltd ИНН:1401552291
Терминал No 4565
ул.20 Января 6
{date}

Наличные за услугу Доступ в Интернет

имя клиента : {fio}
Логин: {login}
Получено: {credit} azn
Операция No: {order_id}


Просьба не выбрасывать чек до поступления средств на ваш счёт

Тел :012 4081498
График с 10.00-18.00
http://sistema.az
'''


class SerialMock(object):
    POLL_CMD = ('7f0001071188'.decode('hex'), '7f8001071202'.decode('hex'))

    def __init__(self, *args, **kwargs):
        self.response = ''
        self.poll_count = 0
        self.sent = ''

    def write(self, data):
        self.sent = data
        if data in self.POLL_CMD:
            self.poll_count += 1
            if self.poll_count == 2:
                self.response = '7f8003f0ef00cfca'
                return
            if self.poll_count == 3:
                self.response = '7f8003f0ef04d44a'
                return
            if self.poll_count == 4:
                self.response = '7f0004f0ee04cce0d6'
                return
        self.response = '7f8001f02380'

    def read(self, count=None):
        res = self.response[0:count*2]
        self.response = self.response[count*2:]
        return res.decode('hex')

    def inWaiting(self):
        return len(self.response)


class App(object):
    def __init__(self, params):
        self.params = params
        self.child = None
        self.routes = []
        self.queue_request = Queue()
        self.queue_response = Queue()

    @staticmethod
    def _template_to_regex(template):
        regex = ''
        last_pos = 0
        var_regex = re.compile(r'\{(\w+)(?::([^}]+))?\}', re.VERBOSE)
        for match in var_regex.finditer(template):
            regex += re.escape(template[last_pos:match.start()])
            var_name = match.group(1)
            expr = match.group(2) or '[^/]+'
            expr = '(?P<%s>%s)' % (var_name, expr)
            regex += expr
            last_pos = match.end()
        regex += re.escape(template[last_pos:])
        regex = '^%s$' % regex
        return regex

    def add_route(self, template, view, **kwargs):
        self.routes.append((re.compile(self._template_to_regex(template)), view, kwargs))

    def __call__(self, environ, start_response):
        if not self.child or not self.child.is_alive():
            self.child = Process(
                target=note_acceptor_worker,
                args=(self.queue_request, self.queue_response, self.params)
            )
            self.child.daemon = True
            self.child.start()
        req = Request(environ)
        for regex, controller, kwvars in self.routes:
            match = regex.match(req.path_info)
            if match:
                req.urlvars = match.groupdict()
                req.urlvars.update(kwvars)
                res = json.dumps(controller(req))
                headers = RESP_HEADERS[:]
                headers.append(('Content-Length', str(len(res))),)
                start_response('200 OK', headers)
                return [res]
        return exc.HTTPNotFound()(environ, start_response)

    def index(self, req):
        return 'ok'

    def simple_cmd(self, req):
        cmd = req.urlvars['cmd']
        self.queue_request.put({'cmd': cmd})
        return 'ok'

    def poll(self, req):
        data = []
        while True:
            try:
                data.append(self.queue_response.get(block=False))
            except Exception:
                break
        return data

    def print_check(self, req):
        data = {
            'date': datetime.datetime.now().strftime('%d.%m.%Y'),
        }
        data.update(req.POST)
        try:
            check = CHECK_TEMPLATE.format(**data)
        except:
            return 'input data error'
        print check
        # lpr = Popen([LPR_PATH, '-P', PRINTER_NAME], stdin=PIPE)
        # lpr.stdin.write('Test1234\n')
        # lpr.stdin.flush()
        return 'ok'


def note_acceptor_worker(queue_request, queue_response, params):
    verbose = params.verbose
    if params.test:
        serial.Serial = SerialMock
    lh = logging.FileHandler(params.logfile) if params.daemon else logging.StreamHandler(sys.stdout)
    verbose = verbose and verbose > 1
    essp = EsspApi('/dev/ttyACM0', logger_handler=lh, verbose=verbose)
    logger = essp.get_logger()
    logger.info('[WORKER] Start')
    cmds = {
        'sync': lambda: essp.sync,
        'reset': lambda: essp.reset,
        'enable': lambda: essp.enable,
        'disable': lambda: essp.disable,
        'hold': lambda: essp.hold,
        'display_on': lambda: essp.display_on,
        'display_off': lambda: essp.display_off,
    }
    essp_state = 'disabled'
    while True:
        try:
            data = queue_request.get(block=False)
        except:
            pass
        else:
            logger.info('[WORKER] command: %s' % data['cmd'])
            cmd = data['cmd']
            res = {'cmd': cmd, 'result': False}
            if cmd == 'test':
                res['result'] = True
                queue_response.put(res)
                continue
            if cmd in ('start', 'reset', 'disable'):
                if essp_state == 'hold':
                    essp.reject_note()
                essp_state = 'disabled'
            elif cmd in ('enable',):
                if essp_state == 'disabled':
                    essp_state = 'enabled' if HOLD_AND_WAIT_ACCEPT_CMD else 'accept'
            if cmd in cmds:
                res['result'] = cmds[cmd]()()
            elif cmd == 'start':
                res['result'] = bool(essp.sync() and essp.enable_higher_protocol() and essp.disable() and
                                     essp.set_inhibits(essp.easy_inhibit([1, 1, 1, 1, 1, 1, 1]), '0'))
            elif cmd == 'accept':
                if essp_state == 'hold':
                    essp_state = 'accept'
                    res['result'] = True
            queue_response.put(res)
            continue
        if essp_state in ('enabled', 'accept'):
            for event in essp.poll():
                status = event['status']
                param = event['param']
                if status == EsspApi.DISABLED:
                    continue
                if status == EsspApi.READ_NOTE:
                    logger.info('[WORKER] read note %s' % (param if param else 'unknown yet'))
                    if event['param'] and essp_state == 'enabled':
                        essp_state = 'hold'
                        essp.hold()
                queue_response.put({'cmd': 'poll', 'status': status, 'param': param})
        if essp_state == 'hold':
            essp.hold()
        sleep(1)

        if os.getppid() == 1:
            logger.info('[WORKER] Parent process has terminated')
            break


def http_server_worker(params):
    app = App(params)
    app.add_route('/', app.index)
    app.add_route('/{cmd:sync|reset|enable|disable|hold|accept}', app.simple_cmd)
    app.add_route('/display_on', app.simple_cmd, cmd='display_on')
    app.add_route('/display_off', app.simple_cmd, cmd='display_off')
    app.add_route('/poll', app.poll)
    app.add_route('/start', app.simple_cmd, cmd='start')
    app.add_route('/test', app.simple_cmd, cmd='test')
    app.add_route('/print', app.print_check)
    httpd = make_server(params.host, int(params.port), app)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


class Daemon:
    def __init__(self, params):
        logfile = params.logfile if params.daemon else None
        self.stdin = '/dev/null'
        self.stdout = logfile
        self.stderr = logfile
        self.params = params
        self.children = []

    def daemonize(self):
        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError, e:
            sys.stderr.write('fork #1 failed: %d (%s)\n' % (e.errno, e.strerror))
            sys.exit(1)

        os.chdir('/')
        os.setsid()
        os.umask(0)

        try:
            pid = os.fork()
            if pid > 0:
                sys.exit(0)
        except OSError, e:
            sys.stderr.write('fork #2 failed: %d (%s)\n' % (e.errno, e.strerror))
            sys.exit(1)

        sys.stdout.flush()
        sys.stderr.flush()
        si = file(self.stdin, 'r')
        so = file(self.stdout, 'a+')
        se = file(self.stderr, 'a+', 0)
        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())

        atexit.register(self.delpid)
        pid = str(os.getpid())
        file(self.params.pidfile, 'w+').write("%s\n" % pid)

    def delpid(self):
        os.remove(self.params.pidfile)

    def start(self):
        try:
            pf = file(self.params.pidfile, 'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None

        if pid:
            message = 'pidfile %s already exist. Daemon already running?\n'
            sys.stderr.write(message % self.params.pidfile)
            sys.exit(1)

        self.daemonize()
        self.run()

    def stop(self):
        try:
            pf = file(self.params.pidfile, 'r')
            pid = int(pf.read().strip())
            pf.close()
        except IOError:
            pid = None

        if not pid:
            message = 'pidfile %s does not exist. Daemon not running?\n'
            sys.stderr.write(message % self.params.pidfile)
            return

        try:
            while 1:
                os.kill(pid, SIGTERM)
                sleep(0.1)
        except OSError, err:
            err = str(err)
            if err.find('No such process') > 0:
                if os.path.exists(self.params.pidfile):
                    os.remove(self.params.pidfile)
            else:
                print str(err)
                sys.exit(1)

    def restart(self):
        self.stop()
        self.start()

    def run(self):
        http_server_worker(self.params)


def start(params):
    daemon = Daemon(params)
    daemon.start()


def restart(params):
    daemon = Daemon(params)
    daemon.restart()


def stop(params):
    daemon = Daemon(params)
    daemon.stop()


def run(params):
    daemon = Daemon(params)
    daemon.run()

daemon_params = argparse.ArgumentParser(add_help=False)
daemon_params.add_argument('-p', '--pidfile', default='/tmp/kiosk_server.pid', help='Pid for daemon')
daemon_params.add_argument('-l', '--logfile', default=LOG_FILE, help='Logfile')
run_params = argparse.ArgumentParser(add_help=False)
run_params.add_argument('-t', '--test', help='Test', action='count')
run_params.add_argument('-v', '--verbose', action='count', help='-vv: very verbose')
run_params.add_argument('-P', '--port', default=BIND_PORT, help='Port to serve on (default %s)' % BIND_PORT)
run_params.add_argument('-H', '--host', default=BIND_ADDRESS,
                        help='Host to serve on (default %s; 0.0.0.0 to make public)' % BIND_ADDRESS)

parser = argparse.ArgumentParser(description='Kiosk http server. Help: %(prog)s start -h')
p = parser.add_subparsers()
sp_start = p.add_parser('start', parents=[run_params, daemon_params], help='Starts %(prog)s daemon')
sp_stop = p.add_parser('stop', parents=[daemon_params], help='Stops %(prog)s daemon')
sp_restart = p.add_parser('restart', parents=[daemon_params], help='Restarts %(prog)s daemon')
sp_run = p.add_parser('run', parents=[run_params], help='Run in foreground')

sp_start.set_defaults(func=start, daemon=True)
sp_stop.set_defaults(func=stop, daemon=True)
sp_restart.set_defaults(func=restart, daemon=True)
sp_run.set_defaults(func=run, daemon=False)

args = parser.parse_args()
args.func(args)
