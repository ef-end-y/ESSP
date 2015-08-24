import re
import sys
import logging
import serial
import json
from wsgiref.simple_server import make_server
from webob.dec import wsgify
from webob import Request, Response, exc
from essp_api import EsspApi
from time import sleep
from multiprocessing import Process, Queue


LOG_FILE = './essp_server.log'
BIND_PORT = 8080
BIND_ADDRESS = '127.0.0.1'

queue_request = Queue()
queue_response = Queue()

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
            step = self.poll_count
            if step == 2:
                # a note is in the process of being scanned
                self.response = '7f8003f0ef00cfca'
                return
            if step == 3:
                # valid note has been scanned, 4 channel
                self.response = '7f8003f0ef04d44a'
                return
            if step == 4:
                # a note has passed through the device
                self.response = '7f0004f0ee04cce0d6'
                return
        self.response = '7f8001f02380'

    def read(self, count=None):
        res = self.response[0:count*2]
        self.response = self.response[count*2:]
        return res.decode('hex')

    def inWaiting(self):
        return len(self.response)


#serial.Serial = SerialMock


def load_controller(string):
    module_name, func_name = string.split(':', 1)
    __import__(module_name)
    module = sys.modules[module_name]
    func = getattr(module, func_name)
    return func

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

    def add_route(self, template, view, **vars):
        self.routes.append((re.compile(template_to_regex(template)), view, vars))

    def __call__(self, environ, start_response):
        req = Request(environ)
        for regex, controller, vars in self.routes:
            match = regex.match(req.path_info)
            if match:
                req.urlvars = match.groupdict()
                req.urlvars.update(vars)
                return controller(environ, start_response)
        return exc.HTTPNotFound()(environ, start_response)


def http_server_process():
    app = Router()
    app.add_route('/{cmd:sync|reset|enable|disable|hold|}', API.simple_cmd)
    app.add_route('/display_on', API.simple_cmd, cmd='display_on')
    app.add_route('/display_off', API.simple_cmd, cmd='display_off')
    app.add_route('/poll', API.poll)
    httpd = make_server(BIND_ADDRESS, BIND_PORT, app)
    httpd.serve_forever()


class API(object):
    @wsgify
    @staticmethod
    def simple_cmd(req):
        cmd = req.urlvars['cmd']
        queue_request.put({'cmd': cmd})
        return Response('ok')

    @wsgify
    @staticmethod
    def poll(req):
        data = []
        while True:
            try:
                data.append(queue_response.get(block=False))
            except Exception:
                break
        return Response(json.dumps(data))


def essp_process(queue_request, queue_response):
    essp = EsspApi('/dev/ttyACM0')
    cmds = {
        'sync': lambda: essp.sync,
        'reset': lambda: essp.reset,
        'enable': lambda: essp.enable,
        'disable': lambda: essp.disable,
        'hold': lambda: essp.hold,
        'display_on': lambda: essp.display_on,
        'display_off': lambda: essp.display_off,
    }
    while True:
        try:
            data = queue_request.get(block=False)
        except Exception:
            pass
        else:
            cmd = data['cmd']
            if cmd in cmds:
                queue_response.put({'cmd': cmd, 'result': cmds[cmd]()()})
        poll = essp.poll()
        for p in poll:
            queue_response.put({'cmd': 'poll', 'status': p['status'], 'param': p['param']})
        sleep(0.01)


p = Process(target=essp_process, args=(queue_request, queue_response))
p.start()
http_server_process()


