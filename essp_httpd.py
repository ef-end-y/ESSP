import re
import sys
import logging
import serial
import json
import argparse
from wsgiref.simple_server import make_server
from webob import Request, exc
from essp_api import EsspApi
from time import sleep
from multiprocessing import Process, Queue

RESP_HEADERS = [('Access-Control-Allow-Origin', '*')]
LOG_FILE = './essp_server.log'
BIND_PORT = 8080
BIND_ADDRESS = '127.0.0.1'

Queue_request = Queue()
Queue_response = Queue()


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


var_regex = re.compile(r'''
    \{          # The exact character "{"
    (\w+)       # The variable name (restricted to a-z, 0-9, _)
    (?::([^}]+))? # The optional :regex part
    \}          # The exact character "}"
    ''', re.VERBOSE)


def template_to_regex(template):
    regex = ''
    last_pos = 0
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


class Router(object):
    def __init__(self):
        self.routes = []

    def add_route(self, template, view, **kwargs):
        self.routes.append((re.compile(template_to_regex(template)), view, kwargs))

    def __call__(self, environ, start_response):
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


class API(object):
    queue_request = Queue_request
    queue_response = Queue_response

    @staticmethod
    def index(req):
        return 'ok'

    @staticmethod
    def simple_cmd(req):
        cmd = req.urlvars['cmd']
        API.queue_request.put({'cmd': cmd})
        return 'ok'

    @staticmethod
    def poll(req):
        data = []
        while True:
            try:
                data.append(API.queue_response.get(block=False))
            except Exception:
                break
        return data


def essp_process(queue_request, queue_response, verbose, test):
    if test:
        serial.Serial = SerialMock
    lh = logging.StreamHandler(sys.stdout) if verbose else None
    verbose = verbose and verbose > 1
    essp = EsspApi('/dev/ttyACM0', logger_handler=lh, verbose=verbose)
    logger = essp.get_logger()
    cmds = {
        'sync': lambda: essp.sync,
        'reset': lambda: essp.reset,
        'enable': lambda: essp.enable,
        'disable': lambda: essp.disable,
        'hold': lambda: essp.hold,
        'display_on': lambda: essp.display_on,
        'display_off': lambda: essp.display_off,
    }
    accept_note = False
    while True:
        try:
            data = queue_request.get(block=False)
        except Exception:
            pass
        else:
            logger.debug('[HTTP ESSP] command: %s' % data['cmd'])
            cmd = data['cmd']
            res = {'cmd': cmd, 'result': False}
            if cmd in cmds:
                res['result'] = cmds[cmd]()()
            elif cmd == 'start':
                res['result'] = bool(essp.sync() and essp.enable_higher_protocol() and essp.disable() and
                                     essp.set_inhibits(essp.easy_inhibit([1, 1, 1, 1, 1, 1, 1]), '0'))
            elif cmd == 'accept':
                accept_note = True
                res['result'] = True
            queue_response.put(res)
            continue
        poll = essp.poll()
        for event in poll:
            if event['status'] == EsspApi.DISABLED:
                continue
            if event['status'] == EsspApi.READ_NOTE and event['param'] and not accept_note:
                essp.hold()
            queue_response.put({'cmd': 'poll', 'status': event['status'], 'param': event['param']})
        sleep(1)


def start_server(namespace):
    app = Router()
    app.add_route('/', API.index)
    app.add_route('/{cmd:sync|reset|enable|disable|hold|accept}', API.simple_cmd)
    app.add_route('/display_on', API.simple_cmd, cmd='display_on')
    app.add_route('/display_off', API.simple_cmd, cmd='display_off')
    app.add_route('/poll', API.poll)
    app.add_route('/start', API.simple_cmd, cmd='start')
    httpd = make_server(namespace.host, int(namespace.port), app)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


class Start(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        p1 = Process(target=essp_process, args=(Queue_request, Queue_response, namespace.verbose, namespace.test))
        p1.start()
        p2 = Process(target=start_server, args=(namespace,))
        p2.start()
        try:
            p1.join()
            p2.join()
        except KeyboardInterrupt:
            print 'Keyboard interrupt'
            p1.terminate()
            p1.join()
            p2.terminate()
            p2.join()


p = argparse.ArgumentParser(description='Essp http server')
p.add_argument('start', help='Start server', action=Start)
p.add_argument('-p', '--port', default=BIND_PORT, help='Port to serve on (default %s)' % BIND_PORT)
p.add_argument('-H', '--host', default=BIND_ADDRESS,
               help='Host to serve on (default %s; 0.0.0.0 to make public)' % BIND_ADDRESS)
p.add_argument('-t', '--test', help='Test', action='count')
p.add_argument('-v', '--verbose', action='count', help='-vv: very verbose')
args = p.parse_args()




